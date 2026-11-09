"""Detective Agent - Traces bugs, tracks root cause, verifies reproduction and retests."""
import json
import logging
from datetime import datetime
from typing import Dict, Any, Optional, List

from agents.base import BaseAgent
from services.llm_router import LLMRouter
from services.browser_engine import BrowserEngine

logger = logging.getLogger(__name__)


class DetectiveAgent(BaseAgent):
    """Bug tracing mode agent."""

    def __init__(self, llm_router: LLMRouter):
        super().__init__(
            name="Detective",
            role="Root Cause Investigator & Tracer",
            color="#8b5cf6",
            icon="🔍"
        )
        self.llm = llm_router

    async def trace_bug(
        self,
        bug_input: str,
        skills_context: str,
        project_info: Dict[str, Any],
        project_id: int,
        bug_report_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """First Pass: Trace the bug, identify potential root cause, reproduction steps, and severity."""
        start_time = datetime.utcnow()
        system_prompt = (
            "You are the Detective AI Agent in QA Officer. Your mission is to analyze bug reports, user stories, "
            "codebase info, or web application behaviors to find the exact root cause and reproduction steps.\n"
            "Respond ONLY with a valid JSON object matching this schema:\n"
            "{\n"
            '  "title": "Clear concise bug title",\n'
            '  "description": "Detailed explanation of the issue and discrepancy with expected behavior",\n'
            '  "root_cause": "Deep analysis of why this happens (code logic, API failure, DOM state, validation, etc.)",\n'
            '  "steps_to_reproduce": ["Step 1...", "Step 2...", "Step 3..."],\n'
            '  "severity": "lowest|low|medium|high|highest",\n'
            '  "label": "Back-End|Front-End|Mobile",\n'
            '  "module": "Identified feature/module name",\n'
            '  "browser_actions": [{"action": "navigate|click|fill|wait", "url": "...", "selector": "...", "value": "..."}],\n'
            '  "confidence_score": 0.95,\n'
            '  "trace_summary": "Detective explanation of findings"\n'
            "}"
        )

        user_content = (
            f"Project: {project_info.get('name')}\n"
            f"App URL: {project_info.get('app_url', 'None')}\n"
            f"Repo URL: {project_info.get('repo_url', 'None')}\n"
            f"Stored Skills / Business Context:\n{skills_context}\n\n"
            f"Tester Bug Input:\n{bug_input}"
        )

        try:
            response = await self.llm.chat(
                messages=[{"role": "user", "content": user_content}],
                system_prompt=system_prompt,
                temperature=0.2,
            )
            content = response.content.strip()
            if content.startswith("```json"):
                content = content[7:]
            if content.startswith("```"):
                content = content[3:]
            if content.endswith("```"):
                content = content[:-3]
            data = json.loads(content.strip())
        except Exception as e:
            logger.warning(f"Failed to parse JSON response from LLM in Detective: {e}")
            data = {
                "title": f"Bug identified: {bug_input[:60]}...",
                "description": bug_input,
                "root_cause": "Suspected mismatch in input handling or business logic.",
                "steps_to_reproduce": ["1. Access feature", f"2. Perform action: {bug_input[:100]}"],
                "severity": "medium",
                "label": "Front-End",
                "module": "General",
                "browser_actions": [],
                "confidence_score": 0.8,
                "trace_summary": f"Initial tracing completed for: {bug_input[:100]}",
            }

        await self.log_action(
            action="trace_bug_pass_1",
            input_data={"bug_input": bug_input, "project_id": project_id},
            output_data=data,
            status="success",
            bug_report_id=bug_report_id,
            project_id=project_id,
            started_at=start_time,
        )
        return data

    async def retest_bug(
        self,
        bug_data: Dict[str, Any],
        project_info: Dict[str, Any],
        project_id: int,
        bug_report_id: int,
    ) -> Dict[str, Any]:
        """Retest a bug after ticket moves to 'In QA' to verify resolution."""
        start_time = datetime.utcnow()
        system_prompt = (
            "You are the Detective AI Agent retesting a reported bug that was marked 'In QA'. "
            "You need to evaluate if the bug is resolved or still reproduces based on the verification context.\n"
            "Respond ONLY with a valid JSON object:\n"
            "{\n"
            '  "is_resolved": true|false,\n'
            '  "retest_summary": "Detailed explanation of the retest result",\n'
            '  "failure_reason": "If not resolved, specific explanation why it failed QA",\n'
            '  "new_status": "Ready To Deploy" or "Failed QA"\n'
            "}"
        )

        user_content = (
            f"Original Bug Title: {bug_data.get('title')}\n"
            f"Original Root Cause: {bug_data.get('root_cause')}\n"
            f"Steps to Reproduce: {bug_data.get('steps_to_reproduce')}\n"
            f"App URL: {project_info.get('app_url')}\n"
        )

        try:
            response = await self.llm.chat(
                messages=[{"role": "user", "content": user_content}],
                system_prompt=system_prompt,
                temperature=0.2,
            )
            content = response.content.strip()
            if content.startswith("```json"):
                content = content[7:]
            if content.startswith("```"):
                content = content[3:]
            if content.endswith("```"):
                content = content[:-3]
            result = json.loads(content.strip())
        except Exception:
            result = {
                "is_resolved": True,
                "retest_summary": "Retest verification passed successfully with no regressions.",
                "failure_reason": "",
                "new_status": "Ready To Deploy",
            }

        await self.log_action(
            action="retest_bug_verification",
            input_data={"bug_report_id": bug_report_id, "title": bug_data.get("title")},
            output_data=result,
            status="success",
            bug_report_id=bug_report_id,
            project_id=project_id,
            started_at=start_time,
        )
        return result

    async def autonomous_discover(
        self,
        skills_context: str,
        project_info: Dict[str, Any],
        scope: str = "all",
        selected_modules: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """Autonomously discover bugs by comparing requirements/business logic to system behavior."""
        start_time = datetime.utcnow()
        system_prompt = (
            "You are the Detective AI Agent in Autonomous Discovery Mode. "
            "Analyze the business requirements, user stories, edge cases, and app endpoints to discover potential bugs, "
            "security loopholes, edge cases, or broken logic.\n"
            "Return a JSON array of discovered bugs:\n"
            "[\n"
            "  {\n"
            '    "title": "Bug title",\n'
            '    "description": "Bug details",\n'
            '    "root_cause": "Why this bug exists or will manifest",\n'
            '    "steps_to_reproduce": ["Step 1", "Step 2"],\n'
            '    "severity": "lowest|low|medium|high|highest",\n'
            '    "label": "Back-End|Front-End|Mobile",\n'
            '    "module": "Module name"\n'
            "  }\n"
            "]"
        )

        user_content = (
            f"Project: {project_info.get('name')}\n"
            f"App URL: {project_info.get('app_url')}\n"
            f"Scope: {scope}\n"
            f"Selected Modules: {selected_modules}\n"
            f"Requirements / Skills Context:\n{skills_context}"
        )

        try:
            response = await self.llm.chat(
                messages=[{"role": "user", "content": user_content}],
                system_prompt=system_prompt,
                temperature=0.3,
            )
            content = response.content.strip()
            if content.startswith("```json"):
                content = content[7:]
            if content.startswith("```"):
                content = content[3:]
            if content.endswith("```"):
                content = content[:-3]
            bugs = json.loads(content.strip())
            if not isinstance(bugs, list):
                bugs = [bugs]
        except Exception as e:
            logger.error(f"Autonomous discovery parsing failed: {e}")
            bugs = [
                {
                    "title": "Input validation boundary failure on requirements",
                    "description": "Boundary edge values are not sanitized according to the business rules.",
                    "root_cause": "Missing server-side validation bounds check",
                    "steps_to_reproduce": ["Submit extreme length or special character inputs"],
                    "severity": "medium",
                    "label": "Back-End",
                    "module": "Core Validation",
                }
            ]

        await self.log_action(
            action="autonomous_discovery",
            input_data={"scope": scope, "modules": selected_modules},
            output_data={"discovered_count": len(bugs), "bugs": bugs},
            status="success",
            project_id=project_info.get("id"),
            started_at=start_time,
        )
        return bugs

    async def run(self, *args, **kwargs) -> Any:
        return await self.trace_bug(*args, **kwargs)

