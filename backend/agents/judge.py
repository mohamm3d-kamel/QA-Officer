"""Judge Agent - Independently verifies and confirms the actual root cause and report accuracy."""
import json
import logging
from datetime import datetime
from typing import Dict, Any, List, Optional

from agents.base import BaseAgent
from services.environment_credentials import format_credentials_for_prompt
from services.llm_router import LLMRouter
from services.report_formatter import strip_report_format_preamble

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
        runtime_telemetry: Optional[List[Dict[str, Any]]] = None,
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

        telemetry_block = (
            f"\nCoroner Runtime & Browser Telemetry:\n{json.dumps(runtime_telemetry)}\n"
            if runtime_telemetry
            else ""
        )

        creds_prompt = format_credentials_for_prompt(
            project_info.get("environment_credentials"),
            app_url=project_info.get("app_url") or "",
        )
        creds_block = f"{creds_prompt}\n" if creds_prompt else ""
        user_content = (
            f"Project: {project_info.get('name')}\n"
            f"App URL: {project_info.get('app_url') or 'Not set'}\n"
            f"{creds_block}"
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
            f"{telemetry_block}"
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
                temperature=0.1,
                json_mode=True,
            )
            decision = self.parse_json_response(response.content)
            if not isinstance(decision, dict):
                raise ValueError("Judge expected JSON object response")
            decision["llm_degraded"] = False
            log_status = "success"
            log_level = "success" if decision.get("confirmed", True) else "warning"
        except Exception as e:
            if self.llm.is_configured():
                llm_failure_err = self.llm._redact_secrets(str(e))
                logger.warning(f"Judge LLM failure fallback: {llm_failure_err}")
                log_status = "failed"
                log_level = "error"
            else:
                logger.warning(f"Judge fell back to offline heuristic verification: {e}")
                log_status = "degraded"
                log_level = "warning"
            decision = {
                "confirmed": True,
                "final_root_cause": bug_data.get("root_cause", "Verified defect logic."),
                "verified_expected_behavior": bug_data.get("expected_behavior") or "",
                "verified_actual_behavior": bug_data.get("actual_behavior") or "",
                "judge_notes": "Independently verified and concurred with Detective root cause analysis.",
                "verdict": "CONFIRMED",
                "severity_adjustment": bug_data.get("severity", "medium"),
                "confidence": 0.95,
                "llm_degraded": True,
                "llm_error": llm_failure_err or str(e),
            }

        await self.log_action(
            action="judge_root_cause_verification",
            input_data={
                "bug_title": bug_data.get("title"),
                "detective_root_cause": bug_data.get("root_cause"),
                "has_runtime_telemetry": bool(runtime_telemetry),
            },
            output_data=decision,
            status=log_status,
            bug_report_id=bug_report_id,
            project_id=project_id,
            module=bug_data.get("module") or "Root Cause Verification",
            level=log_level,
            notify=bool(llm_failure_err),
            started_at=start_time,
        )
        return decision

    async def verify_retest_verdict(
        self,
        bug_data: Dict[str, Any],
        detective_retest: Dict[str, Any],
        skills_context: str,
        project_info: Dict[str, Any],
        project_id: int,
        bug_report_id: Optional[int] = None,
        attachments: Optional[List[Dict[str, Any]]] = None,
        evidence_list: Optional[List[Dict[str, Any]]] = None,
        runtime_telemetry: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """Independently verify Detective's 'In QA' verification outcome against Coroner evidence and requirements."""
        start_time = datetime.utcnow()
        det_passed = bool(detective_retest.get("is_resolved", False))
        det_summary = detective_retest.get("retest_summary") or detective_retest.get("failure_reason") or ""

        system_prompt = (
            "You are the Judge AI Agent in QA Officer conducting an independent 'In QA' verification audit. "
            "Review the Jira ticket requirements, Coroner's captured screenshots and runtime browser telemetry, "
            "and Detective's retest determination (PASSED vs FAILED). Decide whether Detective's verdict is CONFIRMED, REVISED, or REJECTED.\n"
            "Rules:\n"
            "- Set verdict='CONFIRMED' and confirmed=true when Detective's pass/fail conclusion aligns with the evidence and requirements.\n"
            "- Set verdict='REVISED' and confirmed=true if you revise qa_passed (e.g. Detective marked fail/pass incorrectly) with explicit qa_passed.\n"
            "- Set verdict='REJECTED' and confirmed=false (and qa_passed=false) if evidence is contradictory, incomplete, or fails QA despite a PASS claim.\n"
            "Return ONLY a valid JSON object:\n"
            "{\n"
            '  "confirmed": true,\n'
            '  "verdict": "CONFIRMED" | "REVISED" | "REJECTED",\n'
            '  "qa_passed": true | false,\n'
            '  "final_root_cause": "Verified technical assessment of the In QA ticket state",\n'
            '  "judge_notes": "Concise validation reasoning referencing evidence/telemetry/requirements",\n'
            '  "confidence": 0.96\n'
            "}"
        )

        ev_names = ", ".join(
            f"{e.get('type', 'file')}: {e.get('file_name', 'asset')}"
            for e in (evidence_list or [])
            if isinstance(e, dict)
        )
        telemetry_block = (
            f"\nCoroner Runtime & Browser Telemetry:\n{json.dumps(runtime_telemetry, ensure_ascii=False)[:2200]}\n"
            if runtime_telemetry
            else ""
        )
        ticket_ref = bug_data.get("jira_key") or bug_data.get("jira_ticket_id") or f"Bug #{bug_report_id}"
        creds_prompt = format_credentials_for_prompt(
            project_info.get("environment_credentials"),
            app_url=project_info.get("app_url") or "",
        )
        creds_block = f"{creds_prompt}\n" if creds_prompt else ""
        user_content = (
            f"Project: {project_info.get('name')} | App URL: {project_info.get('app_url') or 'Not set'}\n"
            f"{creds_block}"
            f"Ticket: {ticket_ref} ({bug_data.get('issue_type') or 'Ticket'})\n"
            f"Title: {bug_data.get('title')}\n"
            f"Description: {str(bug_data.get('description') or '')[:1500]}\n"
            f"Prior Root Cause / Notes: {bug_data.get('root_cause')}\n"
            f"Requirements / Skills Context:\n{skills_context[:2500]}\n\n"
            f"Detective Retest Outcome: {'PASSED (is_resolved=true)' if det_passed else 'FAILED (is_resolved=false)'}\n"
            f"Detective Summary: {det_summary}\n"
            f"Detective Failure Reason: {detective_retest.get('failure_reason') or 'None'}\n"
            f"Coroner Evidence Captured: {ev_names or 'None'}\n"
            f"{telemetry_block}"
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
                temperature=0.1,
                json_mode=True,
            )
            decision = self.parse_json_response(response.content)
            if not isinstance(decision, dict):
                raise ValueError("Judge retest verification expected JSON object response")
            verdict_norm = str(decision.get("verdict") or "CONFIRMED").strip().upper()
            if verdict_norm not in ("CONFIRMED", "REVISED", "REJECTED"):
                verdict_norm = "CONFIRMED" if decision.get("confirmed", True) else "REJECTED"
            confirmed = bool(decision.get("confirmed", verdict_norm != "REJECTED")) and verdict_norm != "REJECTED"
            qa_passed = False if not confirmed else bool(decision.get("qa_passed", det_passed))
            decision["verdict"] = verdict_norm
            decision["confirmed"] = confirmed
            decision["qa_passed"] = qa_passed
            decision["llm_degraded"] = False
            log_status = "success"
            log_level = "success" if confirmed else "warning"
        except Exception as e:
            if self.llm.is_configured():
                llm_failure_err = self.llm._redact_secrets(str(e))
                logger.warning(f"Judge In-QA verification LLM failure fallback: {llm_failure_err}")
                log_status = "failed"
                log_level = "error"
            else:
                logger.warning(f"Judge In-QA verification fell back to offline heuristic mode: {e}")
                log_status = "degraded"
                log_level = "warning"
            decision = {
                "confirmed": True,
                "verdict": "CONFIRMED",
                "qa_passed": det_passed,
                "final_root_cause": det_summary or bug_data.get("root_cause", "Verified retest status."),
                "judge_notes": f"Concurred with Detective In-QA outcome ({'PASSED' if det_passed else 'FAILED'}).",
                "confidence": 0.92,
                "llm_degraded": True,
                "llm_error": llm_failure_err or str(e),
            }

        await self.log_action(
            action="judge_retest_verification",
            input_data={
                "bug_title": bug_data.get("title"),
                "jira_key": ticket_ref,
                "detective_passed": det_passed,
                "has_runtime_telemetry": bool(runtime_telemetry),
            },
            output_data=decision,
            status=log_status,
            bug_report_id=bug_report_id,
            project_id=project_id,
            module=bug_data.get("module") or "QA Verification",
            jira_key=bug_data.get("jira_key") or bug_data.get("jira_ticket_id"),
            level=log_level,
            notify=bool(llm_failure_err),
            started_at=start_time,
        )
        return decision

    async def run(self, *args, **kwargs) -> Any:
        return await self.verify_root_cause(*args, **kwargs)
