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

    async def auto_login(
        self,
        username: str,
        password: str,
        role: str = "",
        timeout_ms: int = 10000,
    ) -> dict:
        """Detect a visible login form, fill credentials, submit, and return sanitized status."""
        if not username or not password:
            return {
                "attempted": False,
                "authenticated": False,
                "role": role,
                "username": username,
                "final_url": self.page.url,
                "reason": "missing_credentials",
            }

        before_url = self.page.url
        wait_form_ms = 3500 if any(k in before_url.lower() for k in ("login", "signin", "auth")) else 1200
        try:
            await self.page.wait_for_selector('input[type="password"]', state="visible", timeout=wait_form_ms)
        except Exception:
            pass

        pw_locator = None
        try:
            pw_candidates = self.page.locator('input[type="password"]')
            cnt = await pw_candidates.count()
            for i in range(cnt):
                cand = pw_candidates.nth(i)
                if await cand.is_visible():
                    pw_locator = cand
                    break
        except Exception:
            pw_locator = None

        if pw_locator is None:
            return {
                "attempted": False,
                "authenticated": "login" not in self.page.url.lower(),
                "role": role,
                "username": username,
                "final_url": self.page.url,
                "reason": "no_login_form",
            }

        user_selectors = [
            'input[name="email"]',
            'input[type="email"]',
            'input[name*="email" i]',
            'input[id*="email" i]',
            'input[name*="user" i]',
            'input[id*="user" i]',
            'input[autocomplete="email"]',
            'input[autocomplete="username"]',
            'input[type="text"]',
        ]
        user_locator = None
        for sel in user_selectors:
            try:
                loc = self.page.locator(sel)
                cnt = await loc.count()
                for i in range(cnt):
                    cand = loc.nth(i)
                    if await cand.is_visible():
                        user_locator = cand
                        break
                if user_locator is not None:
                    break
            except Exception:
                continue

        if user_locator is None:
            return {
                "attempted": False,
                "authenticated": False,
                "role": role,
                "username": username,
                "final_url": self.page.url,
                "reason": "username_input_not_found",
            }

        error_count_before = len(self.network_errors)
        try:
            await user_locator.fill(username)
            await pw_locator.fill(password)

            submit_selectors = [
                'button[type="submit"]',
                'input[type="submit"]',
                'button:has-text("Login")',
                'button:has-text("Log in")',
                'button:has-text("Sign in")',
                'button:has-text("Submit")',
            ]
            submit_btn = None
            for sel in submit_selectors:
                try:
                    loc = self.page.locator(sel)
                    cnt = await loc.count()
                    for i in range(cnt):
                        cand = loc.nth(i)
                        if await cand.is_visible():
                            submit_btn = cand
                            break
                    if submit_btn is not None:
                        break
                except Exception:
                    continue

            if submit_btn is not None:
                await submit_btn.click(timeout=8000)
            else:
                await pw_locator.press("Enter")

            authenticated = False
            steps = max(1, int(timeout_ms / 300))
            for _ in range(steps):
                await asyncio.sleep(0.3)
                cur_url = self.page.url
                pw_still_visible = False
                try:
                    pw_now = self.page.locator('input[type="password"]')
                    for i in range(await pw_now.count()):
                        if await pw_now.nth(i).is_visible():
                            pw_still_visible = True
                            break
                except Exception:
                    pw_still_visible = False

                if not pw_still_visible or (cur_url != before_url and "login" not in cur_url.lower()):
                    authenticated = True
                    await asyncio.sleep(0.8)
                    break

            error_hint = ""
            if not authenticated:
                try:
                    err_text = await self.page.evaluate(
                        """() => {
                            const els = Array.from(document.querySelectorAll('[role="alert"], .error, .alert-danger, .toast-error'));
                            for (const el of els) {
                                const txt = (el.innerText || '').trim();
                                if (txt) return txt.slice(0, 180);
                            }
                            return '';
                        }"""
                    )
                    if err_text:
                        error_hint = err_text
                except Exception:
                    pass
                if not error_hint and len(self.network_errors) > error_count_before:
                    last_err = self.network_errors[-1]
                    error_hint = f"HTTP {last_err.get('status')} on {last_err.get('url', '')[:120]}"

            logger.info(
                f"Auto-login ({role or 'default'}) on {before_url} -> {self.page.url} "
                f"(authenticated={authenticated})"
            )
            return {
                "attempted": True,
                "authenticated": authenticated,
                "role": role,
                "username": username,
                "before_url": before_url,
                "final_url": self.page.url,
                **({"error_hint": error_hint} if error_hint else {}),
            }
        except Exception as exc:
            logger.warning(f"Auto-login failed for {username}: {exc}")
            return {
                "attempted": True,
                "authenticated": False,
                "role": role,
                "username": username,
                "before_url": before_url,
                "final_url": self.page.url,
                "error_hint": str(exc)[:180],
            }

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

