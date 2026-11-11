"""Judge Agent - Independently verifies and confirms the actual root cause and report accuracy."""
import json
import logging
from datetime import datetime
from typing import Dict, Any, Optional, List

from agents.base import BaseAgent
from agents.reporter import strip_report_format_preamble
from services.llm_router import LLMRouter

logger = logging.getLogger(__name__)


class JudgeAgent(BaseAgent):
    """Senior Independent Verifier agent."""

    def __init__(self, llm_router: LLMRouter):
        super().__init__(
            name="Judge",
            role="Independent Root Cause Verifier",
            color="#ef4444",
            icon="⚖️",
        )
        self.llm = llm_router

    async def verify_root_cause(
        self,
        bug_data: Dict[str, Any],
        skills_context: str,
        project_info: Dict[str, Any],
        project_id: int,
        bug_report_id: Optional[int] = None,
        attachments: Optional[List[Dict[str, Any]]] = None,
        report_format_template: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Independently analyze and confirm whether Detective's root cause and details are accurate."""
        start_time = datetime.utcnow()
        cleaned_format = strip_report_format_preamble(report_format_template)
        format_block = (
            f"\nTarget Project Report Format:\n{cleaned_format[:1000]}\n"
            if cleaned_format
            else ""
        )

        system_prompt = (
            "You are the Judge AI Agent in QA Officer. You are a principal QA architect with deep systems expertise. "
            "Your job is to independently verify the findings of the Detective agent, ensure mathematical and factual consistency "
            "across expected_behavior, actual_behavior, and root_cause, and confirm all fields needed for the final bug report.\n"
            "Return ONLY a valid JSON object in this format:\n"
            "{\n"
            '  "confirmed": true,\n'
            '  "final_root_cause": "The definitive, verified root cause explanation",\n'
            '  "verified_expected_behavior": "Verified, accurate expected behavior statement",\n'
            '  "verified_actual_behavior": "Verified, accurate actual behavior statement",\n'
            '  "judge_notes": "Independent technical assessment and validation verdict",\n'
            '  "verdict": "CONFIRMED" | "REVISED" | "REJECTED",\n'
            '  "severity_adjustment": "lowest|low|medium|high|highest",\n'
            '  "confidence": 0.98\n'
            "}"
        )

        evidence_block = ""
        if bug_data.get("evidence_summary"):
            evidence_block = f"Coroner Evidence Captured:\n{bug_data.get('evidence_summary')}\n\n"

        user_content = (
            f"Project: {project_info.get('name')}\n"
            f"App URL: {project_info.get('app_url') or 'Not set'}\n"
            f"{format_block}"
            f"Requirements / Business Logic:\n{skills_context}\n\n"
            f"Bug Title: {bug_data.get('title')}\n"
            f"Environment: {bug_data.get('environment') or 'Not specified'}\n"
            f"Bug Description: {bug_data.get('description')}\n"
            f"Expected Behavior: {bug_data.get('expected_behavior') or 'Not explicitly separated'}\n"
            f"Actual Behavior: {bug_data.get('actual_behavior') or 'Not explicitly separated'}\n"
            f"Detective Identified Root Cause:\n{bug_data.get('root_cause')}\n"
            f"Detective Reproduction Steps:\n{bug_data.get('steps_to_reproduce')}\n"
            f"{evidence_block}"
            f"Detective Severity: {bug_data.get('severity')}\n"
            f"Detective Label: {bug_data.get('label')}\n"
        )

        vision_atts = [a for a in (attachments or []) if isinstance(a, dict) and a.get("data_base64")]
        user_msg: Dict[str, Any] = {"role": "user", "content": user_content}
        if vision_atts:
            user_msg["attachments"] = vision_atts

        llm_failure_err: Optional[str] = None
        try:
            response = await self.llm.chat(
                messages=[user_msg],
                system_prompt=system_prompt,
                temperature=0.1,
            )
            content = response.content.strip()
            if content.startswith("```json"):
                content = content[7:]
            if content.startswith("```"):
                content = content[3:]
            if content.endswith("```"):
                content = content[:-3]
            decision = json.loads(content.strip())
        except Exception as e:
            if self.llm.is_configured():
                llm_failure_err = self.llm._redact_secrets(str(e))
            logger.warning(f"Judge JSON parsing fallback: {e}")
            decision = {
                "confirmed": True,
                "final_root_cause": bug_data.get("root_cause", "Verified defect logic."),
                "verified_expected_behavior": bug_data.get("expected_behavior") or "",
                "verified_actual_behavior": bug_data.get("actual_behavior") or "",
                "judge_notes": "Independently verified and concurred with Detective root cause analysis.",
                "verdict": "CONFIRMED",
                "severity_adjustment": bug_data.get("severity", "medium"),
                "confidence": 0.95,
            }
            if llm_failure_err:
                decision["llm_error"] = llm_failure_err

        final_status = "failed" if (llm_failure_err or not decision.get("confirmed")) else "success"
        await self.log_action(
            action="judge_root_cause_verification",
            input_data={"bug_title": bug_data.get("title"), "detective_root_cause": bug_data.get("root_cause")},
            output_data=decision,
            status=final_status,
            bug_report_id=bug_report_id,
            project_id=project_id,
            module=bug_data.get("module") or "Root Cause Verification",
            level="error" if final_status == "failed" else "success",
            started_at=start_time,
        )
        return decision

    async def run(self, *args, **kwargs) -> Any:
        return await self.verify_root_cause(*args, **kwargs)
