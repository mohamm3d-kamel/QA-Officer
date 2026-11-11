"""Settings Router - System configuration, live LLM verification, and Slack/Email notification tests."""
import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import config
from database import get_db
from models.settings import Settings
from services.llm_router import LLMRouter
from services.notification_service import NotificationService
from services.watcher_bus import _write_internal_log_no_loop, record_system_event, sanitize_for_log

router = APIRouter(prefix="/api/settings", tags=["settings"])


class TestLLMRequest(BaseModel):
    llm_provider: Optional[str] = None
    llm_api_key: Optional[str] = None
    llm_model: Optional[str] = None
    llm_base_url: Optional[str] = None


class TestNotificationRequest(BaseModel):
    channel: Optional[str] = "all"  # "all" | "slack" | "email"
    notification_email_enabled: Optional[str] = None
    notification_email: Optional[str] = None
    smtp_host: Optional[str] = None
    smtp_port: Optional[str] = None
    smtp_username: Optional[str] = None
    smtp_password: Optional[str] = None
    smtp_from_email: Optional[str] = None
    smtp_from: Optional[str] = None
    smtp_use_tls: Optional[str] = None
    smtp_use_ssl: Optional[str] = None
    notification_slack_enabled: Optional[str] = None
    notification_slack_webhook: Optional[str] = None


@router.get("")
async def get_settings(db: AsyncSession = Depends(get_db)):
    """Get all settings, initialized with defaults and with secrets masked."""
    res = await db.execute(select(Settings))
    existing = {s.key: s.value for s in res.scalars().all()}

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
    changed_keys: List[str] = []

    for key, value in payload.items():
        val_str = str(value) if value is not None else ""
        # Keep existing stored secret if client submits back the masked placeholder
        if key in config.SECRET_SETTING_KEYS and val_str.startswith("••••"):
            continue
        if key in existing:
            if existing[key].value != val_str:
                changed_keys.append(key)
            existing[key].value = val_str
        else:
            changed_keys.append(key)
            new_s = Settings(key=key, value=val_str)
            db.add(new_s)

    await db.commit()

    if changed_keys:
        await record_system_event(
            action="settings_updated",
            summary=f"System configuration updated ({len(changed_keys)} setting(s): {', '.join(changed_keys[:8])}).",
            module="Settings",
            category="system_change",
            status="success",
            level="change",
            agent_name="System",
            input_data={"changed_keys": changed_keys, "values": sanitize_for_log(payload)},
            output_data={"updated_count": len(changed_keys), "changed_keys": changed_keys},
            notify=False,
            notify_ui_bell=False,
        )

    return {"message": "Settings updated successfully", "changed_keys": changed_keys}


@router.get("/llm-status")
async def get_llm_status(db: AsyncSession = Depends(get_db)):
    """Inspect whether a live LLM provider is configured for Desk Officer & agents."""
    res = await db.execute(select(Settings))
    all_settings = {s.key: s.value for s in res.scalars().all()}
    llm = LLMRouter(
        provider=all_settings.get("llm_provider", "openai"),
        api_key=all_settings.get("llm_api_key", ""),
        model=all_settings.get("llm_model", "gpt-4"),
        base_url=all_settings.get("llm_base_url", ""),
    )
    return {
        "provider": llm.provider,
        "model": llm.model,
        "configured": llm.is_configured(),
        "issue": llm.configuration_issue(),
    }


@router.post("/test-llm")
async def test_llm_connection(payload: TestLLMRequest, db: AsyncSession = Depends(get_db)):
    """Verify live LLM credentials/connectivity using either submitted or stored settings."""
    res = await db.execute(select(Settings))
    all_settings = {s.key: s.value for s in res.scalars().all()}

    provider = payload.llm_provider if payload.llm_provider is not None else all_settings.get("llm_provider", "openai")
    submitted_key = payload.llm_api_key
    if submitted_key is None or str(submitted_key).startswith("••••"):
        api_key = all_settings.get("llm_api_key", "")
    else:
        api_key = submitted_key
    model = payload.llm_model if payload.llm_model is not None else all_settings.get("llm_model", "gpt-4")
    base_url = payload.llm_base_url if payload.llm_base_url is not None else all_settings.get("llm_base_url", "")

    llm = LLMRouter(provider=provider, api_key=api_key, model=model, base_url=base_url)
    started = time.perf_counter()
    try:
        resp = await llm.chat(
            messages=[{"role": "user", "content": "Reply with 'OK' and confirm you are Desk Officer ready for QA."}],
            system_prompt="You are Desk Officer in QA Officer. Reply concisely.",
            temperature=0.0,
            max_tokens=200,
        )
        latency_ms = int((time.perf_counter() - started) * 1000)
        await record_system_event(
            action="llm_connection_tested",
            summary=f"LLM connection test succeeded for {llm.provider}/{resp.model or llm.model} ({latency_ms}ms).",
            module="Settings",
            category="agent_action",
            status="success",
            level="success",
            agent_name="System",
            output_data={"provider": llm.provider, "model": resp.model or llm.model, "latency_ms": latency_ms},
            notify=False,
            notify_ui_bell=False,
        )
        return {
            "ok": True,
            "provider": llm.provider,
            "model": resp.model or llm.model,
            "reply": (resp.content or "").strip(),
            "latency_ms": latency_ms,
            "error": None,
        }
    except Exception as e:
        latency_ms = int((time.perf_counter() - started) * 1000)
        err_msg = str(e)
        await record_system_event(
            action="llm_connection_test_failed",
            summary=f"LLM connection test FAILED for {llm.provider}/{llm.model}: {err_msg[:200]}",
            module="Settings",
            category="error",
            status="failed",
            level="error",
            agent_name="System",
            output_data={"provider": llm.provider, "model": llm.model, "error": err_msg},
            notify=False,
            notify_ui_bell=False,
        )
        return {
            "ok": False,
            "provider": llm.provider,
            "model": llm.model,
            "reply": None,
            "latency_ms": latency_ms,
            "error": err_msg,
        }


@router.post("/test-notification")
async def test_notification_channels(
    payload: Optional[TestNotificationRequest] = None,
    db: AsyncSession = Depends(get_db),
):
    """Send a live test notification over Slack and/or Email SMTP using submitted or stored settings."""
    res = await db.execute(select(Settings))
    stored = {s.key: s.value for s in res.scalars().all()}

    req = payload or TestNotificationRequest()
    overrides = req.model_dump(exclude_none=True)
    channel = str(overrides.pop("channel", "all") or "all").lower()

    # Preserve stored plaintext secrets when UI passes back masked preview placeholders
    for secret_key in config.SECRET_SETTING_KEYS:
        if secret_key in overrides and str(overrides[secret_key]).startswith("••••"):
            overrides.pop(secret_key, None)

    merged = {**config.DEFAULTS, **stored, **overrides}
    if channel == "slack":
        merged["notification_slack_enabled"] = "true"
        channels_arg = ["slack"]
    elif channel == "email":
        merged["notification_email_enabled"] = "true"
        channels_arg = ["email"]
    else:
        channels_arg = None

    notifier = NotificationService.from_settings(merged)
    delivery = await notifier.notify_event(
        title="Test Notification — QA Officer Watcher Active",
        message=(
            "QA Officer Watcher is active. Slack and Email alerts are configured to notify you "
            "on Jira updates and AI agent failures."
        ),
        level="info",
        category="notification_test",
        status="success",
        project_name="System Configuration",
        module="Notifications",
        agent_name="Watcher",
        details={"requested_channel": channel},
        force=True,
        channels=channels_arg,
    )

    channels_sent = delivery.get("channels_sent") or []
    ok = len(channels_sent) > 0
    await _write_internal_log_no_loop(
        agent_name="Watcher",
        action="test_notification_sent",
        module="Notifications",
        category="notification_delivery",
        level="success" if ok else "error",
        status="success" if ok else "failed",
        input_data={"channel": channel},
        output_data=delivery,
        notified_channels=",".join(["ws"] + channels_sent),
    )
    return {
        "ok": ok,
        "channel": channel,
        **delivery,
    }
