"""Chat Router - Desk Officer conversational triage & multi-agent dispatch endpoint."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel
from typing import Optional, List, Dict, Any

from database import get_db
from models.project import Project
from models.bug_report import BugReport
from models.settings import Settings
from models.skill import Skill
from agents.orchestrator import AgentOrchestrator
from services.llm_router import LLMRouter
from routers.websocket import manager

router = APIRouter(prefix="/api/chat", tags=["chat"])


class ChatHistoryItem(BaseModel):
    role: Optional[str] = None
    sender: Optional[str] = None
    agent: Optional[str] = None
    content: str
    isStatus: Optional[bool] = False


class ChatMessageRequest(BaseModel):
    project_id: int
    message: str
    attachments: Optional[List[Dict[str, Any]]] = None
    history: Optional[List[ChatHistoryItem]] = None


async def get_orchestrator(db: AsyncSession) -> AgentOrchestrator:
    res = await db.execute(select(Settings))
    all_settings = {s.key: s.value for s in res.scalars().all()}

    provider = all_settings.get("llm_provider", "openai")
    api_key = all_settings.get("llm_api_key", "")
    model = all_settings.get("llm_model", "gpt-4")
    default_reporter = all_settings.get("default_reporter", "Mohamed Kamel")

    llm = LLMRouter(provider=provider, api_key=api_key, model=model)
    return AgentOrchestrator(llm_router=llm, jira_settings=all_settings, default_reporter=default_reporter)


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
                f"### 🔍 Detective Bug Report Draft (Bug #{bug_id})\n\n{report}"
            )
        report_preview = f"\n\n**Formatted Bug Report Draft:**\n{report[:900]}" if report else ""
        return (
            f"{desk_reply}\n\n"
            f"### 🔍 Detective Investigation & Report (Bug #{bug_id})\n"
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
        return (
            f"{desk_reply}\n\n"
            f"### ⚖️ Judge Verification (Bug #{bug_id})\n"
            f"**Verdict:** `{v.get('verdict', 'CONFIRMED')}`\n"
            f"**Final Root Cause:** {v.get('final_root_cause')}\n"
            f"**Notes:** {v.get('judge_notes', '')}"
        )

    if target_agent == "sheriff":
        ticket = result.get("jira_ticket", {})
        already = " (already raised)" if ticket.get("already_raised") else ""
        return (
            f"{desk_reply}\n\n"
            f"### 🏷️ Sheriff Jira Ticket Created{already} (Bug #{bug_id})\n"
            f"**Jira Ticket:** [{ticket.get('key')}]({ticket.get('url')})\n"
            f"**Jira Status:** `{ticket.get('status', 'Backlog')}`\n"
            f"**Priority:** `{ticket.get('priority')}` | **Label:** `{ticket.get('label')}` | **Reporter:** `{ticket.get('reporter')}`"
        )

    if target_agent == "watcher":
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


@router.post("")
async def send_chat_message(data: ChatMessageRequest, db: AsyncSession = Depends(get_db)):
    """Process incoming chat message via Desk Officer before optionally dispatching agents or workflows."""
    project = await db.get(Project, data.project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    text = data.message.strip()
    orchestrator = await get_orchestrator(db)
    skills_context, _ = await orchestrator.get_project_skills_context(data.project_id)

    res_bugs = await db.execute(
        select(BugReport)
        .where(BugReport.project_id == data.project_id)
        .order_by(BugReport.created_at.desc())
        .limit(10)
    )
    recent_bugs = [b.to_dict() for b in res_bugs.scalars().all()]

    async def broadcast_ws(payload: Dict[str, Any]):
        await manager.broadcast({**payload, "project_id": payload.get("project_id") or data.project_id})

    await broadcast_ws({
        "type": "agent_status",
        "project_id": data.project_id,
        "agent": "desk_officer",
        "status": "running",
        "message": "Desk Officer is reviewing your message...",
    })

    history_dicts = [h.model_dump() for h in (data.history or [])]
    decision = await orchestrator.desk_officer.triage_and_route(
        message=text,
        project_info=project.to_dict(),
        skills_context=skills_context,
        recent_bugs=recent_bugs,
        conversation_history=history_dicts,
        attachments=data.attachments,
        project_id=data.project_id,
    )

    action = decision.get("action", "chat")

    # 1. Save persistent skill if detected
    if action == "save_skill":
        skill_type = decision.get("skill_type") or "requirement"
        skill_title = decision.get("skill_title") or (
            "Custom Bug Report Format" if skill_type == "report_format" else text.split("\n")[0][:60]
        )
        skill = Skill(
            project_id=data.project_id,
            type=skill_type,
            title=skill_title,
            content=text,
        )
        db.add(skill)
        await db.commit()
        await db.refresh(skill)
        await broadcast_ws({
            "type": "agent_status",
            "project_id": data.project_id,
            "agent": "desk_officer",
            "status": "success",
            "message": f"Saved {skill_type.replace('_', ' ')} skill.",
        })
        if skill_type == "report_format":
            msg = "✅ I have recorded your custom **Bug Report Format** as a project skill. The Detective Agent will strictly format all future bug reports according to this template."
        else:
            msg = f"📋 Requirement stored as a project skill: **{skill.title}**. All agents will now use this context."
        return {
            "type": "skill_saved",
            "agent": "Desk Officer",
            "response": msg,
            "data": skill.to_dict(),
            "llm_degraded": False,
        }

    # 2. Main Workflow triggered explicitly (e.g. "Create a bug report")
    if action == "run_main_workflow":
        include_sheriff = bool(decision.get("also_raise_jira", False))
        await broadcast_ws({
            "type": "agent_status",
            "project_id": data.project_id,
            "agent": "desk_officer",
            "status": "success",
            "message": "Dispatched main workflow (Detective → Coroner → Judge → Formatted Report).",
        })

        pipeline_result = await orchestrator.process_bug_pipeline(
            bug_input=decision.get("bug_summary") or text,
            project_id=data.project_id,
            broadcast_callback=broadcast_ws,
            include_sheriff=include_sheriff,
        )

        jira_ticket = pipeline_result.get("jira_ticket")
        if jira_ticket:
            jira_line = (
                f"**Jira Ticket:** [{jira_ticket['key']}]({jira_ticket['url']}) "
                f"(Status: `{jira_ticket['status']}`)\n"
                f"**Priority:** `{jira_ticket['priority']}` | **Label:** `{jira_ticket['label']}` | **Reporter:** `{jira_ticket['reporter']}`\n"
            )
            tail_line = "The Watcher Agent is monitoring this ticket for the `In QA` transition."
        else:
            jira_line = (
                f"**Jira Ticket:** Not raised yet — **Sheriff** is standing by.\n"
                f"Say **\"Tell Sheriff to raise bug #{pipeline_result['bug_id']}\"** (or click **Raise to Backlog** in Tracked Defects) whenever you want it logged to Jira **Backlog**.\n"
            )
            tail_line = ""

        llm_degraded = bool(pipeline_result.get("llm_degraded") or decision.get("llm_degraded"))
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
            f"**Formatted Report Preview:**\n{pipeline_result.get('formatted_report', '')[:1200]}\n"
            + (f"\n{tail_line}" if tail_line else "")
            + degraded_note
        )

        return {
            "type": "pipeline_complete",
            "agent": "Desk Officer",
            "response": summary_response,
            "pipeline_result": pipeline_result,
            "evidence": pipeline_result.get("evidence", []),
            "llm_degraded": llm_degraded,
        }

    # 3. Specific Built-in Agent Assignment
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
            broadcast_callback=broadcast_ws,
        )
        llm_degraded = bool(
            decision.get("llm_degraded")
            or (dispatch_result.get("result") or {}).get("llm_degraded")
            or (dispatch_result.get("verdict") or {}).get("llm_degraded")
            or (dispatch_result.get("sanity") or {}).get("llm_degraded")
        )
        reply_text = _format_dispatch_reply(decision.get("reply", ""), target_agent, dispatch_result)
        if llm_degraded and "Fallback mode active" not in reply_text:
            reply_text += "\n\n> ⚠️ *Fallback mode active: configure a valid LLM API key in Settings for full AI analysis.*"
        return {
            "type": "agent_dispatched",
            "agent": "Desk Officer",
            "assigned_agent": target_agent,
            "response": reply_text,
            "data": dispatch_result,
            "evidence": dispatch_result.get("evidence", []),
            "llm_degraded": llm_degraded,
        }

    # 4. Pre-workflow Conversational Triage Chat (Default!)
    llm_degraded = bool(decision.get("llm_degraded"))
    reply_text = decision.get("reply") or ""
    if llm_degraded:
        reply_text += "\n\n> ⚠️ *Fallback mode active: configure a valid LLM API key in Settings for full AI analysis.*"
    await broadcast_ws({
        "type": "agent_status",
        "project_id": data.project_id,
        "agent": "desk_officer",
        "status": "success",
        "message": "Chatting with tester before starting workflow.",
    })
    return {
        "type": "chat",
        "agent": "Desk Officer",
        "response": reply_text,
        "llm_degraded": llm_degraded,
    }
