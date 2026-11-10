"""Jira Cloud API Client.

Handles ticket creation, status updates, comments, and attachment uploads.
"""
import asyncio
import logging
import httpx

logger = logging.getLogger(__name__)


def _escape_jql(value: str) -> str:
    return (value or "").replace("\\", "\\\\").replace('"', '\\"')


def _text_to_adf(text: str) -> dict:
    """Convert multi-line text/markdown into structured Atlassian Document Format paragraphs."""
    paragraphs = [block.strip() for block in (text or "").split("\n\n") if block.strip()]
    content = [
        {"type": "paragraph", "content": [{"type": "text", "text": p}]}
        for p in paragraphs
    ] or [{"type": "paragraph", "content": [{"type": "text", "text": "No details provided."}]}]
    return {"version": 1, "type": "doc", "content": content}


class JiraClient:
    """Async Jira Cloud REST API v3 client."""

    def __init__(self, base_url: str, email: str, api_token: str):
        self.base_url = base_url.rstrip("/")
        self.api_url = f"{self.base_url}/rest/api/3"
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

    async def _lookup_account_id(self, client: httpx.AsyncClient, query: str) -> str | None:
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
        """Create a Jira issue with structured ADF description and optional reporter lookup."""
        fields = {
            "project": {"key": project_key},
            "summary": summary[:255],
            "description": _text_to_adf(description),
            "issuetype": {"name": issue_type},
            "priority": {"name": priority.capitalize()},
        }

        if labels:
            fields["labels"] = labels

        async with self._get_client() as client:
            if reporter_name:
                account_id = await self._lookup_account_id(client, reporter_name)
                if account_id:
                    fields["reporter"] = {"accountId": account_id}

            payload = {"fields": fields}
            response = await client.post(f"{self.api_url}/issue", json=payload)
            # Retry once without reporter field if project screen disallows overriding reporter
            if response.status_code == 400 and "reporter" in fields:
                logger.warning("Jira disallowed custom reporter field; retrying with API token reporter.")
                fields.pop("reporter", None)
                response = await client.post(f"{self.api_url}/issue", json={"fields": fields})
            response.raise_for_status()
            data = response.json()
            logger.info(f"Created Jira issue: {data['key']}")
            return {
                "id": data["id"],
                "key": data["key"],
                "url": f"{self.base_url}/browse/{data['key']}",
            }

    async def add_comment(self, issue_key: str, comment: str) -> dict:
        """Add a comment to a Jira issue."""
        payload = {"body": _text_to_adf(comment)}

        async with self._get_client() as client:
            response = await client.post(
                f"{self.api_url}/issue/{issue_key}/comment", json=payload
            )
            response.raise_for_status()
            return response.json()

    async def add_attachment(self, issue_key: str, file_path: str, file_name: str) -> dict:
        """Upload an attachment to a Jira issue without blocking the event loop."""
        def _read_bytes(path: str) -> bytes:
            with open(path, "rb") as f:
                return f.read()

        file_bytes = await asyncio.to_thread(_read_bytes, file_path)
        async with self._get_client() as client:
            headers = {"X-Atlassian-Token": "no-check"}
            files = {"file": (file_name, file_bytes)}
            response = await client.post(
                f"{self.api_url}/issue/{issue_key}/attachments",
                headers=headers,
                files=files,
            )
            response.raise_for_status()
            return response.json()

    async def transition_issue(self, issue_key: str, status_name: str) -> bool:
        """Transition a Jira issue to a new status."""
        async with self._get_client() as client:
            # First, get available transitions
            response = await client.get(f"{self.api_url}/issue/{issue_key}/transitions")
            response.raise_for_status()
            transitions = response.json()["transitions"]

            # Find the matching transition
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

            # Execute transition
            payload = {"transition": {"id": target["id"]}}
            response = await client.post(
                f"{self.api_url}/issue/{issue_key}/transitions", json=payload
            )
            response.raise_for_status()
            logger.info(f"Transitioned {issue_key} to '{status_name}'")
            return True

    async def get_issue(self, issue_key: str) -> dict:
        """Get a Jira issue by key."""
        async with self._get_client() as client:
            response = await client.get(f"{self.api_url}/issue/{issue_key}")
            response.raise_for_status()
            data = response.json()
            return {
                "id": data["id"],
                "key": data["key"],
                "summary": data["fields"]["summary"],
                "status": data["fields"]["status"]["name"],
                "priority": data["fields"]["priority"]["name"] if data["fields"].get("priority") else None,
                "labels": data["fields"].get("labels", []),
                "assignee": data["fields"]["assignee"]["displayName"] if data["fields"].get("assignee") else None,
                "url": f"{self.base_url}/browse/{data['key']}",
            }

    async def search_issues(
        self, project_key: str, status: str = None, max_results: int = 50
    ) -> list[dict]:
        """Search for issues in a project via Jira Cloud v3 /search/jql, optionally filtered by status."""
        jql = f'project = "{_escape_jql(project_key)}"'
        if status:
            jql += f' AND status = "{_escape_jql(status)}"'
        jql += " ORDER BY created DESC"

        payload = {
            "jql": jql,
            "maxResults": max_results,
            "fields": ["summary", "status", "priority", "labels"],
        }

        async with self._get_client() as client:
            response = await client.post(f"{self.api_url}/search/jql", json=payload)
            response.raise_for_status()
            data = response.json()

            issues = []
            for issue in data.get("issues", []):
                issues.append({
                    "id": issue["id"],
                    "key": issue["key"],
                    "summary": issue["fields"]["summary"],
                    "status": issue["fields"]["status"]["name"],
                    "priority": issue["fields"]["priority"]["name"] if issue["fields"].get("priority") else None,
                    "labels": issue["fields"].get("labels", []),
                    "url": f"{self.base_url}/browse/{issue['key']}",
                })
            return issues

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

