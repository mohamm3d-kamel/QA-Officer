"""Evidence File Server Router."""
from fastapi import APIRouter, HTTPException, Depends
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession
from pathlib import Path

from database import get_db
from models.evidence import Evidence
import config

router = APIRouter(prefix="/api/evidence", tags=["evidence"])

_MEDIA_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".webm": "video/webm",
    ".mp4": "video/mp4",
}


def _resolve_safe_evidence_path(raw_path: str | Path) -> Path:
    """Resolve and validate that a path remains strictly within config.EVIDENCE_DIR."""
    base_dir = config.EVIDENCE_DIR.resolve()
    candidate = Path(raw_path).resolve()
    if not candidate.is_relative_to(base_dir):
        raise HTTPException(status_code=403, detail="Access denied: invalid evidence path")
    if not candidate.is_file():
        raise HTTPException(status_code=404, detail="Evidence file not found")
    return candidate


def _detect_media_type(path: Path, fallback_type: str = "screenshot") -> str:
    ext = path.suffix.lower()
    if ext in _MEDIA_TYPES:
        return _MEDIA_TYPES[ext]
    return "video/webm" if fallback_type == "video" else "image/png"


@router.get("/{evidence_id}/file")
async def get_evidence_file(evidence_id: int, db: AsyncSession = Depends(get_db)):
    """Serve screenshot or video file for a specific evidence entry."""
    ev = await db.get(Evidence, evidence_id)
    if not ev or not ev.file_path:
        raise HTTPException(status_code=404, detail="Evidence file not found")

    safe_path = _resolve_safe_evidence_path(ev.file_path)
    content_type = _detect_media_type(safe_path, ev.type)
    return FileResponse(str(safe_path), media_type=content_type, filename=ev.file_name or safe_path.name)


@router.get("/raw/{filename:path}")
async def get_raw_evidence_file(filename: str):
    """Serve evidence file directly by filename within EVIDENCE_DIR."""
    base_dir = config.EVIDENCE_DIR.resolve()
    safe_path = _resolve_safe_evidence_path(base_dir / filename)
    content_type = _detect_media_type(safe_path)
    return FileResponse(str(safe_path), media_type=content_type, filename=safe_path.name)

