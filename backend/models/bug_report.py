"""Bug Report model."""
from datetime import datetime
from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from database import Base


class BugReport(Base):
    __tablename__ = "bug_reports"

    id = Column(Integer, primary_key=True, autoincrement=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False, index=True)
    title = Column(String(500), nullable=False)
    description = Column(Text, nullable=True)
    root_cause = Column(Text, nullable=True)
    steps_to_reproduce = Column(Text, nullable=True)
    severity = Column(String(50), default="medium")  # lowest, low, medium, high, highest
    status = Column(String(50), default="draft", index=True)  # draft, confirmed, reported, in_qa, resolved, failed
    jira_ticket_id = Column(String(100), nullable=True)
    jira_ticket_url = Column(String(500), nullable=True)
    label = Column(String(50), nullable=True)  # Back-End, Front-End, Mobile
    formatted_report = Column(Text, nullable=True)
    module = Column(String(255), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    project = relationship("Project", back_populates="bug_reports")
    evidence = relationship("Evidence", back_populates="bug_report", cascade="all, delete-orphan", lazy="selectin")
    agent_logs = relationship("AgentLog", back_populates="bug_report", cascade="all, delete-orphan", lazy="selectin")

    def to_dict(self):
        return {
            "id": self.id,
            "project_id": self.project_id,
            "title": self.title,
            "description": self.description,
            "root_cause": self.root_cause,
            "steps_to_reproduce": self.steps_to_reproduce,
            "severity": self.severity,
            "status": self.status,
            "jira_ticket_id": self.jira_ticket_id,
            "jira_ticket_url": self.jira_ticket_url,
            "label": self.label,
            "formatted_report": self.formatted_report,
            "module": self.module,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "evidence": [e.to_dict() for e in self.evidence] if self.evidence else [],
        }

