"""Settings Router."""
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import Dict, Any

from database import get_db
from models.settings import Settings
import config

router = APIRouter(prefix="/api/settings", tags=["settings"])


@router.get("")
async def get_settings(db: AsyncSession = Depends(get_db)):
    """Get all settings, initialized with defaults."""
    res = await db.execute(select(Settings))
    existing = {s.key: s.value for s in res.scalars().all()}

    # Merge with defaults
    merged = {**config.DEFAULTS, **existing}
    return [{"key": k, "value": v} for k, v in merged.items()]


@router.put("")
async def update_settings(payload: Dict[str, Any], db: AsyncSession = Depends(get_db)):
    """Update settings in bulk."""
    res = await db.execute(select(Settings))
    existing = {s.key: s for s in res.scalars().all()}

    for key, value in payload.items():
        val_str = str(value) if value is not None else ""
        if key in existing:
            existing[key].value = val_str
        else:
            new_s = Settings(key=key, value=val_str)
            db.add(new_s)

    await db.commit()
    return {"message": "Settings updated successfully"}

