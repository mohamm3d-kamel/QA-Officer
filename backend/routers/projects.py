"""Projects Router."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete
from pydantic import BaseModel
from typing import Optional

from database import get_db
from models.project import Project
from models.agent_log import AgentLog

router = APIRouter(prefix="/api/projects", tags=["projects"])


class ProjectCreate(BaseModel):
    name: str
    jira_project_key: Optional[str] = "QA"
    jira_base_url: Optional[str] = None
    app_url: Optional[str] = None
    repo_url: Optional[str] = None
    description: Optional[str] = None


class ProjectUpdate(BaseModel):
    name: Optional[str] = None
    jira_project_key: Optional[str] = None
    jira_base_url: Optional[str] = None
    app_url: Optional[str] = None
    repo_url: Optional[str] = None
    description: Optional[str] = None


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
    )
    db.add(project)
    await db.commit()
    await db.refresh(project)
    return project.to_dict()


@router.get("/{project_id}")
async def get_project(project_id: int, db: AsyncSession = Depends(get_db)):
    """Get project details by ID."""
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project.to_dict()


@router.put("/{project_id}")
async def update_project(project_id: int, data: ProjectUpdate, db: AsyncSession = Depends(get_db)):
    """Update project details."""
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    for field, val in data.model_dump(exclude_unset=True).items():
        setattr(project, field, val)

    await db.commit()
    await db.refresh(project)
    return project.to_dict()


@router.delete("/{project_id}")
async def delete_project(project_id: int, db: AsyncSession = Depends(get_db)):
    """Delete project and its orphaned agent audit logs."""
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    await db.delete(project)
    await db.flush()
    await db.execute(delete(AgentLog).where(AgentLog.project_id == project_id))
    await db.commit()
    return {"message": "Project deleted successfully", "id": project_id}

