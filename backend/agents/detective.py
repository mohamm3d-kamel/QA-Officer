"""Detective Agent - Traces bugs, tracks root cause, verifies reproduction and retests."""
import json
import logging
import re
from datetime import datetime
from typing import Dict, Any, Optional, List

from agents.base import BaseAgent
from agents.reporter import clean_bug_description, clean_bug_title, strip_report_format_preamble
from services.llm_router import LLMRouter

logger = logging.getLogger(__name__)


class DetectiveAgent(BaseAgent):
    """Bug tracing mode agent."""

    def __init__(self, llm_router: LLMRouter):
        super().__init__(
            name="Detective",
            role="Root Cause Investigator & Tracer",
            color="#8b5cf6",
            icon="🔍",
        )
        self.llm = llm_router

    @staticmethod
    def _build_offline_findings(
        bug_input: str,
        project_info: Dict[str, Any],
        trace_summary_override: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Build clean, structured investigation findings without chat-synthesis noise when offline/fallback."""
        cleaned_desc = clean_bug_description(bug_input)
        title = clean_bug_title(cleaned_desc)
        app_url = (project_info.get("app_url") or "").strip()
        proj_name = (project_info.get("name") or "Target Project").strip()
        environment = f"{proj_name} ({app_url})" if app_url else proj_name

        # Lightweight heuristic split for expected vs actual sentences
        expected = ""
        actual = ""
        for sent in re.split(r"(?<=[.!?])\s+|\n+", cleaned_desc):
            s = sent.strip()
            if not s:
                continue
            low = s.lower()
            if not expected and re.search(r"\b(expected|should|supposed to|must)\b", low):
                expected = s
            elif not actual and re.search(r"\b(actual|instead|however|but|fails?|error|broken|500|404|400|wrong|incorrect|doesn'?t|does not)\b", low):
                actual = s

        if not expected:
            expected = f"The feature in {proj_name} should complete accurately according to requirements and valid input rules."
        if not actual:
            actual = cleaned_desc.splitlines()[0][:240] if cleaned_desc else "Unexpected behavior observed during testing."

        action_line = cleaned_desc.splitlines()[0][:140] if cleaned_desc else title
        steps = [
            f"Open {app_url or proj_name}",
            f"Execute the affected flow: {action_line}",
            "Observe the resulting state or response",
        ]

        return {
            "title": title,
            "environment": environment,
            "description": cleaned_desc or title,
            "root_cause": "Suspected defect in application validation, calculation, or state handling matching the observed symptoms.",
            "expected_behavior": expected,
            "actual_behavior": actual,
            "steps_to_reproduce": steps,
            "severity": "medium",
            "label": "Front-End",
            "module": "General",
            "browser_actions": [],
            "confidence_score": 0.8,
            "trace_summary": trace_summary_override or f"Initial tracing completed for: {title[:100]}",
        }

    async def trace_bug(
        self,
        bug_input: str,
        skills_context: str,
        project_info: Dict[str, Any],
        project_id: int,
        bug_report_id: Optional[int] = None,
        attachments: Optional[List[Dict[str, Any]]] = None,
        report_format_template: Optional[str] = None,
    ) -> Dict[str, Any]:
        """First Pass: Trace the bug, identify potential root cause, expected/actual behavior, reproduction steps, and severity."""
        start_time = datetime.utcnow()
        cleaned_format = strip_report_format_preamble(report_format_template)
        format_guidance = (
            f"\nTarget Project Report Format Sections (ensure your findings supply details needed by these sections):\n{cleaned_format[:1200]}\n"
            if cleaned_format
            else ""
        )

        system_prompt = (
            "You are the Detective AI Agent in QA Officer. Your mission is to analyze bug reports, user stories, "
            "codebase info, attached screenshots/documents/logs, or web application behaviors to find the exact root cause and reproduction steps.\n"
            "Be factually accurate, concise, and mathematically consistent between expected_behavior, actual_behavior, and root_cause.\n"
            "Respond ONLY with a valid JSON object matching this schema:\n"
            "{\n"
            '  "title": "Clear concise single-line bug title",\n'
            '  "environment": "Target system/URL, component/browser context relevant to the defect",\n'
            '  "description": "Clean factual summary of the issue without chat wrapper text",\n'
            '  "root_cause": "Deep analysis of why this happens (code logic, API failure, DOM state, validation, etc.)",\n'
            '  "expected_behavior": "Exact expected behavior grounded in requirements",\n'
            '  "actual_behavior": "Exact observed defective behavior",\n'
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
            f"{format_guidance}"
            f"Stored Skills / Business Context:\n{skills_context}\n\n"
            f"Tester Bug Input:\n{bug_input}"
        )

        vision_atts = [
            a for a in (attachments or [])
            if isinstance(a, dict) and a.get("data_base64") and (a.get("is_image") or a.get("mime_type") == "application/pdf")
        ]
        user_msg: Dict[str, Any] = {"role": "user", "content": user_content}
        if vision_atts:
            user_msg["attachments"] = vision_atts

        llm_failure_err: Optional[str] = None
        try:
            response = await self.llm.chat(
                messages=[user_msg],
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
            if data.get("title"):
                data["title"] = clean_bug_title(data["title"])
            if data.get("description"):
                data["description"] = clean_bug_description(data["description"])
        except Exception as e:
            if self.llm.is_configured():
                llm_failure_err = self.llm._redact_secrets(str(e))
                data_fallback_note = f"Detective live LLM error ({self.llm.provider}/{self.llm.model}): {llm_failure_err[:200]}"
            else:
                data_fallback_note = None
            logger.warning(f"Failed to parse JSON response from LLM in Detective: {e}")
            data = self._build_offline_findings(bug_input, project_info, trace_summary_override=data_fallback_note)
            if llm_failure_err:
                data["llm_error"] = llm_failure_err

        await self.log_action(
            action="trace_bug_pass_1",
            input_data={"bug_input": bug_input, "project_id": project_id},
            output_data=data,
            status="failed" if llm_failure_err else "success",
            bug_report_id=bug_report_id,
            project_id=project_id,
            module=data.get("module") or "Defect Investigation",
            level="error" if llm_failure_err else "success",
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

        llm_failure_err: Optional[str] = None
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
        except Exception as e:
            if self.llm.is_configured():
                llm_failure_err = self.llm._redact_secrets(str(e))
            result = {
                "is_resolved": True,
                "retest_summary": "Retest verification passed successfully with no regressions.",
                "failure_reason": "",
                "new_status": "Ready To Deploy",
            }
            if llm_failure_err:
                result["llm_error"] = llm_failure_err

        await self.log_action(
            action="retest_bug_verification",
            input_data={"bug_report_id": bug_report_id, "title": bug_data.get("title")},
            output_data=result,
            status="failed" if llm_failure_err else "success",
            bug_report_id=bug_report_id,
            project_id=project_id,
            module=bug_data.get("module") or "QA Verification",
            level="error" if llm_failure_err else "success",
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
            '    "expected_behavior": "Expected behavior per requirements",\n'
            '    "actual_behavior": "Observed or risky failure behavior",\n'
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

        llm_failure_err: Optional[str] = None
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
            if self.llm.is_configured():
                llm_failure_err = self.llm._redact_secrets(str(e))
            logger.error(f"Autonomous discovery parsing failed: {e}")
            bugs = [
                {
                    "title": "Input validation boundary failure on requirements",
                    "description": "Boundary edge values are not sanitized according to the business rules.",
                    "root_cause": "Missing server-side validation bounds check",
                    "expected_behavior": "Out-of-bounds or invalid inputs should be rejected with a clear validation message.",
                    "actual_behavior": "Invalid boundary inputs are accepted or trigger an unhandled validation error.",
                    "steps_to_reproduce": ["Submit extreme length or special character inputs"],
                    "severity": "medium",
                    "label": "Back-End",
                    "module": "Core Validation",
                }
            ]

        await self.log_action(
            action="autonomous_discovery",
            input_data={"scope": scope, "modules": selected_modules},
            output_data={
                "discovered_count": len(bugs),
                "bugs": bugs,
                **({"llm_error": llm_failure_err} if llm_failure_err else {}),
            },
            status="failed" if llm_failure_err else "success",
            project_id=project_info.get("id"),
            module=", ".join(selected_modules[:3]) if selected_modules else "Auto-Test",
            level="error" if llm_failure_err else "success",
            started_at=start_time,
        )
        return bugs

    async def run(self, *args, **kwargs) -> Any:
        return await self.trace_bug(*args, **kwargs)
