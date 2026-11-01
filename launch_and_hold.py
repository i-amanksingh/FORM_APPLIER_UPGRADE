import asyncio
import json
import os
import subprocess
import sys
import time
from playwright.async_api import async_playwright

if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

from mcp_functions import MCPServer

URL = "https://jobs.pyjamahr.com/diginnovators/senior-python-developer/apply?source=LINKEDIN"

async def main():
    print("=" * 70, flush=True)
    print("STEP 1: Using subprocess to close all existing browsers...", flush=True)
    for b in ["brave.exe", "chrome.exe"]:
        subprocess.run(["taskkill", "/F", "/IM", b], capture_output=True)
    await asyncio.sleep(2)
    print("Closed existing browsers successfully.", flush=True)

    print("\nSTEP 2: Opening Brave browser via subprocess.Popen...", flush=True)
    browser_path = r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe"
    user_data_dir = r"C:\Users\amank\AppData\Local\BraveSoftware\Brave-Browser\User Data"
    port = 9222

    args = [
        browser_path,
        f"--remote-debugging-port={port}",
        f"--user-data-dir={user_data_dir}",
        "--profile-directory=Default",
        "--disable-popup-blocking",
        "--no-first-run",
        "--no-default-browser-check",
        "--start-maximized",
        URL
    ]

    proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print(f"Brave subprocess started! (PID: {proc.pid})", flush=True)

    # Wait for CDP port
    print("\nSTEP 3: Checking CDP port 9222...", flush=True)
    cdp_ready = False
    for i in range(40):
        try:
            r, w = await asyncio.open_connection("127.0.0.1", port)
            w.close()
            await w.wait_closed()
            cdp_ready = True
            print(f"CDP is active on port {port} after {i*0.5:.1f}s!", flush=True)
            break
        except OSError:
            await asyncio.sleep(0.5)

    if not cdp_ready:
        print("[ERROR] CDP port failed to respond!", flush=True)
        return

    print("\n" + "#" * 65, flush=True)
    print(f">>> BRAVE BROWSER IS NOW OPEN ON: {URL}", flush=True)
    print(">>> Browser is active on screen. Waiting for user to respond in chat...", flush=True)
    print("#" * 65 + "\n", flush=True)

    flag_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "run_step.txt")
    if os.path.exists(flag_file):
        os.remove(flag_file)

    # Keep browser alive until user creates run_step.txt or timeout
    while not os.path.exists(flag_file):
        await asyncio.sleep(1)

    print("[USER CONFIRMED] Proceeding with form filling via MCPServer...", flush=True)
    try:
        os.remove(flag_file)
    except Exception:
        pass

    server = MCPServer()
    server._initialized = True
    from browser_control import BrowserManager
    server.browser_manager = BrowserManager(headless=False)
    server.browser_manager.playwright = await async_playwright().start()
    server.browser_manager.browser = await server.browser_manager.playwright.chromium.connect_over_cdp(f"http://localhost:{port}")
    server.browser_manager._is_closed = False
    
    ctx = server.browser_manager.browser.contexts[0]
    server.page = ctx.pages[0] if ctx.pages else await ctx.new_page()
    server.current_page_url = URL
    from dom_extractor import FormFieldExtractor
    from working_fill_feilds import FormFiller
    server.dom_extractor = FormFieldExtractor(url=URL)
    server.automatic_filling = FormFiller(page=server.page, url=URL)

    print("\nRunning apply_page()...", flush=True)
    res = await server.apply_page(job_id="live_pyjamahr")
    print("\nApply Result:", flush=True)
    print(json.dumps({k: v for k, v in res.items() if k != "all_filled_value"}, indent=2, default=str), flush=True)

if __name__ == '__main__':
    asyncio.run(main())
