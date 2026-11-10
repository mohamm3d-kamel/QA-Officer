"""Watcher Agent - Monitors Jira tickets, notices 'In QA' status, triggers Detective retest."""
import asyncio
import logging
from datetime import datetime
from typing import Dict, Any, List, Optional
from sqlalchemy import select

from agents.base import BaseAgent
from agents.detective import DetectiveAgent
from agents.coroner import CoronerAgent
from services.jira_client import JiraClient
from services.notification_service import NotificationService
from database import async_session
from models.bug_report import BugReport
from models.project import Project
import config

logger = logging.getLogger(__name__)


class WatcherAgent(BaseAgent):
    """Jira status monitor and lifecycle watcher agent."""

    def __init__(
        self,
        detective: DetectiveAgent,
        coroner: CoronerAgent,
        notification_service: NotificationService,
    ):
        super().__init__(
            name="Watcher",
            role="Jira Sentinel & Retest Trigger",
            color="#6366f1",
            icon="👁️"
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
    ) -> Dict[str, Any]:
        """Check status of a specific ticket and trigger retest flow if in 'In QA'."""
        start_time = datetime.utcnow()
        async with async_session() as session:
            bug = await session.get(BugReport, bug_report_id)
            if not bug:
                return {"error": "Bug report not found"}

            project = await session.get(Project, bug.project_id)
            project_info = project.to_dict() if project else {"id": bug.project_id, "name": "Default"}

            current_status = force_status or bug.status

            # If Jira credentials present, query Jira directly
            base_url = jira_settings.get("jira_base_url") or (project.jira_base_url if project else "")
            email = jira_settings.get("jira_email", "")
            token = jira_settings.get("jira_api_token", "")

            if base_url and email and token and bug.jira_ticket_id:
                try:
                    jira_client = JiraClient(base_url, email, token)
                    issue = await jira_client.get_issue(bug.jira_ticket_id)
                    current_status = issue.get("status", current_status)
                except Exception as e:
                    logger.warning(f"Could not poll Jira issue {bug.jira_ticket_id}: {e}")
                    await self.log_action(
                        action="jira_poll_error",
                        input_data={"bug_id": bug.id, "ticket": bug.jira_ticket_id},
                        output_data={"error": str(e)},
                        status="failed",
                        bug_report_id=bug.id,
                        project_id=bug.project_id,
                        started_at=start_time,
                    )

            # Normalization of status matching "In QA"
            is_in_qa = current_status.lower().replace(" ", "") in ["inqa", "in_qa"]

            if not is_in_qa and force_status is None:
                return {"status": current_status, "retested": False}

            # Ticket is IN QA -> Report Detective to retest bug
            bug.status = "in_qa"
            await session.commit()

            browsers = config.get_enabled_browsers(jira_settings)
            headless = str(jira_settings.get("browser_headless", "true")).lower() == "true"

            retest_result = await self.detective.retest_bug(
                bug_data=bug.to_dict(),
                project_info=project_info,
                project_id=bug.project_id,
                bug_report_id=bug.id,
                browsers=browsers,
                headless=headless,
            )

            is_resolved = retest_result.get("is_resolved", False)
            target_status = "Ready To Deploy" if is_resolved else "Failed QA"

            # Always capture failure evidence when QA fails (both local and Jira mode)
            failure_evidence = []
            if not is_resolved:
                failure_evidence = await self.coroner.capture_evidence(
                    bug_data=bug.to_dict(),
                    project_info=project_info,
                    stage="retest",
                    bug_report_id=bug.id,
                    project_id=bug.project_id,
                    browsers=browsers,
                    headless=headless,
                )

            # Transition in Jira if active client
            if base_url and email and token and bug.jira_ticket_id:
                try:
                    jira_client = JiraClient(base_url, email, token)
                    await jira_client.transition_issue(bug.jira_ticket_id, target_status)

                    if not is_resolved:
                        comment = f"❌ *QA Retest Failed*\n\n{retest_result.get('failure_reason', 'Issue still persists in test environment.')}"
                        await jira_client.add_comment(bug.jira_ticket_id, comment)
                        for ev in failure_evidence:
                            if ev.get("file_path"):
                                await jira_client.add_attachment(bug.jira_ticket_id, ev["file_path"], ev["file_name"])
                except Exception as e:
                    logger.error(f"Jira transition failed in Watcher: {e}")

            # Update DB status
            bug.status = "resolved" if is_resolved else "failed"
            await session.commit()

            # Notifications
            if is_resolved:
                await self.notifier.notify_bug_resolved(bug.title, bug.jira_ticket_id or f"BUG-{bug.id}")
            else:
                await self.notifier.notify_bug_failed(
                    bug.title, bug.jira_ticket_id or f"BUG-{bug.id}", retest_result.get("failure_reason", "Failed QA")
                )

            await self.log_action(
                action="retest_workflow_executed",
                input_data={"bug_id": bug.id, "ticket": bug.jira_ticket_id, "previous_status": "In QA"},
                output_data={"is_resolved": is_resolved, "new_status": target_status, "result": retest_result},
                status="degraded" if retest_result.get("llm_degraded") else "success",
                bug_report_id=bug.id,
                project_id=bug.project_id,
                started_at=start_time,
            )

            return {
                "bug_id": bug.id,
                "ticket_key": bug.jira_ticket_id,
                "is_resolved": is_resolved,
                "new_status": target_status,
                "details": retest_result,
            }

    async def poll_all_active_projects(self, jira_settings: Dict[str, str]):
        """Poll all reported bugs in projects to inspect for 'In QA' status changes."""
        async with async_session() as session:
            result = await session.execute(
                select(BugReport).where(BugReport.status.in_(["reported", "in_qa"]))
            )
            bugs = result.scalars().all()

        for bug in bugs:
            try:
                await self.check_ticket_and_process(bug.id, jira_settings)
            except Exception as e:
                logger.error(f"Watcher error processing bug #{bug.id}: {e}")

    async def run(self, *args, **kwargs) -> Any:
        return await self.check_ticket_and_process(*args, **kwargs)

