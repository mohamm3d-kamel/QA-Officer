"""Watcher Agent - Tracks Jira updates & transitions and monitors AI agent failures."""
import asyncio
import json
import logging
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional
import httpx
from sqlalchemy import select

from agents.base import BaseAgent
from agents.detective import DetectiveAgent
from agents.coroner import CoronerAgent
from services.jira_client import JiraClient, is_real_jira_issue_key
from services.notification_service import (
    NotificationService,
    _as_bool,
    is_ai_agent_failure_event,
    is_jira_update_event,
)
from services.watcher_bus import record_system_event
from database import async_session
from models.agent_log import AgentLog
from models.bug_report import BugReport
from models.project import Project
from models.settings import Settings
from models.skill import Skill

logger = logging.getLogger(__name__)


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


class WatcherAgent(BaseAgent):
    """Sentinel observing Jira Cloud updates and AI agent failures."""

    def __init__(
        self,
        detective: DetectiveAgent,
        coroner: CoronerAgent,
        notification_service: NotificationService,
    ):
        super().__init__(
            name="Watcher",
            role="Jira Update & Agent Failure Sentinel",
            color="#6366f1",
            icon="👁️",
        )
        self.detective = detective
        self.coroner = coroner
        self.notifier = notification_service
        self._running = False
        self._task = None

    async def check_ticket_and_process(
        self,
        bug_report_id: int,
        jira_settings: Dict[str, str],
        force_status: Optional[str] = None,
        update_jira: bool = True,
        notify: bool = True,
    ) -> Dict[str, Any]:
        """Check status of a specific ticket, detect drift, and trigger retest flow if in 'In QA'."""
        start_time = datetime.utcnow()
        async with async_session() as session:
            bug = await session.get(BugReport, bug_report_id)
            if not bug:
                return {"error": "Bug report not found"}

            project = await session.get(Project, bug.project_id)
            project_info = project.to_dict() if project else {"id": bug.project_id, "name": "Default"}
            project_name = project_info.get("name")
            previous_local_status = bug.status
            current_status = force_status or bug.status

            base_url = (jira_settings.get("jira_base_url") or (project.jira_base_url if project else "") or "").strip()
            email = (jira_settings.get("jira_email") or "").strip()
            token = (jira_settings.get("jira_api_token") or "").strip()

            jira_poll_error: Optional[str] = None
            jira_status_fetched: Optional[str] = None
            if base_url and email and token and bug.jira_ticket_id and is_real_jira_issue_key(bug.jira_ticket_id):
                try:
                    jira_client = JiraClient(base_url, email, token)
                    issue = await jira_client.get_issue(bug.jira_ticket_id)
                    jira_status_fetched = issue.get("status")
                    if jira_status_fetched and force_status is None:
                        current_status = jira_status_fetched
                except Exception as e:
                    jira_poll_error = str(e)
                    logger.warning(f"Could not poll Jira issue {bug.jira_ticket_id}: {e}")

            is_in_qa = str(current_status or "").lower().replace(" ", "") in ("inqa", "in_qa")

            if jira_poll_error and force_status is None:
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
                    notify=notify,
                    started_at=start_time,
                )

            if not is_in_qa and force_status is None:
                mapped = _map_jira_status_to_local(current_status) if jira_status_fetched else bug.status
                drifted = bool(jira_status_fetched and mapped != previous_local_status and previous_local_status not in ("draft", "confirmed"))
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
                        output_data={"old_status": previous_local_status, "new_jira_status": jira_status_fetched},
                        notify=notify,
                    )
                else:
                    await self.log_action(
                        action="status_check",
                        input_data={"bug_id": bug.id, "ticket": bug.jira_ticket_id, "status": current_status},
                        output_data={"action_taken": "none", "reason": "Not in 'In QA' column"},
                        status="success",
                        bug_report_id=bug.id,
                        project_id=bug.project_id,
                        module=bug.module or "Jira Integration",
                        jira_key=bug.jira_ticket_id,
                        notify=False,
                        started_at=start_time,
                    )
                return {
                    "status": current_status,
                    "jira_key": bug.jira_ticket_id,
                    "retested": False,
                    "jira_updated": False,
                    "drifted": drifted,
                }

            # Ticket is IN QA -> notify real Jira ticket detection and launch Detective retest
            bug.status = "in_qa"
            await session.commit()

            if notify and update_jira and bug.jira_ticket_id:
                await self.notifier.notify_status_change(
                    jira_key=bug.jira_ticket_id,
                    old_status=previous_local_status,
                    new_status="In QA (Retest Started)",
                    project_name=project_name,
                    module=bug.module or "QA Verification",
                    bug_id=bug.id,
                )

            retest_result = await self.detective.retest_bug(
                bug_data=bug.to_dict(),
                project_info=project_info,
                project_id=bug.project_id,
                bug_report_id=bug.id,
            )

            is_resolved = retest_result.get("is_resolved", False)
            target_status = "Ready To Deploy" if is_resolved else "Failed QA"

            failure_evidence = []
            if not is_resolved:
                failure_evidence = await self.coroner.capture_evidence(
                    bug_data=bug.to_dict(),
                    project_info=project_info,
                    stage="retest",
                    bug_report_id=bug.id,
                    project_id=bug.project_id,
                )

            jira_updated = False
            jira_transition_error: Optional[str] = None
            if update_jira and base_url and email and token and bug.jira_ticket_id:
                try:
                    jira_client = JiraClient(base_url, email, token)
                    await jira_client.transition_issue(bug.jira_ticket_id, target_status)
                    jira_updated = True

                    if not is_resolved:
                        comment = (
                            f"❌ *QA Retest Failed*\n\n"
                            f"{retest_result.get('failure_reason', 'Issue still persists in test environment.')}"
                        )
                        await jira_client.add_comment(bug.jira_ticket_id, comment)
                        from agents.sheriff import resolve_evidence_items
                        resolved_fail_ev, _ = resolve_evidence_items(failure_evidence)
                        for ev in resolved_fail_ev:
                            try:
                                await jira_client.add_attachment(bug.jira_ticket_id, ev["file_path"], ev["file_name"])
                            except Exception as ev_err:
                                logger.warning(f"Watcher failed to attach {ev.get('file_name')} to {bug.jira_ticket_id}: {ev_err}")
                except Exception as e:
                    jira_transition_error = str(e)
                    logger.error(f"Jira transition failed in Watcher: {e}")
                    await self.log_action(
                        action="jira_transition_failed",
                        input_data={"bug_id": bug.id, "ticket": bug.jira_ticket_id, "target_status": target_status},
                        output_data={"error": jira_transition_error},
                        status="failed",
                        bug_report_id=bug.id,
                        project_id=bug.project_id,
                        module=bug.module or "Jira Integration",
                        category="jira_sync",
                        level="error",
                        jira_key=bug.jira_ticket_id,
                        notify=notify,
                    )

            bug.status = "resolved" if is_resolved else "failed"
            await session.commit()

            if notify and update_jira and bug.jira_ticket_id:
                if is_resolved:
                    await self.notifier.notify_bug_resolved(
                        bug.title,
                        bug.jira_ticket_id,
                        project_name=project_name,
                        module=bug.module or "QA Verification",
                        bug_id=bug.id,
                    )
                else:
                    await self.notifier.notify_bug_failed(
                        bug.title,
                        bug.jira_ticket_id,
                        retest_result.get("failure_reason", "Failed QA"),
                        project_name=project_name,
                        module=bug.module or "QA Verification",
                        bug_id=bug.id,
                    )

            await self.log_action(
                action="retest_workflow_executed",
                input_data={
                    "bug_id": bug.id,
                    "ticket": bug.jira_ticket_id,
                    "previous_status": current_status,
                    "update_jira": update_jira,
                },
                output_data={
                    "is_resolved": is_resolved,
                    "new_status": target_status,
                    "jira_updated": jira_updated,
                    "result": retest_result,
                },
                status="success" if is_resolved else "failed",
                bug_report_id=bug.id,
                project_id=bug.project_id,
                module=bug.module or "QA Verification",
                category="status_change",
                level="success" if is_resolved else "error",
                jira_key=bug.jira_ticket_id,
                notify=False,
                started_at=start_time,
            )

            return {
                "bug_id": bug.id,
                "ticket_key": bug.jira_ticket_id,
                "jira_key": bug.jira_ticket_id,
                "is_resolved": is_resolved,
                "new_status": target_status,
                "jira_updated": jira_updated,
                "details": retest_result,
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

    async def run_full_system_sweep(
        self,
        jira_settings: Dict[str, str],
        project_id: Optional[int] = None,
        include_app_health: bool = True,
        notify: bool = True,
    ) -> Dict[str, Any]:
        """Audit every project, module, AI agent, Jira ticket, and system log, then dispatch alerts."""
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

            last_sweep_raw = effective_settings.get("watcher_last_sweep_at") or ""
            since_dt = datetime.utcnow() - timedelta(hours=24)
            if last_sweep_raw:
                try:
                    since_dt = datetime.fromisoformat(last_sweep_raw)
                except Exception:
                    pass

            proj_stmt = select(Project).order_by(Project.id.asc())
            if project_id:
                proj_stmt = proj_stmt.where(Project.id == project_id)
            projects = (await session.execute(proj_stmt)).scalars().all()

            bug_stmt = select(BugReport).order_by(BugReport.id.desc())
            if project_id:
                bug_stmt = bug_stmt.where(BugReport.project_id == project_id)
            all_bugs = (await session.execute(bug_stmt)).scalars().all()

            skill_stmt = select(Skill)
            if project_id:
                skill_stmt = skill_stmt.where(Skill.project_id == project_id)
            all_skills = (await session.execute(skill_stmt)).scalars().all()

            log_stmt = select(AgentLog).where(AgentLog.started_at >= since_dt).order_by(AgentLog.started_at.desc())
            if project_id:
                log_stmt = log_stmt.where((AgentLog.project_id == project_id) | (AgentLog.project_id.is_(None)))
            recent_logs = (await session.execute(log_stmt)).scalars().all()

        # 1. Check active / tracked bugs + Jira status changes & In QA retests
        retested_tickets: List[Dict[str, Any]] = []
        jira_changes: List[Dict[str, Any]] = []
        for bug in all_bugs:
            should_poll = bug.status in ("reported", "in_qa", "failed", "confirmed")
            if not should_poll:
                continue
            try:
                res = await self.check_ticket_and_process(bug.id, effective_settings)
                if res.get("new_status") or res.get("is_resolved") is not None:
                    retested_tickets.append({"bug_id": bug.id, "jira_key": bug.jira_ticket_id, **res})
                elif res.get("drifted"):
                    jira_changes.append({"bug_id": bug.id, "jira_key": bug.jira_ticket_id, "status": res.get("status")})
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
                )

        # 2. Target app health checks across projects
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
                            agent_name="Watcher",
                            project_id=proj.id,
                            output_data=probe,
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
        now_utc = datetime.utcnow()

        if monitor_agents or monitor_logs:
            for lg in recent_logs:
                ag = lg.agent_name or "System"
                st = lg.status or "unknown"
                ab = agents_audit.setdefault(
                    ag,
                    {"total": 0, "success": 0, "failed": 0, "running": 0, "latest_action": lg.action, "latest_status": st},
                )
                ab["total"] += 1
                if st == "success":
                    ab["success"] += 1
                    success_count += 1
                elif st == "failed":
                    ab["failed"] += 1
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

        # 5. Live Jira board pulse if credentials configured
        jira_pulse: Dict[str, Any] = {"configured": False}
        j_base = (effective_settings.get("jira_base_url") or "").strip()
        j_email = (effective_settings.get("jira_email") or "").strip()
        j_tok = (effective_settings.get("jira_api_token") or "").strip()
        if j_base and j_email and j_tok:
            jira_pulse["configured"] = True
            try:
                client = JiraClient(j_base, j_email, j_tok)
                recent_issues = await client.search_jql("updated >= -14d ORDER BY updated DESC", max_results=15)
                in_qa_jira = [i["key"] for i in recent_issues if str(i.get("status") or "").lower().replace(" ", "") in ("inqa", "in_qa")]
                jira_pulse["recent_count"] = len(recent_issues)
                jira_pulse["in_qa_keys"] = in_qa_jira
            except Exception as j_err:
                jira_pulse["error"] = str(j_err)

        # Save sweep timestamp checkpoint
        async with async_session() as session:
            row = (
                await session.execute(select(Settings).where(Settings.key == "watcher_last_sweep_at"))
            ).scalar_one_or_none()
            now_iso = now_utc.isoformat()
            if row:
                row.value = now_iso
            else:
                session.add(Settings(key="watcher_last_sweep_at", value=now_iso))
            await session.commit()

        overall_status = "failed" if (failures_detected or unhealthy_apps) else "success"
        overall_level = "error" if (failures_detected or unhealthy_apps) else ("change" if (retested_tickets or changes_detected) else "success")

        sweep_summary = {
            "scanned_at": now_utc.isoformat(),
            "since": since_dt.isoformat(),
            "projects_count": len(projects),
            "bugs_checked": len(all_bugs),
            "retested_tickets": retested_tickets,
            "jira_changes": jira_changes,
            "modules_monitored": len(modules_audit),
            "modules_audit": modules_audit,
            "agents_monitored": len(agents_audit),
            "agents_audit": agents_audit,
            "logs_audited": len(recent_logs),
            "success_count": success_count,
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
            f"{success_count} success, {len(changes_detected)} change(s), "
            f"{len(failures_detected) + len(unhealthy_apps)} failure(s), "
            f"{len(retested_tickets)} In-QA retest(s)."
        )

        notif_res: Optional[Dict[str, Any]] = None
        if notify and self.notifier.should_notify(category="watcher_sweep", status=overall_status, level=overall_level):
            notif_res = await self.notifier.notify_event(
                title=f"System Sweep: {len(modules_audit)} Modules · {len(agents_audit)} Agents · {len(failures_detected) + len(unhealthy_apps)} Failures",
                message=summary_text,
                level=overall_level,
                category="watcher_sweep",
                status=overall_status,
                project_name=projects[0].name if (project_id and projects) else "All Projects",
                module="All Modules",
                agent_name="Watcher",
                details={
                    "failures": failures_detected[:5],
                    "changes": changes_detected[:5],
                    "retested": retested_tickets[:5],
                    "agents": {k: {"total": v["total"], "failed": v["failed"]} for k, v in agents_audit.items()},
                },
            )
            sweep_summary["notification_delivery"] = notif_res

        channels_list = ["ws"] + list((notif_res or {}).get("channels_sent") or [])
        for ch_name in ("slack", "email"):
            ch_res = (notif_res or {}).get(ch_name) or {}
            if ch_res.get("attempted") and not ch_res.get("sent"):
                channels_list.append(f"{ch_name}(failed)")
        channels_tag = ",".join(dict.fromkeys(channels_list))

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
        return await self.run_full_system_sweep(jira_settings=jira_settings, notify=True)

    async def run(self, *args, **kwargs) -> Any:
        if "bug_report_id" in kwargs or (args and isinstance(args[0], int)):
            return await self.check_ticket_and_process(*args, **kwargs)
        return await self.run_full_system_sweep(*args, **kwargs)
