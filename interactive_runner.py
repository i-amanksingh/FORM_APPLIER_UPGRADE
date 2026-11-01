import asyncio
import json
import os
import sys

if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

from mcp_functions import MCPServer

SITES = [
    ("PyjamaHR", "https://jobs.pyjamahr.com/diginnovators/senior-python-developer/apply?source=LINKEDIN"),
    ("Carpl AI", "https://carpl.ai/careers/sde-1"),
    ("Ideas2IT", "https://www.ideas2it.com/careers/python-developer"),
    ("Autter.dev", "https://autter.dev/careers/backend-engineer#apply-form"),
    ("VE3 Zoho Recruit", "https://ve3.zohorecruit.in/jobs/Careers/80399000019824717/Python-Developer?source=LinkedIn-Basic&embedsource=LinkedIn%2BLimited%2BListings"),
    ("Fynd Keka", "https://fynd.keka.com/careers/jobdetails/145577?source=linkedin"),
    ("Portcast Lever", "https://jobs.lever.co/portcast/1f6381eb-03dd-451a-a8cc-2c862cec3fe3/apply?lever-source=LinkedIn"),
    ("AnantaX", "https://www.anantaxtechnologies.com/careers/nodejs-developer-jobs-rajkot-india#apply"),
]

async def main():
    target_idx = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    site_name, url = SITES[target_idx]
    
    print("=" * 75, flush=True)
    print(f"STARTING VISIBLE BROWSER TEST FOR: {site_name}", flush=True)
    print(f"URL: {url}", flush=True)
    print("=" * 75, flush=True)
    
    server = MCPServer()
    
    # 1. Start browser and open page
    print("\n[STEP 1] Launching visible Chrome browser (headless=False)...", flush=True)
    open_res = await server.open_page(url)
    print(f"[STEP 1 RESULT] Status: {open_res.get('status')}", flush=True)
    
    if server.page:
        try:
            await server.page.bring_to_front()
        except Exception:
            pass
            
    print("\n" + "#" * 60, flush=True)
    print(f">>> BROWSER IS NOW VISIBLE ON YOUR SCREEN ON: {url}", flush=True)
    print(">>> WAITING FOR CONFIRMATION VIA run_step.txt ...", flush=True)
    print("#" * 60 + "\n", flush=True)
    
    base_dir = os.path.dirname(os.path.abspath(__file__))
    flag_file = os.path.join(base_dir, "run_step.txt")
    if os.path.exists(flag_file):
        os.remove(flag_file)
        
    while True:
        if os.path.exists(flag_file):
            try:
                with open(flag_file, "r", encoding="utf-8") as f:
                    cmd = f.read().strip().lower()
                os.remove(flag_file)
                if cmd in ("continue", "go", "start", "run", "yes"):
                    print(f"[USER CONFIRMED] Starting apply_page on {site_name}...", flush=True)
                    break
            except Exception:
                pass
        await asyncio.sleep(0.5)

    # 2. Run apply_page
    print("\n[STEP 2] Running apply_page (extract -> match -> fill -> submit)...", flush=True)
    apply_res = await server.apply_page(Multiple_page=False, job_id=f"live_{site_name.lower().replace(' ', '_')}")
    print("\n[APPLY RESULT]:", flush=True)
    print(json.dumps({k: v for k, v in apply_res.items() if k != "all_filled_value"}, indent=2, default=str), flush=True)
    
    print("\n[COMPLETE] Keeping browser open for observation. Create exit_browser.txt to close.", flush=True)
    exit_file = os.path.join(base_dir, "exit_browser.txt")
    while not os.path.exists(exit_file):
        await asyncio.sleep(1)
    if os.path.exists(exit_file):
        os.remove(exit_file)
    await server.browser_manager.close()
    print("[CLOSED] Browser closed.", flush=True)

if __name__ == '__main__':
    asyncio.run(main())
