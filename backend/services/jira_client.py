"""Jira Cloud API Client.

Handles ticket creation, status updates, comments, project/issue search (API v3 search/jql),
backlog placement, and attachment uploads.
"""
import asyncio
import logging
import mimetypes
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import httpx

logger = logging.getLogger(__name__)

_REAL_JIRA_KEY_RE = re.compile(r"^([A-Z][A-Z0-9]{1,12})-([1-9]\d*)$")


def is_real_jira_issue_key(key: Optional[str], expected_project_key: Optional[str] = None) -> bool:
    """Return True if key looks like a real Jira Cloud issue key (PROJECT-123) matching expected_project_key."""
    if not key or not isinstance(key, str):
        return False
    m = _REAL_JIRA_KEY_RE.fullmatch(key.strip().upper())
    if not m:
        return False
    if expected_project_key and m.group(1) != expected_project_key.strip().upper():
        return False
    return True


def sanitize_jira_summary(summary: str, max_len: int = 255) -> str:
    """Normalize summary into a single line <= 255 chars (Jira rejects CR/LF in summary)."""
    cleaned = " ".join(str(summary or "").replace("\r", " ").replace("\n", " ").split())
    if not cleaned:
        return "Defect reported by QA Officer"
    if len(cleaned) > max_len:
        return cleaned[: max_len - 3].rstrip() + "..."
    return cleaned


_INLINE_MD_TOKEN_RE = re.compile(
    r"(\*\*[^*\n]+\*\*|`[^`\n]+`|\[[^\]\n]+\]\([^)\n]+\)|\*[^*\n]+\*)"
)


def _parse_inline_adf(text: str) -> List[Dict[str, Any]]:
    """Convert inline Markdown (**bold**, *italic*, `code`, [text](url)) to valid non-empty ADF text nodes."""
    raw = str(text or "")
    if not raw:
        return []
    nodes: List[Dict[str, Any]] = []
    pos = 0
    for m in _INLINE_MD_TOKEN_RE.finditer(raw):
        if m.start() > pos:
            plain = raw[pos:m.start()]
            if plain:
                nodes.append({"type": "text", "text": plain})
        tok = m.group(0)
        if tok.startswith("**") and tok.endswith("**") and len(tok) > 4:
            inner = tok[2:-2]
            if inner:
                nodes.append({"type": "text", "text": inner, "marks": [{"type": "strong"}]})
        elif tok.startswith("`") and tok.endswith("`") and len(tok) > 2:
            inner = tok[1:-1]
            if inner:
                nodes.append({"type": "text", "text": inner, "marks": [{"type": "code"}]})
        elif tok.startswith("[") and tok.endswith(")"):
            lm = re.fullmatch(r"\[([^\]\n]+)\]\(([^)\n]+)\)", tok)
            if lm:
                label, href = lm.group(1), lm.group(2).strip()
                if label:
                    if href.startswith(("http://", "https://")):
                        nodes.append({"type": "text", "text": label, "marks": [{"type": "link", "attrs": {"href": href}}]})
                    else:
                        nodes.append({"type": "text", "text": f"{label} ({href})"})
            else:
                nodes.append({"type": "text", "text": tok})
        elif tok.startswith("*") and tok.endswith("*") and len(tok) > 2:
            inner = tok[1:-1]
            if inner:
                nodes.append({"type": "text", "text": inner, "marks": [{"type": "em"}]})
        else:
            if tok:
                nodes.append({"type": "text", "text": tok})
        pos = m.end()

    if pos < len(raw):
        tail = raw[pos:]
        if tail:
            nodes.append({"type": "text", "text": tail})
    return nodes


def markdown_to_adf(markdown_text: str) -> Dict[str, Any]:
    """Convert structured Markdown text into valid Jira Cloud Atlassian Document Format (ADF v1)."""
    text = str(markdown_text or "").replace("\r\n", "\n").strip()[:30000]
    if not text:
        return {
            "version": 1,
            "type": "doc",
            "content": [{"type": "paragraph", "content": [{"type": "text", "text": "No description provided."}]}],
        }

    lines = text.split("\n")
    content: List[Dict[str, Any]] = []
    i = 0
    n = len(lines)

    while i < n:
        raw_line = lines[i]
        stripped = raw_line.strip()

        if not stripped:
            i += 1
            continue

        # 1. Fenced code block ```lang ... ```
        if stripped.startswith("```"):
            lang = stripped[3:].strip()
            code_lines: List[str] = []
            i += 1
            while i < n and not lines[i].strip().startswith("```"):
                code_lines.append(lines[i])
                i += 1
            if i < n and lines[i].strip().startswith("```"):
                i += 1
            code_body = "\n".join(code_lines).strip("\n")
            block: Dict[str, Any] = {"type": "codeBlock"}
            if lang:
                block["attrs"] = {"language": lang}
            if code_body:
                block["content"] = [{"type": "text", "text": code_body}]
            content.append(block)
            continue

        # 2. Horizontal rule (---, ***, ___)
        if re.fullmatch(r"[-*_]{3,}", stripped):
            content.append({"type": "rule"})
            i += 1
            continue

        # 3. ATX Headings (# .. ######)
        m_head = re.match(r"^(#{1,6})\s+(.+?)\s*#*$", stripped)
        if m_head:
            level = min(6, max(1, len(m_head.group(1))))
            inline = _parse_inline_adf(m_head.group(2).strip())
            if inline:
                content.append({"type": "heading", "attrs": {"level": level}, "content": inline})
            i += 1
            continue

        # 4. Blockquote (> ...)
        if stripped.startswith("> "):
            quote_lines: List[str] = []
            while i < n and lines[i].strip().startswith(">"):
                quote_lines.append(re.sub(r"^>\s?", "", lines[i].strip()))
                i += 1
            q_inline = _parse_inline_adf(" ".join(l for l in quote_lines if l))
            if q_inline:
                content.append({"type": "blockquote", "content": [{"type": "paragraph", "content": q_inline}]})
            continue

        # 5. Bullet list (- / * / •)
        if re.match(r"^[-*•]\s+", stripped):
            items: List[Dict[str, Any]] = []
            while i < n:
                s = lines[i].strip()
                if not re.match(r"^[-*•]\s+", s):
                    break
                item_text = re.sub(r"^[-*•]\s+", "", s).strip()
                inline = _parse_inline_adf(item_text)
                if inline:
                    items.append({"type": "listItem", "content": [{"type": "paragraph", "content": inline}]})
                i += 1
            if items:
                content.append({"type": "bulletList", "content": items})
            continue

        # 6. Ordered list (1. / 1) )
        if re.match(r"^\d+[\.)]\s+", stripped):
            items = []
            while i < n:
                s = lines[i].strip()
                if not re.match(r"^\d+[\.)]\s+", s):
                    break
                item_text = re.sub(r"^\d+[\.)]\s+", "", s).strip()
                inline = _parse_inline_adf(item_text)
                if inline:
                    items.append({"type": "listItem", "content": [{"type": "paragraph", "content": inline}]})
                i += 1
            if items:
                content.append({"type": "orderedList", "content": items})
            continue

        # 7. Paragraph
        para_lines: List[str] = []
        while i < n:
            s = lines[i].strip()
            if (
                not s
                or s.startswith("```")
                or re.fullmatch(r"[-*_]{3,}", s)
                or re.match(r"^#{1,6}\s+", s)
                or s.startswith("> ")
                or re.match(r"^[-*•]\s+", s)
                or re.match(r"^\d+[\.)]\s+", s)
            ):
                break
            para_lines.append(s)
            i += 1

        if para_lines:
            para_inline: List[Dict[str, Any]] = []
            for idx_pl, pl in enumerate(para_lines):
                if idx_pl > 0:
                    para_inline.append({"type": "hardBreak"})
                para_inline.extend(_parse_inline_adf(pl))
            if para_inline:
                content.append({"type": "paragraph", "content": para_inline})

    if not content:
        content.append({"type": "paragraph", "content": [{"type": "text", "text": text[:1000]}]})

    return {"version": 1, "type": "doc", "content": content}


def _escape_jql(value: str) -> str:
    return (value or "").replace("\\", "\\\\").replace('"', '\\"')


def _text_to_adf(text: str) -> dict:
    """Compatibility wrapper converting Markdown/text into structured Jira ADF."""
    return markdown_to_adf(text)


class JiraClient:
    """Async Jira Cloud REST API v3 client."""

    def __init__(self, base_url: str, email: str, api_token: str):
        self.base_url = base_url.rstrip("/")
        self.api_url = f"{self.base_url}/rest/api/3"
        self.agile_url = f"{self.base_url}/rest/agile/1.0"
        self.auth = (email, api_token)
        self._headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    def _get_client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            auth=self.auth,
            headers=self._headers,
            timeout=30.0,
        )

    @staticmethod
    def _format_http_error(response: httpx.Response) -> str:
        """Extract actionable Jira error messages without exposing secrets."""
        status = response.status_code
        parts: List[str] = []
        try:
            data = response.json()
            if isinstance(data, dict):
                for msg in data.get("errorMessages") or []:
                    if msg:
                        parts.append(str(msg))
                errs = data.get("errors") or {}
                if isinstance(errs, dict):
                    for k, v in errs.items():
                        parts.append(f"{k}: {v}")
        except Exception:
            pass
        detail = "; ".join(parts) if parts else (response.text or response.reason_phrase or "Request failed")[:350]
        return f"Jira HTTP {status}: {detail}"

    @classmethod
    def extract_adf_text(cls, node: Any) -> str:
        """Recursively convert Atlassian Document Format (ADF) or string fields into plain text."""
        if not node:
            return ""
        if isinstance(node, str):
            return node
        if isinstance(node, list):
            return "\n".join(filter(None, (cls.extract_adf_text(item) for item in node)))
        if isinstance(node, dict):
            if node.get("type") == "text":
                return node.get("text", "")
            if node.get("type") == "hardBreak":
                return "\n"
            content = node.get("content") or []
            ntype = node.get("type")
            if ntype in ("paragraph", "heading"):
                parts = [cls.extract_adf_text(c) for c in content]
                return "".join(parts).strip()
            sep = "\n" if ntype in ("doc", "bulletList", "orderedList", "listItem", "blockquote", "codeBlock") else " "
            parts = [cls.extract_adf_text(c) for c in content]
            return sep.join(p for p in parts if p).strip()
        return str(node)

    async def _lookup_account_id(self, client: httpx.AsyncClient, query: str) -> Optional[str]:
        if not query:
            return None
        try:
            resp = await client.get(f"{self.api_url}/user/search", params={"query": query, "maxResults": 1})
            if resp.status_code == 200:
                users = resp.json()
                if isinstance(users, list) and users:
                    return users[0].get("accountId")
        except Exception as e:
            logger.debug(f"Jira reporter lookup failed for '{query}': {e}")
        return None

    async def create_issue(
        self,
        project_key: str,
        summary: str,
        description: str,
        issue_type: str = "Bug",
        priority: str = "Medium",
        labels: list[str] = None,
        reporter_name: str = None,
    ) -> dict:
        """Create a Jira issue with Markdown ADF description, optional reporter lookup, and safe field fallback."""
        clean_project = (project_key or "").strip().upper()
        clean_summary = sanitize_jira_summary(summary)
        clean_desc = (description or "").strip() or clean_summary
        adf_description = markdown_to_adf(clean_desc)

        fields: Dict[str, Any] = {
            "project": {"key": clean_project},
            "summary": clean_summary,
            "description": adf_description,
            "issuetype": {"name": issue_type},
        }
        if priority:
            fields["priority"] = {"name": priority.capitalize()}
        if labels:
            clean_labels = [re.sub(r"\s+", "-", str(lb).strip()) for lb in labels if str(lb).strip()]
            if clean_labels:
                fields["labels"] = clean_labels

        async with self._get_client() as client:
            if reporter_name:
                account_id = await self._lookup_account_id(client, reporter_name)
                if account_id:
                    fields["reporter"] = {"accountId": account_id}

            response = await client.post(f"{self.api_url}/issue", json={"fields": fields})

            # Graceful retry if project screen restricts optional fields (reporter, priority, or labels)
            if response.status_code == 400:
                try:
                    err_fields = (response.json().get("errors") or {}) if isinstance(response.json(), dict) else {}
                except Exception:
                    err_fields = {}
                retried_fields = dict(fields)
                removed_optional = []
                if "reporter" in retried_fields and ("reporter" in err_fields or not err_fields):
                    retried_fields.pop("reporter", None)
                    removed_optional.append("reporter")
                for opt_key in ("priority", "labels"):
                    if opt_key in err_fields and opt_key in retried_fields:
                        retried_fields.pop(opt_key, None)
                        removed_optional.append(opt_key)
                if removed_optional:
                    logger.warning(
                        f"Retrying Jira issue creation in {clean_project} without rejected optional field(s): {removed_optional}"
                    )
                    response = await client.post(f"{self.api_url}/issue", json={"fields": retried_fields})

            if not response.is_success:
                raise RuntimeError(self._format_http_error(response))

            data = response.json()
            logger.info(f"Created Jira issue: {data['key']}")
            return {
                "id": data["id"],
                "key": data["key"],
                "url": f"{self.base_url}/browse/{data['key']}",
            }

    async def move_to_backlog(self, issue_key: str) -> bool:
        """Ensure an issue is placed in the Jira Software Backlog (not assigned to a sprint)."""
        try:
            async with self._get_client() as client:
                response = await client.post(
                    f"{self.agile_url}/backlog/issue",
                    json={"issues": [issue_key]},
                )
                if response.is_success:
                    logger.info(f"Placed {issue_key} into Jira Software Backlog")
                    return True
                logger.info(
                    f"Agile backlog endpoint returned {response.status_code} for {issue_key}: {response.text[:160]}"
                )
                return False
        except Exception as e:
            logger.debug(f"Could not invoke Agile backlog endpoint for {issue_key}: {e}")
            return False

    async def add_comment(self, issue_key: str, comment: str) -> dict:
        """Add a Markdown-formatted ADF comment to a Jira issue."""
        adf_comment = markdown_to_adf(comment)
        payload = {"body": adf_comment}

        async with self._get_client() as client:
            response = await client.post(
                f"{self.api_url}/issue/{issue_key}/comment", json=payload
            )
            response.raise_for_status()
            return response.json()

    async def update_issue_description(self, issue_key: str, description: str) -> bool:
        """Update an existing Jira issue's description using converted ADF Markdown."""
        adf_description = markdown_to_adf((description or "").strip())
        async with self._get_client() as client:
            response = await client.put(
                f"{self.api_url}/issue/{issue_key}",
                json={"fields": {"description": adf_description}},
            )
            if not response.is_success:
                raise RuntimeError(self._format_http_error(response))
            return True

    @staticmethod
    def _guess_mime_type(file_name: str) -> str:
        ext = Path(file_name or "").suffix.lower()
        overrides = {
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".webp": "image/webp",
            ".gif": "image/gif",
            ".bmp": "image/bmp",
            ".webm": "video/webm",
            ".mp4": "video/mp4",
            ".mov": "video/quicktime",
            ".pdf": "application/pdf",
            ".har": "application/json",
            ".json": "application/json",
            ".log": "text/plain",
            ".txt": "text/plain",
            ".csv": "text/csv",
        }
        if ext in overrides:
            return overrides[ext]
        guessed, _ = mimetypes.guess_type(file_name or "")
        return guessed or "application/octet-stream"

    async def add_attachment(
        self,
        issue_key: str,
        file_path: str,
        file_name: Optional[str] = None,
    ) -> list[dict] | dict:
        """Upload an attachment to a Jira issue without blocking the event loop or leaking JSON Content-Type."""
        path_obj = Path(file_path)
        effective_name = (file_name or path_obj.name or "evidence.bin").strip()
        mime_type = self._guess_mime_type(effective_name)

        def _read_bytes(p: Path) -> bytes:
            with open(p, "rb") as f:
                return f.read()

        file_bytes = await asyncio.to_thread(_read_bytes, path_obj)

        # IMPORTANT: Omit "Content-Type": "application/json" for multipart uploads.
        upload_headers = {
            "Accept": "application/json",
            "X-Atlassian-Token": "no-check",
        }
        async with httpx.AsyncClient(auth=self.auth, headers=upload_headers, timeout=60.0) as client:
            files = {"file": (effective_name, file_bytes, mime_type)}
            response = await client.post(
                f"{self.api_url}/issue/{issue_key}/attachments",
                files=files,
            )
            if not response.is_success:
                raise RuntimeError(self._format_http_error(response))
            logger.info(f"Uploaded Jira attachment '{effective_name}' ({mime_type}) to {issue_key}")
            return response.json()

    async def transition_issue(self, issue_key: str, status_name: str) -> bool:
        """Transition a Jira issue to a new status."""
        async with self._get_client() as client:
            response = await client.get(f"{self.api_url}/issue/{issue_key}/transitions")
            response.raise_for_status()
            transitions = response.json()["transitions"]

            target = None
            for t in transitions:
                if t["name"].lower() == status_name.lower() or (t.get("to") or {}).get("name", "").lower() == status_name.lower():
                    target = t
                    break

            if not target:
                logger.warning(
                    f"Transition '{status_name}' not found for {issue_key}. "
                    f"Available: {[t['name'] for t in transitions]}"
                )
                return False

            payload = {"transition": {"id": target["id"]}}
            response = await client.post(
                f"{self.api_url}/issue/{issue_key}/transitions", json=payload
            )
            response.raise_for_status()
            logger.info(f"Transitioned {issue_key} to '{status_name}'")
            return True

    async def get_issue(self, issue_key: str) -> dict:
        """Get a Jira issue by key including description, comments, and attachments."""
        async with self._get_client() as client:
            response = await client.get(f"{self.api_url}/issue/{issue_key}")
            response.raise_for_status()
            data = response.json()
            fields = data.get("fields", {})
            comments_raw = ((fields.get("comment") or {}).get("comments")) or []
            comments = [
                {
                    "author": (c.get("author") or {}).get("displayName"),
                    "body": self.extract_adf_text(c.get("body"))[:400],
                    "created": c.get("created"),
                }
                for c in comments_raw[-5:]
            ]
            attachments = [
                {
                    "filename": a.get("filename"),
                    "mimeType": a.get("mimeType"),
                    "size": a.get("size"),
                }
                for a in (fields.get("attachment") or [])
            ]
            return {
                "id": data["id"],
                "key": data["key"],
                "project_key": (fields.get("project") or {}).get("key"),
                "summary": fields.get("summary"),
                "description": self.extract_adf_text(fields.get("description")),
                "status": (fields.get("status") or {}).get("name"),
                "issue_type": (fields.get("issuetype") or {}).get("name"),
                "priority": (fields.get("priority") or {}).get("name") if fields.get("priority") else None,
                "labels": fields.get("labels", []),
                "assignee": (fields.get("assignee") or {}).get("displayName") if fields.get("assignee") else None,
                "reporter": (fields.get("reporter") or {}).get("displayName") if fields.get("reporter") else None,
                "comments": comments,
                "attachments": attachments,
                "url": f"{self.base_url}/browse/{data['key']}",
            }

    async def list_projects(self, max_results: int = 50) -> List[Dict[str, Any]]:
        """List accessible Jira Cloud projects."""
        async with self._get_client() as client:
            response = await client.get(f"{self.api_url}/project/search", params={"maxResults": max_results})
            response.raise_for_status()
            values = response.json().get("values") or []
            return [
                {
                    "id": p.get("id"),
                    "key": p.get("key"),
                    "name": p.get("name"),
                    "projectTypeKey": p.get("projectTypeKey"),
                }
                for p in values
            ]

    async def resolve_project_keys(
        self, configured_key: Optional[str] = None, project_name: str = ""
    ) -> Tuple[List[str], List[Dict[str, Any]], bool]:
        """Verify configured_key on Jira; if absent, match by project_name or return all accessible projects."""
        projects = await self.list_projects()
        keys = [p["key"] for p in projects if p.get("key")]
        if configured_key and configured_key in keys:
            return [configured_key], projects, True

        matched: List[str] = []
        if project_name:
            name_lower = project_name.lower()
            for p in projects:
                pname = (p.get("name") or "").lower()
                if name_lower in pname or pname in name_lower:
                    matched.append(p["key"])
        if matched:
            return matched, projects, False
        return keys, projects, False

    async def search_jql(
        self, jql: str, max_results: int = 30, fields: Optional[List[str]] = None
    ) -> List[Dict[str, Any]]:
        """Run a JQL query using Jira Cloud API v3 /rest/api/3/search/jql."""
        req_fields = fields or [
            "summary",
            "status",
            "priority",
            "labels",
            "assignee",
            "project",
            "issuetype",
            "created",
            "updated",
        ]
        payload = {"jql": jql, "maxResults": max_results, "fields": req_fields}

        async with self._get_client() as client:
            response = await client.post(f"{self.api_url}/search/jql", json=payload)
            if not response.is_success:
                raise RuntimeError(self._format_http_error(response))
            data = response.json()

            issues = []
            for issue in data.get("issues", []):
                f = issue.get("fields") or {}
                issues.append({
                    "id": issue["id"],
                    "key": issue["key"],
                    "project_key": (f.get("project") or {}).get("key"),
                    "summary": f.get("summary"),
                    "status": (f.get("status") or {}).get("name"),
                    "issue_type": (f.get("issuetype") or {}).get("name"),
                    "priority": (f.get("priority") or {}).get("name") if f.get("priority") else None,
                    "labels": f.get("labels", []),
                    "assignee": (f.get("assignee") or {}).get("displayName") if f.get("assignee") else None,
                    "created": f.get("created"),
                    "updated": f.get("updated"),
                    "url": f"{self.base_url}/browse/{issue['key']}",
                })
            return issues

    @staticmethod
    def build_status_clause(status: Optional[str]) -> Optional[str]:
        """Build a JQL status clause; 'Backlog' also matches Scrum board backlog (sprint is EMPTY AND status = 'To Do')."""
        clean = (status or "").strip()
        if not clean:
            return None
        if clean.lower() == "backlog":
            return '(status = "Backlog" OR (sprint is EMPTY AND status = "To Do"))'
        return f'status = "{_escape_jql(clean)}"'

    async def search_issues(
        self, project_key: Optional[str] = None, status: str = None, max_results: int = 50
    ) -> list[dict]:
        """Search for issues in Jira, optionally filtered by project key and/or status."""
        clauses = []
        if project_key:
            clauses.append(f'project = "{_escape_jql(project_key)}"')
        status_clause = self.build_status_clause(status)
        if status_clause:
            clauses.append(status_clause)
        jql = " AND ".join(clauses) if clauses else ""
        jql = f"{jql} ORDER BY updated DESC".strip()
        try:
            return await self.search_jql(jql=jql, max_results=max_results)
        except Exception as e:
            if status and status.strip().lower() == "backlog" and "sprint" in str(e).lower():
                fallback_clauses = [f'project = "{_escape_jql(project_key)}"'] if project_key else []
                fallback_clauses.append('(status = "Backlog" OR status = "To Do")')
                return await self.search_jql(
                    jql=" AND ".join(fallback_clauses) + " ORDER BY updated DESC",
                    max_results=max_results,
                )
            raise

    async def test_connection(self) -> bool:
        """Test the Jira connection."""
        try:
            async with self._get_client() as client:
                response = await client.get(f"{self.api_url}/myself")
                response.raise_for_status()
                user = response.json()
                logger.info(f"Jira connected as: {user.get('displayName', 'Unknown')}")
                return True
        except Exception as e:
            logger.error(f"Jira connection failed: {e}")
            return False
