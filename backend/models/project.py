"""Project model."""
from datetime import datetime
from sqlalchemy import Column, Integer, String, Text, DateTime
from sqlalchemy.orm import relationship
from database import Base


class Project(Base):
    __tablename__ = "projects"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(255), nullable=False)
    jira_project_key = Column(String(50), nullable=True)
    jira_base_url = Column(String(500), nullable=True)
    app_url = Column(String(500), nullable=True)
    repo_url = Column(String(500), nullable=True)
    description = Column(Text, nullable=True)
    report_format = Column(Text, nullable=True)
    environment_credentials = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    skills = relationship("Skill", back_populates="project", cascade="all, delete-orphan", lazy="selectin")
    bug_reports = relationship("BugReport", back_populates="project", cascade="all, delete-orphan", lazy="selectin")

    def to_dict(self, include_secrets: bool = False):
        from services.environment_credentials import (
            mask_environment_credentials,
            parse_environment_credentials,
        )

        creds = (
            parse_environment_credentials(self.environment_credentials)
            if include_secrets
            else mask_environment_credentials(self.environment_credentials)
        )
        return {
            "id": self.id,
            "name": self.name,
            "jira_project_key": self.jira_project_key,
            "jira_base_url": self.jira_base_url,
            "app_url": self.app_url,
            "repo_url": self.repo_url,
            "description": self.description,
            "report_format": self.report_format,
            "environment_credentials": creds,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
