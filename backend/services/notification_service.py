"""Notification Service - Real SMTP Email and Rich Slack Webhook notifications."""
import asyncio
import html
import json
import logging
import re
import smtplib
import ssl
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any, Dict, List, Optional
import httpx

logger = logging.getLogger(__name__)

# Strict Watcher reporting scope: Jira updates & AI agent failures ONLY
AI_AGENT_NAMES = frozenset({
    "Desk Officer",
    "Detective",
    "Coroner",
    "Judge",
    "Sheriff",
    "Watcher",
    "Sanity Testers",
    "Auto-Test",
})
_AI_AGENT_NAMES_LOWER = frozenset(n.lower() for n in AI_AGENT_NAMES)

JIRA_UPDATE_ACTIONS = frozenset({
    "create_jira_ticket",
    "sync_ticket_evidence",
    "jira_status_drift_detected",
    "jira_issue_updated",
    "jira_ticket_created",
    "jira_status_changed",
    "jira_transitioned",
    "jira_in_qa_verified",
    "jira_retest_resolved",
    "jira_retest_failed",
    "jira_poll_failed",
    "jira_poll_error",
    "jira_transition_failed",
    "jira_attachment_partial_failure",
})

NON_JIRA_ACTIONS = frozenset({
    "project_created",
    "project_updated",
    "project_updated_via_desk_tool",
    "project_deleted",
    "skill_created",
    "skill_created_via_chat",
    "skill_saved_via_desk_tool",
    "skill_uploaded",
    "skill_deleted",
    "skill_deleted_via_desk_tool",
    "settings_updated",
    "chat_files_uploaded",
    "chat_history_cleared",
    "bug_status_changed",
    "auto_test_completed",
    "llm_connection_tested",
    "status_check",
    "system_wide_sweep",
    "triage_chat_message",
    "retest_workflow_executed",
})

NON_AGENT_SYSTEM_FAILURE_ACTIONS = frozenset({
    "api_server_error",
    "unhandled_api_exception",
    "chat_upload_failed",
    "llm_connection_test_failed",
    "module_app_health_failed",
    "slack_delivery_failed",
    "email_delivery_failed",
    "test_notification_sent",
})


def is_jira_update_event(
    agent_name: str = "",
    action: str = "",
    status: str = "",
    level: str = "",
    category: str = "",
    jira_key: Optional[str] = None,
    module: str = "",
) -> bool:
    """Return True iff this event represents a Jira Cloud update or Jira lifecycle failure."""
    cat = (category or "").strip().lower()
    act = (action or "").strip().lower()
    if cat in ("watcher_sweep", "notification_delivery", "module_health", "local_simulation", "regression_finding"):
        return False
    if act in NON_JIRA_ACTIONS or act in NON_AGENT_SYSTEM_FAILURE_ACTIONS:
        return False
    if act in JIRA_UPDATE_ACTIONS:
        if act in (
            "create_jira_ticket",
            "sync_ticket_evidence",
            "jira_status_changed",
            "jira_in_qa_verified",
            "jira_retest_resolved",
            "jira_retest_failed",
            "jira_status_drift_detected",
            "jira_issue_updated",
        ) and not jira_key and (status or "").strip().lower() != "failed":
            return False
        return True
    if cat == "jira_sync":
        return True
    if cat == "status_change" and bool(jira_key):
        return True
    return False


def is_ai_agent_failure_event(
    agent_name: str = "",
    action: str = "",
    status: str = "",
    level: str = "",
    category: str = "",
) -> bool:
    """Return True iff an AI agent execution failed (excludes non-agent CRUD/HTTP/delivery errors)."""
    cat = (category or "").strip().lower()
    act = (action or "").strip().lower()
    st = (status or "").strip().lower()
    lvl = (level or "").strip().lower()
    ag = (agent_name or "").strip().lower()

    if cat in ("watcher_sweep", "notification_delivery", "module_health", "local_simulation", "regression_finding"):
        return False
    if act in NON_AGENT_SYSTEM_FAILURE_ACTIONS or act in NON_JIRA_ACTIONS or act == "jira_retest_failed":
        return False
    if ag not in _AI_AGENT_NAMES_LOWER:
        return False
    return st == "failed" or lvl == "error" or cat in ("error", "failure")


def is_watcher_reportable_event(
    agent_name: str = "",
    action: str = "",
    status: str = "",
    level: str = "",
    category: str = "",
    jira_key: Optional[str] = None,
    module: str = "",
) -> bool:
    """Watcher reports externally & in alerts ONLY for Jira updates and AI agent failures."""
    return is_jira_update_event(
        agent_name=agent_name,
        action=action,
        status=status,
        level=level,
        category=category,
        jira_key=jira_key,
        module=module,
    ) or is_ai_agent_failure_event(
        agent_name=agent_name,
        action=action,
        status=status,
        level=level,
        category=category,
    )


def format_in_qa_alert_content(
    jira_key: Optional[str],
    jira_url: Optional[str],
    bug_title: str,
    issue_type: Optional[str],
    passed: bool,
    detective_result: Optional[Dict[str, Any]] = None,
    judge_result: Optional[Dict[str, Any]] = None,
    sanity_result: Optional[Dict[str, Any]] = None,
    evidence_list: Optional[List[Dict[str, Any]]] = None,
    telemetry_list: Optional[List[Dict[str, Any]]] = None,
    jira_status: str = "In QA",
    bug_id: Optional[int] = None,
) -> tuple[str, str, Dict[str, Any]]:
    """Format a clear, understandable Passed/Failed + Evidence alert for Jira 'In QA' verification."""
    det = detective_result or {}
    jdg = judge_result or {}
    san = sanity_result or {}
    ev_items = list(evidence_list or [])
    tel_items = list(telemetry_list or [])

    ticket_label = jira_key or (f"Bug #{bug_id}" if bug_id else "QA Ticket")
    verdict_word = "PASSED" if passed else "FAILED"
    short_title = (bug_title or ticket_label).strip()[:75]
    title = f"QA {verdict_word}: {ticket_label} — {short_title}"

    det_passed = bool(det.get("is_resolved", False))
    det_tag = "PASSED" if det_passed else "FAILED"
    det_note = (
        (det.get("retest_summary") if det_passed else (det.get("failure_reason") or det.get("retest_summary")))
        or ("Issue verified fixed." if det_passed else "Defect still reproduces in test check.")
    ).strip()[:240]

    judge_verdict = str(jdg.get("verdict") or "CONFIRMED").strip().upper()
    judge_notes = str(jdg.get("judge_notes") or jdg.get("final_root_cause") or "Reviewed evidence.").strip()[:240]

    sanity_status = str(san.get("overall_status") or "PASSED").strip().upper()
    sanity_reg = bool(san.get("regression_detected") or sanity_status == "FAILED")
    sanity_tag = "FAILED (Regression)" if sanity_reg else sanity_status
    sanity_summary = str(san.get("summary") or "Impacted areas checked.").strip()[:240]

    ev_names = [
        str(e.get("file_name") or e.get("filename") or "").strip()
        for e in ev_items
        if (e.get("file_name") or e.get("filename"))
    ]
    ev_preview = ", ".join(ev_names[:6]) + (f" (+{len(ev_names) - 6} more)" if len(ev_names) > 6 else "")
    tel_notes: List[str] = []
    for t in tel_items[:3]:
        if isinstance(t, dict):
            br = t.get("browser") or "browser"
            hs = t.get("http_status")
            ce = len(t.get("console_errors") or [])
            pe = len(t.get("page_errors") or [])
            ne = len(t.get("network_errors") or [])
            tel_notes.append(
                f"{br}: HTTP {hs if hs is not None else 'n/a'}, {ce} console err, {pe} page err, {ne} net >=400"
            )

    if ev_names and tel_notes:
        evidence_line = f"{len(ev_names)} file(s) [{ev_preview}] | Live Telemetry: {'; '.join(tel_notes)}"
    elif ev_names:
        evidence_line = f"{len(ev_names)} file(s) [{ev_preview}]"
    elif tel_notes:
        evidence_line = f"Live Telemetry: {'; '.join(tel_notes)}"
    else:
        evidence_line = "No screenshot/video files captured"

    ev_short = ev_preview or (f"{len(ev_items)} file(s)" if ev_items else "none")
    summary_line = (
        f"Ticket {ticket_label} is {verdict_word} "
        f"(Detective: {det_tag} | Judge: {judge_verdict} | Sanity Testers: {sanity_tag}) "
        f"— Evidence: {ev_short} | Jira status unchanged ({jira_status})"
    )

    type_part = f" ({issue_type})" if issue_type else ""
    url_part = f"\nLink: {jira_url}" if jira_url else ""
    message = (
        f"{summary_line}\n\n"
        f"Ticket: {ticket_label}{type_part} — {bug_title}{url_part}\n"
        f"Verdict: {verdict_word} (Alert-only — Jira status remains '{jira_status}')\n"
        f"• Detective: {det_tag} — {det_note}\n"
        f"• Judge: {judge_verdict} — {judge_notes}\n"
        f"• Sanity Testers: {sanity_tag} — {sanity_summary}\n"
        f"• Coroner Evidence: {evidence_line}"
    )

    details = {
        "ticket_id": ticket_label,
        "issue_type": issue_type,
        "jira_url": jira_url,
        "verdict": verdict_word,
        "jira_status_unchanged": jira_status,
        "detective": {"verdict": det_tag, "summary": det_note},
        "judge": {"verdict": judge_verdict, "notes": judge_notes},
        "sanity_testers": {"status": sanity_tag, "regression_detected": sanity_reg, "summary": sanity_summary},
        "evidence_files": ev_names,
        "telemetry": tel_notes,
    }
    return title[:140], message[:2400], details


def _as_bool(val: Any, default: bool = False) -> bool:
    if val is None:
        return default
    if isinstance(val, bool):
        return val
    return str(val).strip().lower() in ("true", "1", "yes", "on")


def _as_int(val: Any, default: int = 587) -> int:
    try:
        return int(str(val).strip())
    except Exception:
        return default


def _parse_recipients(raw: str) -> List[str]:
    if not raw:
        return []
    parts = [p.strip() for p in re.split(r"[,;\s]+", str(raw)) if p.strip()]
    return [p for p in parts if "@" in p]


def _redact_webhook(url: str) -> str:
    if not url:
        return ""
    if "hooks.slack.com" in url:
        return "https://hooks.slack.com/services/<redacted>"
    return re.sub(r"(https?://[^/]+).*", r"\1/<redacted>", url)


class NotificationService:
    """Sends real-time alerts via SMTP Email and Slack Webhooks."""

    def __init__(
        self,
        email_enabled: bool = False,
        email_address: str = "",
        slack_enabled: bool = False,
        slack_webhook: str = "",
        smtp_host: str = "",
        smtp_port: int = 587,
        smtp_username: str = "",
        smtp_password: str = "",
        smtp_from_email: str = "",
        smtp_from: str = "",
        smtp_use_tls: bool = True,
        smtp_use_ssl: bool = False,
        notify_on_all_events: bool = False,
        notify_on_changes: bool = True,
        notify_on_success: bool = False,
        notify_on_failures: bool = True,
    ):
        self.email_enabled = bool(email_enabled)
        self.email_address = (email_address or "").strip()
        self.slack_enabled = bool(slack_enabled)
        self.slack_webhook = (slack_webhook or "").strip()

        self.smtp_host = (smtp_host or "").strip()
        self.smtp_port = _as_int(smtp_port, 587)
        self.smtp_username = (smtp_username or "").strip()
        self.smtp_password = smtp_password or ""
        effective_from = (smtp_from_email or smtp_from or self.smtp_username or self.email_address).strip()
        self.smtp_from_email = effective_from
        self.smtp_from = effective_from
        self.smtp_use_tls = bool(smtp_use_tls)
        self.smtp_use_ssl = bool(smtp_use_ssl)

        self.notify_on_all_events = bool(notify_on_all_events)
        self.notify_on_changes = bool(notify_on_changes)
        self.notify_on_success = bool(notify_on_success)
        self.notify_on_failures = bool(notify_on_failures)

    @classmethod
    def from_settings(cls, settings_dict: Optional[Dict[str, Any]]) -> "NotificationService":
        """Construct a configured NotificationService from flat QA Officer Settings dict."""
        s = settings_dict or {}
        from_val = str(s.get("smtp_from_email") or s.get("smtp_from") or "")
        return cls(
            email_enabled=_as_bool(s.get("notification_email_enabled"), False),
            email_address=str(s.get("notification_email") or ""),
            slack_enabled=_as_bool(s.get("notification_slack_enabled"), False),
            slack_webhook=str(s.get("notification_slack_webhook") or ""),
            smtp_host=str(s.get("smtp_host") or ""),
            smtp_port=_as_int(s.get("smtp_port"), 587),
            smtp_username=str(s.get("smtp_username") or ""),
            smtp_password=str(s.get("smtp_password") or ""),
            smtp_from_email=from_val,
            smtp_from=from_val,
            smtp_use_tls=_as_bool(s.get("smtp_use_tls"), True),
            smtp_use_ssl=_as_bool(s.get("smtp_use_ssl"), False),
            notify_on_all_events=_as_bool(s.get("notify_on_all_events"), False),
            notify_on_changes=_as_bool(s.get("notify_on_changes"), True),
            notify_on_success=_as_bool(s.get("notify_on_success"), False),
            notify_on_failures=_as_bool(s.get("notify_on_failures"), True),
        )

    def has_any_channel(self) -> bool:
        return bool(
            (self.slack_enabled and self.slack_webhook)
            or (self.email_enabled and _parse_recipients(self.email_address))
        )

    def should_notify(
        self,
        category: str = "update",
        status: str = "success",
        level: str = "info",
        agent_name: str = "",
        action: str = "",
        jira_key: Optional[str] = None,
        module: str = "",
    ) -> bool:
        """Enforce strict Watcher notification scope: Jira updates and AI agent failures ONLY."""
        if not self.has_any_channel():
            return False

        cat = (category or "").strip().lower()
        if cat == "notification_test":
            return True

        is_jira = is_jira_update_event(
            agent_name=agent_name,
            action=action,
            status=status,
            level=level,
            category=category,
            jira_key=jira_key,
            module=module,
        )
        is_agent_failure = is_ai_agent_failure_event(
            agent_name=agent_name,
            action=action,
            status=status,
            level=level,
            category=category,
        )

        if not is_jira and not is_agent_failure:
            return False
        if is_jira and is_agent_failure:
            return bool(self.notify_on_changes or self.notify_on_failures)
        if is_agent_failure:
            return bool(self.notify_on_failures)
        return bool(self.notify_on_changes)

    @staticmethod
    def _build_email_bodies(
        title: str,
        message: str,
        level: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> tuple[str, str]:
        meta = metadata or {}
        badge_colors = {
            "success": ("#10b981", "#ecfdf5", "SUCCESS"),
            "error": ("#ef4444", "#fef2f2", "FAILURE"),
            "warning": ("#f59e0b", "#fffbeb", "WARNING"),
            "change": ("#06b6d4", "#ecfeff", "CHANGE"),
            "info": ("#6366f1", "#eef2ff", "UPDATE"),
        }
        accent, pill_bg, label = badge_colors.get(level, badge_colors["info"])
        timestamp = meta.get("timestamp") or datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")

        details_str = ""
        if meta.get("details"):
            raw_det = meta["details"]
            if isinstance(raw_det, (dict, list)):
                details_str = json.dumps(raw_det, indent=2, ensure_ascii=False, default=str)
            else:
                details_str = str(raw_det)
            details_str = details_str[:2400]

        plain_parts = [f"[QA Officer | {label}] {title}", "", message]
        if details_str:
            plain_parts.extend(["", "--- Details ---", details_str])
        plain_parts.extend(["", f"QA Officer Watcher • {timestamp}"])
        plain_text = "\n".join(plain_parts)

        details_html = ""
        if details_str:
            details_html = (
                f'<div style="margin-top:16px;">'
                f'<div style="font-size:11px;font-weight:700;text-transform:uppercase;color:#64748b;margin-bottom:6px;">Details</div>'
                f'<pre style="background:#0f172a;color:#e2e8f0;padding:12px;border-radius:8px;font-size:12px;'
                f'overflow-x:auto;white-space:pre-wrap;margin:0;">{html.escape(details_str)}</pre>'
                f'</div>'
            )

        html_body = f"""<!DOCTYPE html>
<html>
<body style="margin:0;padding:24px;background:#f1f5f9;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;color:#0f172a;">
  <div style="max-width:640px;margin:0 auto;background:#ffffff;border:1px solid #cbd5e1;border-radius:14px;overflow:hidden;box-shadow:0 6px 24px rgba(15,23,42,0.08);">
    <div style="background:#0f172a;padding:18px 22px;border-bottom:3px solid {accent};display:flex;align-items:center;justify-content:space-between;">
      <div style="color:#f8fafc;font-size:15px;font-weight:700;">👁️ QA Officer Watcher</div>
      <span style="display:inline-block;padding:3px 10px;border-radius:999px;background:{pill_bg};color:{accent};font-size:11px;font-weight:700;letter-spacing:0.5px;">{label}</span>
    </div>
    <div style="padding:22px;">
      <h2 style="margin:0 0 10px 0;font-size:18px;color:#0f172a;">{html.escape(title)}</h2>
      <p style="margin:0 0 16px 0;font-size:14px;line-height:1.55;color:#334155;white-space:pre-wrap;">{html.escape(message)}</p>
      {details_html}
    </div>
    <div style="padding:12px 22px;background:#f8fafc;border-top:1px solid #e2e8f0;font-size:11px;color:#64748b;">
      Sent automatically by QA Officer System-Wide Watcher • {html.escape(str(timestamp))}
    </div>
  </div>
</body>
</html>"""
        return html_body, plain_text

    def _send_email_sync(
        self,
        subject: str,
        html_body: str,
        plain_body: str,
        recipients: List[str],
    ) -> None:
        from_addr = self.smtp_from_email or self.smtp_username or recipients[0]
        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"[QA Officer] {subject}"
        msg["From"] = from_addr
        msg["To"] = ", ".join(recipients)
        msg.attach(MIMEText(plain_body, "plain", "utf-8"))
        msg.attach(MIMEText(html_body, "html", "utf-8"))

        ctx = ssl.create_default_context()
        if self.smtp_use_ssl or self.smtp_port == 465:
            with smtplib.SMTP_SSL(self.smtp_host, self.smtp_port, timeout=15, context=ctx) as server:
                if self.smtp_username and self.smtp_password:
                    server.login(self.smtp_username, self.smtp_password)
                server.sendmail(from_addr, recipients, msg.as_string())
        else:
            with smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=15) as server:
                server.ehlo()
                if self.smtp_use_tls:
                    server.starttls(context=ctx)
                    server.ehlo()
                if self.smtp_username and self.smtp_password:
                    server.login(self.smtp_username, self.smtp_password)
                server.sendmail(from_addr, recipients, msg.as_string())

    async def _send_email(
        self,
        subject: str,
        body: str,
        level: str = "info",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Send real email notification via SMTP (runs blocking smtplib safely off event loop)."""
        if not self.email_enabled:
            return {"attempted": False, "sent": False, "reason": "Email alerts disabled"}

        recipients = _parse_recipients(self.email_address)
        if not recipients:
            return {"attempted": False, "sent": False, "error": "No valid recipient email address configured"}

        if not self.smtp_host:
            reason = (
                "SMTP host is not configured in Settings (set smtp_host e.g. smtp.gmail.com or "
                "smtp.office365.com to deliver emails)."
            )
            logger.warning(f"Email alert skipped for '{subject}': {reason}")
            return {
                "attempted": True,
                "sent": False,
                "recipients": recipients,
                "error": reason,
            }

        try:
            html_body, plain_body = self._build_email_bodies(subject, body, level, metadata)
            await asyncio.to_thread(
                self._send_email_sync,
                subject,
                html_body,
                plain_body,
                recipients,
            )
            logger.info(f"Email notification sent: {subject} -> {', '.join(recipients)}")
            return {"attempted": True, "sent": True, "recipients": recipients}
        except Exception as e:
            err = str(e)
            logger.error(f"Failed to send email via {self.smtp_host}:{self.smtp_port}: {err}")
            return {
                "attempted": True,
                "sent": False,
                "recipients": recipients,
                "error": err,
            }

    async def _send_slack(
        self,
        title: str,
        message: str,
        level: str = "info",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Send rich Slack Block Kit notification via incoming webhook."""
        if not self.slack_enabled:
            return {"attempted": False, "sent": False, "reason": "Slack alerts disabled"}
        if not self.slack_webhook:
            return {"attempted": False, "sent": False, "error": "Slack webhook URL is not configured"}

        emoji_map = {
            "info": "ℹ️",
            "success": "✅",
            "warning": "⚠️",
            "error": "🚨",
            "change": "🔄",
        }
        emoji = emoji_map.get(level, "ℹ️")
        header_text = f"{emoji} {title}".replace("\n", " ")[:145]
        meta = metadata or {}

        details_block = ""
        if meta.get("details"):
            raw_det = meta["details"]
            det_str = (
                json.dumps(raw_det, ensure_ascii=False, default=str)
                if isinstance(raw_det, (dict, list))
                else str(raw_det)
            )
            if det_str:
                details_block = f"\n```{det_str[:900]}```"

        main_text = f"{message[:1800]}{details_block}"[:2900]
        blocks: List[Dict[str, Any]] = [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": header_text},
            },
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": main_text},
            },
        ]
        ts_str = meta.get("timestamp") or datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
        blocks.append(
            {
                "type": "context",
                "elements": [{"type": "mrkdwn", "text": f"👁️ QA Officer Watcher • {ts_str}"}],
            }
        )

        payload = {
            "text": f"{emoji} *{title}*\n{message[:400]}",
            "blocks": blocks,
        }

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.post(self.slack_webhook, json=payload)
                if 200 <= response.status_code < 300:
                    logger.info(f"Slack notification sent: {title}")
                    return {"attempted": True, "sent": True, "status_code": response.status_code}
                err_body = (response.text or response.reason_phrase or "")[:200]
                logger.warning(
                    f"Slack notification failed ({_redact_webhook(self.slack_webhook)}): "
                    f"HTTP {response.status_code} {err_body}"
                )
                return {
                    "attempted": True,
                    "sent": False,
                    "status_code": response.status_code,
                    "error": f"Slack HTTP {response.status_code}: {err_body}",
                }
        except Exception as e:
            err_msg = str(e).replace(self.slack_webhook, "<redacted>")
            logger.error(f"Failed to send Slack notification: {err_msg}")
            return {"attempted": True, "sent": False, "error": err_msg}

    async def notify_event(
        self,
        title: str,
        message: str,
        level: str = "info",
        category: str = "update",
        status: str = "success",
        project_name: Optional[str] = None,
        module: Optional[str] = None,
        agent_name: Optional[str] = None,
        action: Optional[str] = None,
        bug_id: Optional[int] = None,
        jira_key: Optional[str] = None,
        details: Any = None,
        force: bool = False,
        channels: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Dispatch rich structured alert to enabled Slack and/or Email channels."""
        if not force and not self.should_notify(
            category=category,
            status=status,
            level=level,
            agent_name=agent_name or "",
            action=action or "",
            jira_key=jira_key,
            module=module or "",
        ):
            return {
                "skipped": True,
                "channels_sent": [],
                "email": {"attempted": False, "sent": False},
                "slack": {"attempted": False, "sent": False},
            }

        want_email = (channels is None or "email" in channels) and self.email_enabled
        want_slack = (channels is None or "slack" in channels) and self.slack_enabled

        meta: Dict[str, Any] = {
            "project_name": project_name,
            "module": module,
            "agent_name": agent_name,
            "category": category,
            "status": status,
            "bug_ref": f"Bug #{bug_id}" if bug_id else None,
            "jira_key": jira_key,
            "details": details,
            "timestamp": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
        }

        email_res: Dict[str, Any] = {"attempted": False, "sent": False}
        slack_res: Dict[str, Any] = {"attempted": False, "sent": False}

        tasks = []
        task_keys = []
        if want_email:
            tasks.append(self._send_email(title, message, level=level, metadata=meta))
            task_keys.append("email")
        if want_slack:
            tasks.append(self._send_slack(title, message, level=level, metadata=meta))
            task_keys.append("slack")

        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for k, res in zip(task_keys, results):
                if isinstance(res, Exception):
                    val = {"attempted": True, "sent": False, "error": str(res)}
                else:
                    val = res
                if k == "email":
                    email_res = val
                elif k == "slack":
                    slack_res = val

        sent_list = []
        if slack_res.get("sent"):
            sent_list.append("slack")
        if email_res.get("sent"):
            sent_list.append("email")

        return {
            "skipped": False,
            "channels_sent": sent_list,
            "email": email_res,
            "slack": slack_res,
        }

    async def notify(
        self,
        title: str,
        message: str,
        level: str = "info",
        **kwargs,
    ) -> Dict[str, Any]:
        """Backwards-compatible wrapper sending notification through enabled channels."""
        return await self.notify_event(
            title=title,
            message=message,
            level=level,
            **kwargs,
        )

    async def notify_bug_created(
        self,
        bug_title: str,
        jira_key: str,
        jira_url: str,
        project_name: Optional[str] = None,
        module: Optional[str] = None,
        bug_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Notify about a newly created Jira bug ticket."""
        return await self.notify_event(
            title=f"New Jira Ticket: {jira_key} — {bug_title[:80]}",
            message=f"Sheriff created Jira ticket <{jira_url}|{jira_key}> in Backlog.\n\n*Title:* {bug_title}",
            level="change",
            category="jira_sync",
            status="success",
            project_name=project_name,
            module=module or "Jira Integration",
            agent_name="Sheriff",
            action="jira_ticket_created",
            bug_id=bug_id,
            jira_key=jira_key,
            force=False,
        )

    async def notify_bug_resolved(
        self,
        bug_title: str,
        jira_key: str,
        project_name: Optional[str] = None,
        module: Optional[str] = None,
        bug_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Notify about a resolved Jira bug verified in QA."""
        return await self.notify_event(
            title=f"Jira Updated ({jira_key}): Ready To Deploy",
            message=f"Jira ticket {jira_key} ({bug_title}) passed Watcher/Detective QA retest and transitioned to Ready To Deploy.",
            level="change",
            category="jira_sync",
            status="success",
            project_name=project_name,
            module=module or "QA Verification",
            agent_name="Watcher",
            action="jira_retest_resolved",
            bug_id=bug_id,
            jira_key=jira_key,
            force=False,
        )

    async def notify_bug_failed(
        self,
        bug_title: str,
        jira_key: str,
        reason: str,
        project_name: Optional[str] = None,
        module: Optional[str] = None,
        bug_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Notify about a Jira ticket failing QA verification."""
        return await self.notify_event(
            title=f"Jira Updated ({jira_key}): Failed QA",
            message=f"Jira ticket {jira_key} ({bug_title}) failed QA retesting.\n\n*Reason:* {reason}",
            level="error",
            category="jira_sync",
            status="failed",
            project_name=project_name,
            module=module or "QA Verification",
            agent_name="Watcher",
            action="jira_retest_failed",
            bug_id=bug_id,
            jira_key=jira_key,
            force=False,
        )

    async def notify_status_change(
        self,
        jira_key: str,
        old_status: str,
        new_status: str,
        project_name: Optional[str] = None,
        module: Optional[str] = None,
        bug_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Notify about a Jira ticket status transition."""
        return await self.notify_event(
            title=f"Jira Status Change: {jira_key}",
            message=f"Jira ticket {jira_key} moved from *{old_status}* to *{new_status}*",
            level="change",
            category="jira_sync",
            status="success",
            project_name=project_name,
            module=module or "Jira Integration",
            agent_name="Watcher",
            action="jira_status_changed",
            bug_id=bug_id,
            jira_key=jira_key,
            force=False,
        )

    async def notify_in_qa_verification(
        self,
        bug_title: str,
        jira_key: Optional[str],
        jira_url: Optional[str] = None,
        issue_type: Optional[str] = None,
        passed: bool = True,
        detective_result: Optional[Dict[str, Any]] = None,
        judge_result: Optional[Dict[str, Any]] = None,
        sanity_result: Optional[Dict[str, Any]] = None,
        evidence_list: Optional[List[Dict[str, Any]]] = None,
        telemetry_list: Optional[List[Dict[str, Any]]] = None,
        jira_status: str = "In QA",
        project_name: Optional[str] = None,
        module: Optional[str] = None,
        bug_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Send understandable alert-only Pass/Fail + Evidence summary without mutating Jira status."""
        title, message, details = format_in_qa_alert_content(
            jira_key=jira_key,
            jira_url=jira_url,
            bug_title=bug_title,
            issue_type=issue_type,
            passed=passed,
            detective_result=detective_result,
            judge_result=judge_result,
            sanity_result=sanity_result,
            evidence_list=evidence_list,
            telemetry_list=telemetry_list,
            jira_status=jira_status,
            bug_id=bug_id,
        )
        return await self.notify_event(
            title=title,
            message=message,
            level="success" if passed else "error",
            category="jira_sync",
            status="success" if passed else "failed",
            project_name=project_name,
            module=module or "QA Verification",
            agent_name="Watcher",
            action="jira_in_qa_verified",
            bug_id=bug_id,
            jira_key=jira_key,
            details=None,
            force=False,
        )
