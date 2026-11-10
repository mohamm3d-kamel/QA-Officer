"""Base Agent Class."""
from abc import ABC, abstractmethod
from typing import Any, Dict, Optional
from datetime import datetime
import json
import logging
import re

from database import async_session
from models.agent_log import AgentLog

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
    ) -> int:
        """Log agent activity to SQLite database."""
        now = datetime.utcnow()
        start = started_at or now

        # Serialize if needed
        in_str = json.dumps(input_data) if isinstance(input_data, (dict, list)) else (str(input_data) if input_data is not None else None)
        out_str = json.dumps(output_data) if isinstance(output_data, (dict, list)) else (str(output_data) if output_data is not None else None)

        async with async_session() as session:
            log_entry = AgentLog(
                bug_report_id=bug_report_id,
                project_id=project_id,
                agent_name=self.name,
                action=action,
                input_data=in_str,
                output_data=out_str,
                status=status,
                started_at=start,
                finished_at=now,
            )
            session.add(log_entry)
            await session.commit()
            await session.refresh(log_entry)
            return log_entry.id

    @abstractmethod
    async def run(self, *args, **kwargs) -> Any:
        """Run agent task."""
        pass

