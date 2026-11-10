"""QA Officer - FastAPI Application Main Entry Point."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent / "site-packages"))

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select

from database import init_db, async_session
from models.project import Project
from models.settings import Settings
from models.skill import Skill
import config

from routers.projects import router as projects_router
from routers.skills import router as skills_router
from routers.bugs import router as bugs_router
from routers.chat import router as chat_router
from routers.logs import router as logs_router
from routers.settings import router as settings_router
from routers.reports import router as reports_router
from routers.evidence import router as evidence_router
from routers.websocket import router as ws_router

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(name)s - %(message)s")
logger = logging.getLogger("QA_Officer")

# Background Watcher Polling Task
watcher_task = None


async def watcher_background_worker():
    """Background polling loop for Watcher agent."""
    logger.info("Watcher Background Worker started.")
    from agents.detective import DetectiveAgent
    from agents.coroner import CoronerAgent
    from agents.watcher import WatcherAgent
    from services.llm_router import LLMRouter
    from services.notification_service import NotificationService

    while True:
        try:
            async with async_session() as session:
                res = await session.execute(select(Settings))
                settings_dict = {s.key: s.value for s in res.scalars().all()}

            interval = int(settings_dict.get("watcher_interval_minutes", "60"))
            enabled = settings_dict.get("watcher_enabled", "true") == "true"

            if enabled:
                llm = LLMRouter(
                    provider=settings_dict.get("llm_provider", "openai"),
                    api_key=settings_dict.get("llm_api_key", ""),
                    model=settings_dict.get("llm_model", "gpt-4"),
                )
                detective = DetectiveAgent(llm)
                coroner = CoronerAgent()
                notifier = NotificationService.from_settings(settings_dict)
                watcher = WatcherAgent(detective, coroner, notifier)

                logger.info(f"Watcher polling active tickets (interval: {interval}m)...")
                await watcher.poll_all_active_projects(settings_dict)

            await asyncio.sleep(max(60, interval * 60))
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Watcher background worker loop error: {e}")
            await asyncio.sleep(60)


async def seed_initial_data():
    """Seed initial project and default settings if empty."""
    async with async_session() as session:
        # Check settings
        res = await session.execute(select(Settings))
        existing_keys = {s.key for s in res.scalars().all()}
        for k, v in config.DEFAULTS.items():
            if k not in existing_keys:
                session.add(Settings(key=k, value=v))

        # Check projects
        res_proj = await session.execute(select(Project))
        proj = res_proj.scalars().first()
        if not proj:
            default_proj = Project(
                name="E-Commerce Core Platform",
                jira_project_key="QA",
                jira_base_url="https://company.atlassian.net",
                app_url="https://example.com",
                repo_url="https://github.com/company/ecommerce-core",
                description="Primary web store and checkout service under automated QA supervision.",
            )
            session.add(default_proj)
            await session.flush()

            # Seed default skills
            session.add(Skill(
                project_id=default_proj.id,
                type="report_format",
                title="Default QA Bug Report Format",
                content=(
                    "# Bug Report: [Title]\n\n"
                    "## 1. Summary\n[Summary of the defect]\n\n"
                    "## 2. Root Cause (Judge Confirmed)\n[Verified Root Cause]\n\n"
                    "## 3. Steps to Reproduce\n1. [Step 1]\n2. [Step 2]\n\n"
                    "## 4. Expected vs Actual\n**Expected:** ...\n**Actual:** ...\n\n"
                    "## 5. Visual Evidence\n[Attached screenshot / video recording]"
                ),
            ))
            session.add(Skill(
                project_id=default_proj.id,
                type="requirement",
                title="Checkout Business Logic",
                content=(
                    "Requirements for Checkout Module:\n"
                    "- Cart total must update dynamically when a voucher is applied.\n"
                    "- Negative or 0 quantities must not be accepted in checkout.\n"
                    "- Payment gateway webhook must confirm order status before redirecting to confirmation page."
                ),
            ))

        await session.commit()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("Initializing QA Officer Database & Services...")
    await init_db()
    await seed_initial_data()
    global watcher_task
    watcher_task = asyncio.create_task(watcher_background_worker())
    yield
    # Shutdown
    if watcher_task:
        watcher_task.cancel()
        try:
            await watcher_task
        except asyncio.CancelledError:
            pass


app = FastAPI(
    title="QA Officer API",
    description="AI-Powered Software Testing Assistant Multi-Agent Platform",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS (allow localhost and private LAN origins, plus optional env override)
extra_origins = [o.strip() for o in os.environ.get("QA_OFFICER_CORS_ORIGINS", "").split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=extra_origins,
    allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1|0\.0\.0\.0|192\.168\.\d+\.\d+|10\.\d+\.\d+\.\d+|172\.(1[6-9]|2\d|3[01])\.\d+\.\d+)(:\d+)?$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Static storage for evidence files
app.mount("/evidence_files", StaticFiles(directory=str(config.EVIDENCE_DIR)), name="evidence_files")

# Include Routers
app.include_router(projects_router)
app.include_router(skills_router)
app.include_router(bugs_router)
app.include_router(chat_router)
app.include_router(logs_router)
app.include_router(settings_router)
app.include_router(reports_router)
app.include_router(evidence_router)
app.include_router(ws_router)


@app.get("/api/health")
async def health_check():
    return {"status": "ok", "app": "QA Officer", "version": "1.0.0"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=False)
