import asyncio
from playwright.async_api import async_playwright, Browser, Page, Playwright
from typing import Optional, Dict, Any
import subprocess

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


class BrowserManager:
    def __init__(self, headless: bool = False, slow_mo: int = 500):
        self.headless = headless
        self.slow_mo = slow_mo
        self.playwright: Optional[Playwright] = None
        self.browser: Optional[Browser] = None
        self.page: Optional[Page] = None
        self._is_closed = False
        self._cdp_port = 9222


    def session_is_alive(self) -> bool:
        try:
            if not self.browser or not self.browser.is_connected():
                return False
            if not self.page or self.page.is_closed():
                return False
            return True
        except Exception:
            return False

    async def cdp_available(self,port: int) -> bool:
        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.close()
            await writer.wait_closed()
            return True
        except OSError:
            return False

    async def start_browser(self,port = None,profile_no= None, restart = False):
        
        browser_path = "C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe"
        user_data_dir = "C:\Users\amank\AppData\Local\BraveSoftware\Brave-Browser\User Data"
        
        if not restart and await self.cdp_available(port):
            return port

        subprocess.run(
            ["taskkill", "/F", "/IM", "brave.exe"],
            capture_output=True,
            text=True,
        )

        await asyncio.sleep(1.5)
        
        profile_dir = f"Profile {profile_no}"
        args = [
            browser_path,
            f"--remote-debugging-port={port}",
            f"--user-data-dir={user_data_dir}",
            f"--profile-directory={profile_dir}",
            "--disable-popup-blocking",
            "--no-first-run",
            "--no-default-browser-check",
        ]        
        
        subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        
        for _ in range(40):
            if await self.cdp_available(port):
                return port
            await asyncio.sleep(0.5)        
        
    async def start(self, force: bool = False) -> Dict[str, Any]:
        if self.browser and self.browser.is_connected() and not force:
            return {"status": "already_running", "message": "Browser already started"}

        self._cdp_port = await self.start_browser(
            port=self._cdp_port,
            restart=force,
        )
        if self.playwright:
            try:
                await self.playwright.stop()
            except Exception:
                pass
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.connect_over_cdp(
            f"http://localhost:{self._cdp_port}"
        )
        self._is_closed = False
        return {
            "status": "started",
            "message": f"Browser connected on port {self._cdp_port}",
            "cdp_port": self._cdp_port,
        }

    async def new_page(self, force_new: bool = False) -> Page:
        if not self.browser or not self.browser.is_connected():
            raise RuntimeError("Browser not started or disconnected. Call start() first.")

        if not force_new and self.page and not self.page.is_closed():
            return self.page

        contexts = self.browser.contexts
        if not force_new and contexts:
            ctx = contexts[0]
            for existing in ctx.pages:
                if not existing.is_closed():
                    self.page = existing
                    return self.page
            self.page = await ctx.new_page()
            return self.page

        context = await self.browser.new_context()
        self.page = await context.new_page()
        return self.page

    async def ensure_session(self) -> Page:
        """Return a live page — recreate page, context, or full browser if needed."""
        try:
            if self.browser and self.browser.is_connected():
                if self.page and not self.page.is_closed():
                    return self.page
                return await self.new_page()
        except Exception:
            pass
        await self.hard_reset()
        return self.page

    async def hard_reset(self) -> None:
        """Kill stale CDP attachment and launch a fresh browser + page."""
        try:
            if self.browser:
                await self.browser.close()
        except Exception:
            pass
        self.browser = None
        self.page = None
        try:
            if self.playwright:
                await self.playwright.stop()
        except Exception:
            pass
        self.playwright = None
        self._is_closed = False

        await self.start(force=True)
        self.page = await self.new_page(force_new=True)

    async def navigate_to(self, url):
        
        for attempt in range(2):
            try:
                page = await self.ensure_session()
                await page.goto(url, wait_until="domcontentloaded", timeout=60000)
                self.page = page
                return {
                    "status": "success",
                    "url": page.url,
                    "title": await page.title(),
                    "page" : page
                }
            except Exception as e:
                return {"status": "error"}
        return {"status": "error", "error":"navigation failed"}

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
