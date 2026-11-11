"""Agent Log model - tracks all agent actions, module telemetry, and system events."""
from datetime import datetime
from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from database import Base


class AgentLog(Base):
    __tablename__ = "agent_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    bug_report_id = Column(Integer, ForeignKey("bug_reports.id"), nullable=True, index=True)
    project_id = Column(Integer, nullable=True, index=True)
    agent_name = Column(String(50), nullable=False, index=True)
    action = Column(String(255), nullable=False)
    input_data = Column(Text, nullable=True)
    output_data = Column(Text, nullable=True)
    status = Column(String(20), default="running")  # running, success, failed, degraded
    module = Column(String(120), nullable=True)
    category = Column(String(50), nullable=True)  # agent_action, system_change, status_change, module_health, jira_sync, error, notification_delivery, watcher_sweep
    level = Column(String(20), nullable=True)  # info, success, change, warning, error
    jira_key = Column(String(50), nullable=True)
    notified_channels = Column(String(120), nullable=True)
    started_at = Column(DateTime, default=datetime.utcnow, index=True)
    finished_at = Column(DateTime, nullable=True)

    # Relationships
    bug_report = relationship("BugReport", back_populates="agent_logs")

    def to_dict(self):
        return {
            "id": self.id,
            "bug_report_id": self.bug_report_id,
            "project_id": self.project_id,
            "agent_name": self.agent_name,
            "action": self.action,
            "input_data": self.input_data,
            "output_data": self.output_data,
            "status": self.status,
            "module": self.module,
            "category": self.category,
            "level": self.level,
            "jira_key": self.jira_key,
            "notified_channels": self.notified_channels,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
        }
