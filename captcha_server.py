import asyncio
import threading
import webbrowser
from typing import Optional, Dict, Any
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
import uvicorn

app = FastAPI(title="Job Application Captcha Solver")
_event = threading.Event()
_captcha_active = False
_captcha_solution: Optional[str] = None
_captcha_image_b64: Optional[str] = None
_server_thread: Optional[threading.Thread] = None


class SolvePayload(BaseModel):
    solution: Optional[str] = None


@app.get("/", response_class=HTMLResponse)
async def captcha_ui():
    img_html = ""
    if _captcha_image_b64:
        img_html = f"""
        <div style="margin-bottom: 20px; background: #0f172a; padding: 12px; border-radius: 8px; border: 1px dashed #475569;">
            <p style="font-size: 13px; color: #94a3b8; margin: 0 0 8px 0; text-transform: uppercase; letter-spacing: 1px;">Captcha Challenge Image</p>
            <img src="data:image/png;base64,{_captcha_image_b64}" style="background: white; padding: 8px; border-radius: 6px; max-width: 100%; box-shadow: 0 4px 6px -1px rgba(0,0,0,0.3);" />
        </div>
        <div style="margin-bottom: 20px;">
            <input type="text" id="captcha_input" placeholder="Type captcha characters here" autofocus
                   style="width: 85%; padding: 12px 16px; font-size: 20px; font-weight: 700; border-radius: 8px; border: 2px solid #38bdf8; background: #0f172a; color: #f8fafc; text-align: center; letter-spacing: 4px; outline: none;"
                   onkeydown="if(event.key==='Enter') submitSolution()" />
        </div>
        <button id="btn_submit" onclick="submitSolution()" style="background: #0284c7; margin-bottom: 14px;">Submit Captcha & Apply</button>
        <div style="margin: 8px 0 14px 0; color: #64748b; font-size: 13px;">— OR —</div>
        """

    return f"""<!DOCTYPE html>
<html>
<head>
    <title>Captcha Solver - Job Application</title>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            background: #0f172a;
            color: #f8fafc;
            display: flex;
            align-items: center;
            justify-content: center;
            min-height: 100vh;
            margin: 0;
            padding: 20px;
            box-sizing: border-box;
        }}
        .card {{
            background: #1e293b;
            border: 1px solid #334155;
            border-radius: 16px;
            padding: 32px;
            max-width: 480px;
            width: 100%;
            text-align: center;
            box-shadow: 0 20px 25px -5px rgba(0,0,0,0.6);
        }}
        h1 {{ color: #38bdf8; font-size: 22px; margin: 0 0 12px 0; }}
        p {{ color: #94a3b8; font-size: 14px; line-height: 1.5; margin: 0 0 20px 0; }}
        .badge {{
            display: inline-block;
            padding: 4px 12px;
            border-radius: 9999px;
            background: rgba(234, 179, 8, 0.2);
            color: #fde047;
            font-size: 11px;
            font-weight: 700;
            letter-spacing: 0.5px;
            margin-bottom: 14px;
        }}
        button {{
            background: #2563eb;
            color: #fff;
            border: none;
            padding: 13px 24px;
            font-size: 15px;
            font-weight: 600;
            border-radius: 8px;
            cursor: pointer;
            transition: all 0.2s;
            width: 100%;
            box-sizing: border-box;
        }}
        button:hover {{ background: #1d4ed8; filter: brightness(1.1); }}
        .secondary-btn {{ background: #334155; color: #cbd5e1; }}
        .secondary-btn:hover {{ background: #475569; }}
        .success {{ display: none; color: #4ade80; font-weight: 600; margin-top: 16px; font-size: 15px; }}
    </style>
</head>
<body>
    <div class="card">
        <div class="badge">HUMAN INTERVENTION REQUIRED</div>
        <h1>Captcha Challenge Detected</h1>
        <p>A captcha challenge was detected on your job application form.<br>
           Complete it below or directly in your open <strong>Brave Browser</strong> window.</p>
        
        {img_html}
        
        <button id="btn_brave" class="secondary-btn" onclick="confirmSolved()">I Have Solved the Captcha in Brave</button>
        <div id="msg" class="success">✓ Captcha confirmed! Resuming automated application...</div>
    </div>
    <script>
        async function submitSolution() {{
            const input = document.getElementById('captcha_input');
            const val = input ? input.value.trim() : '';
            if (!val) {{
                alert("Please enter the captcha characters or click 'I Have Solved the Captcha in Brave'");
                return;
            }}
            const btn = document.getElementById('btn_submit');
            btn.disabled = true;
            btn.innerText = "Submitting...";
            try {{
                await fetch('/solve', {{
                    method: 'POST',
                    headers: {{'Content-Type': 'application/json'}},
                    body: JSON.stringify({{solution: val}})
                }});
                document.getElementById('msg').style.display = 'block';
                setTimeout(() => window.close(), 2500);
            }} catch(e) {{
                alert("Failed to notify solver: " + e);
                btn.disabled = false;
                btn.innerText = "Submit Captcha & Apply";
            }}
        }}

        async function confirmSolved() {{
            const btn = document.getElementById('btn_brave');
            btn.disabled = true;
            btn.innerText = "Resuming...";
            try {{
                await fetch('/solve', {{
                    method: 'POST',
                    headers: {{'Content-Type': 'application/json'}},
                    body: JSON.stringify({{}})
                }});
                document.getElementById('msg').style.display = 'block';
                setTimeout(() => window.close(), 2500);
            }} catch(e) {{
                alert("Failed to notify solver: " + e);
                btn.disabled = false;
                btn.innerText = "I Have Solved the Captcha in Brave";
            }}
        }}
    </script>
</body>
</html>"""


@app.post("/solve")
async def solve_captcha(payload: Optional[SolvePayload] = None):
    global _captcha_active, _captcha_solution
    _captcha_active = False
    if payload and payload.solution:
        _captcha_solution = payload.solution.strip()
    _event.set()
    return {"status": "ok", "message": "Captcha marked as solved", "solution": _captcha_solution}


@app.get("/status")
async def captcha_status():
    return {"active": _captcha_active, "solved": _event.is_set(), "solution": _captcha_solution}


def start_captcha_server():
    global _server_thread
    if _server_thread is not None and _server_thread.is_alive():
        return
    def _run():
        uvicorn.run(app, host="127.0.0.1", port=8085, log_level="warning")
    _server_thread = threading.Thread(target=_run, daemon=True)
    _server_thread.start()


async def wait_for_captcha(timeout: int = 120, image_b64: Optional[str] = None) -> Dict[str, Any]:
    global _captcha_active, _captcha_image_b64, _captcha_solution
    _captcha_image_b64 = image_b64
    _captcha_solution = None
    _captcha_active = True
    _event.clear()
    start_captcha_server()

    # Automatically pop up the captcha solver tab in the browser
    try:
        webbrowser.open("http://127.0.0.1:8085")
    except Exception:
        pass

    loop = asyncio.get_running_loop()
    def _wait_event():
        return _event.wait(timeout=timeout)

    solved = await loop.run_in_executor(None, _wait_event)
    _captcha_active = False
    return {"solved": bool(solved), "solution": _captcha_solution}
