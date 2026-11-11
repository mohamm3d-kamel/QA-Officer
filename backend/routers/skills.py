"""Skills Router - Stores and manages requirements, business docs, and report formats."""
import io
import zipfile
import xml.etree.ElementTree as ET
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel

from database import get_db
from models.skill import Skill
from models.project import Project
from services.report_formatter import strip_report_format_preamble
from services.watcher_bus import record_system_event

router = APIRouter(tags=["skills"])

MAX_SKILL_UPLOAD_BYTES = 5 * 1024 * 1024  # 5 MB


def _extract_upload_text(filename: str, content_bytes: bytes) -> str:
    """Extract readable UTF-8 text or .docx paragraphs from an uploaded skill document."""
    name_lower = (filename or "").lower()
    if name_lower.endswith(".docx") or (content_bytes[:2] == b"PK"):
        try:
            with zipfile.ZipFile(io.BytesIO(content_bytes)) as zf:
                if "word/document.xml" in zf.namelist():
                    info = zf.getinfo("word/document.xml")
                    if info.file_size <= 10 * 1024 * 1024:
                        xml_bytes = zf.read("word/document.xml")
                        root = ET.fromstring(xml_bytes)
                        paragraphs = []
                        for p in root.iter():
                            if p.tag.endswith("}p"):
                                texts = [t.text for t in p.iter() if t.tag.endswith("}t") and t.text]
                                if texts:
                                    paragraphs.append("".join(texts))
                        extracted = "\n".join(paragraphs).strip()
                        if extracted:
                            return extracted
        except Exception:
            pass

    try:
        return content_bytes.decode("utf-8-sig")
    except UnicodeDecodeError:
        return f"[Binary Document uploaded: {filename}, size {len(content_bytes)} bytes]"


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
        content=(strip_report_format_preamble(data.content) or data.content) if data.type == "report_format" else data.content,
    )
    db.add(skill)
    if data.type == "report_format" and (data.content or "").strip():
        project.report_format = strip_report_format_preamble(data.content) or data.content.strip()
    await db.commit()
    await db.refresh(skill)

    await record_system_event(
        action="skill_created",
        summary=f"Added {skill.type} skill '{skill.title}' to {project.name}.",
        module="Skills",
        category="system_change",
        status="success",
        level="change",
        agent_name="System",
        project_id=project_id,
        output_data={"id": skill.id, "type": skill.type, "title": skill.title},
        notify=False,
        notify_ui_bell=False,
    )
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

    content_bytes = await file.read(MAX_SKILL_UPLOAD_BYTES + 1)
    if len(content_bytes) > MAX_SKILL_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="Uploaded document exceeds 5 MB limit")

    content_text = _extract_upload_text(file.filename or "document", content_bytes)

    skill = Skill(
        project_id=project_id,
        type=type,
        title=title or file.filename,
        content=(strip_report_format_preamble(content_text) or content_text) if type == "report_format" else content_text,
    )
    db.add(skill)
    if type == "report_format" and (content_text or "").strip():
        project.report_format = strip_report_format_preamble(content_text) or content_text.strip()
    await db.commit()
    await db.refresh(skill)

    await record_system_event(
        action="skill_uploaded",
        summary=f"Uploaded skill file '{file.filename}' ({skill.type}: '{skill.title}') to {project.name}.",
        module="Skills",
        category="system_change",
        status="success",
        level="change",
        agent_name="System",
        project_id=project_id,
        output_data={"id": skill.id, "filename": file.filename, "type": skill.type, "title": skill.title},
        notify=False,
        notify_ui_bell=False,
    )
    return skill.to_dict()


@router.delete("/api/skills/{skill_id}")
async def delete_skill(skill_id: int, db: AsyncSession = Depends(get_db)):
    """Delete a stored skill."""
    skill = await db.get(Skill, skill_id)
    if not skill:
        raise HTTPException(status_code=404, detail="Skill not found")

    proj_id = skill.project_id
    skill_title = skill.title
    skill_type = skill.type
    await db.delete(skill)
    await db.commit()

    await record_system_event(
        action="skill_deleted",
        summary=f"Deleted skill #{skill_id} ('{skill_title}', type={skill_type}).",
        module="Skills",
        category="system_change",
        status="success",
        level="change",
        agent_name="System",
        project_id=proj_id,
        output_data={"id": skill_id, "title": skill_title, "deleted": True},
        notify=False,
        notify_ui_bell=False,
    )
    return {"message": "Skill deleted", "id": skill_id}
