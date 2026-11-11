"""Chat Router - Interactive Desk Officer AI chatbot & multi-agent dispatch endpoint."""
import json
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from agents.orchestrator import AgentOrchestrator
from services.report_formatter import strip_report_format_preamble
from database import get_db
from models.bug_report import BugReport
from models.chat_message import ChatMessage
from models.project import Project
from models.settings import Settings
from models.skill import Skill
from routers.websocket import manager
from services.attachment_processor import (
    MAX_UPLOAD_BYTES,
    hydrate_attachments_for_request,
    process_uploaded_bytes,
    resolve_stored_upload,
    sanitize_attachment_for_db,
)
from services.llm_router import LLMRouter
from services.watcher_bus import record_system_event

router = APIRouter(prefix="/api/chat", tags=["chat"])


class ChatHistoryItem(BaseModel):
    role: Optional[str] = None
    sender: Optional[str] = None
    agent: Optional[str] = None
    content: Optional[str] = ""
    isStatus: Optional[bool] = False
    attachments: Optional[List[Dict[str, Any]]] = None


class ChatMessageRequest(BaseModel):
    project_id: int
    message: str = ""
    attachments: Optional[List[Dict[str, Any]]] = None
    history: Optional[List[ChatHistoryItem]] = None
    stream_id: Optional[str] = None


async def get_orchestrator(db: AsyncSession) -> AgentOrchestrator:
    res = await db.execute(select(Settings))
    all_settings = {s.key: s.value for s in res.scalars().all()}

    provider = all_settings.get("llm_provider", "openai")
    api_key = all_settings.get("llm_api_key", "")
    model = all_settings.get("llm_model", "gpt-4")
    base_url = all_settings.get("llm_base_url", "")
    default_reporter = all_settings.get("default_reporter", "Mohamed Kamel")

    llm = LLMRouter(provider=provider, api_key=api_key, model=model, base_url=base_url)
    return AgentOrchestrator(llm_router=llm, jira_settings=all_settings, default_reporter=default_reporter)


async def _persist_chat_message(
    db: AsyncSession,
    project_id: int,
    sender: str,
    content: str,
    agent: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> ChatMessage:
    row = ChatMessage(
        project_id=project_id,
        sender=sender,
        agent=agent,
        content=content,
        metadata_json=json.dumps(metadata) if metadata else None,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


def _format_dispatch_reply(desk_reply: str, target_agent: str, result: Dict[str, Any]) -> str:
    """Format a readable Markdown summary after Desk Officer dispatches a single agent."""
    status = result.get("status")
    if status in ("missing_context", "not_found", "unknown"):
        return result.get("message", desk_reply)

    bug_id = result.get("bug_id")
    if target_agent == "detective":
        d = result.get("result", {})
        report = result.get("formatted_report", "")
        if result.get("mode") == "report":
            return (
                f"{desk_reply}\n\n"
                f"### 🔍 Detective Formatted Bug Report (Bug #{bug_id})\n\n{report}"
            )
        report_preview = f"\n\n**Formatted Bug Report Draft (via Detective):**\n{report[:1200]}" if report else ""
        return (
            f"{desk_reply}\n\n"
            f"### 🔍 Detective Investigation (Bug #{bug_id})\n"
            f"**Title:** {d.get('title')}\n"
            f"**Root Cause:** {d.get('root_cause')}\n"
            f"**Severity:** `{d.get('severity', 'medium')}` | **Label:** `{d.get('label', 'Front-End')}` | **Module:** `{d.get('module', 'General')}`\n"
            f"**Summary:** {d.get('trace_summary', '')}"
            f"{report_preview}"
        )

    if target_agent == "coroner":
        ev = result.get("evidence", [])
        files_str = ", ".join(e.get("file_name", "file") for e in ev) or "None"
        return (
            f"{desk_reply}\n\n"
            f"### 📸 Coroner Evidence Capture (Bug #{bug_id})\n"
            f"Captured **{len(ev)}** evidence file(s): `{files_str}`."
        )

    if target_agent == "judge":
        v = result.get("verdict", {})
        hdr = "### ⚖️ Judge Verification" if status != "needs_clarification" else "### ⚖️ Judge Needs Clarification"
        return (
            f"{desk_reply}\n\n"
            f"{hdr} (Bug #{bug_id})\n"
            f"**Verdict:** `{v.get('verdict', 'CONFIRMED')}`\n"
            f"**Root Cause Assessment:** {v.get('final_root_cause')}\n"
            f"**Notes:** {v.get('judge_notes', '')}"
        )

    if target_agent == "sheriff":
        ticket = result.get("jira_ticket", {})
        if status == "failed" or ticket.get("error") or not ticket.get("key"):
            err_text = result.get("message") or ticket.get("error") or "Unknown Jira API error."
            return (
                f"{desk_reply}\n\n"
                f"### ❌ Sheriff Failed to Create Jira Ticket (Bug #{bug_id})\n"
                f"- **Target Project Key:** `{ticket.get('project_key') or 'N/A'}`\n"
                f"- **Reason:** {err_text}\n\n"
                f"*(No fake/simulated ticket was saved in live Jira mode.)*"
            )
        already = " (already raised)" if ticket.get("already_raised") else ""
        placement = f"`{ticket.get('status', 'Backlog')}`"
        if ticket.get("placement_note"):
            placement += f" *({ticket['placement_note']})*"
        attached_files = ticket.get("evidence_attached") or []
        attached_cnt = ticket.get("attached_count", len(attached_files))
        newly = ticket.get("newly_attached") or []
        ev_str = ", ".join(f"`{f}`" for f in attached_files) if attached_files else "`None`"
        sync_note = (
            f" — synced **{len(newly)}** missing file(s) ({', '.join(f'`{f}`' for f in newly)})"
            if newly and ticket.get("already_raised")
            else ""
        )
        if ticket.get("description_synced"):
            sync_note += " and updated Jira Evidence section"
        warn_line = f"\n⚠️ **Attachment Warning:** {ticket['attachment_warning']}" if ticket.get("attachment_warning") else ""
        return (
            f"{desk_reply}\n\n"
            f"### 🏷️ Sheriff Jira Ticket Created{already} (Bug #{bug_id})\n"
            f"**Jira Ticket:** [{ticket.get('key')}]({ticket.get('url')})\n"
            f"**Jira Status:** {placement}\n"
            f"**Priority:** `{ticket.get('priority')}` | **Label:** `{ticket.get('label')}` | **Reporter:** `{ticket.get('reporter')}`\n"
            f"**Coroner Evidence Attached:** **{attached_cnt}** file(s) ({ev_str}){sync_note}{warn_line}"
        )

    if target_agent == "watcher":
        if result.get("mode") == "full_sweep":
            sw = result.get("sweep") or {}
            lines = [
                f"{desk_reply}\n",
                "### 👁️ Watcher System-Wide Sweep Complete",
                f"- **Projects Audited:** `{sw.get('projects_count', 0)}` | **Modules Monitored:** `{sw.get('modules_monitored', 0)}`",
                f"- **AI Agents Active:** `{sw.get('agents_monitored', 0)}` | **Logs Audited:** `{sw.get('logs_audited', 0)}`",
                f"- **Successes:** `{sw.get('success_count', 0)}` | **Changes:** `{sw.get('changes_count', 0)}` | **Failures Flagged:** `{sw.get('failures_count', 0)}`",
                f"- **Notifications Dispatched:** `{sw.get('notified_channels') or 'ws'}`",
            ]
            if sw.get("failures_detected"):
                lines.append("\n**Recent Failures Flagged:**")
                for fitem in sw["failures_detected"][:5]:
                    lines.append(f"  - `{fitem.get('agent')}` (`{fitem.get('module')}`): `{fitem.get('action')}`")
            return "\n".join(lines)
        retest = result.get("result", {}).get("retest", {})
        sanity = result.get("result", {}).get("sanity")
        lines = [
            f"{desk_reply}\n",
            f"### 👁️ Watcher Retest (Bug #{bug_id})",
            f"**Outcome:** `{retest.get('new_status')}`",
        ]
        if sanity:
            lines.append(f"**Sanity Testers:** {sanity.get('summary')}")
        return "\n".join(lines)

    if target_agent == "sanity":
        sanity = result.get("sanity", {})
        return (
            f"{desk_reply}\n\n"
            f"### 🧪 Sanity Testers Report (Bug #{bug_id})\n"
            f"**Status:** `{sanity.get('overall_status', 'PASSED')}`\n"
            f"**Summary:** {sanity.get('summary', '')}"
        )

    if target_agent == "auto_test":
        return (
            f"{desk_reply}\n\n"
            f"### 🤖 Autonomous Bug Discovery Complete\n"
            f"Discovered **{result.get('discovered_count', 0)}** potential defect(s) and prepared **{result.get('processed_count', 0)}** bug report(s). "
            f"Sheriff is standing by — ask me to raise any of them to Jira **Backlog** when ready."
        )

    return desk_reply


def _format_organized_plan_reply(desk_reply: str, org_result: Dict[str, Any]) -> str:
    """Format a Markdown summary of all agent steps organized and executed by Desk Officer."""
    steps = org_result.get("steps_executed") or []
    bug_id = org_result.get("bug_id")
    jira_key = org_result.get("jira_key")
    blocked = org_result.get("blocked_agents") or []

    bug_label = f" (Bug #{bug_id}" + (f" · Jira `{jira_key}`" if jira_key else "") + ")" if bug_id else ""
    lines = []
    if desk_reply:
        lines.append(desk_reply)
        lines.append("")
    lines.append(f"### 🧭 Coordinated Multi-Agent Execution{bug_label}")

    icons = {
        "detective": "🔍",
        "coroner": "📸",
        "judge": "⚖️",
        "sheriff": "🏷️",
        "watcher": "👁️",
        "sanity": "🧪",
        "auto_test": "🤖",
    }

    for step in steps:
        akey = step.get("agent", "")
        icon = icons.get(akey, "⚙️")
        aname = step.get("agent_name") or akey.title()
        data = step.get("data") or {}
        st = step.get("status", "success")

        if st in ("missing_context", "not_found", "unknown"):
            lines.append(f"{step['step']}. {icon} **{aname}** (`{st}`): {data.get('message', 'Skipped')}")
            continue

        if akey == "detective":
            if data.get("mode") == "report":
                rep = (data.get("formatted_report") or "").strip()
                lines.append(f"{step['step']}. {icon} **{aname}**: Authored formatted bug report for Bug #{step.get('bug_id')}.")
                if rep:
                    lines.append(f"\n{rep}")
            else:
                d = data.get("result") or {}
                lines.append(
                    f"{step['step']}. {icon} **{aname}**: Traced **{d.get('title')}** — Root Cause: {d.get('root_cause')}"
                )
        elif akey == "coroner":
            ev = data.get("evidence") or []
            files = ", ".join(e.get("file_name", "file") for e in ev) or "none"
            lines.append(f"{step['step']}. {icon} **{aname}**: Captured **{len(ev)}** evidence file(s) (`{files}`).")
        elif akey == "judge":
            v = data.get("verdict") or {}
            lines.append(
                f"{step['step']}. {icon} **{aname}**: Verdict `{v.get('verdict', 'CONFIRMED')}` — {v.get('final_root_cause')}"
            )
        elif akey == "sheriff":
            ticket = data.get("jira_ticket") or {}
            if st == "failed" or ticket.get("error") or not ticket.get("key"):
                err_text = data.get("message") or ticket.get("error") or "Unknown Jira API error."
                lines.append(f"{step['step']}. ❌ **{aname}** (`failed`): Could not create ticket — {err_text}")
            else:
                note = f" *({ticket['placement_note']})*" if ticket.get("placement_note") else ""
                attached_files = ticket.get("evidence_attached") or []
                attached_cnt = ticket.get("attached_count", len(attached_files))
                newly = ticket.get("newly_attached") or []
                ev_part = (
                    f" with **{attached_cnt}** Coroner evidence file(s) ("
                    + ", ".join(f"`{f}`" for f in attached_files)
                    + ")"
                    if attached_cnt
                    else ""
                )
                if newly and ticket.get("already_raised"):
                    ev_part += f" *(synced {len(newly)} missing file(s))*"
                if ticket.get("attachment_warning"):
                    ev_part += f" ⚠️ {ticket['attachment_warning']}"
                lines.append(
                    f"{step['step']}. {icon} **{aname}**: Raised Jira [{ticket.get('key')}]({ticket.get('url')}) in status `{ticket.get('status', 'Backlog')}`{note}{ev_part}."
                )
        elif akey == "watcher":
            if data.get("mode") == "full_sweep":
                sw = data.get("sweep") or {}
                lines.append(
                    f"{step['step']}. {icon} **{aname}**: Audited {sw.get('modules_monitored', 0)} module(s), "
                    f"{sw.get('agents_monitored', 0)} agent(s), {sw.get('logs_audited', 0)} log(s) "
                    f"({sw.get('failures_count', 0)} failure(s))."
                )
            else:
                retest = (data.get("result") or {}).get("retest") or {}
                lines.append(f"{step['step']}. {icon} **{aname}**: Retest outcome `{retest.get('new_status') or retest.get('status')}`.")
        elif akey == "sanity":
            san = data.get("sanity") or {}
            lines.append(f"{step['step']}. {icon} **{aname}**: `{san.get('overall_status', 'PASSED')}` — {san.get('summary', '')}")
        elif akey == "auto_test":
            lines.append(
                f"{step['step']}. {icon} **{aname}**: Discovered {data.get('discovered_count', 0)} defect(s), processed {data.get('processed_count', 0)} report(s)."
            )

    if blocked or not org_result.get("sheriff_invoked"):
        lines.append("\n**Sheriff Status:** Standing by — Jira tickets are raised into `Backlog` only when you explicitly ask.")

    return "\n".join(lines)


@router.post("/upload")
async def upload_chat_files(
    files: List[UploadFile] = File(...),
    project_id: Optional[int] = Form(None),
    db: AsyncSession = Depends(get_db),
):
    """Upload images, screenshots, PDFs, Office docs, HAR traces, logs, JSON, CSV, or code files for chat analysis."""
    if project_id is not None:
        project = await db.get(Project, project_id)
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")
    if not files:
        raise HTTPException(status_code=400, detail="No files provided.")

    processed: List[Dict[str, Any]] = []
    for upload in files[:8]:
        raw_bytes = await upload.read()
        if not raw_bytes:
            await record_system_event(
                action="chat_upload_failed",
                summary=f"Empty chat upload rejected: '{upload.filename or 'file'}'",
                module="Chat Uploads",
                category="error",
                status="failed",
                level="error",
                agent_name="System",
                project_id=project_id,
                output_data={"filename": upload.filename, "reason": "File is empty"},
                notify=False,
                notify_ui_bell=False,
            )
            raise HTTPException(status_code=400, detail=f"Uploaded file '{upload.filename or 'file'}' is empty.")
        if len(raw_bytes) > MAX_UPLOAD_BYTES:
            await record_system_event(
                action="chat_upload_failed",
                summary=f"Oversize chat upload rejected: '{upload.filename or 'file'}'",
                module="Chat Uploads",
                category="error",
                status="failed",
                level="error",
                agent_name="System",
                project_id=project_id,
                output_data={"filename": upload.filename, "size_bytes": len(raw_bytes), "reason": "Exceeds 20 MB"},
                notify=False,
                notify_ui_bell=False,
            )
            raise HTTPException(
                status_code=400,
                detail=f"File '{upload.filename or 'file'}' exceeds the 20 MB upload limit.",
            )
        try:
            meta = process_uploaded_bytes(
                filename=upload.filename or "attachment",
                raw_bytes=raw_bytes,
                declared_mime=upload.content_type,
                save_to_disk=True,
            )
        except ValueError as ve:
            await record_system_event(
                action="chat_upload_failed",
                summary=f"Chat upload rejected ('{upload.filename or 'file'}'): {ve}",
                module="Chat Uploads",
                category="error",
                status="failed",
                level="error",
                agent_name="System",
                project_id=project_id,
                output_data={"filename": upload.filename, "error": str(ve)},
                notify=False,
                notify_ui_bell=False,
            )
            raise HTTPException(status_code=400, detail=str(ve))
        processed.append(sanitize_attachment_for_db(meta))

    await record_system_event(
        action="chat_files_uploaded",
        summary=f"Uploaded {len(processed)} attachment(s) for Desk Officer analysis: {', '.join(f.get('name', 'file') for f in processed)}",
        module="Chat Uploads",
        category="system_change",
        status="success",
        level="info",
        agent_name="System",
        project_id=project_id,
        output_data={
            "files": [
                {"name": f.get("name"), "kind": f.get("kind"), "size_bytes": f.get("size_bytes")}
                for f in processed
            ]
        },
        notify=False,
        notify_ui_bell=False,
    )

    return {"files": processed}


@router.get("/uploads/{stored_name}")
async def serve_chat_upload(stored_name: str):
    """Serve a stored chat upload safely without path traversal."""
    file_path = resolve_stored_upload(stored_name)
    if file_path is None:
        raise HTTPException(status_code=404, detail="Uploaded file not found")

    ext = file_path.suffix.lower()
    media_map = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
        ".gif": "image/gif",
        ".bmp": "image/bmp",
        ".pdf": "application/pdf",
        ".txt": "text/plain",
        ".log": "text/plain",
        ".md": "text/plain",
        ".json": "application/json",
        ".har": "application/json",
        ".csv": "text/csv",
        ".tsv": "text/tab-separated-values",
    }
    media_type = media_map.get(ext, "application/octet-stream")
    friendly_name = stored_name.split("_", 1)[1] if "_" in stored_name else stored_name
    disposition = (
        "inline"
        if media_type.startswith("image/") or media_type in ("application/pdf", "text/plain", "application/json")
        else "attachment"
    )
    return FileResponse(
        path=str(file_path),
        media_type=media_type,
        headers={"Content-Disposition": f'{disposition}; filename="{friendly_name}"'},
    )


@router.get("/{project_id}/history")
async def get_chat_history(project_id: int, limit: int = 80, db: AsyncSession = Depends(get_db)):
    """Load persisted multi-turn chat history for a project."""
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    res = await db.execute(
        select(ChatMessage)
        .where(ChatMessage.project_id == project_id)
        .order_by(ChatMessage.id.asc())
        .limit(limit)
    )
    return [m.to_dict() for m in res.scalars().all()]


@router.delete("/{project_id}/history")
async def clear_chat_history(project_id: int, db: AsyncSession = Depends(get_db)):
    """Clear stored chat history for a fresh Desk Officer conversation."""
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    await db.execute(delete(ChatMessage).where(ChatMessage.project_id == project_id))
    await db.commit()
    await record_system_event(
        action="chat_history_cleared",
        summary=f"Cleared Desk Officer chat history for project '{project.name}' (#{project_id})",
        module="Desk Officer Chat",
        category="system_change",
        status="success",
        level="change",
        agent_name="System",
        project_id=project_id,
        output_data={"project_id": project_id, "project_name": project.name},
        notify=False,
        notify_ui_bell=False,
    )
    return {"message": "Chat history cleared"}


@router.post("")
async def send_chat_message(data: ChatMessageRequest, db: AsyncSession = Depends(get_db)):
    """Process incoming chat message via interactive Desk Officer with optional WebSocket token streaming."""
    project = await db.get(Project, data.project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    text = (data.message or "").strip()
    llm_attachments, db_attachments = hydrate_attachments_for_request(
        data.attachments,
        include_binary_for_vision=True,
    )
    if not text and not db_attachments:
        raise HTTPException(status_code=400, detail="Please enter a message or attach an image/file.")

    orchestrator = await get_orchestrator(db)
    skills_context, _ = await orchestrator.get_project_skills_context(data.project_id)

    asset_snapshot = await orchestrator.asset_hub.collect_snapshot(data.project_id)
    prefetched_results = await orchestrator.asset_hub.prefetch_relevant_assets(
        message=text,
        project_id=data.project_id,
        snapshot=asset_snapshot,
    )
    asset_dossier = orchestrator.asset_hub.format_prompt_context(
        snapshot=asset_snapshot,
        extra_results=prefetched_results,
    )
    recent_bugs = asset_snapshot.get("bugs", [])[:10]

    # Load DB chat history if caller did not supply history explicitly
    if data.history is not None:
        history_dicts = [h.model_dump() for h in data.history]
    else:
        res_hist = await db.execute(
            select(ChatMessage)
            .where(ChatMessage.project_id == data.project_id)
            .order_by(ChatMessage.id.desc())
            .limit(16)
        )
        history_dicts = [m.to_dict() for m in reversed(res_hist.scalars().all())]

    await _persist_chat_message(
        db,
        project_id=data.project_id,
        sender="user",
        content=text,
        metadata={"attachments": db_attachments} if db_attachments else None,
    )

    async def broadcast_ws(payload: Dict[str, Any]):
        await manager.broadcast({**payload, "project_id": payload.get("project_id") or data.project_id})

    await broadcast_ws({
        "type": "agent_status",
        "project_id": data.project_id,
        "agent": "desk_officer",
        "status": "running",
        "message": "Desk Officer is thinking...",
    })

    streamed_any = False

    async def on_token(delta: str):
        nonlocal streamed_any
        streamed_any = True
        if data.stream_id:
            await broadcast_ws({
                "type": "desk_stream",
                "stream_id": data.stream_id,
                "project_id": data.project_id,
                "delta": delta,
            })

    try:
        decision = await orchestrator.desk_officer.triage_and_route(
            message=text,
            project_info=project.to_dict(),
            skills_context=skills_context,
            recent_bugs=recent_bugs,
            conversation_history=history_dicts,
            attachments=llm_attachments,
            project_id=data.project_id,
            stream_callback=on_token if data.stream_id else None,
            asset_dossier=asset_dossier,
            asset_snapshot=asset_snapshot,
            asset_hub=orchestrator.asset_hub,
            prefetched_results=prefetched_results,
        )

        if data.stream_id and streamed_any:
            await broadcast_ws({
                "type": "desk_stream_end",
                "stream_id": data.stream_id,
                "project_id": data.project_id,
            })

        action = decision.get("action", "chat")
        suggested_actions = decision.get("suggested_actions") or []
        decision_degraded = bool(decision.get("llm_degraded") or decision.get("llm_mode") == "fallback")
        llm_meta: Dict[str, Any] = {
            "llm_mode": decision.get("llm_mode", "fallback"),
            "llm_notice": decision.get("llm_notice"),
            "llm_provider": orchestrator.llm.provider,
            "llm_model": orchestrator.llm.model,
            "suggested_actions": suggested_actions,
            "llm_degraded": decision_degraded,
        }
        if decision.get("executed_asset_ops"):
            llm_meta["asset_ops"] = decision["executed_asset_ops"]

        # 1. Save persistent skill if detected
        if action == "save_skill":
            skill_type = decision.get("skill_type") or "requirement"
            skill_title = decision.get("skill_title") or (
                "Custom Bug Report Format" if skill_type == "report_format" else text.split("\n")[0][:60]
            )
            cleaned_content = (
                (strip_report_format_preamble(text) or text.strip())
                if skill_type == "report_format"
                else text.strip()
            )
            skill = Skill(
                project_id=data.project_id,
                type=skill_type,
                title=skill_title,
                content=cleaned_content,
            )
            db.add(skill)
            if skill_type == "report_format" and cleaned_content:
                project.report_format = cleaned_content
            await db.commit()
            await db.refresh(skill)
            await broadcast_ws({
                "type": "agent_status",
                "project_id": data.project_id,
                "agent": "desk_officer",
                "status": "success",
                "message": f"Saved {skill_type.replace('_', ' ')} skill.",
            })
            await record_system_event(
                action="skill_created_via_chat",
                summary=f"Desk Officer stored {skill_type} skill '{skill.title}' in project '{project.name}'",
                module="Skills",
                category="system_change",
                status="success",
                level="change",
                agent_name="Desk Officer",
                project_id=data.project_id,
                output_data={"skill_id": skill.id, "type": skill.type, "title": skill.title},
                notify=False,
                notify_ui_bell=False,
            )
            if skill_type == "report_format":
                msg = (
                    "✅ I have updated **Project Configuration → Report Format** with your template:\n\n"
                    f"```\n{cleaned_content}\n```\n\n"
                    "Detective and Judge will strictly follow this format as-is for all bug reports in this project."
                )
            else:
                msg = f"📋 Requirement stored as a project skill: **{skill.title}**. All agents will now use this context."

            await _persist_chat_message(db, data.project_id, "agent", msg, agent="Desk Officer", metadata=llm_meta)
            return {
                "type": "skill_saved",
                "agent": "Desk Officer",
                "response": msg,
                "data": skill.to_dict(),
                **llm_meta,
            }

        # 2. Main Workflow triggered explicitly (e.g. "Create a bug report")
        if action == "run_main_workflow":
            include_sheriff = bool(decision.get("also_raise_jira", False))
            await broadcast_ws({
                "type": "agent_status",
                "project_id": data.project_id,
                "agent": "desk_officer",
                "status": "success",
                "message": "Dispatched main workflow (Detective → Coroner → Judge).",
            })

            pipeline_result = await orchestrator.process_bug_pipeline(
                bug_input=decision.get("bug_summary") or text,
                project_id=data.project_id,
                broadcast_callback=broadcast_ws,
                include_sheriff=include_sheriff,
                bug_report_id=decision.get("bug_report_id"),
                attachments=decision.get("attachments"),
            )
            llm_degraded = bool(pipeline_result.get("llm_degraded") or decision_degraded)
            llm_meta["llm_degraded"] = llm_degraded

            if pipeline_result.get("status") == "needs_clarification":
                jv = pipeline_result.get("judge_verdict") or {}
                clarify_response = (
                    f"### ⚖️ Judge Needs Clarification on Bug #{pipeline_result['bug_id']}\n\n"
                    f"**Title:** {pipeline_result.get('title')}\n"
                    f"**Verdict:** `{jv.get('verdict', 'REJECTED')}`\n"
                    f"**Reason:** {jv.get('judge_notes') or 'Root cause needs additional verification.'}\n"
                    f"**Current Evidence:** {len(pipeline_result.get('evidence', []))} file(s) captured.\n\n"
                    "Please share a bit more detail (exact steps, expected vs. actual behavior, or endpoint/error), then tell me to **create the bug report** again."
                )
                llm_meta["suggested_actions"] = ["Investigate this bug", "Show me all built-in agents"]
                llm_meta["pipeline_result"] = pipeline_result
                await _persist_chat_message(db, data.project_id, "agent", clarify_response, agent="Desk Officer", metadata=llm_meta)
                return {
                    "type": "clarification_needed",
                    "agent": "Desk Officer",
                    "response": clarify_response,
                    "pipeline_result": pipeline_result,
                    "evidence": pipeline_result.get("evidence", []),
                    **llm_meta,
                }

            jira_ticket = pipeline_result.get("jira_ticket")
            if jira_ticket and jira_ticket.get("key") and not jira_ticket.get("error"):
                att_files = jira_ticket.get("evidence_attached") or []
                att_cnt = jira_ticket.get("attached_count", len(att_files))
                att_line = (
                    f"**Sheriff Jira Attachments:** **{att_cnt}** evidence file(s)"
                    + (f" ({', '.join(f'`{f}`' for f in att_files)})" if att_files else "")
                    + "\n"
                )
                if jira_ticket.get("attachment_warning"):
                    att_line += f"⚠️ **Attachment Warning:** {jira_ticket['attachment_warning']}\n"
                jira_line = (
                    f"**Jira Ticket:** [{jira_ticket['key']}]({jira_ticket['url']}) "
                    f"(Status: `{jira_ticket['status']}`)\n"
                    f"**Priority:** `{jira_ticket['priority']}` | **Label:** `{jira_ticket['label']}` | **Reporter:** `{jira_ticket['reporter']}`\n"
                    f"{att_line}"
                )
                tail_line = "The Watcher Agent is monitoring this ticket for the `In QA` transition."
                workflow_actions = ["Ask Watcher to retest this bug", "Show me all built-in agents"]
            elif jira_ticket and jira_ticket.get("error"):
                jira_line = (
                    f"**Jira Ticket Creation Failed (`{jira_ticket.get('project_key')}`):** {jira_ticket.get('error')}\n"
                )
                tail_line = ""
                workflow_actions = [
                    f"Tell Sheriff to raise bug #{pipeline_result['bug_id']} to Backlog",
                    "Show me all built-in agents",
                ]
            else:
                jira_line = (
                    f"**Jira Ticket:** Not raised yet — **Sheriff** is standing by.\n"
                    f"Say **\"Tell Sheriff to raise bug #{pipeline_result['bug_id']}\"** (or click **Raise to Backlog** in Tracked Defects) whenever you want it logged to Jira **Backlog**.\n"
                )
                tail_line = ""
                workflow_actions = [
                    f"Tell Sheriff to raise bug #{pipeline_result['bug_id']} to Backlog",
                    "Show me all built-in agents",
                ]

            degraded_note = (
                "\n\n> ⚠️ *Fallback mode active: configure a valid LLM API key in Settings for full AI analysis.*"
                if llm_degraded
                else ""
            )

            summary_response = (
                f"### 🎯 Bug Report #{pipeline_result['bug_id']} Created!\n\n"
                f"**Title:** {pipeline_result.get('title')}\n"
                f"**Judge Confirmed Root Cause:** {pipeline_result.get('root_cause')}\n"
                f"{jira_line}"
                f"**Evidence:** {len(pipeline_result.get('evidence', []))} file(s) captured by Coroner.\n\n"
                f"**Formatted Bug Report (Detective):**\n\n{pipeline_result.get('formatted_report', '')}\n"
                + (f"\n{tail_line}" if tail_line else "")
                + degraded_note
            )

            llm_meta["suggested_actions"] = workflow_actions
            llm_meta["pipeline_result"] = pipeline_result
            await _persist_chat_message(db, data.project_id, "agent", summary_response, agent="Desk Officer", metadata=llm_meta)
            return {
                "type": "pipeline_complete",
                "agent": "Desk Officer",
                "response": summary_response,
                "pipeline_result": pipeline_result,
                "evidence": pipeline_result.get("evidence", []),
                **llm_meta,
            }

        # 3. Organized Custom Multi-Agent Execution Plan
        if action == "organize_agents":
            plan_steps = decision.get("agent_plan") or []
            allow_sheriff = bool(decision.get("also_raise_jira", False))
            await broadcast_ws({
                "type": "agent_status",
                "project_id": data.project_id,
                "agent": "desk_officer",
                "status": "running",
                "message": f"Organizing coordinated {len(plan_steps)}-step agent workflow...",
            })
            org_result = await orchestrator.organize_agents(
                project_id=data.project_id,
                plan_steps=plan_steps,
                default_bug_input=decision.get("bug_summary") or text,
                default_bug_id=decision.get("bug_report_id"),
                default_jira_key=decision.get("jira_key"),
                allow_sheriff=allow_sheriff,
                default_force_new=bool(decision.get("force_new_ticket", False)),
                default_jira_project_key=decision.get("jira_project_key"),
                broadcast_callback=broadcast_ws,
                attachments=decision.get("attachments"),
            )
            llm_degraded = bool(org_result.get("llm_degraded") or decision_degraded)
            llm_meta["llm_degraded"] = llm_degraded
            reply_text = _format_organized_plan_reply(decision.get("reply", ""), org_result)
            if llm_degraded and "Fallback mode active" not in reply_text:
                reply_text += "\n\n> ⚠️ *Fallback mode active: configure a valid LLM API key in Settings for full AI analysis.*"
            llm_meta["organized_plan"] = org_result
            if org_result.get("sheriff_invoked"):
                llm_meta["suggested_actions"] = ["Ask Watcher to retest this bug", "Show me all built-in agents"]
            elif org_result.get("bug_id"):
                llm_meta["suggested_actions"] = [
                    f"Tell Sheriff to raise bug #{org_result['bug_id']} to Backlog",
                    "Check my Jira In QA list",
                ]
            await _persist_chat_message(db, data.project_id, "agent", reply_text, agent="Desk Officer", metadata=llm_meta)
            return {
                "type": "agents_organized",
                "agent": "Desk Officer",
                "response": reply_text,
                "data": org_result,
                **llm_meta,
            }

        # 4. Specific Built-in Agent Assignment
        if action == "assign_agent":
            target_agent = decision.get("target_agent", "detective")
            await broadcast_ws({
                "type": "agent_status",
                "project_id": data.project_id,
                "agent": "desk_officer",
                "status": "success",
                "message": f"Assigned {target_agent.replace('_', ' ').title()} to handle request.",
            })
            dispatch_result = await orchestrator.dispatch_agent(
                target_agent=target_agent,
                project_id=data.project_id,
                bug_input=decision.get("bug_summary") or text,
                bug_report_id=decision.get("bug_report_id"),
                jira_key=decision.get("jira_key"),
                force_new=bool(decision.get("force_new_ticket", False)),
                target_project_key=decision.get("jira_project_key"),
                broadcast_callback=broadcast_ws,
                attachments=decision.get("attachments"),
            )
            llm_degraded = bool(
                decision_degraded
                or dispatch_result.get("llm_degraded")
                or (dispatch_result.get("result") or {}).get("llm_degraded")
                or (dispatch_result.get("verdict") or {}).get("llm_degraded")
                or (dispatch_result.get("sanity") or {}).get("llm_degraded")
            )
            llm_meta["llm_degraded"] = llm_degraded
            reply_text = _format_dispatch_reply(decision.get("reply", ""), target_agent, dispatch_result)
            if llm_degraded and "Fallback mode active" not in reply_text:
                reply_text += "\n\n> ⚠️ *Fallback mode active: configure a valid LLM API key in Settings for full AI analysis.*"
            llm_meta["assigned_agent"] = target_agent
            await _persist_chat_message(db, data.project_id, "agent", reply_text, agent="Desk Officer", metadata=llm_meta)
            return {
                "type": "agent_dispatched",
                "agent": "Desk Officer",
                "assigned_agent": target_agent,
                "response": reply_text,
                "data": dispatch_result,
                "evidence": dispatch_result.get("evidence", []),
                **llm_meta,
            }

        # 5. Pre-workflow Conversational Triage Chat
        await broadcast_ws({
            "type": "agent_status",
            "project_id": data.project_id,
            "agent": "desk_officer",
            "status": "success",
            "message": "Desk Officer replied in chat.",
        })
        reply_text = decision.get("reply") or ""
        if decision_degraded and "Fallback mode active" not in reply_text and "Settings" not in reply_text:
            reply_text += "\n\n> ⚠️ *Fallback mode active: configure a valid LLM API key in Settings for full AI analysis.*"
        await _persist_chat_message(db, data.project_id, "agent", reply_text, agent="Desk Officer", metadata=llm_meta)
        return {
            "type": "chat",
            "agent": "Desk Officer",
            "response": reply_text,
            **llm_meta,
        }
    except HTTPException:
        raise
    except Exception as exc:
        await broadcast_ws({
            "type": "agent_status",
            "project_id": data.project_id,
            "agent": "desk_officer",
            "status": "failed",
            "message": f"Desk Officer encountered an error: {str(exc)[:160]}",
        })
        await record_system_event(
            action="chat_execution_failed",
            summary=f"Desk Officer chat execution failed in project '{project.name}': {str(exc)[:220]}",
            module="Desk Officer Chat",
            category="error",
            status="failed",
            level="error",
            agent_name="Desk Officer",
            project_id=data.project_id,
            input_data={"message": text[:300]},
            output_data={"error": str(exc)},
        )
        return JSONResponse(
            status_code=500,
            content={"detail": f"Chat execution failed: {exc}"},
            headers={"X-Watcher-Logged": "1"},
        )
