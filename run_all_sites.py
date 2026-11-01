import asyncio
import json
import os
import sys
import traceback

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from mcp_functions import MCPServer

SITES = [
    {
        "id": 1,
        "name": "Carpl AI",
        "url": "https://carpl.ai/careers/sde-1",
    },
    {
        "id": 2,
        "name": "Ideas2IT",
        "url": "https://www.ideas2it.com/careers/python-developer",
    },
    {
        "id": 3,
        "name": "Autter.dev",
        "url": "https://autter.dev/careers/backend-engineer#apply-form",
    },
    {
        "id": 4,
        "name": "VE3 Zoho Recruit",
        "url": "https://ve3.zohorecruit.in/jobs/Careers/80399000019824717/Python-Developer?source=LinkedIn-Basic&embedsource=LinkedIn%2BLimited%2BListings",
    },
    {
        "id": 5,
        "name": "Fynd Keka",
        "url": "https://fynd.keka.com/careers/jobdetails/145577?source=linkedin",
    },
    {
        "id": 6,
        "name": "Portcast Lever",
        "url": "https://jobs.lever.co/portcast/1f6381eb-03dd-451a-a8cc-2c862cec3fe3/apply?lever-source=LinkedIn",
    },
    {
        "id": 7,
        "name": "AnantaX",
        "url": "https://www.anantaxtechnologies.com/careers/nodejs-developer-jobs-rajkot-india#apply",
    },
    {
        "id": 8,
        "name": "PyjamaHR",
        "url": "https://jobs.pyjamahr.com/diginnovators/senior-python-developer/apply?source=LINKEDIN",
    },
]

async def run_single_site(site_info: dict):
    site_id = site_info["id"]
    site_name = site_info["name"]
    url = site_info["url"]

    print("=" * 80, flush=True)
    print(f"[{site_id}/8] TESTING SITE: {site_name}", flush=True)
    print(f"URL: {url}", flush=True)
    print("=" * 80, flush=True)

    server = MCPServer()
    try:
        print(f"\n>>> STEP 1: Calling open_page (Visible Chromium)...", flush=True)
        open_res = await server.open_page(url)
        print(f"open_page result: {open_res.get('status')}", flush=True)

        print(f"\n>>> STEP 2: Calling apply_page...", flush=True)
        apply_res = await server.apply_page(Multiple_page=False, job_id=f"site_{site_id}")
        print(f"apply_page status: {apply_res.get('status')}", flush=True)

        if apply_res.get("status") in ("need_your_response", "needs_fields_solved"):
            leftovers = apply_res.get("leftover_fields", [])
            print(f"\n>>> Resolving {len(leftovers)} leftover field(s)...", flush=True)
            for lf in leftovers:
                try:
                    await server.solve_live_leftover_tag(lf, job_id=f"site_{site_id}")
                except Exception as e_lf:
                    print(f"  Error solving tag: {e_lf}", flush=True)
            print(">>> Resuming application...", flush=True)
            apply_res = await server.apply_page(continue_post=True, job_id=f"site_{site_id}")
            print(f"Final apply_page status: {apply_res.get('status')}", flush=True)

        print(f"\n>>> Site {site_id} ({site_name}) Result: {apply_res.get('status')}", flush=True)
        if server.page and not server.page.is_closed():
            print("Holding browser open for 6 seconds for observation...", flush=True)
            await server.page.wait_for_timeout(6000)

        return {"site": site_name, "url": url, "status": apply_res.get("status"), "result": apply_res}
    except Exception as e:
        print(f"Error testing {site_name}: {e}", flush=True)
        traceback.print_exc()
        return {"site": site_name, "url": url, "status": "error", "message": str(e)}
    finally:
        try:
            if server.browser_manager:
                await server.browser_manager.close()
        except Exception:
            pass

async def main():
    if len(sys.argv) > 1:
        arg = sys.argv[1].strip()
        if arg.isdigit():
            idx = int(arg) - 1
            if 0 <= idx < len(SITES):
                sites_to_run = [SITES[idx]]
            else:
                sites_to_run = SITES
        elif arg.lower() == "all":
            sites_to_run = SITES
        else:
            sites_to_run = [s for s in SITES if arg.lower() in s["name"].lower() or arg in s["url"]] or SITES
    else:
        # Default run PyjamaHR (Site 8) or Site 1
        sites_to_run = [SITES[7]]

    results = []
    for site in sites_to_run:
        res = await run_single_site(site)
        results.append(res)

    print("\n" + "=" * 80, flush=True)
    print("EXECUTION SUMMARY", flush=True)
    print("=" * 80, flush=True)
    for r in results:
        print(f"  * {r['site']:<20}: {r['status']}", flush=True)
    print("=" * 80, flush=True)

if __name__ == '__main__':
    asyncio.run(main())
