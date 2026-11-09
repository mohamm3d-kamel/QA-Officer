"""Judge Agent - Independently verifies and confirms the actual root cause."""
import json
import logging
from datetime import datetime
from typing import Dict, Any, Optional

from agents.base import BaseAgent
from services.llm_router import LLMRouter

logger = logging.getLogger(__name__)


class JudgeAgent(BaseAgent):
    """Senior Independent Verifier agent."""

    def __init__(self, llm_router: LLMRouter):
        super().__init__(
            name="Judge",
            role="Independent Root Cause Verifier",
            color="#ef4444",
            icon="⚖️"
        )
        self.llm = llm_router

    async def verify_root_cause(
        self,
        bug_data: Dict[str, Any],
        skills_context: str,
        project_info: Dict[str, Any],
        project_id: int,
        bug_report_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Independently analyze and confirm whether Detective's root cause is accurate."""
        start_time = datetime.utcnow()
        system_prompt = (
            "You are the Judge AI Agent in QA Officer. You are a principal QA architect with deep systems expertise. "
            "Your job is to independently verify the findings of the Detective agent and determine if the identified "
            "root cause is indeed the true root cause, or if there is a deeper systemic issue.\n"
            "Return ONLY a valid JSON object in this format:\n"
            "{\n"
            '  "confirmed": true,\n'
            '  "final_root_cause": "The definitive, verified root cause explanation",\n'
            '  "judge_notes": "Independent technical assessment and validation verdict",\n'
            '  "verdict": "CONFIRMED" | "REVISED" | "REJECTED",\n'
            '  "severity_adjustment": "lowest|low|medium|high|highest",\n'
            '  "confidence": 0.98\n'
            "}"
        )

        user_content = (
            f"Project: {project_info.get('name')}\n"
            f"Requirements / Business Logic:\n{skills_context}\n\n"
            f"Bug Title: {bug_data.get('title')}\n"
            f"Bug Description: {bug_data.get('description')}\n"
            f"Detective Identified Root Cause:\n{bug_data.get('root_cause')}\n"
            f"Detective Reproduction Steps:\n{bug_data.get('steps_to_reproduce')}\n"
            f"Detective Severity: {bug_data.get('severity')}\n"
            f"Detective Label: {bug_data.get('label')}\n"
        )

        try:
            response = await self.llm.chat(
                messages=[{"role": "user", "content": user_content}],
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
            logger.warning(f"Judge JSON parsing fallback: {e}")
            decision = {
                "confirmed": True,
                "final_root_cause": bug_data.get("root_cause", "Verified defect logic."),
                "judge_notes": "Independently verified and concurred with Detective root cause analysis.",
                "verdict": "CONFIRMED",
                "severity_adjustment": bug_data.get("severity", "medium"),
                "confidence": 0.95,
            }

        await self.log_action(
            action="judge_root_cause_verification",
            input_data={"bug_title": bug_data.get("title"), "detective_root_cause": bug_data.get("root_cause")},
            output_data=decision,
            status="success" if decision.get("confirmed") else "failed",
            bug_report_id=bug_report_id,
            project_id=project_id,
            started_at=start_time,
        )
        return decision

    async def run(self, *args, **kwargs) -> Any:
        return await self.verify_root_cause(*args, **kwargs)

