"""Project Asset Hub - Unified read/write access to all QA Officer project assets and integrations.

Exposes project metadata, stored skills/templates, tracked bug reports, visual evidence files,
agent execution logs, local repository files (when repo_url is a local directory), live Jira Cloud
boards/tickets/statuses, and live Playwright page inspection so Desk Officer can inspect every
project asset and organize any agent workflow cleanly.
"""
import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

import config
from database import async_session
from models.agent_log import AgentLog
from models.bug_report import BugReport
from models.chat_message import ChatMessage
from models.evidence import Evidence
from models.project import Project
from models.skill import Skill
from services.browser_engine import BrowserEngine
from services.jira_client import JiraClient, is_real_jira_issue_key

logger = logging.getLogger(__name__)

_JIRA_KEY_RE = re.compile(r"\b([A-Z][A-Z0-9]{1,12}-\d+)\b")
_BUG_ID_RE = re.compile(r"(?:#\s*(\d+)|\bbug\s+(\d+)\b)", re.IGNORECASE)
_SECRET_FILE_RE = re.compile(r"(\.env|\.pem|\.key|\.p12|secret|credential|\.sqlite|\.db$)", re.IGNORECASE)
_SKIP_DIRS = {".git", "node_modules", "venv", ".venv", "__pycache__", "dist", "build", ".next"}


class ProjectAssetHub:
    """Provides structured visibility and safe actions across all project assets."""

    def __init__(self, settings_dict: Optional[Dict[str, str]] = None):
        self.settings = settings_dict or {}

    def get_jira_client(self, project_jira_base_url: str = "") -> Optional[JiraClient]:
        base_url = (self.settings.get("jira_base_url") or project_jira_base_url or "").strip()
        email = (self.settings.get("jira_email") or "").strip()
        token = (self.settings.get("jira_api_token") or "").strip()
        if base_url and email and token:
            return JiraClient(base_url=base_url, email=email, api_token=token)
        return None

    def get_integration_summary(self, project_info: Dict[str, Any]) -> Dict[str, Any]:
        """Return sanitized integration configuration (never exposing secrets)."""
        jira_base = (self.settings.get("jira_base_url") or project_info.get("jira_base_url") or "").strip()
        jira_email = (self.settings.get("jira_email") or "").strip()
        jira_token_set = bool((self.settings.get("jira_api_token") or "").strip())
        browsers = [
            b
            for b in ("chromium", "firefox", "webkit")
            if self.settings.get(f"browser_{b}", "true") == "true"
        ]
        return {
            "llm": {
                "provider": self.settings.get("llm_provider", "openai"),
                "model": self.settings.get("llm_model", ""),
                "api_key_configured": bool((self.settings.get("llm_api_key") or "").strip()),
            },
            "jira": {
                "live_configured": bool(jira_base and jira_email and jira_token_set),
                "base_url": jira_base,
                "email": jira_email,
                "project_key": project_info.get("jira_project_key") or "QA",
                "default_reporter": self.settings.get("default_reporter", "Mohamed Kamel"),
            },
            "watcher": {
                "enabled": self.settings.get("watcher_enabled", "true") == "true",
                "interval_minutes": self.settings.get("watcher_interval_minutes", "60"),
                "monitor_modules": self.settings.get("watcher_monitor_modules", "true") == "true",
                "monitor_agents": self.settings.get("watcher_monitor_agents", "true") == "true",
                "monitor_logs": self.settings.get("watcher_monitor_logs", "true") == "true",
                "last_sweep_at": self.settings.get("watcher_last_sweep_at") or None,
            },
            "notifications": {
                "email_enabled": self.settings.get("notification_email_enabled", "false") == "true",
                "email_recipient": self.settings.get("notification_email", ""),
                "smtp_configured": bool((self.settings.get("smtp_host") or "").strip()),
                "slack_enabled": self.settings.get("notification_slack_enabled", "false") == "true",
                "slack_webhook_configured": bool((self.settings.get("notification_slack_webhook") or "").strip()),
                "notify_on_all_events": self.settings.get("notify_on_all_events", "false") == "true",
                "notify_on_changes": self.settings.get("notify_on_changes", "true") == "true",
                "notify_on_failures": self.settings.get("notify_on_failures", "true") == "true",
            },
            "browsers": {
                "enabled": browsers or ["chromium"],
                "headless": self.settings.get("browser_headless", "true") == "true",
            },
        }

    @staticmethod
    def _scan_local_repo(repo_url: str, max_files: int = 45) -> Optional[Dict[str, Any]]:
        """Safely list files when repo_url points to a local workspace directory."""
        raw = (repo_url or "").strip()
        if not raw or raw.startswith(("http://", "https://", "git@", "ssh://")):
            return None
        path = Path(raw)
        if not path.exists() or not path.is_dir():
            return None

        root = path.resolve()
        files: List[str] = []
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = sorted(d for d in dirnames if d not in _SKIP_DIRS)
            for fname in sorted(filenames):
                if _SECRET_FILE_RE.search(fname):
                    continue
                rel = Path(dirpath, fname).relative_to(root).as_posix()
                files.append(rel)
                if len(files) >= max_files:
                    break
            if len(files) >= max_files:
                break
        return {"root": str(root), "file_count_shown": len(files), "files": files}

    async def collect_snapshot(self, project_id: int) -> Dict[str, Any]:
        """Collect complete database + filesystem asset inventory for a project."""
        async with async_session() as session:
            project = await session.get(Project, project_id)
            if not project:
                raise ValueError(f"Project #{project_id} not found")
            project_info = project.to_dict()

            res_skills = await session.execute(
                select(Skill).where(Skill.project_id == project_id).order_by(Skill.id.asc())
            )
            skills = [s.to_dict() for s in res_skills.scalars().all()]

            res_bugs = await session.execute(
                select(BugReport)
                .options(selectinload(BugReport.evidence))
                .where(BugReport.project_id == project_id)
                .order_by(BugReport.created_at.desc(), BugReport.id.desc())
            )
            bug_rows = res_bugs.scalars().all()
            bugs = []
            evidence_items: List[Dict[str, Any]] = []
            for b in bug_rows:
                bd = b.to_dict()
                bd["has_formatted_report"] = bool((b.formatted_report or "").strip())
                bd["evidence_count"] = len(b.evidence or [])
                bugs.append(bd)
                for ev in b.evidence or []:
                    fp = Path(ev.file_path) if ev.file_path else None
                    ev_dict = ev.to_dict()
                    ev_dict["bug_title"] = b.title
                    ev_dict["url"] = f"/api/evidence/{ev.id}/file"
                    ev_dict["exists_on_disk"] = bool(fp and fp.exists())
                    ev_dict["size_bytes"] = fp.stat().st_size if fp and fp.exists() else 0
                    evidence_items.append(ev_dict)

            res_logs = await session.execute(
                select(AgentLog)
                .where((AgentLog.project_id == project_id) | (AgentLog.project_id.is_(None)))
                .order_by(AgentLog.started_at.desc())
                .limit(35)
            )
            logs = [l.to_dict() for l in res_logs.scalars().all()]

            chat_count = (
                await session.execute(
                    select(func.count(ChatMessage.id)).where(ChatMessage.project_id == project_id)
                )
            ).scalar() or 0

        status_counts: Dict[str, int] = {}
        modules: Dict[str, int] = {}
        for b in bugs:
            st = b.get("status") or "draft"
            status_counts[st] = status_counts.get(st, 0) + 1
            mod = b.get("module") or "General"
            modules[mod] = modules.get(mod, 0) + 1

        latest_by_agent: Dict[str, Dict[str, Any]] = {}
        for log in logs:
            name = log.get("agent_name")
            if name and name not in latest_by_agent:
                latest_by_agent[name] = {
                    "action": log.get("action"),
                    "status": log.get("status"),
                    "module": log.get("module"),
                    "bug_report_id": log.get("bug_report_id"),
                    "started_at": log.get("started_at"),
                }

        local_repo = self._scan_local_repo(project_info.get("repo_url") or "")
        integrations = self.get_integration_summary(project_info)

        return {
            "project": project_info,
            "skills": skills,
            "bugs": bugs,
            "evidence": evidence_items,
            "agent_logs": logs,
            "latest_agent_activity": latest_by_agent,
            "coverage_summary": {
                "total_bugs": len(bugs),
                "by_status": status_counts,
                "by_module": modules,
                "chat_message_count": chat_count,
            },
            "local_repo": local_repo,
            "integrations": integrations,
        }

    async def inspect_app_url(self, target_url: str, headless: bool = True) -> Dict[str, Any]:
        """Perform read-only Playwright inspection of app_url or a target page."""
        url = (target_url or "").strip()
        if not url:
            return {"error": "No target URL or project app_url is configured."}
        if not url.startswith(("http://", "https://")):
            return {"error": f"Invalid URL scheme for browser inspection: {url}"}

        engine = BrowserEngine(headless=headless)
        session = None
        console_errors: List[str] = []
        network_errors: List[Dict[str, Any]] = []
        try:
            session = await engine.start(record_video=False)
            session.page.on(
                "console",
                lambda msg: console_errors.append(msg.text[:200]) if msg.type == "error" and len(console_errors) < 10 else None,
            )
            session.page.on(
                "response",
                lambda resp: network_errors.append({"url": resp.url, "status": resp.status, "method": resp.request.method})
                if resp.status >= 400 and len(network_errors) < 10
                else None,
            )
            await session.navigate(url, wait_until="domcontentloaded")
            await session.page.wait_for_timeout(1500)

            page_info = await session.page.evaluate(
                """() => {
                    const headings = Array.from(document.querySelectorAll('h1,h2,h3'))
                        .map(el => (el.innerText || '').trim()).filter(Boolean).slice(0, 8);
                    const buttons = Array.from(document.querySelectorAll('button, a[role="button"], input[type="submit"]'))
                        .map(el => (el.innerText || el.value || el.getAttribute('aria-label') || '').trim())
                        .filter(Boolean).slice(0, 12);
                    const inputs = Array.from(document.querySelectorAll('input, select, textarea'))
                        .map(el => ({
                            tag: el.tagName.toLowerCase(),
                            type: el.getAttribute('type') || '',
                            name: el.getAttribute('name') || el.getAttribute('id') || '',
                            placeholder: el.getAttribute('placeholder') || ''
                        })).slice(0, 15);
                    const bodyText = (document.body?.innerText || '').replace(/\\s+/g, ' ').trim().slice(0, 700);
                    return { title: document.title, url: window.location.href, headings, buttons, inputs, bodyText };
                }"""
            )
            return {
                "ok": True,
                "requested_url": url,
                "final_url": page_info.get("url"),
                "title": page_info.get("title"),
                "headings": page_info.get("headings", []),
                "buttons": page_info.get("buttons", []),
                "inputs": page_info.get("inputs", []),
                "visible_text_preview": page_info.get("bodyText", ""),
                "console_errors": console_errors,
                "network_errors": network_errors,
            }
        except Exception as e:
            return {"ok": False, "requested_url": url, "error": str(e)}
        finally:
            try:
                await engine.stop()
            except Exception:
                pass

    async def run_asset_tool(
        self,
        project_id: int,
        tool_name: str,
        params: Optional[Dict[str, Any]] = None,
        snapshot: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Execute an asset inspection or skill/project-asset operation."""
        params = params or {}
        if snapshot is None:
            snapshot = await self.collect_snapshot(project_id)

        project_info = snapshot["project"]
        tool = (tool_name or "").strip().lower()

        if tool == "jira_list_projects":
            client = self.get_jira_client(project_info.get("jira_base_url") or "")
            if not client:
                return {"tool": tool, "ok": False, "error": "Jira Cloud credentials are not configured in Settings."}
            try:
                projects = await client.list_projects()
                configured_key = project_info.get("jira_project_key")
                return {
                    "tool": tool,
                    "ok": True,
                    "configured_project_key": configured_key,
                    "configured_key_found": any(p.get("key") == configured_key for p in projects),
                    "jira_projects": projects,
                }
            except Exception as e:
                return {"tool": tool, "ok": False, "error": str(e)}

        if tool == "jira_search":
            client = self.get_jira_client(project_info.get("jira_base_url") or "")
            if not client:
                return {"tool": tool, "ok": False, "error": "Jira Cloud credentials are not configured in Settings."}
            try:
                configured_key = project_info.get("jira_project_key")
                explicit_proj = (params.get("project_key") or "").strip() or None
                status = (params.get("status") or "").strip() or None
                query_text = (params.get("query") or "").strip() or None
                custom_jql = (params.get("jql") or "").strip() or None
                max_results = int(params.get("max_results") or 25)

                resolved_keys: List[str] = []
                available_projects: List[Dict[str, Any]] = []
                configured_valid = True

                if custom_jql:
                    issues = await client.search_jql(custom_jql, max_results=max_results)
                    return {"tool": tool, "ok": True, "jql": custom_jql, "count": len(issues), "issues": issues}

                if explicit_proj:
                    resolved_keys = [explicit_proj]
                    available_projects = await client.list_projects()
                else:
                    resolved_keys, available_projects, configured_valid = await client.resolve_project_keys(
                        configured_key=configured_key,
                        project_name=project_info.get("name") or "",
                    )

                clauses = []
                if resolved_keys:
                    if len(resolved_keys) == 1:
                        clauses.append(f'project = "{resolved_keys[0]}"')
                    else:
                        quoted = ", ".join(f'"{k}"' for k in resolved_keys)
                        clauses.append(f"project in ({quoted})")
                status_clause = client.build_status_clause(status)
                if status_clause:
                    clauses.append(status_clause)
                if query_text:
                    safe_q = query_text.replace('"', '\\"')
                    clauses.append(f'text ~ "{safe_q}"')

                jql = (" AND ".join(clauses) + " " if clauses else "") + "ORDER BY updated DESC"
                try:
                    issues = await client.search_jql(jql=jql, max_results=max_results)
                except Exception as search_err:
                    if status and status.strip().lower() == "backlog" and "sprint" in str(search_err).lower():
                        fb_clauses = [c for c in clauses if "sprint" not in c.lower()]
                        fb_clauses.append('(status = "Backlog" OR status = "To Do")')
                        jql = " AND ".join(fb_clauses) + " ORDER BY updated DESC"
                        issues = await client.search_jql(jql=jql, max_results=max_results)
                    else:
                        raise

                res_payload: Dict[str, Any] = {
                    "tool": tool,
                    "ok": True,
                    "configured_project_key": configured_key,
                    "configured_key_valid_on_jira": configured_valid,
                    "queried_project_keys": resolved_keys,
                    "available_jira_projects": available_projects,
                    "status_filter": status,
                    "jql": jql,
                    "count": len(issues),
                    "issues": issues,
                }
                if status and status.strip().lower() == "backlog":
                    res_payload["backlog_note"] = (
                        "Backlog includes both workflow status 'Backlog' and Scrum Board Backlog items "
                        "(sprint is EMPTY and status 'To Do')."
                    )
                return res_payload
            except Exception as e:
                return {"tool": tool, "ok": False, "error": str(e)}

        if tool == "jira_get_issue":
            issue_key = (params.get("issue_key") or "").strip().upper()
            if not issue_key:
                return {"tool": tool, "ok": False, "error": "Missing issue_key (e.g. SCRUM-850)."}
            client = self.get_jira_client(project_info.get("jira_base_url") or "")
            if not client:
                return {"tool": tool, "ok": False, "error": "Jira Cloud credentials are not configured in Settings."}
            try:
                issue = await client.get_issue(issue_key)
                return {"tool": tool, "ok": True, "issue": issue}
            except Exception as e:
                return {"tool": tool, "ok": False, "issue_key": issue_key, "error": str(e)}

        if tool == "get_bug_details":
            bug_id = params.get("bug_id")
            if bug_id is not None:
                async with async_session() as session:
                    res = await session.execute(
                        select(BugReport)
                        .options(selectinload(BugReport.evidence), selectinload(BugReport.agent_logs))
                        .where(BugReport.id == int(bug_id), BugReport.project_id == project_id)
                    )
                    bug = res.scalar_one_or_none()
                if not bug:
                    return {"tool": tool, "ok": False, "error": f"Bug #{bug_id} not found in this project."}
                data = bug.to_dict()
                data["logs"] = [l.to_dict() for l in (bug.agent_logs or [])[-10:]]
                return {"tool": tool, "ok": True, "bug": data}
            return {"tool": tool, "ok": True, "bugs": snapshot["bugs"]}

        if tool == "list_evidence":
            bug_id = params.get("bug_id")
            evs = snapshot["evidence"]
            if bug_id is not None:
                evs = [e for e in evs if e.get("bug_report_id") == int(bug_id)]
            return {"tool": tool, "ok": True, "count": len(evs), "evidence": evs}

        if tool == "list_skills":
            return {"tool": tool, "ok": True, "count": len(snapshot["skills"]), "skills": snapshot["skills"]}

        if tool == "save_skill":
            from agents.reporter import strip_report_format_preamble
            from services.watcher_bus import record_system_event

            stype = (params.get("type") or "requirement").strip().lower()
            if stype not in ("requirement", "report_format", "test_case", "business_doc"):
                stype = "requirement"
            content = (params.get("content") or "").strip()
            if stype == "report_format" and content:
                content = strip_report_format_preamble(content) or content
            title = (params.get("title") or "").strip() or (
                "Custom Bug Report Format" if stype == "report_format" else content.splitlines()[0][:60]
            )
            if not content:
                return {"tool": tool, "ok": False, "error": "Skill content cannot be empty."}
            async with async_session() as session:
                skill = Skill(project_id=project_id, type=stype, title=title, content=content)
                session.add(skill)
                if stype == "report_format":
                    proj = await session.get(Project, project_id)
                    if proj:
                        proj.report_format = content
                await session.commit()
                await session.refresh(skill)
                skill_dict = skill.to_dict()
            await record_system_event(
                action="skill_saved_via_desk_tool",
                summary=f"Desk Officer saved skill '{title}' ({stype}) in project #{project_id}",
                module="Skills",
                category="system_change",
                status="success",
                level="change",
                agent_name="Desk Officer",
                project_id=project_id,
                output_data={"skill_id": skill_dict["id"], "type": stype, "title": title},
                notify=False,
                notify_ui_bell=False,
            )
            return {"tool": tool, "ok": True, "skill": skill_dict}

        if tool == "delete_skill":
            from services.watcher_bus import record_system_event

            skill_id = params.get("skill_id")
            if not skill_id:
                return {"tool": tool, "ok": False, "error": "Missing skill_id."}
            async with async_session() as session:
                skill = await session.get(Skill, int(skill_id))
                if not skill or skill.project_id != project_id:
                    return {"tool": tool, "ok": False, "error": f"Skill #{skill_id} not found in this project."}
                deleted_title = skill.title
                await session.delete(skill)
                await session.commit()
            await record_system_event(
                action="skill_deleted_via_desk_tool",
                summary=f"Desk Officer deleted skill #{skill_id} ('{deleted_title}') in project #{project_id}",
                module="Skills",
                category="system_change",
                status="success",
                level="change",
                agent_name="Desk Officer",
                project_id=project_id,
                output_data={"deleted_skill_id": int(skill_id), "title": deleted_title},
                notify=False,
                notify_ui_bell=False,
            )
            return {"tool": tool, "ok": True, "deleted_skill_id": int(skill_id)}

        if tool == "update_project_config":
            from agents.reporter import strip_report_format_preamble
            from services.watcher_bus import record_system_event

            updates = {}
            for field in ("name", "jira_project_key", "jira_base_url", "app_url", "repo_url", "description", "report_format"):
                if field in params and params[field] is not None:
                    val = str(params[field]).strip()
                    updates[field] = strip_report_format_preamble(val) if field == "report_format" else val
            if not updates:
                return {"tool": tool, "ok": False, "error": "No valid project configuration fields supplied."}
            async with async_session() as session:
                proj = await session.get(Project, project_id)
                if not proj:
                    return {"tool": tool, "ok": False, "error": f"Project #{project_id} not found."}
                for k, v in updates.items():
                    setattr(proj, k, v)
                await session.commit()
                await session.refresh(proj)
                proj_dict = proj.to_dict()
            await record_system_event(
                action="project_updated_via_desk_tool",
                summary=f"Desk Officer updated project '{proj_dict.get('name')}' config: {', '.join(updates.keys())}",
                module="Projects",
                category="system_change",
                status="success",
                level="change",
                agent_name="Desk Officer",
                project_id=project_id,
                output_data={"updated_fields": list(updates.keys()), "values": updates},
                notify=False,
                notify_ui_bell=False,
            )
            return {"tool": tool, "ok": True, "project": proj_dict, "updated_fields": list(updates.keys())}

        if tool == "list_agent_logs":
            agent_filter = (params.get("agent") or "").strip().lower()
            module_filter = (params.get("module") or "").strip().lower()
            status_filter = (params.get("status") or "").strip().lower()
            logs = snapshot["agent_logs"]
            if agent_filter:
                logs = [l for l in logs if agent_filter in (l.get("agent_name") or "").lower()]
            if module_filter:
                logs = [l for l in logs if module_filter in (l.get("module") or "").lower()]
            if status_filter:
                logs = [l for l in logs if status_filter == (l.get("status") or "").lower()]
            return {"tool": tool, "ok": True, "count": len(logs), "logs": logs[:20]}

        if tool == "watcher_scan":
            from agents.coroner import CoronerAgent
            from agents.detective import DetectiveAgent
            from agents.watcher import WatcherAgent
            from services.llm_router import LLMRouter
            from services.notification_service import NotificationService

            llm = LLMRouter(
                provider=self.settings.get("llm_provider", "openai"),
                api_key=self.settings.get("llm_api_key", ""),
                model=self.settings.get("llm_model", "gpt-4"),
                base_url=self.settings.get("llm_base_url", ""),
            )
            watcher = WatcherAgent(
                DetectiveAgent(llm),
                CoronerAgent(),
                NotificationService.from_settings(self.settings),
            )
            sweep = await watcher.run_full_system_sweep(
                jira_settings=self.settings,
                project_id=None if params.get("all_projects") else project_id,
                include_app_health=bool(params.get("include_app_health", True)),
                notify=bool(params.get("notify", True)),
            )
            return {"tool": tool, "ok": True, "sweep": sweep}

        if tool == "inspect_app_url":
            url = (params.get("url") or project_info.get("app_url") or "").strip()
            headless = snapshot["integrations"]["browsers"]["headless"]
            res = await self.inspect_app_url(url, headless=headless)
            return {"tool": tool, **res}

        if tool == "read_repo_file":
            rel_path = (params.get("path") or "").strip()
            local_repo = snapshot.get("local_repo")
            if not local_repo:
                return {"tool": tool, "ok": False, "error": "Project repo_url is empty or not a local folder path."}
            if _SECRET_FILE_RE.search(rel_path):
                return {"tool": tool, "ok": False, "error": "Reading secret/environment files is blocked for safety."}
            root = Path(local_repo["root"]).resolve()
            target = (root / rel_path).resolve()
            if root not in target.parents and target != root:
                return {"tool": tool, "ok": False, "error": "Path escapes project repository directory."}
            if not target.is_file():
                return {"tool": tool, "ok": False, "error": f"File not found: {rel_path}"}
            try:
                content = target.read_text(encoding="utf-8", errors="replace")[:4000]
                return {"tool": tool, "ok": True, "path": rel_path, "content": content}
            except Exception as e:
                return {"tool": tool, "ok": False, "error": str(e)}

        return {"tool": tool, "ok": False, "error": f"Unknown asset tool: {tool_name}"}

    async def prefetch_relevant_assets(
        self,
        message: str,
        project_id: int,
        snapshot: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        """Automatically pre-fetch live Jira / Bug / URL data when clearly needed by the user's message."""
        lower = message.lower()
        results: List[Dict[str, Any]] = []
        jira_client = self.get_jira_client(snapshot["project"].get("jira_base_url") or "")

        # 1. Explicit Jira issue keys like SCRUM-850
        jira_keys = list(dict.fromkeys(_JIRA_KEY_RE.findall(message)))
        # Exclude QA-local simulated prefixes if absent on Jira? Still safe to try or check.
        if jira_client and jira_keys:
            for key in jira_keys[:2]:
                results.append(await self.run_asset_tool(project_id, "jira_get_issue", {"issue_key": key}, snapshot))

        # 2. Jira board / list queries ("In QA list", "Backlog", "Jira", "Failed QA", etc.)
        is_ticket_creation_cmd = bool(
            re.search(
                r"\b(raise|push|submit|file|log)\b[^.!?\n]{0,40}\b(?:(?:the|this|a|new)\s+)?(?:bug\s*)?(?:reports?|tickets?|issues?|jira|backlog|scrum)\b"
                r"|\bcreate\b[^.!?\n]{0,40}\b(ticket|issue|jira|backlog|scrum)\b"
                r"|\bconfirm(?:ing)?\b[^.!?\n]{0,30}\bticket\s+creation\b",
                lower,
            )
        ) and not bool(re.search(r"\b(check|show|list|verify|inspect|search|find|what|which)\b", lower))

        jira_board_intent = bool(
            re.search(
                r"\b(jira|in\s*qa|backlog|ready\s*to\s*deploy|failed\s*qa|to\s*do|sprint|board|scrum)\b",
                lower,
            )
        )
        if jira_client and jira_board_intent and not jira_keys and not is_ticket_creation_cmd:
            status_filter = None
            if re.search(r"\bin\s*qa\b", lower):
                status_filter = "In QA"
            elif re.search(r"\bbacklog\b", lower) and not re.search(r"\b(raise|push|move|put|create)\b", lower):
                status_filter = "Backlog"
            elif re.search(r"\bfailed\s*qa\b", lower):
                status_filter = "Failed QA"
            elif re.search(r"\bready\s*to\s*deploy\b", lower):
                status_filter = "Ready To Deploy"
            elif re.search(r"\bto\s*do\b", lower):
                status_filter = "To Do"

            if re.search(r"\b(projects?|boards?)\b", lower) and not status_filter:
                results.append(await self.run_asset_tool(project_id, "jira_list_projects", {}, snapshot))
            results.append(
                await self.run_asset_tool(
                    project_id,
                    "jira_search",
                    {"status": status_filter, "max_results": 25},
                    snapshot,
                )
            )

        # 3. Specific tracked bug details (#id)
        bug_matches = [m[0] or m[1] for m in _BUG_ID_RE.findall(message)]
        if bug_matches and re.search(r"\b(detail|show|inspect|report|evidence|what|status|check|full)\b", lower):
            for bid in list(dict.fromkeys(bug_matches))[:2]:
                results.append(await self.run_asset_tool(project_id, "get_bug_details", {"bug_id": int(bid)}, snapshot))

        # 4. Explicit live URL / app inspection request (when user asks to inspect/check/open/visit app_url)
        explicit_url_match = re.search(r"https?://[^\s)]+", message)
        wants_browser_inspect = bool(
            re.search(
                r"\b(inspect|check|open|visit|browse|load|verify|see|look\s+at)\b[^.!?\n]{0,45}\b(app|site|website|page|url|login|screen|app_url|staging)\b",
                lower,
            )
        )
        if explicit_url_match or wants_browser_inspect:
            target = explicit_url_match.group(0) if explicit_url_match else (snapshot["project"].get("app_url") or "")
            if target:
                results.append(await self.run_asset_tool(project_id, "inspect_app_url", {"url": target}, snapshot))

        return results

    @staticmethod
    def format_prompt_context(
        snapshot: Dict[str, Any],
        extra_results: Optional[List[Dict[str, Any]]] = None,
    ) -> str:
        """Build a concise, high-signal project asset & agent dossier for Desk Officer's system prompt."""
        proj = snapshot["project"]
        integ = snapshot["integrations"]
        cov = snapshot["coverage_summary"]

        lines: List[str] = []
        lines.append("=== PROJECT PROFILE ===")
        lines.append(f"- ID: {proj.get('id')} | Name: {proj.get('name')}")
        lines.append(f"- App URL: {proj.get('app_url') or 'Not set'}")
        lines.append(f"- Repo URL: {proj.get('repo_url') or 'Not set'}")
        lines.append(f"- Configured Jira Project Key: {proj.get('jira_project_key') or 'QA'} | Jira URL: {proj.get('jira_base_url') or integ['jira'].get('base_url') or 'Not set'}")
        if proj.get("description"):
            lines.append(f"- Domain / Business Description:\n{proj['description'][:2200]}")
        from agents.reporter import strip_report_format_preamble

        active_rf = strip_report_format_preamble(proj.get("report_format")) or ""
        if not active_rf:
            for s in reversed(snapshot.get("skills") or []):
                if s.get("type") == "report_format" and (s.get("content") or "").strip():
                    if s.get("title") != "Default QA Bug Report Format" or not active_rf:
                        active_rf = strip_report_format_preamble(s.get("content")) or ""
                        if s.get("title") != "Default QA Bug Report Format":
                            break
        if active_rf:
            lines.append(f"\n=== AUTHORITATIVE PROJECT REPORT FORMAT (FOLLOW AS-IS) ===\n{active_rf[:2000]}")
        else:
            lines.append("\n=== AUTHORITATIVE PROJECT REPORT FORMAT ===\nNot explicitly configured (using standard QA Markdown format).")

        lines.append("\n=== LIVE INTEGRATIONS & ENVIRONMENT CONFIG ===")
        lines.append(
            f"- Jira Cloud API: {'CONNECTED (live access available)' if integ['jira']['live_configured'] else 'NOT CONFIGURED (local Backlog fallback)'}"
            f" | Email={integ['jira'].get('email') or 'N/A'} | Default Reporter={integ['jira'].get('default_reporter')}"
        )
        lines.append(
            f"- Watcher Sentinel: {'Enabled' if integ['watcher']['enabled'] else 'Disabled'} (every {integ['watcher']['interval_minutes']}m"
            f" | modules={integ['watcher'].get('monitor_modules')}, agents={integ['watcher'].get('monitor_agents')}, logs={integ['watcher'].get('monitor_logs')})"
            f" | Browsers: {', '.join(integ['browsers']['enabled'])} (headless={integ['browsers']['headless']})"
            f" | Notifications: Email={integ['notifications']['email_enabled']} (smtp_configured={integ['notifications'].get('smtp_configured')}), "
            f"Slack={integ['notifications']['slack_enabled']} (webhook_configured={integ['notifications'].get('slack_webhook_configured')})"
        )

        lines.append(f"\n=== STORED PROJECT SKILLS & TEMPLATES ({len(snapshot['skills'])}) ===")
        if snapshot["skills"]:
            for s in snapshot["skills"][:12]:
                snippet = (s.get("content") or "").strip().replace("\n", " / ")[:240]
                lines.append(f"- Skill #{s['id']} [{s['type']}] \"{s['title']}\": {snippet}")
        else:
            lines.append("- None stored yet.")

        lines.append(
            f"\n=== TRACKED BUG REPORTS ({cov['total_bugs']} total | statuses={cov['by_status']} | modules={cov['by_module']}) ==="
        )
        proj_jira_key = proj.get("jira_project_key") or "QA"
        if snapshot["bugs"]:
            for b in snapshot["bugs"][:10]:
                rc = (b.get("root_cause") or "Pending")[:120]
                raw_jira = b.get("jira_ticket_id")
                if not raw_jira:
                    jira_disp = "NOT_RAISED"
                elif is_real_jira_issue_key(raw_jira, proj_jira_key):
                    jira_disp = raw_jira
                else:
                    jira_disp = f"{raw_jira} [SIMULATED_OR_WRONG_PROJECT - not a real {proj_jira_key}-xxx ticket; ready for fresh {proj_jira_key} creation]"
                lines.append(
                    f"- Bug #{b['id']} | status={b['status']} | severity={b.get('severity')} | module={b.get('module')} "
                    f"| jira={jira_disp} | evidence={b.get('evidence_count', 0)} "
                    f"| report={'YES' if b.get('has_formatted_report') else 'NO'} | title=\"{b.get('title', '')[:90]}\" | root_cause=\"{rc}\""
                )
        else:
            lines.append("- None tracked yet.")

        lines.append(f"\n=== EVIDENCE ASSETS ({len(snapshot['evidence'])}) ===")
        if snapshot["evidence"]:
            for ev in snapshot["evidence"][:12]:
                lines.append(
                    f"- Evidence #{ev['id']} [{ev['type']}] bug=#{ev['bug_report_id']} stage={ev['stage']} "
                    f"file={ev['file_name']} url={ev['url']} disk={'OK' if ev['exists_on_disk'] else 'MISSING'}"
                )
        else:
            lines.append("- No screenshots or videos captured yet.")

        lines.append("\n=== RECENT AGENT ACTIVITY ===")
        if snapshot["latest_agent_activity"]:
            for agent_name, info in snapshot["latest_agent_activity"].items():
                lines.append(
                    f"- {agent_name}: last={info.get('action')} ({info.get('status')}) module={info.get('module') or 'System'} bug=#{info.get('bug_report_id')} at {info.get('started_at')}"
                )
        else:
            lines.append("- No prior agent runs recorded.")

        if snapshot.get("local_repo"):
            repo = snapshot["local_repo"]
            lines.append(f"\n=== LOCAL CODEBASE FILES ({repo['root']}) ===")
            lines.append(", ".join(repo["files"][:30]))

        if extra_results:
            lines.append("\n=== LIVE ASSET & TOOL RESULTS (JUST FETCHED FOR THIS TURN) ===")
            for res in extra_results:
                tname = res.get("tool", "tool")
                if not res.get("ok"):
                    lines.append(f"- [{tname}] Error: {res.get('error')}")
                    continue
                if tname == "jira_search":
                    lines.append(
                        f"- [jira_search] jql=`{res.get('jql')}` | count={res.get('count')} "
                        f"| configured_key={res.get('configured_project_key')} (valid_on_jira={res.get('configured_key_valid_on_jira')}) "
                        f"| available_jira_projects={[{'key': p['key'], 'name': p['name']} for p in res.get('available_jira_projects', [])]}"
                    )
                    for iss in (res.get("issues") or [])[:20]:
                        lines.append(
                            f"  * {iss['key']} [{iss.get('status')}] ({iss.get('priority') or 'Medium'}) {iss.get('summary')} -> {iss.get('url')}"
                        )
                elif tname == "jira_list_projects":
                    lines.append(f"- [jira_list_projects] configured={res.get('configured_project_key')} | projects={res.get('jira_projects')}")
                elif tname == "jira_get_issue":
                    iss = res.get("issue") or {}
                    lines.append(
                        f"- [jira_get_issue] {iss.get('key')} [{iss.get('status')}] priority={iss.get('priority')} "
                        f"assignee={iss.get('assignee')} labels={iss.get('labels')} url={iss.get('url')}\n"
                        f"  Summary: {iss.get('summary')}\n"
                        f"  Description: {(iss.get('description') or 'N/A')[:900]}\n"
                        f"  Comments({len(iss.get('comments') or [])}): {iss.get('comments')}"
                    )
                elif tname == "get_bug_details":
                    b = res.get("bug")
                    if b:
                        lines.append(
                            f"- [get_bug_details] Bug #{b['id']}: {b.get('title')} (status={b.get('status')}, jira={b.get('jira_ticket_id')})\n"
                            f"  Root Cause: {b.get('root_cause')}\n"
                            f"  Steps: {b.get('steps_to_reproduce')}\n"
                            f"  Formatted Report Preview: {(b.get('formatted_report') or 'None')[:900]}\n"
                            f"  Evidence: {[{'id': e['id'], 'type': e['type'], 'file_name': e['file_name']} for e in b.get('evidence', [])]}"
                        )
                elif tname == "inspect_app_url":
                    lines.append(
                        f"- [inspect_app_url] url={res.get('final_url')} title={res.get('title')!r} "
                        f"headings={res.get('headings')} buttons={res.get('buttons')} inputs={res.get('inputs')[:10]} "
                        f"console_errors={res.get('console_errors')} network_errors={res.get('network_errors')} "
                        f"text_preview={res.get('visible_text_preview', '')[:350]!r}"
                    )
                else:
                    lines.append(f"- [{tname}] {str(res)[:800]}")

        return "\n".join(lines)
