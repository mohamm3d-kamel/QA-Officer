"""Browser Engine - Playwright-based browser automation.

Provides browser automation for the Detective, Coroner, and Sanity Tester agents.
Supports Chromium, Firefox, and WebKit.
"""
import asyncio
import logging
import uuid
from pathlib import Path
from typing import Optional
from datetime import datetime

logger = logging.getLogger(__name__)

EVIDENCE_DIR = Path(__file__).parent.parent / "storage" / "evidence"


class BrowserEngine:
    """Manages Playwright browser instances for agent automation."""

    def __init__(
        self,
        headless: bool = True,
        browsers: list[str] = None,
    ):
        self.headless = headless
        self.browsers = browsers or ["chromium"]
        self._playwright = None
        self._browser = None
        self._context = None

    async def start(self, record_video: bool = False) -> "BrowserSession":
        """Start a new browser session."""
        from playwright.async_api import async_playwright

        self._playwright = await async_playwright().start()

        # Select browser
        browser_type = self.browsers[0] if self.browsers else "chromium"
        launcher = getattr(self._playwright, browser_type, self._playwright.chromium)

        self._browser = await launcher.launch(headless=self.headless)

        context_opts = {
            "viewport": {"width": 1920, "height": 1080},
        }

        if record_video:
            video_dir = str(EVIDENCE_DIR / "videos")
            Path(video_dir).mkdir(parents=True, exist_ok=True)
            context_opts["record_video_dir"] = video_dir
            context_opts["record_video_size"] = {"width": 1920, "height": 1080}

        self._context = await self._browser.new_context(**context_opts)
        page = await self._context.new_page()

        return BrowserSession(page, self._context, self, record_video)

    async def stop(self):
        """Clean up browser resources."""
        try:
            if self._context:
                await self._context.close()
            if self._browser:
                await self._browser.close()
            if self._playwright:
                await self._playwright.stop()
        except Exception as e:
            logger.error(f"Error closing browser: {e}")


class BrowserSession:
    """A browser session for agent use."""

    def __init__(self, page, context, engine: BrowserEngine, recording: bool = False):
        self.page = page
        self.context = context
        self.engine = engine
        self.recording = recording
        self.screenshots = []
        self.video_path = None

    async def navigate(self, url: str, wait_until: str = "networkidle"):
        """Navigate to a URL."""
        try:
            await self.page.goto(url, wait_until=wait_until, timeout=30000)
            logger.info(f"Navigated to: {url}")
        except Exception as e:
            logger.warning(f"Navigation to {url} had issues: {e}")
            # Still proceed - page might have partially loaded

    async def take_screenshot(self, description: str = "") -> str:
        """Take a screenshot and save it."""
        filename = f"screenshot_{uuid.uuid4().hex[:8]}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
        filepath = EVIDENCE_DIR / filename
        filepath.parent.mkdir(parents=True, exist_ok=True)

        await self.page.screenshot(path=str(filepath), full_page=True)
        self.screenshots.append({"path": str(filepath), "description": description})
        logger.info(f"Screenshot saved: {filepath}")
        return str(filepath)

    async def click(self, selector: str):
        """Click an element."""
        await self.page.click(selector, timeout=10000)

    async def fill(self, selector: str, value: str):
        """Fill an input field."""
        await self.page.fill(selector, value)

    async def get_text(self, selector: str) -> str:
        """Get text content of an element."""
        element = await self.page.query_selector(selector)
        if element:
            return await element.text_content()
        return ""

    async def get_page_content(self) -> str:
        """Get the full page text content."""
        return await self.page.content()

    async def get_console_errors(self) -> list[str]:
        """Get console errors from the page."""
        errors = []
        self.page.on("console", lambda msg: errors.append(msg.text) if msg.type == "error" else None)
        await asyncio.sleep(2)  # Wait for any console messages
        return errors

    async def get_network_errors(self) -> list[dict]:
        """Monitor for failed network requests."""
        failures = []

        def on_response(response):
            if response.status >= 400:
                failures.append({
                    "url": response.url,
                    "status": response.status,
                    "method": response.request.method,
                })

        self.page.on("response", on_response)
        await asyncio.sleep(2)
        return failures

    async def execute_steps(self, steps: list[dict]) -> list[dict]:
        """Execute a sequence of browser steps.
        
        Each step is a dict like:
        {"action": "navigate", "url": "..."} 
        {"action": "click", "selector": "..."}
        {"action": "fill", "selector": "...", "value": "..."}
        {"action": "screenshot", "description": "..."}
        {"action": "wait", "seconds": 2}
        """
        results = []
        for step in steps:
            action = step.get("action")
            try:
                if action == "navigate":
                    await self.navigate(step["url"])
                    results.append({"action": action, "status": "success", "url": step["url"]})
                elif action == "click":
                    await self.click(step["selector"])
                    results.append({"action": action, "status": "success", "selector": step["selector"]})
                elif action == "fill":
                    await self.fill(step["selector"], step["value"])
                    results.append({"action": action, "status": "success"})
                elif action == "screenshot":
                    path = await self.take_screenshot(step.get("description", ""))
                    results.append({"action": action, "status": "success", "path": path})
                elif action == "wait":
                    await asyncio.sleep(step.get("seconds", 1))
                    results.append({"action": action, "status": "success"})
                elif action == "get_text":
                    text = await self.get_text(step["selector"])
                    results.append({"action": action, "status": "success", "text": text})
                else:
                    results.append({"action": action, "status": "skipped", "reason": "unknown action"})
            except Exception as e:
                results.append({"action": action, "status": "error", "error": str(e)})
                logger.error(f"Step '{action}' failed: {e}")
        return results

    async def close(self) -> Optional[str]:
        """Close the session and return video path if recording."""
        video_path = None
        if self.recording and self.page.video:
            try:
                video_path = await self.page.video.path()
                self.video_path = str(video_path)
            except Exception as e:
                logger.warning(f"Could not get video path: {e}")
        
        await self.engine.stop()
        return self.video_path

