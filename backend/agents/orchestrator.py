"""Agent Pipeline Orchestrator - Coordinates all AI Agents through the QA lifecycle."""
import asyncio
import logging
from datetime import datetime
from typing import Dict, Any, List, Optional, Callable, Awaitable
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from agents.desk_officer import DeskOfficerAgent
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
    """Coordinates Desk Officer, Detective, Coroner, Judge, Reporter, Sheriff, Watcher, and Sanity Testers."""

    def __init__(self, llm_router: LLMRouter, jira_settings: Dict[str, str], default_reporter: str = "Mohamed Kamel"):
        self.llm = llm_router
        self.jira_settings = jira_settings
        self.default_reporter = default_reporter

        self.desk_officer = DeskOfficerAgent(self.llm)
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

    async def _make_notifier(
        self,
        broadcast_callback: Optional[Callable[[Dict[str, Any]], Awaitable[None]]],
    ):
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

        return notify

    async def process_bug_pipeline(
        self,
        bug_input: str,
        project_id: int,
        broadcast_callback: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None,
        include_sheriff: bool = False,
    ) -> Dict[str, Any]:
        """Execute the main bug reporting pipeline: Detective -> Coroner -> Judge -> Reporter.

        Sheriff is NOT invoked automatically unless ``include_sheriff=True`` (explicitly requested).
        """
        notify = await self._make_notifier(broadcast_callback)

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
            b.steps_to_reproduce = (
                "\n".join(detective_result.get("steps_to_reproduce", []))
                if isinstance(detective_result.get("steps_to_reproduce"), list)
                else str(detective_result.get("steps_to_reproduce"))
            )
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
        async with async_session() as session:
            b = await session.get(BugReport, bug_id)
            b.formatted_report = formatted_report
            await session.commit()
        await notify("reporter", "success", "Reporter completed formatted bug report", {"preview": formatted_report[:200]})

        ticket_result = None
        final_status = "confirmed"

        # 5. SHERIFF (Only raises ticket in Jira 'Backlog' when explicitly requested)
        if include_sheriff:
            ticket_result = await self.raise_jira_ticket(
                bug_report_id=bug_id,
                broadcast_callback=broadcast_callback,
            )
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
            "root_cause": judge_verdict.get("final_root_cause"),
            "judge_verdict": judge_verdict,
            "formatted_report": formatted_report,
            "evidence": evidence_list,
            "jira_ticket": ticket_result,
            "status": final_status,
        }

    async def raise_jira_ticket(
        self,
        bug_report_id: int,
        broadcast_callback: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None,
    ) -> Dict[str, Any]:
        """Explicitly invoke Sheriff to create a Jira ticket in 'Backlog' for an existing bug report."""
        notify = await self._make_notifier(broadcast_callback)

        async with async_session() as session:
            res = await session.execute(
                select(BugReport)
                .options(selectinload(BugReport.evidence))
                .where(BugReport.id == bug_report_id)
            )
            bug = res.scalar_one_or_none()
            if not bug:
                raise ValueError(f"Bug Report #{bug_report_id} not found")

            if bug.jira_ticket_id:
                return {
                    "key": bug.jira_ticket_id,
                    "url": bug.jira_ticket_url,
                    "status": "Backlog",
                    "reporter": self.default_reporter,
                    "priority": bug.severity or "medium",
                    "label": bug.label or "Front-End",
                    "already_raised": True,
                }

            project = await session.get(Project, bug.project_id)
            project_info = project.to_dict() if project else {"id": bug.project_id, "jira_project_key": "QA", "name": "Default"}

            bug_data = {
                "title": bug.title,
                "description": bug.description,
                "root_cause": bug.root_cause,
                "steps_to_reproduce": bug.steps_to_reproduce.splitlines() if bug.steps_to_reproduce else [],
                "severity": bug.severity or "medium",
                "label": bug.label or "Front-End",
                "module": bug.module or "General",
            }
            evidence_list = [
                {
                    "type": ev.type,
                    "file_path": ev.file_path,
                    "file_name": ev.file_name,
                    "stage": ev.stage,
                    "description": ev.description,
                }
                for ev in (bug.evidence or [])
            ]
            formatted_report = bug.formatted_report or self.reporter._fallback_report(
                bug_data=bug_data,
                judge_verdict={
                    "final_root_cause": bug.root_cause or bug.description or "Pending confirmation",
                    "judge_notes": "Submitted via Sheriff on tester request.",
                    "severity_adjustment": bug.severity or "medium",
                },
                project_info=project_info,
            )

        await notify(
            "sheriff",
            "running",
            f"Sheriff is creating Jira ticket in 'Backlog' with label '{bug_data['label']}' assigned to {self.default_reporter}...",
        )
        ticket_result = await self.sheriff.create_jira_ticket(
            bug_data=bug_data,
            formatted_report=formatted_report,
            evidence_list=evidence_list,
            project_info=project_info,
            jira_settings=self.jira_settings,
            bug_report_id=bug_report_id,
            reporter_name=self.default_reporter,
        )
        await notify(
            "sheriff",
            "success",
            f"Sheriff created ticket: {ticket_result['key']} in 'Backlog'",
            ticket_result,
        )

        await self.notifier.notify_bug_created(
            bug_title=bug_data["title"],
            jira_key=ticket_result["key"],
            jira_url=ticket_result["url"],
        )
        return ticket_result

    async def _resolve_target_bug(
        self,
        project_id: int,
        bug_report_id: Optional[int],
        bug_input: str,
        create_if_missing: bool = False,
        prefer_unraised: bool = False,
    ) -> Optional[BugReport]:
        """Find a referenced bug report, latest bug report for the project, or optionally create a draft."""
        async with async_session() as session:
            if bug_report_id:
                res = await session.execute(
                    select(BugReport)
                    .options(selectinload(BugReport.evidence))
                    .where(BugReport.id == bug_report_id, BugReport.project_id == project_id)
                )
                return res.scalar_one_or_none()

            if prefer_unraised:
                res = await session.execute(
                    select(BugReport)
                    .options(selectinload(BugReport.evidence))
                    .where(BugReport.project_id == project_id, BugReport.jira_ticket_id.is_(None))
                    .order_by(BugReport.created_at.desc())
                )
                unraised = res.scalars().first()
                if unraised:
                    return unraised

            res = await session.execute(
                select(BugReport)
                .options(selectinload(BugReport.evidence))
                .where(BugReport.project_id == project_id)
                .order_by(BugReport.created_at.desc())
            )
            latest = res.scalars().first()
            if latest:
                return latest

            if create_if_missing and bug_input.strip():
                bug = BugReport(
                    project_id=project_id,
                    title=f"Bug: {bug_input.strip()[:80]}",
                    description=bug_input.strip(),
                    status="draft",
                )
                session.add(bug)
                await session.commit()
                await session.refresh(bug)
                return bug

            return None

    async def dispatch_agent(
        self,
        target_agent: str,
        project_id: int,
        bug_input: str = "",
        bug_report_id: Optional[int] = None,
        broadcast_callback: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None,
    ) -> Dict[str, Any]:
        """Assign and execute a single specialized built-in agent on behalf of Desk Officer."""
        notify = await self._make_notifier(broadcast_callback)
        agent_key = target_agent.lower().strip()

        async with async_session() as session:
            project = await session.get(Project, project_id)
            if not project:
                raise ValueError(f"Project #{project_id} not found")
            project_info = project.to_dict()

        skills_context, report_format = await self.get_project_skills_context(project_id)

        if agent_key == "detective":
            if bug_report_id:
                bug = await self._resolve_target_bug(project_id, bug_report_id, bug_input, create_if_missing=False)
                if not bug:
                    return {"agent": "Detective", "status": "not_found", "message": f"Bug #{bug_report_id} was not found in this project."}
                bug_id = bug.id
                effective_input = bug_input or bug.description or bug.title
            else:
                async with async_session() as session:
                    bug = BugReport(
                        project_id=project_id,
                        title="Analyzing bug...",
                        description=bug_input or "Investigating reported bug",
                        status="draft",
                    )
                    session.add(bug)
                    await session.commit()
                    await session.refresh(bug)
                    bug_id = bug.id
                effective_input = bug_input

            await notify("detective", "running", "Detective is tracing the bug and investigating root cause...")
            detective_result = await self.detective.trace_bug(
                bug_input=effective_input,
                skills_context=skills_context,
                project_info=project_info,
                project_id=project_id,
                bug_report_id=bug_id,
            )
            async with async_session() as session:
                b = await session.get(BugReport, bug_id)
                b.title = detective_result.get("title", b.title)
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
            await notify("detective", "success", f"Detective identified root cause for: {detective_result.get('title')}", detective_result)
            return {"agent": "Detective", "status": "success", "bug_id": bug_id, "result": detective_result}

        if agent_key == "coroner":
            bug = await self._resolve_target_bug(project_id, bug_report_id, bug_input, create_if_missing=True)
            if not bug:
                return {"agent": "Coroner", "status": "missing_context", "message": "Please describe the bug or reference a bug # first so Coroner knows what to capture."}
            bug_data = {
                "title": bug.title,
                "description": bug.description or bug_input,
                "root_cause": bug.root_cause or "Under investigation",
                "steps_to_reproduce": bug.steps_to_reproduce.splitlines() if bug.steps_to_reproduce else [bug_input or bug.title],
                "severity": bug.severity or "medium",
                "label": bug.label or "Front-End",
                "module": bug.module or "General",
                "browser_actions": [],
            }
            await notify("coroner", "running", f"Coroner is capturing screenshot and video evidence for Bug #{bug.id}...")
            evidence_list = await self.coroner.capture_evidence(
                bug_data=bug_data,
                project_info=project_info,
                stage="detection",
                bug_report_id=bug.id,
                project_id=project_id,
            )
            await notify("coroner", "success", f"Coroner captured {len(evidence_list)} evidence files", evidence_list)
            return {"agent": "Coroner", "status": "success", "bug_id": bug.id, "evidence": evidence_list}

        if agent_key == "judge":
            bug = await self._resolve_target_bug(project_id, bug_report_id, bug_input, create_if_missing=True)
            if not bug:
                return {"agent": "Judge", "status": "missing_context", "message": "Please describe a bug or reference a bug # for Judge to verify."}
            bug_data = {
                "title": bug.title,
                "description": bug.description or bug_input,
                "root_cause": bug.root_cause or bug_input or "Initial analysis pending",
                "steps_to_reproduce": bug.steps_to_reproduce or bug_input,
                "severity": bug.severity or "medium",
                "label": bug.label or "Front-End",
            }
            await notify("judge", "running", f"Judge is independently verifying Bug #{bug.id}...")
            judge_verdict = await self.judge.verify_root_cause(
                bug_data=bug_data,
                skills_context=skills_context,
                project_info=project_info,
                project_id=project_id,
                bug_report_id=bug.id,
            )
            async with async_session() as session:
                b = await session.get(BugReport, bug.id)
                b.root_cause = judge_verdict.get("final_root_cause", b.root_cause)
                b.status = "confirmed"
                await session.commit()
            await notify("judge", "success", f"Judge Verdict: {judge_verdict.get('verdict')} - {judge_verdict.get('final_root_cause', '')[:80]}", judge_verdict)
            return {"agent": "Judge", "status": "success", "bug_id": bug.id, "verdict": judge_verdict}

        if agent_key == "reporter":
            bug = await self._resolve_target_bug(project_id, bug_report_id, bug_input, create_if_missing=True)
            if not bug:
                return {"agent": "Reporter", "status": "missing_context", "message": "Please describe the bug or reference a bug # so Reporter can draft the report."}
            bug_data = {
                "title": bug.title,
                "description": bug.description or bug_input,
                "steps_to_reproduce": bug.steps_to_reproduce.splitlines() if bug.steps_to_reproduce else [],
                "severity": bug.severity or "medium",
                "label": bug.label or "Front-End",
                "module": bug.module or "General",
            }
            judge_verdict = {
                "final_root_cause": bug.root_cause or bug.description or "Confirmed defect",
                "judge_notes": "Authored by Reporter via Desk Officer assignment.",
                "severity_adjustment": bug.severity or "medium",
            }
            await notify("reporter", "running", f"Reporter is authoring the bug report for Bug #{bug.id}...")
            formatted_report = await self.reporter.generate_report(
                bug_data=bug_data,
                judge_verdict=judge_verdict,
                report_format_template=report_format,
                project_info=project_info,
                project_id=project_id,
                bug_report_id=bug.id,
            )
            async with async_session() as session:
                b = await session.get(BugReport, bug.id)
                b.formatted_report = formatted_report
                await session.commit()
            await notify("reporter", "success", "Reporter completed formatted bug report", {"preview": formatted_report[:200]})
            return {"agent": "Reporter", "status": "success", "bug_id": bug.id, "formatted_report": formatted_report}

        if agent_key == "sheriff":
            bug = await self._resolve_target_bug(
                project_id, bug_report_id, bug_input, create_if_missing=False, prefer_unraised=True
            )
            if not bug:
                return {
                    "agent": "Sheriff",
                    "status": "missing_context",
                    "message": "There is no confirmed bug report ready to raise yet. Say **\"Create a bug report\"** first so the main workflow can investigate, capture evidence, and draft the report — then Sheriff will raise it to Jira **Backlog**.",
                }
            ticket_result = await self.raise_jira_ticket(bug_report_id=bug.id, broadcast_callback=broadcast_callback)
            return {"agent": "Sheriff", "status": "success", "bug_id": bug.id, "jira_ticket": ticket_result}

        if agent_key == "watcher":
            bug = await self._resolve_target_bug(project_id, bug_report_id, bug_input, create_if_missing=False)
            if not bug:
                return {"agent": "Watcher", "status": "missing_context", "message": "No tracked bug report found to retest yet."}
            result = await self.retest_and_sanity_pipeline(bug_report_id=bug.id, broadcast_callback=broadcast_callback)
            return {"agent": "Watcher", "status": "success", "bug_id": bug.id, "result": result}

        if agent_key == "sanity":
            bug = await self._resolve_target_bug(project_id, bug_report_id, bug_input, create_if_missing=False)
            if not bug:
                return {"agent": "Sanity Testers", "status": "missing_context", "message": "No tracked bug report found to run sanity regression checks against."}
            await notify("sanity", "running", f"Sanity Testers checking impacted modules around Bug #{bug.id}...")
            sanity_result = await self.sanity_tester.run_sanity_checks(
                resolved_bug=bug.to_dict(),
                skills_context=skills_context,
                project_info=project_info,
                project_id=project_id,
                bug_report_id=bug.id,
            )
            await notify("sanity", "success", f"Sanity Testing complete: {sanity_result.get('summary')}", sanity_result)
            return {"agent": "Sanity Testers", "status": "success", "bug_id": bug.id, "sanity": sanity_result}

        if agent_key == "auto_test":
            await notify("detective", "running", "Detective is running Autonomous Bug Discovery across project skills...")
            discovered = await self.detective.autonomous_discover(
                skills_context=skills_context,
                project_info=project_info,
                scope="all",
            )
            reports = []
            for item in discovered[:3]:
                res = await self.process_bug_pipeline(
                    bug_input=f"{item.get('title')}: {item.get('description')}\nRoot Cause: {item.get('root_cause')}",
                    project_id=project_id,
                    broadcast_callback=broadcast_callback,
                    include_sheriff=False,
                )
                reports.append(res)
            return {
                "agent": "Auto-Test",
                "status": "success",
                "discovered_count": len(discovered),
                "processed_count": len(reports),
                "reports": reports,
            }

        return {"agent": target_agent, "status": "unknown", "message": f"Unknown agent '{target_agent}'."}

    async def retest_and_sanity_pipeline(
        self,
        bug_report_id: int,
        broadcast_callback: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None,
    ) -> Dict[str, Any]:
        """Triggered when ticket moves to 'In QA': Retests bug, transitions, and runs Sanity Testers."""
        notify = await self._make_notifier(broadcast_callback)

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
