"""Projects Router."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete
from pydantic import BaseModel
from typing import Optional

from agents.desk_officer import AGENT_ROSTER
from agents.reporter import strip_report_format_preamble
from database import get_db
from models.agent_log import AgentLog
from models.chat_message import ChatMessage
from models.project import Project
from models.settings import Settings
from services.project_assets import ProjectAssetHub
from services.watcher_bus import record_system_event

router = APIRouter(prefix="/api/projects", tags=["projects"])


class ProjectCreate(BaseModel):
    name: str
    jira_project_key: Optional[str] = "QA"
    jira_base_url: Optional[str] = None
    app_url: Optional[str] = None
    repo_url: Optional[str] = None
    description: Optional[str] = None
    report_format: Optional[str] = None


class ProjectUpdate(BaseModel):
    name: Optional[str] = None
    jira_project_key: Optional[str] = None
    jira_base_url: Optional[str] = None
    app_url: Optional[str] = None
    repo_url: Optional[str] = None
    description: Optional[str] = None
    report_format: Optional[str] = None


@router.get("")
async def get_projects(db: AsyncSession = Depends(get_db)):
    """List all projects."""
    result = await db.execute(select(Project).order_by(Project.created_at.desc()))
    projects = result.scalars().all()
    return [p.to_dict() for p in projects]


@router.post("")
async def create_project(data: ProjectCreate, db: AsyncSession = Depends(get_db)):
    """Create a new project."""
    project = Project(
        name=data.name,
        jira_project_key=data.jira_project_key,
        jira_base_url=data.jira_base_url,
        app_url=data.app_url,
        repo_url=data.repo_url,
        description=data.description,
        report_format=strip_report_format_preamble(data.report_format),
    )
    db.add(project)
    await db.commit()
    await db.refresh(project)

    await record_system_event(
        action="project_created",
        summary=f"Created project '{project.name}' (Jira key: {project.jira_project_key or 'QA'}, App URL: {project.app_url or 'N/A'}).",
        module="Projects",
        category="system_change",
        status="success",
        level="change",
        agent_name="System",
        project_id=project.id,
        output_data=project.to_dict(),
        notify=False,
        notify_ui_bell=False,
    )
    return project.to_dict()


@router.get("/{project_id}")
async def get_project(project_id: int, db: AsyncSession = Depends(get_db)):
    """Get project details by ID."""
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project.to_dict()


@router.get("/{project_id}/assets")
async def get_project_assets(project_id: int, db: AsyncSession = Depends(get_db)):
    """Return full sanitized project asset inventory and available QA agents."""
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    res = await db.execute(select(Settings))
    all_settings = {s.key: s.value for s in res.scalars().all()}

    hub = ProjectAssetHub(all_settings)
    snapshot = await hub.collect_snapshot(project_id)
    snapshot["agents"] = AGENT_ROSTER
    return snapshot


@router.put("/{project_id}")
async def update_project(project_id: int, data: ProjectUpdate, db: AsyncSession = Depends(get_db)):
    """Update project details."""
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    updates = data.model_dump(exclude_unset=True)
    if "report_format" in updates and updates["report_format"] is not None:
        updates["report_format"] = strip_report_format_preamble(updates["report_format"])
    for field, val in updates.items():
        setattr(project, field, val)

    await db.commit()
    await db.refresh(project)

    await record_system_event(
        action="project_updated",
        summary=f"Updated project '{project.name}' fields: {', '.join(updates.keys()) or 'none'}.",
        module="Projects",
        category="system_change",
        status="success",
        level="change",
        agent_name="System",
        project_id=project.id,
        input_data={"updated_fields": list(updates.keys())},
        output_data=project.to_dict(),
        notify=False,
        notify_ui_bell=False,
    )
    return project.to_dict()


@router.delete("/{project_id}")
async def delete_project(project_id: int, db: AsyncSession = Depends(get_db)):
    """Delete project, its chat history, and its orphaned agent audit logs safely under FK enforcement."""
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    proj_name = project.name
    await db.execute(delete(ChatMessage).where(ChatMessage.project_id == project_id))
    await db.execute(delete(AgentLog).where(AgentLog.project_id == project_id))
    await db.delete(project)
    await db.flush()
    await db.commit()

    await record_system_event(
        action="project_deleted",
        summary=f"Deleted project #{project_id} ('{proj_name}').",
        module="Projects",
        category="system_change",
        status="success",
        level="change",
        agent_name="System",
        project_id=None,
        input_data={"deleted_project_id": project_id, "name": proj_name},
        output_data={"id": project_id, "deleted": True},
        notify=False,
        notify_ui_bell=False,
    )
    return {"message": "Project deleted successfully", "id": project_id}
