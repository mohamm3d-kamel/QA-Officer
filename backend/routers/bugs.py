"""Bugs Router."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from pydantic import BaseModel
from typing import Optional, List

from database import get_db
from models.bug_report import BugReport
from models.project import Project
from models.settings import Settings
from agents.orchestrator import AgentOrchestrator
from routers.websocket import manager
from services.llm_router import LLMRouter
from services.watcher_bus import record_system_event

router = APIRouter(tags=["bugs"])


class AutoTestRequest(BaseModel):
    scope: str = "all"  # "all" or "selected"
    modules: Optional[List[str]] = None


class StatusUpdateRequest(BaseModel):
    status: str  # "draft", "reported", "in_qa", "resolved", "failed"


async def get_orchestrator(db: AsyncSession) -> AgentOrchestrator:
    """Helper to assemble an AgentOrchestrator from DB settings."""
    res = await db.execute(select(Settings))
    all_settings = {s.key: s.value for s in res.scalars().all()}

    provider = all_settings.get("llm_provider", "openai")
    api_key = all_settings.get("llm_api_key", "")
    model = all_settings.get("llm_model", "gpt-4")
    base_url = all_settings.get("llm_base_url", "")
    default_reporter = all_settings.get("default_reporter", "Mohamed Kamel")

    llm = LLMRouter(provider=provider, api_key=api_key, model=model, base_url=base_url)
    return AgentOrchestrator(llm_router=llm, jira_settings=all_settings, default_reporter=default_reporter)


@router.get("/api/projects/{project_id}/bugs")
async def get_project_bugs(project_id: int, db: AsyncSession = Depends(get_db)):
    """List all bug reports for a project with their evidence."""
    result = await db.execute(
        select(BugReport)
        .options(selectinload(BugReport.evidence))
        .where(BugReport.project_id == project_id)
        .order_by(BugReport.created_at.desc())
    )
    bugs = result.scalars().all()
    return [b.to_dict() for b in bugs]


@router.get("/api/bugs/{bug_id}")
async def get_bug(bug_id: int, db: AsyncSession = Depends(get_db)):
    """Get single bug report with evidence and logs."""
    result = await db.execute(
        select(BugReport)
        .options(selectinload(BugReport.evidence), selectinload(BugReport.agent_logs))
        .where(BugReport.id == bug_id)
    )
    bug = result.scalar_one_or_none()
    if not bug:
        raise HTTPException(status_code=404, detail="Bug report not found")
    data = bug.to_dict()
    data["agent_logs"] = [l.to_dict() for l in bug.agent_logs]
    return data


@router.delete("/api/bugs/{bug_id}")
async def delete_bug(bug_id: int, db: AsyncSession = Depends(get_db)):
    """Delete a bug report and its cascaded evidence/logs."""
    res = await db.execute(
        select(BugReport)
        .options(selectinload(BugReport.evidence), selectinload(BugReport.agent_logs))
        .where(BugReport.id == bug_id)
    )
    bug = res.scalar_one_or_none()
    if not bug:
        raise HTTPException(status_code=404, detail="Bug report not found")

    await db.delete(bug)
    await db.commit()
    return {"message": "Bug report deleted", "id": bug_id}


@router.post("/api/bugs/{bug_id}/raise-jira")
async def raise_bug_to_jira(bug_id: int, force_new: bool = False, db: AsyncSession = Depends(get_db)):
    """Explicitly trigger Sheriff to raise a Jira ticket in 'Backlog' for an existing bug report."""
    bug = await db.get(BugReport, bug_id)
    if not bug:
        raise HTTPException(status_code=404, detail="Bug report not found")

    orchestrator = await get_orchestrator(db)
    await db.rollback()

    async def broadcast_ws(payload):
        await manager.broadcast({**payload, "project_id": payload.get("project_id") or bug.project_id})

    ticket = await orchestrator.raise_jira_ticket(
        bug_report_id=bug_id,
        broadcast_callback=broadcast_ws,
        force_new=force_new,
    )
    if ticket.get("error") or not ticket.get("key"):
        raise HTTPException(status_code=502, detail=ticket.get("error") or "Sheriff failed to create Jira ticket")
    return {"bug_id": bug_id, "jira_ticket": ticket}


@router.post("/api/bugs/{bug_id}/retest")
async def trigger_retest(bug_id: int, db: AsyncSession = Depends(get_db)):
    """Trigger Watcher and Detective to retest an 'In QA' bug and run sanity tests if resolved."""
    bug = await db.get(BugReport, bug_id)
    if not bug:
        raise HTTPException(status_code=404, detail="Bug report not found")

    orchestrator = await get_orchestrator(db)

    async def broadcast_ws(payload):
        await manager.broadcast({**payload, "project_id": payload.get("project_id") or bug.project_id})

    result = await orchestrator.retest_and_sanity_pipeline(
        bug_report_id=bug_id,
        broadcast_callback=broadcast_ws,
    )
    return result


@router.put("/api/bugs/{bug_id}/status")
async def update_bug_status(bug_id: int, data: StatusUpdateRequest, db: AsyncSession = Depends(get_db)):
    """Update bug status (e.g. moving it to 'in_qa' to trigger retesting)."""
    bug = await db.get(BugReport, bug_id)
    if not bug:
        raise HTTPException(status_code=404, detail="Bug report not found")

    old_status = bug.status
    bug.status = data.status
    await db.commit()
    await db.refresh(bug)

    is_in_qa = data.status.lower() in ("in_qa", "in qa")
    await record_system_event(
        action="bug_status_changed",
        summary=f"Bug #{bug.id} ('{bug.title[:70]}') status changed from '{old_status}' to '{data.status}'.",
        module=bug.module or "Bug Lifecycle",
        category="status_change",
        status="success",
        level="change",
        agent_name="System",
        project_id=bug.project_id,
        bug_report_id=bug.id,
        jira_key=bug.jira_ticket_id,
        input_data={"old_status": old_status, "new_status": data.status},
        output_data={"bug_id": bug.id, "status": data.status},
        notify=False,
        notify_ui_bell=False,
    )

    if is_in_qa:
        orchestrator = await get_orchestrator(db)

        async def broadcast_ws(payload):
            await manager.broadcast({**payload, "project_id": payload.get("project_id") or bug.project_id})

        retest_result = await orchestrator.retest_and_sanity_pipeline(
            bug_report_id=bug_id,
            broadcast_callback=broadcast_ws,
        )
        return {"bug": bug.to_dict(), "retest_result": retest_result}

    return bug.to_dict()


@router.post("/api/projects/{project_id}/bugs/auto-test")
async def run_auto_test(project_id: int, data: AutoTestRequest, db: AsyncSession = Depends(get_db)):
    """Autonomous Bug Discovery: Analyzes all stored requirements and tests the application."""
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    orchestrator = await get_orchestrator(db)

    async def broadcast_ws(payload):
        await manager.broadcast({**payload, "project_id": payload.get("project_id") or project_id})

    try:
        result = await orchestrator.run_auto_test(
            project_id=project_id,
            scope=data.scope,
            selected_modules=data.modules,
            broadcast_callback=broadcast_ws,
        )

        await record_system_event(
            action="auto_test_completed",
            summary=f"Auto-Test discovered {result['discovered_count']} potential bug(s) and created {result['processed_count']} report(s) for {project.name}.",
            module=", ".join(data.modules[:3]) if data.modules else "Auto-Test",
            category="agent_action",
            status="success",
            level="success",
            agent_name="Auto-Test",
            project_id=project_id,
            output_data={
                "discovered_count": result["discovered_count"],
                "processed_pipeline_count": result["processed_count"],
                "skipped_duplicates": result.get("skipped_duplicates", 0),
            },
            notify=False,
            notify_ui_bell=False,
        )

        return {
            "discovered_count": result["discovered_count"],
            "processed_pipeline_count": result["processed_count"],
            "skipped_duplicates": result.get("skipped_duplicates", 0),
            "bugs": result["reports"],
        }
    except Exception as e:
        await record_system_event(
            action="auto_test_failed",
            summary=f"Auto-Test execution failed on {project.name}: {e}",
            module="Auto-Test",
            category="error",
            status="failed",
            level="error",
            agent_name="Auto-Test",
            project_id=project_id,
            output_data={"error": str(e)},
        )
        raise
