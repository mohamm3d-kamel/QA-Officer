"""Base Agent Class."""
from abc import ABC, abstractmethod
from typing import Any, Dict, Optional
from datetime import datetime
import json
import logging
import re

from database import async_session
from models.agent_log import AgentLog
from services.watcher_bus import observe_agent_log, sanitize_for_log

logger = logging.getLogger(__name__)


class BaseAgent(ABC):
    """Abstract base class for all QA Officer AI Agents."""

    def __init__(self, name: str, role: str, color: str = "#3b82f6", icon: str = "🤖"):
        self.name = name
        self.role = role
        self.color = color
        self.icon = icon

    @staticmethod
    def parse_json_response(content: str) -> Any:
        """Parse JSON from an LLM response, handling Markdown fences or surrounding prose."""
        text = (content or "").strip()
        if not text:
            raise ValueError("Empty LLM response")

        fence_match = re.search(r"```(?:json)?\s*([\s\S]*?)```", text, re.IGNORECASE)
        if fence_match:
            text = fence_match.group(1).strip()

        try:
            return json.loads(text)
        except json.JSONDecodeError:
            decoder = json.JSONDecoder()
            for idx, ch in enumerate(text):
                if ch in "{[":
                    try:
                        obj, _ = decoder.raw_decode(text, idx)
                        return obj
                    except json.JSONDecodeError:
                        continue
            raise

    async def log_action(
        self,
        action: str,
        input_data: Any = None,
        output_data: Any = None,
        status: str = "success",
        bug_report_id: Optional[int] = None,
        project_id: Optional[int] = None,
        started_at: Optional[datetime] = None,
        module: Optional[str] = None,
        category: str = "agent_action",
        level: Optional[str] = None,
        jira_key: Optional[str] = None,
        notified_channels: Optional[str] = None,
        notify: bool = True,
        observer_bypass: bool = False,
    ) -> int:
        """Log agent activity to SQLite database and notify Watcher telemetry bus."""
        now = datetime.utcnow()
        start = started_at or now

        clean_in = sanitize_for_log(input_data)
        clean_out = sanitize_for_log(output_data)

        in_str = json.dumps(clean_in, ensure_ascii=False, default=str) if isinstance(clean_in, (dict, list)) else (str(clean_in) if clean_in is not None else None)
        out_str = json.dumps(clean_out, ensure_ascii=False, default=str) if isinstance(clean_out, (dict, list)) else (str(clean_out) if clean_out is not None else None)

        async with async_session() as session:
            log_entry = AgentLog(
                bug_report_id=bug_report_id,
                project_id=project_id,
                agent_name=self.name,
                action=action,
                input_data=in_str,
                output_data=out_str,
                status=status,
                module=module,
                category=category,
                level=level,
                jira_key=jira_key,
                notified_channels=notified_channels[:120] if notified_channels else None,
                started_at=start,
                finished_at=now,
            )
            session.add(log_entry)
            await session.commit()
            await session.refresh(log_entry)
            log_id = log_entry.id
            log_dict = log_entry.to_dict()

        if not observer_bypass:
            await observe_agent_log(
                log_id=log_id,
                agent_name=self.name,
                action=action,
                status=status,
                module=module,
                category=category,
                level=level,
                project_id=project_id,
                bug_report_id=bug_report_id,
                jira_key=jira_key,
                input_data=clean_in,
                output_data=clean_out,
                notify=notify,
            )
        else:
            try:
                from routers.websocket import manager
                await manager.broadcast({
                    "type": "watcher_event",
                    "log": log_dict,
                    "level": level or status,
                    "timestamp": now.isoformat(),
                })
            except Exception:
                pass

        return log_id

    @abstractmethod
    async def run(self, *args, **kwargs) -> Any:
        """Run agent task."""
        pass
