"""Evidence File Server Router."""
from fastapi import APIRouter, HTTPException, Depends
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession
from pathlib import Path
import os

from database import get_db
from models.evidence import Evidence
import config

router = APIRouter(prefix="/api/evidence", tags=["evidence"])


@router.get("/{evidence_id}/file")
async def get_evidence_file(evidence_id: int, db: AsyncSession = Depends(get_db)):
    """Serve screenshot or video file for a specific evidence entry."""
    ev = await db.get(Evidence, evidence_id)
    if not ev or not ev.file_path or not os.path.exists(ev.file_path):
        raise HTTPException(status_code=404, detail="Evidence file not found")

    content_type = "image/png" if ev.type == "screenshot" else "video/webm"
    return FileResponse(ev.file_path, media_type=content_type, filename=ev.file_name)


@router.get("/raw/{filename}")
async def get_raw_evidence_file(filename: str):
    """Serve evidence file directly by filename."""
    file_path = config.EVIDENCE_DIR / filename
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found")

    content_type = "video/webm" if filename.endswith((".webm", ".mp4")) else "image/png"
    return FileResponse(str(file_path), media_type=content_type, filename=filename)

