"""Watcher Agent - Monitors Jira 'In QA' tickets with 4-agent verification, tracks Jira updates, and monitors AI agent failures."""
import json
import logging
import re
from datetime import datetime, timedelta
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple
import httpx
from sqlalchemy import select

import config
from agents.base import BaseAgent
from agents.detective import DetectiveAgent
from agents.coroner import CoronerAgent
from agents.judge import JudgeAgent
from agents.sanity_tester import SanityTesterAgent
from routers.websocket import manager
from services.attachment_processor import IMAGE_EXTS, _encode_image_for_vision, detect_mime_type
from services.jira_client import JiraClient, is_real_jira_issue_key
from services.notification_service import NotificationService, _as_bool, format_in_qa_alert_content
from services.watcher_bus import _write_internal_log_no_loop, record_system_event
from database import async_session
from models.agent_log import AgentLog
from models.bug_report import BugReport
from models.project import Project
from models.settings import Settings
from models.skill import Skill

logger = logging.getLogger(__name__)


def _is_in_qa_status(raw_status: Optional[str]) -> bool:
    norm = re.sub(r"[\s_-]+", "", (raw_status or "").strip().lower())
    return norm == "inqa"


def _map_jira_priority_to_severity(priority: Optional[str]) -> str:
    p = (priority or "").strip().lower()
    if p in ("highest", "blocker", "critical"):
        return "highest"
    if p in ("high", "major"):
        return "high"
    if p in ("low", "minor"):
        return "low"
    if p in ("lowest", "trivial"):
        return "lowest"
    return "medium"


def _infer_label_from_jira(labels: Any) -> str:
    if isinstance(labels, list):
        for lbl in labels:
            low = str(lbl or "").lower()
            if "back" in low or "api" in low:
                return "Back-End"
            if "mobile" in low or "ios" in low or "android" in low:
                return "Mobile"
            if "front" in low or "ui" in low:
                return "Front-End"
    return "Front-End"


def _load_in_qa_processed(settings_dict: Optional[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    raw = str((settings_dict or {}).get("watcher_in_qa_processed") or "").strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            return {str(k).upper(): v for k, v in parsed.items() if isinstance(v, dict)}
    except Exception:
        pass
    return {}


def _hydrate_vision_evidence(evidence_items: List[Dict[str, Any]], max_images: int = 3) -> List[Dict[str, Any]]:
    hydrated: List[Dict[str, Any]] = []
    from pathlib import Path
    for ev in evidence_items or []:
        if not isinstance(ev, dict):
            continue
        fp = ev.get("file_path")
        if not fp:
            continue
        p = Path(fp)
        if not p.is_file() or p.suffix.lower() not in IMAGE_EXTS:
            continue
        try:
            raw_bytes = p.read_bytes()
            mime = detect_mime_type(p.name, content=raw_bytes)
            b64 = _encode_image_for_vision(raw_bytes)
            hydrated.append({
                "name": ev.get("file_name") or p.name,
                "mime_type": mime,
                "is_image": True,
                "data_base64": b64,
            })
            if len(hydrated) >= max_images:
                break
        except Exception:
            continue
    return hydrated


def _map_jira_status_to_local(raw_status: str) -> str:
    norm = (raw_status or "").lower().replace(" ", "").replace("_", "")
    if norm in ("inqa",):
        return "in_qa"
    if "deploy" in norm or norm in ("done", "resolved", "closed"):
        return "resolved"
    if "fail" in norm:
        return "failed"
    if norm in ("backlog", "todo", "selectedfordevelopment", "open", "reported"):
        return "reported"
    return "reported"


def _diff_jira_snapshot(
    recent_issues: List[Dict[str, Any]],
    prev_snapshot_raw: str,
) -> Tuple[List[Dict[str, Any]], str]:
    """Compare live Jira issues against the last stored snapshot to detect board updates cleanly."""
    curr_snapshot: Dict[str, Dict[str, Any]] = {}
    for iss in recent_issues or []:
        key = str(iss.get("key") or "").strip().upper()
        if not key:
            continue
        curr_snapshot[key] = {
            "status": iss.get("status") or "",
            "summary": iss.get("summary") or "",
            "priority": iss.get("priority") or "",
            "updated": iss.get("updated") or "",
            "url": iss.get("url") or "",
        }

    prev_snapshot: Optional[Dict[str, Any]] = None
    if (prev_snapshot_raw or "").strip():
        try:
            loaded = json.loads(prev_snapshot_raw)
            if isinstance(loaded, dict):
                prev_snapshot = loaded
        except Exception:
            prev_snapshot = None

    curr_json = json.dumps(curr_snapshot, ensure_ascii=False)
    if prev_snapshot is None:
        # Establish baseline on very first snapshot without flooding existing tickets
        return [], curr_json

    changes: List[Dict[str, Any]] = []
    for key, cur in curr_snapshot.items():
        prev = prev_snapshot.get(key)
        if not prev:
            changes.append({
                "key": key,
                "change_type": "created_or_updated",
                "status": cur.get("status"),
                "summary": cur.get("summary"),
                "url": cur.get("url"),
            })
            continue
        diffs = {}
        for field in ("status", "summary", "priority", "updated"):
            if (cur.get(field) or "") != (prev.get(field) or ""):
                diffs[field] = {"old": prev.get(field), "new": cur.get(field)}
        if diffs:
            changes.append({
                "key": key,
                "change_type": "status_changed" if "status" in diffs else "issue_updated",
                "status": cur.get("status"),
                "summary": cur.get("summary"),
                "url": cur.get("url"),
                "diffs": diffs,
            })
    return changes, curr_json


class WatcherAgent(BaseAgent):
    """Sentinel monitoring Jira 'In QA' queue, Jira updates, and AI agent failures."""

    def __init__(
        self,
        detective: DetectiveAgent,
        coroner: CoronerAgent,
        notification_service: NotificationService,
        judge: Optional[JudgeAgent] = None,
        sanity_tester: Optional[SanityTesterAgent] = None,
    ):
        super().__init__(
            name="Watcher",
            role="In-QA Sentinel & Failure Monitor",
            color="#6366f1",
            icon="👁️",
        )
        self.detective = detective
        self.coroner = coroner
        self.notifier = notification_service
        self.judge = judge or JudgeAgent(detective.llm)
        self.sanity_tester = sanity_tester or SanityTesterAgent(detective.llm)
        self._running = False
        self._task = None

    async def _load_project_skills_text(self, project_id: int, max_chars: int = 4000) -> str:
        async with async_session() as session:
            rows = (
                await session.execute(
                    select(Skill).where(Skill.project_id == project_id).order_by(Skill.id.asc())
                )
            ).scalars().all()
        parts: List[str] = []
        for sk in rows:
            if (sk.type or "").lower() == "report_format":
                continue
            parts.append(f"[{sk.type.upper()}] {sk.title}:\n{sk.content}")
        joined = "\n\n".join(parts)
        return joined[:max_chars] if joined else "No additional skills stored."

    async def _emit_agent_status(
        self,
        broadcast_callback: Optional[Callable[[Dict[str, Any]], Awaitable[None]]],
        project_id: int,
        bug_id: Optional[int],
        agent: str,
        status: str,
        message: str,
        data: Any = None,
    ) -> None:
        if broadcast_callback is None:
            return
        try:
            await broadcast_callback({
                "type": "agent_status",
                "agent": agent,
                "status": status,
                "message": message,
                "data": data,
                "project_id": project_id,
                "bug_id": bug_id,
            })
        except Exception as exc:
            logger.debug(f"Watcher broadcast callback warning ({agent}): {exc}")

    async def _save_in_qa_checkpoint_entry(
        self,
        jira_key: str,
        jira_updated: str,
        verdict: str,
        bug_id: int,
    ) -> None:
        key_up = str(jira_key or "").strip().upper()
        if not key_up:
            return
        async with async_session() as session:
            row = (
                await session.execute(select(Settings).where(Settings.key == "watcher_in_qa_processed"))
            ).scalar_one_or_none()
            current_map = _load_in_qa_processed({"watcher_in_qa_processed": row.value if row else ""})
            current_map[key_up] = {
                "updated": jira_updated or "",
                "verdict": verdict,
                "checked_at": datetime.utcnow().isoformat(),
                "bug_id": bug_id,
            }
            serialized = json.dumps(current_map, ensure_ascii=False)
            if row:
                row.value = serialized
            else:
                session.add(Settings(key="watcher_in_qa_processed", value=serialized))
            await session.commit()

    async def check_ticket_and_process(
        self,
        bug_report_id: int,
        jira_settings: Dict[str, str],
        force_status: Optional[str] = None,
        update_jira: bool = False,
        notify: Optional[bool] = None,
        browsers: Optional[List[str]] = None,
        headless: Optional[bool] = None,
        capture_failure_evidence: bool = True,
        allow_live_probe: bool = True,
        force: bool = False,
        jira_issue_context: Optional[Dict[str, Any]] = None,
        in_qa_processed_map: Optional[Dict[str, Dict[str, Any]]] = None,
        broadcast_callback: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None,
    ) -> Dict[str, Any]:
        """Check ticket status; when 'In QA', engage Coroner + Detective + Judge + Sanity Testers (always) and alert only (no Jira status change)."""
        start_time = datetime.utcnow()
        eff_browsers = browsers or config.get_enabled_browsers(jira_settings or {})
        eff_headless = (
            str((jira_settings or {}).get("browser_headless", "true")).lower() == "true"
            if headless is None
            else bool(headless)
        )

        async with async_session() as session:
            bug = await session.get(BugReport, bug_report_id)
            if not bug:
                return {"error": "Bug report not found"}

            project = await session.get(Project, bug.project_id)
            project_info = project.to_dict() if project else {"id": bug.project_id, "name": "Default"}
            project_name = project_info.get("name")
            previous_local_status = bug.status
            current_status = force_status or bug.status

            # Load latest settings baseline so notifier/checkpoints reflect DB even if sparse jira_settings passed
            db_rows = (await session.execute(select(Settings))).scalars().all()
            db_settings = {r.key: r.value for r in db_rows}
            merged_settings = {**db_settings, **(jira_settings or {})}
            effective_notify = (
                _as_bool(merged_settings.get("watcher_enabled"), True)
                if notify is None
                else bool(notify)
            )
            has_explicit_notif_cfg = any(
                k in (jira_settings or {})
                for k in (
                    "notification_slack_enabled",
                    "notification_slack_webhook",
                    "notification_email_enabled",
                    "notification_email",
                    "smtp_host",
                )
            )
            active_notifier = (
                NotificationService.from_settings(merged_settings)
                if (has_explicit_notif_cfg or not (self.notifier.slack_enabled or self.notifier.email_enabled))
                else self.notifier
            )

            base_url = (merged_settings.get("jira_base_url") or (project.jira_base_url if project else "") or "").strip()
            email = (merged_settings.get("jira_email") or "").strip()
            token = (merged_settings.get("jira_api_token") or "").strip()

            live_jira_configured = bool(base_url and email and token)
            is_simulated_url = (bug.jira_ticket_url or "").startswith("https://jira.local/")
            has_real_jira = bool(
                live_jira_configured
                and bug.jira_ticket_id
                and not is_simulated_url
                and is_real_jira_issue_key(bug.jira_ticket_id)
            )

            jira_poll_error: Optional[str] = None
            jira_status_fetched: Optional[str] = None
            fetched_issue: Dict[str, Any] = dict(jira_issue_context or {})
            if has_real_jira:
                jira_status_fetched = fetched_issue.get("status")
                needs_issue_fetch = (not fetched_issue) or ("description" not in fetched_issue)
                if needs_issue_fetch:
                    try:
                        jira_client = JiraClient(base_url, email, token)
                        full_issue = await jira_client.get_issue(bug.jira_ticket_id)
                        fetched_issue = {**fetched_issue, **{k: v for k, v in full_issue.items() if v is not None}}
                        jira_status_fetched = fetched_issue.get("status") or jira_status_fetched
                    except Exception as e:
                        jira_poll_error = str(e)
                        logger.warning(f"Could not poll Jira issue {bug.jira_ticket_id}: {e}")
                        if force_status is None and not fetched_issue:
                            await self.log_action(
                                action="jira_poll_failed",
                                input_data={"bug_id": bug.id, "ticket": bug.jira_ticket_id},
                                output_data={"error": jira_poll_error},
                                status="failed",
                                bug_report_id=bug.id,
                                project_id=bug.project_id,
                                module=bug.module or "Jira Integration",
                                category="jira_sync",
                                level="error",
                                jira_key=bug.jira_ticket_id,
                                notify=effective_notify,
                                started_at=start_time,
                            )

                if jira_status_fetched and force_status is None:
                    current_status = jira_status_fetched

            is_in_qa = _is_in_qa_status(current_status)

            if not is_in_qa and force_status is None:
                mapped = _map_jira_status_to_local(current_status) if jira_status_fetched else bug.status
                drifted = bool(
                    jira_status_fetched
                    and mapped != previous_local_status
                    and previous_local_status not in ("draft", "confirmed")
                )
                if drifted:
                    bug.status = mapped
                    await session.commit()
                    await record_system_event(
                        action="jira_status_drift_detected",
                        summary=f"Jira {bug.jira_ticket_id} changed status from '{previous_local_status}' to '{jira_status_fetched}'.",
                        module=bug.module or "Jira Integration",
                        category="jira_sync",
                        status="success",
                        level="change",
                        agent_name="Watcher",
                        project_id=bug.project_id,
                        bug_report_id=bug.id,
                        jira_key=bug.jira_ticket_id,
                        output_data={
                            "old_status": previous_local_status,
                            "new_status": jira_status_fetched,
                            "new_jira_status": jira_status_fetched,
                        },
                        notify=effective_notify,
                    )
                return {
                    "status": current_status,
                    "jira_key": bug.jira_ticket_id,
                    "retested": False,
                    "jira_updated": False,
                    "drifted": drifted,
                }

            # Checkpoint guard: skip repeating In-QA check unless Jira 'updated' changed or forced manually
            jira_updated_ts = str(fetched_issue.get("updated") or "")
            checkpoint_map = (
                in_qa_processed_map
                if in_qa_processed_map is not None
                else _load_in_qa_processed(merged_settings)
            )
            if not force and force_status is None and has_real_jira and bug.jira_ticket_id:
                key_up = bug.jira_ticket_id.upper()
                prev_chk = checkpoint_map.get(key_up)
                if prev_chk and (
                    (jira_updated_ts and prev_chk.get("updated") == jira_updated_ts)
                    or (not jira_updated_ts and prev_chk.get("checked_at"))
                ):
                    return {
                        "bug_id": bug.id,
                        "ticket_key": bug.jira_ticket_id,
                        "jira_key": bug.jira_ticket_id,
                        "status": current_status,
                        "retested": False,
                        "skipped_cached_in_qa": True,
                        "jira_updated": False,
                        "drifted": False,
                    }

            # Refresh bug details from fetched Jira issue if local record was sparse
            if fetched_issue.get("description") and not (bug.description or "").strip():
                bug.description = fetched_issue["description"]
            if fetched_issue.get("url") and not (bug.jira_ticket_url or "").strip():
                bug.jira_ticket_url = fetched_issue["url"]
            bug.status = "in_qa"
            await session.commit()

            bug_dict = bug.to_dict()
            bug_dict["jira_key"] = bug.jira_ticket_id
            bug_dict["jira_url"] = fetched_issue.get("url") or bug.jira_ticket_url
            bug_dict["issue_type"] = fetched_issue.get("issue_type") or (bug.module if bug.module in ("Bug", "Story", "Task", "Sub-task", "Epic") else None)
            if fetched_issue.get("description"):
                bug_dict["description"] = fetched_issue["description"]
            if fetched_issue.get("comments"):
                bug_dict["jira_comments"] = fetched_issue["comments"]

        skills_context = await self._load_project_skills_text(bug.project_id)

        # ------------------------------------------------------------------
        # 4-Agent Verification Pipeline: Coroner -> Detective -> Judge -> Sanity Testers (ALWAYS)
        # ------------------------------------------------------------------
        # Step 1: Coroner captures visual proof + live runtime telemetry
        coroner_evidence: List[Dict[str, Any]] = []
        coroner_telemetry: List[Dict[str, Any]] = []
        if capture_failure_evidence:
            await self._emit_agent_status(
                broadcast_callback,
                bug.project_id,
                bug.id,
                "coroner",
                "running",
                f"Coroner capturing visual evidence & runtime telemetry for {bug.jira_ticket_id or f'Bug #{bug.id}'}...",
            )
            try:
                coroner_evidence, coroner_telemetry = await self.coroner.capture_with_telemetry(
                    bug_data=bug_dict,
                    project_info=project_info,
                    stage="retest",
                    bug_report_id=bug.id,
                    project_id=bug.project_id,
                    browsers=eff_browsers,
                    headless=eff_headless,
                )
                await self._emit_agent_status(
                    broadcast_callback,
                    bug.project_id,
                    bug.id,
                    "coroner",
                    "success",
                    f"Coroner captured {len(coroner_evidence)} evidence file(s)",
                    coroner_evidence,
                )
            except Exception as cor_err:
                logger.warning(f"Coroner capture failed during In-QA check for Bug #{bug.id}: {cor_err}")
                await self._emit_agent_status(
                    broadcast_callback,
                    bug.project_id,
                    bug.id,
                    "coroner",
                    "failed",
                    f"Coroner evidence capture failed: {cor_err}",
                )
        else:
            await self._emit_agent_status(
                broadcast_callback,
                bug.project_id,
                bug.id,
                "coroner",
                "idle",
                "Coroner capture skipped (capture_failure_evidence=False).",
            )

        vision_attachments = _hydrate_vision_evidence(coroner_evidence, max_images=3)

        # Step 2: Detective retests / verifies the In-QA ticket using Coroner evidence & telemetry
        await self._emit_agent_status(
            broadcast_callback,
            bug.project_id,
            bug.id,
            "detective",
            "running",
            f"Detective retesting {bug.jira_ticket_id or f'Bug #{bug.id}'} against Coroner evidence & skills...",
        )
        retest_result = await self.detective.retest_bug(
            bug_data=bug_dict,
            project_info=project_info,
            project_id=bug.project_id,
            bug_report_id=bug.id,
            browsers=eff_browsers,
            headless=eff_headless,
            allow_live_probe=allow_live_probe,
            skills_context=skills_context,
            runtime_telemetry=coroner_telemetry,
            evidence_list=coroner_evidence,
            attachments=vision_attachments,
        )
        detective_passed = bool(retest_result.get("is_resolved", False))
        await self._emit_agent_status(
            broadcast_callback,
            bug.project_id,
            bug.id,
            "detective",
            "success" if detective_passed else "failed",
            f"Detective In-QA Retest: {'PASSED' if detective_passed else 'FAILED'} — {(retest_result.get('retest_summary') or retest_result.get('failure_reason') or '')[:120]}",
            retest_result,
        )

        # Step 3: Judge independently audits Detective's verdict + Coroner evidence
        await self._emit_agent_status(
            broadcast_callback,
            bug.project_id,
            bug.id,
            "judge",
            "running",
            f"Judge independently verifying In-QA findings for {bug.jira_ticket_id or f'Bug #{bug.id}'}...",
        )
        judge_verdict = await self.judge.verify_retest_verdict(
            bug_data=bug_dict,
            detective_retest=retest_result,
            skills_context=skills_context,
            project_info=project_info,
            project_id=bug.project_id,
            bug_report_id=bug.id,
            attachments=vision_attachments,
            evidence_list=coroner_evidence,
            runtime_telemetry=coroner_telemetry,
        )
        judge_verdict_label = str(judge_verdict.get("verdict") or "CONFIRMED").strip().upper()
        judge_confirmed = bool(judge_verdict.get("confirmed", True)) and judge_verdict_label != "REJECTED"
        judge_qa_passed = bool(judge_verdict.get("qa_passed", detective_passed)) if judge_confirmed else False
        await self._emit_agent_status(
            broadcast_callback,
            bug.project_id,
            bug.id,
            "judge",
            "success" if judge_confirmed else "failed",
            f"Judge Verdict: {judge_verdict_label} — {str(judge_verdict.get('judge_notes') or '')[:120]}",
            judge_verdict,
        )

        # Step 4: Sanity Testers ALWAYS runs — whether Judge CONFIRMED or REJECTED
        await self._emit_agent_status(
            broadcast_callback,
            bug.project_id,
            bug.id,
            "sanity",
            "running",
            f"Sanity Testers verifying impacted areas around {bug.jira_ticket_id or f'Bug #{bug.id}'}...",
        )
        sanity_result = await self.sanity_tester.run_sanity_checks(
            resolved_bug=bug_dict,
            skills_context=skills_context,
            project_info=project_info,
            project_id=bug.project_id,
            bug_report_id=bug.id,
            browsers=eff_browsers,
            headless=eff_headless,
            runtime_telemetry=coroner_telemetry,
            judge_verdict=judge_verdict,
        )
        sanity_regression = bool(
            sanity_result.get("regression_detected")
            or str(sanity_result.get("overall_status") or "").strip().upper() == "FAILED"
        )
        await self._emit_agent_status(
            broadcast_callback,
            bug.project_id,
            bug.id,
            "sanity",
            "failed" if sanity_regression else "success",
            f"Sanity Testers complete: {sanity_result.get('summary')}",
            sanity_result,
        )

        # Final combined QA verdict
        final_passed = bool(judge_qa_passed and judge_confirmed and not sanity_regression)
        verdict_word = "PASSED" if final_passed else "FAILED"
        target_status = "Ready To Deploy" if final_passed else "Failed QA"

        # Alert-only Jira policy: never change Jira Cloud status unless explicitly requested by legacy override
        jira_updated = False
        if update_jira and has_real_jira:
            try:
                jira_client = JiraClient(base_url, email, token)
                jira_updated = bool(await jira_client.transition_issue(bug.jira_ticket_id, target_status))
            except Exception as e:
                logger.error(f"Jira transition failed in Watcher: {e}")
                await self.log_action(
                    action="jira_transition_failed",
                    input_data={"bug_id": bug.id, "ticket": bug.jira_ticket_id, "target_status": target_status},
                    output_data={"error": str(e)},
                    status="failed",
                    bug_report_id=bug.id,
                    project_id=bug.project_id,
                    module=bug.module or "Jira Integration",
                    category="jira_sync",
                    level="error",
                    jira_key=bug.jira_ticket_id,
                    notify=effective_notify,
                )

        async with async_session() as session:
            db_bug = await session.get(BugReport, bug.id)
            if db_bug:
                db_bug.status = "resolved" if final_passed else "failed"
                if judge_verdict.get("final_root_cause") and not db_bug.root_cause:
                    db_bug.root_cause = judge_verdict["final_root_cause"]
                await session.commit()

        if has_real_jira and bug.jira_ticket_id:
            if in_qa_processed_map is not None:
                in_qa_processed_map[bug.jira_ticket_id.upper()] = {
                    "updated": jira_updated_ts,
                    "verdict": verdict_word,
                    "checked_at": datetime.utcnow().isoformat(),
                    "bug_id": bug.id,
                }
            await self._save_in_qa_checkpoint_entry(
                bug.jira_ticket_id,
                jira_updated_ts,
                verdict_word,
                bug.id,
            )

        jira_display_status = jira_status_fetched or ("In QA" if is_in_qa else current_status)
        alert_title, alert_message, alert_details = format_in_qa_alert_content(
            jira_key=bug.jira_ticket_id if has_real_jira else None,
            jira_url=bug_dict.get("jira_url"),
            bug_title=bug_dict.get("title") or bug.title,
            issue_type=bug_dict.get("issue_type"),
            passed=final_passed,
            detective_result=retest_result,
            judge_result=judge_verdict,
            sanity_result=sanity_result,
            evidence_list=coroner_evidence,
            telemetry_list=coroner_telemetry,
            jira_status=jira_display_status,
            bug_id=bug.id,
        )

        # Dispatch external Slack/Email alert + UI Bell (only for real Jira tickets under strict scope)
        channels_used = ["ws"]
        if effective_notify and has_real_jira:
            notif_res = await active_notifier.notify_in_qa_verification(
                bug_title=bug_dict.get("title") or bug.title,
                jira_key=bug.jira_ticket_id,
                jira_url=bug_dict.get("jira_url"),
                issue_type=bug_dict.get("issue_type"),
                passed=final_passed,
                detective_result=retest_result,
                judge_result=judge_verdict,
                sanity_result=sanity_result,
                evidence_list=coroner_evidence,
                telemetry_list=coroner_telemetry,
                jira_status=jira_display_status,
                project_name=project_name,
                module=bug.module or "QA Verification",
                bug_id=bug.id,
            )
            for ch in notif_res.get("channels_sent") or []:
                if ch not in channels_used:
                    channels_used.append(ch)
            for ch_name in ("slack", "email"):
                ch_res = notif_res.get(ch_name) or {}
                if ch_res.get("attempted") and not ch_res.get("sent"):
                    channels_used.append(f"{ch_name}(failed)")
                    err_txt = ch_res.get("error") or ch_res.get("reason") or f"{ch_name} delivery failed"
                    await _write_internal_log_no_loop(
                        agent_name="Watcher",
                        action=f"{ch_name}_delivery_failed",
                        module="Notifications",
                        category="notification_delivery",
                        level="error",
                        status="failed",
                        project_id=bug.project_id,
                        bug_report_id=bug.id,
                        jira_key=bug.jira_ticket_id,
                        input_data={"source_action": "jira_in_qa_verified", "channel": ch_name},
                        output_data={"error": err_txt},
                        notified_channels="ws",
                    )
            try:
                await manager.broadcast({
                    "type": "notification",
                    "title": alert_title,
                    "message": alert_message.splitlines()[0][:240],
                    "level": "success" if final_passed else "error",
                    "module": bug.module or "QA Verification",
                    "agent": "Watcher",
                    "project_id": bug.project_id,
                    "bug_report_id": bug.id,
                    "jira_key": bug.jira_ticket_id,
                    "timestamp": datetime.utcnow().isoformat(),
                })
            except Exception as ws_err:
                logger.debug(f"Watcher UI bell broadcast warning: {ws_err}")

        channels_tag = ",".join(channels_used)
        any_degraded = (
            bool(retest_result.get("llm_degraded"))
            and not self.detective.llm.is_configured()
        )
        log_action_status = "degraded" if (any_degraded and not has_real_jira) else "success"
        await self.log_action(
            action="jira_in_qa_verified",
            input_data={
                "bug_id": bug.id,
                "ticket": bug.jira_ticket_id,
                "previous_status": current_status,
                "update_jira": update_jira,
                "agents": ["Coroner", "Detective", "Judge", "Sanity Testers"],
            },
            output_data={
                "alert_title": alert_title,
                "alert_message": alert_message,
                "is_resolved": final_passed,
                "verdict": verdict_word,
                "new_status": target_status,
                "jira_updated": jira_updated,
                "jira_status_unchanged": not jira_updated,
                "details": alert_details,
                "detective": retest_result,
                "judge": judge_verdict,
                "sanity": sanity_result,
                "evidence_files": [e.get("file_name") for e in coroner_evidence if e.get("file_name")],
            },
            status=log_action_status,
            bug_report_id=bug.id,
            project_id=bug.project_id,
            module=bug.module or "QA Verification",
            category="jira_sync",
            level="success" if final_passed else "warning",
            jira_key=bug.jira_ticket_id,
            notified_channels=channels_tag,
            notify=False,
            observer_bypass=True,
            started_at=start_time,
        )

        return {
            "bug_id": bug.id,
            "ticket_key": bug.jira_ticket_id,
            "jira_key": bug.jira_ticket_id,
            "is_resolved": final_passed,
            "verdict": verdict_word,
            "new_status": target_status,
            "jira_updated": jira_updated,
            "jira_status_unchanged": not jira_updated,
            "in_qa_verified": True,
            "evidence": coroner_evidence,
            "telemetry": coroner_telemetry,
            "details": retest_result,
            "judge": judge_verdict,
            "sanity": sanity_result,
            "alert_title": alert_title,
            "alert_message": alert_message,
            "notified_channels": channels_tag,
        }

    async def _probe_project_app_health(self, project: Project) -> Optional[Dict[str, Any]]:
        """Lightweight HTTP health probe for project app_url (skips empty or example.com placeholders)."""
        url = (project.app_url or "").strip()
        if not url or not url.startswith(("http://", "https://")):
            return None
        if "example.com" in url.lower() or "localhost:5173" in url.lower() or "localhost:8000" in url.lower():
            return {"project_id": project.id, "project_name": project.name, "url": url, "skipped": True, "ok": True}

        try:
            async with httpx.AsyncClient(timeout=4.0, follow_redirects=True) as client:
                resp = await client.get(url)
                ok = 200 <= resp.status_code < 400
                return {
                    "project_id": project.id,
                    "project_name": project.name,
                    "url": url,
                    "status_code": resp.status_code,
                    "ok": ok,
                }
        except Exception as e:
            return {
                "project_id": project.id,
                "project_name": project.name,
                "url": url,
                "ok": False,
                "error": str(e),
            }

    @staticmethod
    def _resolve_project_for_jira_issue(
        issue: Dict[str, Any],
        projects: List[Project],
        scoped_project_id: Optional[int] = None,
    ) -> Optional[Project]:
        """Match a Jira issue (e.g. SCRUM-850) to its configured local Project row."""
        if not projects:
            return None
        key_up = str(issue.get("key") or "").strip().upper()
        proj_prefix = key_up.split("-", 1)[0] if "-" in key_up else ""

        matched: Optional[Project] = None
        if proj_prefix:
            for p in projects:
                if (p.jira_project_key or "").strip().upper() == proj_prefix:
                    matched = p
                    break
        if matched is None:
            matched = projects[0]

        if scoped_project_id and matched and matched.id != scoped_project_id:
            return None
        return matched

    async def _sync_jira_in_qa_tickets(
        self,
        j_base: str,
        j_email: str,
        j_tok: str,
        all_projects: List[Project],
        scoped_project_id: Optional[int],
        processed_map: Dict[str, Dict[str, Any]],
    ) -> Tuple[
        Dict[int, Dict[str, Any]],
        List[BugReport],
        Optional[List[Dict[str, Any]]],
        Dict[str, Dict[str, Any]],
        bool,
        Optional[str],
    ]:
        """Discover ALL Jira 'In QA' issues (Bugs, Stories, Tasks, etc.), import external ones, and prune exited keys."""
        client = JiraClient(j_base, j_email, j_tok)
        recent_issues: Optional[List[Dict[str, Any]]] = None
        in_qa_raw: List[Dict[str, Any]] = []
        recent_err: Optional[str] = None
        in_qa_err: Optional[str] = None
        recent_ok = False
        in_qa_ok = False

        try:
            recent_issues = await client.search_jql("updated >= -14d ORDER BY updated DESC", max_results=30)
            recent_ok = True
        except Exception as exc:
            recent_err = str(exc)

        try:
            in_qa_raw = await client.search_jql('status = "In QA" ORDER BY updated DESC', max_results=50)
            in_qa_ok = True
        except Exception as exc:
            in_qa_err = str(exc)

        if not recent_ok and not in_qa_ok:
            return {}, [], None, processed_map, False, recent_err or in_qa_err or "Jira search failed"

        combined_by_key: Dict[str, Dict[str, Any]] = {}
        for iss in list(in_qa_raw or []) + list(recent_issues or []):
            k_up = str(iss.get("key") or "").strip().upper()
            if not k_up:
                continue
            if k_up not in combined_by_key:
                combined_by_key[k_up] = dict(iss)
            else:
                merged = {**iss, **{fk: fv for fk, fv in combined_by_key[k_up].items() if fv}}
                combined_by_key[k_up] = merged

        in_qa_candidates = [
            iss for iss in combined_by_key.values() if _is_in_qa_status(iss.get("status"))
        ]
        live_in_qa_keys = {str(iss.get("key") or "").strip().upper() for iss in in_qa_candidates}

        checkpoint_modified = False
        if not scoped_project_id and in_qa_ok:
            for k in list(processed_map.keys()):
                if k not in live_in_qa_keys:
                    processed_map.pop(k, None)
                    checkpoint_modified = True
        else:
            for k_up, iss in combined_by_key.items():
                if k_up in processed_map and not _is_in_qa_status(iss.get("status")):
                    processed_map.pop(k_up, None)
                    checkpoint_modified = True

        live_in_qa_context: Dict[int, Dict[str, Any]] = {}
        imported_bugs: List[BugReport] = []

        if in_qa_candidates:
            async with async_session() as session:
                for iss in in_qa_candidates:
                    key_up = str(iss.get("key") or "").strip().upper()
                    if not key_up:
                        continue
                    target_proj = self._resolve_project_for_jira_issue(
                        iss,
                        all_projects,
                        scoped_project_id=scoped_project_id,
                    )
                    if not target_proj:
                        continue

                    existing = (
                        await session.execute(
                            select(BugReport)
                            .where(BugReport.jira_ticket_id == key_up)
                            .order_by(BugReport.id.desc())
                        )
                    ).scalars().first()

                    enriched = dict(iss)
                    if existing is None or "description" not in enriched:
                        try:
                            full = await client.get_issue(key_up)
                            enriched = {**enriched, **{fk: fv for fk, fv in full.items() if fv is not None}}
                        except Exception:
                            pass

                    if existing is None:
                        issue_type = (enriched.get("issue_type") or "Jira Ticket").strip()
                        new_bug = BugReport(
                            project_id=target_proj.id,
                            title=(enriched.get("summary") or f"Jira {key_up}")[:250],
                            description=(
                                enriched.get("description")
                                or enriched.get("summary")
                                or f"Imported from Jira 'In QA' queue ({key_up})."
                            )[:4000],
                            severity=_map_jira_priority_to_severity(enriched.get("priority")),
                            label=_infer_label_from_jira(enriched.get("labels")),
                            module=issue_type[:120] or "Jira In QA",
                            jira_ticket_id=key_up,
                            jira_ticket_url=(
                                enriched.get("url") or f"{j_base.rstrip('/')}/browse/{key_up}"
                            )[:500],
                            status="in_qa",
                        )
                        session.add(new_bug)
                        await session.flush()
                        await session.refresh(new_bug)
                        existing = new_bug
                        imported_bugs.append(new_bug)

                    live_in_qa_context[existing.id] = enriched

                if imported_bugs:
                    await session.commit()

            for imp_bug in imported_bugs:
                await record_system_event(
                    action="jira_in_qa_ticket_imported",
                    summary=f"Auto-imported Jira 'In QA' {imp_bug.module or 'ticket'} {imp_bug.jira_ticket_id}: {imp_bug.title[:100]}",
                    module=imp_bug.module or "Jira Integration",
                    category="jira_sync",
                    status="success",
                    level="info",
                    agent_name="Watcher",
                    project_id=imp_bug.project_id,
                    bug_report_id=imp_bug.id,
                    jira_key=imp_bug.jira_ticket_id,
                    output_data={
                        "bug_id": imp_bug.id,
                        "jira_key": imp_bug.jira_ticket_id,
                        "issue_type": imp_bug.module,
                    },
                    notify=False,
                    notify_ui_bell=False,
                )

        return (
            live_in_qa_context,
            imported_bugs,
            recent_issues if recent_ok else list(in_qa_raw or []),
            processed_map,
            checkpoint_modified,
            None,
        )

    async def run_full_system_sweep(
        self,
        jira_settings: Dict[str, str],
        project_id: Optional[int] = None,
        include_app_health: bool = True,
        notify: Optional[bool] = None,
    ) -> Dict[str, Any]:
        """Audit projects, modules, AI agents, Jira 'In QA' queue, and logs; dispatch alerts strictly for Jira updates & AI agent failures."""
        start_time = datetime.utcnow()
        s = jira_settings or {}
        monitor_modules = _as_bool(s.get("watcher_monitor_modules"), True)
        monitor_agents = _as_bool(s.get("watcher_monitor_agents"), True)
        monitor_logs = _as_bool(s.get("watcher_monitor_logs"), True)

        async with async_session() as session:
            settings_rows = (await session.execute(select(Settings))).scalars().all()
            db_settings = {row.key: row.value for row in settings_rows}
            effective_settings = {**db_settings, **s}

            # Refresh notifier from effective settings so sweep uses latest Slack/Email/SMTP config
            self.notifier = NotificationService.from_settings(effective_settings)
            effective_notify = (
                _as_bool(effective_settings.get("watcher_enabled"), True)
                if notify is None
                else (bool(notify) and _as_bool(effective_settings.get("watcher_enabled"), True))
            )

            last_sweep_raw = effective_settings.get("watcher_last_sweep_at") or ""
            since_dt = datetime.utcnow() - timedelta(hours=24)
            if last_sweep_raw:
                try:
                    since_dt = datetime.fromisoformat(last_sweep_raw)
                except Exception:
                    pass

            all_projects_for_routing = (await session.execute(select(Project).order_by(Project.id.asc()))).scalars().all()
            projects = (
                [p for p in all_projects_for_routing if p.id == project_id]
                if project_id
                else list(all_projects_for_routing)
            )

            bug_stmt = select(BugReport).order_by(BugReport.id.desc())
            if project_id:
                bug_stmt = bug_stmt.where(BugReport.project_id == project_id)
            all_bugs = list((await session.execute(bug_stmt)).scalars().all())

            skill_stmt = select(Skill)
            if project_id:
                skill_stmt = skill_stmt.where(Skill.project_id == project_id)
            all_skills = (await session.execute(skill_stmt)).scalars().all()

            log_stmt = select(AgentLog).where(AgentLog.started_at >= since_dt).order_by(AgentLog.started_at.desc())
            if project_id:
                log_stmt = log_stmt.where((AgentLog.project_id == project_id) | (AgentLog.project_id.is_(None)))
            recent_logs = (await session.execute(log_stmt)).scalars().all()

        # 1. Discover ALL Jira 'In QA' tickets (Bugs, Stories, Tasks, etc.) + check tracked bugs
        retested_tickets: List[Dict[str, Any]] = []
        in_qa_checked: List[Dict[str, Any]] = []
        jira_changes: List[Dict[str, Any]] = []
        drift_emitted_keys = set()

        live_in_qa_context: Dict[int, Dict[str, Any]] = {}
        imported_bugs: List[BugReport] = []
        synced_recent_issues: Optional[List[Dict[str, Any]]] = None
        processed_map = _load_in_qa_processed(effective_settings)
        checkpoint_modified = False
        jira_poll_err: Optional[str] = None

        j_base = (effective_settings.get("jira_base_url") or "").strip()
        j_email = (effective_settings.get("jira_email") or "").strip()
        j_tok = (effective_settings.get("jira_api_token") or "").strip()
        if j_base and j_email and j_tok:
            (
                live_in_qa_context,
                imported_bugs,
                synced_recent_issues,
                processed_map,
                checkpoint_modified,
                jira_poll_err,
            ) = await self._sync_jira_in_qa_tickets(
                j_base=j_base,
                j_email=j_email,
                j_tok=j_tok,
                all_projects=all_projects_for_routing,
                scoped_project_id=project_id,
                processed_map=processed_map,
            )
            if imported_bugs:
                existing_ids = {b.id for b in all_bugs}
                for imp_b in imported_bugs:
                    if imp_b.id not in existing_ids:
                        all_bugs.insert(0, imp_b)
                        existing_ids.add(imp_b.id)

        for bug in all_bugs:
            is_live_in_qa = bug.id in live_in_qa_context
            should_poll = is_live_in_qa or bug.status in ("reported", "in_qa", "failed", "confirmed")
            if not should_poll:
                continue
            try:
                res = await self.check_ticket_and_process(
                    bug.id,
                    effective_settings,
                    update_jira=False,
                    notify=effective_notify,
                    capture_failure_evidence=True,
                    allow_live_probe=True,
                    jira_issue_context=live_in_qa_context.get(bug.id),
                    in_qa_processed_map=processed_map,
                )
                if res.get("skipped_cached_in_qa"):
                    if bug.jira_ticket_id:
                        drift_emitted_keys.add(str(bug.jira_ticket_id).upper())
                elif res.get("in_qa_verified") or res.get("new_status") or res.get("is_resolved") is not None:
                    checkpoint_modified = True
                    retested_tickets.append({"bug_id": bug.id, "jira_key": bug.jira_ticket_id, **res})
                    in_qa_checked.append({
                        "bug_id": bug.id,
                        "jira_key": bug.jira_ticket_id,
                        "verdict": res.get("verdict") or ("PASSED" if res.get("is_resolved") else "FAILED"),
                        "evidence_count": len(res.get("evidence") or []),
                        "jira_status_unchanged": res.get("jira_status_unchanged", True),
                    })
                    if bug.jira_ticket_id:
                        drift_emitted_keys.add(str(bug.jira_ticket_id).upper())
                elif res.get("drifted"):
                    jira_changes.append({"bug_id": bug.id, "jira_key": bug.jira_ticket_id, "status": res.get("status")})
                    if bug.jira_ticket_id:
                        drift_emitted_keys.add(str(bug.jira_ticket_id).upper())
            except Exception as e:
                logger.error(f"Watcher sweep error on bug #{bug.id}: {e}")
                await record_system_event(
                    action="watcher_bug_check_failed",
                    summary=f"Watcher failed checking Bug #{bug.id}: {e}",
                    module=bug.module or "Watcher Monitor",
                    category="error",
                    status="failed",
                    level="error",
                    agent_name="Watcher",
                    project_id=bug.project_id,
                    bug_report_id=bug.id,
                    jira_key=bug.jira_ticket_id,
                    output_data={"error": str(e)},
                    notify=effective_notify,
                    notify_ui_bell=effective_notify,
                )

        # 2. Target app health checks across projects (audit log only; never triggers Jira/AI-failure alert)
        app_health_checks: List[Dict[str, Any]] = []
        unhealthy_apps: List[Dict[str, Any]] = []
        if include_app_health and monitor_modules:
            for proj in projects:
                probe = await self._probe_project_app_health(proj)
                if probe:
                    app_health_checks.append(probe)
                    if not probe.get("ok"):
                        unhealthy_apps.append(probe)
                        await record_system_event(
                            action="module_app_health_failed",
                            summary=f"Target URL unhealthy for {proj.name} ({probe.get('url')}): {probe.get('error') or probe.get('status_code')}",
                            module="Target App Health",
                            category="module_health",
                            status="failed",
                            level="error",
                            agent_name="System",
                            project_id=proj.id,
                            output_data=probe,
                            notify=False,
                            notify_ui_bell=False,
                        )

        # 3. Module telemetry audit
        modules_audit: Dict[str, Dict[str, Any]] = {}
        if monitor_modules:
            for bug in all_bugs:
                mod = (bug.module or "General").strip()
                bucket = modules_audit.setdefault(mod, {"bugs": 0, "resolved": 0, "failed": 0, "open": 0, "logs": 0, "log_failures": 0})
                bucket["bugs"] += 1
                if bug.status == "resolved":
                    bucket["resolved"] += 1
                elif bug.status == "failed":
                    bucket["failed"] += 1
                else:
                    bucket["open"] += 1
            for sk in all_skills:
                mod = (sk.title or "Skills").split("-")[0].strip()[:40] or "Skills"
                modules_audit.setdefault(mod, {"bugs": 0, "resolved": 0, "failed": 0, "open": 0, "logs": 0, "log_failures": 0})
            for lg in recent_logs:
                mod = (lg.module or "System").strip()
                bucket = modules_audit.setdefault(mod, {"bugs": 0, "resolved": 0, "failed": 0, "open": 0, "logs": 0, "log_failures": 0})
                bucket["logs"] += 1
                if lg.status == "failed":
                    bucket["log_failures"] += 1

        # 4. Every AI agent & log stream audit
        agents_audit: Dict[str, Dict[str, Any]] = {}
        failures_detected: List[Dict[str, Any]] = []
        stale_running: List[Dict[str, Any]] = []
        changes_detected: List[Dict[str, Any]] = list(jira_changes)
        success_count = 0
        degraded_count = 0
        now_utc = datetime.utcnow()

        if monitor_agents or monitor_logs:
            for lg in recent_logs:
                ag = lg.agent_name or "System"
                st = lg.status or "unknown"
                ab = agents_audit.setdefault(
                    ag,
                    {"total": 0, "success": 0, "failed": 0, "degraded": 0, "running": 0, "latest_action": lg.action, "latest_status": st},
                )
                ab["total"] += 1
                if st == "success":
                    ab["success"] += 1
                    success_count += 1
                elif st == "degraded":
                    ab["degraded"] += 1
                    degraded_count += 1
                elif st == "failed":
                    ab["failed"] += 1
                    if (lg.category or "") not in ("regression_finding", "notification_delivery") and lg.action != "jira_in_qa_verified":
                        failures_detected.append({
                            "log_id": lg.id,
                            "agent": ag,
                            "action": lg.action,
                            "module": lg.module or "System",
                            "bug_id": lg.bug_report_id,
                        })
                elif st == "running":
                    ab["running"] += 1
                    if lg.started_at and (now_utc - lg.started_at) > timedelta(minutes=15):
                        stale_running.append({"log_id": lg.id, "agent": ag, "action": lg.action})

                if (lg.category in ("system_change", "status_change", "jira_sync") or lg.level == "change") and len(changes_detected) < 25:
                    changes_detected.append({
                        "log_id": lg.id,
                        "agent": ag,
                        "action": lg.action,
                        "module": lg.module or "System",
                    })

        # 5. Live Jira board pulse & snapshot diffing (detects ALL Jira updates across board)
        jira_pulse: Dict[str, Any] = {"configured": False, "updated_issues": []}
        new_snapshot_json: Optional[str] = None
        board_jira_updates: List[Dict[str, Any]] = []

        if j_base and j_email and j_tok:
            jira_pulse["configured"] = True
            if jira_poll_err:
                jira_pulse["error"] = jira_poll_err
                await record_system_event(
                    action="jira_poll_failed",
                    summary=f"Watcher failed polling Jira Cloud board: {jira_poll_err}",
                    module="Jira Integration",
                    category="jira_sync",
                    status="failed",
                    level="error",
                    agent_name="Watcher",
                    project_id=project_id,
                    output_data={"error": jira_poll_err},
                    notify=effective_notify,
                    notify_ui_bell=effective_notify,
                )
            else:
                try:
                    recent_issues = synced_recent_issues
                    if recent_issues is None:
                        client = JiraClient(j_base, j_email, j_tok)
                        recent_issues = await client.search_jql("updated >= -14d ORDER BY updated DESC", max_results=30)
                    in_qa_jira = sorted(
                        {
                            i["key"]
                            for i in (recent_issues or [])
                            if i.get("key") and _is_in_qa_status(i.get("status"))
                        }
                        | {
                            ctx.get("key")
                            for ctx in live_in_qa_context.values()
                            if ctx.get("key")
                        }
                    )
                    jira_pulse["recent_count"] = len(recent_issues or [])
                    jira_pulse["in_qa_keys"] = in_qa_jira
                    jira_pulse["imported_in_qa_keys"] = [
                        b.jira_ticket_id for b in imported_bugs if b.jira_ticket_id
                    ]

                    diff_list, new_snapshot_json = _diff_jira_snapshot(
                        recent_issues or [],
                        effective_settings.get("watcher_jira_issue_snapshot") or "",
                    )
                    client = JiraClient(j_base, j_email, j_tok)
                    for diff_item in diff_list[:5]:
                        key = diff_item["key"]
                        if key in drift_emitted_keys:
                            continue
                        board_jira_updates.append(diff_item)
                        jira_changes.append({"jira_key": key, "status": diff_item.get("status"), "change_type": diff_item.get("change_type")})

                        st_diff = (diff_item.get("diffs") or {}).get("status")
                        cur_status = diff_item.get("status") or "Unknown"
                        if isinstance(st_diff, dict):
                            old_status = st_diff.get("old") or "Unknown"
                            new_status = st_diff.get("new") or cur_status
                        elif diff_item.get("change_type") == "created_or_updated":
                            hist_pair = await client.get_latest_status_transition(key)
                            if hist_pair and (hist_pair[0] or hist_pair[1]):
                                old_status = hist_pair[0] or "None (New Issue)"
                                new_status = hist_pair[1] or cur_status
                            else:
                                old_status = "None (New Issue)"
                                new_status = cur_status
                        else:
                            old_status = cur_status
                            new_status = cur_status

                        diff_item["old_status"] = old_status
                        diff_item["new_status"] = new_status

                        if old_status != new_status and old_status != "None (New Issue)":
                            msg_summary = f"{key} moved from '{old_status}' to '{new_status}' — {diff_item.get('summary') or ''}".rstrip(" —")
                        elif diff_item.get("change_type") == "created_or_updated":
                            msg_summary = f"Jira update detected on {key} ({new_status}): {diff_item.get('summary')}"
                        else:
                            msg_summary = f"Jira issue {key} was updated ({new_status}): {diff_item.get('summary')}"
                        await record_system_event(
                            action="jira_issue_updated",
                            summary=msg_summary,
                            module="Jira Integration",
                            category="jira_sync",
                            status="success",
                            level="change",
                            agent_name="Watcher",
                            project_id=project_id,
                            jira_key=key,
                            output_data={"old_status": old_status, "new_status": new_status},
                            notify=effective_notify,
                            notify_ui_bell=effective_notify,
                        )
                    jira_pulse["updated_issues"] = board_jira_updates
                except Exception as j_err:
                    jira_pulse["error"] = str(j_err)
                    await record_system_event(
                        action="jira_poll_failed",
                        summary=f"Watcher failed polling Jira Cloud board: {j_err}",
                        module="Jira Integration",
                        category="jira_sync",
                        status="failed",
                        level="error",
                        agent_name="Watcher",
                        project_id=project_id,
                        output_data={"error": str(j_err)},
                        notify=effective_notify,
                        notify_ui_bell=effective_notify,
                    )

        # Save sweep timestamp checkpoint, In-QA checkpoint map & Jira board snapshot
        async with async_session() as session:
            now_iso = now_utc.isoformat()
            updates_to_save = {"watcher_last_sweep_at": now_iso}
            if checkpoint_modified:
                updates_to_save["watcher_in_qa_processed"] = json.dumps(processed_map, ensure_ascii=False)
            if new_snapshot_json is not None and not project_id:
                updates_to_save["watcher_jira_issue_snapshot"] = new_snapshot_json
            elif new_snapshot_json is not None and not (effective_settings.get("watcher_jira_issue_snapshot") or "").strip():
                updates_to_save["watcher_jira_issue_snapshot"] = new_snapshot_json

            for k, val in updates_to_save.items():
                row = (
                    await session.execute(select(Settings).where(Settings.key == k))
                ).scalar_one_or_none()
                if row:
                    row.value = val
                else:
                    session.add(Settings(key=k, value=val))
            await session.commit()

        overall_status = "failed" if (failures_detected or unhealthy_apps) else "success"
        overall_level = "error" if (failures_detected or unhealthy_apps) else ("change" if (retested_tickets or jira_changes or changes_detected) else "success")

        sweep_summary = {
            "scanned_at": now_utc.isoformat(),
            "since": since_dt.isoformat(),
            "projects_count": len(projects),
            "bugs_checked": len(all_bugs),
            "retested_tickets": retested_tickets,
            "in_qa_checked": in_qa_checked,
            "jira_changes": jira_changes,
            "modules_monitored": len(modules_audit),
            "modules_audit": modules_audit,
            "agents_monitored": len(agents_audit),
            "agents_audit": agents_audit,
            "logs_audited": len(recent_logs),
            "success_count": success_count,
            "degraded_count": degraded_count,
            "failures_count": len(failures_detected) + len(unhealthy_apps),
            "changes_count": len(changes_detected),
            "failures_detected": failures_detected[:15],
            "unhealthy_apps": unhealthy_apps,
            "stale_running": stale_running,
            "app_health_checks": app_health_checks,
            "jira_pulse": jira_pulse,
        }

        summary_text = (
            f"Watcher audited {len(projects)} project(s), {len(modules_audit)} module(s), "
            f"{len(agents_audit)} active agent(s), and {len(recent_logs)} log event(s): "
            f"{success_count} success, {len(jira_changes)} Jira change(s), "
            f"{len(failures_detected) + len(unhealthy_apps)} failure(s), "
            f"{len(retested_tickets)} In-QA check(s) (Coroner+Detective+Judge+Sanity, alert-only)."
        )

        # Sweep summary logs silently to DB/WS; external alerts fire only on real Jira updates or AI agent failures
        channels_tag = "ws"
        await self.log_action(
            action="system_wide_sweep",
            input_data={
                "project_id": project_id,
                "include_app_health": include_app_health,
                "since": since_dt.isoformat(),
            },
            output_data=sweep_summary,
            status=overall_status,
            project_id=project_id,
            module="All Modules",
            category="watcher_sweep",
            level=overall_level,
            notified_channels=channels_tag,
            notify=False,
            observer_bypass=True,
            started_at=start_time,
        )

        sweep_summary["notified_channels"] = channels_tag
        sweep_summary["summary"] = summary_text
        return sweep_summary

    async def poll_all_active_projects(self, jira_settings: Dict[str, str]) -> Dict[str, Any]:
        """Run full system-wide Watcher sweep across all projects, modules, agents, bugs, and logs."""
        enabled = _as_bool((jira_settings or {}).get("watcher_enabled"), True)
        return await self.run_full_system_sweep(jira_settings=jira_settings, notify=enabled)

    async def run(self, *args, **kwargs) -> Any:
        if "bug_report_id" in kwargs or (args and isinstance(args[0], int)):
            return await self.check_ticket_and_process(*args, **kwargs)
        return await self.run_full_system_sweep(*args, **kwargs)
