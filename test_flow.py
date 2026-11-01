import asyncio
import json
import sys
from playwright.async_api import async_playwright
from dom_extractor import FormFieldExtractor
from working_fill_feilds import FormFiller
from mcp_functions import MCPServer

async def test_extract_and_fill(url="https://www.cynbit.in/careers/backend-developer#application-form"):
    pw = await async_playwright().start()
    browser = await pw.chromium.connect_over_cdp('http://localhost:9222')
    pages = browser.contexts[0].pages
    page = next((p for p in pages if 'cynbit' in p.url), pages[0])
    print(f"Target page: {page.url}")
    
    extractor = FormFieldExtractor(url=page.url)
    fields = await extractor.main(page)
    print(f"Total fields extracted: {len(fields)}")
    
    with open("cynbit_extracted.json", "w", encoding="utf-8") as f:
        json.dump(fields, f, indent=2, ensure_ascii=False)
        
    for i, fld in enumerate(fields):
        q = fld.get('question') or fld.get('label') or fld.get('placeholder') or ''
        sel = fld.get('selector') or ''
        tag = fld.get('tag') or ''
        print(f"[{i:02d}] {fld.get('field_key'):25} | {tag:6} | {sel:35} | {q[:40]}")

    await pw.stop()

if __name__ == "__main__":
    asyncio.run(test_extract_and_fill())
