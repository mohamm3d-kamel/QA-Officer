"""Chat Message model - persists Desk Officer & multi-agent conversations per project."""
import json
from datetime import datetime
from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey
from database import Base


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id = Column(Integer, primary_key=True, autoincrement=True)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    sender = Column(String(20), nullable=False)  # "user" or "agent"
    agent = Column(String(100), nullable=True)   # "Desk Officer", "Sheriff", etc.
    content = Column(Text, nullable=False)
    metadata_json = Column(Text, nullable=True)  # JSON: suggested_actions, pipeline_result, llm_mode, etc.
    created_at = Column(DateTime, default=datetime.utcnow)

    def to_dict(self):
        meta = {}
        if self.metadata_json:
            try:
                meta = json.loads(self.metadata_json)
            except Exception:
                meta = {}
        data = {
            "id": f"db-{self.id}",
            "db_id": self.id,
            "project_id": self.project_id,
            "sender": self.sender,
            "agent": self.agent,
            "content": self.content,
            "timestamp": self.created_at.isoformat() if self.created_at else None,
        }
        for key in ("suggested_actions", "pipeline_result", "assigned_agent", "llm_mode", "llm_notice", "attachments"):
            if key in meta and meta[key] is not None:
                data[key] = meta[key]
        return data
