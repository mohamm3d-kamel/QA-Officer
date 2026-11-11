"""Environment Credentials Service - Secure parsing, masking, prompt formatting, and role selection."""
import json
import re
from typing import Any, Dict, List, Optional

from sqlalchemy import select

import config
from database import async_session

_BULLET_MASK = "\u2022" * 4

_ROLE_HINT_PATTERNS: List[tuple[str, str]] = [
    ("customer", r"\b(?:customer\s*service|customer\s*care|qacustomer|cs\s+(?:agent|portal|user|role))\b"),
    ("operation", r"\b(?:operations?|ops\s+(?:role|user|portal|team))\b"),
    ("merchant", r"\bmerchants?\b"),
    ("tenant", r"\btenants?\b"),
]


def is_masked_password(value: Any) -> bool:
    """Return True when value is an API mask placeholder rather than a new plaintext password."""
    text = str(value or "").strip()
    return bool(text and (text.startswith(_BULLET_MASK) or text == "<redacted>"))


def parse_environment_credentials(raw: Any) -> List[Dict[str, str]]:
    """Normalize environment credentials from a JSON string or list of mappings."""
    if raw is None or raw == "":
        return []

    data = raw
    if isinstance(raw, str):
        stripped = raw.strip()
        if not stripped:
            return []
        try:
            data = json.loads(stripped)
        except Exception:
            return []

    if not isinstance(data, (list, tuple)):
        return []

    entries: List[Dict[str, str]] = []
    for item in data:
        if hasattr(item, "model_dump"):
            item = item.model_dump()
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or item.get("name") or "").strip()
        username = str(item.get("username") or item.get("email") or item.get("user") or "").strip()
        raw_pw = item.get("password")
        if raw_pw is None:
            raw_pw = item.get("pass")
        password = "" if raw_pw is None else str(raw_pw)
        if not (role or username or password):
            continue
        entries.append({
            "role": role,
            "username": username,
            "password": password,
        })
    return entries


def serialize_environment_credentials(raw: Any) -> Optional[str]:
    """Serialize normalized credentials into JSON text (or None when empty)."""
    parsed = parse_environment_credentials(raw)
    if not parsed:
        return None
    return json.dumps(parsed, ensure_ascii=False)


def mask_environment_credentials(raw: Any) -> List[Dict[str, str]]:
    """Return a copy of credentials safe for API responses and logs with passwords masked."""
    masked: List[Dict[str, str]] = []
    for entry in parse_environment_credentials(raw):
        pw = entry.get("password") or ""
        masked.append({
            "role": entry.get("role") or "",
            "username": entry.get("username") or "",
            "password": pw if is_masked_password(pw) else (config.mask_secret(pw) if pw else ""),
        })
    return masked


def merge_environment_credentials(submitted: Any, existing_raw: Any) -> List[Dict[str, str]]:
    """Merge updated credentials while preserving stored plaintext passwords when masked placeholders are submitted."""
    submitted_entries = parse_environment_credentials(submitted)
    existing_entries = parse_environment_credentials(existing_raw)

    by_pair: Dict[tuple[str, str], str] = {}
    by_role: Dict[str, str] = {}
    by_user: Dict[str, str] = {}
    for ex in existing_entries:
        pw = ex.get("password") or ""
        if not pw or is_masked_password(pw):
            continue
        rl = (ex.get("role") or "").strip().lower()
        un = (ex.get("username") or "").strip().lower()
        if rl or un:
            by_pair[(rl, un)] = pw
        if rl and rl not in by_role:
            by_role[rl] = pw
        if un and un not in by_user:
            by_user[un] = pw

    merged: List[Dict[str, str]] = []
    for idx, entry in enumerate(submitted_entries):
        role = entry.get("role") or ""
        username = entry.get("username") or ""
        password = entry.get("password") or ""

        if is_masked_password(password):
            rl = role.strip().lower()
            un = username.strip().lower()
            preserved = (
                by_pair.get((rl, un))
                or (by_role.get(rl) if rl else None)
                or (by_user.get(un) if un else None)
            )
            if not preserved and idx < len(existing_entries):
                cand = existing_entries[idx].get("password") or ""
                if cand and not is_masked_password(cand):
                    preserved = cand
            password = preserved or ""

        merged.append({
            "role": role,
            "username": username,
            "password": password,
        })
    return merged


def format_credentials_for_prompt(raw: Any, app_url: str = "") -> str:
    """Format configured role accounts cleanly for AI agent prompts WITHOUT exposing plaintext passwords."""
    if isinstance(raw, dict) and "environment_credentials" in raw:
        app_url = app_url or (raw.get("app_url") or "")
        raw = raw.get("environment_credentials")
    entries = parse_environment_credentials(raw)
    if not entries:
        return ""

    url_note = f" (login URL: {app_url.strip()})" if (app_url or "").strip() else ""
    lines = [f"Configured Environment Role Accounts{url_note}:"]
    for entry in entries:
        role = entry.get("role") or "Account"
        username = entry.get("username") or "N/A"
        pw_note = (
            "password stored securely for automated Playwright login"
            if (entry.get("password") or "").strip()
            else "no password configured"
        )
        lines.append(f"- {role}: username `{username}` ({pw_note})")
    return "\n".join(lines)


def _find_best_cred_in_text(creds: List[Dict[str, str]], text: str) -> Optional[Dict[str, str]]:
    """Pick the credential entry matching the earliest/strongest role mention in text."""
    low = (text or "").lower()
    if not low.strip() or not creds:
        return None

    # 1. Exact username mention wins immediately
    for entry in creds:
        uname = (entry.get("username") or "").strip().lower()
        if uname and uname in low:
            return entry

    # 2. Match exact configured role name OR generic role hint keywords, choosing earliest mention
    best_pos: Optional[int] = None
    best_entry: Optional[Dict[str, str]] = None

    for entry in creds:
        r_low = (entry.get("role") or "").strip().lower()
        u_low = (entry.get("username") or "").strip().lower()
        patterns: List[str] = []
        if r_low:
            patterns.append(rf"\b{re.escape(r_low)}\b")
        for key, pat in _ROLE_HINT_PATTERNS:
            if key in r_low or key in u_low:
                patterns.append(pat)

        for pat in patterns:
            m = re.search(pat, low, re.IGNORECASE)
            if m and (best_pos is None or m.start() < best_pos):
                best_pos = m.start()
                best_entry = entry

    return best_entry


def select_environment_credential(
    raw_or_creds: Any,
    bug_data: Optional[Dict[str, Any]] = None,
    preferred_role: Optional[str] = None,
) -> Optional[Dict[str, str]]:
    """Choose the best matching environment credential for a bug/ticket or preferred role."""
    all_creds = parse_environment_credentials(raw_or_creds)
    if not all_creds:
        return None

    usable = [
        c for c in all_creds
        if c.get("username") and c.get("password") and not is_masked_password(c.get("password"))
    ]
    pool = usable or [c for c in all_creds if c.get("username")]
    if not pool:
        return None

    if preferred_role:
        pref = preferred_role.strip().lower()
        for c in pool:
            if (c.get("role") or "").strip().lower() == pref:
                return c
        for c in pool:
            if pref in (c.get("role") or "").lower() or pref in (c.get("username") or "").lower():
                return c

    if isinstance(bug_data, dict):
        primary_text = " ".join(
            str(bug_data.get(k) or "")
            for k in ("title", "module", "environment", "target_role", "role")
        )
        matched = _find_best_cred_in_text(pool, primary_text)
        if matched:
            return matched

        steps = bug_data.get("steps_to_reproduce")
        steps_str = "\n".join(str(s) for s in steps) if isinstance(steps, list) else str(steps or "")
        comments = bug_data.get("jira_comments")
        comments_str = json.dumps(comments, ensure_ascii=False, default=str) if comments else ""
        secondary_text = " ".join([
            str(bug_data.get("description") or ""),
            str(bug_data.get("root_cause") or ""),
            steps_str,
            comments_str,
        ])
        matched = _find_best_cred_in_text(pool, secondary_text)
        if matched:
            return matched

    # Default primary portal role: Tenant when available, else first usable account
    for c in pool:
        if "tenant" in (c.get("role") or "").lower():
            return c
    return pool[0]


async def resolve_project_runtime_credentials(
    project_id: Optional[int] = None,
    project_info: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, str]]:
    """Resolve plaintext runtime credentials for Playwright login without mutating API dicts."""
    info = project_info or {}
    injected = parse_environment_credentials(info.get("_runtime_credentials"))
    if injected:
        usable = [c for c in injected if c.get("password") and not is_masked_password(c.get("password"))]
        if usable:
            return usable

    from_info = parse_environment_credentials(info.get("environment_credentials"))
    if from_info:
        usable_info = [c for c in from_info if c.get("password") and not is_masked_password(c.get("password"))]
        if usable_info:
            return usable_info

    pid = project_id or info.get("id")
    if pid:
        from models.project import Project

        async with async_session() as session:
            row = (
                await session.execute(
                    select(Project.environment_credentials).where(Project.id == int(pid))
                )
            ).scalar_one_or_none()
            return parse_environment_credentials(row)

    return []
