"""Watcher Bus - Central system-wide observability, audit enrichment, WebSocket broadcast, and Slack/Email alert hub."""
import contextvars
import json
import logging
import re
from datetime import datetime
from typing import Any, Dict, Optional

from sqlalchemy import select

from database import async_session
from models.agent_log import AgentLog
from models.bug_report import BugReport
from models.project import Project
from models.settings import Settings
from routers.websocket import manager
from services.notification_service import NotificationService, _as_bool, is_watcher_reportable_event

logger = logging.getLogger(__name__)

# Prevents recursive observation loops when Watcher itself logs notification delivery or internal diagnostics
_OBSERVER_DEPTH: contextvars.ContextVar[int] = contextvars.ContextVar("watcher_observer_depth", default=0)

_SECRET_KEYS = {
    "llm_api_key",
    "jira_api_token",
    "smtp_password",
    "notification_slack_webhook",
    "api_key",
    "token",
    "password",
    "secret",
}


def sanitize_for_log(data: Any, max_len: int = 4000) -> Any:
    """Redact secret keys/values recursively before storing in logs or sending notifications."""
    if data is None:
        return None
    if isinstance(data, dict):
        cleaned = {}
        for k, v in data.items():
            kl = str(k).lower()
            if any(sk in kl for sk in _SECRET_KEYS):
                cleaned[k] = "<redacted>" if v else ""
            else:
                cleaned[k] = sanitize_for_log(v, max_len=max_len)
        return cleaned
    if isinstance(data, list):
        return [sanitize_for_log(x, max_len=max_len) for x in data[:30]]
    text = str(data)
    text = re.sub(r"https://hooks\.slack\.com/services/[^\s\"']+", "https://hooks.slack.com/services/<redacted>", text)
    if len(text) > max_len:
        return text[:max_len] + "... [truncated]"
    return text


def _serialize_payload(data: Any) -> Optional[str]:
    if data is None:
        return None
    clean = sanitize_for_log(data)
    if isinstance(clean, (dict, list)):
        return json.dumps(clean, ensure_ascii=False, default=str)
    return str(clean)


def infer_level(status: str = "success", category: str = "agent_action", explicit_level: Optional[str] = None) -> str:
    if explicit_level:
        return explicit_level
    st = (status or "").lower()
    cat = (category or "").lower()
    if st == "degraded":
        return "warning"
    if st == "failed":
        return "error"
    if cat in ("system_change", "status_change", "jira_sync"):
        return "change"
    if st == "success":
        return "success"
    if st == "running":
        return "info"
    return "info"


def infer_module(
    agent_name: str,
    action: str,
    explicit_module: Optional[str] = None,
    input_data: Any = None,
    output_data: Any = None,
    fallback_bug_module: Optional[str] = None,
) -> str:
    if explicit_module:
        return str(explicit_module)[:120]

    for blob in (output_data, input_data):
        if isinstance(blob, dict):
            for key in ("module", "target_module"):
                if blob.get(key):
                    return str(blob[key])[:120]
            aff = blob.get("affected_modules")
            if isinstance(aff, list) and aff:
                return str(aff[0])[:120]

    if fallback_bug_module:
        return str(fallback_bug_module)[:120]

    act = (action or "").lower()
    ag = (agent_name or "").lower()
    if "jira" in act or ag == "sheriff":
        return "Jira Integration"
    if "evidence" in act or ag == "coroner":
        return "Evidence & Browser"
    if "sanity" in act or "sanity" in ag:
        return "Sanity & Regression"
    if "judge" in act or ag == "judge":
        return "Root Cause Verification"
    if "report" in act:
        return "Bug Reporting"
    if "discover" in act or "retest" in act or "trace" in act or ag == "detective":
        return "Defect Investigation"
    if "desk_officer" in act or "chat" in act or ag == "desk officer":
        return "Desk Officer Chat"
    if "project" in act:
        return "Projects"
    if "skill" in act:
        return "Skills"
    if "setting" in act:
        return "Settings"
    if "upload" in act:
        return "Chat Uploads"
    if ag == "watcher":
        return "Watcher Monitor"
    return "System"


async def _load_settings_and_names(
    project_id: Optional[int],
    bug_report_id: Optional[int],
) -> tuple[Dict[str, str], Optional[str], Optional[str], Optional[str]]:
    """Fetch settings, project name, bug module, and bug jira_key efficiently in one session."""
    async with async_session() as session:
        res = await session.execute(select(Settings))
        settings_dict = {s.key: s.value for s in res.scalars().all()}

        project_name: Optional[str] = None
        bug_module: Optional[str] = None
        bug_jira_key: Optional[str] = None

        if project_id:
            proj = await session.get(Project, project_id)
            if proj:
                project_name = proj.name

        if bug_report_id:
            bug = await session.get(BugReport, bug_report_id)
            if bug:
                bug_module = bug.module
                bug_jira_key = bug.jira_ticket_id
                if not project_name and bug.project_id:
                    proj = await session.get(Project, bug.project_id)
                    if proj:
                        project_name = proj.name

    return settings_dict, project_name, bug_module, bug_jira_key


def _format_human_alert(
    agent_name: str,
    action: str,
    status: str,
    module: str,
    summary_hint: Optional[str],
    output_data: Any,
    input_data: Any,
    bug_report_id: Optional[int],
    jira_key: Optional[str],
) -> tuple[str, str]:
    """Build clean human-readable title and message for Slack / Email / UI toast."""
    pretty_action = action.replace("_", " ").strip().title()
    ref_parts = []
    if bug_report_id:
        ref_parts.append(f"Bug #{bug_report_id}")
    if jira_key:
        ref_parts.append(jira_key)
    ref_suffix = f" ({' · '.join(ref_parts)})" if ref_parts else ""

    status_tag = "FAILED" if status == "failed" else ("Update" if status != "success" else "Completed")
    if action == "create_jira_ticket" and status == "success" and jira_key:
        title = f"[{module}] Sheriff Created Jira Ticket: {jira_key}"
    elif status == "failed":
        title = f"[{module}] {agent_name} FAILED: {pretty_action}{ref_suffix}"
    else:
        title = f"[{module}] {agent_name}: {pretty_action}{ref_suffix}"

    if summary_hint:
        message = summary_hint
    elif action == "create_jira_ticket" and status == "success" and jira_key and isinstance(output_data, dict):
        j_status = output_data.get("status", "Backlog")
        j_url = output_data.get("url") or ""
        att_cnt = output_data.get("attached_count", output_data.get("evidence_count", 0))
        message = (
            f"Sheriff created Jira ticket {jira_key} in {j_status}"
            + (f" ({att_cnt} Coroner evidence file(s) attached)" if att_cnt else "")
            + (f".\n{j_url}" if j_url else ".")
        )
    else:
        detail_snippet = ""
        if isinstance(output_data, dict):
            for key in ("error", "message", "summary", "trace_summary", "final_root_cause", "judge_notes", "title"):
                val = output_data.get(key)
                if val:
                    detail_snippet = str(val)[:320]
                    break
            if not detail_snippet:
                detail_snippet = json.dumps(sanitize_for_log(output_data), ensure_ascii=False, default=str)[:320]
        elif output_data is not None:
            detail_snippet = str(output_data)[:320]
        elif isinstance(input_data, dict):
            detail_snippet = json.dumps(sanitize_for_log(input_data), ensure_ascii=False, default=str)[:320]
        elif input_data is not None:
            detail_snippet = str(input_data)[:320]

        message = f"{agent_name} {status_tag.lower()} `{action}` in module **{module}**."
        if detail_snippet:
            message += f"\n\n{detail_snippet}"

    return title[:140], message[:1800]


async def _dispatch_notifications_and_ws(
    log_dict: Dict[str, Any],
    settings_dict: Dict[str, str],
    project_name: Optional[str],
    summary_hint: Optional[str],
    raw_output_data: Any,
    raw_input_data: Any,
    notify_external: bool,
    notify_ui_bell: bool,
) -> Optional[str]:
    """Broadcast live WebSocket telemetry and send Slack/Email alerts without recursion."""
    channels_used = []
    log_id = log_dict.get("id")
    agent_name = log_dict.get("agent_name") or "System"
    action = log_dict.get("action") or "event"
    status = log_dict.get("status") or "info"
    module = log_dict.get("module") or "System"
    category = log_dict.get("category") or "agent_action"
    level = log_dict.get("level") or infer_level(status, category)
    bug_id = log_dict.get("bug_report_id")
    jira_key = log_dict.get("jira_key")

    title, message = _format_human_alert(
        agent_name=agent_name,
        action=action,
        status=status,
        module=module,
        summary_hint=summary_hint,
        output_data=raw_output_data,
        input_data=raw_input_data,
        bug_report_id=bug_id,
        jira_key=jira_key,
    )

    # 1. Always push live watcher_event to WebSocket so LogsPage & store stay synced
    try:
        await manager.broadcast({
            "type": "watcher_event",
            "log": log_dict,
            "project_name": project_name,
            "title": title,
            "message": message,
            "level": level,
            "timestamp": datetime.utcnow().isoformat(),
        })
        channels_used.append("ws")
    except Exception as ws_err:
        logger.debug(f"Watcher WS broadcast warning: {ws_err}")

    if notify_ui_bell:
        try:
            await manager.broadcast({
                "type": "notification",
                "title": title,
                "message": summary_hint or message.splitlines()[0][:180],
                "level": "warning" if level == "change" else level,
                "module": module,
                "agent": agent_name,
                "project_id": log_dict.get("project_id"),
                "bug_report_id": bug_id,
                "jira_key": jira_key,
                "timestamp": datetime.utcnow().isoformat(),
            })
        except Exception as ws_err:
            logger.debug(f"Watcher WS notification warning: {ws_err}")

    # 2. Dispatch Slack / Email via NotificationService if enabled and allowed (strictly Jira updates & AI agent failures)
    if notify_external:
        notifier = NotificationService.from_settings(settings_dict)
        if notifier.should_notify(
            category=category,
            status=status,
            level=level,
            agent_name=agent_name,
            action=action,
            jira_key=jira_key,
            module=module,
        ):
            clean_details = sanitize_for_log(raw_output_data if raw_output_data is not None else raw_input_data)
            res = await notifier.notify_event(
                title=title,
                message=message,
                level=level,
                category=category,
                status=status,
                project_name=project_name,
                module=module,
                agent_name=agent_name,
                action=action,
                bug_id=bug_id,
                jira_key=jira_key,
                details=clean_details,
            )
            for ch in res.get("channels_sent") or []:
                channels_used.append(ch)

            # Record any external channel delivery errors without triggering another external notification loop
            for ch_name in ("slack", "email"):
                ch_res = res.get(ch_name) or {}
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
                        project_id=log_dict.get("project_id"),
                        bug_report_id=bug_id,
                        jira_key=jira_key,
                        input_data={"source_log_id": log_id, "source_action": action, "channel": ch_name},
                        output_data={"error": err_txt},
                        notified_channels="ws",
                    )

    channels_str = ",".join(dict.fromkeys(channels_used)) if channels_used else None
    if log_id and channels_str:
        try:
            async with async_session() as session:
                row = await session.get(AgentLog, log_id)
                if row:
                    row.notified_channels = channels_str[:120]
                    await session.commit()
        except Exception as upd_err:
            logger.debug(f"Could not update notified_channels on log #{log_id}: {upd_err}")

    return channels_str


async def _write_internal_log_no_loop(
    agent_name: str,
    action: str,
    module: str,
    category: str,
    level: str,
    status: str,
    project_id: Optional[int] = None,
    bug_report_id: Optional[int] = None,
    jira_key: Optional[str] = None,
    input_data: Any = None,
    output_data: Any = None,
    notified_channels: Optional[str] = "ws",
    started_at: Optional[datetime] = None,
) -> Optional[int]:
    """Write an AgentLog row directly without re-triggering Slack/Email observation loops."""
    now = datetime.utcnow()
    entry = AgentLog(
        bug_report_id=bug_report_id,
        project_id=project_id,
        agent_name=agent_name,
        action=action,
        input_data=_serialize_payload(input_data),
        output_data=_serialize_payload(output_data),
        status=status,
        module=module[:120] if module else "System",
        category=category[:50] if category else "watcher_observation",
        level=level[:20] if level else "info",
        jira_key=jira_key[:50] if jira_key else None,
        notified_channels=notified_channels,
        started_at=started_at or now,
        finished_at=now,
    )
    async with async_session() as session:
        session.add(entry)
        await session.commit()
        await session.refresh(entry)
        log_dict = entry.to_dict()

    try:
        await manager.broadcast({
            "type": "watcher_event",
            "log": log_dict,
            "level": level,
            "timestamp": now.isoformat(),
        })
    except Exception:
        pass
    return entry.id


async def observe_agent_log(
    log_id: int,
    agent_name: str,
    action: str,
    status: str = "success",
    module: Optional[str] = None,
    category: str = "agent_action",
    level: Optional[str] = None,
    project_id: Optional[int] = None,
    bug_report_id: Optional[int] = None,
    jira_key: Optional[str] = None,
    input_data: Any = None,
    output_data: Any = None,
    notify: bool = True,
) -> None:
    """Observe a newly recorded AI agent action and forward telemetry + alerts safely."""
    if _OBSERVER_DEPTH.get() > 0:
        return
    if category in ("watcher_observation", "notification_delivery"):
        return

    token = _OBSERVER_DEPTH.set(_OBSERVER_DEPTH.get() + 1)
    try:
        settings_dict, project_name, bug_module, bug_jira_key = await _load_settings_and_names(
            project_id, bug_report_id
        )

        watcher_enabled = _as_bool(settings_dict.get("watcher_enabled"), True)
        monitor_agents = _as_bool(settings_dict.get("watcher_monitor_agents"), True)
        monitor_logs = _as_bool(settings_dict.get("watcher_monitor_logs"), True)

        resolved_module = infer_module(
            agent_name=agent_name,
            action=action,
            explicit_module=module,
            input_data=input_data,
            output_data=output_data,
            fallback_bug_module=bug_module,
        )
        resolved_jira = jira_key or bug_jira_key
        resolved_level = infer_level(status, category, level)

        async with async_session() as session:
            row = await session.get(AgentLog, log_id)
            if not row:
                return
            row.module = resolved_module
            row.category = category
            row.level = resolved_level
            if resolved_jira:
                row.jira_key = resolved_jira[:50]
            await session.commit()
            await session.refresh(row)
            log_dict = row.to_dict()

        reportable = is_watcher_reportable_event(
            agent_name=agent_name,
            action=action,
            status=status,
            level=resolved_level,
            category=category,
            jira_key=resolved_jira,
            module=resolved_module,
        )
        if reportable and not monitor_agents and agent_name != "Watcher":
            from services.notification_service import is_jira_update_event
            reportable = is_jira_update_event(
                agent_name=agent_name,
                action=action,
                status=status,
                level=resolved_level,
                category=category,
                jira_key=resolved_jira,
                module=resolved_module,
            )

        notify_bell = bool(watcher_enabled and reportable)
        notify_external = bool(notify and watcher_enabled and reportable)
        await _dispatch_notifications_and_ws(
            log_dict=log_dict,
            settings_dict=settings_dict,
            project_name=project_name,
            summary_hint=None,
            raw_output_data=output_data,
            raw_input_data=input_data,
            notify_external=notify_external,
            notify_ui_bell=notify_bell,
        )
    except Exception as e:
        logger.warning(f"Watcher observe_agent_log error (non-fatal): {e}")
    finally:
        _OBSERVER_DEPTH.reset(token)


async def record_system_event(
    action: str,
    summary: str,
    module: str,
    category: str = "system_change",
    status: str = "success",
    level: Optional[str] = None,
    agent_name: str = "Watcher",
    project_id: Optional[int] = None,
    bug_report_id: Optional[int] = None,
    jira_key: Optional[str] = None,
    input_data: Any = None,
    output_data: Any = None,
    notify: bool = True,
    notify_ui_bell: Optional[bool] = None,
    started_at: Optional[datetime] = None,
) -> Optional[int]:
    """Log any module change, update, failure, or success directly into Watcher telemetry and alert channels."""
    now = datetime.utcnow()
    resolved_level = infer_level(status, category, level)
    out_payload = output_data if output_data is not None else {"summary": summary}

    async with async_session() as session:
        entry = AgentLog(
            bug_report_id=bug_report_id,
            project_id=project_id,
            agent_name=agent_name,
            action=action[:255],
            input_data=_serialize_payload(input_data),
            output_data=_serialize_payload(out_payload),
            status=status,
            module=module[:120] if module else "System",
            category=category[:50] if category else "system_change",
            level=resolved_level[:20],
            jira_key=jira_key[:50] if jira_key else None,
            started_at=started_at or now,
            finished_at=now,
        )
        session.add(entry)
        await session.commit()
        await session.refresh(entry)
        log_dict = entry.to_dict()

    if _OBSERVER_DEPTH.get() > 0:
        return entry.id

    token = _OBSERVER_DEPTH.set(_OBSERVER_DEPTH.get() + 1)
    try:
        settings_dict, project_name, _, _ = await _load_settings_and_names(project_id, bug_report_id)
        watcher_enabled = _as_bool(settings_dict.get("watcher_enabled"), True)
        reportable = is_watcher_reportable_event(
            agent_name=agent_name,
            action=action,
            status=status,
            level=resolved_level,
            category=category,
            jira_key=jira_key,
            module=module,
        )
        notify_external = bool(notify and watcher_enabled and reportable)
        bell = (
            bool(watcher_enabled and reportable)
            if notify_ui_bell is None
            else bool(notify_ui_bell and watcher_enabled and reportable)
        )
        await _dispatch_notifications_and_ws(
            log_dict=log_dict,
            settings_dict=settings_dict,
            project_name=project_name,
            summary_hint=summary,
            raw_output_data=out_payload,
            raw_input_data=input_data,
            notify_external=notify_external,
            notify_ui_bell=bell,
        )
    except Exception as e:
        logger.warning(f"Watcher record_system_event error (non-fatal): {e}")
    finally:
        _OBSERVER_DEPTH.reset(token)

    return entry.id
