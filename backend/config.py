"""QA Officer - Configuration Management"""
import os
import json
from pathlib import Path
from typing import Optional

# Base paths
BASE_DIR = Path(__file__).parent
STORAGE_DIR = BASE_DIR / "storage"
EVIDENCE_DIR = STORAGE_DIR / "evidence"
DB_PATH = Path(os.environ["QA_OFFICER_DB_PATH"]) if os.environ.get("QA_OFFICER_DB_PATH") else (STORAGE_DIR / "qa_officer.db")

# Ensure directories exist
STORAGE_DIR.mkdir(exist_ok=True)
EVIDENCE_DIR.mkdir(exist_ok=True)

# Default settings
DEFAULTS = {
    "llm_provider": "openai",
    "llm_api_key": "",
    "llm_model": "gpt-4",
    "jira_base_url": "",
    "jira_email": "",
    "jira_api_token": "",
    "default_reporter": "Mohamed Kamel",
    "watcher_interval_minutes": "60",
    "watcher_enabled": "true",
    "notification_email_enabled": "false",
    "notification_email": "",
    "notification_slack_enabled": "false",
    "notification_slack_webhook": "",
    "browser_chromium": "true",
    "browser_firefox": "true",
    "browser_webkit": "true",
    "browser_headless": "true",
}

DATABASE_URL = f"sqlite+aiosqlite:///{DB_PATH}"

