import asyncio
import json
import sys
import traceback
from mcp_functions import MCPServer

async def test_site(url: str, site_num: int = 1):
    print(f"\n{'='*70}\n[SITE {site_num}] STARTING TEST FOR: {url}\n{'='*70}")
    server = MCPServer()
    try:
        # Step 1: Open Page
        print("\n>>> STEP 1: Calling open_page...")
        open_res = await server.open_page(url)
        print(f"[open_page result]:\n{json.dumps(open_res, indent=2, default=str)}")

        # Step 2: Apply Page
        print("\n>>> STEP 2: Calling apply_page...")
        apply_res = await server.apply_page(Multiple_page=False)
        print(f"[apply_page result]:\n{json.dumps(apply_res, indent=2, default=str)}")

        # Step 3: Handle any leftovers if requested
        if isinstance(apply_res, dict) and apply_res.get("status") in ("need_your_response", "needs_fields_solved"):
            leftovers = apply_res.get("leftover_fields", [])
            print(f"\n[LEFTOVER FIELDS DETECTED ({len(leftovers)})]:")
            for i, lf in enumerate(leftovers, 1):
                print(f"  {i}. idx={lf.get('element_index')} | q={lf.get('question')} | sel={lf.get('selector')} | type={lf.get('type')}")
                try:
                    await server.solve_live_leftover_tag(lf)
                except Exception as e_lf:
                    print(f"  Failed to solve leftover {i}: {e_lf}")
            print("\n>>> Continuing apply_page post-resolution...")
            apply_res = await server.apply_page(continue_post=True)
            print(f"[apply_page continue_post result]:\n{json.dumps(apply_res, indent=2, default=str)}")
        
        # Keep open for a few seconds to let user observe
        if server.page and not server.page.is_closed():
            print("\nObserving browser state for 8 seconds before finishing site test...")
            await server.page.wait_for_timeout(8000)

        return {"open_res": open_res, "apply_res": apply_res}
    except Exception as e:
        print(f"\n[ERROR in test_site]: {e}")
        traceback.print_exc()
        return {"error": str(e)}
    finally:
        try:
            if server.browser_manager:
                print("Cleaning up browser session...")
                await server.browser_manager.close()
        except Exception:
            pass

if __name__ == "__main__":
    target_url = sys.argv[1] if len(sys.argv) > 1 else "https://carpl.ai/careers/sde-1"
    num = int(sys.argv[2]) if len(sys.argv) > 2 else 1
    asyncio.run(test_site(target_url, num))
