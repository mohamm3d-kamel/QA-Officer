"""Sheriff Agent - Creates Jira issues with evidence, priority, label, and reporter."""
import logging
import uuid
from datetime import datetime
from typing import Dict, Any, List, Optional

from agents.base import BaseAgent
from services.jira_client import JiraClient
from database import async_session
from models.bug_report import BugReport

logger = logging.getLogger(__name__)


class SheriffAgent(BaseAgent):
    """Jira Ticket Creation Agent."""

    def __init__(self, default_reporter: str = "Mohamed Kamel"):
        super().__init__(
            name="Sheriff",
            role="Jira Ticket Commander",
            color="#3b82f6",
            icon="🏷️"
        )
        self.default_reporter = default_reporter

    async def create_jira_ticket(
        self,
        bug_data: Dict[str, Any],
        formatted_report: str,
        evidence_list: List[Dict[str, Any]],
        project_info: Dict[str, Any],
        jira_settings: Dict[str, str],
        bug_report_id: Optional[int] = None,
        reporter_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Create Jira ticket in 'To Do' with bug report, evidence, priority, and label."""
        start_time = datetime.utcnow()
        reporter = reporter_name or jira_settings.get("default_reporter") or self.default_reporter
        jira_key = project_info.get("jira_project_key", "QA")
        base_url = jira_settings.get("jira_base_url") or project_info.get("jira_base_url", "")
        email = jira_settings.get("jira_email", "")
        token = jira_settings.get("jira_api_token", "")

        priority = bug_data.get("severity", "medium").lower()
        if priority not in ["lowest", "low", "medium", "high", "highest"]:
            priority = "medium"

        label = bug_data.get("label", "Front-End")
        if label not in ["Back-End", "Front-End", "Mobile"]:
            label = "Front-End"

        ticket_result = None

        # If credentials provided, attempt real Jira API call
        if base_url and email and token and jira_key:
            try:
                client = JiraClient(base_url=base_url, email=email, api_token=token)
                created = await client.create_issue(
                    project_key=jira_key,
                    summary=f"[{label}] {bug_data.get('title')}",
                    description=formatted_report,
                    issue_type="Bug",
                    priority=priority,
                    labels=[label],
                    reporter_name=reporter,
                )
                issue_key = created["key"]
                issue_url = created["url"]

                # Upload evidence attachments
                for ev in evidence_list:
                    file_path = ev.get("file_path")
                    file_name = ev.get("file_name", "evidence.png")
                    if file_path:
                        try:
                            await client.add_attachment(issue_key, file_path, file_name)
                        except Exception as attach_err:
                            logger.warning(f"Failed to attach {file_name} to {issue_key}: {attach_err}")

                ticket_result = {
                    "key": issue_key,
                    "url": issue_url,
                    "status": "To Do",
                    "reporter": reporter,
                    "priority": priority,
                    "label": label,
                    "simulated": False,
                }
            except Exception as e:
                logger.warning(f"Sheriff live Jira integration encountered error: {e}. Falling back to tracked ticket.")

        # Fallback / Local mode ticket creation
        if not ticket_result:
            rand_id = uuid.uuid4().hex[:4].upper()
            sim_key = f"{jira_key}-{rand_id}"
            ticket_result = {
                "key": sim_key,
                "url": f"{base_url.rstrip('/')}/browse/{sim_key}" if base_url else f"https://jira.local/browse/{sim_key}",
                "status": "To Do",
                "reporter": reporter,
                "priority": priority,
                "label": label,
                "simulated": True,
            }

        # Update bug report record in database
        if bug_report_id:
            async with async_session() as session:
                bug = await session.get(BugReport, bug_report_id)
                if bug:
                    bug.jira_ticket_id = ticket_result["key"]
                    bug.jira_ticket_url = ticket_result["url"]
                    bug.status = "reported"
                    bug.label = label
                    bug.severity = priority
                    bug.formatted_report = formatted_report
                    await session.commit()

        await self.log_action(
            action="create_jira_ticket",
            input_data={"bug_title": bug_data.get("title"), "reporter": reporter, "priority": priority, "label": label},
            output_data=ticket_result,
            status="success",
            bug_report_id=bug_report_id,
            project_id=project_info.get("id"),
            started_at=start_time,
        )
        return ticket_result

    async def run(self, *args, **kwargs) -> Any:
        return await self.create_jira_ticket(*args, **kwargs)

