"""Logs Router - Agent activity history and ticket status transition logs."""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import Optional
from datetime import datetime

from database import get_db
from models.agent_log import AgentLog

router = APIRouter(prefix="/api/logs", tags=["logs"])


@router.get("")
async def get_logs(
    project_id: Optional[int] = Query(None),
    agent: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    bug_id: Optional[int] = Query(None),
    limit: int = Query(100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
):
    """Retrieve agent logs with dynamic filtering."""
    query = select(AgentLog).order_by(AgentLog.started_at.desc())

    if project_id:
        query = query.where(AgentLog.project_id == project_id)
    if agent:
        query = query.where(AgentLog.agent_name.ilike(f"%{agent}%"))
    if status:
        query = query.where(AgentLog.status == status)
    if bug_id:
        query = query.where(AgentLog.bug_report_id == bug_id)

    query = query.limit(limit)
    result = await db.execute(query)
    logs = result.scalars().all()
    return [l.to_dict() for l in logs]

