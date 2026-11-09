"""Skills Router - Stores and manages requirements, business docs, and report formats."""
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel
from typing import Optional

from database import get_db
from models.skill import Skill
from models.project import Project

router = APIRouter(tags=["skills"])


class SkillCreate(BaseModel):
    type: str  # requirement, report_format, test_case, business_doc
    title: str
    content: str


@router.get("/api/projects/{project_id}/skills")
async def get_project_skills(project_id: int, db: AsyncSession = Depends(get_db)):
    """List all stored skills/requirements for a project."""
    result = await db.execute(
        select(Skill).where(Skill.project_id == project_id).order_by(Skill.created_at.desc())
    )
    skills = result.scalars().all()
    return [s.to_dict() for s in skills]


@router.post("/api/projects/{project_id}/skills")
async def add_project_skill(project_id: int, data: SkillCreate, db: AsyncSession = Depends(get_db)):
    """Add a new requirement or report format skill."""
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    skill = Skill(
        project_id=project_id,
        type=data.type,
        title=data.title,
        content=data.content,
    )
    db.add(skill)
    await db.commit()
    await db.refresh(skill)
    return skill.to_dict()


@router.post("/api/projects/{project_id}/skills/upload")
async def upload_skill_file(
    project_id: int,
    type: str = Form("requirement"),
    title: str = Form("Uploaded Requirement"),
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    """Upload a requirements document or file and store it as a reusable skill."""
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    content_bytes = await file.read()
    try:
        content_text = content_bytes.decode("utf-8")
    except UnicodeDecodeError:
        content_text = f"[Binary Document uploaded: {file.filename}, size {len(content_bytes)} bytes]"

    skill = Skill(
        project_id=project_id,
        type=type,
        title=title or file.filename,
        content=content_text,
    )
    db.add(skill)
    await db.commit()
    await db.refresh(skill)
    return skill.to_dict()


@router.delete("/api/skills/{skill_id}")
async def delete_skill(skill_id: int, db: AsyncSession = Depends(get_db)):
    """Delete a stored skill."""
    skill = await db.get(Skill, skill_id)
    if not skill:
        raise HTTPException(status_code=404, detail="Skill not found")

    await db.delete(skill)
    await db.commit()
    return {"message": "Skill deleted", "id": skill_id}

