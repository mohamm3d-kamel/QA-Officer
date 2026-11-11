"""QA Officer - Database Setup"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent / "site-packages"))

import re
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


def _migrate_agent_logs_columns(sync_conn):
    """Idempotently add Watcher telemetry columns to existing SQLite databases."""
    res = sync_conn.execute(text("PRAGMA table_info(agent_logs)"))
    existing_cols = {row[1] for row in res.fetchall()}
    migrations = {
        "module": "VARCHAR(120)",
        "category": "VARCHAR(50)",
        "level": "VARCHAR(20)",
        "jira_key": "VARCHAR(50)",
        "notified_channels": "VARCHAR(120)",
    }
    for col_name, col_type in migrations.items():
        if col_name not in existing_cols:
            sync_conn.execute(text(f"ALTER TABLE agent_logs ADD COLUMN {col_name} {col_type}"))


def _migrate_projects_columns(sync_conn):
    """Idempotently add report_format column to projects and backfill from stored report_format skills."""
    res = sync_conn.execute(text("PRAGMA table_info(projects)"))
    existing_cols = {row[1] for row in res.fetchall()}
    added_col = False
    if "report_format" not in existing_cols:
        sync_conn.execute(text("ALTER TABLE projects ADD COLUMN report_format TEXT"))
        added_col = True

    if added_col:
        proj_rows = sync_conn.execute(text("SELECT id, report_format FROM projects")).fetchall()
        for proj_id, current_rf in proj_rows:
            if current_rf and str(current_rf).strip():
                continue
            skill_rows = sync_conn.execute(
                text(
                    "SELECT id, title, content FROM skills "
                    "WHERE project_id = :pid AND type = 'report_format' "
                    "ORDER BY CASE WHEN lower(title) = 'default qa bug report format' THEN 1 ELSE 0 END ASC, id DESC"
                ),
                {"pid": proj_id},
            ).fetchall()
            if skill_rows:
                raw_content = (skill_rows[0][2] or "").strip()
                cleaned = re.sub(
                    r"^(?:(?:here\s+is|this\s+is|use|follow|set)\s+)?(?:the|my|our)?\s*(?:bug\s+)?report\s+format(?:\s+template)?\s*[:\-]*\s*\n+",
                    "",
                    raw_content,
                    flags=re.IGNORECASE,
                ).strip()
                if cleaned:
                    sync_conn.execute(
                        text("UPDATE projects SET report_format = :rf WHERE id = :pid"),
                        {"rf": cleaned, "pid": proj_id},
                    )


def _migrate_watcher_scope_settings(sync_conn):
    """One-time idempotent migration scoping legacy Watcher notification flags to Jira updates & AI agent failures only."""
    marker = sync_conn.execute(
        text("SELECT value FROM settings WHERE key = 'watcher_scope_jira_agent_failures_v1'")
    ).fetchone()
    if marker:
        return

    for k, v in (("notify_on_all_events", "false"), ("notify_on_success", "false")):
        existing = sync_conn.execute(text("SELECT id FROM settings WHERE key = :k"), {"k": k}).fetchone()
        if existing:
            sync_conn.execute(text("UPDATE settings SET value = :v WHERE key = :k"), {"k": k, "v": v})
        else:
            sync_conn.execute(text("INSERT INTO settings (key, value) VALUES (:k, :v)"), {"k": k, "v": v})

    sync_conn.execute(
        text("INSERT INTO settings (key, value) VALUES ('watcher_scope_jira_agent_failures_v1', 'true')")
    )


async def init_db():
    """Create all tables, apply column migrations, and ensure performance indexes exist."""
    async with engine.begin() as conn:
        from models import project, skill, bug_report, evidence, agent_log, settings, chat_message
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_migrate_agent_logs_columns)
        await conn.run_sync(_migrate_projects_columns)
        await conn.run_sync(_migrate_watcher_scope_settings)

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
            "CREATE INDEX IF NOT EXISTS ix_chat_messages_project_id ON chat_messages (project_id)",
        ]
        for stmt in index_statements:
            await conn.execute(text(stmt))
