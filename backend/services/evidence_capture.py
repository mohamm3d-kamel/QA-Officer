"""Evidence Capture Service - Manages screenshot and video evidence."""
import uuid
import shutil
import logging
from pathlib import Path
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

EVIDENCE_DIR = Path(__file__).parent.parent / "storage" / "evidence"
EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)


class EvidenceCapture:
    """Manages evidence files (screenshots, videos) for bug reports."""

    @staticmethod
    async def save_uploaded_file(
        file_content: bytes,
        file_name: str,
        evidence_type: str = "screenshot",
    ) -> str:
        """Save an uploaded evidence file and return its path."""
        ext = Path(file_name).suffix or (".png" if evidence_type == "screenshot" else ".webm")
        unique_name = f"{evidence_type}_{uuid.uuid4().hex[:8]}_{datetime.now().strftime('%Y%m%d_%H%M%S')}{ext}"
        filepath = EVIDENCE_DIR / unique_name
        
        with open(filepath, "wb") as f:
            f.write(file_content)
        
        logger.info(f"Saved evidence: {filepath}")
        return str(filepath)

    @staticmethod
    async def copy_evidence(source_path: str) -> str:
        """Copy an evidence file to the evidence directory."""
        source = Path(source_path)
        if not source.exists():
            raise FileNotFoundError(f"Source evidence not found: {source_path}")
        
        dest_name = f"evidence_{uuid.uuid4().hex[:8]}_{source.name}"
        dest = EVIDENCE_DIR / dest_name
        shutil.copy2(str(source), str(dest))
        
        logger.info(f"Copied evidence: {source} -> {dest}")
        return str(dest)

    @staticmethod
    def get_evidence_path(filename: str) -> Optional[Path]:
        """Get the full path to an evidence file."""
        filepath = EVIDENCE_DIR / filename
        if filepath.exists():
            return filepath
        return None

    @staticmethod
    def list_evidence() -> list[dict]:
        """List all evidence files."""
        files = []
        for f in EVIDENCE_DIR.iterdir():
            if f.is_file() and not f.name.startswith("."):
                files.append({
                    "name": f.name,
                    "path": str(f),
                    "size": f.stat().st_size,
                    "type": "video" if f.suffix in (".webm", ".mp4", ".avi") else "screenshot",
                    "created": datetime.fromtimestamp(f.stat().st_ctime).isoformat(),
                })
        return files

