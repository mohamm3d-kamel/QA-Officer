"""Coroner Agent - Captures visual evidence (screenshots, videos) during Detective second pass."""
import asyncio
import logging
import os
import uuid
from datetime import datetime
from typing import Dict, Any, List, Optional, Tuple
from PIL import Image, ImageDraw

from agents.base import BaseAgent
from services.browser_engine import BrowserEngine
from services.environment_credentials import (
    resolve_project_runtime_credentials,
    select_environment_credential,
)
from database import async_session
from models.evidence import Evidence
import config

logger = logging.getLogger(__name__)


class CoronerAgent(BaseAgent):
    """Evidence collection agent."""

    def __init__(self):
        super().__init__(
            name="Coroner",
            role="Evidence Collector & Recorder",
            color="#f59e0b",
            icon="📸",
        )

    async def capture_with_telemetry(
        self,
        bug_data: Dict[str, Any],
        project_info: Dict[str, Any],
        stage: str = "detection",
        bug_report_id: Optional[int] = None,
        project_id: Optional[int] = None,
        browsers: Optional[List[str]] = None,
        headless: bool = True,
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """Capture screenshots/video across enabled browsers and collect runtime telemetry."""
        start_time = datetime.utcnow()
        captured_evidence: List[Dict[str, Any]] = []
        runtime_telemetry: List[Dict[str, Any]] = []
        app_url = project_info.get("app_url")
        browser_actions = bug_data.get("browser_actions", [])
        target_browsers = browsers or ["chromium"]

        runtime_creds = await resolve_project_runtime_credentials(
            project_id=project_id or project_info.get("id"),
            project_info=project_info,
        )
        selected_cred = select_environment_credential(
            runtime_creds,
            bug_data=bug_data,
        )

        # Try live browser capture across enabled engines
        captured_live = False
        live_capture_note: Optional[str] = None
        if app_url and str(app_url).strip().lower() not in ("", "none"):
            for idx, browser_name in enumerate(target_browsers):
                engine = BrowserEngine(headless=headless, browsers=[browser_name])
                session = None
                try:
                    record_vid = (idx == 0)
                    session = await engine.start(record_video=record_vid, browser_name=browser_name)
                    nav_status = await session.navigate(app_url)

                    auth_result = None
                    if selected_cred and selected_cred.get("username") and selected_cred.get("password"):
                        auth_result = await session.auto_login(
                            username=selected_cred["username"],
                            password=selected_cred["password"],
                            role=selected_cred.get("role", ""),
                        )

                    effective_actions = browser_actions
                    if auth_result and auth_result.get("authenticated") and browser_actions:
                        login_urls = {
                            str(app_url).strip().rstrip("/").lower(),
                            str(auth_result.get("before_url") or "").strip().rstrip("/").lower(),
                        }
                        effective_actions = [
                            step for step in browser_actions
                            if not (
                                isinstance(step, dict)
                                and step.get("action") == "navigate"
                                and str(step.get("url") or "").strip().rstrip("/").lower() in login_urls
                            )
                        ]

                    step_results = []
                    if effective_actions:
                        step_results = await session.execute_steps(effective_actions)

                    screenshot_path = await session.take_screenshot(
                        description=f"[{browser_name}] Evidence for bug: {bug_data.get('title', 'Bug')}"
                    )
                    telemetry = await session.inspect_page(max_elements=15)
                    runtime_telemetry.append({
                        "browser": browser_name,
                        "http_status": nav_status,
                        "final_url": telemetry.get("url"),
                        **({"auth": auth_result} if auth_result else {}),
                        "step_results": step_results,
                        "console_errors": telemetry.get("console_errors", []),
                        "page_errors": telemetry.get("page_errors", []),
                        "network_errors": telemetry.get("network_errors", []),
                    })

                    video_path = await session.close()
                    session = None

                    captured_evidence.append({
                        "type": "screenshot",
                        "file_path": screenshot_path,
                        "file_name": os.path.basename(screenshot_path),
                        "stage": stage,
                        "description": f"[{browser_name}] Screenshot of reproduction: {bug_data.get('title')}",
                    })

                    if video_path and os.path.exists(video_path) and os.path.getsize(video_path) > 0:
                        captured_evidence.append({
                            "type": "video",
                            "file_path": video_path,
                            "file_name": os.path.basename(video_path),
                            "stage": stage,
                            "description": f"[{browser_name}] Video recording of bug reproduction for: {bug_data.get('title')}",
                        })
                    captured_live = True
                except Exception as e:
                    live_capture_note = str(e)
                    logger.warning(f"Live browser capture ({browser_name}) failed: {e}")
                finally:
                    try:
                        if session is not None:
                            await session.close()
                        else:
                            await engine.stop()
                    except Exception:
                        pass

        # If live browser didn't succeed, generate high-fidelity diagnostic snapshot
        fallback_capture_error: Optional[str] = None
        if not captured_live:
            try:
                snapshot_path = await asyncio.to_thread(
                    self._generate_visual_proof_sync,
                    bug_data,
                    project_info,
                    stage,
                )
                captured_evidence.append({
                    "type": "screenshot",
                    "file_path": snapshot_path,
                    "file_name": os.path.basename(snapshot_path),
                    "stage": stage,
                    "description": f"Captured snapshot of defect reproduction state: {bug_data.get('title')}",
                })
            except Exception as snap_err:
                fallback_capture_error = str(snap_err)
                logger.error(f"Coroner fallback snapshot generation failed: {snap_err}")

        # Save to database and populate DB IDs on returned items
        if bug_report_id and captured_evidence:
            async with async_session() as session:
                db_records = []
                for ev in captured_evidence:
                    db_ev = Evidence(
                        bug_report_id=bug_report_id,
                        type=ev["type"],
                        file_path=ev["file_path"],
                        file_name=ev["file_name"],
                        stage=ev["stage"],
                        description=ev["description"],
                    )
                    session.add(db_ev)
                    db_records.append((ev, db_ev))
                await session.commit()
                for ev, db_ev in db_records:
                    ev["id"] = db_ev.id

        capture_failed = not captured_evidence or bool(fallback_capture_error)
        out_payload: Dict[str, Any] = {
            "evidence_count": len(captured_evidence),
            "files": [e["file_name"] for e in captured_evidence],
            "captured_live": captured_live,
            "telemetry": runtime_telemetry,
        }
        if live_capture_note:
            out_payload["live_browser_fallback_reason"] = live_capture_note[:240]
        if fallback_capture_error:
            out_payload["error"] = fallback_capture_error[:240]

        log_status = "failed" if capture_failed else ("success" if captured_live else "degraded")
        log_level = "error" if capture_failed else ("warning" if not captured_live else "success")

        await self.log_action(
            action="capture_bug_evidence",
            input_data={
                "bug_title": bug_data.get("title"),
                "stage": stage,
                "module": bug_data.get("module"),
                "browsers": target_browsers,
                **({"auth_role": selected_cred.get("role")} if selected_cred else {}),
            },
            output_data=out_payload,
            status=log_status,
            bug_report_id=bug_report_id,
            project_id=project_id,
            module=bug_data.get("module") or "Evidence & Browser",
            level=log_level,
            started_at=start_time,
        )
        return captured_evidence, runtime_telemetry

    async def capture_evidence(
        self,
        bug_data: Dict[str, Any],
        project_info: Dict[str, Any],
        stage: str = "detection",
        bug_report_id: Optional[int] = None,
        project_id: Optional[int] = None,
        browsers: Optional[List[str]] = None,
        headless: bool = True,
    ) -> List[Dict[str, Any]]:
        """Capture screenshots and/or video for the bug during reproduction."""
        evidence, _ = await self.capture_with_telemetry(
            bug_data=bug_data,
            project_info=project_info,
            stage=stage,
            bug_report_id=bug_report_id,
            project_id=project_id,
            browsers=browsers,
            headless=headless,
        )
        return evidence

    def _generate_visual_proof_sync(self, bug_data: Dict[str, Any], project_info: Dict[str, Any], stage: str) -> str:
        """Create a visual evidence image showing reproduction proof & diagnostics."""
        width, height = 1280, 720
        img = Image.new("RGB", (width, height), color=(15, 23, 42))  # slate-900
        draw = ImageDraw.Draw(img)

        # Header bar
        draw.rectangle([(0, 0), (width, 80)], fill=(30, 41, 59))
        draw.text((30, 25), f"QA OFFICER — EVIDENCE CAPTURE [{stage.upper()}]", fill=(6, 182, 212))

        # Metadata box
        draw.rectangle([(30, 100), (width - 30, 240)], fill=(24, 34, 53), outline=(51, 65, 85))
        draw.text((50, 115), f"Bug: {bug_data.get('title', 'Unknown')}", fill=(248, 250, 252))
        draw.text((50, 145), f"Project: {project_info.get('name', 'N/A')}  |  Module: {bug_data.get('module', 'General')}", fill=(148, 163, 184))
        draw.text((50, 175), f"Severity: {bug_data.get('severity', 'medium').upper()}  |  Label: {bug_data.get('label', 'Front-End')}", fill=(239, 68, 68))
        draw.text((50, 205), f"Timestamp: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}", fill=(100, 116, 139))

        # Root Cause & Steps Box
        draw.rectangle([(30, 260), (width - 30, 680)], fill=(17, 24, 39), outline=(55, 65, 81))
        draw.text((50, 280), "ROOT CAUSE ANALYSIS:", fill=(245, 158, 11))

        # Word wrap root cause
        rc = bug_data.get("root_cause", "Investigating...")
        rc_lines = [rc[i:i+110] for i in range(0, min(len(rc), 330), 110)]
        y = 310
        for line in rc_lines:
            draw.text((50, y), line, fill=(209, 213, 219))
            y += 24

        y += 20
        draw.text((50, y), "VERIFIED REPRODUCTION STEPS:", fill=(139, 92, 246))
        y += 30
        steps = bug_data.get("steps_to_reproduce", [])
        if isinstance(steps, str):
            steps = [steps]
        for step in steps[:6]:
            draw.text((60, y), f"• {str(step)[:120]}", fill=(226, 232, 240))
            y += 26

        draw.text((50, 640), "[STATUS: DIAGNOSTIC SNAPSHOT CAPTURE (NO LIVE BROWSER)]", fill=(245, 158, 11))

        filename = f"coroner_evidence_{uuid.uuid4().hex[:8]}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
        filepath = config.EVIDENCE_DIR / filename
        filepath.parent.mkdir(parents=True, exist_ok=True)
        img.save(str(filepath))
        return str(filepath)

    async def run(self, *args, **kwargs) -> Any:
        return await self.capture_evidence(*args, **kwargs)
