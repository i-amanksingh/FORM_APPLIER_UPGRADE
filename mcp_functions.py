import sys
import os
import re
import json
import time
import asyncio
import base64
import traceback
from pathlib import Path
from typing import Optional, Dict, Any, List

from playwright.async_api import Page
from browser_control import BrowserManager
from dom_extractor import FormFieldExtractor
from working_fill_feilds import FormFiller, _find_default_resume_path
from captcha_server import wait_for_captcha


class ApplicationStateManager:
    """Manages job state, filled fields, leftovers, and logs for Claude and MCP tools."""

    def __init__(self):
        self.jobs: Dict[str, Dict[str, Any]] = {}

    def get_or_create(self, job_id: str, url: str) -> Dict[str, Any]:
        if job_id not in self.jobs:
            self.jobs[job_id] = {
                "job_id": job_id,
                "url": url,
                "current_page_index": 1,
                "status": "initialized",
                "progress_summary": "Job initialized",
                "filled_fields": [],
                "leftover_fields": [],
                "last_action": None,
                "logs": [],
                "submitted": False,
                "submit_result": None,
            }
        return self.jobs[job_id]

    def record_field(self, job_id: Optional[str], element_index: Any, question: str, selector: str, value: Any, method: str, verified: bool):
        if not job_id:
            if not self.jobs:
                return
            job_id = list(self.jobs.keys())[-1]
        job = self.jobs.get(job_id)
        if not job:
            return

        existing = next((f for f in job["filled_fields"] if f.get("element_index") == element_index), None)
        item = {
            "element_index": element_index,
            "question": question or selector,
            "selector": selector,
            "value": value,
            "method": method,
            "verified": verified
        }
        if existing:
            existing.update(item)
        else:
            job["filled_fields"].append(item)

        action_desc = f"Filled [{question or selector}] -> {value} ({'VERIFIED' if verified else 'UNVERIFIED'})"
        job["last_action"] = action_desc
        job["logs"].append(action_desc)
        job["leftover_fields"] = [f for f in job["leftover_fields"] if f.get("element_index") != element_index]

    def set_leftovers(self, job_id: str, leftovers: List[Dict[str, Any]]):
        job = self.jobs.get(job_id)
        if job:
            job["leftover_fields"] = leftovers
            job["status"] = "needs_intervention" if leftovers else "ready_to_submit"

    def record_submit(self, job_id: Optional[str], success: bool, message: str):
        if not job_id and self.jobs:
            job_id = list(self.jobs.keys())[-1]
        job = self.jobs.get(job_id)
        if job:
            job["submitted"] = success
            job["status"] = "submitted" if success else "error_after_submit"
            job["submit_result"] = {"success": success, "message": message}
            job["logs"].append(f"Submit attempt: {'SUCCESS' if success else 'FAILED'} - {message}")

    def get_status(self, job_id: Optional[str] = None) -> Dict[str, Any]:
        if not job_id:
            if not self.jobs:
                return {"status": "no_active_job", "message": "No application job has been started yet."}
            job_id = list(self.jobs.keys())[-1]
        job = self.jobs.get(job_id)
        if not job:
            return {"status": "not_found", "message": f"Job ID {job_id} not found."}
        return {
            "status": job.get("status"),
            "job_id": job.get("job_id"),
            "url": job.get("url"),
            "page_index": job.get("current_page_index", 1),
            "last_action": job.get("last_action"),
            "total_filled": len(job.get("filled_fields", [])),
            "total_leftover": len(job.get("leftover_fields", [])),
            "filled_fields": job.get("filled_fields", []),
            "leftover_fields": job.get("leftover_fields", []),
            "submitted": job.get("submitted", False),
            "recent_logs": job.get("logs", [])[-10:],
        }


class MCPServer:
    """MCP Server for form filling operations."""

    def __init__(self):
        self.browser_manager = None
        self.page = None
        self.current_page_url = None
        self.current_page_title = None
        self.dom_extractor = None
        self.automatic_filling = None
        self.state_manager = ApplicationStateManager()
        self.last_elements = []
        self._initialized = False

    async def sync_active_page(self) -> Page:
        """Detect if a new tab/window opened, close unwanted auth popups, and focus active application page."""
        if not self.browser_manager or not self.browser_manager.browser:
            return self.page
        try:
            contexts = self.browser_manager.browser.contexts
            if not contexts:
                return self.page
            ctx = contexts[0]
            pages = [p for p in ctx.pages if not p.is_closed()]
            if not pages:
                return self.page

            # Check for unwanted external auth popups (e.g. LinkedIn or Google OAuth popup)
            for p in list(pages):
                if p.is_closed():
                    continue
                p_url = p.url.lower()
                if len(pages) > 1 and ("linkedin.com/uas/login" in p_url or "linkedin.com/checkpoint" in p_url or "accounts.google.com/o/oauth2" in p_url):
                    print(f"[MCPServer] Auto-closing unwanted external login popup: {p.url[:60]}")
                    try:
                        await p.close()
                    except Exception:
                        pass
            
            # Refresh list of live pages
            pages = [p for p in ctx.pages if not p.is_closed()]
            if not pages:
                return self.page

            # Pick the active application page (newest non-blank page with job/career/form/ats url)
            best_page = pages[-1]
            for p in reversed(pages):
                p_url = p.url.lower()
                if any(w in p_url for w in ["job", "career", "apply", "candidate", "form", "workday", "lever", "greenhouse", "keka", "defenzelite", "cynbit", "teamtailor", "zohopublic"]):
                    best_page = p
                    break

            if best_page != self.page or not self.page or self.page.is_closed():
                print(f"[MCPServer] Switching active page to: {best_page.url[:70]}")
                self.page = best_page
                self.current_page_url = best_page.url
                try:
                    self.current_page_title = await best_page.title()
                except Exception:
                    pass
                self.browser_manager.page = best_page
                try:
                    await best_page.bring_to_front()
                except Exception:
                    pass
                if self.dom_extractor:
                    self.dom_extractor.page = best_page
                    self.dom_extractor.url = best_page.url
                if self.automatic_filling:
                    self.automatic_filling.page = best_page
                    self.automatic_filling.url = best_page.url
        except Exception as e:
            print(f"[MCPServer] Tab sync warning: {e}")
        return self.page

    async def _ensure_browser_session(self) -> None:
        """Ensure Playwright browser and shared page is alive."""
        if not self.browser_manager or not self._initialized:
            await self.initialize()
        page = await self.browser_manager.ensure_session()
        self.page = page
        await self.sync_active_page()
        if self.dom_extractor:
            self.dom_extractor.page = self.page
        if self.automatic_filling:
            self.automatic_filling.page = self.page

    async def initialize(self):
        if self._initialized:
            try:
                await self._ensure_browser_session()
            except Exception as e:
                self._initialized = False
                return {"status": "error", "message": str(e)}
            return {
                "status": "already_initialized",
                "session_alive": self.browser_manager.session_is_alive() if self.browser_manager else False,
            }

        self.browser_manager = BrowserManager(headless=False)
        await self.browser_manager.start()
        self.page = await self.browser_manager.ensure_session()
        self._initialized = True
        return {"status": "initialized", "message": "Browser connected over CDP"}

    async def open_page(self, url: str):
        try:
            url = (url or "").strip()
            if not url:
                return {
                    "status": "error",
                    "message": "URL is missing. Call open_page with an accurate HTTP/HTTPS URL.",
                }

            if not self._initialized:
                init = await self.initialize()
                if init.get("status") == "error":
                    return init

            await self._ensure_browser_session()
            result = await self.browser_manager.navigate_to(url)
            if result.get("status") == "success":
                self.current_page_url = result.get("url")
                self.current_page_title = result.get("title")
                self.page = result.get("page")
                if self.page:
                    try:
                        await self.page.bring_to_front()
                    except Exception:
                        pass
                self.dom_extractor = FormFieldExtractor(url=self.current_page_url)
                self.automatic_filling = FormFiller(page=self.page, url=self.current_page_url)

                final_url = (result.get("url") or "").lower()
                title = (result.get("title") or "").lower()
                if (
                    "errortype=404" in final_url
                    or "/errorpage" in final_url
                    or "page not found" in title
                    or "404" in title
                ):
                    return {
                        "status": "error",
                        "message": "Opened URL landed on a 404/error page. Check the URL.",
                        "url": result.get("url"),
                        "title": result.get("title"),
                    }

                # Perceive page state: check if form is visible or if Apply button exists
                form_visible = False
                apply_btn_found = False
                apply_btn_text = ""
                apply_btn_sel = ""
                needs_login = False

                try:
                    await self.handle_active_popups()
                    # Wait up to 5s for page hydration / loading spinners to detach
                    try:
                        for _ in range(10):
                            is_loading = await self.page.evaluate("""() => {
                                const spin = document.querySelector('.spinner, .loading, .ant-spin, [class*="loader" i], [class*="spinner" i]');
                                return Boolean(spin && spin.offsetParent !== null);
                            }""")
                            if not is_loading:
                                break
                            await self.page.wait_for_timeout(500)
                    except Exception:
                        pass

                    if self.dom_extractor:
                        input_count = await self.dom_extractor._count_visible_inputs(self.page)
                    else:
                        input_count = await self.page.evaluate("""() => {
                            return [...document.querySelectorAll('input:not([type="hidden"]):not([type="submit"]):not([type="button"]), select, textarea')]
                                .filter(el => el.offsetParent !== null && !el.disabled).length;
                        }""")

                    body_text = (await self.page.locator("body").inner_text(timeout=1500) or "").lower()
                    if any(m in body_text for m in ["sign in to apply", "log in to apply", "please log in to continue", "login to your account"]) and input_count <= 2:
                        needs_login = True

                    if input_count >= 2:
                        form_visible = True
                    else:
                        all_frames = [self.page] + [f for f in self.page.frames if not (self.dom_extractor and self.dom_extractor._is_ignorable_frame(f))]
                        for fr in all_frames:
                            try:
                                apply_info = await fr.evaluate("""() => {
                                    const btns = [...document.querySelectorAll('button, a, [role="button"], input[type="button"]')];
                                    for (const b of btns) {
                                        if (b.offsetParent === null) continue;
                                        const t = (b.innerText || b.textContent || b.value || '').trim().toLowerCase();
                                        if (/\\b(apply now|apply for this job|easy apply|apply online|apply)\\b/i.test(t) && !/\\b(how to apply|terms|privacy|linkedin|indeed|oauth|social|google|apple)\\b/i.test(t) && !t.includes('apply with')) {
                                            let sel = '';
                                            if (b.id) sel = `#${CSS.escape(b.id)}`;
                                            else if (b.tagName.toLowerCase() === 'button') sel = `button:has-text("${t.slice(0, 30)}")`;
                                            else if (b.tagName.toLowerCase() === 'a') sel = `a:has-text("${t.slice(0, 30)}")`;
                                            else sel = `[role="button"]:has-text("${t.slice(0, 30)}")`;
                                            return { found: true, text: (b.innerText || t).trim().slice(0, 40), selector: sel };
                                        }
                                    }
                                    return { found: false, text: '', selector: '' };
                                }""")
                                if apply_info.get("found"):
                                    apply_btn_found = True
                                    apply_btn_text = apply_info.get("text", "")
                                    apply_btn_sel = apply_info.get("selector", "")
                                    break
                            except Exception:
                                continue

                except Exception as e:
                    print(f"[MCPServer] open_page form check notice: {e}")

                if needs_login:
                    action_hint = "Login required on this portal. Please log in in the browser window or provide credentials."
                elif form_visible:
                    action_hint = "Application form is already visible on page. Next step: Call apply_page() directly to automatically extract, fill, and submit."
                elif apply_btn_found:
                    action_hint = f"Job description page detected with '{apply_btn_text}' button. Next step: Call apply_page() which will automatically click Apply and fill the form."
                else:
                    action_hint = "Page loaded. Next step: Call apply_page() to process application."

                return {
                    "status": "success",
                    "url": result.get("url"),
                    "title": result.get("title"),
                    "form_visible": form_visible,
                    "apply_button_found": apply_btn_found,
                    "apply_button_selector": apply_btn_sel if apply_btn_found else None,
                    "action_hint": action_hint,
                }

            return result
        except Exception as e:
            return {"status": "error", "message": str(e)}

    async def extract_important_tags(self):
        """Extract all interactive form elements from the current page."""
        try:
            await self._ensure_browser_session()
            if not self.page:
                return {"status": "error", "message": "No active page. Call open_page first."}
            if not self.dom_extractor:
                self.dom_extractor = FormFieldExtractor(url=self.current_page_url or self.page.url)

            dom_result = await self.dom_extractor.main(self.page)
            if isinstance(dom_result, list):
                important = dom_result
            elif isinstance(dom_result, dict):
                important = dom_result.get("fields") or dom_result.get("important") or []
            else:
                important = []
            self.last_elements = important
            return {
                "status": "success",
                "url": self.page.url,
                "total_fields": len(important),
                "fields": important,
            }
        except Exception as e:
            return {"status": "error", "message": str(e)}

    async def extract_dom(self, page=None):
        return await self.extract_important_tags()

    def get_resume_summary(self) -> Dict[str, Any]:
        """Load and return applicant resume content."""
        try:
            resume_path = Path("resume_imp_content.json")
            if resume_path.exists():
                with open(resume_path, "r", encoding="utf-8") as f:
                    return json.load(f)
        except Exception:
            pass
        return {}

    def get_application_status(self, job_id: Optional[str] = None) -> Dict[str, Any]:
        """Return real-time state of filled fields and leftover questions."""
        return self.state_manager.get_status(job_id)
 
    def _normalize_locator(self,locator: str) -> str:
        loc = (locator or "").strip()
        if re.match(r"^#\d", loc):
            return f'[id="{loc[1:]}"]'
        return loc 
 

    async def get_screenshot(self):
        """Alias for get_page_screenshot."""
        return await self.get_page_screenshot()

    async def get_page_screenshot(self):
        
        if not self.browser_manager or not self.browser_manager.page:
            return {"status": "error", "message": "No active page"}

        page = self.browser_manager.page
        try:

            data = await page.screenshot(
                type="jpeg", quality=75, full_page=False, animations="disabled", timeout=8000,
            )
            return {
                "status": "success",
                "url": page.url,
                "title": await page.title(),
                "format": "jpeg",
                "data": data,
            }
        except Exception as e:
            return {
                "status": False,
                "problem": f"Failed to get screenshot: {e}"
            }
    
    async def handle_active_popups(self) -> List[Dict[str, Any]]:
        """Perceive and resolve in-page popups, modals, cookie banners, and overlays.
        STRICT RULE: Only dismisses unnecessary popups (newsletters, marketing promos, cookie banners, job alerts).
        NEVER dismisses:
        1. The actual job application form modal (contains file/resume upload or >= 4 application fields).
        2. Login or Sign-up modals (contains password or login/create account authentication).
        """
        if not self.page:
            return []
        actions_taken = []
        try:
            # 1. Proactive localStorage flags to prevent known recurring popups
            try:
                await self.page.evaluate("""() => {
                    try { localStorage.setItem("cynbit_newsletter_seen", "true"); } catch(e) {}
                    try { localStorage.setItem("newsletter_subscribed", "true"); } catch(e) {}
                    try { sessionStorage.setItem("modal_dismissed", "true"); } catch(e) {}
                }""")
            except Exception:
                pass

            # 2. LinkedIn / Social login prompt overlays: choose manual application
            linkedin_prompt_selectors = [
                'button:has-text("Apply manually")',
                'button:has-text("Continue with email")',
                'button:has-text("Fill application manually")',
                'button:has-text("Apply with Resume")',
                'a:has-text("Apply manually")',
                'a:has-text("Continue without LinkedIn")',
            ]
            for sel in linkedin_prompt_selectors:
                try:
                    loc = self.page.locator(sel).first
                    if await loc.count() > 0 and await loc.is_visible():
                        print(f"[MCPServer] Choosing manual application option over LinkedIn: '{sel}'")
                        await loc.click(timeout=2000)
                        actions_taken.append({"type": "linkedin_manual_chosen", "selector": sel})
                        await self.page.wait_for_timeout(500)
                        break
                except Exception:
                    continue

            # 3. Dismiss cookie / GDPR consent banners
            cookie_selectors = [
                '#onetrust-accept-btn-handler',
                '#accept-cookie-notification',
                'button[id*="cookie-accept" i]',
                'button[class*="cookie-accept" i]',
                'button[id*="accept-all" i]',
                'button:has-text("Accept all cookies")',
                'button:has-text("Accept Cookies")',
                'button:has-text("Accept All")',
                'button:has-text("I Agree")',
                'button:has-text("Got it")',
                'button:has-text("Allow all")',
                '.cc-accept',
                '.cookie-btn',
            ]
            for sel in cookie_selectors:
                try:
                    loc = self.page.locator(sel).first
                    if await loc.count() > 0 and await loc.is_visible():
                        await loc.click(timeout=1500)
                        actions_taken.append({"type": "cookie_dismissed", "selector": sel})
                        await self.page.wait_for_timeout(300)
                        break
                except Exception:
                    continue

            # 4. Comprehensive & Safe Modal Dismissal (Newsletter, Promos, Job Alerts)
            # Safe: Strictly preserves Login/Signup and Job Application forms
            modal_actions = await self.page.evaluate("""() => {
                const results = [];
                const candidates = [...document.querySelectorAll('dialog[open], [role="dialog"], div, section, aside')].filter(el => {
                    if (!el || el === document.body || el === document.documentElement) return false;
                    const style = window.getComputedStyle(el);
                    if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
                    const pos = style.position;
                    const z = parseInt(style.zIndex, 10) || 0;
                    const isDialog = el.tagName === 'DIALOG' || el.getAttribute('role') === 'dialog';
                    return isDialog || ((pos === 'fixed' || (pos === 'absolute' && z >= 50)) && el.offsetWidth > 100 && el.offsetHeight > 100);
                });

                for (const d of candidates) {
                    // GENERIC SAFEGUARD 1: Never close active job application modal or form container!
                    const hasAppForm = !!d.querySelector('form, [class*="step" i], [class*="wizard" i], [class*="progress" i]');
                    const hasAppControls = d.querySelectorAll('input:not([type="hidden"]), select, textarea').length > 0;
                    const hasAppButtons = [...d.querySelectorAll('button, [role="button"], a')].some(b => {
                        const t = (b.innerText || '').trim().toLowerCase();
                        return /\\b(next|continue|submit|confirm|previous|back|save|apply)\\b/i.test(t);
                    });
                    const dText = (d.innerText || '').toLowerCase();
                    const isAppModal = /apply|application|candidate|resume|applicant|confirm/i.test(dText);
                    if ((hasAppForm || hasAppButtons || isAppModal) && hasAppControls) {
                        continue; // Strictly preserve active application forms
                    }

                    const text = (d.innerText || '').toLowerCase();

                    // SAFEGUARD 2: Never close Login or Signup modal!
                    const isAuth = (
                        text.includes('sign in') || text.includes('log in') || text.includes('login') || 
                        text.includes('sign up') || text.includes('signup') || text.includes('create account') ||
                        text.includes('register') || text.includes('forgot password')
                    ) && (
                        !!d.querySelector('input[type="password"]') || 
                        text.includes('google') || text.includes('continue with') || text.includes('password')
                    );
                    if (isAuth) {
                        continue;
                    }

                    // IDENTIFICATION: Check if unnecessary popup
                    const isNewsletter = text.includes('newsletter') || text.includes('zero noise') || text.includes('architect') ||
                                         text.includes('subscribe') || text.includes('digest') || text.includes('inner circle') ||
                                         text.includes('editorial briefing') || text.includes('mailing list');
                    const isJobAlert = text.includes('job alert') || text.includes('talent community') || text.includes('talent network') ||
                                       text.includes('future opportunities') || text.includes('get notified');
                    const isPromo = text.includes('special offer') || text.includes('take a survey') || text.includes('feedback') ||
                                    text.includes('discount') || text.includes('join our community');
                    const isCookie = text.includes('cookie') || text.includes('privacy notice') || text.includes('we value your privacy');

                    if (isNewsletter || isJobAlert || isPromo || isCookie) {
                        // Look for close button
                        const allElements = [...d.querySelectorAll('button, [role="button"], a, span, svg')];
                        let clicked = false;
                        for (const b of allElements) {
                            const bText = (b.innerText || '').trim().toLowerCase();
                            const aria = (b.getAttribute('aria-label') || '').toLowerCase();
                            const cls = (b.className || '').toString().toLowerCase();
                            const title = (b.getAttribute('title') || '').toLowerCase();

                            const isCloseBtn = (
                                bText === '✕' || bText === '×' || bText === 'x' || bText === 'close' ||
                                bText === 'no thanks' || bText === 'not now' || bText === 'skip' ||
                                bText === 'dismiss' || bText === 'accept' || bText === 'accept all' ||
                                aria.includes('close') || aria.includes('dismiss') ||
                                title.includes('close') || cls.includes('close-btn') ||
                                (b.tagName === 'SPAN' && bText === 'close' && cls.includes('material-symbols'))
                            );

                            if (isCloseBtn) {
                                try {
                                    b.click();
                                    results.push({ type: 'dismissed_btn', tag: b.tagName, text: bText || aria });
                                    clicked = true;
                                    break;
                                } catch(e) {}
                            }
                        }

                        if (!clicked) {
                            d.style.display = 'none';
                            results.push({ type: 'hidden_overlay', tag: d.tagName });
                        }
                    }
                }
                return results;
            }""")
            if modal_actions:
                actions_taken.extend(modal_actions)
                print(f"[MCPServer] Dismissed unnecessary popup(s): {modal_actions}")
                await self.page.wait_for_timeout(300)

            # 5. Confirmation dialogs (e.g. "Review your application" or "Are you sure you want to submit?")
            confirmation_confirmed = await self.page.evaluate("""() => {
                const dialogs = [...document.querySelectorAll('dialog[open], [role="dialog"]:not([aria-hidden="true"]), .modal.show, .modal.in')];
                for (const d of dialogs) {
                    if (d.offsetParent === null) continue;
                    const txt = (d.innerText || '').toLowerCase();
                    if (txt.includes('are you sure') || txt.includes('confirm application') || txt.includes('review your application') || txt.includes('ready to submit')) {
                        const btns = [...d.querySelectorAll('button, [role="button"]')];
                        for (const b of btns) {
                            const bt = (b.innerText || '').trim().toLowerCase();
                            if (bt === 'confirm' || bt === 'submit' || bt === 'yes, submit' || bt === 'submit application') {
                                b.click();
                                return 'confirmed_submission_modal';
                            }
                        }
                    }
                }
                return null;
            }""")
            if confirmation_confirmed:
                actions_taken.append({"type": confirmation_confirmed})
                await self.page.wait_for_timeout(500)

        except Exception as e:
            print(f"[MCPServer] Popup handling warning: {e}")
        return actions_taken

    async def detect_live_leftover_tags(self) -> List[Dict[str, Any]]:
        """Perceive and inspect the live DOM for unfilled, required, or invalid tags right now."""
        if not self.page:
            return []
        await self.sync_active_page()
        try:
            leftovers = await self.page.evaluate("""() => {
                const leftovers = [];
                const seen = new Set();
                const inputs = [...document.querySelectorAll('input, select, textarea')];
                
                for (const el of inputs) {
                    const type = (el.type || '').toLowerCase();
                    if (['hidden', 'submit', 'button', 'reset', 'image'].includes(type)) continue;
                    if (el.disabled || el.readOnly || el.getAttribute('aria-readonly') === 'true') continue;
                    if (el.offsetParent === null) continue;
                    
                    let isInvalid = false;
                    let reason = '';
                    
                    if (typeof el.checkValidity === 'function' && !el.checkValidity()) {
                        isInvalid = true;
                        reason = el.validationMessage || 'HTML5 validation failed';
                    }
                    if (el.getAttribute('aria-invalid') === 'true') {
                        isInvalid = true;
                        reason = reason || 'aria-invalid is true';
                    }
                    const fieldContainer = el.closest('.MuiFormControl-root, .form-group, .field, .input-group, .react-datepicker-wrapper, [class*="form-item"]') || el.parentElement;

                    if (fieldContainer) {
                        const pClass = (fieldContainer.className || '').toString();
                        if (/\b(error|has-error|field-with-errors|form-error|is-invalid|choice-input-wrapper--error|Mui-error|text-danger)\b/i.test(pClass)) {
                            isInvalid = true;
                            reason = reason || 'container has error class';
                        }
                        const errChild = fieldContainer.querySelector('.error-feedback, .select-error-message, .Mui-error, [class*="error" i], .text-danger, [class*="danger" i], [class*="invalid" i]');
                        if (errChild && errChild.offsetParent !== null) {
                            isInvalid = true;
                            reason = reason || errChild.innerText.replace(/[\u200b\u200c\u200d\ufeff]/g, '').trim();
                        }
                    }
                    const colItem = el.closest('.MuiGrid-item, .col, [class*="col-"]');
                    if (colItem && !isInvalid) {
                        const errChild = colItem.querySelector('.error-feedback, .select-error-message, .Mui-error, [class*="error" i], .text-danger, [class*="danger" i], [class*="invalid" i]');
                        if (errChild && errChild.offsetParent !== null) {
                            const errTxt = (errChild.innerText || '').toLowerCase();
                            if (errTxt.includes('required') || errTxt.includes('invalid') || errTxt.includes('must') || errTxt.includes('enter') || errTxt.includes('fill')) {
                                isInvalid = true;
                                reason = reason || errChild.innerText.replace(/[\u200b\u200c\u200d\ufeff]/g, '').trim();
                            }
                        }
                    }
                    
                    const isRequired = el.required || el.getAttribute('aria-required') === 'true' ||
                        Boolean(fieldContainer && (
                            /\b(required|mandatory)\b/i.test(fieldContainer.className || '') ||
                            fieldContainer.querySelector('label')?.innerText?.includes('*') ||
                            fieldContainer.querySelector('.MuiFormLabel-asterisk, [class*="asterisk"], [class*="required"]')
                        ));
                        
                    let isEmpty = false;
                    const val = (el.value || '').trim();
                    let hasFiles = false;
                    let hasAttachedUi = false;
                    if (type === 'radio' && el.name) {
                        const group = document.querySelectorAll(`input[type="radio"][name="${CSS.escape(el.name)}"]`);
                        const anyChecked = [...group].some(r => r.checked);
                        if (!anyChecked && (isRequired || isInvalid)) {
                            isEmpty = true;
                            reason = reason || 'required radio group has no selection';
                        }
                    } else if (type === 'checkbox') {
                        if ((isRequired || isInvalid) && !el.checked) {
                            isEmpty = true;
                            reason = reason || 'required checkbox is unchecked';
                        }
                    } else if (type === 'file') {
                        hasFiles = (el.files && el.files.length > 0) || Boolean(val);
                        hasAttachedUi = Boolean(
                            el.closest('.upload, [class*="upload" i], [class*="dropzone" i], [class*="file" i]')?.querySelector('.upload__file, .upload__done, .upload__replace, [class*="uploaded" i], [class*="file-name" i], [class*="filename" i]') ||
                            document.querySelector('.upload__file, .upload__done, .upload__replace, [class*="uploaded" i]')
                        );
                        if (!hasFiles && !hasAttachedUi && (isRequired || isInvalid)) {
                            isEmpty = true;
                            reason = reason || 'required file upload is empty';
                        }
                    } else {
                        if (!val && (isRequired || isInvalid)) {
                            isEmpty = true;
                            reason = reason || (isInvalid ? reason : 'required field is empty');
                        }
                    }
                    
                    // CRITICAL: If a file input already has a file or attached UI, never treat it as leftover unless error specifically mentions file
                    if (type === 'file' && (hasFiles || hasAttachedUi)) {
                        const isFileError = isInvalid && /\b(file|resume|cv|pdf|doc|upload|format|size)\b/i.test(reason || '');
                        if (!isFileError) continue;
                    }
                    if (!isEmpty && !isInvalid) continue;
                    if (type === 'file' && !isEmpty && !isInvalid) continue;
                    if (val && !el.getAttribute('aria-invalid') && (typeof el.checkValidity !== 'function' || el.checkValidity()) && !isInvalid) continue;
                    
                    let sel = '';
                    if (el.id) {
                        sel = `#${CSS.escape(el.id)}`;
                    } else if (el.getAttribute('data-testid')) {
                        sel = `${el.tagName.toLowerCase()}[data-testid="${CSS.escape(el.getAttribute('data-testid'))}"]`;
                    } else if (el.name) {
                        sel = `${el.tagName.toLowerCase()}[name="${CSS.escape(el.name)}"]`;
                    } else if (el.placeholder) {
                        const matches = document.querySelectorAll(`${el.tagName.toLowerCase()}[placeholder="${CSS.escape(el.placeholder)}"]`);
                        if (matches.length === 1) {
                            sel = `${el.tagName.toLowerCase()}[placeholder="${CSS.escape(el.placeholder)}"]`;
                        } else {
                            const idx = [...matches].indexOf(el);
                            sel = `${el.tagName.toLowerCase()}[placeholder="${CSS.escape(el.placeholder)}"]:nth-of-type(${idx + 1})`;
                        }
                    } else {
                        let path = el.tagName.toLowerCase();
                        if (el.className) path += '.' + el.className.trim().split(/\\s+/)[0];
                        sel = path;
                    }
                    
                    if (seen.has(sel) && type === 'radio') continue;
                    seen.add(sel);
                    
                    let label = el.labels && el.labels[0] ? el.labels[0].innerText.replace(/[\u200b\u200c\u200d\ufeff]/g, '').trim() : '';
                    if (!label && el.id) {
                        const labFor = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
                        if (labFor) label = labFor.innerText.replace(/[\u200b\u200c\u200d\ufeff]/g, '').trim();
                    }
                    if (!label && fieldContainer) {
                        const lab = fieldContainer.querySelector('label, [class*="label"], [class*="question"]');
                        if (lab) label = lab.innerText.replace(/[\u200b\u200c\u200d\ufeff]/g, '').trim();
                    }
                    if (!label) label = el.getAttribute('aria-label') || el.placeholder || el.name || el.id || '';
                        
                        let options = [];
                        if (el.tagName.toLowerCase() === 'select') {
                            options = [...el.options].map(o => ({label: o.text.trim(), value: o.value})).filter(o => o.value);
                        } else if (type === 'radio' && el.name) {
                            const group = document.querySelectorAll(`input[type="radio"][name="${CSS.escape(el.name)}"]`);
                            options = [...group].map(r => {
                                const l = r.labels && r.labels[0] ? r.labels[0].innerText.trim() : (r.parentElement ? r.parentElement.innerText.trim() : r.value);
                                return {label: l, value: r.value, id: r.id, selector: r.id ? `#${CSS.escape(r.id)}` : ''};
                            });
                        }
                        
                        leftovers.push({
                            selector: sel,
                            id: el.id,
                            name: el.name,
                            tag: el.tagName.toLowerCase(),
                            type: type,
                            question: label.slice(0, 100),
                            placeholder: el.placeholder || '',
                            required: isRequired,
                            reason: reason,
                            current_value: el.value || (el.checked ? 'checked' : ''),
                            options: options.slice(0, 10)
                        });
                }
                return leftovers;
            }""")
            return leftovers or []
        except Exception as e:
            print(f"[MCPServer] detect_live_leftover_tags error: {e}")
            return []

    async def solve_live_leftover_tag(self, tag: Dict[str, Any], job_id: Optional[str] = None) -> Dict[str, Any]:
        """Directly solve a single leftover or invalid tag on the live UI using candidate data."""
        sel = tag.get("selector")
        q = f"{tag.get('question') or ''} {tag.get('placeholder') or ''} {tag.get('name') or ''} {sel or ''}"
        q = re.sub(r"[\u200b\u200c\u200d\ufeff]", "", q).strip().lower()
        tag_name = (tag.get("tag") or "").lower()
        tag_type = (tag.get("type") or "text").lower()
        is_select = (tag_name == "select" or "select" in tag_type)
        options = tag.get("options") or []

        ans = "NA"
        action = "fill"

        resume = self.get_resume_summary()
        prompt_data = resume.get("prompt", {}) if isinstance(resume.get("prompt"), dict) else {}
        personal_info = prompt_data.get("personal_info") or resume.get("personal_info") or {}
        work_exp = prompt_data.get("work_experience") or resume.get("work_experience") or []
        edu_list = prompt_data.get("education") or resume.get("education") or []

        full_name = personal_info.get("full_name") or resume.get("candidate_name") or ""
        name_parts = full_name.split()
        first_name = name_parts[0] if name_parts else ""
        last_name = name_parts[-1] if len(name_parts) > 1 else ""
        email = personal_info.get("email") or ""
        phone = personal_info.get("phone") or ""
        linkedin = personal_info.get("linkedin") or ""
        github = personal_info.get("github") or ""
        gender = personal_info.get("gender") or ""
        location = personal_info.get("location") or personal_info.get("city") or ""
        pincode = personal_info.get("pincode") or personal_info.get("postal_code") or personal_info.get("zip") or ""
        country = personal_info.get("country") or personal_info.get("nationality") or ""

        primary_edu = edu_list[0] if edu_list else {}
        edu_degree = primary_edu.get("degree") or ""
        edu_inst = primary_edu.get("institution") or ""
        edu_field = primary_edu.get("field_of_study") or ""
        edu_start = primary_edu.get("start_date") or ""
        edu_end = primary_edu.get("end_date") or ""

        primary_work = work_exp[0] if work_exp else {}
        work_company = primary_work.get("company") or ""
        work_title = primary_work.get("job_title") or ""
        work_start = primary_work.get("start_date") or ""
        work_end = primary_work.get("end_date") or ""

        curr_sal = str(prompt_data.get("Current Salary") or resume.get("Current Salary") or "")
        exp_sal = str(prompt_data.get("Expected Salary") or resume.get("Expected Salary") or "")
        tot_exp = str(prompt_data.get("total_experience_years") or resume.get("year_of_exp") or "")

        avail = personal_info.get("available_to_join_days") or personal_info.get("notice_period") or ""
        m_avail_digits = re.search(r"\d+", str(avail))
        if m_avail_digits:
            notice_days = m_avail_digits.group(0)
        elif not any(w.get("currently_working") for w in work_exp):
            notice_days = "0"
        else:
            notice_days = "15"

        def _get_opt_text(o: Any) -> str:
            if isinstance(o, dict):
                return str(o.get("label") or o.get("text") or o.get("value") or "").strip()
            return str(o).strip()

        def _match_option_generically(opts: List[Any], target_val: str, fallback_words: Optional[List[str]] = None) -> str:
            if not opts:
                return str(target_val or "")
            target_str = str(target_val or "").strip()

            if not target_str:
                if fallback_words:
                    for fw in fallback_words:
                        for o in opts:
                            if fw.lower() in _get_opt_text(o).lower():
                                return _get_opt_text(o)
                return _get_opt_text(opts[0])

            filler = FormFiller.__new__(FormFiller)
            best_opt = None
            best_score = -1

            for o in opts:
                txt = _get_opt_text(o)
                score = filler.fuzzy_score(target_str, txt)
                if score > best_score:
                    best_score = score
                    best_opt = txt

            if best_opt and best_score >= 40:
                return best_opt

            if fallback_words:
                for fw in fallback_words:
                    for o in opts:
                        if fw.lower() in _get_opt_text(o).lower():
                            return _get_opt_text(o)

            for o in opts:
                txt = _get_opt_text(o)
                if re.match(r"^(other|any|none)\b", txt, re.I):
                    return txt
            return _get_opt_text(opts[0])

        if tag_type == "file" or any(w in q for w in ["resume", "cv", "curriculum vitae", "upload resume", "attach resume"]):
            ans = _find_default_resume_path()
            action = "upload"
        elif "first name" in q or "firstname" in q:
            ans = first_name
            action = "fill"
        elif "last name" in q or "lastname" in q:
            ans = last_name
            action = "fill"
        elif any(w in q for w in ["full name", "your name"]) or (q == "name"):
            ans = full_name
            action = "fill"
        elif any(w in q for w in ["email", "e-mail"]):
            ans = email
            action = "fill"
        elif any(w in q for w in ["phone", "mobile", "contact number", "cell"]) and not any(w in q for w in ["company", "designation", "title", "job", "notice", "education", "experience"]):
            clean_digits = re.sub(r"[^\d]", "", phone)
            ans = clean_digits[-10:] if len(clean_digits) >= 10 else clean_digits
            action = "fill"
        elif "github" in q:
            gh_user = re.sub(r"^(https?://)?(www\.)?github\.com/", "", github).strip("/")
            if "your-username" in q or "username" in q:
                ans = f"https://github.com/{gh_user}" if tag_type == "url" else gh_user
            else:
                ans = f"https://github.com/{gh_user}" if not github.startswith("http") else github
            action = "fill"
        elif "linkedin" in q:
            li_user = re.sub(r"^(https?://)?(www\.)?linkedin\.com/in/", "", linkedin).strip("/")
            if "your-name" in q or "username" in q:
                ans = f"https://www.linkedin.com/in/{li_user}" if tag_type == "url" else li_user
            else:
                ans = f"https://www.linkedin.com/in/{li_user}" if not linkedin.startswith("http") else linkedin
            action = "fill"
        elif any(w in q for w in ["website", "portfolio", "personal site", "project link"]):
            port_val = personal_info.get("portfolio") or personal_info.get("website") or ""
            if not port_val and github:
                port_val = f"https://github.com/{re.sub(r'^(https?://)?(www\.)?github\.com/', '', github).strip('/')}"
            ans = port_val or ""
            action = "fill"
        elif any(w in q for w in ["city", "current city", "current location"]):
            ans = location
            action = "fill"
        elif any(w in q for w in ["address", "street"]):
            ans = location
            action = "fill"
        elif any(w in q for w in ["state", "province"]):
            if options:
                ans = _match_option_generically(options, location)
                action = "select" if is_select else "radio"
            else:
                ans = location
                action = "fill"
        elif any(w in q for w in ["zip", "postal", "pincode"]):
            ans = pincode
            action = "fill"
        elif "country" in q:
            if options:
                ans = _match_option_generically(options, country, fallback_words=["india", "united states", "select"])
                action = "select" if is_select else "radio"
            else:
                ans = country
                action = "fill"
        elif any(w in q for w in ["hear", "referral", "source", "how did you find"]):
            source_pref = personal_info.get("how_did_you_hear") or "LinkedIn"
            if options:
                ans = _match_option_generically(options, source_pref, fallback_words=["linkedin", "job board", "online", "other"])
                action = "select" if is_select else "radio"
            else:
                ans = source_pref
                action = "fill"
        elif any(w in q for w in ["education_course", "course", "qualification_course"]):
            is_combobox = "combobox" in tag_type or is_select or "react-select" in q or "#education" in q
            if options:
                ans = _match_option_generically(options, edu_degree)
                action = "select"
            else:
                ans = edu_degree
                action = "select" if is_combobox else "fill"
        elif any(w in q for w in ["education_", "degree", "qualification"]) and any(k in q for k in ["#education", "select", "course", "degree"]):
            if options:
                ans = _match_option_generically(options, edu_degree)
                action = "select"
            else:
                ans = edu_degree
                action = "select"
        elif any(w in q for w in ["institute", "university", "college", "school"]):
            ans = edu_inst
            action = "fill"
        elif any(w in q for w in ["specialisation", "specialization", "branch", "stream", "major", "field of study"]):
            ans = edu_field
            action = "fill"
        elif any(w in q for w in ["company", "employer", "organization", "current employer"]):
            ans = work_company
            action = "fill"
        elif any(w in q for w in ["start", "from", "duration", "start_date", "start year"]) or "start" in q:
            is_edu_q = any(w in q for w in ["edu", "college", "school", "course", "degree", "university", "10th", "12th"])
            ans = edu_start if is_edu_q else work_start
            action = "fill"
        elif any(w in q for w in ["end", "completion", "to date", "end_date", "passing year", "relieving"]):
            is_edu_q = any(w in q for w in ["edu", "college", "school", "course", "degree", "university", "10th", "12th"])
            ans = (edu_end if str(edu_end).lower() not in {"present", "current"} else "") if is_edu_q else work_end
            action = "fill"
        elif any(w in q for w in ["title", "designation", "current role", "job title"]):
            if options:
                ans = _match_option_generically(options, work_title)
                action = "select" if is_select else "radio"
            else:
                ans = work_title
                action = "fill"
        elif any(w in q for w in ["notice", "joining", "available", "availability"]):
            if options:
                ans = _match_option_generically(options, notice_days, fallback_words=["imm", "0", "immediate", "15", "30"])
                action = "select" if is_select else "radio"
            else:
                ans = notice_days
                action = "fill"
        elif any(w in q for w in ["experience", "exp", "years"]):
            if options:
                ans = _match_option_generically(options, tot_exp, fallback_words=["0", "1", "entry", "fresher", "junior"])
                action = "select" if is_select else "radio"
            else:
                ans = tot_exp
                action = "fill"
        elif any(w in q for w in ["ctc", "salary", "compensation"]):
            ans = exp_sal if "expected" in q else curr_sal
            action = "fill"
        elif "gender" in q:
            if options:
                ans = _match_option_generically(options, gender, fallback_words=["male", "female", "prefer not to say"])
                action = "select" if is_select else "radio"
            else:
                ans = gender
                action = "fill"
        elif any(w in q for w in ["graduation", "degree", "authorized", "eligible", "relocate", "commute", "consent", "agree", "shift", "responsibilities", "prerequisites", "terms", "policy"]):
            if options:
                ans = next((_get_opt_text(o) for o in options 
                           if _get_opt_text(o).lower().startswith("yes") or "agree" in _get_opt_text(o).lower() or "confirm" in _get_opt_text(o).lower()), 
                          _get_opt_text(options[0]))
                action = "select" if is_select else "radio"
            elif tag_type == "checkbox":
                ans = "true"
                action = "check"
            else:
                ans = "Yes"
                action = "fill"
        elif any(w in q for w in ["sponsorship", "visa", "relative", "convicted", "disability", "felony"]):
            if options:
                ans = next((_get_opt_text(o) for o in options if _get_opt_text(o).lower().startswith("no")), 
                          _get_opt_text(options[-1]))
                action = "select" if is_select else "radio"
            elif tag_type == "checkbox":
                ans = "false"
                action = "check"
            else:
                ans = "No"
                action = "fill"
        elif options:
            action = "select" if is_select else "radio"
            ans = options[0].get("label") if isinstance(options[0], dict) else options[0]
        elif tag_type == "checkbox":
            action = "check"
            ans = "true"

        print(f"[MCPServer] Targeted solve for leftover tag '{q[:40]}': action='{action}', ans='{ans}', selector='{sel}'")
        res = await self.do_action(action=action, identifier=sel, data=ans, job_id=job_id)
        return {"tag": tag, "action": action, "answer": ans, "result": res}

    async def solve_leftovers_with_ai(self, leftovers: List[Dict[str, Any]], job_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Verify and resolve leftover application fields using Bedrock AI question understanding."""
        if not leftovers or not self.page:
            return leftovers

        print(f"[MCPServer] AI Answering Engine invoked for {len(leftovers)} leftover field(s)...")
        resume = self.get_resume_summary()

        enriched_leftovers = []
        for lf in leftovers:
            sel = lf.get("selector") or ""
            q = lf.get("question") or lf.get("label") or lf.get("name") or lf.get("placeholder") or ""
            opts = lf.get("options") or []
            tag = (lf.get("tag") or "").lower()
            c_type = (lf.get("type") or "text").lower()

            target_frame = self.page
            if sel:
                for f in self.page.frames:
                    try:
                        if await f.locator(sel).count() > 0:
                            target_frame = f
                            break
                    except Exception:
                        continue

            try:
                live_info = await target_frame.evaluate("""sel => {
                    let qText = '';
                    let liveOpts = [];
                    const el = sel ? document.querySelector(sel) : null;
                    const container = el ? (el.closest('.ashby-application-form-field-entry, [class*="field-entry" i], [class*="question" i], [class*="step" i], form, main') || document) : document;
                    const h = container.querySelector('h1, h2, h3, h4, [class*="title" i], [class*="question" i], [class*="heading" i], [class*="label" i]');
                    if (h) qText = (h.innerText || '').trim();
                    const choiceBtns = Array.from(container.querySelectorAll('button[role="radio"], button.choice, [role="radio"], input[type="radio"], button[data-option], .ashby-application-form-input-yesno-option'));
                    if (choiceBtns.length > 0) {
                        liveOpts = choiceBtns.map(b => (b.getAttribute('data-option') || b.innerText || b.value || '').trim()).filter(Boolean);
                    }
                    return { question: qText, options: liveOpts };
                }""", sel)
                if not q or len(q) < 4 or q in {"company", "text", "url", "email", "phone"}:
                    if live_info.get("question"):
                        q = live_info["question"]
                if not opts and live_info.get("options"):
                    opts = live_info["options"]
            except Exception:
                pass

            enriched_leftovers.append({
                "element_index": lf.get("element_index", lf.get("_element_index", 0)),
                "selector": sel,
                "question": q,
                "placeholder": lf.get("placeholder") or "",
                "type": c_type,
                "control_kind": lf.get("control_kind") or lf.get("control_type") or ("radio" if opts else "fill"),
                "options": opts,
                "raw_field": lf
            })

        resume_json = json.dumps(resume, ensure_ascii=False, default=str)
        questions_json = json.dumps([
            {
                "element_index": x["element_index"],
                "selector": x["selector"],
                "question": x["question"],
                "placeholder": x["placeholder"],
                "type": x["type"],
                "control_kind": x["control_kind"],
                "options": x["options"][:10] if isinstance(x["options"], list) else []
            }
            for x in enriched_leftovers
        ], ensure_ascii=False, indent=2)

        prompt = f"""You are an advanced AI job-application question answering engine.
Answer the application questions based on the candidate's resume and career context.

CANDIDATE RESUME:
{resume_json}

QUESTIONS:
{questions_json}

INSTRUCTIONS:
1. Understand the question and what format is required:
   - For GitHub profile questions: If input expects a full URL (type='url' or placeholder has http/github.com), return complete URL from applicant data. If it expects only username, return only the username. NEVER output LinkedIn.
   - For LinkedIn profile questions: Return the complete LinkedIn URL from applicant data (or username if only username requested). NEVER output GitHub.
   - For Website / Portfolio / previous projects (e.g. 'Have you built a website before?'): Use the candidate's project repository or portfolio URL from applicant data. NEVER output LinkedIn.
   - For choices / radio options (e.g. Gender, Experience level, Willingness to relocate, Immediate joining): Pick the exact matching option label based on applicant data.
   - For tech stack / tools questions: Output technologies and tools documented in applicant data projects or skills.
2. Action must be 'fill', 'select', 'radio', or 'check'.
3. Return ONLY valid JSON:
{{"answers": [{{"element_index": 0, "selector": "...", "answer": "...", "action": "fill|radio|select|check", "option_label": "..."}}]}}
"""
        ai_answers = []
        try:
            if not self.automatic_filling:
                self.automatic_filling = FormFiller(page=self.page, url=self.page.url)
            res = await asyncio.to_thread(self.automatic_filling.call_bedrock, prompt)
            if isinstance(res, dict) and "answers" in res:
                ai_answers = res["answers"]
            elif isinstance(res, list):
                ai_answers = res
        except Exception as e:
            print(f"[MCPServer] AI Answering exception: {e}")
            return leftovers

        still_unresolved = []
        answers_by_idx = {a.get("element_index"): a for a in ai_answers if isinstance(a, dict)}

        for item in enriched_leftovers:
            idx = item["element_index"]
            sel = item["selector"]
            raw_lf = item["raw_field"]
            ans_data = answers_by_idx.get(idx)

            if not ans_data:
                still_unresolved.append(raw_lf)
                continue

            val = str(ans_data.get("answer") or ans_data.get("option_label") or "").strip()
            act = ans_data.get("action", "fill").lower()

            if not val or val.lower() in {"skip", "na", "n/a", "none"}:
                still_unresolved.append(raw_lf)
                continue

            success = False
            try:
                if act in {"radio", "select"} or item.get("options"):
                    v_low = val.lower().strip()
                    choice_sels = [
                        f"button[data-option='{v_low}']",
                        f"button:has-text('{val}')",
                        f"[role='radio']:has-text('{val}')",
                        f".choice:has-text('{val}')",
                        f"label:has-text('{val}')",
                        f"input[type='radio'][value='{val}']",
                    ]
                    val_clean = re.sub(r"^[A-Z]\s+", "", val).strip()
                    if val_clean != val:
                        choice_sels.extend([
                            f"button:has-text('{val_clean}')",
                            f"[role='radio']:has-text('{val_clean}')",
                            f".choice:has-text('{val_clean}')",
                        ])
                    all_frames = [self.page] + [f for f in self.page.frames if not (self.dom_extractor and self.dom_extractor._is_ignorable_frame(f))]
                    for cs in choice_sels:
                        for ctx in all_frames:
                            try:
                                c_loc = ctx.locator(cs).first
                                if await c_loc.count() > 0 and await c_loc.is_visible():
                                    await c_loc.click(timeout=3000)
                                    success = True
                                    print(f"[MCPServer] AI Answer applied choice: '{val}' via {cs}")
                                    break
                            except Exception:
                                continue
                        if success:
                            break

                if not success and sel:
                    all_frames = [self.page] + [f for f in self.page.frames if not (self.dom_extractor and self.dom_extractor._is_ignorable_frame(f))]
                    for ctx in all_frames:
                        try:
                            loc = ctx.locator(sel).first
                            if await loc.count() > 0 and await loc.is_visible():
                                await loc.fill(val)
                                success = True
                                print(f"[MCPServer] AI Answer filled input: '{val}' into {sel}")
                                break
                        except Exception:
                            continue

                if not success and act == "check" and sel:
                    all_frames = [self.page] + [f for f in self.page.frames if not (self.dom_extractor and self.dom_extractor._is_ignorable_frame(f))]
                    for ctx in all_frames:
                        try:
                            loc = ctx.locator(sel).first
                            if await loc.count() > 0:
                                await loc.check(force=True)
                                success = True
                                break
                        except Exception:
                            continue

                if success:
                    self.state_manager.record_field(job_id, idx, item["question"], sel, val, "ai", True)
                else:
                    still_unresolved.append(raw_lf)
            except Exception as exc:
                print(f"[MCPServer] Failed applying AI answer to {sel}: {exc}")
                still_unresolved.append(raw_lf)

        return still_unresolved

    async def perceive_ui_state(self) -> Dict[str, Any]:
        """Perceive complete real-time UI state: active tab, open popups/modals, and leftover tags."""
        await self._ensure_browser_session()
        await self.sync_active_page()
        
        popups = await self.page.evaluate("""() => {
            const results = [];
            const cookieSels = [
                '#onetrust-accept-btn-handler', 'button[id*="cookie-accept" i]',
                'button:has-text("Accept all cookies")', 'button:has-text("Accept Cookies")',
                'button:has-text("Accept All")', '.cc-accept'
            ];
            for (const sel of cookieSels) {
                try {
                    const el = document.querySelector(sel);
                    if (el && el.offsetParent !== null) results.push({type: 'cookie_banner', selector: sel});
                } catch(e) {}
            }
            const dialogs = [...document.querySelectorAll('dialog[open], [role="dialog"], .modal.show, .modal.in, div[class*="modal"]')];
            for (const d of dialogs) {
                if (d.offsetParent === null) continue;
                const txt = (d.innerText || '').toLowerCase();
                if (txt.includes('linkedin')) results.push({type: 'linkedin_prompt', text: txt.slice(0, 80)});
                else if (txt.includes('job alert') || txt.includes('subscribe')) results.push({type: 'marketing_modal', text: txt.slice(0, 80)});
                else if (txt.includes('confirm') || txt.includes('are you sure')) results.push({type: 'confirmation_modal', text: txt.slice(0, 80)});
                else results.push({type: 'generic_modal', text: txt.slice(0, 80)});
            }
            return results;
        }""")

        leftover_tags = await self.detect_live_leftover_tags()

        return {
            "status": "success",
            "active_tab": {
                "url": self.page.url,
                "title": await self.page.title(),
            },
            "popups_detected": popups or [],
            "leftover_tags": leftover_tags or [],
            "ready_to_submit": len(leftover_tags or []) == 0 and len([p for p in (popups or []) if p.get('type') != 'cookie_banner']) == 0
        }

    async def perceive_solve_and_submit(self, job_id: Optional[str] = None) -> Dict[str, Any]:
        """Perceive UI state, dismiss popups/LinkedIn overlays, solve any leftover tags, and submit fast."""
        await self._ensure_browser_session()
        await self.sync_active_page()
        
        handled_popups = await self.handle_active_popups()
        
        leftovers = await self.detect_live_leftover_tags()
        solved_tags = []
        if leftovers:
            print(f"[MCPServer] Detected {len(leftovers)} leftover/invalid tag(s) on live UI. Solving targetedly...")
            for tag in leftovers:
                s_res = await self.solve_live_leftover_tag(tag, job_id=job_id)
                solved_tags.append(s_res)
            await self.page.wait_for_timeout(400)

        print("[MCPServer] Triggering fast submit...")
        submit_res = await self._submit_form(job_id=job_id)
        await self.handle_active_popups()
        
        return {
            "status": submit_res.get("status"),
            "message": submit_res.get("message"),
            "url": self.page.url,
            "handled_popups": handled_popups,
            "solved_tags": solved_tags,
            "submit_result": submit_res
        }
    
    """-----------------------------------------tools usage function --------------------------------------------------------------------------"""
  
    def get_element(self,target):
        field_locator = self._normalize_locator(target.get("locator") or "")

        if not field_locator:
            raise ValueError("Target locator missing")

        frame_locator = target.get("frame")

        if frame_locator is not None and frame_locator != "":
            frame_ref = frame_locator
            
            try:
                if isinstance(frame_ref, int) or (
                    isinstance(frame_ref, str) and frame_ref.strip().isdigit()
                ):
                    idx = int(frame_ref)
                    frames = self.page.frames
                    if idx < 0 or idx >= len(frames):
                        raise ValueError(f"Frame index {idx} out of range (0..{len(frames)-1})")
                    return frames[idx].locator(field_locator).first
            except ValueError:
                raise
            except Exception as e:
                raise ValueError(f"Invalid frame index {frame_ref!r}: {e}") from e

            return (
                self.page
                .frame_locator(str(frame_ref))
                .locator(field_locator)
                .first
            )

        return self.page.locator(field_locator).first
     
    async def resolve_element(self, target):
        
        field_locator = self._normalize_locator(target.get("locator") or "")
        if not field_locator:
            raise ValueError("Target locator missing")

        frame_index = target.get("frame_index")
        if frame_index is not None and str(frame_index).strip() != "":
            idx = int(frame_index)
            frames = self.page.frames
            if 0 <= idx < len(frames):
                el = frames[idx].locator(field_locator).first
                if await el.count() > 0:
                    return el
        try:
            element = self.get_element(target)
            if await element.count() > 0:
                return element
        except Exception:
            pass

        for frame in self.page.frames:
            if frame == self.page.main_frame:
                continue
            el = frame.locator(field_locator).first
            if await el.count() > 0:
                return el

        return self.page.locator(field_locator).first 
 

    async def _resolve_file_input(self, element, hint) :
        page = self.page
        hint = (hint or "").lower()

        try:
            tag = await element.evaluate("el => el.tagName.toLowerCase()")
            input_type = await element.evaluate("el => el.type || ''")
            if tag == "input" and input_type == "file":
                return element
        except Exception:
            pass

        loc_str = hint if hint.startswith("input") else ""
        if "input" in (loc_str or "") and "file" in (loc_str or ""):
            all_inputs = await self._find_file_inputs_all_frames(hint)
            if all_inputs:
                frame, index, _, _ = all_inputs[0]
                return frame.locator("input[type='file']").nth(index)

        # Button/label inside same upload widget → sibling or parent file input
        try:
            nearby = element.locator(
                "xpath=ancestor-or-self::*[.//input[@type='file']][1]//input[@type='file']"
            ).first
            if await nearby.count():
                return nearby
        except Exception:
            pass

        try:
            sibling = element.locator(
                "xpath=following::input[@type='file'][1] | preceding::input[@type='file'][1]"
            ).first
            if await sibling.count():
                return sibling
        except Exception:
            pass

        # label[for] → file input id
        try:
            for_id = await element.get_attribute("for")
            if not for_id:
                label = element.locator("xpath=ancestor::label[1]")
                if await label.count():
                    for_id = await label.first.get_attribute("for")
            if for_id:
                linked = page.locator(f"#{for_id}")
                if await linked.count():
                    tag = await linked.first.evaluate("el => el.tagName.toLowerCase()")
                    itype = await linked.first.evaluate("el => el.type || ''")
                    if tag == "input" and itype == "file":
                        return linked.first
        except Exception:
            pass

        # Search all frames by hint keywords
        all_inputs = await self._find_file_inputs_all_frames(hint)
        if all_inputs:
            frame, index, _, _ = all_inputs[0]
            return frame.locator("input[type='file']").nth(index)

        keywords = []
        if hint:
            keywords.extend(h for h in hint.replace("_", " ").split() if h)
        for word in ("resume", "cv", "cover", "letter", "attachment", "file"):
            if word in hint and word not in keywords:
                keywords.append(word)

        selectors = []
        for kw in keywords:
            selectors.extend([
                f"input[type='file'][name*='{kw}' i]",
                f"input[type='file'][id*='{kw}' i]",
                f"input[type='file'][aria-label*='{kw}' i]",
            ])
        selectors.extend([
            "input[type='file'][name*='resume' i]",
            "input[type='file'][id*='resume' i]",
            "input[type='file'][name*='cv' i]",
            "input[type='file'][id*='cv' i]",
            "input[type='file'][name*='cover' i]",
            "input[type='file'][id*='cover' i]",
            "input[type='file']",
        ])

        seen = set()
        for sel in selectors:
            if sel in seen:
                continue
            seen.add(sel)
            loc = page.locator(sel)
            count = await loc.count()
            for i in range(count):
                candidate = loc.nth(i)
                try:
                    if await candidate.get_attribute("disabled") is not None:
                        continue
                    return candidate
                except Exception:
                    continue
        return None    
    
    
    async def upload_file_direct(self,selector: str,file_path: str):
        
        try:
            if not os.path.isfile(file_path):
                return {
                    "status": "error",
                    "problem": f"File does not exist: {file_path}"
                }

            target = {"locator": selector}
            element = await self.resolve_element(target)

            if await element.count() == 0:
                return {
                    "status": "error",
                    "problem": f"Element not found: {selector}"
                }

            file_input = await self._resolve_file_input(
                element,
                hint=selector
            )

            if file_input is None:
                return {
                    "status": "error",
                    "problem": (
                        f"Could not resolve input[type='file'] "
                        f"from selector: {selector}"
                    )
                }
                
            await file_input.set_input_files(file_path)

            uploaded_files = await file_input.evaluate(
                "el => Array.from(el.files || []).map(f => f.name)"
            )

            if not uploaded_files:
                return {
                    "status": "error",
                    "problem": "Upload attempted, but no file was detected"
                }

            return {
                "status": "success",
                "action": "upload",
                "selector": selector,
                "file_path": os.path.abspath(file_path),
                "uploaded_files": uploaded_files,
                "verified": True,
                "message": "File assigned successfully"
            }

        except Exception as e:
            return {
                "status": "error",
                "problem": f"File upload failed: {e}"
            }    
    
    async def _find_frame_with_element(self, selector: str):
        """Return page or child frame that contains selector."""
        norm = self._normalize_locator(selector)
        if not norm:
            return self.page
        try:
            if await self.page.locator(norm).count() > 0:
                return self.page
        except Exception:
            pass
        for frame in self.page.frames:
            if frame == self.page.main_frame:
                continue
            try:
                if await frame.locator(norm).count() > 0:
                    return frame
            except Exception:
                continue
        return self.page

    async def _smart_select_custom(
        self,
        element,
        context,
        wanted: str,
        target
    ):
        wanted = str(wanted).strip()
        field_locator = target.get("locator", "")

        if not wanted:
            return {"status": "error", "message": "Empty select value", "target": target}

        try:
            await element.scroll_into_view_if_needed(timeout=3000)
        except Exception:
            pass

        try:
            tag = await element.evaluate("el => el.tagName.toLowerCase()")
            role = await element.get_attribute("role") or ""
        except Exception:
            tag, role = "", ""

        if tag == "select":
            try:
                await element.select_option(label=wanted, timeout=3000)
            except Exception:
                try:
                    await element.select_option(value=wanted, timeout=3000)
                except Exception:
                    pass

            ok, actual, label = await self._verify_dropdown_selection(element, wanted)
            if ok:
                return {
                    "status": "success", "action": "select", "method": "native_select",
                    "target": target, "expected": wanted, "actual_value": actual,
                    "actual_label": label, "verified": True,
                    "message": f"Selected {label or actual}",
                }

        # Open the custom dropdown.
        try:
            await element.click(timeout=3000)
        except Exception:
            try:
                await element.click(force=True, timeout=3000)
            except Exception:
                pass

        # Type only search keyword into searchable controls.
        main_term = re.split(r"\s+(?:in|with|major|specialization|stream|branch)\s+|[\(\[\{]", wanted, flags=re.I)[0].strip()
        search_kw = main_term if main_term else wanted
        try:
            input_type = await element.get_attribute("type") or ""
            editable = tag in ("input", "textarea") or await element.get_attribute("contenteditable") == "true"
            if editable and input_type not in ("checkbox", "radio", "file", "hidden"):
                await element.fill("")
                await element.type(search_kw, delay=25)
        except Exception:
            pass

        try:
            await context.wait_for_timeout(400)
        except Exception:
            pass

        # Search visible options and prefer exact matches.
        selectors = [
            '[role="option"]',
            '[role="listbox"] [role="option"]',
            'li[role="option"]',
            '.react-select__option',
            '.select__option',
            '.select-option',
            '.dropdown-option',
            '.option',
            '[class*="-option"]',
            '[id*="-option-"]',
        ]

        best_option, best_text, best_score = None, "", 0
        filler = FormFiller.__new__(FormFiller)

        for selector in selectors:
            try:
                options = context.locator(selector)
                count = min(await options.count(), 80)
            except Exception:
                continue

            for i in range(count):
                option = options.nth(i)
                try:
                    if not await option.is_visible():
                        continue
                    if await option.get_attribute("aria-disabled") == "true":
                        continue

                    text = (await option.inner_text()).strip()
                    if not text:
                        continue

                    score = filler.fuzzy_score(wanted, text)
                    if score > best_score:
                        best_option, best_text, best_score = option, text, score

                    if score >= 90:
                        break
                except Exception:
                    continue

            if best_score >= 90:
                break

        clicked = False

        if best_option is not None and best_score >= 60:
            try:
                await best_option.click(timeout=3000)
                clicked = True
            except Exception:
                try:
                    await best_option.click(force=True, timeout=3000)
                    clicked = True
                except Exception:
                    pass

        # Fallback to accessible exact option/text.
        if not clicked:
            for attempt in (
                lambda: context.get_by_role("option", name=wanted, exact=True).first,
                lambda: context.get_by_text(wanted, exact=True).last,
            ):
                try:
                    candidate = attempt()
                    if await candidate.count() and await candidate.is_visible():
                        await candidate.click(timeout=3000)
                        clicked = True
                        break
                except Exception:
                    continue

        # Keyboard fallback.
        if not clicked:
            try:
                await element.press("ArrowDown", timeout=2000)
                await element.press("Enter", timeout=2000)
            except Exception:
                pass

        ok, actual, label = await self._verify_dropdown_selection(element, wanted)

        if not ok and best_text:
            ok, actual, label = await self._verify_dropdown_selection(element, best_text)

        return {
            "status": "success" if ok else "error",
            "action": "select",
            "method": "smart_select",
            "target": target,
            "expected": wanted,
            "matched_option": best_text or None,
            "match_score": best_score,
            "actual_value": actual,
            "actual_label": label,
            "verified": ok,
            "message": (
                f"Selected {label or best_text or wanted!r}"
                if ok
                else f"Could not verify dropdown selection for {wanted!r}"
            ),
        }

    async def _verify_dropdown_selection(self, element, expected):
        expected = (expected or "").strip()
        try:
            await element.blur()
        except Exception:
            pass
        try:
            await self.page.wait_for_timeout(200)
        except Exception:
            pass

        actual = ""
        label = ""
        try:
            actual = await element.input_value()
        except Exception:
            pass
        if not actual:
            try:
                actual = await element.get_attribute("value") or ""
            except Exception:
                pass

        try:
            label = await element.evaluate("el => el.options && el.selectedIndex >= 0 ? (el.options[el.selectedIndex].text || '') : ''")
        except Exception:
            label = ""

        if not label:
            try:
                label = (await element.inner_text()).strip()
            except Exception:
                label = actual

        if not expected:
            return True, actual, label

        exp = expected.lower().replace("’", "'").strip()
        act = actual.lower().replace("’", "'").strip()
        lbl = label.lower().replace("’", "'").strip()

        ok = (
            exp == act or exp in act or act in exp
            or exp == lbl or exp in lbl or lbl in exp
            or any(w in lbl for w in exp.split() if len(w) > 3)
            or any(w in exp for w in lbl.split() if len(w) > 3)
        )
        return ok, actual, label
        
    async def do_action(
        self,
        action: Optional[str] = None,
        identifier: Any = None,
        data: Any = None,
        selector: Optional[str] = None,
        action_type: Optional[str] = None,
        target: Any = None,
        value: Any = None,
        locator: Optional[str] = None,
        job_id: Optional[str] = None,
    ):
        try:
            await self._ensure_browser_session()
            if not self.page:
                return {"status": "error", "message": "No active page. Call open_page first."}

            action_type = (action or action_type or "").strip().lower()
            value = data if data is not None else value
            raw_target = identifier if identifier is not None else (selector if selector is not None else (locator if locator is not None else target))

            elem_index = None
            if isinstance(raw_target, (int, float)) or (isinstance(raw_target, str) and raw_target.strip().isdigit()):
                elem_index = int(raw_target)
                matched = None
                if self.last_elements and elem_index < len(self.last_elements):
                    matched = self.last_elements[elem_index]
                if not matched and Path("elements.json").exists():
                    try:
                        with open("elements.json", "r", encoding="utf-8") as f:
                            raw_el = json.load(f)
                        if elem_index < len(raw_el):
                            matched = raw_el[elem_index]
                    except Exception:
                        pass
                if not matched and Path("important_data_fields.json").exists():
                    try:
                        with open("important_data_fields.json", "r", encoding="utf-8") as f:
                            idata = json.load(f)
                        flds = idata.get("fields", []) if isinstance(idata, dict) else idata
                        matched = next((f for f in flds if f.get("element_index") == elem_index or f.get("_element_index") == elem_index), None)
                    except Exception:
                        pass

                if matched:
                    target = {
                        "locator": matched.get("selector") or matched.get("id") or f"[data-jobfill-scan-idx='{elem_index}']",
                        "frame_index": matched.get("frame_index", 0),
                        "hint": matched.get("question") or matched.get("label") or "",
                    }
                else:
                    target = {"locator": f"[data-jobfill-scan-idx='{elem_index}'], [data-index='{elem_index}']", "frame_index": 0}
            elif isinstance(raw_target, dict):
                target = raw_target
            elif isinstance(raw_target, str):
                target = {"locator": raw_target.strip(), "frame_index": 0}
            else:
                target = {"locator": ""}

            field_locator = target.get("locator") or ""

            if action_type == "upload":
                upload_file = str(value) if value else _find_default_resume_path()
                direct = await self.upload_file_direct(
                    selector=field_locator,
                    file_path=upload_file,
                )
                direct["target"] = target
                if direct.get("status") == "success":
                    self.state_manager.record_field(
                        job_id=job_id,
                        element_index=elem_index if elem_index is not None else field_locator,
                        question=target.get("hint") or field_locator,
                        selector=field_locator,
                        value=upload_file,
                        method="manual_upload",
                        verified=True,
                    )
                return direct

            element = await self.resolve_element(target)

            if await element.count() == 0:
                return {
                    "status": "error",
                    "message": f"Element not found for identifier: {raw_target}",
                    "target": target,
                    "action": action_type,
                }

            if action_type in {"click", "press_click"}:
                try:
                    await element.scroll_into_view_if_needed(timeout=2000)
                except Exception:
                    pass
                try:
                    await element.click(timeout=5000)
                except Exception:
                    await element.click(force=True, timeout=5000)
                return {
                    "status": "success",
                    "action": "click",
                    "target": target,
                    "verified": True,
                    "message": f"Clicked element {field_locator}",
                }

            elif action_type == "press":
                key = str(value or "Enter")
                await element.press(key)
                return {
                    "status": "success",
                    "action": "press",
                    "key": key,
                    "target": target,
                    "verified": True,
                    "message": f"Pressed key {key}",
                }

            elif action_type == "fill":
                if value is None:
                    return {"status": "error", "message": "Missing value for fill"}

                value_str = str(value)
                try:
                    await element.scroll_into_view_if_needed(timeout=1000)
                except Exception:
                    pass

                try:
                    await element.evaluate("el => el.removeAttribute('readonly')")
                except Exception:
                    pass

                filled = False
                try:
                    await element.fill(value_str, timeout=1500)
                    filled = True
                except Exception:
                    try:
                        await element.click(timeout=1000)
                        await element.press_sequentially(value_str, delay=5)
                        filled = True
                    except Exception:
                        pass

                is_loc = any(w in str(field_locator).lower() for w in ["location", "city", "address", "place"])
                is_combobox = False
                try:
                    is_combobox = (await element.get_attribute("role")) == "combobox" or bool(await element.get_attribute("aria-controls"))
                except Exception:
                    pass

                if is_loc or is_combobox:
                    if not self.automatic_filling or self.automatic_filling.page != self.page:
                        self.automatic_filling = FormFiller(page=self.page, url=self.current_page_url or self.page.url)
                    try:
                        r_data = self.automatic_filling.load_json("resume_imp_content.json") if Path("resume_imp_content.json").exists() else {}
                        r_idx = self.automatic_filling.build_resume_index(r_data)
                    except Exception:
                        r_idx = None
                    try:
                        await self.automatic_filling.select_best_autocomplete_suggestion(
                            self.page, element, value_str, resume_idx=r_idx
                        )
                    except Exception:
                        pass
                else:
                    try:
                        await element.evaluate("""(el, val) => {
                            try {
                                const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')?.set
                                    || Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value')?.set;
                                if (setter) setter.call(el, val);
                                else el.value = val;
                            } catch (e) {}
                            el.dispatchEvent(new Event('input', {bubbles: true}));
                            el.dispatchEvent(new Event('change', {bubbles: true}));
                            el.dispatchEvent(new Event('blur', {bubbles: true}));
                        }""", value_str)
                    except Exception:
                        pass

                try:
                    ok_btn = self.page.locator('button:has-text("OK")').first
                    if await ok_btn.count() > 0 and await ok_btn.is_visible():
                        await ok_btn.click()
                        await self.page.wait_for_timeout(200)
                except Exception:
                    pass

                actual_value = ""
                try:
                    actual_value = await element.input_value()
                except Exception:
                    pass
                ok = actual_value == value_str or value_str.lower() in actual_value.lower()
                if not ok:
                    try:
                        await element.fill("")
                        await element.type(value_str, delay=25)
                        await self.page.wait_for_timeout(150)
                        try:
                            await element.press("Enter")
                        except Exception:
                            pass
                        try:
                            await element.press("Tab")
                        except Exception:
                            pass
                        actual_value = await element.input_value()
                        ok = actual_value == value_str or value_str.lower() in actual_value.lower()
                    except Exception:
                        pass

                if ok:
                    self.state_manager.record_field(
                        job_id=job_id,
                        element_index=elem_index if elem_index is not None else field_locator,
                        question=target.get("hint") or field_locator,
                        selector=field_locator,
                        value=value_str,
                        method="manual_fill",
                        verified=True,
                    )

                return {
                    "status": "success" if ok else "error",
                    "action": "fill",
                    "target": target,
                    "expected": value_str,
                    "actual": actual_value,
                    "verified": ok,
                    "message": "Fill executed" if ok else "Field filled but value mismatch",
                }

            elif action_type == "clear":
                try:
                    await element.click(timeout=3000)
                except Exception:
                    pass
                await element.fill("")
                return {
                    "status": "success",
                    "action": "clear",
                    "target": target,
                    "verified": True,
                    "message": "Cleared input",
                }

            elif action_type == "check":
                try:
                    await element.check(timeout=3000)
                except Exception:
                    try:
                        await element.check(force=True, timeout=3000)
                    except Exception:
                        try:
                            await element.click(force=True, timeout=3000)
                        except Exception:
                            await element.evaluate("el => { el.checked = true; el.dispatchEvent(new Event('input', {bubbles: true})); el.dispatchEvent(new Event('change', {bubbles: true})); }")
                checked = False
                try:
                    checked = await element.is_checked()
                except Exception:
                    pass
                if not checked:
                    try:
                        checked = await element.evaluate("el => !!el.checked")
                    except Exception:
                        pass
                if checked:
                    self.state_manager.record_field(
                        job_id=job_id,
                        element_index=elem_index if elem_index is not None else field_locator,
                        question=target.get("hint") or field_locator,
                        selector=field_locator,
                        value=True,
                        method="manual_check",
                        verified=True,
                    )
                return {
                    "status": "success" if checked else "error",
                    "action": "check",
                    "target": target,
                    "checked": checked,
                    "verified": checked,
                    "message": "Check executed" if checked else "Checkbox was not checked",
                }

            elif action_type == "uncheck":
                await element.uncheck()
                checked = await element.is_checked()
                ok = not checked
                return {
                    "status": "success" if ok else "error",
                    "action": "uncheck",
                    "target": target,
                    "checked": checked,
                    "verified": ok,
                    "message": "Uncheck executed" if ok else "Checkbox remained checked",
                }

            elif action_type == "select":
                if value is None:
                    return {"status": "error", "message": "Missing value for select"}

                wanted = str(value).strip()
                tag = await element.evaluate("el => el.tagName.toLowerCase()")

                if tag == "select":
                    try:
                        await element.select_option(label=wanted, timeout=2000)
                    except Exception:
                        try:
                            await element.select_option(value=wanted, timeout=2000)
                        except Exception:
                            # Fuzzy matching for curly quotes, casing, or substrings
                            try:
                                await element.evaluate("""(el, target) => {
                                    const norm = s => (s || '').toLowerCase().replace(/['’]/g, "'").replace(/\\s+/g, ' ').trim();
                                    const t = norm(target);
                                    let best = -1;
                                    for (let i = 0; i < el.options.length; i++) {
                                        const opt = el.options[i];
                                        const ot = norm(opt.text);
                                        const ov = norm(opt.value);
                                        if (ot === t || ov === t || ot.includes(t) || t.includes(ot)) {
                                            best = i;
                                            break;
                                        }
                                    }
                                    if (best >= 0) {
                                        el.selectedIndex = best;
                                        el.dispatchEvent(new Event('input', {bubbles: true}));
                                        el.dispatchEvent(new Event('change', {bubbles: true}));
                                    }
                                }""", wanted)
                            except Exception:
                                pass

                    ok, actual, label = await self._verify_dropdown_selection(element, wanted)
                    if ok:
                        self.state_manager.record_field(
                            job_id=job_id,
                            element_index=elem_index if elem_index is not None else field_locator,
                            question=target.get("hint") or field_locator,
                            selector=field_locator,
                            value=wanted,
                            method="manual_select",
                            verified=True,
                        )
                        return {
                            "status": "success",
                            "action": "select",
                            "method": "native_select",
                            "target": target,
                            "expected": wanted,
                            "actual_value": actual,
                            "actual_label": label,
                            "verified": True,
                            "message": f"Selected {label or actual}",
                        }

                # Custom dropdown fallback
                context = await self._find_frame_with_element(field_locator)
                fallback = await self._smart_select_custom(element, context, wanted, target)
                if fallback.get("verified"):
                    self.state_manager.record_field(
                        job_id=job_id,
                        element_index=elem_index if elem_index is not None else field_locator,
                        question=target.get("hint") or field_locator,
                        selector=field_locator,
                        value=wanted,
                        method="manual_custom_select",
                        verified=True,
                    )
            elif action_type in {"radio", "radio_select"}:
                wanted = str(value or "Yes").strip()
                try:
                    await element.scroll_into_view_if_needed(timeout=2000)
                except Exception:
                    pass
                is_radio = False
                try:
                    is_radio = await element.evaluate("el => el.type === 'radio'")
                except Exception:
                    pass
                if is_radio:
                    await element.check(force=True, timeout=3000)
                    checked = await element.is_checked()
                else:
                    checked = await element.evaluate("""(el, target) => {
                        const norm = s => (s || '').toLowerCase().replace(/\\s+/g, ' ').trim();
                        const t = norm(target);
                        const name = el.getAttribute('name');
                        const radios = name ? [...document.querySelectorAll(`input[type="radio"][name="${CSS.escape(name)}"]`)] : [...el.querySelectorAll('input[type="radio"]')];
                        for (const r of radios) {
                            const lab = r.id ? document.querySelector(`label[for="${CSS.escape(r.id)}"]`) : r.closest('label');
                            const txt = norm((r.value || '') + ' ' + (lab ? lab.innerText : ''));
                            if (txt === t || txt.includes(t) || t.includes(txt) || (t === 'yes' && (r.value === 'true' || txt.includes('yes'))) || (t === 'no' && (r.value === 'false' || txt.includes('no')))) {
                                r.checked = true;
                                r.dispatchEvent(new Event('input', {bubbles: true}));
                                r.dispatchEvent(new Event('change', {bubbles: true}));
                                r.click();
                                return true;
                            }
                        }
                        if (radios.length > 0) {
                            radios[0].checked = true;
                            radios[0].click();
                            return true;
                        }
                        return false;
                    }""", wanted)
                if checked:
                    self.state_manager.record_field(
                        job_id=job_id,
                        element_index=elem_index if elem_index is not None else field_locator,
                        question=target.get("hint") or field_locator,
                        selector=field_locator,
                        value=wanted,
                        method="manual_radio",
                        verified=True,
                    )
                return {
                    "status": "success" if checked else "error",
                    "action": "radio",
                    "target": target,
                    "expected": wanted,
                    "verified": checked,
                    "message": f"Selected radio option '{wanted}'" if checked else "Radio option not selected",
                }

            else:
                return {
                    "status": "error",
                    "message": f"Unknown action: {action_type}",
                    "supported_actions": ["fill", "select", "radio", "check", "uncheck", "upload", "click", "press", "clear"],
                }

        except Exception as e:
            return {
                "status": "error",
                "message": f"Action {action_type} failed: {e}",
                "error": str(e),
            }

    async def get_raw_html(self):
        try:
            await self._ensure_browser_session()
            if not self.page:
                return {"error": "No active page"}
            await self.page.wait_for_load_state("domcontentloaded", timeout=15000)
            content = await self.page.content()
            try:
                with open("page_debug.html", "w", encoding="utf-8") as f:
                    f.write(content)
            except Exception:
                pass
            outline = await self.page.evaluate("""() => {
                const forms = [...document.querySelectorAll('form')].map((f, i) => {
                    const inputs = [...f.querySelectorAll('input, select, textarea')].map(el => {
                        return `${el.tagName.toLowerCase()}[name="${el.name || ''}"][type="${el.type || ''}"]`;
                    });
                    return { form_index: i, input_count: inputs.length, sample_inputs: inputs.slice(0, 8) };
                });
                return { total_forms: forms.length, forms: forms };
            }""")
            return {
                "status": "success",
                "html": content,
                "length": len(content),
                "message": "Full HTML returned. Also saved to 'page_debug.html'.",
                "dom_outline": outline,
            }
        except Exception as e:
            return {"error": str(e)}

    async def _find_next_button(self) -> Optional[Any]:
        """Locate multi-page Next / Continue button.
        Searches inside open modals/dialogs first, then falls back to page-level.
        Iterates all matches (not just .first) to find a visible, enabled button.
        """
        if not self.page:
            return None

        # Bad text patterns that should NOT be treated as Next buttons
        BAD_WORDS = {"submit", "apply", "finish", "done", "back", "previous", "cancel", "close", "skip"}
        # Text patterns that ARE next/continue buttons
        GOOD_WORDS = {"ok", "next", "continue", "save", "start", "proceed"}

        next_selectors = [
            # Ant Design modal Next button (CVViZ pattern)
            '.ant-modal button:has-text("Next")',
            '.ant-modal button:has-text("Continue")',
            '.ant-modal button:has-text("OK")',
            # Dialog Next buttons (general)
            '[role="dialog"] button:has-text("Next")',
            '[role="dialog"] button:has-text("Continue")',
            '[role="dialog"] button:has-text("OK")',
            # Page-level Next buttons
            'button:has-text("OK")',
            'button:has-text("Ok")',
            'button.q__ok',
            'button[class*="ok" i]',
            'button:has-text("Next")',
            'button:has-text("Continue")',
            'button:has-text("Save & Continue")',
            'button:has-text("Save and Continue")',
            'button:has-text("Next Step")',
            'button:has-text("Next Page")',
            'button:has-text("Start")',
            'button.apply__start',
            'a:has-text("Start")',
            'a:has-text("Next")',
            'a:has-text("Continue")',
            'input[type="button"][value*="Next" i]',
            'input[type="submit"][value*="Next" i]',
            '[role="button"]:has-text("Next")',
            '[role="button"]:has-text("Continue")',
            '[role="button"]:has-text("OK")',
            'button[aria-label*="next" i]',
            'button[aria-label*="continue" i]',
            'button[aria-label*="ok" i]',
            '[data-testid*="next" i]',
            '[data-testid*="continue" i]',
        ]
        for sel in next_selectors:
            try:
                loc = self.page.locator(sel)
                cnt = await loc.count()
                for i in range(cnt):
                    btn = loc.nth(i)
                    try:
                        if not await btn.is_visible():
                            continue
                        txt = (await btn.text_content() or "").strip().lower()
                        cls = (await btn.evaluate("el => el.className || ''")).lower()
                        # Skip arrow-only buttons with no text
                        if "arrow" in cls and not txt:
                            continue
                        # Skip disabled buttons
                        is_disabled = await btn.evaluate("el => el.disabled || el.getAttribute('aria-disabled') === 'true'")
                        if is_disabled:
                            continue
                        # Skip bad-word buttons
                        if any(w in txt for w in BAD_WORDS):
                            continue
                        # Must contain at least one good word OR have no text (icon-only ok button)
                        if txt and not any(w in txt for w in GOOD_WORDS):
                            continue
                        return btn
                    except Exception:
                        continue
            except Exception:
                continue
        return None


    async def detect_and_handle_captcha(self, job_id: str) -> Dict[str, Any]:
        """Detect and handle captchas (inline image/text or iframe) with AI solver + human fallback."""
        if not self.page:
            return {"has_captcha": False, "solved": True}

        # 1. Inline captcha input locator (check all matching elements, not just .first)
        inline_input_selectors = [
            'input[placeholder*="captcha" i]',
            'input[name*="captcha" i]',
            'input[id*="captcha" i]',
            'input[aria-label*="captcha" i]',
            'input[id*="verificationcode" i]',
            'input[name*="verificationcode" i]',
            'input[id*="verification_code" i]',
            'input[name*="verification_code" i]',
            'input[id*="vcode" i]',
            'input[name*="vcode" i]',
            'input[placeholder*="verification" i]',
            'input[placeholder*="image text" i]',
            'input[placeholder*="below image" i]',
            'input[placeholder*="type the characters" i]',
            'input[placeholder*="enter code" i]',
            'input[data-zcqa*="captcha" i]',
            'rec-captcha-component input',
            '[class*="captcha" i] input',
            '[id*="captcha" i] input',
        ]
        inline_input = None
        for sel in inline_input_selectors:
            locs = self.page.locator(sel)
            try:
                cnt = await locs.count()
                for i in range(cnt):
                    cand = locs.nth(i)
                    if await cand.is_visible():
                        inline_input = cand
                        break
            except Exception:
                continue
            if inline_input:
                break

        # 2. Iframe / Container captcha locator (check all matching elements, not just .first)
        iframe_captcha = None
        iframe_selectors = [
            'iframe[src*="recaptcha" i]',
            'iframe[src*="hcaptcha" i]',
            'iframe[src*="turnstile" i]',
            'iframe[src*="challenges.cloudflare.com" i]',
            '.g-recaptcha',
            '.h-captcha',
            '.cf-turnstile',
            '[data-sitekey]',
        ]
        for sel in iframe_selectors:
            locs = self.page.locator(sel)
            try:
                cnt = await locs.count()
                for i in range(cnt):
                    cand = locs.nth(i)
                    if await cand.is_visible():
                        iframe_captcha = cand
                        break
            except Exception:
                continue
            if iframe_captcha:
                break

        if not inline_input and not iframe_captcha:
            return {"has_captcha": False, "solved": True}

        # Check if an inline captcha error is visible (e.g. "That CAPTCHA was incorrect.")
        has_captcha_error = False
        if inline_input:
            try:
                has_captcha_error = await self.page.evaluate("""() => {
                    const err = document.querySelector('[id*="error-verificationcode"], [class*="error"][class*="captcha" i], .zfErrorDiv');
                    return !!(err && err.offsetParent !== null && (err.innerText || '').trim().length > 0);
                }""")
            except Exception:
                pass

        # If inline captcha input is already filled and has no error, it's solved!
        if inline_input and not has_captcha_error:
            val = (await inline_input.input_value() or "").strip()
            if val and len(val) >= 3:
                return {"has_captcha": True, "solved": True, "type": "inline_text", "value": val}
        elif inline_input and has_captcha_error:
            try:
                await inline_input.fill("")
            except Exception:
                pass

        # If iframe captcha, check if already solved
        if iframe_captcha:
            try:
                is_iframe_solved = await self.page.evaluate("""() => {
                    const tas = [...document.querySelectorAll('textarea[name="g-recaptcha-response"], textarea[name="h-captcha-response"], input[name="cf-turnstile-response"]')];
                    for (const t of tas) {
                        if ((t.value || '').trim().length > 10) return true;
                    }
                    return false;
                }""")
                if is_iframe_solved:
                    return {"has_captcha": True, "solved": True, "type": "iframe"}
            except Exception:
                pass

        print(f"[MCPServer] Captcha detected on page for Job {job_id}. Attempting AI / automated solving first...")

        # STEP A: Try Automated / AI Solution
        # 1. For Checkbox Captchas (reCAPTCHA v2 / Turnstile / hCaptcha)
        if iframe_captcha:
            try:
                recaptcha_iframes = self.page.locator('iframe[src*="recaptcha/api2/anchor" i], iframe[title="reCAPTCHA" i], iframe[src*="recaptcha" i]')
                for i in range(await recaptcha_iframes.count()):
                    ifr = recaptcha_iframes.nth(i)
                    if await ifr.is_visible():
                        name = await ifr.get_attribute("name")
                        fl = self.page.frame_locator(f'iframe[name="{name}"]') if name else self.page.frame_locator('iframe[src*="recaptcha/api2/anchor" i]').nth(i)
                        anchor_chk = fl.locator("#recaptcha-anchor, .recaptcha-checkbox, [role='checkbox']")
                        if await anchor_chk.count() > 0:
                            is_checked = await anchor_chk.first.get_attribute("aria-checked")
                            if is_checked != "true":
                                print(f"[MCPServer] AI auto-clicking reCAPTCHA checkbox in iframe '{name or i}'...")
                                await anchor_chk.first.click()
                                await self.page.wait_for_timeout(2500)
                                new_chk = await anchor_chk.first.get_attribute("aria-checked")
                                if new_chk == "true":
                                    print("[MCPServer] reCAPTCHA passed automatically on AI checkbox click!")
                                    return {"has_captcha": True, "solved": True, "type": "recaptcha", "method": "auto_click"}
                        break
            except Exception as e:
                print(f"[MCPServer] Notice during automated reCAPTCHA checkbox click: {e}")

            # Check if token is present after click
            try:
                has_token = await self.page.evaluate("""() => {
                    const tas = [...document.querySelectorAll('textarea[name="g-recaptcha-response"], textarea[name="h-captcha-response"], input[name="cf-turnstile-response"]')];
                    return tas.some(t => (t.value || '').trim().length > 10);
                }""")
                if has_token:
                    print("[MCPServer] Captcha verified automatically with response token!")
                    return {"has_captcha": True, "solved": True, "type": "iframe", "method": "auto_click"}
            except Exception:
                pass

        # 2. For Inline Image Captchas, capture screenshot and use Bedrock/Claude Vision
        img_b64 = None
        raw_bytes = None
        if inline_input:
            try:
                await inline_input.scroll_into_view_if_needed(timeout=2000)
                target_img = None
                img_locators = [
                    self.page.locator("img#zf-captcha"),
                    self.page.locator("img[src*='showcaptcha' i]"),
                    self.page.locator("img[src*='captcha' i]"),
                    self.page.locator("img[id*='captcha' i]"),
                    inline_input.locator("xpath=ancestor::*[contains(@class, 'captcha') or contains(@id, 'captcha') or contains(@class, 'wrapper')][1]//img"),
                    inline_input.locator("xpath=..//img"),
                    self.page.locator("[class*='captcha' i] img, canvas[class*='captcha' i]")
                ]
                for iloc in img_locators:
                    try:
                        if await iloc.count() > 0 and await iloc.first.is_visible():
                            target_img = iloc.first
                            break
                    except Exception:
                        continue

                if target_img:
                    raw_bytes = await target_img.screenshot(timeout=3000)
                    img_b64 = base64.b64encode(raw_bytes).decode("utf-8")
                else:
                    parent = inline_input.locator("xpath=..")
                    raw_bytes = await parent.screenshot(timeout=3000)
                    img_b64 = base64.b64encode(raw_bytes).decode("utf-8")
            except Exception as e:
                print(f"[MCPServer] Notice: Could not capture captcha image screenshot: {e}")

            # Try Bedrock vision auto-solver for inline image captcha
            if img_b64 and raw_bytes:
                try:
                    import boto3, re
                    bclient = boto3.client("bedrock-runtime", region_name="us-east-1")
                    bresp = bclient.converse(
                        modelId="us.amazon.nova-pro-v1:0",
                        messages=[{
                            "role": "user",
                            "content": [
                                {"image": {"format": "png", "source": {"bytes": raw_bytes}}},
                                {"text": "Solve this captcha. Return ONLY the exact text or digits shown in the captcha image, with no extra spaces, words, or punctuation. Just the code."}
                            ]
                        }],
                        inferenceConfig={"temperature": 0.0, "maxTokens": 20}
                    )
                    btext = "".join(b.get("text", "") for b in bresp.get("output", {}).get("message", {}).get("content", []) if isinstance(b, dict)).strip()
                    btext = re.sub(r'[^a-zA-Z0-9]', '', btext)
                    if btext and len(btext) >= 3:
                        print(f"[MCPServer] Bedrock Vision solved inline captcha: '{btext}'")
                        await inline_input.fill(btext)
                        await inline_input.press("Tab")
                        await self.page.wait_for_timeout(500)
                        return {"has_captcha": True, "solved": True, "type": "inline_text", "value": btext}
                except Exception as be:
                    print(f"[MCPServer] Bedrock vision captcha attempt notice: {be}")

        # 3. For Iframe / Challenge Popup Captcha screenshot
        if not img_b64 and iframe_captcha:
            try:
                challenge_iframes = self.page.locator('iframe[src*="recaptcha/api2/bframe" i], iframe[title*="challenge" i], iframe[src*="hcaptcha" i][src*="challenge" i]')
                for i in range(await challenge_iframes.count()):
                    c_ifr = challenge_iframes.nth(i)
                    if await c_ifr.is_visible():
                        raw_bytes = await c_ifr.screenshot(timeout=3000)
                        img_b64 = base64.b64encode(raw_bytes).decode("utf-8")
                        break
                if not img_b64:
                    raw_bytes = await iframe_captcha.screenshot(timeout=3000)
                    img_b64 = base64.b64encode(raw_bytes).decode("utf-8")
            except Exception as e:
                print(f"[MCPServer] Notice: Could not capture iframe challenge screenshot: {e}")

        # STEP B: Not solvable automatically -> Fallback to Human-in-the-loop solver UI
        print(f"[MCPServer] Captcha could not be solved automatically. Opening human solver UI on http://localhost:8085 and notifying user...")

        # Start captcha server and pop up browser window
        from captcha_server import start_captcha_server
        import captcha_server
        captcha_server._captcha_image_b64 = img_b64
        captcha_server._captcha_solution = None
        captcha_server._captcha_active = True
        captcha_server._event.clear()
        start_captcha_server()
        try:
            import webbrowser
            webbrowser.open("http://127.0.0.1:8085")
        except Exception:
            pass

        # Poll for completion: check both the UI event AND direct solution in the browser
        max_wait_seconds = 120
        poll_interval = 2.0
        elapsed = 0.0
        solved = False
        solution = None

        while elapsed < max_wait_seconds:
            # 1. Did user click solve button in http://localhost:8085?
            if captcha_server._event.is_set():
                solved = True
                solution = captcha_server._captcha_solution
                break

            # 2. Did user solve reCAPTCHA / hCaptcha / Turnstile directly in Brave?
            try:
                is_live_solved = await self.page.evaluate("""() => {
                    const tas = [...document.querySelectorAll('textarea[name="g-recaptcha-response"], textarea[name="h-captcha-response"], input[name="cf-turnstile-response"]')];
                    return tas.some(t => (t.value || '').trim().length > 10);
                }""")
                if is_live_solved:
                    solved = True
                    break
            except Exception:
                pass

            # 3. Did user fill inline input directly in Brave?
            if inline_input:
                try:
                    val = (await inline_input.input_value() or "").strip()
                    if val and len(val) >= 3 and not has_captcha_error:
                        solved = True
                        solution = val
                        break
                except Exception:
                    pass

            await asyncio.sleep(poll_interval)
            elapsed += poll_interval

        captcha_server._captcha_active = False

        if solved:
            print(f"[MCPServer] Captcha solved! Resuming application...")
            if solution and inline_input:
                try:
                    await inline_input.fill(solution)
                    await inline_input.press("Tab")
                    await self.page.wait_for_timeout(500)
                except Exception as e:
                    print(f"[MCPServer] Error entering captcha solution: {e}")
            return {"has_captcha": True, "solved": True, "type": "inline_text" if inline_input else "iframe"}
        else:
            print(f"[MCPServer] Captcha was not solved within {max_wait_seconds}s timeout.")
            return {"has_captcha": True, "solved": False}

    async def _submit_form(self, job_id: Optional[str] = None) -> Dict[str, Any]:
        """Locate and click the final Submit button, then diagnose submission result."""
        if not self.page:
            return {"status": "error", "message": "No active page to submit"}

        # 1. Proactively synchronize active window and dismiss modals/popups
        await self.sync_active_page()
        await self.handle_active_popups()

        # Proactively check all consent/terms/agreement/privacy checkboxes across page and frames
        try:
            await self.page.evaluate("""() => {
                const chks = [...document.querySelectorAll('input[type="checkbox"], [role="checkbox"]')];
                for (const c of chks) {
                    if (c.checked || c.getAttribute('aria-checked') === 'true') continue;
                    const parent = c.closest('label, div, p, li, form, section, .form-group, .custom-control') || c.parentElement;
                    const parentText = (parent ? parent.innerText : '').toLowerCase();
                    const idName = ((c.id || '') + ' ' + (c.name || '') + ' ' + (c.className || '')).toLowerCase();
                    if (/agree|consent|terms|condition|privacy|policy|subscribe|declaration|acknowledg|receive.*(email|text|message)/i.test(parentText + ' ' + idName)) {
                        try {
                            c.scrollIntoView({ behavior: 'instant', block: 'center' });
                            c.click();
                        } catch(e) {}
                        if (!c.checked) {
                            try {
                                const lbl = parent ? parent.querySelector('label') : null;
                                if (lbl) lbl.click();
                                else {
                                    c.checked = true;
                                    c.dispatchEvent(new Event('input', { bubbles: true }));
                                    c.dispatchEvent(new Event('change', { bubbles: true }));
                                }
                            } catch(e) {}
                        }
                    }
                }
            }""")
        except Exception:
            pass

        # Auto-confirm any unpressed required toggle button groups (e.g. MuiToggleButtonGroup for relocate/consent)
        try:
            tg_res = await self.page.evaluate("""() => {
                const results = [];
                const groups = [...document.querySelectorAll('.MuiToggleButtonGroup-root, [role="group"]:has(button), [role="radiogroup"]')];
                for (const g of groups) {
                    if (g.offsetParent === null) continue;
                    if (g.closest('footer, nav, [class*="nav" i], [aria-label*="navigate" i], [aria-label*="arrow" i], [class*="arrow" i]')) continue;
                    const btns = [...g.querySelectorAll('button')].filter(b => !b.closest('footer, nav') && !b.className.includes('arrow') && !(b.getAttribute('aria-label') || '').includes('question'));
                    if (btns.length === 0) continue;
                    const anyPressed = btns.some(b => b.getAttribute('aria-pressed') === 'true' || b.getAttribute('aria-checked') === 'true' || b.classList.contains('Mui-selected') || b.classList.contains('active'));
                    if (!anyPressed) {
                        const yesBtn = btns.find(b => {
                            const val = (b.value || b.innerText || '').trim().toLowerCase();
                            return val === 'yes' || val === 'agree' || val === 'accept';
                        });
                        if (yesBtn) {
                            yesBtn.click();
                            results.push(yesBtn.value || yesBtn.innerText);
                        }
                    }
                }
                return results;
            }""")
            if tg_res:
                print(f"[MCPServer] Auto-confirmed unpressed toggle group(s): {tg_res}")
                await self.page.wait_for_timeout(200)
        except Exception:
            pass

        # 2. Targeted pre-submit check for any live unfilled required or invalid tags
        try:
            live_leftovers = await self.detect_live_leftover_tags()
            if live_leftovers:
                print(f"[MCPServer] Pre-submit: targetedly solving {len(live_leftovers)} live leftover tag(s)...")
                for tag in live_leftovers:
                    await self.solve_live_leftover_tag(tag, job_id=job_id)
                await self.page.wait_for_timeout(300)
        except Exception as e:
            print(f"[MCPServer] Pre-submit tag resolution error: {e}")

        # Check consent/agreement checkboxes before submit
        try:
            await self.page.evaluate("""() => {
                const chks = [...document.querySelectorAll('input[type="checkbox"], [role="checkbox"], .ant-checkbox-input')];
                for (const c of chks) {
                    if (c.checked || c.getAttribute('aria-checked') === 'true') continue;
                    const parent = c.closest('label, div, p, li, form, section, .form-group') || c.parentElement;
                    const parentText = (parent ? parent.innerText : '').toLowerCase();
                    const idName = ((c.id || '') + ' ' + (c.name || '') + ' ' + (c.className || '')).toLowerCase();
                    if (/agree|consent|terms|condition|privacy|policy|declaration/i.test(parentText + ' ' + idName) || chks.length === 1) {
                        try { c.click(); } catch(e) {}
                        if (!c.checked) {
                            c.checked = true;
                            c.dispatchEvent(new Event('input', { bubbles: true }));
                            c.dispatchEvent(new Event('change', { bubbles: true }));
                        }
                    }
                }
            }""")
            await self.page.wait_for_timeout(300)
        except Exception:
            pass

        # Pre-submit captcha verification: ensure ANY captcha (inline or iframe) is detected and handled before clicking submit
        try:
            c_res = await self.detect_and_handle_captcha(job_id or "job")
            if c_res.get("has_captcha") and not c_res.get("solved"):
                print(f"[MCPServer] Aborting submit: unsolved captcha on page for Job {job_id}!")
                return {
                    "status": "needs_captcha_solved",
                    "job_id": job_id,
                    "message": "Captcha challenge detected on page! Please complete the captcha in the open Brave browser window or on http://localhost:8085, then retry submit.",
                    "url": self.page.url,
                }
        except Exception as e:
            print(f"[MCPServer] Notice during pre-submit captcha check: {e}")

        submit_selectors = [
            'button[data-testid="apply-button"]',
            '.gh-widget-btn:has-text("Submit")',
            'div.btn:has-text("Submit")',
            'div[class*="gh-widget"]:has-text("Submit")',
            'button:has-text("Apply for this position")',
            'button:has-text("Apply for this Job")',
            'button:has-text("Apply for this job")',
            'button:has-text("Apply for this Role")',
            'button:has-text("Apply for Role")',
            'button[data-testid*="submit"]',
            'button[data-testid*="apply"]',
            '[data-testid*="apply-button"]',
            '[data-testid*="submit-button"]',
            'button:has-text("Submit application")',
            'button:has-text("Submit Application")',
            'button:has-text("Confirm & Submit")',
            'button:has-text("Confirm Application")',
            'button:has-text("Confirm")',
            '#career button',
            '#application-form button[type="submit"], #application-form input[type="submit"]',
            '#apply button[type="submit"], #apply input[type="submit"]',
            'button:has-text("Submit Form")',
            'button:has-text("Apply Now")',
            'button:has-text("Apply now")',
            'button:has-text("Apply")',
            'button[type="submit"]:has-text("Apply")',
            'button:has-text("Submit")',
            'input[type="submit"]',
            'button[type="submit"]',
            '[role="button"]:has-text("Submit Application")',
            '[role="button"]:has-text("Confirm")',
            '[role="button"]:has-text("Submit")',
        ]
        submit_btn = None
        all_frames = [self.page] + [f for f in self.page.frames if not (self.dom_extractor and self.dom_extractor._is_ignorable_frame(f))]
        for attempt in range(4):
            for ctx in all_frames:
                for sel in submit_selectors:
                    try:
                        loc = ctx.locator(sel).first
                        if await loc.count() > 0 and await loc.is_visible():
                            txt = (await loc.text_content() or "").strip().lower()
                            if any(w in txt for w in ["cancel", "back", "previous", "search", "filter", "login", "sign in"]):
                                continue
                            in_unrelated = await loc.evaluate("""el => {
                                const bad = el.closest('#contact, footer, .footer, [id*="contact-us"]');
                                const good = el.closest('#career, #apply, [id*="career"], [id*="apply"], form, .apply');
                                if (bad !== null && good === null) return true;
                                const openModal = document.querySelector('[role="dialog"], .modal.show, .modal.in, .ant-modal');
                                if (openModal && !openModal.contains(el)) return true;
                                const txt = (el.innerText || el.textContent || '').trim().toLowerCase();
                                if (txt === 'apply' && el.type !== 'submit' && !el.closest('form, [role="dialog"], .modal, .ant-modal')) return true;
                                return false;
                            }""")
                            if in_unrelated:
                                continue
                            submit_btn = loc
                            break
                    except Exception:
                        continue
                if submit_btn:
                    break
            if submit_btn:
                break
            await self.page.wait_for_timeout(800)

        if not submit_btn:
            try:
                if Path("important_data_fields.json").exists():
                    with open("important_data_fields.json", "r", encoding="utf-8") as f:
                        data = json.load(f)
                    fields = data.get("fields", []) if isinstance(data, dict) else data
                    for fld in fields:
                        if fld.get("field_key") == "submit_button" and fld.get("selector"):
                            loc = self.page.locator(fld["selector"]).first
                            if await loc.count() > 0 and await loc.is_visible():
                                submit_btn = loc
                                break
            except Exception:
                pass

        if not submit_btn:
            return {
                "status": "error",
                "message": "Submit button not found on page. Inspect screenshot and use do_action('click', identifier=selector) to submit.",
                "url": self.page.url,
            }

        # Pre-submit HTML5 validity check
        try:
            invalid_fields = await self.page.evaluate("""() => {
                const inv = [];
                for (const el of document.querySelectorAll('input, select, textarea')) {
                    if (el.offsetParent !== null && !el.disabled && typeof el.checkValidity === 'function' && !el.checkValidity()) {
                        const lbl = el.getAttribute('aria-label') || el.placeholder || el.name || el.id || '';
                        inv.push({
                            tag: el.tagName.toLowerCase(),
                            type: el.type,
                            name: el.name,
                            id: el.id,
                            label: lbl.slice(0, 60),
                            msg: el.validationMessage
                        });
                    }
                }
                return inv;
            }""")
            if invalid_fields:
                print(f"[MCPServer] Notice: {len(invalid_fields)} invalid/unfilled required field(s) detected: {invalid_fields}")
                if self.automatic_filling:
                    try:
                        await self.automatic_filling.fill_required_empty_controls(self.page)
                    except Exception as e:
                        print(f"[MCPServer] Auto-repair error: {e}")
        except Exception:
            pass

        # Check if an asynchronous file upload is currently in flight before clicking submit
        try:
            for _wait in range(25):
                is_uploading = await self.page.evaluate("""() => {
                    const uploadBar = document.querySelector('.upload__bar, [class*="uploading"], [class*="progress-bar"], [role="progressbar"]');
                    const uploadText = (document.body ? document.body.innerText : '').toLowerCase();
                    const hasUploadingText = uploadText.includes('uploading') || uploadText.includes('is still uploading') || uploadText.includes('résumé is still uploading');
                    return Boolean(uploadBar || hasUploadingText);
                }""")
                if not is_uploading:
                    has_completed_or_idle = await self.page.evaluate("""() => {
                        const fileComp = document.querySelector('.upload, [class*="upload"]');
                        if (!fileComp) return true;
                        const done = fileComp.querySelector('.upload__done, .upload__replace, [class*="uploaded" i]');
                        return Boolean(done);
                    }""")
                    if has_completed_or_idle:
                        break
                print(f"[MCPServer] File upload in progress... waiting for upload to complete ({_wait+1}s)...")
                await self.page.wait_for_timeout(1000)
        except Exception:
            pass

        print("[MCPServer] Clicking Submit button...")
        # Proactively re-locate submit button right before click to avoid stale references
        for ctx in all_frames:
            for sel in submit_selectors:
                try:
                    loc = ctx.locator(sel).first
                    if await loc.count() > 0 and await loc.is_visible():
                        submit_btn = loc
                        break
                except Exception:
                    continue
            if submit_btn:
                break

        # Wait if submit button is disabled or busy
        try:
            if submit_btn:
                for _wait in range(12):
                    is_disabled = await submit_btn.evaluate("el => el.disabled || el.getAttribute('aria-disabled') === 'true' || el.getAttribute('aria-busy') === 'true'")
                    if not is_disabled:
                        break
                    print(f"[MCPServer] Submit button currently disabled/busy... waiting ({_wait+1}s)...")
                    await self.page.wait_for_timeout(1000)
        except Exception:
            pass

        try:
            await submit_btn.scroll_into_view_if_needed(timeout=2000)
        except Exception:
            pass

        url_before = self.page.url
        form_count_before = 1
        try:
            form_count_before = await self.page.locator("form").count()
        except Exception:
            pass

        clicked_successfully = False
        try:
            await submit_btn.click(timeout=5000)
            clicked_successfully = True
        except Exception:
            try:
                await submit_btn.click(force=True, timeout=5000)
                clicked_successfully = True
            except Exception:
                try:
                    await submit_btn.evaluate("el => el.click()")
                    clicked_successfully = True
                except Exception:
                    try:
                        await self.page.evaluate("() => { const f = document.querySelector('form'); if (f && f.requestSubmit) f.requestSubmit(); }")
                        clicked_successfully = True
                    except Exception as e:
                        return {"status": "error", "message": f"Failed to click submit button: {e}"}

        # Wait 4 seconds for response/navigation
        await self.page.wait_for_timeout(4000)
        await self.handle_active_popups()
        url_after = self.page.url

        # Check if an interactive captcha challenge appeared upon clicking submit
        try:
            post_submit_captcha = await self.detect_and_handle_captcha(job_id or "job")
            if post_submit_captcha.get("has_captcha") and not post_submit_captcha.get("solved"):
                return {
                    "status": "needs_captcha_solved",
                    "job_id": job_id,
                    "message": "Interactive captcha challenge popped up upon submit. Please solve the captcha in Chrome/Brave, then call apply_page(continue_post=True).",
                    "url": self.page.url,
                    "total_filled": total_filled_count if 'total_filled_count' in locals() else 0,
                    "screenshot_file": "last_submission_screenshot.jpg",
                }
        except Exception:
            pass

        # Check for success indicators
        url_lower = url_after.lower()
        success_urls = ["/thank", "/confirm", "/success", "/applied", "/submitted", "/complete", "/email_verification", "/verification"]
        url_success = any(k in url_lower for k in success_urls) or (url_after != url_before and "error" not in url_lower)

        try:
            body_texts = []
            for f in [self.page] + self.page.frames:
                try:
                    bt = await f.evaluate("() => document.body ? document.body.innerText : ''")
                    if bt:
                        body_texts.append(bt)
                except Exception:
                    pass
            body_text = " ".join(body_texts).lower()
        except Exception:
            body_text = ""

        text_success = any(phrase in body_text for phrase in [
            "thank you for applying",
            "thanks for submitting",
            "thank you for submitting",
            "application submitted",
            "application received",
            "successfully submitted",
            "application was successfully submitted",
            "your application was successfully submitted",
            "application has been submitted",
            "thank you for your interest",
            "we have received your application",
            "already applied",
            "you have already applied",
            "application sent",
            "application has been sent",
            "your application was submitted",
            "submission successful",
            "thanks for applying",
            "we will review your application",
            "application completed",
            "form submitted",
            "enquiry sent",
            "message sent",
            "verify your email",
            "verification link",
            "we'll get back to you",
            "we will get back to you",
        ])

        # Check if form was replaced by a success view (form count became 0)
        form_unmounted = False
        try:
            form_count_after = await self.page.locator("form").count()
            if form_count_before > 0 and form_count_after == 0:
                form_unmounted = True
        except Exception:
            pass

        # Check for error indicators
        has_error = False
        error_messages = []
        try:
            invalid_inputs = await self.page.evaluate("""() => {
                const invalids = [];
                for (const el of document.querySelectorAll('input, select, textarea')) {
                    if (el.getAttribute('aria-invalid') === 'true' || (el.offsetParent !== null && !el.disabled && typeof el.checkValidity === 'function' && !el.checkValidity())) {
                        invalids.push(el.name || el.id || 'invalid input');
                    }
                }
                return invalids;
            }""")
            if invalid_inputs:
                has_error = True
                error_messages.extend([f"Field {n} is invalid" for n in invalid_inputs[:3]])

            error_locs = self.page.locator('.error-message, .field-error, .invalid-feedback, .form-error, [role="alert"], [data-error], .alert-error, .choice-input-wrapper--error, .error-wrapper, .error-feedback, .select-error-message, .Mui-error, .Toastify__toast')
            count = await error_locs.count()
            for i in range(min(count, 5)):
                el = error_locs.nth(i)
                if await el.is_visible():
                    if await el.locator('input, textarea, select').count() > 0:
                        continue
                    t = (await el.text_content() or "").strip()
                    if t and len(t) < 200:
                        low_t = t.lower()
                        if any(pos in low_t for pos in ["already applied", "we have your application", "already submitted", "already received", "application received", "will be in touch"]):
                            text_success = True
                            continue
                        error_messages.append(t)
                        has_error = True
        except Exception:
            pass

        # If errors were detected after submit, attempt targeted reactive solve on leftover/invalid fields and retry submit once
        if has_error:
            # If the error is specifically that the file is still uploading, do NOT re-upload! Wait and retry submit!
            is_upload_wait_needed = any("uploading" in msg.lower() or "still uploading" in msg.lower() for msg in error_messages)
            if is_upload_wait_needed:
                print("[MCPServer] Notice: Form indicated file is still uploading. Waiting 5 seconds for upload completion before retrying submit...")
                await self.page.wait_for_timeout(5000)
                if submit_btn:
                    try:
                        await submit_btn.click(timeout=5000)
                    except Exception:
                        await submit_btn.click(force=True, timeout=5000)
                    await self.page.wait_for_timeout(4000)
                    await self.handle_active_popups()
                    url_after = self.page.url
                    url_lower = url_after.lower()
                    url_success = any(k in url_lower for k in success_urls) or (url_after != url_before and "error" not in url_lower)
                    try:
                        body_text = (await self.page.locator("body").inner_text(timeout=2000) or "").lower()
                    except Exception:
                        body_text = ""
                    text_success = any(phrase in body_text for phrase in [
                        "thank you for applying", "thanks for submitting", "thank you for submitting",
                        "application submitted", "application received", "successfully submitted",
                        "application has been submitted", "we'll get back to you", "we will get back to you"
                    ])
                    if text_success or url_success:
                        has_error = False
            try:
                retry_leftovers = await self.detect_live_leftover_tags()
                if retry_leftovers:
                    print(f"[MCPServer] Post-submit errors detected! Attempting targeted fast solve for {len(retry_leftovers)} tag(s)...")
                    for tag in retry_leftovers:
                        await self.solve_live_leftover_tag(tag, job_id=job_id)
                    try:
                        await self.page.evaluate("""() => {
                            const groups = [...document.querySelectorAll('.MuiToggleButtonGroup-root, [role="group"]:has(button), [role="radiogroup"]')];
                            for (const g of groups) {
                                if (g.offsetParent === null) continue;
                                if (g.closest('footer, nav, [class*="nav" i], [aria-label*="navigate" i], [aria-label*="arrow" i], [class*="arrow" i]')) continue;
                                const btns = [...g.querySelectorAll('button')].filter(b => !b.closest('footer, nav') && !b.className.includes('arrow') && !(b.getAttribute('aria-label') || '').includes('question'));
                                if (btns.length === 0) continue;
                                const anyPressed = btns.some(b => b.getAttribute('aria-pressed') === 'true' || b.getAttribute('aria-checked') === 'true' || b.classList.contains('Mui-selected') || b.classList.contains('active'));
                                if (!anyPressed) {
                                    const yesBtn = btns.find(b => {
                                        const val = (b.value || b.innerText || '').trim().toLowerCase();
                                        return val === 'yes' || val === 'agree' || val === 'accept';
                                    });
                                    if (yesBtn) yesBtn.click();
                                }
                            }
                        }""")
                    except Exception:
                        pass
                    await self.page.wait_for_timeout(500)
                    if submit_btn:
                        print("[MCPServer] Retrying submit after solving leftover tags...")
                        try:
                            await submit_btn.click(timeout=5000)
                        except Exception:
                            await submit_btn.click(force=True, timeout=5000)
                        await self.page.wait_for_timeout(4000)
                        await self.handle_active_popups()
                        url_after = self.page.url
                        url_lower = url_after.lower()
                        url_success = any(k in url_lower for k in success_urls) or (url_after != url_before and "error" not in url_lower)
                        try:
                            body_text = (await self.page.locator("body").inner_text(timeout=2000) or "").lower()
                        except Exception:
                            body_text = ""
                        text_success = any(phrase in body_text for phrase in [
                            "thank you for applying",
                            "application submitted",
                            "application received",
                            "successfully submitted",
                            "application has been submitted",
                            "thank you for your interest",
                            "we have received your application",
                            "application sent",
                            "application has been sent",
                            "your application was submitted",
                            "submission successful",
                            "thanks for applying",
                            "we will review your application",
                            "application completed",
                            "form submitted",
                            "enquiry sent",
                            "message sent",
                            "verify your email",
                            "verification link",
                        ])
                        still_invalid = await self.page.evaluate("""() => {
                            const inv = [];
                            for (const el of document.querySelectorAll('input, select, textarea')) {
                                if (el.getAttribute('aria-invalid') === 'true' || (el.offsetParent !== null && !el.disabled && typeof el.checkValidity === 'function' && !el.checkValidity())) {
                                    inv.push(el.name || el.id || 'invalid input');
                                }
                            }
                            return inv;
                        }""")
                        if (url_success or text_success) and not still_invalid:
                            has_error = False
                            error_messages = []
            except Exception as retry_err:
                print(f"[MCPServer] Post-submit retry error: {retry_err}")

        screenshot_saved = False
        screenshot_file = "last_submission_screenshot.jpg"
        screenshot = await self.get_page_screenshot()
        if isinstance(screenshot, dict) and isinstance(screenshot.get("data"), (bytes, bytearray)):
            try:
                with open(screenshot_file, "wb") as f:
                    f.write(screenshot["data"])
                screenshot_saved = True
            except Exception:
                pass

        total_filled_count = len(self.state_manager.get_status(job_id).get("filled_fields", []))

        if (url_success or text_success or form_unmounted) and not has_error:
            self.state_manager.record_submit(job_id, success=True, message="Application successfully submitted!")
            return {
                "status": "success",
                "job_id": job_id,
                "message": "Congrats! Form has been filled and submitted successfully!",
                "url": url_after,
                "total_filled": total_filled_count,
                "screenshot_file": screenshot_file if screenshot_saved else None,
            }
        elif has_error:
            self.state_manager.record_submit(job_id, success=False, message=f"Errors after submit: {', '.join(error_messages)}")
            return {
                "status": "error_after_submit",
                "job_id": job_id,
                "message": "Submit was clicked, but validation errors were detected on the page.",
                "errors": error_messages[:3],
                "url": url_after,
                "total_filled": total_filled_count,
                "prompt": "Errors detected. Use do_action to fix the invalid fields or solve captcha, then retry submit.",
                "screenshot_file": screenshot_file if screenshot_saved else None,
            }
        else:
            return {
                "status": "submitted_pending_verification",
                "job_id": job_id,
                "message": "Submit clicked. Please verify final page screenshot or confirmation email.",
                "url": url_after,
                "total_filled": total_filled_count,
                "screenshot_file": screenshot_file if screenshot_saved else None,
            }

    async def run_application_state(self, job_id: Optional[str] = None, multiple_pages: bool = False, continue_post: bool = False):
        """Automated form filling with state management, pagination, and submission."""
        try:
            await self._ensure_browser_session()
            if not self.page:
                return {"status": "error", "message": "No active page. Please call open_page with the job URL first."}

            if not job_id:
                job_id = f"job_{int(time.time())}"

            job_state = self.state_manager.get_or_create(job_id, self.page.url)

            if not self.dom_extractor:
                self.dom_extractor = FormFieldExtractor(url=self.current_page_url or self.page.url)

            if not continue_post:
                ensure_form_visible = await self.dom_extractor._ensure_application_form_open(self.page)
                if not ensure_form_visible:
                    return {
                        "status": "error",
                        "message": "The application form is not visible. Please use get_screenshot and do_action to login/register or click Apply, then call apply_page again.",
                        "job_id": job_id,
                    }
                # If CTA opened a new tab/window, sync active page immediately before extraction
                await self.sync_active_page()
                if self.dom_extractor.url != self.page.url:
                    self.dom_extractor = FormFieldExtractor(url=self.page.url)

            page_index = job_state.get("current_page_index", 1)
            max_pages = 25

            while page_index <= max_pages:
                job_state["current_page_index"] = page_index
                job_state["status"] = "filling"
                print(f"\n[MCPServer] --- Running application state for Job {job_id} (Page {page_index}) ---")

                if continue_post:
                    print(f"[MCPServer] Resuming application (continue_post=True) for Job {job_id}...")
                    try:
                        consent_selectors = [
                            '#terms',
                            '#candidateConsent',
                            'input[type="checkbox"][id*="consent" i]',
                            'input[type="checkbox"][id*="agree" i]',
                            'input[type="checkbox"][name*="consent" i]',
                            'input[type="checkbox"][name*="agree" i]',
                            'input[type="checkbox"][name*="term" i]',
                            'input[type="checkbox"][id*="term" i]',
                        ]
                        for c_sel in consent_selectors:
                            c_loc = self.page.locator(c_sel)
                            c_count = await c_loc.count()
                            for i in range(c_count):
                                chk = c_loc.nth(i)
                                try:
                                    if await chk.is_visible() and not await chk.is_checked():
                                        await chk.check(force=True, timeout=2000)
                                except Exception:
                                    try:
                                        await chk.evaluate("el => { el.checked = true; el.dispatchEvent(new Event('input', {bubbles: true})); el.dispatchEvent(new Event('change', {bubbles: true})); }")
                                    except Exception:
                                        pass
                    except Exception:
                        pass

                    captcha_res = await self.detect_and_handle_captcha(job_id)
                    if captcha_res.get("has_captcha") and not captcha_res.get("solved"):
                        return {
                            "status": "needs_captcha_solved",
                            "job_id": job_id,
                            "message": "Captcha detected! Please complete the captcha in Brave or on http://localhost:8085, then call apply_page(continue_post=True).",
                            "url": self.page.url,
                        }

                    if multiple_pages:
                        next_btn = await self._find_next_button()
                        if next_btn:
                            print(f"[MCPServer] Clicking Next button on page {page_index}...")
                            try:
                                await next_btn.click(timeout=5000)
                            except Exception:
                                await next_btn.click(force=True, timeout=5000)
                            await self.page.wait_for_timeout(3000)
                            page_index += 1
                            continue_post = False
                            continue

                    return await self._submit_form(job_id)

                # 1. Discover and extract DOM fields
                dom_result = await self.dom_extractor.main(self.page)
                if isinstance(dom_result, list):
                    self.last_elements = dom_result
                elif isinstance(dom_result, dict):
                    self.last_elements = dom_result.get("fields") or dom_result.get("important") or []
                else:
                    self.last_elements = []

                # Snapshot the stabilized current page / question signature before filling actions
                sig_before = ""
                try:
                    sig_before = await self.page.evaluate("""() => {
                        const h = document.querySelector('.ant-steps-item-active, .step.active, [class*="step" i][class*="active" i], h1, h2, h3, [class*="title"], [class*="question"]');
                        const inpCount = document.querySelectorAll('input:not([type="hidden"]), select, textarea').length;
                        return window.location.href + '::' + (h ? h.innerText.trim() : '') + '::' + inpCount;
                    }""")
                except Exception:
                    pass

                # 2. Run FormFiller with fresh elements from extraction
                self.automatic_filling = FormFiller(page=self.page, url=self.current_page_url or self.page.url)

                def _on_field_update(item):
                    idx = item.get("element_index")
                    val = item.get("answer") or item.get("actual_value")
                    q = item.get("question") or ""
                    sel = item.get("selector") or ""
                    ver = item.get("final_verified", item.get("verified", False))
                    meth = item.get("method", "auto")
                    self.state_manager.record_field(job_id, idx, q, sel, val, meth, ver)

                fill_output = await self.automatic_filling.main(
                    job_id=job_id,
                    continue_post=continue_post,
                    state_callback=_on_field_update,
                    mcp_mode=True,
                )
                if hasattr(self.automatic_filling, "elements") and self.automatic_filling.elements:
                    self.last_elements = self.automatic_filling.elements

                # Sync state from FormFiller summary
                summary = self.automatic_filling.get_field_summary()
                for f in summary.get("filled_fields", []):
                    self.state_manager.record_field(job_id, f["element_index"], f["question"], f["selector"], f["value"], f["method"], f["verified"])

                # Check if FormFiller already verified all fields and submitted the application
                is_already_submitted = (
                    isinstance(fill_output, dict)
                    and (
                        fill_output.get("submitted") is True
                        or fill_output.get("output", {}).get("submitted") is True
                    )
                )
                if is_already_submitted:
                    print(f"[MCPServer] FormFiller has fully verified and submitted the application for Job {job_id}!")
                    await self.page.wait_for_timeout(3000)
                    await self.handle_active_popups()

                    shot_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), f"submission_{job_id}.png")
                    try:
                        await self.page.screenshot(path=shot_path, full_page=False)
                    except Exception:
                        pass

                    self.state_manager.record_submit(job_id, success=True, message="Application verified and submitted successfully")
                    return {
                        "status": "success",
                        "message": "Application submitted successfully! All fields were verified.",
                        "job_id": job_id,
                        "url": self.page.url,
                        "submitted": True,
                        "total_filled": summary.get("verified_count", 0),
                        "screenshot_file": shot_path
                    }

                # FormFiller detected a Next/Continue button — skip straight to pagination
                next_step_available = isinstance(fill_output, dict) and fill_output.get("next_step_available") is True
                if next_step_available:
                    print(f"[MCPServer] FormFiller signals next step available on page {page_index}. Clicking Next...")
                    next_btn = await self._find_next_button()
                    if next_btn:
                        try:
                            await next_btn.click(timeout=5000)
                        except Exception:
                            try:
                                await next_btn.click(force=True, timeout=5000)
                            except Exception:
                                pass
                        await self.page.wait_for_timeout(2500)
                        page_index += 1
                        continue_post = False
                        continue
                    else:
                        print(f"[MCPServer] next_step_available but Next button not found — falling through to submit.")

                # Post-fill consent sweep: ensure all consent / agreement checkboxes are checked
                try:
                    consent_selectors = [
                        '#candidateConsent',
                        '#job-application_agreement',
                        'input[type="checkbox"][id*="agreement" i]',
                        'input[type="checkbox"][id*="consent" i]',
                        'input[type="checkbox"][id*="agree" i]',
                        'input[type="checkbox"][id*="privacy" i]',
                        'input[type="checkbox"][id*="term" i]',
                        'input[type="checkbox"][name*="consent" i]',
                        'input[type="checkbox"][name*="agree" i]',
                        'input[type="checkbox"][name*="agreement" i]',
                        'input[type="checkbox"][name*="privacy" i]',
                        'input[type="checkbox"][name*="term" i]',
                    ]
                    for c_sel in consent_selectors:
                        c_loc = self.page.locator(c_sel)
                        c_count = await c_loc.count()
                        for i in range(c_count):
                            chk = c_loc.nth(i)
                            try:
                                is_c = await chk.is_checked()
                                if not is_c:
                                    try:
                                        await chk.check(force=True, timeout=1500)
                                    except Exception:
                                        pass
                                is_c2 = await chk.is_checked()
                                if not is_c2:
                                    await chk.evaluate("""el => {
                                        const lbl = el.closest('label') || (el.id ? document.querySelector(`label[for="${CSS.escape(el.id)}"]`) : null) || el.parentElement;
                                        if (lbl) lbl.click();
                                        else el.click();
                                    }""")
                            except Exception:
                                pass
                except Exception:
                    pass

                leftovers = summary.get("leftover_fields", [])
                if not leftovers and isinstance(fill_output, dict):
                    leftovers = fill_output.get("leftover") or fill_output.get("question") or []

                # Filter out consent from leftovers since it is automatically handled
                leftovers = [
                    f for f in leftovers
                    if not any(t in str(f.get("question") or "").lower() or t in str(f.get("selector") or "").lower()
                               for t in ["consent", "privacy", "terms", "agree", "policy", "declaration", "dataprocessing", "candidateconsent"])
                ]

                # Step 1: Answering leftover / understanding fields with Bedrock AI FIRST
                if leftovers:
                    print(f"[MCPServer] Step 1: Answering {len(leftovers)} leftover field(s) with Bedrock AI...")
                    leftovers = await self.solve_leftovers_with_ai(leftovers, job_id=job_id)

                # Step 2: Fallback to targeted MCP answer resolution only if AI was unable to resolve
                if leftovers:
                    print(f"[MCPServer] Step 2: Attempting fallback MCP resolution for {len(leftovers)} remaining field(s)...")
                    still_unresolved = []
                    for lf in leftovers:
                        try:
                            s_res = await self.solve_live_leftover_tag(lf, job_id=job_id)
                            r_status = s_res.get("result", {}).get("status")
                            if r_status != "success":
                                still_unresolved.append(lf)
                        except Exception:
                            still_unresolved.append(lf)
                    leftovers = still_unresolved

                # Step 3: Only if fields still remain unresolved after both AI and MCP fallback:
                if leftovers:
                    job_state["status"] = "needs_intervention"
                    self.state_manager.set_leftovers(job_id, leftovers)
                    compact_leftovers = [
                        {
                            "element_index": f.get("element_index", f.get("_element_index")),
                            "question": f.get("question") or f.get("name") or f.get("placeholder"),
                            "selector": f.get("selector"),
                            "type": f.get("type", "text"),
                            "options": (f.get("options") or [])[:5] if isinstance(f.get("options"), list) else []
                        }
                        for f in leftovers
                    ]
                    return {
                        "status": "need_your_response",
                        "job_id": job_id,
                        "page_index": page_index,
                        "message": f"Form filled {summary.get('verified_count', 0)} fields. {len(compact_leftovers)} field(s) require intervention.",
                        "leftover_fields": compact_leftovers,
                        "instructions": (
                            "Please answer ONLY the leftover question(s) above using do_action(action='fill'|'select'|'check', identifier=element_index_or_selector, data=value). "
                            "Do NOT alter or touch already-filled fields. "
                            f"Then call apply_page(Multiple_page={multiple_pages}, job_id='{job_id}', continue_post=True) to submit."
                        ),
                    }

                # 3. Check and handle captcha right before submit/next page
                captcha_res = await self.detect_and_handle_captcha(job_id)
                if captcha_res.get("has_captcha") and not captcha_res.get("solved"):
                    return {
                        "status": "needs_captcha_solved",
                        "job_id": job_id,
                        "message": "Captcha detected! Please complete the captcha in the open Brave browser window or on http://localhost:8085, then call apply_page with continue_post=True. Do NOT modify already-filled fields.",
                        "url": self.page.url,
                    }

                # 4. Multi-page pagination
                next_btn = await self._find_next_button()
                if multiple_pages or next_btn is not None:
                    # 1. Check if application has already reached confirmation screen
                    is_done = await self.page.evaluate("""() => {
                        const text = (document.body ? document.body.innerText : '').toLowerCase();
                        return text.includes('thanks for submitting') || text.includes('application submitted') || text.includes('thank you for applying') || text.includes('your application has been submitted') || text.includes('application received');
                    }""")
                    if is_done:
                        print(f"[MCPServer] Multi-page flow complete: confirmation detected on page {page_index}!")
                        return {
                            "status": "success",
                            "message": "Application submitted successfully! Confirmation detected on page.",
                            "job_id": job_id,
                            "url": self.page.url,
                            "screenshot_file": "final_submission_success.png"
                        }

                    # 2. Check if the page / step ALREADY auto-advanced from field interaction (e.g. radio choice click or file upload)
                    sig_after = ""
                    try:
                        sig_after = await self.page.evaluate("""() => {
                            const h = document.querySelector('.ant-steps-item-active, .step.active, [class*="step" i][class*="active" i], h1, h2, h3, [class*="title"], [class*="question"]');
                            const inpCount = document.querySelectorAll('input:not([type="hidden"]), select, textarea').length;
                            return window.location.href + '::' + (h ? h.innerText.trim() : '') + '::' + inpCount;
                        }""")
                    except Exception:
                        pass

                    has_choice_action = any(
                        (f.get("action") in {"radio", "choice", "button", "check", "upload"}) or (f.get("method") == "radio_option_selector")
                        for f in summary.get("filled_fields", [])
                    )

                    if has_choice_action and sig_before and sig_after and sig_before != sig_after:
                        new_head = sig_after.split('::')[-1]
                        print(f"[MCPServer] Step auto-advanced to: '{new_head}' on page {page_index + 1} without needing button click.")
                        page_index += 1
                        continue_post = False
                        continue

                    # 3. Check if final submit button is visible and no Next / OK button exists
                    submit_btn = None
                    if not next_btn:
                        for s_sel in ['button:has-text("Submit Application")', 'button:has-text("Submit application")', 'button:has-text("Submit")', 'button[type="submit"]:has-text("Submit")', 'button:has-text("Apply Now")']:
                            loc = self.page.locator(s_sel).first
                            try:
                                if await loc.count() > 0 and await loc.is_visible():
                                    submit_btn = loc
                                    break
                            except Exception:
                                pass

                    if submit_btn and not next_btn:
                        print(f"[MCPServer] Reached final page with Submit button on page {page_index}. Submitting...")
                        return await self._submit_form(job_id)

                    # 3. Advance to next page / step
                    advanced = False
                    if next_btn:
                        print(f"[MCPServer] Clicking Next / OK button on page {page_index}...")
                        try:
                            await next_btn.click(timeout=4000)
                            advanced = True
                        except Exception:
                            try:
                                await next_btn.click(force=True, timeout=4000)
                                advanced = True
                            except Exception:
                                pass

                    if not advanced:
                        try:
                            await self.page.keyboard.press("Enter")
                            advanced = True
                        except Exception:
                            pass

                    await self.page.wait_for_timeout(2500)

                    # 4. Error verification: Check if validation error appeared on the page
                    error_info = await self.page.evaluate("""() => {
                        const candidates = document.querySelectorAll('.error, [aria-invalid="true"], .q__error, [class*="error-message" i], [class*="invalid" i], [role="alert"]');
                        for (const el of candidates) {
                            if (el.offsetParent !== null && (el.innerText || '').trim()) {
                                const container = el.closest('.field, .form-group, div, section') || document.body;
                                const input = container.querySelector('input:not([type="hidden"]), select, textarea') || document.querySelector(':invalid');
                                return {
                                    error_text: el.innerText.trim(),
                                    tag: input ? input.tagName.toLowerCase() : '',
                                    id: input ? input.id : '',
                                    name: input ? input.name : '',
                                    type: input ? input.type : '',
                                    value: input ? input.value : '',
                                    selector: input ? (input.id ? '#' + CSS.escape(input.id) : (input.name ? input.tagName.toLowerCase() + '[name="' + input.name + '"]' : '')) : ''
                                };
                            }
                        }
                        return null;
                    }""")

                    if error_info and error_info.get("error_text"):
                        err_msg = error_info["error_text"]
                        print(f"[MCPServer] Error detected on page {page_index}: '{err_msg}'. Capturing screenshot...")
                        shot_res = await self.get_page_screenshot()

                        # Attempt AI repair of the errored field
                        repaired = False
                        if error_info.get("selector"):
                            try:
                                print(f"[MCPServer] Asking Bedrock AI to repair field {error_info['selector']} with error '{err_msg}'...")
                                resume_info = self.automatic_filling._resume_identity() if hasattr(self.automatic_filling, "_resume_identity") else {}
                                repair_prompt = f"""You are a universal application form repair engine.
A field failed validation with error message: "{err_msg}".
Field details:
- Tag: {error_info.get('tag')}
- Type: {error_info.get('type')}
- Current Value: "{error_info.get('value')}"
- Name/ID: {error_info.get('name') or error_info.get('id')}

Applicant Data:
{json.dumps(resume_info)}

Provide the exact compliant value that resolves this error without failing validation.
For any URL, website, or project field, always use the applicant's real deployed website / portfolio: "{resume_info.get('website') or resume_info.get('portfolio') or ''}". Never invent fake domains.
Return JSON: {{"repaired_value": "compliant_string"}}"""
                                ai_rep = await asyncio.to_thread(self.automatic_filling.call_bedrock, repair_prompt)
                                if isinstance(ai_rep, dict) and ai_rep.get("repaired_value"):
                                    rep_val = ai_rep["repaired_value"]
                                    rep_loc = self.page.locator(error_info["selector"]).first
                                    await rep_loc.fill(rep_val)
                                    await self.page.wait_for_timeout(400)
                                    if next_btn:
                                        await next_btn.click()
                                    else:
                                        await self.page.keyboard.press("Enter")
                                    await self.page.wait_for_timeout(2000)
                                    still_err = await self.page.evaluate("""() => {
                                        const c = document.querySelector('.error:not(:empty), .q__error:not(:empty), [role="alert"]:not(:empty)');
                                        return c && c.offsetParent !== null ? c.innerText.trim() : null;
                                    }""")
                                    if not still_err:
                                        print(f"[MCPServer] Error successfully resolved by AI repair with '{rep_val}'!")
                                        repaired = True
                            except Exception as rep_err:
                                print(f"[MCPServer] AI error repair failed: {rep_err}")

                        if not repaired:
                            return {
                                "status": "needs_intervention",
                                "job_id": job_id,
                                "page_index": page_index,
                                "error": err_msg,
                                "screenshot": shot_res.get("path") if isinstance(shot_res, dict) else None,
                                "message": f"Error on page {page_index}: '{err_msg}'. Screenshot captured for MCP verification.",
                                "instructions": "Please use do_action to correct the field or address the error, then call apply_page with continue_post=True."
                            }

                    page_index += 1
                    continue_post = False
                    continue

                # Final page -> Submit!
                return await self._submit_form(job_id)

            return await self._submit_form(job_id)

        except Exception as e:
            traceback.print_exc()
            return {"status": "error", "message": f"Exception in run_application_state: {e}"}

    async def apply_page(self, Multiple_page: bool = False, extract_data: Any = None, job_id: Optional[str] = None, continue_post: bool = False):
        """Alias for run_application_state for seamless MCP tool calling."""
        return await self.run_application_state(job_id=job_id, multiple_pages=Multiple_page, continue_post=continue_post)
