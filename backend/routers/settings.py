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
    """Get all settings, initialized with defaults and with secrets masked."""
    res = await db.execute(select(Settings))
    existing = {s.key: s.value for s in res.scalars().all()}

    # Merge with defaults and mask secrets before returning
    merged = {**config.DEFAULTS, **existing}
    return [
        {
            "key": k,
            "value": config.mask_secret(v) if k in config.SECRET_SETTING_KEYS else v,
        }
        for k, v in merged.items()
    ]


@router.put("")
async def update_settings(payload: Dict[str, Any], db: AsyncSession = Depends(get_db)):
    """Update settings in bulk while preserving existing secrets if masked placeholders are sent back."""
    res = await db.execute(select(Settings))
    existing = {s.key: s for s in res.scalars().all()}

    for key, value in payload.items():
        val_str = str(value) if value is not None else ""
        # Keep existing stored secret if client submits back the unchanged mask prefix
        if key in config.SECRET_SETTING_KEYS and val_str.startswith("••••"):
            continue
        if key in existing:
            existing[key].value = val_str
        else:
            new_s = Settings(key=key, value=val_str)
            db.add(new_s)

    await db.commit()
    return {"message": "Settings updated successfully"}

