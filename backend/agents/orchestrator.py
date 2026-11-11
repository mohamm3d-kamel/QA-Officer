"""Agent Pipeline Orchestrator - Coordinates all AI Agents through the QA lifecycle."""
import asyncio
import io
import logging
import re
import shutil
import uuid
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional, Callable, Awaitable
from PIL import Image
from sqlalchemy import select
from sqlalchemy.orm import selectinload

import config
from agents.desk_officer import AGENT_ROSTER, DeskOfficerAgent
from agents.detective import DetectiveAgent
from agents.coroner import CoronerAgent
from agents.judge import JudgeAgent
from services.report_formatter import ensure_evidence_in_report, strip_report_format_preamble
from agents.sheriff import SheriffAgent
from agents.watcher import WatcherAgent
from agents.sanity_tester import SanityTesterAgent
from services.attachment_processor import (
    IMAGE_EXTS,
    _encode_image_for_vision,
    detect_mime_type,
    format_attachment_text_block,
    resolve_stored_upload,
)
from services.jira_client import is_real_jira_issue_key
from services.llm_router import LLMRouter
from services.notification_service import NotificationService, _as_bool
from services.project_assets import ProjectAssetHub
from services.watcher_bus import record_system_event
from database import async_session
from models.bug_report import BugReport
from models.evidence import Evidence
from models.skill import Skill
from models.project import Project

logger = logging.getLogger(__name__)


class AgentOrchestrator:
    """Coordinates Desk Officer, Detective, Coroner, Judge, Sheriff, Watcher, and Sanity Testers."""

    def __init__(self, llm_router: LLMRouter, jira_settings: Dict[str, str], default_reporter: str = "Mohamed Kamel"):
        self.llm = llm_router
        self.jira_settings = jira_settings
        self.default_reporter = default_reporter
        self.asset_hub = ProjectAssetHub(jira_settings)

        self.desk_officer = DeskOfficerAgent(self.llm)
        self.detective = DetectiveAgent(self.llm)
        self.coroner = CoronerAgent()
        self.judge = JudgeAgent(self.llm)
        self.sheriff = SheriffAgent(default_reporter=default_reporter)
        self.notifier = NotificationService.from_settings(jira_settings)
        self.sanity_tester = SanityTesterAgent(self.llm)
        self.watcher = WatcherAgent(
            self.detective,
            self.coroner,
            self.notifier,
            judge=self.judge,
            sanity_tester=self.sanity_tester,
        )

        self.agent_registry = {
            "desk_officer": self.desk_officer,
            "detective": self.detective,
            "coroner": self.coroner,
            "judge": self.judge,
            "sheriff": self.sheriff,
            "watcher": self.watcher,
            "sanity": self.sanity_tester,
        }
        if hasattr(self.desk_officer, "attach_context"):
            self.desk_officer.attach_context(
                asset_hub=self.asset_hub,
                agent_registry=self.agent_registry,
            )

    def _browser_config(self) -> tuple[List[str], bool]:
        browsers = config.get_enabled_browsers(self.jira_settings)
        headless = str(self.jira_settings.get("browser_headless", "true")).lower() == "true"
        return browsers, headless

    async def get_project_skills_context(self, project_id: int, max_chars: int = 12000) -> tuple[str, Optional[str]]:
        """Retrieve non-template project skills/requirements and the authoritative Project Report Format."""
        async with async_session() as session:
            project = await session.get(Project, project_id)
            result = await session.execute(
                select(Skill)
                .where(Skill.project_id == project_id)
                .order_by(Skill.created_at.asc(), Skill.id.asc())
            )
            skills = result.scalars().all()

        context_lines = []
        report_format = strip_report_format_preamble(project.report_format) if project else None
        fallback_custom_format = None
        fallback_default_format = None

        for s in skills:
            if s.type == "report_format":
                cleaned = strip_report_format_preamble(s.content)
                if cleaned:
                    if (s.title or "").strip().lower() == "default qa bug report format":
                        fallback_default_format = cleaned
                    else:
                        fallback_custom_format = cleaned
                continue
            context_lines.append(f"[{s.type.upper()}] {s.title}:\n{s.content}\n")

        if not report_format:
            report_format = fallback_custom_format or fallback_default_format

        combined = "\n".join(context_lines)
        if len(combined) > max_chars:
            combined = combined[:max_chars] + "\n...[skills context truncated to fit prompt budget]..."

        return combined, report_format

    async def _make_notifier(
        self,
        broadcast_callback: Optional[Callable[[Dict[str, Any]], Awaitable[None]]],
        project_id: Optional[int] = None,
    ):
        async def notify(agent: str, status: str, message: str, data: Any = None):
            if broadcast_callback:
                try:
                    await broadcast_callback({
                        "type": "agent_status",
                        "project_id": project_id,
                        "agent": agent,
                        "status": status,
                        "message": message,
                        "data": data,
                        "timestamp": datetime.utcnow().isoformat(),
                    })
                except Exception as e:
                    logger.warning(f"Broadcast error: {e}")

        return notify

    async def _attach_user_images_as_evidence(
        self,
        bug_id: int,
        attachments: Optional[List[Dict[str, Any]]],
    ) -> List[Dict[str, Any]]:
        """Persist user-uploaded screenshot attachments as PNG Evidence assets on the bug report."""
        if not bug_id or not attachments:
            return []

        config.EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
        added: List[Dict[str, Any]] = []

        async with async_session() as session:
            res_existing = await session.execute(
                select(Evidence).where(Evidence.bug_report_id == bug_id)
            )
            existing = res_existing.scalars().all()
            existing_sigs = set()
            for ev in existing:
                fsize = None
                if ev.file_path and Path(ev.file_path).exists():
                    fsize = Path(ev.file_path).stat().st_size
                existing_sigs.add((ev.file_name or "", fsize))

            for att in attachments:
                if not isinstance(att, dict) or not att.get("is_image"):
                    continue
                src_path: Optional[Path] = None
                if att.get("file_path") and Path(att["file_path"]).exists():
                    src_path = Path(att["file_path"])
                elif att.get("stored_name"):
                    src_path = resolve_stored_upload(att["stored_name"])
                if src_path is None:
                    continue

                orig_name = str(att.get("name") or src_path.name)[:240]
                dest_name = f"user_upload_{bug_id}_{uuid.uuid4().hex[:8]}.png"
                dest_path = config.EVIDENCE_DIR / dest_name
                try:
                    raw_bytes = src_path.read_bytes()
                    with Image.open(io.BytesIO(raw_bytes)) as img:
                        if img.mode not in ("RGB", "RGBA", "L", "LA"):
                            img = img.convert("RGBA")
                        img.save(dest_path, format="PNG")
                except Exception:
                    shutil.copy2(src_path, dest_path)

                dest_size = dest_path.stat().st_size if dest_path.exists() else None
                if (orig_name, dest_size) in existing_sigs:
                    try:
                        dest_path.unlink(missing_ok=True)
                    except Exception:
                        pass
                    continue

                existing_sigs.add((orig_name, dest_size))
                summary = att.get("summary") or "chat upload"
                ev = Evidence(
                    bug_report_id=bug_id,
                    type="screenshot",
                    file_path=str(dest_path),
                    file_name=orig_name,
                    stage="detection",
                    description=f"User uploaded screenshot: {orig_name} ({summary})"[:490],
                )
                session.add(ev)
                added.append({
                    "type": "screenshot",
                    "file_path": str(dest_path),
                    "file_name": orig_name,
                    "stage": "detection",
                    "description": ev.description,
                })

            if added:
                await session.commit()

        return added

    @staticmethod
    def _hydrate_evidence_as_vision_attachments(
        evidence_items: Optional[List[Any]],
        existing_attachments: Optional[List[Dict[str, Any]]] = None,
        max_images: int = 4,
    ) -> List[Dict[str, Any]]:
        """Merge persisted image Evidence files with chat attachments so Detective/Judge can inspect them visually."""
        merged: List[Dict[str, Any]] = list(existing_attachments or [])
        if not evidence_items:
            return merged

        seen_binary_keys = set()
        img_count = 0
        for att in merged:
            if not isinstance(att, dict):
                continue
            if att.get("is_image") and att.get("data_base64"):
                img_count += 1
                if att.get("name"):
                    seen_binary_keys.add(("name", str(att["name"]).lower()))
                if att.get("file_path"):
                    seen_binary_keys.add(("path", str(att["file_path"]).lower()))

        for ev in evidence_items:
            if img_count >= max_images:
                break
            if isinstance(ev, dict):
                ev_type = (ev.get("type") or "").lower()
                fpath = ev.get("file_path") or ""
                fname = ev.get("file_name") or (Path(fpath).name if fpath else "evidence.png")
                desc = ev.get("description") or ""
            else:
                ev_type = (getattr(ev, "type", None) or "").lower()
                fpath = getattr(ev, "file_path", None) or ""
                fname = getattr(ev, "file_name", None) or (Path(fpath).name if fpath else "evidence.png")
                desc = getattr(ev, "description", None) or ""

            if not fpath:
                continue
            p = Path(fpath)
            if not p.exists() or not p.is_file():
                continue
            ext = p.suffix.lower()
            if ev_type != "screenshot" and ext not in IMAGE_EXTS:
                continue
            if ("path", str(p).lower()) in seen_binary_keys or ("name", str(fname).lower()) in seen_binary_keys:
                continue

            try:
                raw_bytes = p.read_bytes()
                if not raw_bytes:
                    continue
                mime_type = detect_mime_type(fname, raw_bytes)
                llm_mime, b64 = _encode_image_for_vision(raw_bytes, mime_type)
                merged.append({
                    "name": fname,
                    "mime_type": mime_type,
                    "llm_mime_type": llm_mime,
                    "is_image": True,
                    "data_base64": b64,
                    "file_path": str(p),
                    "summary": desc or f"Bug evidence screenshot ({fname})",
                })
                seen_binary_keys.add(("path", str(p).lower()))
                seen_binary_keys.add(("name", str(fname).lower()))
                img_count += 1
            except Exception as exc:
                logger.warning(f"Failed hydrating evidence image '{fname}' for vision analysis: {exc}")

        return merged

    async def process_bug_pipeline(
        self,
        bug_input: str,
        project_id: int,
        broadcast_callback: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None,
        include_sheriff: bool = False,
        include_coroner: bool = False,
        include_judge: bool = False,
        live_probe: bool = False,
        bug_report_id: Optional[int] = None,
        attachments: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """Execute the bug reporting pipeline.

        By default runs Detective only (traces root cause and authors formatted bug report strictly matching Project Report Format).
        Coroner, Judge, and Sheriff stay idle on standby unless explicitly requested (``include_coroner``, ``include_judge``, ``include_sheriff``).
        Reuses an in-progress draft BugReport when ``bug_report_id`` points to an unformatted draft in the same project.
        """
        notify = await self._make_notifier(broadcast_callback, project_id=project_id)
        browsers, headless = self._browser_config()

        # Fetch Project
        async with async_session() as session:
            project = await session.get(Project, project_id)
            if not project:
                raise ValueError(f"Project #{project_id} not found")
            project_info = project.to_dict()

        skills_context, report_format = await self.get_project_skills_context(project_id)
        local_repo = self.asset_hub._scan_local_repo(project_info.get("repo_url") or "")
        if local_repo and local_repo.get("files"):
            skills_context = (
                f"{skills_context}\n\n[LOCAL REPO FILES ({local_repo['root']})]:\n"
                + ", ".join(local_repo["files"][:30])
            ).strip()

        # Reuse an active draft/investigated bug report or create a new BugReport record
        existing_evidence: List[Dict[str, Any]] = []
        prior_investigation_note = ""
        reused_bug_id: Optional[int] = None

        if bug_report_id:
            async with async_session() as session:
                res_existing = await session.execute(
                    select(BugReport)
                    .options(selectinload(BugReport.evidence))
                    .where(BugReport.id == bug_report_id, BugReport.project_id == project_id)
                )
                existing_bug = res_existing.scalar_one_or_none()
                if (
                    existing_bug
                    and existing_bug.status == "draft"
                    and not (existing_bug.formatted_report or "").strip()
                ):
                    reused_bug_id = existing_bug.id
                    existing_evidence = [
                        {
                            "type": ev.type,
                            "file_path": ev.file_path,
                            "file_name": ev.file_name,
                            "stage": ev.stage,
                            "description": ev.description,
                        }
                        for ev in (existing_bug.evidence or [])
                    ]
                    if existing_bug.root_cause:
                        prior_investigation_note = (
                            f"\n\nPrior Investigation on Bug #{existing_bug.id}:\n"
                            f"- Preliminary Title: {existing_bug.title}\n"
                            f"- Preliminary Root Cause: {existing_bug.root_cause}\n"
                            f"- Preliminary Steps: {existing_bug.steps_to_reproduce or 'None'}"
                        )

        if reused_bug_id is not None:
            bug_id = reused_bug_id
            effective_bug_input = f"{bug_input}{prior_investigation_note}".strip()
        else:
            effective_bug_input = bug_input
            async with async_session() as session:
                bug = BugReport(
                    project_id=project_id,
                    title="Analyzing bug...",
                    description=bug_input,
                    status="draft",
                )
                session.add(bug)
                await session.commit()
                await session.refresh(bug)
                bug_id = bug.id

        if attachments and "[Attachment:" not in effective_bug_input:
            att_notes = "\n\n".join(format_attachment_text_block(a, max_chars=2500) for a in attachments)
            if att_notes:
                effective_bug_input = f"{effective_bug_input}\n\nAttached Files & Images:\n{att_notes}".strip()

        user_evidence = await self._attach_user_images_as_evidence(bug_id, attachments)
        if user_evidence:
            existing_evidence.extend(user_evidence)

        # 1. DETECTIVE (Pass 1)
        detective_attachments = self._hydrate_evidence_as_vision_attachments(
            existing_evidence,
            existing_attachments=attachments,
        )
        await notify("detective", "running", "Detective is tracing the bug and investigating root cause...")
        detective_result = await self.detective.trace_bug(
            bug_input=effective_bug_input,
            skills_context=skills_context,
            project_info=project_info,
            project_id=project_id,
            bug_report_id=bug_id,
            attachments=detective_attachments,
            report_format_template=report_format,
            browsers=browsers,
            headless=headless,
            allow_live_probe=bool(live_probe or include_coroner),
        )
        await notify("detective", "success", f"Detective identified root cause for: {detective_result.get('title')}", detective_result)

        # Update bug record with detective findings
        async with async_session() as session:
            b = await session.get(BugReport, bug_id)
            b.title = detective_result.get("title", "Reported Defect")
            if detective_result.get("description"):
                b.description = detective_result["description"]
            b.root_cause = detective_result.get("root_cause")
            b.steps_to_reproduce = (
                "\n".join(detective_result.get("steps_to_reproduce", []))
                if isinstance(detective_result.get("steps_to_reproduce"), list)
                else str(detective_result.get("steps_to_reproduce"))
            )
            b.severity = detective_result.get("severity", "medium")
            b.label = detective_result.get("label", "Front-End")
            b.module = detective_result.get("module", "General")
            await session.commit()

        # 2. CORONER (Only captures screenshots/video when explicitly requested; otherwise reuses existing uploads/captures and stays idle)
        has_coroner_evidence = any(
            not (ev.get("description") or "").startswith("User uploaded screenshot:")
            for ev in existing_evidence
        )
        runtime_telemetry: List[Dict[str, Any]] = []
        evidence_list = list(existing_evidence)
        if include_coroner:
            if evidence_list and has_coroner_evidence:
                await notify("coroner", "running", f"Coroner is attaching {len(evidence_list)} captured evidence file(s) from investigation...")
                await notify("coroner", "success", f"Coroner verified {len(evidence_list)} evidence files", evidence_list)
            else:
                await notify("coroner", "running", "Coroner is capturing video and screenshot evidence during reproduction pass...")
                coroner_evidence, runtime_telemetry = await self.coroner.capture_with_telemetry(
                    bug_data=detective_result,
                    project_info=project_info,
                    stage="detection",
                    bug_report_id=bug_id,
                    project_id=project_id,
                    browsers=browsers,
                    headless=headless,
                )
                evidence_list = evidence_list + (coroner_evidence or [])
                await notify("coroner", "success", f"Coroner verified {len(evidence_list)} evidence file(s)", evidence_list)
        else:
            await notify(
                "coroner",
                "idle",
                "Standing by — Coroner captures evidence only when explicitly asked.",
                {"bug_id": bug_id, "existing_evidence_count": len(evidence_list)},
            )

        # 3. JUDGE (Only verifies when explicitly requested; otherwise stays idle and leaves BugReport as Detective draft)
        judge_verdict: Optional[Dict[str, Any]] = None
        final_root_cause = detective_result.get("root_cause") or detective_result.get("description") or "Pending confirmation"
        if include_judge:
            evidence_summary = ", ".join(
                f"{e.get('type', 'file')}: {e.get('file_name', 'asset')}"
                for e in (evidence_list or [])
            ) or "No visual evidence captured"
            judge_bug_data = {**detective_result, "evidence_summary": evidence_summary}

            judge_attachments = self._hydrate_evidence_as_vision_attachments(
                evidence_list,
                existing_attachments=attachments,
            )
            await notify("judge", "running", "Judge is independently verifying the Detective findings...")
            judge_verdict = await self.judge.verify_root_cause(
                bug_data=judge_bug_data,
                skills_context=skills_context,
                project_info=project_info,
                project_id=project_id,
                bug_report_id=bug_id,
                attachments=judge_attachments,
                report_format_template=report_format,
                runtime_telemetry=runtime_telemetry,
            )

            verdict_label = str(judge_verdict.get("verdict") or "CONFIRMED").upper()
            is_confirmed = bool(judge_verdict.get("confirmed", True)) and verdict_label != "REJECTED"

            if not is_confirmed:
                await notify(
                    "judge",
                    "failed",
                    f"Judge REJECTED root cause (needs clarification): {judge_verdict.get('judge_notes') or judge_verdict.get('final_root_cause', '')}",
                    judge_verdict,
                )
                async with async_session() as session:
                    b = await session.get(BugReport, bug_id)
                    b.root_cause = judge_verdict.get("final_root_cause") or b.root_cause
                    b.status = "draft"
                    await session.commit()
                return {
                    "bug_id": bug_id,
                    "title": detective_result.get("title"),
                    "root_cause": judge_verdict.get("final_root_cause"),
                    "judge_verdict": judge_verdict,
                    "formatted_report": "",
                    "evidence": evidence_list,
                    "jira_ticket": None,
                    "status": "needs_clarification",
                    "coroner_invoked": include_coroner,
                    "judge_invoked": True,
                    "llm_degraded": bool(detective_result.get("llm_degraded") or judge_verdict.get("llm_degraded")),
                }

            await notify("judge", "success", f"Judge Verdict: {judge_verdict.get('verdict')} - {(judge_verdict.get('final_root_cause') or '')[:80]}...", judge_verdict)
            final_root_cause = judge_verdict.get("final_root_cause") or final_root_cause

            async with async_session() as session:
                b = await session.get(BugReport, bug_id)
                b.root_cause = final_root_cause
                b.severity = judge_verdict.get("severity_adjustment") or b.severity
                b.status = "confirmed"
                await session.commit()

            report_verdict = {**judge_verdict, "judge_invoked": True}
            final_status = "confirmed"
        else:
            await notify(
                "judge",
                "idle",
                "Standing by — Judge verifies findings only when explicitly asked.",
                {"bug_id": bug_id},
            )
            report_verdict = {
                "final_root_cause": final_root_cause,
                "judge_notes": "Authored by Detective Agent (Judge on standby).",
                "severity_adjustment": detective_result.get("severity", "medium"),
                "judge_invoked": False,
            }
            final_status = "draft"

        # 4. DETECTIVE (Authors final bug report strictly adhering to Project Report Format)
        await notify(
            "detective",
            "running",
            "Detective is authoring the final bug report following Project Report Format..."
            if report_format
            else "Detective is authoring the final bug report in standard QA format...",
        )
        formatted_report = await self.detective.generate_report(
            bug_data=detective_result,
            judge_verdict=report_verdict,
            report_format_template=report_format,
            project_info=project_info,
            project_id=project_id,
            bug_report_id=bug_id,
            evidence_list=evidence_list,
        )
        async with async_session() as session:
            b = await session.get(BugReport, bug_id)
            b.formatted_report = formatted_report
            await session.commit()
        await notify("detective", "success", "Detective completed formatted bug report", {"preview": formatted_report[:200]})

        ticket_result = None

        # 5. SHERIFF (Only raises ticket in Jira 'Backlog' when explicitly requested)
        if include_sheriff:
            ticket_result = await self.raise_jira_ticket(
                bug_report_id=bug_id,
                broadcast_callback=broadcast_callback,
                include_coroner=include_coroner,
            )
            if ticket_result and ticket_result.get("key") and not ticket_result.get("error"):
                final_status = "reported"
        else:
            await notify(
                "sheriff",
                "idle",
                "Standing by — Sheriff will raise to Jira Backlog only when requested.",
                {"bug_id": bug_id},
            )

        return {
            "bug_id": bug_id,
            "title": detective_result.get("title"),
            "root_cause": final_root_cause,
            "judge_verdict": judge_verdict,
            "formatted_report": formatted_report,
            "evidence": evidence_list,
            "jira_ticket": ticket_result,
            "status": final_status,
            "coroner_invoked": include_coroner,
            "judge_invoked": include_judge,
            "llm_degraded": bool(
                detective_result.get("llm_degraded")
                or (judge_verdict or {}).get("llm_degraded")
                or getattr(self.detective, "last_llm_degraded", False)
            ),
        }

    async def run_auto_test(
        self,
        project_id: int,
        scope: str = "all",
        selected_modules: Optional[List[str]] = None,
        broadcast_callback: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None,
        live_probe: bool = False,
    ) -> Dict[str, Any]:
        """Run autonomous bug discovery with duplicate filtering and bounded concurrent Detective-only reporting."""
        notify = await self._make_notifier(broadcast_callback, project_id=project_id)
        browsers, headless = self._browser_config()

        async with async_session() as session:
            project = await session.get(Project, project_id)
            if not project:
                raise ValueError(f"Project #{project_id} not found")
            project_info = project.to_dict()

            existing_res = await session.execute(
                select(BugReport.title).where(BugReport.project_id == project_id)
            )
            seen_titles = {t.strip().lower() for t in existing_res.scalars().all() if t}

        skills_context, _ = await self.get_project_skills_context(project_id)

        await notify(
            "detective",
            "running",
            "Detective is analyzing requirements and probing the live app for defects..."
            if live_probe
            else "Detective is analyzing requirements for potential defects...",
        )
        discovered = await self.detective.autonomous_discover(
            skills_context=skills_context,
            project_info=project_info,
            scope=scope,
            selected_modules=selected_modules,
            browsers=browsers,
            headless=headless,
            allow_live_probe=live_probe,
        )

        unique_items: List[Dict[str, Any]] = []
        skipped_duplicates = 0
        for item in discovered:
            title_norm = (item.get("title") or "").strip().lower()
            if title_norm and title_norm in seen_titles:
                skipped_duplicates += 1
                continue
            if title_norm:
                seen_titles.add(title_norm)
            unique_items.append(item)

        to_process = unique_items[:3]
        semaphore = asyncio.Semaphore(2)

        async def _run_item(item: Dict[str, Any]) -> Dict[str, Any]:
            async with semaphore:
                return await self.process_bug_pipeline(
                    bug_input=f"{item.get('title')}: {item.get('description')}\nRoot Cause: {item.get('root_cause')}",
                    project_id=project_id,
                    broadcast_callback=broadcast_callback,
                    include_sheriff=False,
                    include_coroner=False,
                    include_judge=False,
                    live_probe=live_probe,
                )

        reports = list(await asyncio.gather(*(_run_item(it) for it in to_process)))
        await notify(
            "detective",
            "success",
            f"Auto-Test finished: {len(reports)} bug report(s) created ({skipped_duplicates} duplicate(s) skipped).",
            {"discovered_count": len(discovered), "processed_count": len(reports)},
        )
        return {
            "discovered_count": len(discovered),
            "processed_count": len(reports),
            "skipped_duplicates": skipped_duplicates,
            "reports": reports,
        }

    async def raise_jira_ticket(
        self,
        bug_report_id: int,
        broadcast_callback: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None,
        force_new: bool = False,
        target_project_key: Optional[str] = None,
        include_coroner: bool = False,
    ) -> Dict[str, Any]:
        """Explicitly invoke Sheriff to create a brand-new Jira ticket in 'Backlog' for a bug report."""
        browsers, headless = self._browser_config()

        async with async_session() as session:
            res = await session.execute(
                select(BugReport)
                .options(selectinload(BugReport.evidence))
                .where(BugReport.id == bug_report_id)
            )
            bug = res.scalar_one_or_none()
            if not bug:
                raise ValueError(f"Bug Report #{bug_report_id} not found")

            notify = await self._make_notifier(broadcast_callback, project_id=bug.project_id)
            project = await session.get(Project, bug.project_id)
            project_info = project.to_dict() if project else {"id": bug.project_id, "jira_project_key": "QA", "name": "Default"}
            effective_proj_key = (target_project_key or project_info.get("jira_project_key") or "QA").strip().upper()

            bug_id_val = bug.id
            bug_proj_id = bug.project_id
            bug_status = bug.status
            bug_jira_key = bug.jira_ticket_id
            bug_jira_url = bug.jira_ticket_url
            bug_formatted = bug.formatted_report
            bug_severity = bug.severity or "medium"
            bug_label = bug.label or "Front-End"

            evidence_list = []
            seen_ev_keys = set()
            for ev in (bug.evidence or []):
                ev_key = (ev.file_path or "", ev.file_name or "")
                if ev_key in seen_ev_keys:
                    continue
                seen_ev_keys.add(ev_key)
                evidence_list.append({
                    "type": ev.type,
                    "file_path": ev.file_path,
                    "file_name": ev.file_name,
                    "stage": ev.stage,
                    "description": ev.description,
                })

            bug_data = {
                "title": bug.title,
                "description": bug.description,
                "root_cause": bug.root_cause,
                "steps_to_reproduce": bug.steps_to_reproduce.splitlines() if bug.steps_to_reproduce else [],
                "severity": bug_severity,
                "label": bug_label,
                "module": bug.module or "General",
            }
        # Outer read session closed before Coroner / Sheriff open write transactions (avoids SQLite locks)
        if bug_jira_key and is_real_jira_issue_key(bug_jira_key, effective_proj_key) and not force_new:
            if not evidence_list and include_coroner:
                await notify("coroner", "running", f"Coroner is capturing missing evidence for Bug #{bug_id_val} before syncing {bug_jira_key}...")
                evidence_list = await self.coroner.capture_evidence(
                    bug_data=bug_data,
                    project_info=project_info,
                    stage="detection",
                    bug_report_id=bug_id_val,
                    project_id=bug_proj_id,
                    browsers=browsers,
                    headless=headless,
                )
                await notify("coroner", "success", f"Coroner captured {len(evidence_list)} evidence file(s)", evidence_list)

            sync_res = await self.sheriff.sync_evidence_to_existing_ticket(
                issue_key=bug_jira_key,
                evidence_list=evidence_list,
                formatted_report=bug_formatted,
                bug_data=bug_data,
                project_info=project_info,
                jira_settings=self.jira_settings,
                bug_report_id=bug_id_val,
            )
            all_attached = sync_res.get("all_attached") or sync_res.get("existing_attached") or []
            return {
                "key": bug_jira_key,
                "url": bug_jira_url,
                "status": "Backlog",
                "project_key": effective_proj_key,
                "reporter": self.default_reporter,
                "priority": bug_severity,
                "label": bug_label,
                "already_raised": True,
                "evidence_count": len(evidence_list),
                "attached_count": len(all_attached),
                "evidence_attached": all_attached,
                "newly_attached": sync_res.get("newly_attached", []),
                "failed_attachments": sync_res.get("failed_attachments", []),
                "description_synced": bool(sync_res.get("description_synced")),
            }

        # Attach already-collected evidence; invoke Coroner ONLY when explicitly requested
        if not evidence_list and include_coroner:
            await notify("coroner", "running", f"Coroner is capturing visual evidence for Bug #{bug_id_val} before Sheriff raises Jira ticket...")
            captured_ev = await self.coroner.capture_evidence(
                bug_data=bug_data,
                project_info=project_info,
                stage="detection",
                bug_report_id=bug_id_val,
                project_id=bug_proj_id,
                browsers=browsers,
                headless=headless,
            )
            evidence_list = captured_ev or []
            await notify("coroner", "success", f"Coroner captured {len(evidence_list)} evidence file(s)", evidence_list)
        elif not evidence_list:
            await notify(
                "coroner",
                "idle",
                "Standing by — Coroner captures evidence only when explicitly asked.",
                {"bug_id": bug_id_val},
            )

        _, active_report_format = await self.get_project_skills_context(bug_proj_id)
        base_report = bug_formatted or self.detective._fallback_report(
            bug_data=bug_data,
            judge_verdict={
                "final_root_cause": bug_data.get("root_cause") or bug_data.get("description") or "Pending confirmation",
                "judge_notes": "Submitted via Sheriff on tester request.",
                "severity_adjustment": bug_severity,
                "judge_invoked": bug_status == "confirmed",
            },
            project_info=project_info,
            report_format_template=active_report_format,
            evidence_list=evidence_list,
            bug_report_id=bug_id_val,
        )
        formatted_report = ensure_evidence_in_report(base_report, evidence_list, bug_data)

        ev_desc = (
            f"with {len(evidence_list)} attached evidence file(s)"
            if evidence_list
            else "(no evidence files attached — Coroner on standby)"
        )
        await notify(
            "sheriff",
            "running",
            f"Sheriff is creating Jira ticket in '{effective_proj_key}' Backlog {ev_desc}...",
        )
        ticket_result = await self.sheriff.create_jira_ticket(
            bug_data=bug_data,
            formatted_report=formatted_report,
            evidence_list=evidence_list,
            project_info=project_info,
            jira_settings=self.jira_settings,
            bug_report_id=bug_report_id,
            reporter_name=self.default_reporter,
            target_project_key=effective_proj_key,
            notify_on_success=False,
        )

        if ticket_result.get("error") or not ticket_result.get("key"):
            err_msg = ticket_result.get("error") or "Unknown Jira ticket creation failure."
            await notify(
                "sheriff",
                "failed",
                f"Sheriff failed to create Jira ticket in '{effective_proj_key}': {err_msg}",
                ticket_result,
            )
            return ticket_result

        ev_attached_cnt = ticket_result.get("attached_count", 0)
        ev_note = f" ({ev_attached_cnt} evidence file(s) attached)" if ev_attached_cnt else ""
        if ticket_result.get("attachment_warning"):
            ev_note += f" — Warning: {ticket_result['attachment_warning']}"
        await notify(
            "sheriff",
            "success",
            f"Sheriff created ticket: {ticket_result['key']} in 'Backlog'{ev_note}",
            ticket_result,
        )

        if not ticket_result.get("simulated") and _as_bool(self.jira_settings.get("watcher_enabled"), True):
            await self.notifier.notify_bug_created(
                bug_title=bug_data["title"],
                jira_key=ticket_result["key"],
                jira_url=ticket_result["url"],
                project_name=project_info.get("name"),
                module=bug_data.get("module") or "Jira Integration",
                bug_id=bug_report_id,
            )
        return ticket_result

    async def _resolve_target_bug(
        self,
        project_id: int,
        bug_report_id: Optional[int],
        bug_input: str,
        create_if_missing: bool = False,
        prefer_unraised: bool = False,
        jira_key: Optional[str] = None,
        allow_reported_fallback: bool = True,
    ) -> Optional[BugReport]:
        """Find a referenced bug report (by ID or Jira key), latest project bug, or optionally create a draft."""
        async with async_session() as session:
            if bug_report_id:
                res = await session.execute(
                    select(BugReport)
                    .options(selectinload(BugReport.evidence))
                    .where(BugReport.id == bug_report_id, BugReport.project_id == project_id)
                )
                return res.scalar_one_or_none()

            if jira_key:
                clean_key = jira_key.strip().upper()
                res = await session.execute(
                    select(BugReport)
                    .options(selectinload(BugReport.evidence))
                    .where(BugReport.project_id == project_id, BugReport.jira_ticket_id == clean_key)
                )
                existing = res.scalar_one_or_none()
                if existing:
                    return existing

                jira_res = await self.asset_hub.run_asset_tool(
                    project_id, "jira_get_issue", {"issue_key": clean_key}
                )
                if jira_res.get("ok") and jira_res.get("issue"):
                    iss = jira_res["issue"]
                    raw_st = (iss.get("status") or "").lower().replace(" ", "")
                    mapped_status = (
                        "in_qa"
                        if raw_st in ("inqa", "in_qa")
                        else ("resolved" if "deploy" in raw_st or "done" in raw_st else ("failed" if "fail" in raw_st else "reported"))
                    )
                    raw_prio = (iss.get("priority") or "medium").lower()
                    if raw_prio not in ("lowest", "low", "medium", "high", "highest"):
                        raw_prio = "medium"
                    labels = iss.get("labels") or []
                    bug = BugReport(
                        project_id=project_id,
                        jira_ticket_id=clean_key,
                        jira_ticket_url=iss.get("url"),
                        title=iss.get("summary") or f"Jira {clean_key}",
                        description=iss.get("description") or bug_input or iss.get("summary") or "",
                        status=mapped_status,
                        severity=raw_prio,
                        label=labels[0] if labels else "Front-End",
                        module="Jira",
                    )
                    session.add(bug)
                    await session.commit()
                    res_loaded = await session.execute(
                        select(BugReport)
                        .options(selectinload(BugReport.evidence))
                        .where(BugReport.id == bug.id)
                    )
                    return res_loaded.scalar_one_or_none()

                if not create_if_missing:
                    return None

                bug = BugReport(
                    project_id=project_id,
                    jira_ticket_id=clean_key,
                    title=f"Bug: {(bug_input or clean_key).strip()[:80]}",
                    description=bug_input.strip() or f"Tracked Jira ticket {clean_key}",
                    status="draft",
                )
                session.add(bug)
                await session.commit()
                res_loaded = await session.execute(
                    select(BugReport)
                    .options(selectinload(BugReport.evidence))
                    .where(BugReport.id == bug.id)
                )
                return res_loaded.scalar_one_or_none()

            if prefer_unraised:
                proj_obj = await session.get(Project, project_id)
                proj_key = (proj_obj.jira_project_key if proj_obj else None) or "QA"
                res = await session.execute(
                    select(BugReport)
                    .options(selectinload(BugReport.evidence))
                    .where(BugReport.project_id == project_id)
                    .order_by(BugReport.created_at.desc(), BugReport.id.desc())
                )
                unraised = [
                    candidate
                    for candidate in res.scalars().all()
                    if not candidate.jira_ticket_id or not is_real_jira_issue_key(candidate.jira_ticket_id, proj_key)
                ]
                for candidate in unraised:
                    if (candidate.formatted_report or "").strip() or candidate.status in ("confirmed", "reported"):
                        return candidate
                if unraised:
                    return unraised[0]

            if allow_reported_fallback:
                res = await session.execute(
                    select(BugReport)
                    .options(selectinload(BugReport.evidence))
                    .where(BugReport.project_id == project_id)
                    .order_by(BugReport.created_at.desc(), BugReport.id.desc())
                )
                latest = res.scalars().first()
                if latest:
                    return latest

            if create_if_missing and (bug_input.strip() or jira_key):
                bug = BugReport(
                    project_id=project_id,
                    title=f"Bug: {(bug_input or jira_key).strip()[:80]}",
                    description=bug_input.strip() or f"Imported from Jira {jira_key}",
                    jira_ticket_id=jira_key.strip().upper() if jira_key else None,
                    status="draft",
                )
                session.add(bug)
                await session.commit()
                res_loaded = await session.execute(
                    select(BugReport)
                    .options(selectinload(BugReport.evidence))
                    .where(BugReport.id == bug.id)
                )
                return res_loaded.scalar_one_or_none()

            return None

    async def dispatch_agent(
        self,
        target_agent: str,
        project_id: int,
        bug_input: str = "",
        bug_report_id: Optional[int] = None,
        broadcast_callback: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None,
        jira_key: Optional[str] = None,
        goal: str = "",
        shared_state: Optional[Dict[str, Any]] = None,
        update_jira: bool = False,
        force_new: bool = False,
        target_project_key: Optional[str] = None,
        attachments: Optional[List[Dict[str, Any]]] = None,
        live_probe: bool = False,
    ) -> Dict[str, Any]:
        """Assign and execute a single specialized built-in agent on behalf of Desk Officer."""
        notify = await self._make_notifier(broadcast_callback, project_id=project_id)
        browsers, headless = self._browser_config()
        agent_key = target_agent.lower().strip()
        shared = shared_state if shared_state is not None else {}
        effective_attachments = attachments if attachments is not None else shared.get("attachments")
        effective_bug_id = bug_report_id or shared.get("bug_id")
        clean_goal = (goal or "").strip()
        combined_input = (
            f"Assigned Goal: {clean_goal}\n\n{bug_input}".strip()
            if clean_goal and clean_goal not in (bug_input or "")
            else (bug_input or clean_goal)
        )

        async with async_session() as session:
            project = await session.get(Project, project_id)
            if not project:
                raise ValueError(f"Project #{project_id} not found")
            project_info = project.to_dict()

        skills_context, report_format = await self.get_project_skills_context(project_id)
        local_repo = self.asset_hub._scan_local_repo(project_info.get("repo_url") or "")
        if local_repo and local_repo.get("files"):
            skills_context = (
                f"{skills_context}\n\n[LOCAL REPO FILES ({local_repo['root']})]:\n"
                + ", ".join(local_repo["files"][:30])
            ).strip()

        if agent_key == "detective":
            latest_instruction = (
                bug_input.rsplit("Latest user instruction:", 1)[-1].strip()
                if "Latest user instruction:" in (bug_input or "")
                else (bug_input or "")
            )
            intent_probe = f"{clean_goal} {latest_instruction}".strip()
            wants_report_authoring = bool(
                re.search(
                    r"\b(?:format|write|draft|author|compile|generate|prepare)\s+(?:a\s+|the\s+|this\s+)?(?:formatted\s+)?(?:bug\s+)?reports?\b",
                    intent_probe,
                    re.IGNORECASE,
                )
            )
            wants_investigation_step = bool(
                re.search(
                    r"\b(?:investigate|trace|debug|reproduce|analyze|root\s*cause)\b",
                    intent_probe,
                    re.IGNORECASE,
                )
            ) or not wants_report_authoring

            if effective_bug_id or jira_key:
                bug = await self._resolve_target_bug(
                    project_id,
                    effective_bug_id,
                    combined_input,
                    create_if_missing=False,
                    jira_key=jira_key,
                )
                if not bug:
                    ref = f"Jira {jira_key}" if jira_key else f"Bug #{effective_bug_id}"
                    return {"agent": "Detective", "status": "not_found", "message": f"{ref} was not found in this project."}
                bug_id = bug.id
                effective_input = combined_input or bug.description or bug.title
                bug_ev_items = list(bug.evidence or [])
            elif wants_report_authoring and not wants_investigation_step:
                bug = await self._resolve_target_bug(
                    project_id,
                    None,
                    combined_input,
                    create_if_missing=True,
                    prefer_unraised=True,
                    jira_key=None,
                    allow_reported_fallback=False,
                )
                if not bug:
                    return {
                        "agent": "Detective",
                        "status": "missing_context",
                        "message": "Please describe the bug or reference a bug # / Jira key so Detective can draft the report.",
                    }
                bug_id = bug.id
                effective_input = combined_input or bug.description or bug.title
                bug_ev_items = list(bug.evidence or [])
            else:
                async with async_session() as session:
                    bug = BugReport(
                        project_id=project_id,
                        title="Analyzing bug...",
                        description=combined_input or "Investigating reported bug",
                        status="draft",
                    )
                    session.add(bug)
                    await session.commit()
                    await session.refresh(bug)
                    bug_id = bug.id
                effective_input = combined_input or "Investigate current application behavior and stored skills."
                bug_ev_items = []

            if effective_attachments and "[Attachment:" not in (effective_input or ""):
                att_notes = "\n\n".join(format_attachment_text_block(a, max_chars=2500) for a in effective_attachments)
                if att_notes:
                    effective_input = f"{effective_input}\n\nAttached Files & Images:\n{att_notes}".strip()

            user_ev = await self._attach_user_images_as_evidence(bug_id, effective_attachments)
            detective_attachments = self._hydrate_evidence_as_vision_attachments(
                bug_ev_items + list(user_ev or []),
                existing_attachments=effective_attachments,
            )
            detective_result = shared.get("detective_result")
            run_trace = wants_investigation_step or (
                not (bug.root_cause or "").strip() and not detective_result
            )

            if run_trace:
                await notify("detective", "running", "Detective is tracing the bug and investigating root cause...")
                detective_result = await self.detective.trace_bug(
                    bug_input=effective_input,
                    skills_context=skills_context,
                    project_info=project_info,
                    project_id=project_id,
                    bug_report_id=bug_id,
                    attachments=detective_attachments,
                    report_format_template=report_format,
                    browsers=browsers,
                    headless=headless,
                    allow_live_probe=bool(live_probe or shared.get("live_probe")),
                )
                async with async_session() as session:
                    b = await session.get(BugReport, bug_id)
                    b.title = detective_result.get("title", b.title)
                    if detective_result.get("description"):
                        b.description = detective_result["description"]
                    b.root_cause = detective_result.get("root_cause")
                    b.steps_to_reproduce = (
                        "\n".join(detective_result.get("steps_to_reproduce", []))
                        if isinstance(detective_result.get("steps_to_reproduce"), list)
                        else str(detective_result.get("steps_to_reproduce"))
                    )
                    b.severity = detective_result.get("severity", "medium")
                    b.label = detective_result.get("label", "Front-End")
                    b.module = detective_result.get("module", "General")
                    await session.commit()
                    bug = b
                shared["bug_id"] = bug_id
                shared["detective_result"] = detective_result
                if not wants_report_authoring:
                    await notify(
                        "detective",
                        "success",
                        f"Detective identified root cause for: {detective_result.get('title')}",
                        detective_result,
                    )
                    return {
                        "agent": "Detective",
                        "status": "success",
                        "mode": "investigate",
                        "bug_id": bug_id,
                        "result": detective_result,
                    }

            # Author formatted bug report strictly following Project Report Format
            det_res = detective_result or {}
            async with async_session() as session:
                res_bug = await session.execute(
                    select(BugReport)
                    .options(selectinload(BugReport.evidence))
                    .where(BugReport.id == bug_id)
                )
                refreshed_bug = res_bug.scalar_one_or_none() or bug
            db_ev_items = [
                {
                    "type": ev.type,
                    "file_path": ev.file_path,
                    "file_name": ev.file_name,
                    "stage": ev.stage,
                    "description": ev.description,
                }
                for ev in (refreshed_bug.evidence or [])
            ]
            combined_ev: List[Dict[str, Any]] = []
            seen_rep_ev = set()
            for item in db_ev_items + list(shared.get("evidence_list") or []) + list(user_ev or []):
                k = (item.get("file_path") or "", item.get("file_name") or "")
                if k in seen_rep_ev:
                    continue
                seen_rep_ev.add(k)
                combined_ev.append(item)

            bug_data = {
                "title": det_res.get("title") or refreshed_bug.title,
                "environment": det_res.get("environment") or "",
                "description": det_res.get("description") or refreshed_bug.description or combined_input,
                "expected_behavior": det_res.get("expected_behavior") or "",
                "actual_behavior": det_res.get("actual_behavior") or "",
                "root_cause": det_res.get("root_cause") or refreshed_bug.root_cause or "",
                "steps_to_reproduce": det_res.get("steps_to_reproduce")
                or (refreshed_bug.steps_to_reproduce.splitlines() if refreshed_bug.steps_to_reproduce else []),
                "severity": det_res.get("severity") or refreshed_bug.severity or "medium",
                "label": det_res.get("label") or refreshed_bug.label or "Front-End",
                "module": det_res.get("module") or refreshed_bug.module or "General",
            }
            judge_verdict = shared.get("judge_verdict") or {
                "final_root_cause": refreshed_bug.root_cause or refreshed_bug.description or "Pending confirmation",
                "judge_notes": "Authored by Detective via Desk Officer assignment.",
                "severity_adjustment": refreshed_bug.severity or "medium",
                "judge_invoked": bool(refreshed_bug.status == "confirmed"),
            }
            await notify("detective", "running", f"Detective is authoring the bug report for Bug #{bug_id}...")
            formatted_report = await self.detective.generate_report(
                bug_data=bug_data,
                judge_verdict=judge_verdict,
                report_format_template=report_format,
                project_info=project_info,
                project_id=project_id,
                bug_report_id=bug_id,
                evidence_list=combined_ev,
            )
            async with async_session() as session:
                b = await session.get(BugReport, bug_id)
                b.formatted_report = formatted_report
                await session.commit()
            shared["bug_id"] = bug_id
            shared["formatted_report"] = formatted_report
            await notify("detective", "success", "Detective completed formatted bug report", {"preview": formatted_report[:200]})
            return {
                "agent": "Detective",
                "status": "success",
                "mode": "report",
                "bug_id": bug_id,
                **({"result": det_res} if det_res else {}),
                "formatted_report": formatted_report,
            }

        if agent_key == "coroner":
            bug = await self._resolve_target_bug(
                project_id,
                effective_bug_id,
                combined_input,
                create_if_missing=True,
                prefer_unraised=True,
                jira_key=jira_key,
                allow_reported_fallback=False,
            )
            if not bug:
                return {"agent": "Coroner", "status": "missing_context", "message": "Please describe the bug or reference a bug # / Jira key first so Coroner knows what to capture."}
            det_res = shared.get("detective_result") or {}
            bug_data = {
                "title": det_res.get("title") or bug.title,
                "description": det_res.get("description") or bug.description or combined_input,
                "root_cause": det_res.get("root_cause") or bug.root_cause or "Under investigation",
                "steps_to_reproduce": det_res.get("steps_to_reproduce")
                or (bug.steps_to_reproduce.splitlines() if bug.steps_to_reproduce else [combined_input or bug.title]),
                "severity": det_res.get("severity") or bug.severity or "medium",
                "label": det_res.get("label") or bug.label or "Front-End",
                "module": det_res.get("module") or bug.module or "General",
                "browser_actions": det_res.get("browser_actions", []),
            }
            user_ev = await self._attach_user_images_as_evidence(bug.id, effective_attachments)
            await notify("coroner", "running", f"Coroner is capturing screenshot and video evidence for Bug #{bug.id}...")
            coroner_ev, coroner_tel = await self.coroner.capture_with_telemetry(
                bug_data=bug_data,
                project_info=project_info,
                stage="detection",
                bug_report_id=bug.id,
                project_id=project_id,
                browsers=browsers,
                headless=headless,
            )
            if coroner_tel:
                shared["runtime_telemetry"] = coroner_tel
            raw_evidence = [
                {
                    "type": ev.type,
                    "file_path": ev.file_path,
                    "file_name": ev.file_name,
                    "stage": ev.stage,
                    "description": ev.description,
                }
                for ev in (bug.evidence or [])
            ] + (user_ev or []) + (coroner_ev or [])
            evidence_list: List[Dict[str, Any]] = []
            seen_ev_keys = set()
            for item in raw_evidence:
                ek = (item.get("file_path") or "", item.get("file_name") or "")
                if ek in seen_ev_keys:
                    continue
                seen_ev_keys.add(ek)
                evidence_list.append(item)

            if (bug.formatted_report or "").strip() and evidence_list:
                updated_report = ensure_evidence_in_report(bug.formatted_report, evidence_list, bug_data)
                if updated_report and updated_report.strip() != (bug.formatted_report or "").strip():
                    async with async_session() as session:
                        b = await session.get(BugReport, bug.id)
                        if b and (b.formatted_report or "").strip():
                            b.formatted_report = updated_report
                            await session.commit()
                    shared["formatted_report"] = updated_report

            shared["bug_id"] = bug.id
            shared["evidence_list"] = evidence_list
            shared["coroner_invoked"] = True
            await notify("coroner", "success", f"Coroner captured {len(coroner_ev or evidence_list)} evidence file(s)", evidence_list)
            return {"agent": "Coroner", "status": "success", "bug_id": bug.id, "evidence": evidence_list}

        if agent_key == "judge":
            bug = await self._resolve_target_bug(
                project_id,
                effective_bug_id,
                combined_input,
                create_if_missing=True,
                prefer_unraised=True,
                jira_key=jira_key,
                allow_reported_fallback=False,
            )
            if not bug:
                return {"agent": "Judge", "status": "missing_context", "message": "Please describe a bug or reference a bug # / Jira key for Judge to verify."}
            user_ev = await self._attach_user_images_as_evidence(bug.id, effective_attachments)
            det_res = shared.get("detective_result") or {}
            ev_items = shared.get("evidence_list") or [
                {
                    "type": ev.type,
                    "file_path": ev.file_path,
                    "file_name": ev.file_name,
                    "description": ev.description,
                }
                for ev in (bug.evidence or [])
            ] + user_ev
            judge_attachments = self._hydrate_evidence_as_vision_attachments(
                ev_items,
                existing_attachments=effective_attachments,
            )
            evidence_summary = ", ".join(
                f"{e.get('type', 'file')}: {e.get('file_name', 'asset')}" for e in ev_items
            )
            bug_data = {
                "title": det_res.get("title") or bug.title,
                "environment": det_res.get("environment") or "",
                "description": det_res.get("description") or bug.description or combined_input,
                "expected_behavior": det_res.get("expected_behavior") or "",
                "actual_behavior": det_res.get("actual_behavior") or "",
                "root_cause": det_res.get("root_cause") or bug.root_cause or combined_input or "Initial analysis pending",
                "steps_to_reproduce": det_res.get("steps_to_reproduce") or bug.steps_to_reproduce or combined_input,
                "severity": det_res.get("severity") or bug.severity or "medium",
                "label": det_res.get("label") or bug.label or "Front-End",
                "evidence_summary": evidence_summary,
            }
            await notify("judge", "running", f"Judge is independently verifying Bug #{bug.id}...")
            judge_verdict = await self.judge.verify_root_cause(
                bug_data=bug_data,
                skills_context=skills_context,
                project_info=project_info,
                project_id=project_id,
                bug_report_id=bug.id,
                attachments=judge_attachments,
                report_format_template=report_format,
                runtime_telemetry=shared.get("runtime_telemetry"),
            )
            verdict_label = str(judge_verdict.get("verdict") or "CONFIRMED").upper()
            is_confirmed = bool(judge_verdict.get("confirmed", True)) and verdict_label != "REJECTED"
            async with async_session() as session:
                b = await session.get(BugReport, bug.id)
                b.root_cause = judge_verdict.get("final_root_cause", b.root_cause)
                b.status = "confirmed" if is_confirmed else "draft"
                await session.commit()
            shared["bug_id"] = bug.id
            shared["judge_invoked"] = True
            shared["judge_verdict"] = {**judge_verdict, "judge_invoked": is_confirmed}
            if is_confirmed:
                await notify("judge", "success", f"Judge Verdict: {judge_verdict.get('verdict')} - {judge_verdict.get('final_root_cause', '')[:80]}", judge_verdict)
            else:
                await notify("judge", "failed", f"Judge REJECTED root cause: {judge_verdict.get('judge_notes') or judge_verdict.get('final_root_cause', '')}", judge_verdict)
            return {"agent": "Judge", "status": "success" if is_confirmed else "needs_clarification", "bug_id": bug.id, "verdict": judge_verdict}

        if agent_key == "sheriff":
            bug = await self._resolve_target_bug(
                project_id,
                effective_bug_id,
                combined_input,
                create_if_missing=False,
                prefer_unraised=True,
                jira_key=None,
            )
            if not bug:
                return {
                    "agent": "Sheriff",
                    "status": "missing_context",
                    "message": "There is no bug report ready to raise yet. Say **\"Create a bug report\"** first so Detective can investigate and draft the report — then Sheriff will raise it to Jira **Backlog**.",
                }
            ticket_result = await self.raise_jira_ticket(
                bug_report_id=bug.id,
                broadcast_callback=broadcast_callback,
                force_new=bool(force_new or shared.get("force_new_ticket")),
                target_project_key=target_project_key or shared.get("target_project_key"),
                include_coroner=bool(shared.get("include_coroner", False)),
            )
            shared["bug_id"] = bug.id
            if ticket_result.get("error") or not ticket_result.get("key"):
                return {
                    "agent": "Sheriff",
                    "status": "failed",
                    "bug_id": bug.id,
                    "message": ticket_result.get("error") or "Sheriff failed to create Jira ticket.",
                    "jira_ticket": ticket_result,
                }
            return {"agent": "Sheriff", "status": "success", "bug_id": bug.id, "jira_ticket": ticket_result}

        if agent_key == "watcher":
            low_in = (combined_input or "").lower()
            wants_sweep = (
                not effective_bug_id
                and not jira_key
                and any(
                    kw in low_in
                    for kw in (
                        "scan",
                        "sweep",
                        "audit",
                        "everything",
                        "all module",
                        "all agent",
                        "all log",
                        "health",
                        "monitor",
                        "in qa",
                        "in-qa",
                        "in_qa",
                        "queue",
                        "list",
                        "tickets",
                    )
                )
            )
            if not wants_sweep:
                bug = await self._resolve_target_bug(
                    project_id,
                    effective_bug_id,
                    combined_input,
                    create_if_missing=False,
                    jira_key=jira_key,
                    allow_reported_fallback=bool(effective_bug_id or jira_key or "retest" in low_in or "verify" in low_in),
                )
                if jira_key and not bug:
                    return {
                        "agent": "Watcher",
                        "status": "not_found",
                        "message": f"Jira ticket {jira_key} was not found in Jira Cloud or tracked defects.",
                    }
                if bug and (effective_bug_id or jira_key or "retest" in low_in or "verify" in low_in):
                    shared["bug_id"] = bug.id
                    result = await self.retest_and_sanity_pipeline(
                        bug_report_id=bug.id,
                        broadcast_callback=broadcast_callback,
                        update_jira=update_jira,
                        capture_failure_evidence=True,
                        allow_live_probe=True,
                    )
                    return {"agent": "Watcher", "status": "success", "mode": "retest", "bug_id": bug.id, "result": result}

            await notify("watcher", "running", "Watcher is checking Jira 'In QA' queue and auditing all modules, AI agents, and logs...")
            sweep_res = await self.watcher.run_full_system_sweep(
                jira_settings=self.jira_settings,
                project_id=project_id,
                include_app_health=True,
                notify=_as_bool(self.jira_settings.get("watcher_enabled"), True),
            )
            await notify("watcher", "success", sweep_res.get("summary", "Watcher system-wide sweep completed."), sweep_res)
            return {"agent": "Watcher", "status": "success", "mode": "full_sweep", "sweep": sweep_res}

        if agent_key == "sanity":
            bug = await self._resolve_target_bug(
                project_id,
                effective_bug_id,
                combined_input,
                create_if_missing=False,
                jira_key=jira_key,
            )
            if not bug:
                return {"agent": "Sanity Testers", "status": "missing_context", "message": "No tracked bug report or Jira ticket found to run sanity regression checks against."}
            shared["bug_id"] = bug.id
            await notify("sanity", "running", f"Sanity Testers checking impacted modules around Bug #{bug.id}...")
            sanity_result = await self.sanity_tester.run_sanity_checks(
                resolved_bug=bug.to_dict(),
                skills_context=skills_context,
                project_info=project_info,
                project_id=project_id,
                bug_report_id=bug.id,
                browsers=browsers,
                headless=headless,
            )
            await notify("sanity", "success", f"Sanity Testing complete: {sanity_result.get('summary')}", sanity_result)
            return {"agent": "Sanity Testers", "status": "success", "bug_id": bug.id, "sanity": sanity_result}

        if agent_key == "auto_test":
            auto_res = await self.run_auto_test(
                project_id=project_id,
                scope="all",
                broadcast_callback=broadcast_callback,
                live_probe=bool(live_probe or shared.get("live_probe")),
            )
            reports = auto_res.get("reports") or []
            if reports:
                shared["bug_id"] = reports[-1].get("bug_id")
            return {
                "agent": "Auto-Test",
                "status": "success",
                "bug_id": shared.get("bug_id"),
                **auto_res,
            }

        return {"agent": target_agent, "status": "unknown", "message": f"Unknown agent '{target_agent}'."}

    async def organize_agents(
        self,
        project_id: int,
        plan_steps: List[Any],
        default_bug_input: str = "",
        default_bug_id: Optional[int] = None,
        default_jira_key: Optional[str] = None,
        allow_sheriff: bool = False,
        allow_coroner: bool = False,
        allow_judge: bool = False,
        live_probe: bool = False,
        update_jira: bool = False,
        default_force_new: bool = False,
        default_jira_project_key: Optional[str] = None,
        broadcast_callback: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None,
        attachments: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """Organize and sequentially execute a custom multi-agent plan coordinated by Desk Officer.

        Strict guardrail: Coroner, Judge, and Sheriff are filtered out unless explicitly requested
        (``allow_coroner=True``, ``allow_judge=True``, ``allow_sheriff=True``).
        """
        start_time = datetime.utcnow()
        notify = await self._make_notifier(broadcast_callback, project_id=project_id)
        roster_names = {a["key"]: a["name"] for a in AGENT_ROSTER}
        roster_names["auto_test"] = "Auto-Test Discovery"

        normalized_steps: List[Dict[str, Any]] = []
        blocked_agents: List[str] = []

        for raw_step in plan_steps or []:
            if isinstance(raw_step, str):
                step = {"agent": raw_step.lower().strip(), "goal": ""}
            elif isinstance(raw_step, dict):
                step = {
                    "agent": str(raw_step.get("agent") or raw_step.get("target_agent") or "").lower().strip(),
                    "goal": str(raw_step.get("goal") or "").strip(),
                    "bug_report_id": raw_step.get("bug_report_id"),
                    "jira_key": raw_step.get("jira_key"),
                }
            else:
                continue

            akey = step["agent"]
            if akey not in roster_names:
                continue
            if akey == "coroner" and not allow_coroner:
                blocked_agents.append("coroner")
                continue
            if akey == "judge" and not allow_judge:
                blocked_agents.append("judge")
                continue
            if akey == "sheriff" and not allow_sheriff:
                blocked_agents.append("sheriff")
                continue
            normalized_steps.append(step)

        shared_state: Dict[str, Any] = {"live_probe": bool(live_probe)}
        if any(s["agent"] == "coroner" for s in normalized_steps):
            shared_state["include_coroner"] = True
        if default_bug_id:
            shared_state["bug_id"] = default_bug_id
        if default_force_new:
            shared_state["force_new_ticket"] = True
        if default_jira_project_key:
            shared_state["target_project_key"] = default_jira_project_key
        if attachments:
            shared_state["attachments"] = attachments

        steps_executed: List[Dict[str, Any]] = []
        total = len(normalized_steps)

        for idx, step in enumerate(normalized_steps, start=1):
            akey = step["agent"]
            alabel = roster_names.get(akey, akey.title())
            goal_note = f" — {step['goal']}" if step.get("goal") else ""
            await notify(
                "desk_officer",
                "running",
                f"Organizing team step {idx}/{total}: assigning {alabel}{goal_note}",
            )

            res = await self.dispatch_agent(
                target_agent=akey,
                project_id=project_id,
                bug_input=default_bug_input,
                bug_report_id=step.get("bug_report_id") or shared_state.get("bug_id") or default_bug_id,
                broadcast_callback=broadcast_callback,
                jira_key=step.get("jira_key") or default_jira_key,
                goal=step.get("goal", ""),
                shared_state=shared_state,
                update_jira=update_jira,
                force_new=default_force_new,
                target_project_key=default_jira_project_key,
                attachments=attachments,
                live_probe=live_probe,
            )
            if res.get("bug_id"):
                shared_state["bug_id"] = res["bug_id"]

            steps_executed.append({
                "step": idx,
                "agent": akey,
                "agent_name": res.get("agent") or alabel,
                "goal": step.get("goal", ""),
                "status": res.get("status", "success"),
                "bug_id": res.get("bug_id") or shared_state.get("bug_id"),
                "data": res,
            })

        coroner_invoked = any(s["agent"] == "coroner" and s["status"] == "success" for s in steps_executed)
        judge_invoked = any(s["agent"] == "judge" and s["status"] == "success" for s in steps_executed)
        sheriff_invoked = any(s["agent"] == "sheriff" and s["status"] == "success" for s in steps_executed)
        if not coroner_invoked:
            await notify(
                "coroner",
                "idle",
                "Standing by — Coroner collects visual evidence only when requested.",
                {"bug_id": shared_state.get("bug_id")},
            )
        if not judge_invoked:
            await notify(
                "judge",
                "idle",
                "Standing by — Judge verifies root cause only when requested.",
                {"bug_id": shared_state.get("bug_id")},
            )
        if not sheriff_invoked:
            await notify(
                "sheriff",
                "idle",
                "Standing by — Sheriff will raise to Jira Backlog only when requested.",
                {"bug_id": shared_state.get("bug_id")},
            )

        await notify(
            "desk_officer",
            "success",
            f"Completed organized {len(steps_executed)}-agent execution plan.",
            {"steps": [s["agent"] for s in steps_executed], "bug_id": shared_state.get("bug_id")},
        )

        await self.desk_officer.log_action(
            action="desk_officer_organize_agents",
            input_data={
                "project_id": project_id,
                "requested_steps": [s["agent"] for s in normalized_steps],
                "allow_coroner": allow_coroner,
                "allow_judge": allow_judge,
                "allow_sheriff": allow_sheriff,
            },
            output_data={
                "bug_id": shared_state.get("bug_id"),
                "steps_executed": [s["agent"] for s in steps_executed],
                "blocked_agents": blocked_agents,
                "coroner_invoked": coroner_invoked,
                "judge_invoked": judge_invoked,
                "sheriff_invoked": sheriff_invoked,
            },
            status="success",
            bug_report_id=shared_state.get("bug_id"),
            project_id=project_id,
            started_at=start_time,
        )

        return {
            "status": "success" if steps_executed else "noop",
            "bug_id": shared_state.get("bug_id"),
            "jira_key": default_jira_key,
            "steps_executed": steps_executed,
            "blocked_agents": blocked_agents,
            "coroner_invoked": coroner_invoked,
            "judge_invoked": judge_invoked,
            "sheriff_invoked": sheriff_invoked,
        }

    async def retest_and_sanity_pipeline(
        self,
        bug_report_id: int,
        broadcast_callback: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None,
        update_jira: bool = False,
        capture_failure_evidence: bool = True,
        allow_live_probe: bool = True,
    ) -> Dict[str, Any]:
        """Triggered for an 'In QA' ticket: Watcher engages Coroner, Detective, Judge, and Sanity Testers (always) and alerts without mutating Jira."""
        async with async_session() as session:
            bug = await session.get(BugReport, bug_report_id)
            if not bug:
                return {"retest": {"error": "Bug report not found"}, "judge": None, "evidence": [], "sanity": None}
            project_id = bug.project_id
            ticket_label = bug.jira_ticket_id or f"Bug #{bug_report_id}"

        notify = await self._make_notifier(broadcast_callback, project_id=project_id)

        await notify(
            "watcher",
            "running",
            f"Watcher triggered In-QA verification for {ticket_label} (Coroner -> Detective -> Judge -> Sanity Testers)...",
        )
        retest_outcome = await self.watcher.check_ticket_and_process(
            bug_report_id=bug_report_id,
            jira_settings=self.jira_settings,
            force_status="In QA",
            force=True,
            update_jira=update_jira,
            capture_failure_evidence=capture_failure_evidence,
            allow_live_probe=allow_live_probe,
            broadcast_callback=broadcast_callback,
        )

        is_resolved = bool(retest_outcome.get("is_resolved", False))
        verdict = retest_outcome.get("verdict") or ("PASSED" if is_resolved else "FAILED")
        status_name = retest_outcome.get("new_status", "Ready To Deploy" if is_resolved else "Failed QA")
        sanity_result = retest_outcome.get("sanity")
        judge_result = retest_outcome.get("judge")
        evidence_list = retest_outcome.get("evidence") or []

        if retest_outcome.get("error"):
            await notify("watcher", "failed", f"Watcher check error: {retest_outcome['error']}", retest_outcome)
        else:
            jira_note = (
                "Jira status left unchanged in 'In QA' (alert-only)."
                if not retest_outcome.get("jira_updated")
                else f"Updated Jira status to '{status_name}'."
            )
            await notify(
                "watcher",
                "success" if is_resolved else "failed",
                f"Watcher In-QA Verdict for {retest_outcome.get('jira_key') or ticket_label}: {verdict} ({len(evidence_list)} evidence file(s)). {jira_note}",
                retest_outcome,
            )

        return {
            "retest": retest_outcome,
            "judge": judge_result,
            "evidence": evidence_list,
            "sanity": sanity_result,
        }
