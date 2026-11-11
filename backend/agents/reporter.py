"""Reporter Agent - Writes clean, accurate bug reports adhering strictly to the Project Report Format."""
import logging
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from agents.base import BaseAgent
from services.llm_router import LLMRouter

logger = logging.getLogger(__name__)

_PREAMBLE_LINE_RE = re.compile(
    r"^\s*(?:please\s+|kindly\s+|could\s+you\s+|can\s+you\s+)?"
    r"(?:here\s+is|this\s+is|use|follow|set|apply|update|save)?\s*"
    r"(?:the|my|our|this)?\s*(?:bug\s+)?report\s+(?:format|template)"
    r"(?:\s+template)?(?:\s+as\s+(?:it\s+is|follows))?\s*[:\-–]*\s*$",
    re.IGNORECASE,
)
_INLINE_PREAMBLE_PREFIX_RE = re.compile(
    r"^\s*(?:(?:please\s+|kindly\s+)?(?:here\s+is|this\s+is|use|follow|set|apply)\s+)?"
    r"(?:the|my|our)?\s*(?:bug\s+)?report\s+(?:format|template)(?:\s+template)?\s*[:\-–]+\s*",
    re.IGNORECASE,
)

_CHAT_WRAPPER_LINE_RE = re.compile(
    r"^\s*(?:Prior bug discussion from user:|Latest user instruction:|Assigned Goal:.*?)\s*$",
    re.IGNORECASE,
)
_GREETING_ONLY_LINE_RE = re.compile(
    r"^\s*(?:hi|hello|hey|yo|hola|salam|good\s+(?:morning|afternoon|evening)|ok|okay|thanks|thank you)[!.?\s]*$",
    re.IGNORECASE,
)

_COMMON_SECTION_KW_RE = re.compile(
    r"\b(?:"
    r"title|summary|environment|environments|env|description|descriptions|details|overview|"
    r"steps?|repro|reproduce|reproduction|expected|actual|observed|result|results|"
    r"behavior|behaviour|evidence|attachments?|screenshots?|proof|video|videos|logs?|"
    r"severity|priority|impact|root\s+cause|cause|modules?|components?|area|areas|"
    r"preconditions?|prerequisites?|setup|labels?|tags?|notes?|comments?|workaround|"
    r"endpoints?|urls?|browser|browsers|os|platform|platforms|build|version|bug\s*id|issue\s*id"
    r")\b",
    re.IGNORECASE,
)


def strip_report_format_preamble(template: Optional[str]) -> Optional[str]:
    """Strip conversational intro lines such as 'the bug report format:' while preserving the template body."""
    raw = (template or "").strip()
    if not raw:
        return None

    lines = raw.splitlines()
    # Strip outer fenced code blocks if user pasted ```markdown ... ```
    if len(lines) >= 3 and lines[0].strip().startswith("```") and lines[-1].strip() == "```":
        lines = lines[1:-1]

    while lines and (not lines[0].strip() or _PREAMBLE_LINE_RE.match(lines[0])):
        lines.pop(0)

    if lines:
        lines[0] = _INLINE_PREAMBLE_PREFIX_RE.sub("", lines[0]).strip()
        if not lines[0]:
            lines.pop(0)

    cleaned = "\n".join(lines).strip()
    return cleaned or None


def clean_bug_description(raw: Optional[str]) -> str:
    """Remove chat-synthesis boilerplate and greetings to isolate the actual bug description."""
    text = (raw or "").strip()
    if not text:
        return ""

    # Drop appended raw attachment blocks if substantive prose precedes them
    att_split = re.split(r"\n+Attached Files & Images:\s*\n", text, maxsplit=1, flags=re.IGNORECASE)
    if len(att_split) == 2 and att_split[0].strip():
        text = att_split[0].strip()

    cleaned_lines: List[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            if cleaned_lines and cleaned_lines[-1] != "":
                cleaned_lines.append("")
            continue
        if stripped in ("---", "___", "***"):
            continue
        if _CHAT_WRAPPER_LINE_RE.match(stripped):
            continue
        if _GREETING_ONLY_LINE_RE.match(stripped):
            continue
        if re.match(r"^(?:Prior Investigation on Bug #\d+:|-\s*Preliminary\s+(?:Title|Root Cause|Steps):)", stripped, re.IGNORECASE):
            continue
        # Strip pure command turns with no defect content
        if re.fullmatch(
            r"(?:please\s+)?(?:create|generate|make|build|investigate|trace|debug)\s+(?:a\s+|the\s+|this\s+)?(?:bug\s*report|it|this|issue)[!.?\s]*",
            stripped,
            re.IGNORECASE,
        ):
            continue
        cleaned_lines.append(line.rstrip())

    result = "\n".join(cleaned_lines).strip()
    result = re.sub(r"\n{3,}", "\n\n", result)
    return result or text.strip()


def clean_bug_title(raw: Optional[str], fallback_desc: str = "") -> str:
    """Normalize bug title into a crisp, single-line summary without chat wrapper clutter."""
    text = clean_bug_description(raw or "")
    if not text and fallback_desc:
        text = clean_bug_description(fallback_desc)

    first_line = ""
    for line in text.splitlines():
        s = line.strip()
        if s:
            first_line = s
            break
    if not first_line:
        return "Reported Defect"

    first_line = re.sub(r"^(?:Bug identified:\s*|Defect:\s*|Issue:\s*|Title:\s*)", "", first_line, flags=re.IGNORECASE).strip()
    first_line = re.sub(r"^#+\s*", "", first_line).strip()
    first_line = re.sub(r"\s+", " ", first_line)
    if len(first_line) > 120:
        first_line = first_line[:117].rstrip(" ,.;:-") + "..."
    return first_line or "Reported Defect"


def _strip_leading_section_index(label: str) -> str:
    return re.sub(r"^\d+[\.)]\s+", "", (label or "").strip()).strip()


def _is_placeholder_line(line: str) -> bool:
    s = line.strip()
    if not s:
        return True
    if re.fullmatch(r"[\[{<].*[\]}>]", s):
        return True
    if re.match(r"^(?:[-*•]\s+|\d+[\.)]\s+)", s):
        # Numbered item could be a numbered section header only if short & handled earlier
        return False
    return False


def parse_template_sections(template: Optional[str]) -> List[Dict[str, Any]]:
    """Parse section headers and formatting style from a custom Report Format template."""
    cleaned = strip_report_format_preamble(template)
    if not cleaned:
        return []

    lines = cleaned.splitlines()
    raw_entries: List[Dict[str, Any]] = []
    in_code_block = False

    # Check whether non-empty lines look predominantly like a bare list of short labels
    candidate_bare = 0
    non_empty_count = 0
    for line in lines:
        s = line.strip()
        if not s or s.startswith("```"):
            continue
        non_empty_count += 1
        if (
            len(s) <= 55
            and len(s.split()) <= 6
            and not s.startswith(("#", "-", "*", "•", "[", "{", "<", ">"))
            and "://" not in s
            and not s.endswith((".", "?", "!"))
        ):
            candidate_bare += 1

    bare_list_mode = non_empty_count >= 2 and (candidate_bare / non_empty_count) >= 0.6

    for idx, raw_line in enumerate(lines):
        s = raw_line.strip()
        if s.startswith("```"):
            in_code_block = not in_code_block
            continue
        if in_code_block or not s or s in ("---", "===", "***"):
            continue

        # 1. Markdown ATX heading: # Title, ## 1. Summary, ### Steps to Reproduce
        m_md = re.match(r"^(#{1,6})\s+(.+?)\s*#*$", s)
        if m_md:
            prefix, lbl = m_md.group(1), m_md.group(2).strip()
            # Clean placeholder bracket wrappers inside headings like '# Bug Report: [Title]' only for matching name
            raw_entries.append({
                "raw_line": s,
                "style": "markdown_heading",
                "heading_prefix": prefix,
                "label": lbl,
                "clean_label": _strip_leading_section_index(re.sub(r"[:\s]+$", "", lbl)),
                "has_colon": lbl.endswith(":"),
                "inline_hint": "",
                "hints": [],
                "line_idx": idx,
            })
            continue

        # 2. Bold inline key-value or bold header: **Title:** value  OR  **Title**
        m_bold_inline = re.match(r"^\*\*([^*]{1,65}?)\*\*\s*(.*)$", s)
        if m_bold_inline and not s.startswith(("-", "* ")):
            inner = m_bold_inline.group(1).strip()
            rest = m_bold_inline.group(2).strip()
            has_colon = inner.endswith(":") or rest.startswith(":")
            rest_clean = rest.lstrip(":").strip()
            clean_lbl = _strip_leading_section_index(inner.rstrip(":").strip())
            if len(clean_lbl.split()) <= 7:
                raw_entries.append({
                    "raw_line": s,
                    "style": "bold_inline" if (has_colon or rest_clean) else "bold_heading",
                    "heading_prefix": "**",
                    "label": inner.rstrip(":").strip(),
                    "clean_label": clean_lbl,
                    "has_colon": has_colon,
                    "inline_hint": rest_clean,
                    "hints": [],
                    "line_idx": idx,
                })
                continue

        # 3. Bullet-wrapped bold metadata line (e.g. "- **Project:** ...") -> capture if part of template
        m_bullet_bold = re.match(r"^[-*•]\s+\*\*([^*]{1,50}?):?\*\*\s*(.*)$", s)
        if m_bullet_bold:
            lbl = m_bullet_bold.group(1).strip().rstrip(":")
            rest = m_bullet_bold.group(2).strip()
            if len(lbl.split()) <= 5:
                raw_entries.append({
                    "raw_line": s,
                    "style": "bullet_bold",
                    "heading_prefix": "-",
                    "label": lbl,
                    "clean_label": _strip_leading_section_index(lbl),
                    "has_colon": ":" in m_bullet_bold.group(0),
                    "inline_hint": rest,
                    "hints": [],
                    "line_idx": idx,
                })
                continue

        # 4. Colon label: "Title:" or "Title: [value]"
        m_colon = re.match(r"^([A-Za-z][A-Za-z0-9 /&()._-]{1,55}):(?:\s+(.*))?$", s)
        if m_colon and "://" not in s and not s.lower().startswith(("http", "https")):
            lbl = m_colon.group(1).strip()
            rest = (m_colon.group(2) or "").strip()
            if len(lbl.split()) <= 7:
                # Avoid splitting prose sentences before a colon
                is_likely_header = (
                    not rest
                    or re.match(r"^[\[{<]", rest)
                    or bool(_COMMON_SECTION_KW_RE.search(lbl))
                    or bare_list_mode
                )
                if is_likely_header:
                    raw_entries.append({
                        "raw_line": s,
                        "style": "colon_label",
                        "heading_prefix": "",
                        "label": lbl,
                        "clean_label": _strip_leading_section_index(lbl),
                        "has_colon": True,
                        "inline_hint": rest,
                        "hints": [],
                        "line_idx": idx,
                    })
                    continue

        # 5. Numbered section header like "1. Title" / "2. Environment"
        # Do NOT treat ordered step examples inside "## Steps to Reproduce" or another section as top-level sections.
        m_num = re.match(r"^(\d+[\.)])\s+([A-Za-z][A-Za-z0-9 /&()._-]{1,50}):?$", s)
        inside_markdown_or_steps = bool(
            raw_entries
            and (
                any(e["style"] == "markdown_heading" for e in raw_entries)
                or (raw_entries[-1]["style"] != "numbered_heading" and _classify_section(raw_entries[-1]["clean_label"]) == "steps")
            )
        )
        if m_num and not inside_markdown_or_steps and not s.endswith(".") and len(m_num.group(2).split()) <= 6:
            num_prefix, lbl = m_num.group(1), m_num.group(2).strip()
            if _COMMON_SECTION_KW_RE.search(lbl) or bare_list_mode:
                raw_entries.append({
                    "raw_line": s,
                    "style": "numbered_heading",
                    "heading_prefix": num_prefix,
                    "label": f"{num_prefix} {lbl}",
                    "clean_label": lbl,
                    "has_colon": s.endswith(":"),
                    "inline_hint": "",
                    "hints": [],
                    "line_idx": idx,
                })
                continue

        # 6. Bare section label line (e.g., "Title", "Environment", "Steps to reproduce")
        if (
            not raw_line.startswith((" ", "\t"))
            and not s.startswith(("-", "*", "•", "[", "{", "<", ">"))
            and "://" not in s
            and len(s) <= 55
            and len(s.split()) <= 6
            and not s.endswith((".", "?", "!"))
        ):
            is_kw = bool(_COMMON_SECTION_KW_RE.search(s))
            if bare_list_mode or (is_kw and len(s.split()) <= 4 and not inside_markdown_or_steps):
                raw_entries.append({
                    "raw_line": s,
                    "style": "bare_label",
                    "heading_prefix": "",
                    "label": s,
                    "clean_label": _strip_leading_section_index(s),
                    "has_colon": False,
                    "inline_hint": "",
                    "hints": [],
                    "line_idx": idx,
                })
                continue

        # Otherwise append as hint/body example under the previous section
        if raw_entries:
            raw_entries[-1]["hints"].append(s)

    return raw_entries


def _classify_section(label: str) -> str:
    """Map a section label to a semantic content slot."""
    low = re.sub(r"\s+", " ", (label or "").lower()).strip()
    if "bug id" in low or "ticket id" in low or low == "id":
        return "bug_id"
    if "root cause" in low or low == "cause" or "root-cause" in low:
        return "root_cause"
    if "expected" in low:
        return "expected"
    if "actual" in low or "observed" in low:
        return "actual"
    if any(k in low for k in ("step", "repro", "how to")):
        return "steps"
    if any(k in low for k in ("evidence", "attachment", "screenshot", "proof", "video", "media")):
        return "evidence"
    if any(k in low for k in ("environment", "env", "browser", "platform", "system", "os", "version", "build")):
        return "environment"
    if any(k in low for k in ("precondition", "prerequisite", "setup")):
        return "preconditions"
    if "severity" in low or "priority" in low:
        return "severity"
    if any(k in low for k in ("module", "component", "feature", "area")):
        return "module"
    if low in ("label", "labels", "tag", "tags"):
        return "label"
    if "project" in low:
        return "project"
    if "date" in low or "time" in low:
        return "date"
    if "impact" in low or "risk" in low:
        return "impact"
    if "description" in low or "detail" in low or "overview" in low or "issue" in low:
        return "description"
    if "summary" in low:
        return "summary"
    if "title" in low or "bug report" in low:
        return "title"
    if any(k in low for k in ("note", "comment", "workaround", "recommendation", "judge")):
        return "notes"
    return "description"


def _strip_llm_wrapper_chatter(text: str, section_labels: Optional[List[str]] = None) -> str:
    """Remove accidental markdown fences and conversational intro/outro added by the LLM."""
    cleaned = (text or "").strip()
    if not cleaned:
        return ""

    # Remove outer ```markdown ... ``` fence if it wraps the report
    fence_match = re.match(r"^```(?:markdown|md|text)?\s*\n([\s\S]*?)\n```\s*$", cleaned, re.IGNORECASE)
    if fence_match:
        cleaned = fence_match.group(1).strip()

    first_section = (section_labels[0].lower() if section_labels else "")
    lines = cleaned.splitlines()
    # Trim up to 2 leading conversational filler lines ("Here is the bug report...")
    for _ in range(3):
        if not lines:
            break
        first = lines[0].strip()
        if not first:
            lines.pop(0)
            continue
        low = first.lower()
        if first_section and first_section in low:
            break
        if re.match(
            r"^(?:here\s+is|below\s+is|sure[,!]|certainly[,!]|i\s+have\s+(?:generated|created|prepared|formatted)|following\s+the\s+template)\b",
            low,
        ):
            lines.pop(0)
            continue
        break

    # Trim trailing conversational filler lines ("Let me know if you need anything else...")
    while lines:
        last = lines[-1].strip().lower()
        if not last:
            lines.pop()
            continue
        if re.match(r"^(?:let\s+me\s+know|please\s+let\s+me\s+know|hope\s+this\s+helps|would\s+you\s+like)\b", last):
            lines.pop()
            continue
        break

    return "\n".join(lines).strip()


def _extract_heading_texts(report_text: str) -> List[Dict[str, Any]]:
    """Extract normalized section header entries (distinguishing standalone headers from inline labels)."""
    found: List[Dict[str, Any]] = []
    in_code_block = False
    for raw in (report_text or "").splitlines():
        s = raw.strip()
        if s.startswith("```"):
            in_code_block = not in_code_block
            continue
        if in_code_block or not s:
            continue
        m_md = re.match(r"^#{1,6}\s+(.+?)\s*#*$", s)
        if m_md:
            found.append({
                "text": _strip_leading_section_index(re.sub(r"[:\s]+$", "", m_md.group(1))),
                "is_standalone_section": True,
            })
            continue
        m_bullet_bold = re.match(r"^[-*•]\s+\*\*([^*]{1,65}?):?\*\*\s*(.*)$", s)
        if m_bullet_bold:
            found.append({
                "text": _strip_leading_section_index(m_bullet_bold.group(1).rstrip(":").strip()),
                "is_standalone_section": False,
            })
            continue
        m_bold = re.match(r"^\*\*([^*]{1,75}?)\*\*(?:\s+(.*))?$", s)
        if m_bold:
            rest = (m_bold.group(2) or "").lstrip(":").strip()
            found.append({
                "text": _strip_leading_section_index(m_bold.group(1).rstrip(":").strip()),
                "is_standalone_section": not bool(rest),
            })
            continue
        m_colon = re.match(r"^([A-Za-z][A-Za-z0-9 /&()._-]{1,55}):(?:\s+(.*))?$", s)
        if m_colon and "://" not in s:
            rest = (m_colon.group(2) or "").strip()
            found.append({
                "text": _strip_leading_section_index(m_colon.group(1).strip()),
                "is_standalone_section": not bool(rest),
            })
            continue
        if len(s) <= 55 and not s.startswith(("-", "*", "•", ">")) and "://" not in s and not s.endswith((".", "?", "!")):
            found.append({
                "text": _strip_leading_section_index(s),
                "is_standalone_section": False,
            })
    return found


def _matches_template_strictly(
    report_text: str,
    parsed_sections: List[Dict[str, Any]],
) -> Tuple[bool, str]:
    """Verify generated report includes every template section in exact order without hallucinated sections."""
    if not report_text or len(report_text.strip()) < 20:
        return False, "Report output was empty or too short."
    if not parsed_sections:
        return True, ""

    expected_labels = [sec["clean_label"] for sec in parsed_sections if sec.get("clean_label")]
    if not expected_labels:
        return True, ""

    extracted = _extract_heading_texts(report_text)
    h_idx = 0
    for sec in parsed_sections:
        lbl = sec.get("clean_label") or ""
        if not lbl:
            continue
        # Remove bracketed placeholders from template label when checking presence (e.g. "Bug Report: [Title]")
        check_phrase = re.sub(r"(?:\[[^\]]+\]|\{\{[^}]+\}\}|\{[^}]+\}|<[^>]+>)", "", lbl).strip(" :-").lower()
        if not check_phrase:
            continue

        matched_pos = None
        for idx in range(h_idx, len(extracted)):
            h_low = extracted[idx]["text"].strip(" :-").lower()
            if h_low == check_phrase or check_phrase in h_low:
                matched_pos = idx
                break
        if matched_pos is None:
            return False, f"Missing required template section '{lbl}'."
        h_idx = matched_pos + 1

    # Disallow unrequested standalone headings like '### Root Cause Analysis', '### Summary', '### Metadata'
    allowed_slots = {_classify_section(sec["clean_label"]) for sec in parsed_sections}
    expected_lower = {
        re.sub(r"(?:\[[^\]]+\]|\{\{[^}]+\}\}|\{[^}]+\}|<[^>]+>)", "", l).strip(" :-").lower()
        for l in expected_labels
    }
    for entry in extracted:
        if not entry.get("is_standalone_section"):
            continue
        h = entry["text"]
        h_low = h.strip(" :-").lower()
        if not h_low or len(h_low) > 60:
            continue
        if any(exp and (exp in h_low or h_low in exp) for exp in expected_lower):
            continue
        slot = _classify_section(h_low)
        # If heading introduces a brand new semantic section that the user intentionally excluded from template
        if slot not in allowed_slots and any(
            kw in h_low
            for kw in (
                "root cause",
                "summary",
                "metadata",
                "severity",
                "priority",
                "impact",
                "recommendation",
                "workaround",
                "judge",
                "conclusion",
                "precondition",
            )
        ):
            return False, f"Unrequested extra section '{h}' added outside template."

    return True, ""


def _build_evidence_lines(
    evidence_list: Optional[List[Dict[str, Any]]],
    bug_data: Dict[str, Any],
) -> List[str]:
    lines: List[str] = []
    seen = set()
    for ev in evidence_list or []:
        if not isinstance(ev, dict):
            continue
        fname = str(ev.get("file_name") or ev.get("filename") or "").strip()
        etype = str(ev.get("type") or "file").strip()
        stage = str(ev.get("stage") or "").strip()
        desc = str(ev.get("description") or "").strip()
        fpath = str(ev.get("file_path") or "").strip()
        key = (fname or desc, fpath or fname)
        if not (fname or desc) or key in seen:
            continue
        seen.add(key)
        meta = f"{etype}, {stage}" if stage else etype
        if fname and desc:
            lines.append(f"- `{fname}` ({meta}): {desc}")
        elif fname:
            lines.append(f"- `{fname}` ({meta})")
        else:
            lines.append(f"- {desc} ({meta})")

    if not lines:
        ev_sum = str(bug_data.get("evidence_summary") or "").strip()
        if ev_sum and ev_sum.lower() != "no visual evidence captured":
            lines.append(f"- {ev_sum}")
    if not lines:
        lines.append("- No visual evidence files were captured during this run.")
    return lines


def _extract_line_section_label(raw_line: str) -> Optional[Tuple[str, str]]:
    """Return (clean_label, inline_rest) if raw_line starts a section heading/label."""
    s = raw_line.strip()
    if not s:
        return None
    m_md = re.match(r"^#{1,6}\s+(.+?)\s*#*$", s)
    if m_md:
        return _strip_leading_section_index(re.sub(r"[:\s]+$", "", m_md.group(1))), ""
    m_bold = re.match(r"^\*\*([^*]{1,75}?)\*\*(?:\s+(.*))?$", s)
    if m_bold:
        rest = (m_bold.group(2) or "").lstrip(":").strip()
        return _strip_leading_section_index(m_bold.group(1).rstrip(":").strip()), rest
    m_colon = re.match(r"^([A-Za-z][A-Za-z0-9 /&()._-]{1,55}):(?:\s+(.*))?$", s)
    if m_colon and "://" not in s:
        return _strip_leading_section_index(m_colon.group(1).strip()), (m_colon.group(2) or "").strip()
    m_num = re.match(r"^(\d+[\.)])\s+([A-Za-z][A-Za-z0-9 /&()._-]{1,50}):?$", s)
    if m_num and not s.endswith("."):
        return _strip_leading_section_index(m_num.group(2).strip()), ""
    if s.lower() in ("evidence", "visual evidence", "attachments", "screenshots"):
        return s, ""
    return None


def ensure_evidence_in_report(
    report_text: str,
    evidence_list: Optional[List[Dict[str, Any]]],
    bug_data: Optional[Dict[str, Any]] = None,
) -> str:
    """Guarantee that Coroner/uploaded evidence filenames appear under the report's Evidence section (if present)."""
    text = (report_text or "").strip()
    if not text or not evidence_list:
        return text

    valid_items = [
        ev for ev in evidence_list
        if isinstance(ev, dict) and (ev.get("file_name") or ev.get("filename") or ev.get("description"))
    ]
    if not valid_items:
        return text

    lines = text.splitlines()
    ev_header_idx: Optional[int] = None
    ev_inline_rest = ""
    section_end_idx = len(lines)

    header_is_md = lines[ev_header_idx].strip().startswith("#") if ev_header_idx is not None else False
    for idx, line in enumerate(lines):
        parsed = _extract_line_section_label(line)
        if not parsed:
            continue
        lbl, rest = parsed
        slot = _classify_section(lbl)
        if ev_header_idx is None:
            if slot == "evidence":
                ev_header_idx = idx
                ev_inline_rest = rest
                header_is_md = line.strip().startswith("#")
        else:
            # Never terminate an Evidence section on nested evidence sub-labels (e.g. 'Screenshot: ...', 'Video: ...')
            if slot == "evidence":
                continue
            # Inside a Markdown-headed Evidence section, ignore non-section key:value lines unless standalone heading/keyword
            s_cur = line.strip()
            if header_is_md and not s_cur.startswith(("#", "**")) and rest and not _COMMON_SECTION_KW_RE.search(lbl):
                continue
            section_end_idx = idx
            break

    if ev_header_idx is None:
        # Respect templates that intentionally omit an Evidence section (files are still uploaded as Jira attachments).
        return text

    body_lines = lines[ev_header_idx + 1 : section_end_idx]
    existing_slice = "\n".join(([ev_inline_rest] if ev_inline_rest else []) + body_lines).lower()

    missing_items = []
    for ev in valid_items:
        fname = str(ev.get("file_name") or ev.get("filename") or "").strip()
        if fname and fname.lower() not in existing_slice:
            missing_items.append(ev)

    if not missing_items:
        return text

    def _is_empty_ev_placeholder(val: str) -> bool:
        v = val.strip().lower()
        if not v:
            return False
        if "no visual evidence" in v or v in ("none", "none.", "none captured", "none captured.", "n/a", "na", "no evidence", "pending", "- none"):
            return True
        if re.fullmatch(r"[\[{<].*[\]}>]", v):
            return True
        return False

    # Clean out placeholder-only lines in the Evidence section when adding real captured files
    cleaned_body: List[str] = []
    for bl in body_lines:
        stripped_bl = re.sub(r"^[-*•]\s+", "", bl.strip())
        if _is_empty_ev_placeholder(stripped_bl):
            continue
        cleaned_body.append(bl)

    # Clear inline placeholder text if present while preserving **bold** closing delimiters
    header_line = lines[ev_header_idx]
    if ev_inline_rest and _is_empty_ev_placeholder(ev_inline_rest):
        if re.match(r"^\*\*[^*]+:\*\*\s+", header_line.strip()):
            header_line = re.sub(r"(:\*\*)\s+.*$", r"\1", header_line).rstrip()
        elif re.match(r"^\*\*[^*]+\*\*\s*:\s+", header_line.strip()):
            header_line = re.sub(r"(\*\*\s*:)\s+.*$", r"\1", header_line).rstrip()
        else:
            header_line = re.sub(r":\s+.*$", ":", header_line).rstrip()

    # Trim trailing blank lines inside body before appending missing evidence items
    trailing_blanks: List[str] = []
    while cleaned_body and not cleaned_body[-1].strip():
        trailing_blanks.insert(0, cleaned_body.pop())

    new_ev_lines = _build_evidence_lines(missing_items, bug_data or {})
    rebuilt_section = [header_line] + cleaned_body + new_ev_lines + trailing_blanks
    updated_lines = lines[:ev_header_idx] + rebuilt_section + lines[section_end_idx:]
    return "\n".join(updated_lines).strip()


def _format_steps_list(steps_raw: Any) -> List[str]:
    if isinstance(steps_raw, list):
        items = [str(s).strip() for s in steps_raw if str(s).strip()]
    elif isinstance(steps_raw, str):
        items = [line.strip() for line in steps_raw.splitlines() if line.strip()]
    else:
        items = []

    cleaned_items: List[str] = []
    for idx, step in enumerate(items, start=1):
        step_clean = re.sub(r"^\d+[\.)]\s+", "", step).strip()
        cleaned_items.append(f"{idx}. {step_clean}")

    if not cleaned_items:
        cleaned_items.append("1. Navigate to the affected feature in the target environment and reproduce the reported action.")
    return cleaned_items


def _populate_section_values(
    bug_data: Dict[str, Any],
    judge_verdict: Dict[str, Any],
    project_info: Dict[str, Any],
    evidence_list: Optional[List[Dict[str, Any]]] = None,
    bug_report_id: Optional[int] = None,
) -> Dict[str, str]:
    """Compute clean, consistent values for all standard bug report slots."""
    raw_desc = clean_bug_description(bug_data.get("description") or "")
    title = clean_bug_title(bug_data.get("title"), fallback_desc=raw_desc)
    module = str(bug_data.get("module") or "General").strip()
    label = str(bug_data.get("label") or "Front-End").strip()
    severity = str(judge_verdict.get("severity_adjustment") or bug_data.get("severity") or "medium").strip().capitalize()
    root_cause = clean_bug_description(judge_verdict.get("final_root_cause") or bug_data.get("root_cause") or "")
    judge_notes = clean_bug_description(judge_verdict.get("judge_notes") or "")

    app_url = (project_info.get("app_url") or "").strip()
    env_custom = str(bug_data.get("environment") or "").strip()
    env_parts: List[str] = []
    if project_info.get("name"):
        env_parts.append(f"- **Project:** {project_info['name']}")
    if app_url:
        env_parts.append(f"- **App URL:** {app_url}")
    if env_custom and env_custom.lower() not in (app_url.lower(), (project_info.get("name") or "").lower()):
        env_parts.append(f"- **Details:** {env_custom}")
    env_parts.append(f"- **Component / Label:** {module} ({label}) | **Severity:** {severity}")
    environment = "\n".join(env_parts)

    description = raw_desc or title
    if root_cause and root_cause.lower() not in description.lower():
        description_with_rc = f"{description}\n\n**Verified Root Cause:** {root_cause}"
    else:
        description_with_rc = description

    expected = clean_bug_description(
        judge_verdict.get("verified_expected_behavior")
        or bug_data.get("expected_behavior")
        or ""
    )
    if not expected:
        expected = f"The {module} workflow should process valid input accurately and match documented business rules."

    actual = clean_bug_description(
        judge_verdict.get("verified_actual_behavior")
        or bug_data.get("actual_behavior")
        or ""
    )
    if not actual:
        actual = raw_desc or f"The defect occurred during the {module} flow as observed in testing."

    steps = "\n".join(_format_steps_list(bug_data.get("steps_to_reproduce")))
    evidence = "\n".join(_build_evidence_lines(evidence_list, bug_data))

    return {
        "bug_id": f"Bug #{bug_report_id}" if bug_report_id else "Draft Bug Report",
        "title": title,
        "summary": raw_desc.splitlines()[0] if raw_desc else title,
        "environment": environment,
        "description": description_with_rc,
        "raw_description": description,
        "root_cause": root_cause or "Under investigation",
        "steps": steps,
        "expected": expected,
        "actual": actual,
        "evidence": evidence,
        "severity": severity,
        "module": module,
        "label": label,
        "project": str(project_info.get("name") or "Project"),
        "date": datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
        "preconditions": f"User has access to `{app_url or project_info.get('name', 'the application')}`.",
        "impact": f"Severity `{severity}` issue affecting the `{module}` (`{label}`) flow.",
        "notes": judge_notes or "Verified by Judge Agent.",
    }


def _render_deterministic_template(
    template: str,
    bug_data: Dict[str, Any],
    judge_verdict: Dict[str, Any],
    project_info: Dict[str, Any],
    evidence_list: Optional[List[Dict[str, Any]]] = None,
    bug_report_id: Optional[int] = None,
) -> str:
    """Render a bug report deterministically following parsed template sections as they are."""
    sections = parse_template_sections(template)
    values = _populate_section_values(
        bug_data=bug_data,
        judge_verdict=judge_verdict,
        project_info=project_info,
        evidence_list=evidence_list,
        bug_report_id=bug_report_id,
    )
    if not sections:
        return ""

    # If template has no separate Root Cause section, keep verified root cause inside Description;
    # otherwise keep Description focused on symptom so Root Cause isn't duplicated.
    has_root_cause_section = any(_classify_section(s["clean_label"]) == "root_cause" for s in sections)
    if has_root_cause_section:
        values["description"] = values["raw_description"]

    blocks: List[str] = []
    for sec in sections:
        style = sec["style"]
        raw_label = sec["label"]
        clean_lbl = sec["clean_label"]
        slot = _classify_section(clean_lbl)
        body = values.get(slot) or values["description"]

        if style == "markdown_heading":
            prefix = sec["heading_prefix"]
            # Handle template header patterns like "# Bug Report: [Title]"
            if "[" in raw_label and "]" in raw_label and slot == "title":
                rendered_heading = re.sub(r"\[[^\]]+\]", values["title"], raw_label)
                blocks.append(f"{prefix} {rendered_heading}")
            else:
                blocks.append(f"{prefix} {raw_label}\n{body}")
        elif style == "bullet_bold":
            val_single = body.replace("\n", " | ") if "\n" in body else body
            blocks.append(f"- **{raw_label}:** {val_single}")
        elif style == "bold_inline":
            colon = ":" if sec.get("has_colon") else ""
            if "\n" in body or slot in ("steps", "evidence", "environment", "description"):
                blocks.append(f"**{raw_label}{colon}**\n{body}")
            else:
                blocks.append(f"**{raw_label}{colon}** {body}")
        elif style == "bold_heading":
            blocks.append(f"**{raw_label}**\n{body}")
        elif style == "colon_label":
            if "\n" in body or slot in ("steps", "evidence", "environment", "description"):
                blocks.append(f"**{raw_label}:**\n{body}")
            else:
                blocks.append(f"**{raw_label}:** {body}")
        elif style == "numbered_heading":
            blocks.append(f"### {raw_label}\n{body}")
        else:
            # Bare section label style (e.g. Title / Environment / Description / ...)
            blocks.append(f"### {raw_label}\n{body}")

    return "\n\n".join(blocks).strip()


class ReporterAgent(BaseAgent):
    """Bug report authoring agent."""

    def __init__(self, llm_router: LLMRouter):
        super().__init__(
            name="Reporter",
            role="Bug Report Author",
            color="#22c55e",
            icon="📝",
        )
        self.llm = llm_router
        self.last_llm_degraded = False

    async def generate_report(
        self,
        bug_data: Dict[str, Any],
        judge_verdict: Dict[str, Any],
        report_format_template: Optional[str],
        project_info: Dict[str, Any],
        project_id: int,
        bug_report_id: Optional[int] = None,
        evidence_list: Optional[List[Dict[str, Any]]] = None,
    ) -> str:
        """Compose the bug report adhering strictly to the Project's Report Format template as-is."""
        start_time = datetime.utcnow()
        cleaned_template = strip_report_format_preamble(report_format_template)
        parsed_sections = parse_template_sections(cleaned_template) if cleaned_template else []

        values = _populate_section_values(
            bug_data=bug_data,
            judge_verdict=judge_verdict,
            project_info=project_info,
            evidence_list=evidence_list,
            bug_report_id=bug_report_id,
        )

        if cleaned_template:
            section_list_str = (
                "\n".join(f"  {i+1}. {s['label']}" for i, s in enumerate(parsed_sections))
                if parsed_sections
                else "(Follow exact structure shown in template below)"
            )
            system_prompt = (
                "You are the Reporter AI Agent in QA Officer.\n"
                "YOUR #1 RULE: Follow the Project's Report Format template AS IT IS.\n\n"
                "STRICT FORMATTING RULES WHEN A REPORT FORMAT TEMPLATE IS PROVIDED:\n"
                "1. Preserve the EXACT section names, exact wording/spelling, exact casing, and exact order from the template.\n"
                "2. Do NOT add extra sections, standalone metadata headings, summaries, root cause sections, dividers (`---`), or emojis unless they are explicitly present in the template.\n"
                "   - If the template does NOT have a separate Root Cause section, weave the Judge-confirmed root cause concisely into the `Description` or `Actual Behavior` prose without adding a separate Root Cause heading.\n"
                "3. Replace any template placeholders (`[...]`, `{{...}}`, `<...>`) with clear, accurate content from the investigation.\n"
                "4. If the template uses plain bare section labels (e.g., `Title`, `Environment`, `Description`), format each section header using `### <Exact Template Label>` followed cleanly by its content (numbered `1. ` list for steps, concise `- ` bullets for environment and evidence). If the template already specifies `#`, `##`, `###`, `**Label:**`, or `Label:`, preserve that exact header style.\n"
                "5. Ground EVERY claim, number, URL, and filename strictly in the investigation facts below. List exact captured evidence filenames when an Evidence/Attachments section exists.\n"
                "6. Output ONLY the final bug report itself — no conversational greeting, no explanation, and NO outer ```markdown fences."
            )
            template_block = (
                f"=== AUTHORITATIVE PROJECT REPORT FORMAT (FOLLOW AS-IS) ===\n"
                f"{cleaned_template}\n\n"
                f"Expected Sections in Exact Order:\n{section_list_str}"
            )
        else:
            system_prompt = (
                "You are the Reporter AI Agent in QA Officer. "
                "Generate a crisp, structured, Jira-ready bug report in clean Markdown covering: "
                "Title, Environment, Description, Steps to Reproduce, Expected Behavior, Actual Behavior, Root Cause, and Evidence.\n"
                "Be concise, factual, and grounded in the investigation findings and captured evidence filenames. "
                "Output ONLY the bug report itself without conversational intro/outro or outer ```markdown fences."
            )
            template_block = "No custom Project Report Format configured; use clean Standard QA Markdown sections."

        evidence_lines = _build_evidence_lines(evidence_list, bug_data)
        user_content = (
            f"Project: {values['project']}\n"
            f"Target URL (App URL): {project_info.get('app_url') or 'Not configured'}\n"
            f"Target Repo: {project_info.get('repo_url') or 'Not configured'}\n"
            f"Module: {values['module']} | Label: {values['label']} | Severity: {values['severity']}\n\n"
            f"Clean Bug Title: {values['title']}\n"
            f"Symptom / Description:\n{values['raw_description']}\n\n"
            f"Judge Confirmed Root Cause:\n{values['root_cause']}\n\n"
            f"Judge Notes:\n{values['notes']}\n\n"
            f"Steps To Reproduce:\n{values['steps']}\n\n"
            f"Expected Behavior:\n{values['expected']}\n\n"
            f"Actual Behavior:\n{values['actual']}\n\n"
            f"Captured Evidence Assets:\n" + "\n".join(evidence_lines) + "\n\n"
            f"{template_block}"
        )

        llm_failure_err: Optional[str] = None
        llm_degraded = False
        fallback_reason: Optional[str] = None
        report_text = ""

        try:
            response = await self.llm.chat(
                messages=[{"role": "user", "content": user_content}],
                system_prompt=system_prompt,
                temperature=0.1,
            )
            candidate = _strip_llm_wrapper_chatter(
                response.content,
                section_labels=[s["clean_label"] for s in parsed_sections],
            )
            if cleaned_template and parsed_sections:
                ok, reason = _matches_template_strictly(candidate, parsed_sections)
                if ok:
                    report_text = candidate
                else:
                    fallback_reason = reason
                    logger.warning(f"Reporter LLM output drifted from Project Report Format ({reason}); enforcing strict template rendering.")
                    report_text = self._fallback_report(
                        bug_data=bug_data,
                        judge_verdict=judge_verdict,
                        project_info=project_info,
                        report_format_template=cleaned_template,
                        evidence_list=evidence_list,
                        bug_report_id=bug_report_id,
                    )
            elif candidate:
                report_text = candidate
        except Exception as e:
            llm_degraded = True
            if self.llm.is_configured():
                llm_failure_err = self.llm._redact_secrets(str(e))
                logger.error(f"Reporter LLM generation error: {e}")

        if not report_text:
            report_text = self._fallback_report(
                bug_data=bug_data,
                judge_verdict=judge_verdict,
                project_info=project_info,
                report_format_template=cleaned_template,
                evidence_list=evidence_list,
                bug_report_id=bug_report_id,
            )

        report_text = ensure_evidence_in_report(report_text, evidence_list, bug_data)
        self.last_llm_degraded = llm_degraded

        out_payload: Dict[str, Any] = {
            "report_preview": report_text[:200] + ("..." if len(report_text) > 200 else ""),
            "custom_template_used": bool(cleaned_template),
            "llm_degraded": llm_degraded,
        }
        if fallback_reason:
            out_payload["template_enforced_deterministically"] = fallback_reason
        if llm_failure_err:
            out_payload["llm_error"] = llm_failure_err

        await self.log_action(
            action="generate_bug_report",
            input_data={
                "bug_title": values["title"],
                "module": values["module"],
                "has_custom_format": bool(cleaned_template),
            },
            output_data=out_payload,
            status="failed" if llm_failure_err else ("degraded" if llm_degraded else "success"),
            bug_report_id=bug_report_id,
            project_id=project_id,
            module=values["module"] or "Bug Reporting",
            level="error" if llm_failure_err else ("warning" if llm_degraded else "success"),
            notify=bool(llm_failure_err),
            started_at=start_time,
        )
        return report_text

    def _fallback_report(
        self,
        bug_data: Dict[str, Any],
        judge_verdict: Dict[str, Any],
        project_info: Dict[str, Any],
        report_format_template: Optional[str] = None,
        evidence_list: Optional[List[Dict[str, Any]]] = None,
        bug_report_id: Optional[int] = None,
    ) -> str:
        """Render clean deterministic bug report honoring report_format_template when provided."""
        cleaned_template = strip_report_format_preamble(report_format_template)
        if cleaned_template:
            rendered = _render_deterministic_template(
                template=cleaned_template,
                bug_data=bug_data,
                judge_verdict=judge_verdict,
                project_info=project_info,
                evidence_list=evidence_list,
                bug_report_id=bug_report_id,
            )
            if rendered:
                return rendered

        values = _populate_section_values(
            bug_data=bug_data,
            judge_verdict=judge_verdict,
            project_info=project_info,
            evidence_list=evidence_list,
            bug_report_id=bug_report_id,
        )
        return (
            f"### Title\n{values['title']}\n\n"
            f"### Environment\n{values['environment']}\n\n"
            f"### Description\n{values['raw_description']}\n\n"
            f"### Steps to Reproduce\n{values['steps']}\n\n"
            f"### Expected Behavior\n{values['expected']}\n\n"
            f"### Actual Behavior\n{values['actual']}\n\n"
            f"### Root Cause\n{values['root_cause']}\n\n"
            f"### Evidence\n{values['evidence']}"
        )

    async def run(self, *args, **kwargs) -> Any:
        return await self.generate_report(*args, **kwargs)
