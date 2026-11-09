"""Reporter Agent - Writes polished bug reports following the Tester's format specifications."""
import logging
from datetime import datetime
from typing import Dict, Any, Optional

from agents.base import BaseAgent
from services.llm_router import LLMRouter

logger = logging.getLogger(__name__)


class ReporterAgent(BaseAgent):
    """Bug report authoring agent."""

    def __init__(self, llm_router: LLMRouter):
        super().__init__(
            name="Reporter",
            role="Bug Report Author",
            color="#22c55e",
            icon="📝"
        )
        self.llm = llm_router

    async def generate_report(
        self,
        bug_data: Dict[str, Any],
        judge_verdict: Dict[str, Any],
        report_format_template: Optional[str],
        project_info: Dict[str, Any],
        project_id: int,
        bug_report_id: Optional[int] = None,
    ) -> str:
        """Compose the bug report adhering to the Tester's specified format."""
        start_time = datetime.utcnow()
        system_prompt = (
            "You are the Reporter AI Agent in QA Officer. "
            "Your job is to generate a pristine, high-impact bug report adhering strictly to the user's custom "
            "Report Format template if provided, or standard professional QA format if not specified.\n"
            "Format the report using clean Markdown."
        )

        user_content = (
            f"Project: {project_info.get('name')}\n"
            f"Target System URL: {project_info.get('app_url')}\n"
            f"Target Repository: {project_info.get('repo_url')}\n\n"
            f"Bug Title: {bug_data.get('title')}\n"
            f"Initial Description: {bug_data.get('description')}\n"
            f"Judge Confirmed Root Cause: {judge_verdict.get('final_root_cause')}\n"
            f"Judge Notes: {judge_verdict.get('judge_notes')}\n"
            f"Steps To Reproduce: {bug_data.get('steps_to_reproduce')}\n"
            f"Severity: {judge_verdict.get('severity_adjustment') or bug_data.get('severity')}\n"
            f"Module/Component: {bug_data.get('module')}\n"
            f"Label: {bug_data.get('label')}\n\n"
            f"--- TESTER SPECIFIED REPORT FORMAT TEMPLATE ---\n"
            f"{report_format_template or 'Standard QA Markdown Format: Title, Summary, Severity, Root Cause (Judge Confirmed), Steps to Reproduce, Expected Behavior, Actual Behavior, Visual Evidence.'}"
        )

        try:
            response = await self.llm.chat(
                messages=[{"role": "user", "content": user_content}],
                system_prompt=system_prompt,
                temperature=0.3,
            )
            report_text = response.content.strip()
        except Exception as e:
            logger.error(f"Reporter LLM generation error: {e}")
            report_text = self._fallback_report(bug_data, judge_verdict, project_info)

        await self.log_action(
            action="generate_bug_report",
            input_data={"bug_title": bug_data.get("title")},
            output_data={"report_preview": report_text[:200] + "..."},
            status="success",
            bug_report_id=bug_report_id,
            project_id=project_id,
            started_at=start_time,
        )
        return report_text

    def _fallback_report(self, bug_data: Dict[str, Any], judge_verdict: Dict[str, Any], project_info: Dict[str, Any]) -> str:
        steps = bug_data.get("steps_to_reproduce", [])
        if isinstance(steps, list):
            steps_str = "\n".join([f"{i+1}. {s}" for i, s in enumerate(steps)])
        else:
            steps_str = str(steps)

        return f"""# 🐛 BUG REPORT: {bug_data.get('title')}

**Project:** {project_info.get('name')}  
**Module:** {bug_data.get('module', 'General')}  
**Severity:** {judge_verdict.get('severity_adjustment') or bug_data.get('severity', 'Medium')}  
**Label:** {bug_data.get('label', 'Front-End')}  
**Date:** {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}  

---

### 📝 Summary
{bug_data.get('description')}

### ⚖️ Root Cause (Judge Confirmed)
{judge_verdict.get('final_root_cause')}

*Judge Notes:* {judge_verdict.get('judge_notes', 'Verified')}

### 🔁 Steps to Reproduce
{steps_str}

### 🎯 Expected Result
The system should behave according to user requirements and business documentation.

### ⚠️ Actual Result
Discrepancy encountered as described in reproduction steps.

### 📸 Evidence
Visual evidence captured by Journalist Agent attached to Jira ticket.
"""

    async def run(self, *args, **kwargs) -> Any:
        return await self.generate_report(*args, **kwargs)

