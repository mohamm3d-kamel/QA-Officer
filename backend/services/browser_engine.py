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

    async def start(self, record_video: bool = False, browser_name: Optional[str] = None) -> "BrowserSession":
        """Start a new browser session with upfront console/network listeners and cleanup safety."""
        from playwright.async_api import async_playwright

        self._playwright = await async_playwright().start()
        try:
            browser_type = browser_name or (self.browsers[0] if self.browsers else "chromium")
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
            return BrowserSession(page, self._context, self, record_video, browser_type=browser_type)
        except Exception:
            await self.stop()
            raise

    async def close_context(self):
        """Close only the browser context so Playwright flushes recorded videos before stop()."""
        if self._context:
            try:
                await self._context.close()
            except Exception as e:
                logger.error(f"Error closing browser context: {e}")
            finally:
                self._context = None

    async def stop(self):
        """Clean up browser resources reliably without leaking subprocess transports."""
        try:
            await self.close_context()
            if self._browser:
                try:
                    await self._browser.close()
                finally:
                    self._browser = None
        except Exception as e:
            logger.error(f"Error closing browser: {e}")
        finally:
            if self._playwright:
                try:
                    await self._playwright.stop()
                except Exception as e:
                    logger.error(f"Error stopping Playwright: {e}")
                finally:
                    self._playwright = None


class BrowserSession:
    """A browser session for agent use with continuous console/network capture."""

    def __init__(self, page, context, engine: BrowserEngine, recording: bool = False, browser_type: str = "chromium"):
        self.page = page
        self.context = context
        self.engine = engine
        self.recording = recording
        self.browser_type = browser_type
        self.screenshots = []
        self.video_path = None
        self.console_errors: list[str] = []
        self.page_errors: list[str] = []
        self.network_errors: list[dict] = []

        self.page.on(
            "console",
            lambda msg: self.console_errors.append(msg.text[:300])
            if msg.type == "error" and len(self.console_errors) < 50
            else None,
        )
        self.page.on(
            "pageerror",
            lambda exc: self.page_errors.append(str(exc)[:300])
            if len(self.page_errors) < 50
            else None,
        )
        self.page.on(
            "response",
            lambda resp: self.network_errors.append({
                "url": resp.url[:300],
                "status": resp.status,
                "method": resp.request.method,
            })
            if resp.status >= 400 and len(self.network_errors) < 50
            else None,
        )

    async def navigate(self, url: str, wait_until: str = "domcontentloaded") -> Optional[int]:
        """Navigate to a URL and return HTTP status code if available."""
        try:
            response = await self.page.goto(url, wait_until=wait_until, timeout=20000)
            logger.info(f"Navigated ({self.browser_type}) to: {url}")
            return response.status if response else None
        except Exception as e:
            logger.warning(f"Navigation to {url} had issues: {e}")
            return None

    async def inspect_page(self, max_elements: int = 35) -> dict:
        """Capture DOM structure, interactive selectors, and runtime console/network telemetry."""
        title = ""
        elements = []
        try:
            title = await self.page.title()
            elements = await self.page.evaluate(
                """(limit) => {
                    const els = Array.from(document.querySelectorAll('a, button, input, select, textarea, [role="button"], form'));
                    return els.slice(0, limit).map(el => ({
                        tag: el.tagName.toLowerCase(),
                        id: el.id || null,
                        name: el.getAttribute('name') || null,
                        type: el.getAttribute('type') || null,
                        placeholder: el.getAttribute('placeholder') || null,
                        text: (el.innerText || el.value || el.getAttribute('aria-label') || '').trim().slice(0, 60),
                        selector: el.id ? '#' + el.id : (el.getAttribute('data-testid') ? `[data-testid="${el.getAttribute('data-testid')}"]` : (el.getAttribute('name') ? `${el.tagName.toLowerCase()}[name="${el.getAttribute('name')}"]` : el.tagName.toLowerCase()))
                    }));
                }""",
                max_elements,
            )
        except Exception as e:
            logger.warning(f"Page DOM inspection warning: {e}")

        return {
            "url": self.page.url,
            "browser": self.browser_type,
            "title": title,
            "interactive_elements": elements,
            "console_errors": self.console_errors[:20],
            "page_errors": self.page_errors[:20],
            "network_errors": self.network_errors[:20],
        }

    async def take_screenshot(self, description: str = "") -> str:
        """Take a screenshot and save it."""
        filename = f"screenshot_{self.browser_type}_{uuid.uuid4().hex[:8]}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
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
        """Return console errors accumulated during the session."""
        return list(self.console_errors)

    async def get_network_errors(self) -> list[dict]:
        """Return failed (>=400) network requests accumulated during the session."""
        return list(self.network_errors)

    async def execute_steps(self, steps: list[dict]) -> list[dict]:
        """Execute a sequence of browser steps."""
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
                    await self.fill(step["selector"], step.get("value", ""))
                    results.append({"action": action, "status": "success"})
                elif action == "screenshot":
                    path = await self.take_screenshot(step.get("description", ""))
                    results.append({"action": action, "status": "success", "path": path})
                elif action == "wait":
                    await asyncio.sleep(min(float(step.get("seconds", 1)), 10.0))
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
        """Close the context so video flushes cleanly, resolve its path, then stop engine."""
        video_obj = self.page.video if (self.recording and self.page) else None
        try:
            await self.engine.close_context()
            if video_obj is not None:
                try:
                    raw_path = await video_obj.path()
                    self.video_path = str(raw_path) if raw_path else None
                except Exception as e:
                    logger.warning(f"Could not finalize video path: {e}")
        finally:
            await self.engine.stop()
        return self.video_path

