"""Sheriff Agent - Creates Jira issues with Coroner evidence, priority, label, and reporter."""
import logging
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import config
from agents.base import BaseAgent
from services.report_formatter import ensure_evidence_in_report
from database import async_session
from models.bug_report import BugReport
from services.jira_client import JiraClient
from services.watcher_bus import record_system_event

logger = logging.getLogger(__name__)


def resolve_evidence_items(
    evidence_list: Optional[List[Dict[str, Any]]],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, str]]]:
    """Resolve and deduplicate Coroner/upload evidence paths on disk.

    Returns (resolved_items, missing_items).
    """
    resolved: List[Dict[str, Any]] = []
    missing: List[Dict[str, str]] = []
    seen_resolved_paths = set()

    for ev in evidence_list or []:
        if not isinstance(ev, dict):
            continue
        raw_fp = str(ev.get("file_path") or "").strip()
        raw_fn = str(ev.get("file_name") or ev.get("filename") or "").strip()
        if not raw_fn and raw_fp:
            raw_fn = Path(raw_fp).name

        candidates: List[Path] = []
        if raw_fp:
            candidates.append(Path(raw_fp))
        if raw_fn:
            candidates.extend([
                config.EVIDENCE_DIR / raw_fn,
                config.EVIDENCE_DIR / "videos" / raw_fn,
                config.CHAT_UPLOADS_DIR / raw_fn,
            ])
        if raw_fp:
            bname = Path(raw_fp).name
            candidates.extend([
                config.EVIDENCE_DIR / bname,
                config.EVIDENCE_DIR / "videos" / bname,
                config.CHAT_UPLOADS_DIR / bname,
            ])

        found_path: Optional[Path] = None
        for cand in candidates:
            try:
                if cand.exists() and cand.is_file() and cand.stat().st_size > 0:
                    found_path = cand
                    break
            except OSError:
                continue

        if not found_path:
            if raw_fn or raw_fp:
                missing.append({
                    "file_name": raw_fn or Path(raw_fp).name,
                    "error": f"Evidence file not found or empty on disk: {raw_fp or raw_fn}",
                })
            continue

        norm_key = str(found_path.resolve()).lower()
        if norm_key in seen_resolved_paths:
            continue
        seen_resolved_paths.add(norm_key)

        item = dict(ev)
        item["file_path"] = str(found_path)
        item["file_name"] = raw_fn or found_path.name
        item["type"] = ev.get("type") or ("video" if found_path.suffix.lower() in (".webm", ".mp4", ".mov") else "screenshot")
        resolved.append(item)

    return resolved, missing


class SheriffAgent(BaseAgent):
    """Jira Ticket Creation Agent."""

    def __init__(self, default_reporter: str = "Mohamed Kamel"):
        super().__init__(
            name="Sheriff",
            role="Jira Ticket Commander",
            color="#3b82f6",
            icon="🏷️",
        )
        self.default_reporter = default_reporter

    @staticmethod
    async def _upload_evidence_batch(
        client: JiraClient,
        issue_key: str,
        resolved_items: List[Dict[str, Any]],
        existing_filenames: Optional[set] = None,
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, str]]]:
        """Upload resolved evidence files to Jira issue, avoiding duplicates cleanly."""
        attached: List[Dict[str, Any]] = []
        failed: List[Dict[str, str]] = []
        already_on_issue = {str(n).lower() for n in (existing_filenames or set()) if n}

        for ev in resolved_items:
            file_path = ev["file_path"]
            file_name = ev["file_name"]
            if file_name.lower() in already_on_issue:
                continue
            try:
                resp = await client.add_attachment(issue_key, file_path, file_name)
                first_att = resp[0] if isinstance(resp, list) and resp else (resp if isinstance(resp, dict) else {})
                attached.append({
                    "file_name": file_name,
                    "type": ev.get("type", "screenshot"),
                    "stage": ev.get("stage", "detection"),
                    "jira_attachment_id": first_att.get("id"),
                })
                already_on_issue.add(file_name.lower())
            except Exception as attach_err:
                err_str = str(attach_err)
                logger.warning(f"Failed to attach {file_name} to {issue_key}: {err_str}")
                failed.append({"file_name": file_name, "error": err_str})

        return attached, failed

    async def sync_evidence_to_existing_ticket(
        self,
        issue_key: str,
        evidence_list: List[Dict[str, Any]],
        formatted_report: Optional[str],
        bug_data: Dict[str, Any],
        project_info: Dict[str, Any],
        jira_settings: Dict[str, str],
        bug_report_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Ensure an already-raised Jira issue has all Coroner evidence files attached & referenced."""
        resolved_evidence, missing_files = resolve_evidence_items(evidence_list)
        base_url = (jira_settings.get("jira_base_url") or project_info.get("jira_base_url") or "").strip()
        email = (jira_settings.get("jira_email") or "").strip()
        token = (jira_settings.get("jira_api_token") or "").strip()
        live_configured = bool(base_url and email and token)

        updated_report = ensure_evidence_in_report(
            formatted_report or "",
            resolved_evidence or evidence_list,
            bug_data,
        )

        if not live_configured:
            return {
                "synced": False,
                "newly_attached": [],
                "existing_attached": [e["file_name"] for e in resolved_evidence],
                "failed_attachments": missing_files,
                "updated_report": updated_report,
            }

        client = JiraClient(base_url=base_url, email=email, api_token=token)
        existing_names = set()
        try:
            issue = await client.get_issue(issue_key)
            existing_names = {
                str(a.get("filename")).strip()
                for a in (issue.get("attachments") or [])
                if a.get("filename")
            }
        except Exception as fetch_err:
            logger.warning(f"Could not inspect existing attachments on {issue_key}: {fetch_err}")

        newly_attached, upload_failed = await self._upload_evidence_batch(
            client=client,
            issue_key=issue_key,
            resolved_items=resolved_evidence,
            existing_filenames=existing_names,
        )
        all_failed = missing_files + upload_failed

        # Sync Jira description cleanly if updated_report added missing evidence links
        description_synced = False
        if updated_report and formatted_report and updated_report.strip() != formatted_report.strip():
            try:
                await client.update_issue_description(issue_key, updated_report)
                description_synced = True
            except Exception as desc_err:
                logger.warning(f"Could not sync updated Evidence section on {issue_key}: {desc_err}")

        if bug_report_id and description_synced:
            async with async_session() as session:
                bug = await session.get(BugReport, bug_report_id)
                if bug:
                    bug.formatted_report = updated_report
                    await session.commit()

        if newly_attached or all_failed:
            await self.log_action(
                action="sync_ticket_evidence",
                input_data={
                    "issue_key": issue_key,
                    "evidence_count": len(resolved_evidence),
                },
                output_data={
                    "newly_attached": [a["file_name"] for a in newly_attached],
                    "already_present": sorted(existing_names),
                    "failed_attachments": all_failed,
                    "description_synced": description_synced,
                },
                status="failed" if (all_failed and not newly_attached and not existing_names) else "success",
                bug_report_id=bug_report_id,
                project_id=project_info.get("id"),
                module=bug_data.get("module") or "Jira Integration",
                category="jira_sync",
                level="warning" if all_failed else "change",
                jira_key=issue_key,
            )

        return {
            "synced": True,
            "newly_attached": [a["file_name"] for a in newly_attached],
            "existing_attached": sorted(existing_names),
            "all_attached": sorted(existing_names | {a["file_name"] for a in newly_attached}),
            "failed_attachments": all_failed,
            "description_synced": description_synced,
            "updated_report": updated_report,
        }

    async def create_jira_ticket(
        self,
        bug_data: Dict[str, Any],
        formatted_report: str,
        evidence_list: List[Dict[str, Any]],
        project_info: Dict[str, Any],
        jira_settings: Dict[str, str],
        bug_report_id: Optional[int] = None,
        reporter_name: Optional[str] = None,
        target_project_key: Optional[str] = None,
        notify_on_success: bool = True,
    ) -> Dict[str, Any]:
        """Create Jira ticket in 'Backlog' with bug report, Coroner evidence, priority, and label."""
        start_time = datetime.utcnow()
        reporter = reporter_name or jira_settings.get("default_reporter") or self.default_reporter
        jira_key = (target_project_key or project_info.get("jira_project_key") or "QA").strip().upper()
        base_url = (jira_settings.get("jira_base_url") or project_info.get("jira_base_url") or "").strip()
        email = (jira_settings.get("jira_email") or "").strip()
        token = (jira_settings.get("jira_api_token") or "").strip()
        live_configured = bool(base_url and email and token)

        priority = str(bug_data.get("severity") or "medium").lower().strip()
        if priority not in ["lowest", "low", "medium", "high", "highest"]:
            priority = "medium"

        label = str(bug_data.get("label") or "Front-End").strip()
        if label not in ["Back-End", "Front-End", "Mobile"]:
            label = "Front-End"

        resolved_evidence, missing_files = resolve_evidence_items(evidence_list)
        effective_report = ensure_evidence_in_report(
            formatted_report or "",
            resolved_evidence or evidence_list,
            bug_data,
        )

        ticket_result: Optional[Dict[str, Any]] = None

        if live_configured and jira_key:
            client = JiraClient(base_url=base_url, email=email, api_token=token)
            try:
                created = await client.create_issue(
                    project_key=jira_key,
                    summary=f"[{label}] {bug_data.get('title') or 'Reported Bug'}",
                    description=effective_report,
                    issue_type="Bug",
                    priority=priority,
                    labels=[label],
                    reporter_name=reporter,
                )
                issue_key = created["key"]
                issue_url = created["url"]

                transitioned_backlog = False
                try:
                    transitioned_backlog = bool(await client.transition_issue(issue_key, "Backlog"))
                except Exception as trans_err:
                    logger.warning(f"Could not transition {issue_key} to Backlog status: {trans_err}")

                board_backlog_ok = False
                if not transitioned_backlog:
                    try:
                        board_backlog_ok = bool(await client.move_to_backlog(issue_key))
                    except Exception as bl_err:
                        logger.warning(f"Could not move {issue_key} via Agile backlog API: {bl_err}")

                attached_files, upload_failed = await self._upload_evidence_batch(
                    client=client,
                    issue_key=issue_key,
                    resolved_items=resolved_evidence,
                )
                failed_attachments = missing_files + upload_failed
                attachment_warning = None
                if failed_attachments:
                    attachment_warning = (
                        f"{len(failed_attachments)} evidence file(s) failed to attach: "
                        + ", ".join(f"{f['file_name']} ({f['error'][:80]})" for f in failed_attachments[:3])
                    )
                    await record_system_event(
                        action="jira_attachment_partial_failure",
                        summary=f"Sheriff created {issue_key}, but {attachment_warning}",
                        module=bug_data.get("module") or "Jira Integration",
                        category="error",
                        status="failed",
                        level="warning",
                        agent_name="Sheriff",
                        project_id=project_info.get("id"),
                        bug_report_id=bug_report_id,
                        jira_key=issue_key,
                        output_data={"attached": attached_files, "failed": failed_attachments},
                    )

                ticket_result = {
                    "key": issue_key,
                    "url": issue_url,
                    "status": "Backlog",
                    "project_key": jira_key,
                    "jira_workflow_status": "Backlog" if transitioned_backlog else "To Do",
                    "backlog_placement": "workflow_status" if transitioned_backlog else "board_backlog",
                    "placement_note": (
                        None
                        if transitioned_backlog
                        else (
                            "Placed in Jira Software Board Backlog (workflow status: To Do, no sprint assigned)"
                            if board_backlog_ok
                            else "Created in Jira Backlog without sprint assignment (workflow initial status: To Do)"
                        )
                    ),
                    "reporter": reporter,
                    "priority": priority,
                    "label": label,
                    "simulated": False,
                    "evidence_count": len(resolved_evidence),
                    "attached_count": len(attached_files),
                    "evidence_attached": [a["file_name"] for a in attached_files],
                    "failed_attachments": failed_attachments,
                    **({"attachment_warning": attachment_warning} if attachment_warning else {}),
                }
            except Exception as e:
                err_msg = str(e)
                if "project" in err_msg.lower() or "issuetype" in err_msg.lower():
                    try:
                        projs = await client.list_projects()
                        keys_list = [f"{p.get('key')} ({p.get('name')})" for p in projs if p.get("key")]
                        if keys_list:
                            err_msg = f"{err_msg} | Available Jira projects: {', '.join(keys_list[:8])}"
                    except Exception:
                        pass

                logger.error(f"Sheriff live Jira issue creation failed for project '{jira_key}': {err_msg}")
                failed_result = {
                    "key": None,
                    "url": None,
                    "status": "failed",
                    "project_key": jira_key,
                    "reporter": reporter,
                    "priority": priority,
                    "label": label,
                    "simulated": False,
                    "evidence_count": len(resolved_evidence),
                    "attached_count": 0,
                    "evidence_attached": [],
                    "failed_attachments": missing_files,
                    "error": err_msg,
                }
                await self.log_action(
                    action="create_jira_ticket",
                    input_data={
                        "bug_title": bug_data.get("title"),
                        "project_key": jira_key,
                        "reporter": reporter,
                        "priority": priority,
                        "label": label,
                        "evidence_count": len(resolved_evidence),
                        "evidence_files": [e["file_name"] for e in resolved_evidence],
                    },
                    output_data=failed_result,
                    status="failed",
                    bug_report_id=bug_report_id,
                    project_id=project_info.get("id"),
                    module=bug_data.get("module") or "Jira Integration",
                    category="jira_sync",
                    level="error",
                    started_at=start_time,
                )
                return failed_result

        # Offline / Local simulation ONLY when live Jira credentials are NOT configured
        if not ticket_result:
            rand_id = uuid.uuid4().hex[:4].upper()
            sim_key = f"{jira_key}-{rand_id}"
            ticket_result = {
                "key": sim_key,
                "url": f"{base_url.rstrip('/')}/browse/{sim_key}" if base_url else f"https://jira.local/browse/{sim_key}",
                "status": "Backlog",
                "project_key": jira_key,
                "reporter": reporter,
                "priority": priority,
                "label": label,
                "simulated": True,
                "evidence_count": len(resolved_evidence),
                "attached_count": len(resolved_evidence),
                "evidence_attached": [e["file_name"] for e in resolved_evidence],
                "failed_attachments": missing_files,
            }

        # Update bug report record in database only after verified/local creation
        if bug_report_id and ticket_result.get("key"):
            async with async_session() as session:
                bug = await session.get(BugReport, bug_report_id)
                if bug:
                    bug.jira_ticket_id = ticket_result["key"]
                    bug.jira_ticket_url = ticket_result["url"]
                    bug.status = "reported"
                    bug.label = label
                    bug.severity = priority
                    bug.formatted_report = effective_report
                    await session.commit()

        is_simulated = bool(ticket_result.get("simulated"))
        await self.log_action(
            action="create_jira_ticket",
            input_data={
                "bug_title": bug_data.get("title"),
                "project_key": jira_key,
                "reporter": reporter,
                "priority": priority,
                "label": label,
                "evidence_count": len(resolved_evidence),
                "evidence_files": [e["file_name"] for e in resolved_evidence],
                "simulated": is_simulated,
            },
            output_data=ticket_result,
            status="success",
            bug_report_id=bug_report_id,
            project_id=project_info.get("id"),
            module=bug_data.get("module") or ("Local Simulation" if is_simulated else "Jira Integration"),
            category="local_simulation" if is_simulated else "jira_sync",
            level="info" if is_simulated else ("warning" if ticket_result.get("failed_attachments") else "change"),
            jira_key=ticket_result.get("key"),
            notify=False if is_simulated else notify_on_success,
            started_at=start_time,
        )
        return ticket_result

    async def run(self, *args, **kwargs) -> Any:
        return await self.create_jira_ticket(*args, **kwargs)
