"""QA Officer - FastAPI Application Main Entry Point."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent / "site-packages"))

import asyncio
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
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
    """Background polling loop for system-wide Watcher sentinel."""
    logger.info("Watcher Background Worker started.")
    from agents.detective import DetectiveAgent
    from agents.coroner import CoronerAgent
    from agents.watcher import WatcherAgent
    from services.llm_router import LLMRouter
    from services.notification_service import NotificationService
    from services.watcher_bus import record_system_event

    while True:
        try:
            async with async_session() as session:
                res = await session.execute(select(Settings))
                settings_dict = {s.key: s.value for s in res.scalars().all()}

            interval = int(settings_dict.get("watcher_interval_minutes", "60") or 60)
            enabled = settings_dict.get("watcher_enabled", "true") == "true"

            if enabled:
                llm = LLMRouter(
                    provider=settings_dict.get("llm_provider", "openai"),
                    api_key=settings_dict.get("llm_api_key", ""),
                    model=settings_dict.get("llm_model", "gpt-4"),
                    base_url=settings_dict.get("llm_base_url", ""),
                )
                detective = DetectiveAgent(llm)
                coroner = CoronerAgent()
                notifier = NotificationService.from_settings(settings_dict)
                watcher = WatcherAgent(detective, coroner, notifier)

                logger.info(f"Watcher running system-wide sweep (interval: {interval}m)...")
                await watcher.poll_all_active_projects(settings_dict)

            await asyncio.sleep(max(60, interval * 60))
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Watcher background worker loop error: {e}")
            try:
                await record_system_event(
                    action="watcher_worker_error",
                    summary=f"Watcher background loop encountered an error: {e}",
                    module="Watcher Monitor",
                    category="error",
                    status="failed",
                    level="error",
                    agent_name="Watcher",
                    output_data={"error": str(e)},
                )
            except Exception:
                pass
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
            default_report_format = (
                "# Bug Report: [Title]\n\n"
                "## 1. Summary\n[Summary of the defect]\n\n"
                "## 2. Root Cause (Judge Confirmed)\n[Verified Root Cause]\n\n"
                "## 3. Steps to Reproduce\n1. [Step 1]\n2. [Step 2]\n\n"
                "## 4. Expected vs Actual\n**Expected:** ...\n**Actual:** ...\n\n"
                "## 5. Visual Evidence\n[Attached screenshot / video recording]"
            )
            default_proj = Project(
                name="E-Commerce Core Platform",
                jira_project_key="QA",
                jira_base_url="https://company.atlassian.net",
                app_url="https://example.com",
                repo_url="https://github.com/company/ecommerce-core",
                description="Primary web store and checkout service under automated QA supervision.",
                report_format=default_report_format,
            )
            session.add(default_proj)
            await session.flush()

            # Seed default skills
            session.add(Skill(
                project_id=default_proj.id,
                type="report_format",
                title="Default QA Bug Report Format",
                content=default_report_format,
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

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def watcher_unhandled_error_middleware(request: Request, call_next):
    """Capture unhandled 500 server errors across all modules via Watcher telemetry."""
    try:
        response = await call_next(request)
        if response.status_code >= 500 and not response.headers.get("x-watcher-logged") and "raise-jira" not in request.url.path:
            try:
                from services.watcher_bus import record_system_event
                await record_system_event(
                    action="api_server_error",
                    summary=f"{request.method} {request.url.path} returned HTTP {response.status_code}.",
                    module="API / System",
                    category="error",
                    status="failed",
                    level="error",
                    agent_name="Watcher",
                    input_data={"method": request.method, "path": request.url.path},
                    output_data={"status_code": response.status_code},
                )
            except Exception:
                pass
        return response
    except Exception as exc:
        logger.exception(f"Unhandled exception on {request.method} {request.url.path}: {exc}")
        try:
            from services.watcher_bus import record_system_event
            await record_system_event(
                action="unhandled_api_exception",
                summary=f"Unhandled error in {request.method} {request.url.path}: {str(exc)[:200]}",
                module="API / System",
                category="error",
                status="failed",
                level="error",
                agent_name="Watcher",
                input_data={"method": request.method, "path": request.url.path},
                output_data={"error": str(exc)},
            )
        except Exception:
            pass
        return JSONResponse(status_code=500, content={"detail": str(exc)})


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
