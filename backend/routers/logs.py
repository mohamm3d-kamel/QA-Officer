"""Logs Router - System-wide Watcher telemetry, agent logs, summary stats, and manual Watcher scan."""
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from agents.coroner import CoronerAgent
from agents.detective import DetectiveAgent
from agents.watcher import WatcherAgent
from database import get_db
from models.agent_log import AgentLog
from models.settings import Settings
from services.llm_router import LLMRouter
from services.notification_service import NotificationService

router = APIRouter(prefix="/api/logs", tags=["logs"])


class WatcherScanRequest(BaseModel):
    project_id: Optional[int] = None
    include_app_health: bool = True
    notify: bool = True


@router.get("")
async def get_logs(
    project_id: Optional[int] = Query(None),
    agent: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    bug_id: Optional[int] = Query(None),
    module: Optional[str] = Query(None),
    category: Optional[str] = Query(None),
    level: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
):
    """Retrieve Watcher & agent logs with multi-dimensional filtering."""
    query = select(AgentLog).order_by(AgentLog.started_at.desc(), AgentLog.id.desc())

    if project_id:
        query = query.where(AgentLog.project_id == project_id)
    if agent:
        query = query.where(AgentLog.agent_name.ilike(f"%{agent}%"))
    if status:
        query = query.where(AgentLog.status == status)
    if bug_id:
        query = query.where(AgentLog.bug_report_id == bug_id)
    if module:
        query = query.where(AgentLog.module.ilike(f"%{module}%"))
    if category:
        query = query.where(AgentLog.category == category)
    if level:
        query = query.where(AgentLog.level == level)
    if search:
        pat = f"%{search.strip()}%"
        query = query.where(
            or_(
                AgentLog.action.ilike(pat),
                AgentLog.agent_name.ilike(pat),
                AgentLog.module.ilike(pat),
                AgentLog.jira_key.ilike(pat),
                AgentLog.input_data.ilike(pat),
                AgentLog.output_data.ilike(pat),
            )
        )

    query = query.limit(limit)
    result = await db.execute(query)
    logs = result.scalars().all()
    return [l.to_dict() for l in logs]


@router.get("/summary")
async def get_logs_summary(
    project_id: Optional[int] = Query(None),
    limit: int = Query(500, ge=1, le=2000),
    db: AsyncSession = Depends(get_db),
):
    """Aggregate Watcher telemetry across every module, every AI agent, failures, and notification channels."""
    query = select(AgentLog).order_by(AgentLog.started_at.desc(), AgentLog.id.desc())
    if project_id:
        query = query.where(AgentLog.project_id == project_id)
    logs = (await db.execute(query.limit(limit))).scalars().all()

    by_agent: Dict[str, Dict[str, Any]] = {}
    by_module: Dict[str, Dict[str, Any]] = {}
    recent_failures = []
    success_count = 0
    failed_count = 0
    running_count = 0
    change_count = 0
    slack_sent = 0
    email_sent = 0

    for lg in logs:
        st = (lg.status or "info").lower()
        lvl = (lg.level or "").lower()
        cat = (lg.category or "agent_action").lower()
        mod = lg.module or "System"
        ag = lg.agent_name or "System"

        if st == "success":
            success_count += 1
        elif st == "failed":
            failed_count += 1
            if len(recent_failures) < 10:
                recent_failures.append(lg.to_dict())
        elif st == "running":
            running_count += 1

        if lvl == "change" or cat in ("system_change", "status_change", "jira_sync"):
            change_count += 1

        channels = (lg.notified_channels or "").split(",")
        if "slack" in channels:
            slack_sent += 1
        if "email" in channels:
            email_sent += 1

        ab = by_agent.setdefault(
            ag,
            {
                "agent_name": ag,
                "total": 0,
                "success": 0,
                "failed": 0,
                "running": 0,
                "latest_action": lg.action,
                "latest_status": lg.status,
                "latest_at": lg.started_at.isoformat() if lg.started_at else None,
            },
        )
        ab["total"] += 1
        if st in ("success", "failed", "running"):
            ab[st] += 1

        mb = by_module.setdefault(
            mod,
            {
                "module": mod,
                "total": 0,
                "success": 0,
                "failed": 0,
                "changes": 0,
                "latest_action": lg.action,
                "latest_at": lg.started_at.isoformat() if lg.started_at else None,
            },
        )
        mb["total"] += 1
        if st == "success":
            mb["success"] += 1
        elif st == "failed":
            mb["failed"] += 1
        if lvl == "change" or cat in ("system_change", "status_change", "jira_sync"):
            mb["changes"] += 1

    return {
        "total_logs": len(logs),
        "success_count": success_count,
        "failed_count": failed_count,
        "running_count": running_count,
        "change_count": change_count,
        "notifications": {
            "slack_sent": slack_sent,
            "email_sent": email_sent,
            "total_external": slack_sent + email_sent,
        },
        "by_agent": sorted(by_agent.values(), key=lambda x: x["total"], reverse=True),
        "by_module": sorted(by_module.values(), key=lambda x: x["total"], reverse=True),
        "recent_failures": recent_failures,
    }


@router.post("/watcher-scan")
async def trigger_watcher_scan(
    payload: Optional[WatcherScanRequest] = None,
    db: AsyncSession = Depends(get_db),
):
    """Trigger an immediate full system-wide Watcher sweep across modules, agents, Jira, and logs."""
    req = payload or WatcherScanRequest()
    settings_rows = (await db.execute(select(Settings))).scalars().all()
    all_settings = {s.key: s.value for s in settings_rows}

    llm = LLMRouter(
        provider=all_settings.get("llm_provider", "openai"),
        api_key=all_settings.get("llm_api_key", ""),
        model=all_settings.get("llm_model", "gpt-4"),
        base_url=all_settings.get("llm_base_url", ""),
    )
    watcher = WatcherAgent(
        detective=DetectiveAgent(llm),
        coroner=CoronerAgent(),
        notification_service=NotificationService.from_settings(all_settings),
    )
    sweep = await watcher.run_full_system_sweep(
        jira_settings=all_settings,
        project_id=req.project_id,
        include_app_health=req.include_app_health,
        notify=req.notify,
    )
    return sweep
