"""QA Officer - Configuration Management"""
import os
import json
from pathlib import Path
from typing import Optional

# Base paths
BASE_DIR = Path(__file__).parent
STORAGE_DIR = BASE_DIR / "storage"
EVIDENCE_DIR = STORAGE_DIR / "evidence"
CHAT_UPLOADS_DIR = STORAGE_DIR / "chat_uploads"
DB_PATH = Path(os.environ["QA_OFFICER_DB_PATH"]) if os.environ.get("QA_OFFICER_DB_PATH") else (STORAGE_DIR / "qa_officer.db")

# Ensure directories exist
STORAGE_DIR.mkdir(exist_ok=True)
EVIDENCE_DIR.mkdir(exist_ok=True)
CHAT_UPLOADS_DIR.mkdir(exist_ok=True)

# Default settings
DEFAULTS = {
    "llm_provider": "openai",
    "llm_api_key": "",
    "llm_model": "gpt-4",
    "llm_base_url": "",
    "jira_base_url": "",
    "jira_email": "",
    "jira_api_token": "",
    "default_reporter": "Mohamed Kamel",
    "watcher_interval_minutes": "60",
    "watcher_enabled": "true",
    "watcher_monitor_modules": "true",
    "watcher_monitor_agents": "true",
    "watcher_monitor_logs": "true",
    "watcher_last_sweep_at": "",
    "watcher_jira_issue_snapshot": "",
    "notification_email_enabled": "false",
    "notification_email": "",
    "smtp_host": "",
    "smtp_port": "587",
    "smtp_username": "",
    "smtp_password": "",
    "smtp_from_email": "",
    "smtp_from": "",
    "smtp_use_tls": "true",
    "smtp_use_ssl": "false",
    "notification_slack_enabled": "false",
    "notification_slack_webhook": "",
    "notify_on_all_events": "false",
    "notify_on_changes": "true",
    "notify_on_success": "false",
    "notify_on_failures": "true",
    "browser_chromium": "true",
    "browser_firefox": "true",
    "browser_webkit": "true",
    "browser_headless": "true",
}

SECRET_SETTING_KEYS = {
    "llm_api_key",
    "jira_api_token",
    "notification_slack_webhook",
    "smtp_password",
}


def mask_secret(value: str) -> str:
    """Return masked preview for API responses without leaking raw secrets."""
    if not value:
        return ""
    if len(value) <= 4:
        return "••••••••"
    return f"••••••••{value[-4:]}"


def get_enabled_browsers(settings: dict) -> list[str]:
    """Extract enabled Playwright browser engines from settings dict."""
    browsers = []
    if str(settings.get("browser_chromium", "true")).lower() == "true":
        browsers.append("chromium")
    if str(settings.get("browser_firefox", "false")).lower() == "true":
        browsers.append("firefox")
    if str(settings.get("browser_webkit", "false")).lower() == "true":
        browsers.append("webkit")
    return browsers or ["chromium"]


DATABASE_URL = f"sqlite+aiosqlite:///{DB_PATH}"
