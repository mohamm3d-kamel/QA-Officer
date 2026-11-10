"""QA Officer - Database Setup"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent / "site-packages"))

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from config import DATABASE_URL

engine = create_async_engine(DATABASE_URL, echo=False)


@event.listens_for(engine.sync_engine, "connect")
def _set_sqlite_pragma(dbapi_connection, connection_record):
    """Enable SQLite WAL mode, busy timeout, and foreign keys on every connection."""
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_db():
    """Dependency for getting database sessions."""
    async with async_session() as session:
        yield session


async def init_db():
    """Create all tables and ensure performance indexes exist."""
    async with engine.begin() as conn:
        from models import project, skill, bug_report, evidence, agent_log, settings
        await conn.run_sync(Base.metadata.create_all)

        index_statements = [
            "CREATE INDEX IF NOT EXISTS ix_bug_reports_project_id ON bug_reports (project_id)",
            "CREATE INDEX IF NOT EXISTS ix_bug_reports_status ON bug_reports (status)",
            "CREATE INDEX IF NOT EXISTS ix_bug_reports_created_at ON bug_reports (created_at)",
            "CREATE INDEX IF NOT EXISTS ix_skills_project_id ON skills (project_id)",
            "CREATE INDEX IF NOT EXISTS ix_skills_type ON skills (type)",
            "CREATE INDEX IF NOT EXISTS ix_evidence_bug_report_id ON evidence (bug_report_id)",
            "CREATE INDEX IF NOT EXISTS ix_agent_logs_project_id ON agent_logs (project_id)",
            "CREATE INDEX IF NOT EXISTS ix_agent_logs_bug_report_id ON agent_logs (bug_report_id)",
            "CREATE INDEX IF NOT EXISTS ix_agent_logs_started_at ON agent_logs (started_at)",
            "CREATE INDEX IF NOT EXISTS ix_agent_logs_agent_name ON agent_logs (agent_name)",
        ]
        for stmt in index_statements:
            await conn.execute(text(stmt))

