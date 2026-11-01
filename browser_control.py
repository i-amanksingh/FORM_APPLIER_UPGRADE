import asyncio
import os
import subprocess
import sys
import time
from typing import Optional, Dict, Any
from playwright.async_api import async_playwright, Browser, Page, Playwright

_CLOSED_MARKERS = (
    "closed",
    "target page",
    "target context",
    "target browser",
    "browser has been closed",
    "context has been closed",
)

def _is_dead_error(exc: BaseException) -> bool:
    msg = str(exc).lower()
    return any(m in msg for m in _CLOSED_MARKERS)

def _get_chrome_executable() -> str:
    candidates = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
    ]
    for c in candidates:
        if os.path.exists(c):
            return c
    return "chrome.exe"

class BrowserManager:
    def __init__(self, headless: bool = False, slow_mo: int = 500):
        self.headless = headless
        self.slow_mo = slow_mo
        self.playwright: Optional[Playwright] = None
        self.browser: Optional[Browser] = None
        self.page: Optional[Page] = None
        self.chrome_proc: Optional[subprocess.Popen] = None
        self._is_closed = False

    def session_is_alive(self) -> bool:
        try:
            if not self.browser or not self.browser.is_connected():
                return False
            if not self.page or self.page.is_closed():
                return False
            return True
        except Exception:
            return False

    async def start(self, force: bool = False) -> Dict[str, Any]:
        if self.browser and self.browser.is_connected() and not force:
            return {"status": "already_running", "message": "Browser already started"}

        if self.playwright:
            try:
                await self.playwright.stop()
            except Exception:
                pass
        self.playwright = await async_playwright().start()

        if not self.headless:
            print("[BrowserManager] Launching real Google Chrome on user desktop...")
            temp_profile = os.path.join(os.environ.get("LOCALAPPDATA", r"C:\Temp"), "Temp", "cdp_chrome_applier_profile")
            os.makedirs(temp_profile, exist_ok=True)

            # Check if CDP port 9222 is already active
            cdp_connected = False
            try:
                self.browser = await self.playwright.chromium.connect_over_cdp("http://127.0.0.1:9222")
                context = self.browser.contexts[0]
                self.page = context.pages[0] if context.pages else await context.new_page()
                cdp_connected = True
                print("[BrowserManager] Connected to existing browser via CDP on port 9222")
            except Exception:
                pass

            if not cdp_connected:
                chrome_exe = _get_chrome_executable()
                chrome_args = [
                    chrome_exe,
                    "--remote-debugging-port=9222",
                    "--remote-allow-origins=*",
                    f"--user-data-dir={temp_profile}",
                    "--start-maximized",
                    "--no-first-run",
                    "--no-default-browser-check",
                ]

                # Do NOT use si.lpDesktop = 'WinSta0\\default' as it detaches from interactive desktop on Win10/11
                try:
                    self.chrome_proc = subprocess.Popen(chrome_args)
                    await asyncio.sleep(2)
                except Exception as e:
                    print(f"[BrowserManager] Subprocess chrome launch notice: {e}")

                # Connect via CDP
                try:
                    self.browser = await self.playwright.chromium.connect_over_cdp("http://127.0.0.1:9222")
                    context = self.browser.contexts[0]
                    self.page = context.pages[0] if context.pages else await context.new_page()
                except Exception as e_cdp:
                    print(f"[BrowserManager] CDP connection fallback ({e_cdp}). Launching standard chromium...")
                    self.browser = await self.playwright.chromium.launch(
                        headless=False,
                        channel="chrome" if os.path.exists(chrome_exe) else None,
                        slow_mo=self.slow_mo,
                        args=["--start-maximized"]
                    )
                    context = self.browser.contexts[0] if self.browser.contexts else await self.browser.new_context()
                    self.page = context.pages[0] if context.pages else await context.new_page()

            if self.page:
                try:
                    await self.page.bring_to_front()
                except Exception:
                    pass
        else:
            self.browser = await self.playwright.chromium.launch(headless=True)
            self.page = await self.browser.new_page()

        self._is_closed = False
        return {
            "status": "started",
            "message": "Visible browser launched on user desktop",
        }

    async def new_page(self, force_new: bool = False) -> Page:
        if not self.browser or not self.browser.is_connected():
            await self.start(force=True)
            return self.page

        if not force_new and self.page and not self.page.is_closed():
            return self.page

        context = self.browser.contexts[0] if self.browser.contexts else await self.browser.new_context()
        self.page = await context.new_page()
        return self.page

    async def ensure_session(self) -> Page:
        try:
            if self.browser and self.browser.is_connected():
                if self.page and not self.page.is_closed():
                    return self.page
                return await self.new_page()
        except Exception:
            pass
        await self.start(force=True)
        return self.page

    async def hard_reset(self) -> None:
        try:
            if self.browser:
                await self.browser.close()
        except Exception:
            pass
        self.browser = None
        self.page = None
        if self.chrome_proc:
            try:
                self.chrome_proc.terminate()
            except Exception:
                pass
            self.chrome_proc = None
        try:
            if self.playwright:
                await self.playwright.stop()
        except Exception:
            pass
        self.playwright = None
        self._is_closed = False
        await self.start(force=True)

    async def navigate_to(self, url: str) -> Dict[str, Any]:
        for attempt in range(2):
            try:
                page = await self.ensure_session()
                await page.goto(url, wait_until="domcontentloaded", timeout=60000)
                self.page = page
                return {
                    "status": "success",
                    "url": page.url,
                    "title": await page.title(),
                    "page": page,
                }
            except Exception as e:
                if attempt == 1:
                    return {"status": "error", "error": str(e)}
                await self.hard_reset()
        return {"status": "error", "error": "navigation failed"}

    def get_current_page(self) -> Optional[Page]:
        if self.page and not self.page.is_closed():
            return self.page
        return None

    async def close(self) -> Dict[str, Any]:
        if self._is_closed:
            return {"status": "already_closed"}
        if self.page:
            self.page = None
        if self.browser:
            try:
                await self.browser.close()
            except Exception:
                pass
            self.browser = None
        if self.chrome_proc:
            try:
                self.chrome_proc.terminate()
            except Exception:
                pass
            self.chrome_proc = None
        if self.playwright:
            try:
                await self.playwright.stop()
            except Exception:
                pass
            self.playwright = None
        self._is_closed = True
        return {"status": "closed"}

    async def __aenter__(self):
        await self.start()
        await self.new_page()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()
