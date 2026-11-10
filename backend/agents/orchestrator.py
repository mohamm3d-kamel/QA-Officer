"""Agent Pipeline Orchestrator - Coordinates all AI Agents through the QA lifecycle."""
import asyncio
import logging
from datetime import datetime
from typing import Dict, Any, List, Optional, Callable, Awaitable
from sqlalchemy import select

from agents.detective import DetectiveAgent
from agents.coroner import CoronerAgent
from agents.judge import JudgeAgent
from agents.reporter import ReporterAgent
from agents.sheriff import SheriffAgent
from agents.watcher import WatcherAgent
from agents.sanity_tester import SanityTesterAgent
from services.llm_router import LLMRouter
from services.notification_service import NotificationService
from database import async_session
from models.bug_report import BugReport
from models.skill import Skill
from models.project import Project

logger = logging.getLogger(__name__)


class AgentOrchestrator:
    """Coordinates Detective, Coroner, Judge, Reporter, Sheriff, Watcher, and Sanity Testers."""

    def __init__(self, llm_router: LLMRouter, jira_settings: Dict[str, str], default_reporter: str = "Mohamed Kamel"):
        self.llm = llm_router
        self.jira_settings = jira_settings
        self.default_reporter = default_reporter

        self.detective = DetectiveAgent(self.llm)
        self.coroner = CoronerAgent()
        self.judge = JudgeAgent(self.llm)
        self.reporter = ReporterAgent(self.llm)
        self.sheriff = SheriffAgent(default_reporter=default_reporter)
        self.notifier = NotificationService(
            email_enabled=jira_settings.get("notification_email_enabled") == "true",
            email_address=jira_settings.get("notification_email", ""),
            slack_enabled=jira_settings.get("notification_slack_enabled") == "true",
            slack_webhook=jira_settings.get("notification_slack_webhook", ""),
        )
        self.watcher = WatcherAgent(self.detective, self.coroner, self.notifier)
        self.sanity_tester = SanityTesterAgent(self.llm)

    async def get_project_skills_context(self, project_id: int) -> tuple[str, Optional[str]]:
        """Retrieve stored skills/requirements and report format for a project."""
        async with async_session() as session:
            result = await session.execute(
                select(Skill).where(Skill.project_id == project_id)
            )
            skills = result.scalars().all()

        context_lines = []
        report_format = None

        for s in skills:
            if s.type == "report_format":
                report_format = s.content
            context_lines.append(f"[{s.type.upper()}] {s.title}:\n{s.content}\n")

        return "\n".join(context_lines), report_format

    async def process_bug_pipeline(
        self,
        bug_input: str,
        project_id: int,
        broadcast_callback: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None,
    ) -> Dict[str, Any]:
        """Execute the full 6-agent initial pipeline: Detective -> Coroner -> Judge -> Reporter -> Sheriff."""
        async def notify(agent: str, status: str, message: str, data: Any = None):
            if broadcast_callback:
                try:
                    await broadcast_callback({
                        "type": "agent_status",
                        "agent": agent,
                        "status": status,
                        "message": message,
                        "data": data,
                        "timestamp": datetime.utcnow().isoformat(),
                    })
                except Exception as e:
                    logger.warning(f"Broadcast error: {e}")

        # Fetch Project
        async with async_session() as session:
            project = await session.get(Project, project_id)
            if not project:
                raise ValueError(f"Project #{project_id} not found")
            project_info = project.to_dict()

        skills_context, report_format = await self.get_project_skills_context(project_id)

        # Create Draft Bug Report record in DB
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

        # 1. DETECTIVE (Pass 1)
        await notify("detective", "running", "Detective is tracing the bug and investigating root cause...")
        detective_result = await self.detective.trace_bug(
            bug_input=bug_input,
            skills_context=skills_context,
            project_info=project_info,
            project_id=project_id,
            bug_report_id=bug_id,
        )
        await notify("detective", "success", f"Detective identified root cause for: {detective_result.get('title')}", detective_result)

        # Update bug record with detective findings
        async with async_session() as session:
            b = await session.get(BugReport, bug_id)
            b.title = detective_result.get("title", "Reported Defect")
            b.root_cause = detective_result.get("root_cause")
            b.steps_to_reproduce = "\n".join(detective_result.get("steps_to_reproduce", [])) if isinstance(detective_result.get("steps_to_reproduce"), list) else str(detective_result.get("steps_to_reproduce"))
            b.severity = detective_result.get("severity", "medium")
            b.label = detective_result.get("label", "Front-End")
            b.module = detective_result.get("module", "General")
            await session.commit()

        # 2. CORONER (Pass 2 Evidence Capture)
        await notify("coroner", "running", "Coroner is capturing video and screenshot evidence during reproduction pass...")
        evidence_list = await self.coroner.capture_evidence(
            bug_data=detective_result,
            project_info=project_info,
            stage="detection",
            bug_report_id=bug_id,
            project_id=project_id,
        )
        await notify("coroner", "success", f"Coroner captured {len(evidence_list)} evidence files", evidence_list)

        # 3. JUDGE (Independent verification of root cause)
        await notify("judge", "running", "Judge is independently verifying the Detective root cause...")
        judge_verdict = await self.judge.verify_root_cause(
            bug_data=detective_result,
            skills_context=skills_context,
            project_info=project_info,
            project_id=project_id,
            bug_report_id=bug_id,
        )
        await notify("judge", "success", f"Judge Verdict: {judge_verdict.get('verdict')} - {judge_verdict.get('final_root_cause')[:80]}...", judge_verdict)

        # Update root cause with Judge's final decision
        async with async_session() as session:
            b = await session.get(BugReport, bug_id)
            b.root_cause = judge_verdict.get("final_root_cause")
            b.status = "confirmed"
            await session.commit()

        # 4. REPORTER (Writes report adhering to Tester's format)
        await notify("reporter", "running", "Reporter is authoring the final bug report matching your custom template...")
        formatted_report = await self.reporter.generate_report(
            bug_data=detective_result,
            judge_verdict=judge_verdict,
            report_format_template=report_format,
            project_info=project_info,
            project_id=project_id,
            bug_report_id=bug_id,
        )
        await notify("reporter", "success", "Reporter completed formatted bug report", {"preview": formatted_report[:200]})

        # 5. SHERIFF (Creates ticket in Jira "To Do" with evidence & reporter)
        await notify("sheriff", "running", f"Sheriff is creating Jira ticket with label '{detective_result.get('label')}' assigned to {self.default_reporter}...")
        ticket_result = await self.sheriff.create_jira_ticket(
            bug_data=detective_result,
            formatted_report=formatted_report,
            evidence_list=evidence_list,
            project_info=project_info,
            jira_settings=self.jira_settings,
            bug_report_id=bug_id,
            reporter_name=self.default_reporter,
        )
        await notify("sheriff", "success", f"Sheriff created ticket: {ticket_result['key']} in 'To Do'", ticket_result)

        # Notify via email / Slack if configured
        await self.notifier.notify_bug_created(
            bug_title=detective_result.get("title"),
            jira_key=ticket_result["key"],
            jira_url=ticket_result["url"],
        )

        return {
            "bug_id": bug_id,
            "title": detective_result.get("title"),
            "root_cause": judge_verdict.get("final_root_cause"),
            "judge_verdict": judge_verdict,
            "formatted_report": formatted_report,
            "evidence": evidence_list,
            "jira_ticket": ticket_result,
            "status": "reported",
        }

    async def retest_and_sanity_pipeline(
        self,
        bug_report_id: int,
        broadcast_callback: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None,
    ) -> Dict[str, Any]:
        """Triggered when ticket moves to 'In QA': Retests bug, transitions, and runs Sanity Testers."""
        async def notify(agent: str, status: str, message: str, data: Any = None):
            if broadcast_callback:
                try:
                    await broadcast_callback({
                        "type": "agent_status",
                        "agent": agent,
                        "status": status,
                        "message": message,
                        "data": data,
                        "timestamp": datetime.utcnow().isoformat(),
                    })
                except Exception as e:
                    logger.warning(f"Broadcast error: {e}")

        # Watcher & Detective retest
        await notify("watcher", "running", "Watcher detected ticket moved to 'In QA'. Alerting Detective to retest...")
        retest_outcome = await self.watcher.check_ticket_and_process(
            bug_report_id=bug_report_id,
            jira_settings=self.jira_settings,
            force_status="In QA",
        )

        is_resolved = retest_outcome.get("is_resolved", False)
        status_name = retest_outcome.get("new_status", "Failed QA")

        if is_resolved:
            await notify("watcher", "success", f"Bug verified RESOLVED! Moved to '{status_name}'", retest_outcome)

            # Trigger SANITY TESTERS for related areas
            await notify("sanity", "running", "Sanity Testers now testing related and affected parts to ensure no regression...")

            async with async_session() as session:
                bug = await session.get(BugReport, bug_report_id)
                bug_dict = bug.to_dict() if bug else {}
                project = await session.get(Project, bug.project_id) if bug else None
                project_info = project.to_dict() if project else {}

            skills_context, _ = await self.get_project_skills_context(bug.project_id)
            sanity_result = await self.sanity_tester.run_sanity_checks(
                resolved_bug=bug_dict,
                skills_context=skills_context,
                project_info=project_info,
                project_id=bug.project_id,
                bug_report_id=bug_report_id,
            )

            await notify("sanity", "success", f"Sanity Testing complete: {sanity_result.get('summary')}", sanity_result)
            return {
                "retest": retest_outcome,
                "sanity": sanity_result,
            }
        else:
            await notify("watcher", "failed", f"Bug verification FAILED! Moved to '{status_name}' with failure evidence.", retest_outcome)
            return {
                "retest": retest_outcome,
                "sanity": None,
            }

