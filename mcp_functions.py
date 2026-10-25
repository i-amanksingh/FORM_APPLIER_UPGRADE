import sys
import os
from browser_control import BrowserManager
from dom_extractor import FormFieldExtractor
from working_fill_feilds import FormFiller
import re

import dom_extractor

class MCPServer:
    """MCP Server for form filling operations."""

    def __init__(self):
        self.browser_manager= None
        self.executor= None
        self._last_inspect_locators= set()
        self.page = None
        self.current_page_url = None
        self.current_page_title = None
        self.dom_extractor = None
        self.automatic_filling = None
        self._initialized = False

    async def _ensure_browser_session(self) -> None:
        """Ensure Playwright browser/page is alive; sync executor + registry."""
        if not self.browser_manager:
            await self.initialize()
            return
        page = await self.browser_manager.ensure_session()
        self.registry.set_page(page)
        if self.executor:
            self.executor.page = page
   
    async def initialize(self):
            if self._initialized:
                try:
                    await self._ensure_browser_session()
                except Exception as e:
                    self._initialized = False
                    return {"status": "error", "message": str(e)}
                return {
                    "status": "already_initialized",
                    "session_alive": self.browser_manager.session_is_alive(),
                }

            self.browser_manager = BrowserManager(headless=False)
            await self.browser_manager.start()
   
    async def open_page(self, url):
        try:
            url = (url or "").strip()
            if not url:
                return {
                    "status": "error",
                    "message": (
                        "URL IS NOT THERE AGAIN CALL open_page with accurate url , start with Http or Https "
                    ),
                }

            if not self._initialized:
                init = await self.initialize()
                if init.get("status") == "error":
                    return init
                if not self._initialized:
                    return {
                        "status": "error",
                        "message": init.get("message") or "Browser failed to initialize",
                    }
            
            await self._ensure_browser_session()
            result = await self.browser_manager.navigate_to(url)    
            if result.get("status") == "success":
                self.current_page_url = result.get("url")
                self.current_page_title = result.get("title")
                self.page = result.get("page")
                final_url = (result.get("url")).lower()      
                title = (result.get("title") or "").lower() 
                self.dom_extractor = FormFieldExtractor(url = self.current_page_url)
                self.automatic_filling = FormFiller(page= self.page , url= self.current_page_url)
                if (
                    "errortype=404" in final_url
                    or "/errorpage" in final_url
                    or "page not found" in title
                    or "404" in title
                ):
                    return {
                        "status": "error",
                        "message": (
                            "Opened URL landed on a 404/error page. "
                            "The URL was wrong or invented. "
                            "Call open_next_job(company_contains='EY') for a real DB URL."
                        ),
                        "url": result.get("url"),
                        "title": result.get("title"),
                        "requested_url": url,
                    }    

            return result          
        except Exception as e:
            return {"status": "error", "message": str(e)}
        
    async def extract_dom(self, page = None):
        try:
            if not page : 
                page = self.page
            feilds = await self.dom_extractor(page)
            if feilds:
                return {"feild" : feilds}
            
            return "There is no feilds detected , get screenshot to get page content and understand problem"
        
        except Exception as e:
            return e
 
    def _normalize_locator(locator: str) -> str:
        loc = (locator or "").strip()
        if re.match(r"^#\d", loc):
            return f'[id="{loc[1:]}"]'
        return loc 
 

    async def get_page_screenshot(self):
        
        if not self.browser_manager or not self.browser_manager.page:
            return {"status": "error", "message": "No active page"}

        page = self.browser_manager.page
        try:

            data = await page.screenshot(
                type="jpeg", quality=75, full_page=False, animations="disabled", timeout=4000,
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

        # Type only into searchable controls.
        try:
            input_type = await element.get_attribute("type") or ""
            editable = tag in ("input", "textarea") or await element.get_attribute("contenteditable") == "true"
            if editable and input_type not in ("checkbox", "radio", "file", "hidden"):
                await element.fill(wanted, timeout=3000)
        except Exception:
            try:
                await element.press_sequentially(wanted, delay=25, timeout=3000)
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
        ]

        best_option, best_text, best_score = None, "", 0

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

                    score = await self._score_option_text(text, wanted)
                    if score > best_score:
                        best_option, best_text, best_score = option, text, score

                    if score == 100:
                        break
                except Exception:
                    continue

            if best_score == 100:
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
            await self.page.wait_for_timeout(300)
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
        if not actual:
            try:
                actual = (await element.inner_text()).strip()
            except Exception:
                pass

        label = actual
        if not expected or not actual:
            return False, actual, label

        exp = expected.lower()
        act = actual.lower()
        ok = exp == act or exp in act or act in exp
        return ok, actual, label
        
    async def do_action(self, selector, action_type ,target,  value = None, locator = None):
        try:
            if not self.page:
                await self.open_page(self.current_page_url)
            element = await self.resolve_element(target)
            
            if action_type == "upload"and value:
                direct = await self.upload_file_direct(
                    str(value),
                    hint=target.get("hint"),
                )
                direct["target"] = target
                return direct
           
            if await element.count() == 0:
                return {
                    "status": "error",
                    "message": "Element not found",
                    "target": target,
                    "action": action_type,
                }          
            
            if action_type == "fill":
                if value is None:
                    return {"status": "error", "message": "Missing value for fill"}

                value_str = str(value)              

                try:
                    meta = await element.evaluate(
                        """el => ({
                            type: (el.type || '').toLowerCase(),
                            name: (el.name || '').toLowerCase(),
                            id: (el.id || '').toLowerCase(),
                            autocomplete: (el.autocomplete || '').toLowerCase(),
                            placeholder: (el.placeholder || '').toLowerCase(),
                            aria: (el.getAttribute('aria-label') || '').toLowerCase()
                        })"""
                    )
                except Exception:
                    meta = {}
                blob = " ".join([
                    locator.lower(),
                    meta.get("type", ""),
                    meta.get("name", ""),
                    meta.get("id", ""),
                    meta.get("autocomplete", ""),
                    meta.get("placeholder", ""),
                    meta.get("aria", ""),
                ])
                
                await element.fill(value_str)
                await self.page.wait_for_timeout(150)
                
                actual_value = await element.input_value()
                ok = actual_value == value      
                if not ok:
                    try:
                        await element.fill("")
                        await element.type(value, delay=25)
                        await self.page.wait_for_timeout(150)
                        actual_value = await element.input_value()
                        ok = actual_value == value
                    except Exception:
                        pass
                    
                return {
                    "status": "success" if ok else "error",
                    "action": "fill",
                    "target": target,
                    "expected": value,
                    "actual": actual_value,
                    "verified": ok,
                    "message": (
                        "Fill executed"
                        if ok
                        else "Field was filled but verification failed try to get screenshot to get understanding"
                    ),
                }
                
            elif action_type == "clear":
                await element.click(timeout=5000)
                await element.fill("")
                await element.press("Control+A")
                await element.press("Backspace")
                actual_value = ""
                try:
                    actual_value = await element.input_value()
                except Exception:
                    pass
                ok = actual_value == ""       
                return {
                    "status": "success" if ok else "error",
                    "action": "clear",
                    "target": target,
                    "actual": actual_value,
                    "verified": ok,
                    "message": "Cleared" if ok else "Clear failed",
                }
                        
            elif action_type == "check":
                await element.check()
                checked = await element.is_checked()    
                return {
                "status": "success" if checked else "error",
                "action": "check",
                "target": target,
                "checked": checked,
                "verified": checked,
                "message": "Check executed" if checked else "Checkbox/radio was not checked",
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
                "message": "Uncheck executed" if ok else "Checkbox was not unchecked",
            }          
                      
            elif action_type == "select":
                if value is None:
                    return {"status": "error", "message": "Missing value for select"}

                wanted = str(value).strip()
                tag = await element.evaluate("el => el.tagName.toLowerCase()")

                aliases = {
                    "india": ["IN", "IND", "India"],
                    "in": ["IN", "India"],
                    "united states": ["US", "USA", "United States"],
                    "us": ["US", "United States"],
                    "usa": ["US", "USA", "United States"],
                }
                candidates = aliases.get(wanted.lower(), [wanted, wanted.upper(), wanted.title()])
                candidates = list(dict.fromkeys(candidates + [wanted]))

                options = await element.evaluate("""el => [...el.options].map(o => ({
                    value: o.value, text: (o.textContent || '').trim(), disabled: o.disabled
                }))""")

                selected = None
                last_err = None

                for candidate in candidates:
                    for mode in ("label", "value"):
                        try:
                            await element.select_option(**{mode: candidate}, timeout=3000)
                            selected = candidate
                            break
                        except Exception as e:
                            last_err = e
                    if selected is not None:
                        break

                if selected is None:
                    wanted_l = wanted.lower()
                    match = next((
                        o for o in options
                        if not o["disabled"] and (
                            wanted_l == o["text"].lower()
                            or wanted_l == o["value"].lower()
                            or wanted_l in o["text"].lower()
                            or o["text"].lower() in wanted_l
                        )
                    ), None)

                    if match:
                        try:
                            await element.select_option(value=match["value"], timeout=5000)
                            selected = match["value"]
                        except Exception as e:
                            last_err = e
                            try:
                                await element.select_option(label=match["text"], timeout=5000)
                                selected = match["text"]
                            except Exception as e2:
                                last_err = e2

                try:
                    actual_value = await element.input_value()
                    actual_label = await element.evaluate(
                        "el => el.selectedOptions[0]?.text.trim() || ''"
                    )
                except Exception:
                    actual_value = actual_label = ""

                ok = selected is not None and actual_value not in ("", "-1", "0", "null")
                ok = ok or bool(actual_label and wanted.lower() in actual_label.lower())

                if not ok:
                    context = await self._find_frame_with_element(locator)
                    fallback = await self._smart_select_custom(element, context, wanted, target)
                    if fallback.get("verified"):
                        return fallback

                return {
                    "status": "success" if ok else "error",
                    "action": "select",
                    "target": target,
                    "expected": wanted,
                    "tried": candidates,
                    "actual_value": actual_value,
                    "actual_label": actual_label,
                    "verified": ok,
                    "available_options_sample": [
                        {"value": o["value"], "text": o["text"]}
                        for o in options[:25] if o["value"] or o["text"]
                    ] if not ok else None,
                    "message": "Select executed" if ok else (
                        f"Could not select {wanted!r}. Use an option value/text from "
                        f"available_options_sample. Last error: {last_err}"
                    ),
                }                      
        
            else:
                return {
                    "status": "error",
                    "message": f"Unknown action: {action_type}",
                    "supported_actions": [
                        "fill",
                        "clear",
                        "check",
                        "uncheck",
                        "select",
                        "click",
                        "press",
                        "upload",
                        "focus",
                    ],
                }
        
        except Exception as e:
            return  {                 
                    "status": "error",
                    "message": f"Unknown action: {action_type}",
                    "error" : e,
                    "supported_actions": [
                        "fill",
                        "clear",
                        "check",
                        "uncheck",
                        "select",
                        "click",
                        "press",
                        "upload",
                        "focus",
                    ],
                }
    
    async def get_raw_html(self):
        try:
            await self.page.wait_for_load_state("domcontentloaded", timeout=30000)
            return await self.page.content()
        except Exception as e:
            return {"error" : e}
        
    async def run_application_state(self,job_id = None, multiple_pages = False , continue_post = False):
        try:
            ensure_form_visible = await self.dom_extractor._ensure_application_form_open(self.page)
            if not ensure_form_visible:
                return {"success" : False, "Problem" : "The form is not visible try to get screenshot and fill email or password or click apply and again call when the run_application_state visible"}
            
            while True:
                if continue_post:
                    dom_extractor = self.dom_extractor.main(self.page)
                    automatic_filling = self.automatic_filling.fill(dom_extractor)
                    if automatic_filling.get("success"):
                        return {f"Congrats !!!!! , all filled is sucessfully filled view screenshot to see is everything submitted ;{self.get_page_screenshot()} "}
                    
                    elif not automatic_filling.get("success"):
                        is_reqd_mcp_intervention = automatic_filling.get("problem_statement")
                        resume_content = self.resume_content()
                        return {f"""There is Problem even Bedrock unable to solve - problem is {is_reqd_mcp_intervention}
                                & user resume content is {resume_content} ; 
                                Understand problem and using resume_content try to get answer from resume content or best match 
                                & Call again (run_application_state) with parameter as job_id , continue_post = True .     
                                    """}
        except Exception as e:
            return {f"There is Exception in current code which is {e}"}