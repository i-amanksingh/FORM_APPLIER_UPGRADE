import os
import asyncio
import subprocess
import time
from mcp.server.fastmcp import FastMCP, Image
from mcp.server.transport_security import TransportSecuritySettings

transport_security = TransportSecuritySettings(
    enable_dns_rebinding_protection=False,
)

mcp = FastMCP(
    "Apply_Form",
    instructions="You are a helpful assistant that can help with form filling and submission.",
    host="0.0.0.0",
    port= 8080,
    streamable_http_path="/mcp",
    transport_security=transport_security,
)

global_server = None

@mcp.tool(
    description=(
        "Open a REAL URL in the browser. Starts browser on first call. "
        "NEVER invent URLs (no jobId/1234567, no placeholder). "
        "Rejects fake URLs and 404/errorpage landings.",
        "you will get response code to understand what happen on page "
    )
)
async def open_page(url: str):
    """Open a URL. Initializes browser + DB if needed."""
    try:
        return await global_server.open_page(url)
    except Exception as e:
        return {"status": "error", "message": str(e)}
    
    
@mcp.tool(
        description=(
        "Get important tags of current page + iframe in structured format"
        "once you get understand important tags required currently"
    )
)
async def extract_important_tags():
    """Extract all interactive form elements from the current page."""
    return await global_server.extract_important_tags()

@mcp.tool(
    description=(
        "Try to self fill the tags by sending action along with identifier of tags + data to insert init "
    )
)
async def do_action(action , identifier , data = None):
    """Try to self fill the tags by sending action along with identifier of tags + data to insert init """
    return await global_server.do_action(action , identifier , data)
    
@mcp.tool(
    description=(
        "Get current page Screenshot in png format")
    )
async def get_screenshot():
    """Get current page Screenshot in png format"""
    return await global_server.get_screenshot()

@mcp.tool(
    description=(
        "Get raw html of current page + iframe ")
)
async def get_raw_html():
    """Get raw html of current page + iframe """
    return await global_server.get_raw_html()

@mcp.tool(
    description= (
    "Call this function only when you see the form appeared accurately with feilds visible do singup/login first then call this function"
    "Call this function using telling wether Multiple = True or False means if true then internal process will try to fill each page and call next and submit it"
    "start automatic page_apply internal it will work like get current page html in structured format. "
    "")
)
async def apply_page(Multiple_page: bool , extract_data):
    """Call this function only when you see the form appeared accurately with feilds visible do singup/login first then call this function"""
    result = await global_server.apply_page(Multiple_page , extract_data)
    for type in result:
       if type == "success":
          return "success"
       elif type == "need_your_response":
          blank_feilds = result[type]
          return "need_your_response , call do_action function to fill rest feilds " + blank_feilds
       elif type == "error":
          return "error , " + result[type]
       else:
          return "unknown"
    
