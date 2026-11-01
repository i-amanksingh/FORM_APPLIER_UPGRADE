import os
import sys
import asyncio
import subprocess
import time
from typing import Optional, Any
from mcp.server.fastmcp import FastMCP, Image
from mcp.server.transport_security import TransportSecuritySettings
from mcp_functions import MCPServer

transport_security = TransportSecuritySettings(
    enable_dns_rebinding_protection=False,
)

mcp = FastMCP(
    "Apply_Form",
    instructions=(
        "You are an autonomous AI job application agent. Your goal is to apply for jobs completely and submit them with minimal tool calls.\n"
        "STANDARD WORKFLOW:\n"
        "1. Open the URL: call open_page(url).\n"
        "2. Check the response:\n"
        "   - If form_visible is True: Call apply_page() IMMEDIATELY.\n"
        "   - If apply_button_found is True: Call apply_page() directly (which will auto-click Apply) OR click the Apply button using do_action('click', ...).\n"
        "   - If login is required: Ask user to log in in Brave.\n"
        "3. NEVER call get_raw_html or get_screenshot during normal operations. They waste tokens and cause context limits to be exceeded. The internal system automatically handles extraction, Bedrock AI answers, verification, and submission.\n"
        "4. If apply_page returns leftover_fields, ONLY answer the specific leftover_fields listed using do_action, then immediately resume apply_page(continue_post=True). Do not inspect or touch any other fields.\n"
        "5. If apply_page returns needs_captcha_solved, tell the user to complete the captcha in Brave or on http://localhost:8085, then call apply_page(continue_post=True).\n"
        "6. Always agree to standard application terms, text message updates, privacy policies, and communications consent automatically (select Yes/Consent)."
    ),
    host="0.0.0.0",
    port=8080,
    streamable_http_path="/mcp",
    transport_security=transport_security,
)

global_server = MCPServer()


@mcp.tool(
    description=(
        "Open a REAL URL in the browser (Brave over CDP). Starts browser on first call.\n"
        "Returns status, page title, URL, whether the form is visible, and next action hint."
    )
)
async def open_page(url: str):
    """Open a URL in the browser."""
    try:
        return await global_server.open_page(url)
    except Exception as e:
        return {"status": "error", "message": str(e)}


@mcp.tool(
    description=(
        "Extract interactive form fields from the current page.\n"
        "NOTE: Do NOT call this during standard flow. Call apply_page directly to fill and submit forms."
    )
)
async def extract_important_tags():
    """Extract all interactive form elements from the current page."""
    return await global_server.extract_important_tags()


@mcp.tool(
    description=(
        "Perform an action on a specific form element.\n"
        "- action (str): 'fill', 'select', 'check', 'uncheck', 'upload', 'click', 'press', or 'clear'.\n"
        "- identifier (Any): Element index (int/str), CSS/XPath selector, or target dict.\n"
        "- data (Any, optional): Text to fill, option to select, key to press, or file path to upload."
    )
)
async def do_action(action: str, identifier: Any, data: Any = None):
    """Perform an action (fill, select, click, check, upload, etc.) on an element."""
    return await global_server.do_action(action=action, identifier=identifier, data=data)


@mcp.tool(
    description="EMERGENCY ONLY: Get page screenshot. Do NOT call this before or during apply_page unless explicitly requested for captcha or human verification."
)
async def get_screenshot():
    """Get current page screenshot as an image."""
    result = await global_server.get_screenshot()
    if isinstance(result, dict) and result.get("data") and result.get("format"):
        return Image(data=result["data"], format=result["format"])
    return result


@mcp.tool(
    description="DEBUG ONLY: Do NOT use during standard workflow. Returns compact outline and saves full HTML to page_debug.html."
)
async def get_raw_html():
    """Get raw HTML outline of current page."""
    return await global_server.get_raw_html()


@mcp.tool(
    description="Get real-time application status, including filled fields count and any unresolved fields."
)
async def get_application_status(job_id: Optional[str] = None):
    """Get current application status and field summary."""
    return global_server.get_application_status(job_id)


@mcp.tool(
    description=(
        "PRIMARY APPLICATION TOOL: Automatically fill and submit the job application form.\n"
        "Call this immediately after open_page once you see the job page.\n"
        "It automatically extracts fields, matches resume data, runs AI for questions, handles multi-page pagination, and submits.\n"
        "Parameters:\n"
        "- Multiple_page (bool): True if the application spans multiple pages (Next/Continue buttons), False if single page.\n"
        "- job_id (str, optional): Unique ID to track and resume this application (e.g. 'job_01').\n"
        "- continue_post (bool): Set to True when resuming after answering leftover questions or solving a captcha."
    )
)
async def apply_page(Multiple_page: bool = False, job_id: Optional[str] = None, continue_post: bool = False):
    """Automatically fill, navigate, and submit the job application."""
    result = await global_server.run_application_state(job_id=job_id, multiple_pages=Multiple_page, continue_post=continue_post)
    return result


if __name__ == "__main__":
    transport = "stdio"
    if len(sys.argv) > 1 and sys.argv[1] in ("--sse", "--http", "streamable-http"):
        transport = "streamable-http"
    elif len(sys.argv) > 1 and sys.argv[1] in ("--stdio", "stdio"):
        transport = "stdio"

    if transport == "stdio":
        asyncio.run(mcp.run_stdio_async())
    elif transport == "streamable-http":
        asyncio.run(mcp.run_streamable_http_async())
    elif transport == "sse":
        asyncio.run(mcp.run_sse_async())
    else:
        asyncio.run(mcp.run_stdio_async())
