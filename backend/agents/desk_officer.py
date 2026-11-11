"""Desk Officer Agent - Interactive Multi-Turn AI Chat Coordinator & Multi-Agent Dispatcher.

Chats naturally with the tester regarding bugs before starting the main workflow,
has full visibility into all project assets (live Jira Cloud boards/tickets, stored skills,
bug reports, visual evidence, execution logs, app URL inspection, and local repo files),
knows all built-in QA Officer agents and can assign or organize any multi-agent sequence
on request, streams real LLM responses token-by-token when configured, provides context-aware
local QA triage when offline, triggers the main workflow on explicit request,
and enforces that Sheriff never raises Jira tickets unless explicitly asked.
"""
import json
import logging
import re
from datetime import datetime
from typing import Any, Awaitable, Callable, Dict, List, Optional

from agents.base import BaseAgent
from agents.reporter import strip_report_format_preamble
from services.attachment_processor import (
    format_attachment_text_block,
    hydrate_history_attachments,
)
from services.jira_client import is_real_jira_issue_key
from services.llm_router import LLMRouter

logger = logging.getLogger(__name__)


AGENT_ROSTER: List[Dict[str, str]] = [
    {
        "key": "detective",
        "name": "Detective",
        "role": "Root Cause Investigator",
        "description": "Traces bug symptoms, live browser telemetry, runtime errors, logs, and business requirements to identify root cause, reproduction steps, severity, label, and affected module; and retests fixes or runs autonomous discovery.",
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
        "description": "Authors clean, verified Markdown bug reports incorporating Detective findings, Coroner visual evidence, and Judge verdicts while adhering strictly to the project's configured Report Format template as-is.",
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
        "role": "Jira Update & Agent Failure Sentinel",
        "description": "Monitors Jira tickets ('In QA', status transitions, and Jira updates) and AI agent failures — triggering Detective retesting and dispatching real-time Slack and Email alerts strictly for Jira updates and AI agent failures.",
    },
    {
        "key": "sanity",
        "name": "Sanity Testers",
        "role": "Impacted Area & Regression Verifier",
        "description": "Evaluates adjacent and affected modules after a bug is resolved to verify zero regressions.",
    },
]

VALID_ACTIONS = {"chat", "run_main_workflow", "assign_agent", "organize_agents", "save_skill"}
VALID_AGENTS = {"none", "detective", "coroner", "judge", "reporter", "sheriff", "watcher", "sanity", "auto_test"}

_ACTION_MARKER = "<!-- DESK_ACTION"
_TOOL_MARKER = "<!-- DESK_TOOL"
_ACTION_TAG_RE = re.compile(r"<!--\s*DESK_ACTION\s*(\{.*?\})\s*-->", re.DOTALL)
_TOOL_TAG_RE = re.compile(r"<!--\s*DESK_TOOL\s*(\{.*?\})\s*-->", re.DOTALL)
_ANY_DESK_TAG_RE = re.compile(r"<!--\s*DESK_(?:ACTION|TOOL)\s*\{.*?\}\s*-->", re.DOTALL)
_JIRA_KEY_RE = re.compile(r"\b([A-Z][A-Z0-9]{1,12}-\d+)\b")

_NEGATION_PREFIX_RE = re.compile(
    r"\b(don'?t|do not|shouldn'?t|should not|mustn'?t|must not|won'?t|will not|can'?t|cannot|not yet|not to|never|without|before|until i ask|until asked|unless i ask|wait to|stand by|hold off)\b",
    re.IGNORECASE,
)
_NEGATION_SUFFIX_RE = re.compile(
    r"\b(don'?t|do not|shouldn'?t|should not|mustn'?t|must not|won'?t|will not|can'?t|cannot|not yet|not to|never|until i ask|until asked|unless i ask|should wait|stand by|hold off)\b",
    re.IGNORECASE,
)
_CLAUSE_SPLIT_RE = re.compile(r"[,.;!?\n—]+|--|\b(?:but|however|except|and)\b", re.IGNORECASE)

_GREETING_PHRASES = {
    "hi", "hello", "hey", "yo", "hola", "salam", "salam alaikum", "marhaba",
    "good morning", "good afternoon", "good evening", "how are you",
    "how are you doing", "whats up", "what's up", "sup",
    "hi there", "hello there", "hey there",
    "hi desk officer", "hello desk officer", "hey desk officer",
    "test", "testing",
}
_ACK_PHRASES = {
    "ok", "okay", "k", "thanks", "thank you", "thx", "ty",
    "thanks a lot", "thank you so much", "thank you very much",
    "cool", "nice", "great", "awesome", "perfect", "got it",
    "sounds good", "noted", "bye", "goodbye",
}
_BUG_SIGNAL_RE = re.compile(
    r"\b(bug|bugs|error|errors|fail|fails|failed|crash|crashes|broken|issue|issues|defect|404|500|exception|timeout|slow|fix|broken|create|assign|jira|retest|sanity)\b",
    re.IGNORECASE,
)
_BUG_LIST_QUERY_RE = re.compile(
    r"\b(what|which|show|list|view|get|check|status\s+of)\b[^.?\n]{0,30}\b(bugs?|defects?|issues?|tickets?|reports?)\b|\b(recent|latest|open|tracked)\s+(bugs?|defects?|issues?|tickets?)\b",
    re.IGNORECASE,
)


def format_agent_roster() -> str:
    """Format all built-in QA Officer agents and their roles as Markdown."""
    lines = ["Here are the built-in QA Officer agents I can assign or organize for you:"]
    for a in AGENT_ROSTER:
        lines.append(f"- **{a['name']}** (*{a['role']}*): {a['description']}")
    lines.append(
        "- **Auto-Test Discovery** (*Autonomous Exploratory Run*): Launches Detective across stored project skills to discover defects and run the main reporting pipeline."
    )
    return "\n".join(lines)


class DeskOfficerAgent(BaseAgent):
    """Interactive front-desk chat coordinator that discusses bugs, accesses all project assets, and dispatches/organizes agents."""

    def __init__(self, llm_router: LLMRouter):
        super().__init__(
            name="Desk Officer",
            role="Chat Coordinator & Agent Dispatcher",
            color="#06b6d4",
            icon="🎖️",
        )
        self.llm = llm_router
        self.asset_hub: Optional[Any] = None
        self.agent_registry: Dict[str, Any] = {}

    def attach_context(
        self,
        asset_hub: Optional[Any] = None,
        agent_registry: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Bind orchestrator's ProjectAssetHub and live agent registry."""
        if asset_hub is not None:
            self.asset_hub = asset_hub
        if agent_registry is not None:
            self.agent_registry = agent_registry

    @staticmethod
    def _extract_bug_report_id(text: str) -> Optional[int]:
        match = re.search(r"(?:(?<![A-Z0-9-])#\s*(\d+)|\bbug\s*#?\s*(\d+)\b|\bticket\s+#?\s*(\d+)\b)", text or "", re.IGNORECASE)
        if not match:
            return None
        for g in match.groups():
            if g:
                return int(g)
        return None

    @classmethod
    def _resolve_bug_id_from_context(
        cls,
        message: str,
        parsed_meta: Optional[Dict[str, Any]] = None,
        conversation_history: Optional[List[Dict[str, Any]]] = None,
        recent_bugs: Optional[List[Dict[str, Any]]] = None,
        prefer_unraised_project_key: Optional[str] = None,
    ) -> Optional[int]:
        raw_meta_id = (parsed_meta or {}).get("bug_report_id")
        if isinstance(raw_meta_id, int) and raw_meta_id > 0:
            return raw_meta_id
        if isinstance(raw_meta_id, str) and raw_meta_id.isdigit():
            return int(raw_meta_id)

        direct = cls._extract_bug_report_id(message)
        if direct:
            return direct

        bugs_by_id = {b.get("id"): b for b in (recent_bugs or []) if b.get("id")}
        fallback_cand: Optional[int] = None
        for item in reversed((conversation_history or [])[-10:]):
            if item.get("isStatus"):
                continue
            cand = cls._extract_bug_report_id(item.get("content") or "")
            if cand and (not bugs_by_id or cand in bugs_by_id):
                if prefer_unraised_project_key and cand in bugs_by_id:
                    cand_bug = bugs_by_id[cand]
                    existing_key = cand_bug.get("jira_ticket_id")
                    if existing_key and is_real_jira_issue_key(existing_key, prefer_unraised_project_key):
                        continue
                    if not (cand_bug.get("has_formatted_report") or cand_bug.get("status") in ("confirmed", "reported")):
                        if fallback_cand is None:
                            fallback_cand = cand
                        continue
                return cand
        return fallback_cand

    @staticmethod
    def _extract_jira_key(text: str) -> Optional[str]:
        match = _JIRA_KEY_RE.search(text or "")
        return match.group(1) if match else None

    @staticmethod
    def _extract_jira_project_key(
        message: str,
        parsed_meta: Optional[Dict[str, Any]] = None,
        project_info: Optional[Dict[str, Any]] = None,
        asset_snapshot: Optional[Dict[str, Any]] = None,
    ) -> Optional[str]:
        meta = parsed_meta or {}
        explicit = meta.get("jira_project_key")
        if isinstance(explicit, str) and re.fullmatch(r"[A-Za-z][A-Za-z0-9]{1,14}", explicit.strip()):
            return explicit.strip().upper()

        # LLMs sometimes place bare project key (e.g. "SCRUM") into jira_key
        raw_jk = meta.get("jira_key")
        if isinstance(raw_jk, str) and re.fullmatch(r"[A-Za-z][A-Za-z0-9]{1,14}", raw_jk.strip()):
            return raw_jk.strip().upper()

        known_keys = set()
        if project_info and project_info.get("jira_project_key"):
            known_keys.add(str(project_info["jira_project_key"]).strip().upper())
        if asset_snapshot and isinstance(asset_snapshot.get("project"), dict):
            pk = asset_snapshot["project"].get("jira_project_key")
            if pk:
                known_keys.add(str(pk).strip().upper())

        for key in known_keys:
            if re.search(rf"\b{re.escape(key)}\b", message or "", re.IGNORECASE):
                return key

        m = re.search(r"\b(?:in|to|into|project|key)\s+([A-Z][A-Z0-9]{1,12})\b", message or "")
        if m and m.group(1) not in {"QA"}:
            return m.group(1)
        if m and m.group(1) == "QA" and "QA" in known_keys:
            return "QA"
        return None

    @staticmethod
    def _user_requested_fresh_ticket(text: str) -> bool:
        return bool(
            re.search(
                r"\b(new|fresh|valid|re-?create|recreate|another|separate|replace)\b[^.!?\n]{0,30}\b(ticket|issue|jira|scrum)\b"
                r"|\b(fresh|new)\s+ticket\b"
                r"|\bconfirm(?:ing)?\b[^.!?\n]{0,30}\bticket\s+(?:creation|key\s+generation)\b",
                text or "",
                re.IGNORECASE,
            )
        )

    @staticmethod
    def _is_question_about_agent(text: str) -> bool:
        lower = text.strip().lower()
        if re.match(r"^(who|what|which|how|why|explain|tell me about|list|describe)\b", lower):
            # Allow multi-sentence inputs like "What is in Backlog? Assign Detective to SCRUM-850"
            sentences = [s.strip() for s in re.split(r"[.!?\n]+", lower) if s.strip()]
            if len(sentences) > 1:
                non_q = [
                    s
                    for s in sentences
                    if not re.match(r"^(who|what|which|how|why|explain|tell me about|list|describe)\b", s)
                ]
                if any(
                    re.search(
                        r"\b(assign|dispatch|tell|ask|have|run|start|trigger|organize|orchestrate|coordinate|create\s+(?:a\s+|the\s+)?bug\s*report|investigate|raise)\b",
                        s,
                    )
                    for s in non_q
                ):
                    return False
            return True

        if lower.endswith("?") and re.match(r"^(did|has|have|was|were|is|are|should\s+i|should\s+we)\b", lower):
            return True

        return bool(
            re.match(r"^(can|could|would|do|will)\s+you\s+(explain|describe|list|tell me|show|check|read|get)\b", lower)
            and not re.search(
                r"\b(assign|dispatch|tell|ask|have|run|start|trigger|organize|orchestrate|coordinate|investigate|raise|push|create\s+(?:a\s+|the\s+)?bug\s*report)\b",
                lower,
            )
        )

    @staticmethod
    def _has_negation_near(text: str, keyword_pattern: str, check_suffix: bool = False) -> bool:
        matches = list(re.finditer(keyword_pattern, text, re.IGNORECASE))
        if not matches:
            return False
        for m in matches:
            clause_prefix = _CLAUSE_SPLIT_RE.split(text[: m.start()])[-1][-50:]
            prefix_neg = bool(_NEGATION_PREFIX_RE.search(clause_prefix))
            suffix_neg = False
            if check_suffix:
                clause_suffix = _CLAUSE_SPLIT_RE.split(text[m.end() :])[0][:55]
                suffix_neg = bool(_NEGATION_SUFFIX_RE.search(clause_suffix) or _NEGATION_PREFIX_RE.search(m.group(0)))
            if not prefix_neg and not suffix_neg:
                return False
        return True

    @classmethod
    def _user_explicitly_requested_jira(cls, text: str) -> bool:
        lower = text.strip().lower()
        if cls._is_question_about_agent(lower):
            return False
        if re.match(r"^(verify|inspect|check|audit|show|list)\b", lower) and not re.search(
            r"\b(then|and)\s+(assign|dispatch|tell|have|run|trigger|raise|create)\b", lower
        ):
            return False

        sheriff_pat = (
            r"\b(assign|dispatch|tell|ask|have|let|run|start|trigger|use|call|send|organize|coordinate|include|make)\b[^.!?\n]{0,45}\bsheriff\b"
            r"|\bsheriff\b[^.!?\n]{0,40}\b(raise|create|make|log|push|send|submit|file|ticket|backlog|jira|scrum)\b"
        )
        if re.search(sheriff_pat, lower) and not cls._has_negation_near(lower, sheriff_pat, check_suffix=True):
            return True

        jira_pat = (
            r"\b(raise|push|send|log|submit|file|create|make|generate|move|put)\b[^.!?\n]{0,45}\b(to\s+|in\s+|into\s+)?(jira|backlog|scrum\s+backlog)\b"
            r"|\braise\b[^.!?\n]{0,30}\b(?:the\s+|this\s+|that\s+|our\s+|my\s+|a\s+|new\s+|fresh\s+)?(?:bug\s*reports?|reports?|tickets?|jira|bugs?|it)\b"
            r"|\b(?:push|submit|log|file)\b[^.!?\n]{0,30}\b(?:the\s+|this\s+|that\s+|our\s+|my\s+|a\s+)?(?:bug\s*)?reports?\b"
            r"|\b(create|make|generate|submit|push|log|file|raise)\s+(?:a\s+|the\s+|this\s+|an?\s+)?(?:(?:new|fresh|valid|real|separate|another|jira|scrum)\s+){0,3}(?:jira\s+|scrum\s+)?(?:ticket|issue)\b"
            r"|\bopen\s+(?:a\s+|the\s+|this\s+|an?\s+)?(?:(?:new|fresh|valid|jira|scrum)\s+){0,2}(?:jira\s+)?ticket\b"
            r"|\bcreate\s+(?:a\s+|the\s+)?(?:jira\s+|scrum\s+)(?:issue|bug|ticket)\b"
            r"|\bconfirm(?:ing)?\b[^.!?\n]{0,30}\b(?:(?:new|fresh|valid|jira|scrum)\s+){0,2}ticket\s+(?:creation|key\s+generation)\b"
        )
        if re.search(jira_pat, lower) and not cls._has_negation_near(lower, jira_pat, check_suffix=True):
            return True

        return False

    @classmethod
    def _user_requested_main_workflow(cls, text: str) -> bool:
        lower = text.strip().lower()
        if cls._is_question_about_agent(lower):
            return False

        patterns = [
            r"\b(create|generate|make|build|prepare|start|run)\s+(a\s+|the\s+|this\s+)?bug\s*report\b(?!\s+(?:ticket|jira)\b)",
            r"\b(start|run|execute|trigger|launch)\s+(the\s+)?(main|full|qa)\s+(workflow|pipeline)\b",
            r"\b(start|run|execute)\s+(the\s+)?workflow\b",
        ]
        if re.search(r"\bsheriff\b[^.!?\n]{0,35}\b(create|raise|log|open)\b", lower):
            return False
        for pat in patterns:
            if re.search(pat, lower) and not cls._has_negation_near(lower, pat):
                return True
        return False

    @classmethod
    def _user_requested_investigation(cls, text: str) -> bool:
        """Detect natural user requests to investigate/debug/trace/reproduce a defect."""
        lower = text.strip().lower()
        if cls._is_question_about_agent(lower):
            return False
        if re.match(r"^(verify|inspect|check|audit|show|list|read)\b", lower) and not re.search(
            r"\b(then|and)\s+(investigate|trace|debug|reproduce|assign|dispatch|tell|have|run|trigger)\b", lower
        ):
            return False
        if cls._user_requested_main_workflow(text) or cls._user_explicitly_requested_jira(text):
            return False

        inv_pat = (
            r"\b(?:investigate|root\s*cause\s*analysis|reproduce\s+(?:and\s+investigate|this|the\s+(?:bug|issue))|"
            r"find\s+(?:out\s+)?(?:the\s+root\s*cause|why)|"
            r"trace\s+(?:this|the)\s+(?:bug|issue|error)|"
            r"debug\s+(?:this|the)\s+(?:bug|issue|error)|"
            r"look\s+into\s+(?:this|the)\s+(?:bug|issue|error))\b"
        )
        return bool(re.search(inv_pat, lower) and not cls._has_negation_near(lower, inv_pat, check_suffix=True))

    @classmethod
    def _build_investigation_plan(cls) -> List[Dict[str, Any]]:
        """Default collaborative investigation team: Detective (trace/root cause) + Coroner (visual reproduction)."""
        return [
            {
                "agent": "detective",
                "goal": "Investigate symptoms, trace root cause, and identify reproduction steps",
            },
            {
                "agent": "coroner",
                "goal": "Reproduce the issue and capture visual screenshot/video evidence",
            },
        ]

    @classmethod
    def _detect_agent_plan(cls, text: str) -> List[Dict[str, Any]]:
        """Detect explicit user instructions to organize >= 2 agents in sequence."""
        if cls._is_question_about_agent(text):
            return []

        lower = text.lower()
        dispatch_cue = re.search(
            r"\b(organize|orchestrate|coordinate|assign|dispatch|tell|ask|have|let|run|start|trigger|use|call|send|activate|invoke|sequence|pipeline|chain)\b"
            r"|\bfirst\b[^.!?\n]{0,60}\bthen\b",
            lower,
        )
        if not dispatch_cue:
            return []

        agent_patterns = [
            ("auto_test", r"\b(auto[\s-]?test|autonomous\s+(bug\s+)?(discovery|testing|test))\b"),
            ("detective", r"\bdetective\b"),
            ("coroner", r"\bcoroner\b"),
            ("judge", r"\bjudge\b"),
            ("reporter", r"\breporter\b"),
            ("sheriff", r"\bsheriff\b"),
            ("watcher", r"\bwatcher\b"),
            ("sanity", r"\bsanity(\s+testers?)?\b"),
        ]

        mentions: List[tuple[int, str]] = []
        wants_jira = cls._user_explicitly_requested_jira(text)

        for agent_key, pat in agent_patterns:
            for m in re.finditer(pat, text, re.IGNORECASE):
                if agent_key == "reporter":
                    pre = lower[max(0, m.start() - 16) : m.start()]
                    post = lower[m.end() : min(len(lower), m.end() + 12)]
                    if re.search(r"\b(jira|default)\s+$", pre) or re.match(r"^\s+field\b", post):
                        continue
                win_start = max(0, m.start() - 60)
                win_end = min(len(text), m.end() + 60)
                snippet = text[win_start:win_end]
                if cls._has_negation_near(snippet, pat, check_suffix=True):
                    continue
                if agent_key == "sheriff" and not wants_jira:
                    continue
                mentions.append((m.start(), agent_key))

        mentions.sort(key=lambda item: item[0])
        ordered: List[str] = []
        for _, key in mentions:
            if key not in ordered:
                ordered.append(key)

        if len(ordered) >= 2:
            return [{"agent": key, "goal": ""} for key in ordered]
        return []

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
            ("reporter", r"(?<!default\s)(?<!jira\s)\breporter\b"),
            ("watcher", r"\bwatcher\b"),
            ("sanity", r"\bsanity(\s+testers?)?\b"),
        ]
        dispatch_verb = re.compile(
            r"\b(assign|dispatch|tell|ask|have|let|run|start|trigger|use|call|send|activate|invoke)\b",
            re.IGNORECASE,
        )

        for agent_key, pat in agent_patterns:
            if re.search(pat, lower) and not cls._has_negation_near(lower, pat, check_suffix=True):
                if agent_key == "reporter":
                    for m in re.finditer(pat, lower):
                        pre = lower[max(0, m.start() - 16) : m.start()]
                        post = lower[m.end() : min(len(lower), m.end() + 12)]
                        if re.search(r"\b(jira|default)\s+$", pre) or re.match(r"^\s+field\b", post):
                            continue
                        if dispatch_verb.search(lower):
                            return agent_key
                    continue
                if agent_key == "auto_test" or dispatch_verb.search(lower):
                    return agent_key

        # Goal-based single-agent triggers when not a read-only question
        if not re.match(r"^(verify|inspect|check|audit|show|list)\b", lower):
            if re.search(r"\b(retest|re-?test|watcher\s*(?:scan|sweep|audit)|scan\s+(?:all\s+)?(?:modules?|agents?|logs?|everything|system))\b", lower) and not cls._has_negation_near(lower, r"\b(retest|re-?test|watcher|scan|sweep)\b", check_suffix=True):
                return "watcher"
            if re.search(r"\b(sanity\s+checks?|regression\s+(?:checks?|tests?))\b", lower) and not cls._has_negation_near(lower, r"\b(sanity|regression)\b", check_suffix=True):
                return "sanity"
            if re.search(r"\b(capture\s+(?:visual\s+)?evidence|take\s+(?:a\s+)?screenshot)\b", lower) and not cls._user_requested_main_workflow(lower):
                return "coroner"
        return None

    @classmethod
    def _is_skill_upload(cls, text: str) -> Optional[str]:
        stripped = (text or "").strip()
        lower = stripped.lower()
        if cls._user_requested_main_workflow(text) or cls._detect_explicit_agent_assignment(text) or cls._detect_agent_plan(text):
            return None
        if cls._is_question_about_agent(lower) or (
            lower.endswith("?")
            and re.match(r"^(what|which|how|show|view|get|tell|list|did|do|have|has|is|are|can|could|will|remember)\b", lower)
        ):
            return None
        has_template_cue = bool(
            re.search(
                r"\b(?:bug\s+)?report\s+(?:format|template)\b"
                r"|\bformat\s+of\s+(?:the\s+)?(?:bug\s+)?report\b"
                r"|\bfollow\s+this\s+(?:format|template)\b",
                lower,
            )
        )
        has_multiline_or_colon = ("\n" in stripped) or (":" in stripped)
        if (
            has_template_cue
            and has_multiline_or_colon
            and len(lower) > 25
            and not re.match(r"^(what|show|view|get|tell me|how|list|check|remember)\b", lower)
        ):
            return "report_format"
        if lower.startswith(("requirement:", "user story:", "business logic:")):
            return "requirement"
        return None

    _BUG_BOUNDARY_MARKERS = (
        "### 🎯 Bug Report #",
        "### 🏷️ Sheriff Jira Ticket Created",
        "🏷️ **Sheriff**: Raised Jira",
        "### 🤖 Autonomous Bug Discovery Complete",
        "Authored formatted bug report for Bug #",
    )

    @classmethod
    def _slice_history_since_last_completed_report(
        cls,
        conversation_history: Optional[List[Dict[str, Any]]],
    ) -> List[Dict[str, Any]]:
        """Return only turns after the most recent completed bug report / Jira raise boundary."""
        if not conversation_history:
            return []
        recent = list(conversation_history[-14:])
        cut_idx = 0
        for idx, item in enumerate(recent):
            sender = (item.get("sender") or item.get("role") or "").lower()
            if sender == "user" or item.get("isStatus"):
                continue
            content = item.get("content") or ""
            if any(marker in content for marker in cls._BUG_BOUNDARY_MARKERS):
                cut_idx = idx + 1
        return recent[cut_idx:]

    @classmethod
    def _collect_active_attachments(
        cls,
        attachments: Optional[List[Dict[str, Any]]] = None,
        conversation_history: Optional[List[Dict[str, Any]]] = None,
        include_binary_from_history: bool = True,
    ) -> List[Dict[str, Any]]:
        """Gather unique attachments from post-boundary history + current turn."""
        collected: List[Dict[str, Any]] = []
        seen = set()

        def _add(att: Dict[str, Any]):
            if not isinstance(att, dict):
                return
            key = att.get("stored_name") or att.get("id") or att.get("name")
            if not key or key in seen:
                return
            seen.add(key)
            collected.append(att)

        active_history = cls._slice_history_since_last_completed_report(conversation_history)
        history_with_atts = [
            item for item in active_history
            if (item.get("sender") or item.get("role") or "").lower() == "user"
            and not item.get("isStatus")
            and item.get("attachments")
        ]
        # Hydrate binary bytes only for the 2 most recent attachment turns in history to keep payloads lean
        binary_threshold = max(0, len(history_with_atts) - 2)
        for idx, item in enumerate(history_with_atts):
            allow_bin = include_binary_from_history and idx >= binary_threshold
            for att in hydrate_history_attachments(item, include_binary=allow_bin):
                _add(att)

        for att in attachments or []:
            _add(att)

        return collected

    @classmethod
    def _classify_small_talk(cls, text: str) -> Optional[str]:
        """Classify greetings and short acknowledgements so they aren't treated as bug notes."""
        normalized = " ".join(re.sub(r"[^\w'\s]", " ", (text or "").lower()).split())
        if not normalized or len(normalized.split()) > 6:
            return None
        if normalized in _ACK_PHRASES:
            return "ack"
        if normalized in _GREETING_PHRASES:
            return "greeting"
        # Extended elongated greetings like 'hiii' / 'heyyy' / 'hi how are you'
        first_word = normalized.split()[0]
        if re.match(r"^(hi+|hello+|hey+)$", first_word) and not _BUG_SIGNAL_RE.search(normalized):
            return "greeting"
        return None

    @classmethod
    def _synthesize_bug_context(
        cls,
        message: str,
        conversation_history: Optional[List[Dict[str, Any]]] = None,
        attachments: Optional[List[Dict[str, Any]]] = None,
    ) -> str:
        """Combine prior substantive chat discussion and attached files/images about the active bug."""
        chunks: List[str] = []
        active_history = cls._slice_history_since_last_completed_report(conversation_history)
        if active_history:
            user_notes: List[str] = []
            for item in active_history:
                sender = (item.get("sender") or item.get("role") or "").lower()
                content = (item.get("content") or "").strip()
                if not content or item.get("isStatus"):
                    continue
                if sender == "user" and not cls._classify_small_talk(content):
                    user_notes.append(content)
            if user_notes:
                chunks.append("Prior bug discussion from user:\n" + "\n---\n".join(user_notes))

        trimmed = message.strip()
        if trimmed and not cls._classify_small_talk(trimmed):
            chunks.append(f"Latest user instruction:\n{trimmed}")
        elif not chunks and trimmed:
            chunks.append(f"Latest user instruction:\n{trimmed}")

        all_atts = cls._collect_active_attachments(attachments, conversation_history, include_binary_from_history=False)
        if all_atts:
            att_lines = [format_attachment_text_block(att, max_chars=2500) for att in all_atts]
            if att_lines:
                chunks.append("Attached Files & Images:\n" + "\n\n".join(att_lines))

        return "\n\n".join(chunks)

    @classmethod
    def _build_llm_messages(
        cls,
        message: str,
        conversation_history: Optional[List[Dict[str, Any]]] = None,
        attachments: Optional[List[Dict[str, Any]]] = None,
    ) -> List[Dict[str, Any]]:
        """Build clean multi-turn multimodal user/assistant chat messages for the LLM."""
        turns: List[Dict[str, Any]] = []
        recent_hist = list((conversation_history or [])[-16:])
        active_tail_ids = {
            id(item) for item in cls._slice_history_since_last_completed_report(conversation_history)
        }
        hist_att_indices = [
            idx for idx, item in enumerate(recent_hist)
            if id(item) in active_tail_ids
            and (item.get("sender") or item.get("role") or "").lower() == "user"
            and not item.get("isStatus")
            and item.get("attachments")
        ]
        recent_binary_hist_indices = set(hist_att_indices[-2:])

        for idx, item in enumerate(recent_hist):
            if item.get("isStatus"):
                continue
            raw_content = _ANY_DESK_TAG_RE.sub("", (item.get("content") or "")).strip()
            sender = (item.get("sender") or item.get("role") or "").lower()
            role = "user" if sender == "user" else "assistant"

            hist_atts: List[Dict[str, Any]] = []
            if role == "user" and item.get("attachments"):
                allow_bin = idx in recent_binary_hist_indices and not attachments
                hist_atts = hydrate_history_attachments(item, include_binary=allow_bin)
                if hist_atts:
                    att_text = "\n\n".join(format_attachment_text_block(a, max_chars=2200) for a in hist_atts)
                    raw_content = f"{raw_content}\n\n{att_text}".strip()

            if not raw_content and not hist_atts:
                continue

            agent_label = item.get("agent")
            if role == "assistant" and agent_label and agent_label != "Desk Officer":
                raw_content = f"[{agent_label} update]\n{raw_content}"

            turn_media = [
                a for a in hist_atts
                if a.get("data_base64") and (a.get("is_image") or a.get("mime_type") == "application/pdf")
            ]

            if turns and turns[-1]["role"] == role:
                if raw_content:
                    turns[-1]["content"] = (turns[-1]["content"] + "\n\n" + raw_content).strip()
                if turn_media:
                    turns[-1].setdefault("attachments", []).extend(turn_media)
            else:
                entry: Dict[str, Any] = {
                    "role": role,
                    "content": raw_content or "Please inspect the attached file(s).",
                }
                if turn_media:
                    entry["attachments"] = list(turn_media)
                turns.append(entry)

        current_content = message.strip()
        if not current_content and attachments:
            current_content = "Please inspect and analyze the attached image(s)/file(s), extract key details or errors, and tell me what you find."

        current_media: List[Dict[str, Any]] = []
        if attachments:
            att_blocks = [format_attachment_text_block(att, max_chars=5000) for att in attachments]
            current_content = (f"{current_content}\n\n" + "\n\n".join(att_blocks)).strip()
            current_media = [
                a for a in attachments
                if a.get("data_base64") and (a.get("is_image") or a.get("mime_type") == "application/pdf")
            ]

        if turns and turns[-1]["role"] == "user":
            turns[-1]["content"] = (turns[-1]["content"] + "\n\n" + current_content).strip()
            if current_media:
                turns[-1].setdefault("attachments", []).extend(current_media)
        else:
            last_entry: Dict[str, Any] = {"role": "user", "content": current_content}
            if current_media:
                last_entry["attachments"] = list(current_media)
            turns.append(last_entry)

        # Ensure conversation starts with a user turn (skip leading seeded greeting if present cleanly)
        if turns and turns[0]["role"] == "assistant":
            turns = turns[1:] or [
                {
                    "role": "user",
                    "content": current_content,
                    **({"attachments": list(current_media)} if current_media else {}),
                }
            ]
        return turns

    @staticmethod
    def _extract_suggested_actions(
        action: str,
        target_agent: str,
        recent_bugs: List[Dict[str, Any]],
        has_bug_context: bool,
    ) -> List[str]:
        """Provide contextual interactive quick-reply pills for the UI."""
        unraised = next(
            (
                b
                for b in recent_bugs
                if b.get("status") != "resolved"
                and (not b.get("jira_ticket_id") or not _JIRA_KEY_RE.fullmatch(str(b.get("jira_ticket_id")).strip().upper()))
            ),
            None,
        )
        if action in ("run_main_workflow", "organize_agents"):
            if unraised:
                return [
                    f"Tell Sheriff to raise bug #{unraised['id']} to Backlog",
                    "Show me all built-in agents",
                ]
            return ["Check my Jira In QA list", "Show me all built-in agents"]
        if action == "assign_agent" and target_agent == "detective":
            return ["Have Coroner capture evidence", "Create a bug report"]
        if unraised and has_bug_context:
            return [
                "Create a bug report",
                "Organize Detective, Coroner, and Reporter for this bug",
                f"Tell Sheriff to raise bug #{unraised['id']} to Backlog",
            ]
        if has_bug_context:
            return [
                "Assign Detective to investigate",
                "Organize Detective, Coroner, and Reporter for this bug",
                "Create a bug report",
            ]
        return [
            "Check my Jira In QA list",
            "Show all project assets and built-in agents",
            "Run Auto-Test discovery",
        ]

    @staticmethod
    def _analyze_bug_symptoms(text: str, project_info: Dict[str, Any]) -> Dict[str, Any]:
        """Extract concrete technical clues from user messages for smart triage replies."""
        lower = text.lower()
        http_codes = re.findall(r"\b([45]\d{2})\b", text)
        urls = re.findall(r"https?://[^\s)]+|/[a-zA-Z0-9_./-]{2,}", text)

        areas = []
        area_keywords = {
            "Authentication / Login": ("login", "auth", "signin", "sign in", "password", "token", "jwt", "session", "otp"),
            "Checkout & Cart / Payments": ("cart", "checkout", "voucher", "coupon", "discount", "payment", "stripe", "order", "price", "total"),
            "UI / Frontend Layout": ("button", "modal", "form", "click", "ui", "css", "render", "screen", "blank", "overflow", "responsive"),
            "Backend API / Server": ("api", "endpoint", "500", "502", "503", "504", "400", "404", "422", "server", "payload", "json", "cors"),
            "Database / Persistence": ("db", "database", "sql", "save", "saved", "persist", "duplicate", "null", "missing"),
            "Performance / Timeout": ("slow", "timeout", "hang", "freeze", "latency", "loading"),
        }
        for area, kws in area_keywords.items():
            if any(k in lower for k in kws):
                areas.append(area)

        has_steps = bool(
            re.search(
                r"\b(step|steps|when|after|before|click|clicking|open|opening|enter|entering|submit|submitting|apply|applying|select|selecting|mark|marking|1\.|->)\b",
                lower,
            )
        )
        has_expected_actual = bool(
            re.search(
                r"\b(expected|should|instead|actual|but\s+it|fails?\s+to|doesn'?t|does\s+not|returns?|shows?|stays?|remains?)\b",
                lower,
            )
        )

        missing_questions = []
        if not has_steps:
            missing_questions.append("What exact user steps or input trigger it?")
        if not has_expected_actual:
            missing_questions.append("What was the **expected behavior** vs what actually happened?")
        if not urls and project_info.get("app_url"):
            missing_questions.append(f"Which specific page/route under `{project_info['app_url']}` or API endpoint is affected?")
        elif not urls:
            missing_questions.append("Which page URL or API endpoint did this happen on?")
        if not http_codes and ("Backend API / Server" in areas or "Checkout & Cart / Payments" in areas):
            missing_questions.append("Do you see a specific HTTP status code or browser console error?")

        return {
            "http_codes": list(dict.fromkeys(http_codes)),
            "urls": list(dict.fromkeys(urls))[:3],
            "areas": areas,
            "missing_questions": missing_questions[:3],
            "has_steps": has_steps,
            "has_expected_actual": has_expected_actual,
        }

    @classmethod
    def _strip_command_phrases(cls, text: str) -> str:
        """Remove command wrappers (e.g. 'Create a bug report', 'Investigate this') to isolate actual bug substance."""
        cleaned = (text or "").strip()
        patterns = (
            r"\b(?:please|kindly|could you|can you|would you)\b",
            r"\b(?:create|generate|make|build|prepare|start|run)\s+(?:a\s+|the\s+|this\s+)?bug\s*report\b",
            r"\b(?:start|run|execute|trigger|launch)\s+(?:the\s+)?(?:main|full|qa)?\s*(?:workflow|pipeline)\b",
            r"\b(?:investigate|trace|debug|reproduce|look\s+into|find\s+(?:the\s+|out\s+)?(?:root\s*cause|why))\b(?:\s+(?:this|the|that|it))?(?:\s+(?:bug|issue|error|problem|defect))?",
            r"\b(?:assign|dispatch|tell|ask|have|let|use|call|send|organize|coordinate)\s+(?:the\s+)?(?:detective|coroner|judge|reporter|sheriff|watcher|sanity(?:\s+testers?)?)\b(?:\s+(?:to|for))?",
            r"\b(?:raise|push|submit|log|file|open)\s+(?:a\s+|the\s+|this\s+|that\s+|new\s+|fresh\s+)?(?:bug\s*)?(?:report|ticket|issue|it)\b(?:\s+(?:to|in|into)\s+(?:jira|backlog|scrum(?:\s+backlog)?))?",
            r"\b(?:to\s+(?:analyze|investigate|capture|verify|check|retest))\b(?:\s+(?:this|the)\s+(?:bug|issue))?",
        )
        for pat in patterns:
            cleaned = re.sub(pat, " ", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"[:.,;!?\"'()\-–—]+", " ", cleaned)
        return " ".join(cleaned.split())

    @classmethod
    def _extract_substantive_bug_turns(
        cls,
        message: str,
        conversation_history: Optional[List[Dict[str, Any]]],
    ) -> List[str]:
        """Collect user turns for the active bug (post last completed report) + current message."""
        turns: List[str] = []
        raw_user_turns: List[str] = []
        for item in cls._slice_history_since_last_completed_report(conversation_history):
            sender = (item.get("sender") or item.get("role") or "").lower()
            content = (item.get("content") or "").strip()
            if sender == "user" and content and not item.get("isStatus"):
                raw_user_turns.append(content)
        if message.strip():
            raw_user_turns.append(message.strip())

        for t in raw_user_turns:
            lower = t.lower()
            if cls._classify_small_talk(lower):
                continue
            if cls._is_question_about_agent(lower):
                continue
            if re.match(r"^(verify|inspect|check|audit|show|list|read)\b", lower) and not re.search(
                r"\b(500|502|503|504|400|401|403|404|422|error|fails?|broken|exception|crash)\b", lower
            ):
                continue
            sub = cls._strip_command_phrases(t)
            if len(sub) >= 12:
                turns.append(t)
        return turns

    def _assess_bug_clarity(
        self,
        message: str,
        conversation_history: Optional[List[Dict[str, Any]]],
        project_info: Dict[str, Any],
        bug_id: Optional[int],
        jira_key: Optional[str],
        attachments: Optional[List[Dict[str, Any]]],
    ) -> tuple[bool, str, List[str]]:
        """Check whether critical bug details are still missing before dispatching investigation or report generation."""
        has_active_attachments = bool(attachments) or bool(
            self._collect_active_attachments(None, conversation_history, include_binary_from_history=False)
        )
        if bug_id is not None or jira_key or has_active_attachments:
            return False, "", []

        if re.search(
            r"\b(anyway|just\s+(?:run|do|start|investigate|create)|with\s+what\s+we\s+have|go\s+ahead|proceed)\b",
            message or "",
            re.IGNORECASE,
        ):
            return False, "", []

        bug_turns = self._extract_substantive_bug_turns(message, conversation_history)
        if not bug_turns:
            reply = (
                "I can kick off the agents for you right away — first, please share a quick description of the bug:\n\n"
                "- What **failed or behaved unexpectedly** (expected vs. actual behavior)?\n"
                "- What **exact steps or inputs** reproduce it (and which screen, URL, or API endpoint)?\n"
                "- Or tell me a **Bug `#`** / **Jira key** (like `SCRUM-xxx`) to investigate."
            )
            return True, reply, ["Describe steps + expected vs actual", "Show tracked bugs"]

        combined_substantive = "\n".join(self._strip_command_phrases(t) for t in bug_turns)
        analysis = self._analyze_bug_symptoms(combined_substantive, project_info)
        signals = (
            (1 if analysis["has_steps"] else 0)
            + (1 if analysis["has_expected_actual"] else 0)
            + (1 if analysis["http_codes"] else 0)
            + (1 if analysis["urls"] else 0)
        )

        prior_turns = self._extract_substantive_bug_turns("", conversation_history)
        current_substantive = self._strip_command_phrases(message)
        current_is_pure_command = len(current_substantive) < 12

        if (
            len(bug_turns) >= 2
            or len(combined_substantive) >= 140
            or signals >= 2
            or (prior_turns and current_is_pure_command and (signals >= 1 or len(combined_substantive) >= 80))
        ):
            return False, "", []

        snippet = bug_turns[-1].strip().splitlines()[0][:110]
        qs = analysis["missing_questions"] or [
            "What exact steps trigger this issue?",
            "What was the **expected behavior** vs. what actually happened?",
            "Which page URL, API endpoint, or error message did you observe?",
        ]
        questions_md = "\n".join(f"- {q}" for q in qs[:3])
        reply = (
            f"Before I assign the team for **\"{snippet}\"**, could you clarify a couple of details so the agents investigate accurately?\n\n"
            f"{questions_md}\n\n"
            "Once you reply (or say **\"go ahead anyway\"**), I'll dispatch the right agents immediately."
        )
        return True, reply, ["Go ahead and investigate anyway", "Show tracked bugs"]

    @staticmethod
    def _format_offline_assets_overview(
        project_info: Dict[str, Any],
        asset_snapshot: Optional[Dict[str, Any]],
        prefetched_results: Optional[List[Dict[str, Any]]],
    ) -> Optional[str]:
        """Format grounded offline replies when querying Jira or project assets."""
        prefetched = prefetched_results or []

        for res in prefetched:
            if res.get("tool") == "jira_get_issue":
                if not res.get("ok"):
                    return f"Couldn't fetch Jira issue `{res.get('issue_key')}`: {res.get('error')}"
                iss = res["issue"]
                comments = iss.get("comments") or []
                comment_lines = "\n".join(
                    f"  - **{c.get('author') or 'User'}:** {c.get('body', '')[:160]}"
                    for c in comments
                )
                return (
                    f"### Jira Issue [{iss.get('key')}]({iss.get('url')})\n"
                    f"- **Summary:** {iss.get('summary')}\n"
                    f"- **Status:** `{iss.get('status')}` | **Priority:** `{iss.get('priority') or 'Medium'}` | **Type:** `{iss.get('issue_type') or 'Bug'}`\n"
                    f"- **Assignee:** `{iss.get('assignee') or 'Unassigned'}` | **Reporter:** `{iss.get('reporter') or 'N/A'}`\n"
                    f"- **Description:** {(iss.get('description') or 'None')[:600]}\n"
                    + (f"- **Recent Comments:**\n{comment_lines}" if comment_lines else "")
                )

            if res.get("tool") == "jira_search":
                if not res.get("ok"):
                    return f"Couldn't query Jira Cloud: {res.get('error')}"
                issues = res.get("issues") or []
                status_lbl = res.get("status_filter") or "Matching"
                proj_keys = ", ".join(res.get("queried_project_keys") or []) or "All"
                lines = [
                    f"### Jira Cloud — `{status_lbl}` Issues (`{len(issues)}` found across `{proj_keys}`)"
                ]
                if res.get("configured_key_valid_on_jira") is False and res.get("available_jira_projects"):
                    avail = ", ".join(f"`{p['key']}` ({p['name']})" for p in res["available_jira_projects"])
                    lines.append(
                        f"> Notice: Project setting Jira Key `{res.get('configured_project_key')}` wasn't found on Jira; "
                        f"queried accessible project(s): {avail}."
                    )
                if not issues:
                    lines.append("No Jira issues matched that query.")
                else:
                    for iss in issues[:15]:
                        lines.append(
                            f"- **[{iss['key']}]({iss['url']})** `{iss.get('status')}` ({iss.get('priority') or 'Medium'}) — {iss.get('summary')}"
                        )
                return "\n".join(lines)

        if asset_snapshot:
            skills = asset_snapshot.get("skills") or []
            bugs = asset_snapshot.get("bugs") or []
            ev = asset_snapshot.get("evidence") or []
            integ = asset_snapshot.get("integrations") or {}
            jira_cfg = integ.get("jira") or {}
            return (
                f"### Project Assets Inventory — **{project_info.get('name')}**\n"
                f"- **App URL:** `{project_info.get('app_url') or 'Not set'}` | **Repo URL:** `{project_info.get('repo_url') or 'Not set'}`\n"
                f"- **Jira Cloud:** `{'Connected' if jira_cfg.get('live_configured') else 'Not configured'}` (Project Key: `{project_info.get('jira_project_key') or 'QA'}`)\n"
                f"- **Stored Skills ({len(skills)}):** {', '.join(f'#{s['id']} {s['title']}' for s in skills[:6]) or 'None'}\n"
                f"- **Tracked Bug Reports ({len(bugs)}):** {', '.join(f'#{b['id']} ({b['status']})' for b in bugs[:6]) or 'None'}\n"
                f"- **Evidence Files ({len(ev)}):** {', '.join(e['file_name'] for e in ev[:6]) or 'None'}"
            )
        return None

    def _build_contextual_offline_reply(
        self,
        message: str,
        conversation_history: Optional[List[Dict[str, Any]]],
        project_info: Dict[str, Any],
        recent_bugs: List[Dict[str, Any]],
        bug_id: Optional[int],
        llm_error: Optional[str] = None,
        asset_snapshot: Optional[Dict[str, Any]] = None,
        prefetched_results: Optional[List[Dict[str, Any]]] = None,
        attachments: Optional[List[Dict[str, Any]]] = None,
    ) -> tuple[str, List[str]]:
        """Generate a dynamic, context-aware conversational response when offline/unconfigured."""
        lower = message.strip().lower()
        proj_name = project_info.get("name", "this project")
        active_history = self._slice_history_since_last_completed_report(conversation_history)
        user_turns = [
            (h.get("content") or "").strip()
            for h in active_history
            if (h.get("sender") or h.get("role") or "").lower() == "user" and not h.get("isStatus")
        ]

        # 0. Pre-fetched live Jira / specific asset inspection results
        if prefetched_results and any(
            r.get("tool") in ("jira_search", "jira_get_issue", "jira_list_projects") for r in prefetched_results
        ):
            jira_md = self._format_offline_assets_overview(project_info, asset_snapshot, prefetched_results)
            if jira_md:
                return jira_md, [
                    "Assign Detective to investigate SCRUM-850",
                    "Show all project assets and built-in agents",
                ]

        # 1. Greetings & short acknowledgements
        small_talk = self._classify_small_talk(message)
        if small_talk == "greeting":
            unraised_count = sum(1 for b in recent_bugs if not b.get("jira_ticket_id") and b.get("status") != "resolved")
            bug_line = (
                f"You currently have **{len(recent_bugs)}** tracked defect(s) in **{proj_name}**"
                + (f" ({unraised_count} awaiting your explicit approval to raise to Jira Backlog)." if unraised_count else ".")
                if recent_bugs
                else f"No defects are logged in **{proj_name}** yet."
            )
            reply = (
                f"Hey! I'm your **Desk Officer** for **{proj_name}**.\n\n"
                f"{bug_line}\n\n"
                "I have live access to all project assets (Jira Cloud tickets, skills, bug reports, visual evidence, and agent logs) "
                "plus our full agent team (**Detective, Coroner, Judge, Reporter, Sheriff, Watcher, Sanity Testers**). "
                "Describe a bug, inspect Jira, organize any sequence of agents, or say **\"Create a bug report\"** when ready."
            )
            return reply, [
                "Check my Jira In QA list",
                "Show all project assets and built-in agents",
                "Run Auto-Test discovery",
            ]
        if small_talk == "ack":
            reply = (
                "Got it! Let me know whenever you want to discuss another bug, investigate an issue, "
                "say **\"Create a bug report\"**, or assign any QA agent."
            )
            return reply, [
                "Check my Jira In QA list",
                "Show all project assets and built-in agents",
                "Create a bug report",
            ]

        # 2. Questions about specific built-in agent or full roster / project assets
        for meta in AGENT_ROSTER:
            if re.search(rf"\b{re.escape(meta['key'])}\b|\b{re.escape(meta['name'].lower())}\b", lower) and self._is_question_about_agent(lower):
                extra = (
                    " (When asked to raise a ticket, it always lands in Jira **Backlog**, never **To Do**.)"
                    if meta["key"] == "sheriff"
                    else ""
                )
                reply = (
                    f"**{meta['name']}** — *{meta['role']}*\n\n"
                    f"{meta['description']}{extra}\n\n"
                    f"Would you like me to assign **{meta['name']}** right now, or keep discussing the issue first?"
                )
                sample_cmd = (
                    "Tell Sheriff to raise bug report to Backlog"
                    if meta["key"] == "sheriff"
                    else f"Assign {meta['name']} to start working"
                )
                return reply, [sample_cmd, "List all built-in agents and roles"]

        if re.search(r"\b(report\s+format|bug\s+report\s+template|template)\b", lower) and self._is_question_about_agent(lower):
            active_rf = strip_report_format_preamble(project_info.get("report_format"))
            if not active_rf and asset_snapshot:
                for s in reversed(asset_snapshot.get("skills") or []):
                    if s.get("type") == "report_format" and (s.get("content") or "").strip():
                        if s.get("title") != "Default QA Bug Report Format" or not active_rf:
                            active_rf = strip_report_format_preamble(s.get("content"))
                            if s.get("title") != "Default QA Bug Report Format":
                                break
            if active_rf:
                return (
                    f"Yes — **Project Configuration -> Report Format** for **{proj_name}** is currently set to:\n\n"
                    f"```\n{active_rf}\n```\n\n"
                    "Detective, Judge, and Reporter follow this exact structure as-is during bug report creation."
                ), ["Create a bug report", "Show all project assets and built-in agents"]
            return (
                f"No custom **Report Format** is set in **{proj_name}** Project Configuration yet (currently using Standard QA Markdown format). "
                "You can set one anytime under **Projects -> Edit Project -> Report Format** or paste it directly here."
            ), ["Show all project assets and built-in agents"]

        if re.search(r"\b(assets?|inventory|skills?|evidence|integrations?|agents?|roster|who can|roles?|capabilities|team|what can you do|how.*organize)\b", lower):
            parts: List[str] = []
            if re.search(r"\b(agents?|roster|who can|roles?|capabilities|team|organize|what can you do)\b", lower):
                parts.append(format_agent_roster())
            if re.search(r"\b(assets?|inventory|skills?|evidence|integrations?|everything|access)\b", lower):
                assets_md = self._format_offline_assets_overview(project_info, asset_snapshot, prefetched_results)
                if assets_md:
                    parts.append(assets_md)
            if not parts:
                parts.append(format_agent_roster())
            parts.append(
                "Right now we're in pre-workflow discussion — tell me which agent(s) to assign or organize, query Jira, or say **\"Create a bug report\"** when ready."
            )
            return "\n\n".join(parts), [
                "Check my Jira In QA list",
                "Organize Detective, Coroner, and Reporter for a bug",
                "Create a bug report",
            ]

        # 3. Query about tracked bugs or a specific bug #id
        if bug_id and re.search(r"\b(status|details|what|show|about|summary|check)\b", lower):
            target = next((b for b in recent_bugs if b.get("id") == bug_id), None)
            if target:
                jira_str = target.get("jira_ticket_id") or "Not raised yet (Sheriff standing by)"
                reply = (
                    f"Here's the current state of **Bug #{bug_id}** (*{target.get('title')}*):\n"
                    f"- **Status:** `{target.get('status')}` | **Severity:** `{target.get('severity')}` | **Module:** `{target.get('module')}`\n"
                    f"- **Root Cause:** {target.get('root_cause') or 'Pending analysis'}\n"
                    f"- **Jira Ticket:** `{jira_str}`"
                )
                actions = (
                    [f"Tell Sheriff to raise bug #{bug_id} to Backlog", f"Ask Watcher to retest bug #{bug_id}"]
                    if not target.get("jira_ticket_id")
                    else [f"Ask Watcher to retest bug #{bug_id}", f"Ask Sanity Testers to verify bug #{bug_id}"]
                )
                return reply, actions

        if re.search(r"\b(what bugs|list bugs|open bugs|summary|what did we discuss|recap)\b", lower):
            if recent_bugs:
                items = "\n".join(
                    f"- **#{b['id']}** `{b['status']}` — {b['title']} *(Jira: {b.get('jira_ticket_id') or 'Standby'})*"
                    for b in recent_bugs[:5]
                )
                reply = f"Here are the latest tracked bugs for **{proj_name}**:\n{items}"
            elif user_turns:
                reply = (
                    f"So far in this chat ({len(user_turns)} note(s)), you've described:\n"
                    + "\n".join(f"- {n[:180]}" for n in user_turns[-4:])
                    + "\n\nNo formal Bug Report has been created yet."
                )
            else:
                reply = f"We haven't logged any bugs or notes in **{proj_name}** yet. Describe an issue or paste an error to start triaging!"
            return reply, ["Create a bug report", "Assign Detective to investigate"]

        # 3b. Attachment analysis in offline/deterministic mode (images, PDFs, Office docs, HAR/logs, JSON/CSV)
        if attachments:
            att_sections: List[str] = [
                f"### 📎 Attachment Analysis ({len(attachments)} file(s))"
            ]
            has_image_att = False
            for att in attachments:
                name = att.get("name") or "attachment"
                summary = att.get("summary") or att.get("mime_type") or "File"
                is_img = bool(att.get("is_image"))
                if is_img:
                    has_image_att = True
                block_lines = [f"- **{name}** — {summary}"]
                hl = att.get("highlights") or []
                if hl:
                    block_lines.append("  - **Detected Error / Key Signals:** " + "; ".join(f"`{h[:100]}`" for h in hl[:5]))
                preview = (att.get("extracted_text") or att.get("content") or "").strip()
                if preview:
                    block_lines.append(f"  - **Extracted Content Preview:**\n```\n{preview[:900]}\n```")
                elif is_img:
                    block_lines.append(
                        "  - **Visual Note:** Image dimensions and format were validated and stored cleanly for chat/evidence. "
                        "Configure a vision-capable LLM (such as Gemini or GPT-4o in Settings) for deep pixel/OCR analysis."
                    )
                att_sections.append("\n".join(block_lines))

            if message.strip():
                att_sections.append(f"**Your Note:** {message.strip()[:250]}")
            att_sections.append(
                "You can say **\"Investigate this bug\"** or **\"Create a bug report\"** and I will include these attachment details"
                + (" (plus attach your screenshot as visual evidence)." if has_image_att else ".")
            )
            return "\n\n".join(att_sections), [
                "Investigate this bug",
                "Create a bug report",
                "Show all project assets and built-in agents",
            ]

        # 4. Bug triage & symptom analysis across turns
        combined_text = "\n".join(user_turns[-4:] + [message])
        analysis = self._analyze_bug_symptoms(combined_text, project_info)

        findings = []
        if analysis["areas"]:
            findings.append(f"**Likely Area:** {', '.join(analysis['areas'])}")
        if analysis["http_codes"]:
            findings.append(f"**HTTP Status Seen:** {', '.join(f'`{c}`' for c in analysis['http_codes'])}")
        if analysis["urls"]:
            findings.append(f"**Target Path/URL:** {', '.join(f'`{u}`' for u in analysis['urls'])}")

        latest_snippet = (message.strip().splitlines() or ["attached file"])[0][:140]
        findings_block = "\n".join(f"- {line}" for line in findings)
        questions_block = "\n".join(f"- {q}" for q in analysis["missing_questions"])

        if len(user_turns) == 0:
            header = f"Got it — looking at **\"{latest_snippet}\"** in **{proj_name}**."
        else:
            header = f"Thanks for the added context (**turn {len(user_turns) + 1}** in our triage for **{proj_name}**)."

        parts = [header]
        if findings_block:
            parts.append(f"**What I extracted so far:**\n{findings_block}")
        if analysis["missing_questions"]:
            parts.append(f"**To sharpen this before we run agents:**\n{questions_block}")
        else:
            parts.append(
                "You already gave clear steps, affected behavior, and technical signals — we have enough detail to investigate or generate a full report whenever you give the word."
            )

        if len(user_turns) == 0:
            parts.append(
                "When ready, say **\"Create a bug report\"** (`Detective → Coroner → Judge → Reporter`, with **Sheriff** standing by) or ask me to assign/organize specific agents."
            )

        actions = self._extract_suggested_actions("chat", "none", recent_bugs, has_bug_context=True)
        return "\n\n".join(parts), actions

    def _build_fallback_decision(
        self,
        message: str,
        conversation_history: Optional[List[Dict[str, Any]]],
        project_info: Dict[str, Any],
        recent_bugs: List[Dict[str, Any]],
        attachments: Optional[List[Dict[str, Any]]],
        llm_error: Optional[str] = None,
        asset_snapshot: Optional[Dict[str, Any]] = None,
        prefetched_results: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """Deterministic & context-aware decision builder for skill/command/team routing and offline chat."""
        jira_key = self._extract_jira_key(message)
        jira_project_key = self._extract_jira_project_key(message, None, project_info, asset_snapshot)
        force_new_ticket = self._user_requested_fresh_ticket(message)
        active_attachments = self._collect_active_attachments(attachments, conversation_history, include_binary_from_history=True)
        skill_type = None if attachments else self._is_skill_upload(message)
        if skill_type:
            return {
                "reply": "Got it — I'll store that as a project skill.",
                "action": "save_skill",
                "target_agent": "none",
                "agent_plan": [],
                "bug_summary": "",
                "bug_report_id": None,
                "jira_key": jira_key,
                "jira_project_key": jira_project_key,
                "force_new_ticket": False,
                "also_raise_jira": False,
                "skill_type": skill_type,
                "skill_title": "Custom Bug Report Format" if skill_type == "report_format" else message.split("\n")[0][:60],
                "suggested_actions": ["Assign Detective to investigate", "Create a bug report"],
                "llm_mode": "deterministic",
                "llm_notice": None,
            }

        bug_context = self._synthesize_bug_context(message, conversation_history, attachments)
        agent_plan = self._detect_agent_plan(message)
        wants_workflow = self._user_requested_main_workflow(message)
        wants_jira = self._user_explicitly_requested_jira(message)
        explicit_agent = self._detect_explicit_agent_assignment(message)
        wants_investigation = (
            False
            if (agent_plan or wants_workflow or wants_jira or explicit_agent)
            else self._user_requested_investigation(message)
        )
        direct_bug_id = self._extract_bug_report_id(message)
        bugs_by_id = {b.get("id"): b for b in recent_bugs if b.get("id")}
        stripped_msg = self._strip_command_phrases(message)
        plan_agents = {s.get("agent") for s in (agent_plan or [])}
        uses_unsliced_history = bool(
            wants_jira
            or explicit_agent in ("sheriff", "watcher", "sanity", "judge", "reporter")
            or plan_agents.intersection({"sheriff", "watcher", "sanity", "judge", "reporter"})
            or (explicit_agent == "coroner" and not wants_investigation)
        )
        active_history = self._slice_history_since_last_completed_report(conversation_history)
        history_for_bug_id = (
            conversation_history
            if uses_unsliced_history
            else (active_history if (agent_plan or explicit_agent or wants_investigation or wants_workflow) else None)
        )

        bug_id = self._resolve_bug_id_from_context(
            message,
            None,
            history_for_bug_id,
            recent_bugs,
            prefer_unraised_project_key=(jira_project_key or project_info.get("jira_project_key")) if (explicit_agent == "sheriff" or wants_jira) else None,
        )

        # If wants_workflow / investigation resolved an older non-draft/already-reported bug from history (not explicitly requested), don't reuse it
        if (wants_workflow or wants_investigation) and not direct_bug_id and bug_id in bugs_by_id:
            cand = bugs_by_id[bug_id]
            if cand.get("status") != "draft" or cand.get("has_formatted_report"):
                bug_id = None

        active_draft_for_continuation = bool(
            wants_workflow
            and not direct_bug_id
            and len(stripped_msg) < 12
            and bug_id in bugs_by_id
            and bugs_by_id[bug_id].get("status") == "draft"
            and not bugs_by_id[bug_id].get("has_formatted_report")
        )

        # Clarification gate before running investigation or main workflow when details are empty/unclear
        if (
            not agent_plan
            and (wants_workflow or wants_investigation or explicit_agent == "detective")
            and not active_draft_for_continuation
        ):
            needs_clarify, clarify_reply, clarify_actions = self._assess_bug_clarity(
                message=message,
                conversation_history=conversation_history,
                project_info=project_info,
                bug_id=direct_bug_id,
                jira_key=jira_key,
                attachments=attachments,
            )
            if needs_clarify:
                return {
                    "reply": clarify_reply,
                    "action": "chat",
                    "target_agent": "none",
                    "agent_plan": [],
                    "bug_summary": bug_context,
                    "bug_report_id": None,
                    "jira_key": jira_key,
                    "jira_project_key": jira_project_key,
                    "force_new_ticket": False,
                    "also_raise_jira": False,
                    "skill_type": None,
                    "skill_title": None,
                    "attachments": [],
                    "suggested_actions": clarify_actions,
                    "llm_mode": "live" if self.llm.is_configured() and not llm_error else "fallback",
                    "llm_notice": llm_error,
                }

        if wants_investigation and not agent_plan and not explicit_agent:
            agent_plan = self._build_investigation_plan()

        if agent_plan:
            names_map = {a["key"]: a["name"] for a in AGENT_ROSTER}
            names_map["auto_test"] = "Auto-Test"
            plan_labels = " → ".join(names_map.get(s["agent"], s["agent"].title()) for s in agent_plan)
            intro = (
                f"Understood! Assigning our investigation team (**{plan_labels}**) to trace the root cause and capture visual evidence."
                if wants_investigation
                else f"Understood! Organizing the requested agent sequence: **{plan_labels}**."
            )
            return {
                "reply": (
                    intro
                    + (
                        " **Sheriff** is included to log the ticket to Jira **Backlog**."
                        if wants_jira and any(s["agent"] == "sheriff" for s in agent_plan)
                        else " (**Sheriff** remains on standby unless explicitly asked to raise to Jira Backlog.)"
                    )
                ),
                "action": "organize_agents",
                "target_agent": "none",
                "agent_plan": agent_plan,
                "bug_summary": bug_context,
                "bug_report_id": bug_id,
                "jira_key": jira_key,
                "jira_project_key": jira_project_key,
                "force_new_ticket": force_new_ticket,
                "also_raise_jira": wants_jira,
                "skill_type": None,
                "skill_title": None,
                "attachments": active_attachments,
                "suggested_actions": self._extract_suggested_actions("organize_agents", "none", recent_bugs, True),
                "llm_mode": "live" if self.llm.is_configured() and not llm_error else "fallback",
                "llm_notice": llm_error,
            }

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
                "agent_plan": [],
                "bug_summary": bug_context,
                "bug_report_id": bug_id,
                "jira_key": jira_key,
                "jira_project_key": jira_project_key,
                "force_new_ticket": force_new_ticket,
                "also_raise_jira": wants_jira,
                "skill_type": None,
                "skill_title": None,
                "attachments": active_attachments,
                "suggested_actions": self._extract_suggested_actions("run_main_workflow", "none", recent_bugs, True),
                "llm_mode": "live" if self.llm.is_configured() and not llm_error else "fallback",
                "llm_notice": llm_error,
            }

        if explicit_agent:
            agent_meta = next((a for a in AGENT_ROSTER if a["key"] == explicit_agent), None)
            agent_label = agent_meta["name"] if agent_meta else explicit_agent.replace("_", " ").title()
            role_label = agent_meta["role"] if agent_meta else "Autonomous Bug Discovery"
            return {
                "reply": f"Assigning **{agent_label}** (*{role_label}*) to handle this now.",
                "action": "assign_agent",
                "target_agent": explicit_agent,
                "agent_plan": [],
                "bug_summary": bug_context,
                "bug_report_id": bug_id,
                "jira_key": jira_key,
                "jira_project_key": jira_project_key,
                "force_new_ticket": force_new_ticket,
                "also_raise_jira": explicit_agent == "sheriff",
                "skill_type": None,
                "skill_title": None,
                "attachments": active_attachments,
                "suggested_actions": self._extract_suggested_actions("assign_agent", explicit_agent, recent_bugs, True),
                "llm_mode": "live" if self.llm.is_configured() and not llm_error else "fallback",
                "llm_notice": llm_error,
            }

        reply, suggested = self._build_contextual_offline_reply(
            message=message,
            conversation_history=conversation_history,
            project_info=project_info,
            recent_bugs=recent_bugs,
            bug_id=bug_id,
            llm_error=llm_error,
            asset_snapshot=asset_snapshot,
            prefetched_results=prefetched_results,
            attachments=attachments,
        )
        return {
            "reply": reply,
            "action": "chat",
            "target_agent": "none",
            "agent_plan": [],
            "bug_summary": bug_context,
            "bug_report_id": bug_id,
            "jira_key": jira_key,
            "jira_project_key": jira_project_key,
            "force_new_ticket": False,
            "also_raise_jira": False,
            "skill_type": None,
            "skill_title": None,
            "attachments": [],
            "suggested_actions": suggested,
            "llm_mode": "fallback",
            "llm_degraded": (not self.llm.is_configured()) or bool(llm_error),
            "llm_notice": llm_error or self.llm.configuration_issue(),
        }

    def _parse_streamed_or_full_llm_output(
        self,
        raw_output: str,
        message: str,
        conversation_history: Optional[List[Dict[str, Any]]],
        project_info: Dict[str, Any],
        recent_bugs: List[Dict[str, Any]],
        attachments: Optional[List[Dict[str, Any]]],
        asset_snapshot: Optional[Dict[str, Any]] = None,
        prefetched_results: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """Parse natural Markdown reply + optional trailing <!-- DESK_ACTION {...} --> tag."""
        fallback = self._build_fallback_decision(
            message,
            conversation_history,
            project_info,
            recent_bugs,
            attachments,
            asset_snapshot=asset_snapshot,
            prefetched_results=prefetched_results,
        )
        parsed_meta: Dict[str, Any] = {}
        tag_match = _ACTION_TAG_RE.search(raw_output)
        if tag_match:
            try:
                parsed_meta = json.loads(tag_match.group(1))
            except Exception as e:
                logger.warning(f"Failed parsing DESK_ACTION tag JSON: {e}")

        visible_reply = _ANY_DESK_TAG_RE.sub("", raw_output).strip()
        if not visible_reply:
            visible_reply = parsed_meta.get("reply") or fallback["reply"]

        action = parsed_meta.get("action", "chat")
        if action not in VALID_ACTIONS:
            action = "chat"

        target_agent = (parsed_meta.get("target_agent") or "none").lower()
        if target_agent not in VALID_AGENTS:
            target_agent = "none"

        allowed_plan_agents = VALID_AGENTS - {"none"}
        cleaned_plan: List[Dict[str, Any]] = []
        raw_plan = parsed_meta.get("agent_plan")
        if isinstance(raw_plan, list):
            for item in raw_plan:
                if isinstance(item, str):
                    ag = item.lower().strip()
                    if ag in allowed_plan_agents:
                        cleaned_plan.append({"agent": ag, "goal": ""})
                elif isinstance(item, dict):
                    ag = str(item.get("agent") or item.get("target_agent") or "").lower().strip()
                    if ag in allowed_plan_agents:
                        cleaned_plan.append({
                            "agent": ag,
                            "goal": str(item.get("goal") or "").strip(),
                            "bug_report_id": item.get("bug_report_id"),
                            "jira_key": item.get("jira_key"),
                        })

        # Deterministic command detection & guardrails
        llm_originally_chose_chat = action == "chat"
        is_question = self._is_question_about_agent(message)
        det_plan = self._detect_agent_plan(message)
        wants_workflow = self._user_requested_main_workflow(message)
        wants_jira = self._user_explicitly_requested_jira(message)
        explicit_agent = self._detect_explicit_agent_assignment(message)
        wants_investigation = (
            False
            if (is_question or det_plan or wants_workflow or wants_jira or explicit_agent)
            else self._user_requested_investigation(message)
        )

        if is_question:
            action = "chat"
            target_agent = "none"
            cleaned_plan = []
        else:
            if det_plan:
                if action in ("chat", "assign_agent") or not cleaned_plan:
                    action = "organize_agents"
                    if not cleaned_plan:
                        cleaned_plan = det_plan
            elif wants_workflow and action == "chat":
                action = "run_main_workflow"
            elif explicit_agent:
                # Override when LLM stayed in 'chat' OR proposed a Sheriff-less plan for a Jira request
                if action == "chat" or (explicit_agent == "sheriff" and action == "organize_agents" and not any(s.get("agent") == "sheriff" for s in cleaned_plan)):
                    action = "assign_agent"
                    target_agent = explicit_agent
                    if explicit_agent == "sheriff":
                        cleaned_plan = []
            elif wants_investigation and action == "chat":
                action = "organize_agents"
                cleaned_plan = self._build_investigation_plan()

        # Strict guardrail: Sheriff NEVER runs unless user explicitly asked
        also_raise_jira = bool(parsed_meta.get("also_raise_jira", False)) and wants_jira
        if wants_jira and action in ("run_main_workflow", "organize_agents"):
            also_raise_jira = True
        if action == "assign_agent" and target_agent == "sheriff" and wants_jira:
            also_raise_jira = True

        if not wants_jira:
            cleaned_plan = [s for s in cleaned_plan if s["agent"] != "sheriff"]

        if action == "organize_agents" and not cleaned_plan:
            action = "chat"
        if target_agent == "sheriff" and not wants_jira:
            action = "chat"
            target_agent = "none"

        jira_project_key = self._extract_jira_project_key(message, parsed_meta, project_info, asset_snapshot)
        raw_jira_key = parsed_meta.get("jira_key") or self._extract_jira_key(message)
        if isinstance(raw_jira_key, str) and _JIRA_KEY_RE.fullmatch(raw_jira_key.strip().upper()):
            jira_key = raw_jira_key.strip().upper()
        else:
            jira_key = self._extract_jira_key(message)

        direct_bug_id = self._extract_bug_report_id(message)
        bugs_by_id = {b.get("id"): b for b in recent_bugs if b.get("id")}
        stripped_msg = self._strip_command_phrases(message)
        plan_agents = {s.get("agent") for s in (cleaned_plan or [])}
        uses_unsliced_history = bool(
            wants_jira
            or also_raise_jira
            or target_agent in ("sheriff", "watcher", "sanity", "judge", "reporter")
            or plan_agents.intersection({"sheriff", "watcher", "sanity", "judge", "reporter"})
            or (target_agent == "coroner" and not wants_investigation)
        )
        active_history = self._slice_history_since_last_completed_report(conversation_history)
        if action == "chat":
            history_for_bug_id = None
        elif uses_unsliced_history:
            history_for_bug_id = conversation_history
        else:
            history_for_bug_id = active_history

        bug_id = self._resolve_bug_id_from_context(
            message,
            parsed_meta,
            history_for_bug_id,
            recent_bugs,
            prefer_unraised_project_key=(jira_project_key or project_info.get("jira_project_key")) if (target_agent == "sheriff" or also_raise_jira) else None,
        )
        if (action == "run_main_workflow" or wants_investigation) and not direct_bug_id and bug_id in bugs_by_id:
            cand = bugs_by_id[bug_id]
            if cand.get("status") != "draft" or cand.get("has_formatted_report"):
                bug_id = None

        # Clarification gate: if user asked to run workflow/investigation with unclear/empty details, ask first!
        if not is_question and not det_plan:
            is_inv_plan = action == "organize_agents" and (
                wants_investigation
                or [s.get("agent") for s in cleaned_plan] == ["detective", "coroner"]
            )
            is_det_or_workflow = action == "run_main_workflow" or (action == "assign_agent" and target_agent == "detective") or is_inv_plan
            active_draft_for_continuation = bool(
                action == "run_main_workflow"
                and not direct_bug_id
                and len(stripped_msg) < 12
                and bug_id in bugs_by_id
                and bugs_by_id[bug_id].get("status") == "draft"
                and not bugs_by_id[bug_id].get("has_formatted_report")
            )
            if is_det_or_workflow and not active_draft_for_continuation:
                needs_clarify, clarify_reply, clarify_actions = self._assess_bug_clarity(
                    message=message,
                    conversation_history=conversation_history,
                    project_info=project_info,
                    bug_id=direct_bug_id,
                    jira_key=jira_key,
                    attachments=attachments,
                )
                if needs_clarify:
                    action = "chat"
                    target_agent = "none"
                    cleaned_plan = []
                    also_raise_jira = False
                    bug_id = None
                    if not llm_originally_chose_chat:
                        visible_reply = clarify_reply
                        parsed_meta["suggested_actions"] = clarify_actions

        force_new_ticket = bool(parsed_meta.get("force_new_ticket", False)) or self._user_requested_fresh_ticket(message)

        synthesized = parsed_meta.get("bug_summary") or fallback["bug_summary"]
        if len(synthesized.strip()) < len(fallback["bug_summary"].strip()) // 2:
            synthesized = fallback["bug_summary"]

        suggested_actions = parsed_meta.get("suggested_actions")
        if not isinstance(suggested_actions, list) or not suggested_actions:
            suggested_actions = self._extract_suggested_actions(
                action,
                target_agent,
                recent_bugs,
                has_bug_context=bool(fallback["bug_summary"].strip()),
            )
        else:
            suggested_actions = [str(x)[:70] for x in suggested_actions[:4] if str(x).strip()]

        active_attachments = (
            self._collect_active_attachments(attachments, conversation_history, include_binary_from_history=True)
            if action != "chat"
            else []
        )

        return {
            "reply": visible_reply,
            "action": action,
            "target_agent": target_agent,
            "agent_plan": cleaned_plan,
            "bug_summary": synthesized,
            "bug_report_id": bug_id,
            "jira_key": jira_key,
            "jira_project_key": jira_project_key,
            "force_new_ticket": force_new_ticket,
            "also_raise_jira": also_raise_jira,
            "skill_type": None,
            "skill_title": None,
            "attachments": active_attachments,
            "suggested_actions": suggested_actions,
            "llm_mode": "live",
            "llm_degraded": False,
            "llm_notice": None,
        }

    async def _invoke_llm_round(
        self,
        messages: List[Dict[str, Any]],
        system_prompt: str,
        stream_callback: Optional[Callable[[str], Awaitable[None]]],
        leading_separator: str = "",
    ) -> tuple[str, bool]:
        """Execute one LLM turn, streaming visible Markdown while hiding DESK_TOOL and DESK_ACTION HTML comments."""
        if stream_callback is None:
            resp = await self.llm.chat(
                messages=messages,
                system_prompt=system_prompt,
                temperature=0.55,
            )
            return resp.content or "", False

        raw_chunks: List[str] = []
        buffer = ""
        in_hidden_tag = False
        emitted_any = False

        async def emit_visible(chunk: str) -> None:
            nonlocal emitted_any
            if not chunk:
                return
            if not emitted_any and leading_separator:
                chunk = leading_separator + chunk
            emitted_any = True
            await stream_callback(chunk)

        async for token in self.llm.chat_stream(
            messages=messages,
            system_prompt=system_prompt,
            temperature=0.55,
        ):
            raw_chunks.append(token)
            buffer += token

            while buffer:
                if in_hidden_tag:
                    end_idx = buffer.find("-->")
                    if end_idx == -1:
                        break
                    buffer = buffer[end_idx + 3 :]
                    in_hidden_tag = False
                    continue

                marker_idx = buffer.find("<!--")
                if marker_idx == -1:
                    hold = 0
                    if buffer.endswith("<!-"):
                        hold = 3
                    elif buffer.endswith("<!"):
                        hold = 2
                    elif buffer.endswith("<"):
                        hold = 1
                    safe = buffer[:-hold] if hold else buffer
                    await emit_visible(safe)
                    buffer = buffer[-hold:] if hold else ""
                    break

                if marker_idx > 0:
                    await emit_visible(buffer[:marker_idx])
                    buffer = buffer[marker_idx:]

                if len(buffer) < 20:
                    break

                if re.match(r"^<!--\s*DESK_(?:ACTION|TOOL)\b", buffer):
                    in_hidden_tag = True
                    continue

                await emit_visible("<!--")
                buffer = buffer[4:]

        if not in_hidden_tag and buffer:
            safe_rem = _ANY_DESK_TAG_RE.sub("", buffer)
            for m_str in (_ACTION_MARKER, _TOOL_MARKER):
                if m_str in safe_rem:
                    safe_rem = safe_rem.split(m_str, 1)[0]
            if safe_rem:
                await emit_visible(safe_rem)

        return "".join(raw_chunks), emitted_any

    async def triage_and_route(
        self,
        message: str,
        project_info: Dict[str, Any],
        skills_context: str = "",
        recent_bugs: Optional[List[Dict[str, Any]]] = None,
        conversation_history: Optional[List[Dict[str, Any]]] = None,
        attachments: Optional[List[Dict[str, Any]]] = None,
        project_id: Optional[int] = None,
        stream_callback: Optional[Callable[[str], Awaitable[None]]] = None,
        asset_dossier: str = "",
        asset_snapshot: Optional[Dict[str, Any]] = None,
        asset_hub: Optional[Any] = None,
        prefetched_results: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """Chat naturally about bugs, inspect any project asset, and dispatch or organize agents cleanly."""
        start_time = datetime.utcnow()
        recent_bugs = recent_bugs or []
        active_hub = asset_hub or self.asset_hub

        # Deterministic skill upload detection first
        active_attachments = self._collect_active_attachments(attachments, conversation_history, include_binary_from_history=True)
        skill_type = None if attachments else self._is_skill_upload(message)
        if skill_type:
            decision = self._build_fallback_decision(
                message,
                conversation_history,
                project_info,
                recent_bugs,
                attachments,
                asset_snapshot=asset_snapshot,
                prefetched_results=prefetched_results,
            )
            decision["llm_degraded"] = False
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
        if not asset_dossier:
            bugs_str = (
                "\n".join(
                    f"- Bug #{b['id']}: \"{b['title']}\" | status={b['status']} | severity={b.get('severity')} | jira={b.get('jira_ticket_id') or 'NOT_RAISED'}"
                    for b in recent_bugs[:8]
                )
                or "None logged yet."
            )
            asset_block = (
                f"Active Project:\n"
                f"- Name: {project_info.get('name')}\n"
                f"- App URL: {project_info.get('app_url') or 'Not specified'}\n"
                f"- Repo URL: {project_info.get('repo_url') or 'Not specified'}\n"
                f"- Jira Project Key: {project_info.get('jira_project_key') or 'QA'}\n"
                f"- Description: {project_info.get('description') or 'N/A'}\n"
                f"- Project Report Format:\n{strip_report_format_preamble(project_info.get('report_format')) or 'Standard QA Markdown Format'}\n\n"
                f"Stored Project Skills / Requirements / Report Format:\n{skills_context[:2500] or 'None stored yet.'}\n\n"
                f"Recent Tracked Bugs:\n{bugs_str}"
            )
        else:
            asset_block = asset_dossier

        system_prompt = (
            "You are the **Desk Officer** — the interactive Senior QA Lead AI assistant and multi-agent team coordinator in QA Officer.\n\n"
            f"{asset_block}\n\n"
            f"Built-in QA Officer Agents (you can explain, assign individually, OR organize into custom multi-agent sequences):\n{roster_str}\n"
            "- auto_test (Autonomous Bug Discovery): Runs Detective discovery across stored skills.\n\n"
            "ON-DEMAND PROJECT ASSET TOOLS (already pre-fetched when obvious, or call via <!-- DESK_TOOL {...} --> when you need extra live data):\n"
            "- jira_list_projects: {} — list accessible Jira Cloud projects & keys.\n"
            "- jira_search: {\"project_key\": optional, \"status\": \"In QA|Backlog|Failed QA|Ready To Deploy|To Do\", \"query\": optional} — query live Jira Cloud issues.\n"
            "- jira_get_issue: {\"issue_key\": \"SCRUM-850\"} — read full Jira issue description, status, priority, comments, attachments.\n"
            "- get_bug_details: {\"bug_id\": 1} — full local bug report, root cause, reproduction steps, formatted report, logs.\n"
            "- list_evidence: {\"bug_id\": optional} — list screenshot/video assets with URLs.\n"
            "- list_skills / save_skill / delete_skill — manage project skills (CALL save_skill/delete_skill ONLY when user explicitly asks to add/delete a skill).\n"
            "- update_project_config: {\"jira_project_key\": \"SCRUM\", \"report_format\": \"...\"} — update project metadata or Project Configuration -> Report Format (ONLY call when user explicitly asks to update settings or set/change the report format).\n"
            "- list_agent_logs: {\"agent\": optional, \"module\": optional, \"status\": optional} — inspect recent execution & telemetry logs across all agents and modules.\n"
            "- watcher_scan: {\"all_projects\": false, \"include_app_health\": true} — run a live Watcher scan for Jira updates and AI agent failures.\n"
            "- inspect_app_url: {\"url\": optional} — run live headless Playwright inspection of app_url or target URL.\n"
            "- read_repo_file: {\"path\": \"relative/path\"} — read a source file if repo_url is a local folder.\n\n"
            "BEHAVIOR & GROUNDING RULES:\n"
            "1. You HAVE FULL LIVE ACCESS to all project assets shown above and the agent team. NEVER falsely say you lack access to Jira, skills, bugs, evidence, logs, or agents.\n"
            "2. Cite ONLY verified Jira keys, titles, statuses, bug IDs, evidence URLs, and asset details present in the Project Asset Dossier or DESK_TOOL outputs. Never invent Jira keys or fake data.\n"
            "3. CLARIFY FIRST BEFORE DISPATCHING: Whenever the user describes a bug or asks to investigate / create a bug report with vague or missing details (unclear steps/trigger, missing expected vs. actual behavior, or no affected route/endpoint/Bug #/Jira key), keep action='chat' and ask concise clarifying questions FIRST before dispatching any agents. If a previous bug report was already created or raised earlier in chat, treat any new vague bug mention as a fresh defect requiring clarification rather than reusing the finished bug's details.\n"
            "4. Keep action='chat' during bug triage discussions, greetings, asset/Jira inspection queries (like 'check my Jira In QA list'), and questions about agents or how you organize them.\n"
            "5. CRITICAL EXECUTION RULE: Writing prose like 'I am triggering Sheriff/Detective' while setting action='chat' DOES NOT run any agent! To actually execute agents, you MUST set action='assign_agent' (single agent), action='organize_agents' (agent sequence), or action='run_main_workflow'.\n"
            "6. INVESTIGATION ASSIGNMENT: When details are clear and the user asks to investigate/debug/trace/reproduce a bug (without limiting to just one named agent), set action='organize_agents' with agent_plan=[{\"agent\":\"detective\",\"goal\":\"Investigate root cause and steps\"},{\"agent\":\"coroner\",\"goal\":\"Capture screenshot/video evidence\"}]. If they explicitly ask for just one agent (e.g. 'Assign Detective'), set action='assign_agent'.\n"
            "7. REPORT COLLABORATION ('create a bug report'): Once details are clear (or a draft investigation already exists), set action='run_main_workflow' so Detective -> Coroner -> Judge -> Reporter collaborate end-to-end (leaving Sheriff on standby). Reporter will strictly follow the project's configured Report Format as-is. If the user explicitly asks to set or update the project's report format, call update_project_config with `report_format` (or let save_skill handle it).\n"
            "8. RAISING THE REPORT ('raise the report', 'raise bug #...', 'create ticket in SCRUM', 'tell Sheriff...'): Set action='assign_agent', target_agent='sheriff', bug_report_id=<target bug ID>, also_raise_jira=true, jira_project_key='SCRUM' (plus force_new_ticket=true when replacing a simulated/wrong-project key).\n"
            "9. STRICT SHERIFF RULE: NEVER include 'sheriff' in target_agent/agent_plan or set also_raise_jira=true unless the user EXPLICITLY asks to raise/push/submit/create the ticket in Jira/Backlog. Sheriff always creates a brand-new Jira ticket in 'Backlog' (never 'To Do') and never modifies old existing Jira tickets.\n"
            "10. Backlog note: `jira_search` with `status: \"Backlog\"` searches both 'Backlog' workflow status and Scrum Board Backlog items (sprint EMPTY, status 'To Do'). Treat SIMULATED_OR_WRONG_PROJECT keys like QA-D715 as unraised on SCRUM.\n"
            "11. VISUAL & FILE ANALYSIS: When images, screenshots, PDFs, Office documents (DOCX/XLSX/PPTX), HAR network traces, logs, JSON, CSV, or code files are attached, inspect BOTH the visual pixels/pages AND extracted text thoroughly. Point out specific UI elements, visible error messages, layout issues, failing HTTP requests/status codes, stack trace lines, or anomalies directly grounded in the attachment. Keep action='chat' when the user asks you to examine, explain, or analyze an uploaded file/image unless they also explicitly ask to run agents or create/raise a report.\n\n"
            "OUTPUT FORMAT:\n"
            "- If you need additional live asset data BEFORE answering, emit `<!-- DESK_TOOL {\"tool\":\"...\",\"params\":{...}} -->` and wait for tool results.\n"
            "- Otherwise write your clear conversational Markdown reply directly first, and at the VERY END on its own line append exactly one hidden metadata comment (never mention DESK_ACTION or DESK_TOOL to the user):\n"
            '<!-- DESK_ACTION {"action":"chat|run_main_workflow|assign_agent|organize_agents","target_agent":"none|detective|coroner|judge|reporter|sheriff|watcher|sanity|auto_test","agent_plan":[],"bug_report_id":null,"jira_key":null,"jira_project_key":null,"force_new_ticket":false,"also_raise_jira":false,"bug_summary":"concise synthesis if bug discussed","suggested_actions":["short reply 1","short reply 2"]} -->'
        )

        llm_messages = self._build_llm_messages(message, conversation_history, attachments)
        executed_asset_ops: List[Dict[str, Any]] = []
        decision: Optional[Dict[str, Any]] = None

        if not self.llm.is_configured():
            decision = self._build_fallback_decision(
                message,
                conversation_history,
                project_info,
                recent_bugs,
                attachments,
                llm_error=self.llm.configuration_issue(),
                asset_snapshot=asset_snapshot,
                prefetched_results=prefetched_results,
            )
        else:
            working_messages = list(llm_messages)
            try:
                full_raw = ""
                prior_streamed = False
                for round_idx in range(3):
                    round_raw, round_streamed = await self._invoke_llm_round(
                        messages=working_messages,
                        system_prompt=system_prompt,
                        stream_callback=stream_callback,
                        leading_separator="\n\n" if prior_streamed else "",
                    )
                    full_raw = round_raw
                    prior_streamed = prior_streamed or round_streamed

                    tool_matches = _TOOL_TAG_RE.findall(round_raw)
                    if not tool_matches or active_hub is None or project_id is None or round_idx >= 2:
                        break

                    tool_outputs: List[Dict[str, Any]] = []
                    for raw_json in tool_matches[:3]:
                        try:
                            req = json.loads(raw_json)
                            tname = str(req.get("tool") or "").strip()
                            tparams = req.get("params") if isinstance(req.get("params"), dict) else {}
                        except Exception as parse_err:
                            tool_outputs.append({"ok": False, "error": f"Invalid DESK_TOOL JSON: {parse_err}"})
                            continue

                        t_res = await active_hub.run_asset_tool(
                            project_id=project_id,
                            tool_name=tname,
                            params=tparams,
                            snapshot=asset_snapshot,
                        )
                        tool_outputs.append(t_res)
                        if tname in ("save_skill", "delete_skill", "update_project_config") and t_res.get("ok"):
                            executed_asset_ops.append(t_res)
                            asset_snapshot = await active_hub.collect_snapshot(project_id)

                    working_messages.append({"role": "assistant", "content": round_raw})
                    working_messages.append({
                        "role": "user",
                        "content": (
                            "[SYSTEM PROJECT ASSET TOOL RESULTS]\n"
                            + json.dumps(tool_outputs, ensure_ascii=False, default=str)[:4500]
                            + "\n\nNow write your final clear Markdown response grounded strictly in these results "
                            "(do NOT repeat DESK_TOOL tags unless essential) and append <!-- DESK_ACTION {...} --> at the end."
                        ),
                    })

                decision = self._parse_streamed_or_full_llm_output(
                    full_raw,
                    message,
                    conversation_history,
                    project_info,
                    recent_bugs,
                    attachments,
                    asset_snapshot=asset_snapshot,
                    prefetched_results=prefetched_results,
                )
            except Exception as e:
                err_msg = self.llm._redact_secrets(str(e))
                logger.warning(f"Desk Officer LLM fallback triggered ({self.llm.provider}): {err_msg}")
                decision = self._build_fallback_decision(
                    message,
                    conversation_history,
                    project_info,
                    recent_bugs,
                    attachments,
                    llm_error=f"LLM error ({self.llm.provider}/{self.llm.model}): {err_msg[:180]}",
                    asset_snapshot=asset_snapshot,
                    prefetched_results=prefetched_results,
                )

        if executed_asset_ops:
            decision["executed_asset_ops"] = executed_asset_ops

        had_live_llm_error = bool(decision.get("llm_mode") == "fallback" and decision.get("llm_notice") and self.llm.is_configured())
        is_degraded = bool(decision.get("llm_mode") == "fallback" or decision.get("llm_degraded"))
        decision.setdefault("llm_degraded", is_degraded)
        log_status = "failed" if had_live_llm_error else ("degraded" if is_degraded else "success")
        log_level = "error" if had_live_llm_error else ("warning" if is_degraded else "success")
        await self.log_action(
            action=f"desk_officer_{decision['action']}",
            input_data={
                "message": message[:400],
                "project_id": project_id,
                "llm_mode": decision.get("llm_mode"),
            },
            output_data={
                "action": decision["action"],
                "target_agent": decision["target_agent"],
                "agent_plan": [s.get("agent") for s in (decision.get("agent_plan") or [])],
                "also_raise_jira": decision["also_raise_jira"],
                "bug_report_id": decision["bug_report_id"],
                "jira_key": decision.get("jira_key"),
                "jira_project_key": decision.get("jira_project_key"),
                "force_new_ticket": decision.get("force_new_ticket"),
                "llm_degraded": is_degraded,
                **({"llm_notice": decision.get("llm_notice")} if (had_live_llm_error or is_degraded) and decision.get("llm_notice") else {}),
            },
            status=log_status,
            module="Desk Officer Chat",
            category="error" if had_live_llm_error else "agent_action",
            level=log_level,
            notify=bool(had_live_llm_error),
            bug_report_id=decision.get("bug_report_id"),
            jira_key=decision.get("jira_key"),
            project_id=project_id,
            started_at=start_time,
        )
        return decision

    async def run(self, *args, **kwargs) -> Any:
        return await self.triage_and_route(*args, **kwargs)
