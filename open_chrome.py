import asyncio
import os
from playwright.async_api import async_playwright

async def test_launch():
    async with async_playwright() as p:
        print("Launching visible Chrome directly via Playwright...")
        browser = await p.chromium.launch(
            channel="chrome",
            headless=False,
            args=["--start-maximized"]
        )
        page = await browser.new_page(no_viewport=True)
        await page.goto("https://jobs.pyjamahr.com/diginnovators/senior-python-developer/apply?source=LINKEDIN")
        await page.bring_to_front()
        print("Page loaded! Title:", await page.title())
        await asyncio.sleep(5)
        await browser.close()
        print("Done test.")

if __name__ == "__main__":
    asyncio.run(test_launch())

