"""Evidence model - screenshots and videos captured by agents."""
from datetime import datetime
from sqlalchemy import Column, Integer, String, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from database import Base


class Evidence(Base):
    __tablename__ = "evidence"

    id = Column(Integer, primary_key=True, autoincrement=True)
    bug_report_id = Column(Integer, ForeignKey("bug_reports.id"), nullable=False)
    type = Column(String(20), nullable=False)  # screenshot, video
    file_path = Column(String(500), nullable=False)
    file_name = Column(String(255), nullable=True)
    stage = Column(String(50), nullable=False)  # detection, retest, sanity
    description = Column(String(500), nullable=True)
    captured_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    bug_report = relationship("BugReport", back_populates="evidence")

    def to_dict(self):
        return {
            "id": self.id,
            "bug_report_id": self.bug_report_id,
            "type": self.type,
            "file_path": self.file_path,
            "file_name": self.file_name,
            "stage": self.stage,
            "description": self.description,
            "captured_at": self.captured_at.isoformat() if self.captured_at else None,
        }

