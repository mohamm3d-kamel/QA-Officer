"""Desk Officer Agent - Conversational QA Coordinator & Multi-Agent Dispatcher.

Handles pre-workflow chat regarding bugs, explains and assigns any built-in
QA Officer agent with its role, triggers the main workflow on explicit request,
and enforces that Sheriff never raises Jira tickets unless explicitly asked.
"""
import json
import logging
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

from agents.base import BaseAgent
from services.llm_router import LLMRouter

logger = logging.getLogger(__name__)


AGENT_ROSTER: List[Dict[str, str]] = [
    {
        "key": "detective",
        "name": "Detective",
        "role": "Root Cause Investigator & Tracer",
        "description": "Traces bug symptoms, logs, and business requirements to identify root cause, reproduction steps, severity, label, and affected module; also retests fixes and performs autonomous bug discovery.",
    },
    {
        "key": "coroner",
        "name": "Coroner",
        "role": "Evidence Collector & Recorder",
        "description": "Runs automated Playwright browser sessions to capture screenshots and video recordings (or diagnostic snapshots) during bug reproduction and failed QA retests.",
    },
    {
        "key": "judge",
        "name": "Judge",
        "role": "Independent Root Cause Verifier",
        "description": "Independently reviews and confirms, revises, or rejects the Detective's identified root cause and severity before a bug report is finalized.",
    },
    {
        "key": "reporter",
        "name": "Reporter",
        "role": "Bug Report Author",
        "description": "Authors the formatted Markdown bug report adhering strictly to the project's custom report template.",
    },
    {
        "key": "sheriff",
        "name": "Sheriff",
        "role": "Jira Ticket Commander",
        "description": "Raises the Jira Cloud bug ticket into the Backlog (never To Do) with Coroner evidence attachments, priority, label, and reporter — ONLY when explicitly requested.",
    },
    {
        "key": "watcher",
        "name": "Watcher",
        "role": "Jira Sentinel & Retest Trigger",
        "description": "Monitors Jira tickets for 'In QA' status and triggers Detective retesting (moving resolved bugs to 'Ready To Deploy' or failed ones to 'Failed QA').",
    },
    {
        "key": "sanity",
        "name": "Sanity Testers",
        "role": "Impacted Area & Regression Verifier",
        "description": "Evaluates adjacent and affected modules after a bug is resolved to verify zero regressions.",
    },
]

VALID_ACTIONS = {"chat", "run_main_workflow", "assign_agent", "save_skill"}
VALID_AGENTS = {"none", "detective", "coroner", "judge", "reporter", "sheriff", "watcher", "sanity", "auto_test"}

_NEGATION_PREFIX_RE = re.compile(
    r"\b(don'?t|do not|shouldn'?t|should not|mustn'?t|must not|won'?t|will not|can'?t|cannot|not yet|not to|never|without|before|until i ask|until asked|unless i ask|wait to|stand by|hold off)\b",
    re.IGNORECASE,
)
_NEGATION_SUFFIX_RE = re.compile(
    r"\b(don'?t|do not|shouldn'?t|should not|mustn'?t|must not|won'?t|will not|can'?t|cannot|not yet|not to|never|until i ask|until asked|unless i ask|should wait|stand by|hold off)\b",
    re.IGNORECASE,
)
_CLAUSE_SPLIT_RE = re.compile(r"[,.;!?\n]+|\b(?:but|however|except|and)\b", re.IGNORECASE)


def format_agent_roster() -> str:
    """Format all built-in QA Officer agents and their roles as Markdown."""
    lines = ["Here are the built-in QA Officer agents I can assign for you:"]
    for a in AGENT_ROSTER:
        lines.append(f"- **{a['name']}** (*{a['role']}*): {a['description']}")
    return "\n".join(lines)


class DeskOfficerAgent(BaseAgent):
    """Front-desk chat coordinator that discusses bugs and dispatches specialized agents."""

    def __init__(self, llm_router: LLMRouter):
        super().__init__(
            name="Desk Officer",
            role="Chat Coordinator & Agent Dispatcher",
            color="#06b6d4",
            icon="🎖️",
        )
        self.llm = llm_router

    @staticmethod
    def _extract_bug_report_id(text: str) -> Optional[int]:
        match = re.search(r"(?:#\s*(\d+)|\bbug\s+(\d+)\b|\bticket\s+(\d+)\b)", text, re.IGNORECASE)
        if not match:
            return None
        for g in match.groups():
            if g:
                return int(g)
        return None

    @staticmethod
    def _is_question_about_agent(text: str) -> bool:
        lower = text.strip().lower()
        return bool(
            re.match(
                r"^(who|what|which|how|explain|tell me about|list|describe)\b",
                lower,
            )
            and not re.search(r"\b(assign|dispatch|tell|ask|have|run|start|trigger|let)\b", lower)
        )

    @staticmethod
    def _has_negation_near(text: str, keyword_pattern: str, check_suffix: bool = False) -> bool:
        for m in re.finditer(keyword_pattern, text, re.IGNORECASE):
            clause_prefix = _CLAUSE_SPLIT_RE.split(text[: m.start()])[-1][-50:]
            if _NEGATION_PREFIX_RE.search(clause_prefix):
                return True
            if check_suffix:
                clause_suffix = _CLAUSE_SPLIT_RE.split(text[m.end() :])[0][:55]
                if _NEGATION_SUFFIX_RE.search(clause_suffix):
                    return True
        return False

    @classmethod
    def _user_explicitly_requested_jira(cls, text: str) -> bool:
        lower = text.strip().lower()
        if cls._is_question_about_agent(lower):
            return False

        # Positive patterns for explicitly raising to Jira / invoking Sheriff
        sheriff_cmd = re.search(
            r"\b(assign|dispatch|tell|ask|have|let|run|start|trigger|use|call|send)\b[^.!?\n]{0,30}\bsheriff\b"
            r"|\bsheriff\b[^.!?\n]{0,40}\b(raise|create|log|open|push|send|submit|file|ticket|backlog|jira)\b",
            lower,
        )
        if sheriff_cmd and not cls._has_negation_near(lower, r"\bsheriff\b", check_suffix=True):
            return True

        jira_cmd = re.search(
            r"\b(raise|push|send|log|submit|open|file|create|move|put)\b[^.!?\n]{0,40}\b(to\s+)?(jira|backlog)\b"
            r"|\braise\b[^.!?\n]{0,25}\b(the\s+|this\s+|a\s+)?(bug\s*report|ticket|jira|bug|it)\b"
            r"|\b(create|submit|push|log|open|file)\s+(a\s+|the\s+|this\s+)?(jira\s+)?ticket\b"
            r"|\bcreate\s+(a\s+|the\s+)?jira\s*(issue|bug)?\b",
            lower,
        )
        if jira_cmd and not cls._has_negation_near(lower, r"\b(raise|push|send|log|submit|open|file|create|jira|backlog|ticket)\b", check_suffix=True):
            return True

        return False

    @classmethod
    def _user_requested_main_workflow(cls, text: str) -> bool:
        lower = text.strip().lower()
        if cls._is_question_about_agent(lower):
            return False

        patterns = [
            r"\b(create|generate|make|build|prepare|start|run)\s+(a\s+|the\s+|this\s+)?bug\s*report\b",
            r"\b(start|run|execute|trigger|launch)\s+(the\s+)?(main|full|qa)\s+(workflow|pipeline)\b",
            r"\b(start|run|execute)\s+(the\s+)?workflow\b",
        ]
        for pat in patterns:
            if re.search(pat, lower) and not cls._has_negation_near(lower, pat):
                return True
        return False

    @classmethod
    def _detect_explicit_agent_assignment(cls, text: str) -> Optional[str]:
        lower = text.strip().lower()
        if cls._is_question_about_agent(lower):
            return None

        if cls._user_explicitly_requested_jira(lower):
            return "sheriff"

        agent_patterns = [
            ("auto_test", r"\b(auto[\s-]?test|autonomous\s+(bug\s+)?(discovery|testing|test))\b"),
            ("detective", r"\bdetective\b"),
            ("coroner", r"\bcoroner\b"),
            ("judge", r"\bjudge\b"),
            ("reporter", r"\breporter\b"),
            ("watcher", r"\bwatcher\b"),
            ("sanity", r"\bsanity(\s+testers?)?\b"),
        ]
        dispatch_verb = re.compile(
            r"\b(assign|dispatch|tell|ask|have|let|run|start|trigger|use|call|send|activate|invoke)\b",
            re.IGNORECASE,
        )

        for agent_key, pat in agent_patterns:
            if re.search(pat, lower) and not cls._has_negation_near(lower, pat):
                # For auto_test or explicit verb + agent name
                if agent_key == "auto_test" or dispatch_verb.search(lower):
                    return agent_key
        return None

    @classmethod
    def _is_skill_upload(cls, text: str) -> Optional[str]:
        lower = text.strip().lower()
        if cls._user_requested_main_workflow(text) or cls._detect_explicit_agent_assignment(text):
            return None
        if (
            ("report format" in lower or "format of the report" in lower)
            and not re.match(r"^(what|show|view|get|tell me|how)\b", lower)
            and len(lower) > 25
        ):
            return "report_format"
        if lower.startswith(("requirement:", "user story:", "business logic:")):
            return "requirement"
        return None

    @staticmethod
    def _synthesize_bug_context(
        message: str,
        conversation_history: Optional[List[Dict[str, Any]]] = None,
        attachments: Optional[List[Dict[str, Any]]] = None,
    ) -> str:
        """Combine prior chat discussion about the bug with the current message."""
        chunks: List[str] = []
        if conversation_history:
            user_notes: List[str] = []
            for item in conversation_history[-12:]:
                sender = (item.get("sender") or item.get("role") or "").lower()
                content = (item.get("content") or "").strip()
                if not content or item.get("isStatus"):
                    continue
                if sender == "user":
                    user_notes.append(content)
            if user_notes:
                chunks.append("Prior bug discussion from user:\n" + "\n---\n".join(user_notes))

        trimmed = message.strip()
        if trimmed:
            chunks.append(f"Latest user instruction:\n{trimmed}")

        if attachments:
            att_lines = []
            for att in attachments:
                name = att.get("name", "attachment")
                body = (att.get("content") or "")[:2000]
                att_lines.append(f"[{name}]\n{body}")
            if att_lines:
                chunks.append("Attachments:\n" + "\n\n".join(att_lines))

        return "\n\n".join(chunks)

    def _build_fallback_decision(
        self,
        message: str,
        conversation_history: Optional[List[Dict[str, Any]]],
        project_info: Dict[str, Any],
        recent_bugs: List[Dict[str, Any]],
        attachments: Optional[List[Dict[str, Any]]],
    ) -> Dict[str, Any]:
        """Deterministic fallback when no LLM API key is configured or LLM call fails."""
        bug_id = self._extract_bug_report_id(message)
        skill_type = self._is_skill_upload(message)
        if skill_type:
            return {
                "reply": "Got it — I'll store that as a project skill.",
                "action": "save_skill",
                "target_agent": "none",
                "bug_summary": "",
                "bug_report_id": None,
                "also_raise_jira": False,
                "skill_type": skill_type,
                "skill_title": "Custom Bug Report Format" if skill_type == "report_format" else message.split("\n")[0][:60],
            }

        bug_context = self._synthesize_bug_context(message, conversation_history, attachments)
        wants_workflow = self._user_requested_main_workflow(message)
        wants_jira = self._user_explicitly_requested_jira(message)
        explicit_agent = self._detect_explicit_agent_assignment(message)

        if wants_workflow:
            return {
                "reply": (
                    "Understood! Starting the main QA workflow now (**Detective → Coroner → Judge → Reporter**). "
                    + (
                        "Since you also asked to raise it, **Sheriff** will log the ticket to Jira **Backlog** afterward."
                        if wants_jira
                        else "**Sheriff** will stay on standby until you explicitly ask to raise the ticket to Jira **Backlog**."
                    )
                ),
                "action": "run_main_workflow",
                "target_agent": "none",
                "bug_summary": bug_context,
                "bug_report_id": bug_id,
                "also_raise_jira": wants_jira,
                "skill_type": None,
                "skill_title": None,
            }

        if explicit_agent:
            agent_meta = next((a for a in AGENT_ROSTER if a["key"] == explicit_agent), None)
            agent_label = agent_meta["name"] if agent_meta else explicit_agent.replace("_", " ").title()
            role_label = agent_meta["role"] if agent_meta else "Autonomous Bug Discovery"
            return {
                "reply": f"Assigning **{agent_label}** (*{role_label}*) to handle this now.",
                "action": "assign_agent",
                "target_agent": explicit_agent,
                "bug_summary": bug_context,
                "bug_report_id": bug_id,
                "also_raise_jira": explicit_agent == "sheriff",
                "skill_type": None,
                "skill_title": None,
            }

        lower = message.strip().lower()
        if re.search(r"\b(agents?|roster|who can|roles?|capabilities|help)\b", lower):
            reply = (
                f"{format_agent_roster()}\n\n"
                "We can discuss any bug details first before starting the workflow. "
                "Whenever you're ready, tell me **\"Create a bug report\"** to run the main workflow, "
                "or ask me to assign a specific agent (for example, *\"Ask Detective to investigate\"* or *\"Tell Sheriff to raise it to Backlog\"*)."
            )
        else:
            history_count = len(
                [h for h in (conversation_history or []) if (h.get("sender") or h.get("role") or "").lower() == "user"]
            )
            recent_hint = ""
            if recent_bugs:
                latest = recent_bugs[0]
                recent_hint = f"\n- Latest tracked bug in **{project_info.get('name')}**: `#{latest.get('id')}` — *{latest.get('title')}* (`{latest.get('status')}`)"
            reply = (
                f"I've noted these bug details for **{project_info.get('name', 'this project')}** "
                f"({history_count + 1} user note(s) in our pre-workflow triage):{recent_hint}\n\n"
                "Let's keep refining the issue before running the workflow — feel free to share steps to reproduce, expected vs. actual behavior, console/API logs, or target module.\n\n"
                "When you're ready, you can:\n"
                "- Say **\"Create a bug report\"** to run the main workflow (**Detective → Coroner → Judge → Reporter**; Sheriff stays on standby)\n"
                "- Assign any specific agent by role (e.g., **\"Assign Detective\"**, **\"Have Coroner capture evidence\"**, **\"Ask Judge to verify\"**, **\"Ask Watcher to retest\"**)\n"
                "- Say **\"Tell Sheriff to raise the bug report\"** when you want it pushed to Jira **Backlog**"
            )

        return {
            "reply": reply,
            "action": "chat",
            "target_agent": "none",
            "bug_summary": bug_context,
            "bug_report_id": bug_id,
            "also_raise_jira": False,
            "skill_type": None,
            "skill_title": None,
        }

    async def triage_and_route(
        self,
        message: str,
        project_info: Dict[str, Any],
        skills_context: str = "",
        recent_bugs: Optional[List[Dict[str, Any]]] = None,
        conversation_history: Optional[List[Dict[str, Any]]] = None,
        attachments: Optional[List[Dict[str, Any]]] = None,
        project_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Chat with the tester about a bug and decide whether to continue chatting, dispatch an agent, or start the workflow."""
        start_time = datetime.utcnow()
        recent_bugs = recent_bugs or []

        # Deterministic skill upload detection first
        skill_type = self._is_skill_upload(message)
        if skill_type:
            decision = self._build_fallback_decision(
                message, conversation_history, project_info, recent_bugs, attachments
            )
            await self.log_action(
                action="triage_chat_message",
                input_data={"message": message[:300], "project_id": project_id},
                output_data={"action": decision["action"], "skill_type": skill_type},
                status="success",
                project_id=project_id,
                started_at=start_time,
            )
            return decision

        roster_str = "\n".join(
            f"- {a['key']} ({a['name']} — {a['role']}): {a['description']}" for a in AGENT_ROSTER
        )
        bugs_str = (
            "\n".join(
                f"- Bug #{b['id']}: \"{b['title']}\" | status={b['status']} | jira={b.get('jira_ticket_id') or 'NOT_RAISED'}"
                for b in recent_bugs[:8]
            )
            or "None yet."
        )
        history_str = ""
        if conversation_history:
            hist_lines = []
            for h in conversation_history[-10:]:
                if h.get("isStatus"):
                    continue
                who = "User" if (h.get("sender") or h.get("role") or "").lower() == "user" else (h.get("agent") or "Desk Officer")
                hist_lines.append(f"{who}: {(h.get('content') or '')[:600]}")
            history_str = "\n".join(hist_lines)

        system_prompt = (
            "You are the **Desk Officer** AI Agent in QA Officer — the conversational front-desk QA coordinator.\n"
            "You chat with the tester about bugs BEFORE the main workflow starts, and you know all built-in agents and can assign any of them to start working.\n\n"
            f"Built-in QA Officer Agents:\n{roster_str}\n- auto_test (Autonomous Bug Discovery): Runs Detective autonomous discovery across stored skills.\n\n"
            "STRICT WORKFLOW RULES:\n"
            "1. When the user is describing a bug, asking questions, discussing behavior, or asking about agents, keep action='chat' — DO NOT start the workflow yet. Respond conversationally as an experienced QA partner (ask clarifying questions, summarize understanding, or explain agent roles).\n"
            "2. ONLY set action='run_main_workflow' when the user explicitly tells you to create/generate/start a bug report or run the main workflow (e.g., 'Create a bug report'). The main workflow runs Detective -> Coroner -> Judge -> Reporter.\n"
            "3. NEVER trigger Sheriff (also_raise_jira=true or target_agent='sheriff') unless the user EXPLICITLY asks Sheriff to raise/create/push the Jira ticket. When Sheriff does raise a ticket, it goes to Jira 'Backlog' (not 'To Do').\n"
            "4. Set action='assign_agent' ONLY when the user asks to assign/run a specific agent by name or role (target_agent in: detective, coroner, judge, reporter, sheriff, watcher, sanity, auto_test).\n\n"
            "Respond ONLY with a valid JSON object:\n"
            "{\n"
            '  "reply": "Your clear, helpful Markdown response to the tester",\n'
            '  "action": "chat|run_main_workflow|assign_agent",\n'
            '  "target_agent": "none|detective|coroner|judge|reporter|sheriff|watcher|sanity|auto_test",\n'
            '  "bug_summary": "Complete bug description synthesized from the conversation + latest message",\n'
            '  "bug_report_id": null,\n'
            '  "also_raise_jira": false\n'
            "}"
        )

        user_content = (
            f"Project: {project_info.get('name')} (App URL: {project_info.get('app_url')}, Jira Key: {project_info.get('jira_project_key')})\n"
            f"Stored Skills / Context:\n{skills_context[:2000] or 'None'}\n\n"
            f"Recent Tracked Bugs:\n{bugs_str}\n\n"
            f"Recent Conversation:\n{history_str or 'New conversation'}\n\n"
            f"Latest User Message:\n{message}"
        )

        fallback = self._build_fallback_decision(
            message, conversation_history, project_info, recent_bugs, attachments
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
            parsed = json.loads(content.strip())

            action = parsed.get("action", "chat")
            if action not in VALID_ACTIONS:
                action = "chat"

            target_agent = (parsed.get("target_agent") or "none").lower()
            if target_agent not in VALID_AGENTS:
                target_agent = "none"

            # Apply deterministic safety & command overrides
            wants_workflow = self._user_requested_main_workflow(message)
            wants_jira = self._user_explicitly_requested_jira(message)
            explicit_agent = self._detect_explicit_agent_assignment(message)

            if wants_workflow and action == "chat":
                action = "run_main_workflow"
            if explicit_agent and action == "chat" and not wants_workflow:
                action = "assign_agent"
                target_agent = explicit_agent

            # Strict guardrail: Sheriff NEVER runs unless user explicitly asked
            also_raise_jira = bool(parsed.get("also_raise_jira", False)) and wants_jira
            if target_agent == "sheriff" and not wants_jira:
                action = "chat"
                target_agent = "none"
            if wants_jira and action == "run_main_workflow":
                also_raise_jira = True

            bug_id = parsed.get("bug_report_id") or self._extract_bug_report_id(message)
            if isinstance(bug_id, str) and bug_id.isdigit():
                bug_id = int(bug_id)
            elif not isinstance(bug_id, int):
                bug_id = self._extract_bug_report_id(message)

            synthesized = parsed.get("bug_summary") or fallback["bug_summary"]
            if len(synthesized.strip()) < len(fallback["bug_summary"].strip()) // 2:
                synthesized = fallback["bug_summary"]

            decision = {
                "reply": parsed.get("reply") or fallback["reply"],
                "action": action,
                "target_agent": target_agent,
                "bug_summary": synthesized,
                "bug_report_id": bug_id,
                "also_raise_jira": also_raise_jira,
                "skill_type": None,
                "skill_title": None,
            }
        except Exception as e:
            logger.warning(f"Desk Officer LLM fallback triggered: {e}")
            decision = fallback

        await self.log_action(
            action=f"desk_officer_{decision['action']}",
            input_data={"message": message[:400], "project_id": project_id},
            output_data={
                "action": decision["action"],
                "target_agent": decision["target_agent"],
                "also_raise_jira": decision["also_raise_jira"],
                "bug_report_id": decision["bug_report_id"],
            },
            status="success",
            bug_report_id=decision.get("bug_report_id"),
            project_id=project_id,
            started_at=start_time,
        )
        return decision

    async def run(self, *args, **kwargs) -> Any:
        return await self.triage_and_route(*args, **kwargs)
