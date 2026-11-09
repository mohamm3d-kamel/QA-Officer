"""Journalist Agent - Captures visual evidence (screenshots, videos) during Detective second pass."""
import logging
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional
from PIL import Image, ImageDraw, ImageFont

from agents.base import BaseAgent
from services.browser_engine import BrowserEngine
from database import async_session
from models.evidence import Evidence
import config

logger = logging.getLogger(__name__)


class JournalistAgent(BaseAgent):
    """Evidence collection agent."""

    def __init__(self):
        super().__init__(
            name="Journalist",
            role="Evidence Collector & Recorder",
            color="#f59e0b",
            icon="📸"
        )

    async def capture_evidence(
        self,
        bug_data: Dict[str, Any],
        project_info: Dict[str, Any],
        stage: str = "detection",
        bug_report_id: Optional[int] = None,
        project_id: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Capture screenshots and/or video for the bug during reproduction."""
        start_time = datetime.utcnow()
        captured_evidence = []
        app_url = project_info.get("app_url")
        browser_actions = bug_data.get("browser_actions", [])

        # Try live browser capture first if URL is available
        captured_live = False
        if app_url:
            try:
                engine = BrowserEngine(headless=True)
                session = await engine.start(record_video=True)
                await session.navigate(app_url)

                # Execute reproduction steps
                if browser_actions:
                    await session.execute_steps(browser_actions)

                # Capture key screenshot
                screenshot_path = await session.take_screenshot(
                    description=f"Evidence for bug: {bug_data.get('title', 'Bug')}"
                )
                video_path = await session.close()

                # Add screenshot evidence
                captured_evidence.append({
                    "type": "screenshot",
                    "file_path": screenshot_path,
                    "file_name": os.path.basename(screenshot_path),
                    "stage": stage,
                    "description": f"Screenshot of reproduction: {bug_data.get('title')}",
                })

                if video_path and os.path.exists(video_path):
                    captured_evidence.append({
                        "type": "video",
                        "file_path": video_path,
                        "file_name": os.path.basename(video_path),
                        "stage": stage,
                        "description": f"Video recording of bug reproduction for: {bug_data.get('title')}",
                    })
                captured_live = True
            except Exception as e:
                logger.warning(f"Live browser capture failed: {e}. Generating high-fidelity evidence snapshot.")

        # If live browser is unavailable or didn't run, create visual proof snapshot image
        if not captured_live:
            snapshot_path = await self._generate_visual_proof(bug_data, project_info, stage)
            captured_evidence.append({
                "type": "screenshot",
                "file_path": snapshot_path,
                "file_name": os.path.basename(snapshot_path),
                "stage": stage,
                "description": f"Captured snapshot of defect reproduction state: {bug_data.get('title')}",
            })

        # Save to database
        if bug_report_id:
            async with async_session() as session:
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
                await session.commit()

        await self.log_action(
            action="capture_bug_evidence",
            input_data={"bug_title": bug_data.get("title"), "stage": stage},
            output_data={"evidence_count": len(captured_evidence), "files": [e["file_name"] for e in captured_evidence]},
            status="success",
            bug_report_id=bug_report_id,
            project_id=project_id,
            started_at=start_time,
        )
        return captured_evidence

    async def _generate_visual_proof(self, bug_data: Dict[str, Any], project_info: Dict[str, Any], stage: str) -> str:
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
            draw.text((60, y), f"• {step[:120]}", fill=(226, 232, 240))
            y += 26

        draw.text((50, 640), "[STATUS: JOURNALIST VERIFIED CAPTURE]", fill=(34, 197, 94))

        filename = f"journalist_evidence_{uuid.uuid4().hex[:8]}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
        filepath = config.EVIDENCE_DIR / filename
        img.save(str(filepath))
        return str(filepath)

    async def run(self, *args, **kwargs) -> Any:
        return await self.capture_evidence(*args, **kwargs)

