import asyncio
import json
import os
import re
import sys

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import time
import textwrap
from pathlib import Path
from dotenv import load_dotenv
from playwright.async_api import async_playwright
import boto3
from botocore.config import Config
from botocore.exceptions import ReadTimeoutError, EndpointConnectionError

MODEL_ID = os.getenv("BEDROCK_MODEL_ID", "us.amazon.nova-pro-v1:0")
_BASE_DIR = Path(__file__).resolve().parent
OUTPUT_JSON = str(_BASE_DIR / "elements.json")
OUTPUT_INVENTORY = str(_BASE_DIR / "page_inventory.json")
OUTPUT_FILE = str(_BASE_DIR / "important_data_fields.json")
OUTPUT_HTML = str(_BASE_DIR / "form.html")

FIELD_KEYWORD_RULES = [
    ("first_name", ["first name", "firstname", "given name"]),
    ("last_name", ["last name", "lastname", "surname", "family name"]),
    ("salutation", ["salutation", "name title", "prefix"]),
    ("middle_name", ["middle name"]),
    ("email", ["email", "e-mail", "mail id", "email id", "email address"]),
    ("phone", ["phone", "mobile", "whatsapp", "telephone", "contact number", "cellphone"]),
    ("current_salary", ["current salary", "current ctc", "current pay", "current annual", "current c.t.c", "currentsalary"]),
    ("expected_salary", ["expected salary", "expected ctc", "expected pay", "expected annual", "expected c.t.c", "expectedsalary"]),
    ("experience", ["years of experience", "total experience", "work experience", "experience", "totalexperience"]),
    ("notice_period", ["notice period", "notice", "joining time", "join days", "joindays", "days to join", "noticeperiod"]),
    ("skills", ["skills", "skill set", "technical skills", "technologies", "skill"]),
    ("current_location", ["current location", "location", "current city", "preferred location", "currentlocation"]),
    ("job_title", ["job title", "designation", "current role", "position", "position applied for", "role applied for", "target role"]),
    ("date_of_birth", ["date of birth", "dob", "birth date"]),
    ("gender", ["gender", "sex"]),
    ("address", ["address", "street address", "street", "postal address"]),
    ("city", ["city", "town"]),
    ("state", ["state", "province", "region"]),
    ("country", ["country", "nation"]),
    ("pincode", ["pincode", "pin code", "zip", "zipcode", "postal code"]),
    ("preferred_location", ["preferred location", "preferred city"]),
    ("linkedin_url", ["linkedin"]),
    ("github_url", ["github"]),
    ("portfolio_url", ["portfolio", "website", "personal site"]),
    ("current_company", ["current company", "company", "employer", "organization"]),
    ("graduation_college", ["university", "college", "institute", "school"]),
    ("graduation_degree", ["degree", "qualification", "course"]),
    ("graduation_branch", ["branch", "specialization", "major", "field of study"]),
    ("graduation_year", ["graduation year", "year of passing", "year of completion", "passing year"]),
    ("cgpa", ["cgpa", "gpa", "percentage", "marks"]),
    ("work_authorization", ["work authorization", "authorized to work", "work permit", "visa"]),
    ("currently_pursuing", ["currently pursuing", "currently studying", "pursuing", "ongoing"]),
    ("nationality", ["nationality", "citizenship"]),
    ("availability", ["availability", "available to join", "start date"]),
    ("resume", ["resume", "cv", "curriculum vitae", "attach resume", "upload resume"]),
    ("cover_letter", ["cover letter", "cover note"]),
    ("full_name", ["full name", "your name", "complete name", "candidate name", "applicant name", "legal name", "fullname"]),
]

NAVIGATION_LABELS = {
    "back", "next", "previous", "continue", "cancel", "close", "dismiss",
    "save", "save draft", "save and continue", "save & continue", "discard",
    "reset", "clear", "delete", "remove", "edit", "view", "menu", "open menu",
    "open navigation", "toggle", "toggle menu", "sign in", "log in", "login",
    "logout", "sign out", "register", "share", "print", "download", "copy link",
    "submit", "submit application", "apply", "apply now",
}

SECTION_ACTION_RE = re.compile(
    r"^\s*\+?\s*(add|include|insert)\s+"
    r"(another\s+|new\s+|more\s+|work\s+|education\s+|language\s+|"
    r"experience\s+|position\s+|qualification\s+|school\s+|project\s+|"
    r"certification\s+|reference\s+|document\s+|attachment\s+|degree\s+|"
    r"course\s+|skill\s+|a\s+)",
    re.I,
)

APPLY_TEXT_RE = re.compile(
    r"\b(apply|application|interested|join us|register|quick apply|easy apply|start|get started|begin)\b",
    re.I,
)
APPLY_BAD_RE = re.compile(
    r"\b(filter|login|sign in|log in|search|subscribe|download|share|submit|send|finish|done|next|previous|continue|save|linkedin|indeed|google|facebook|apple|oauth|social)\b|"
    r"apply\s+with\b",
    re.I,
)

IGNORABLE_FRAME_RE = re.compile(
    r"recaptcha|google.*analytics|googletagmanager|doubleclick|"
    r"facebook\.com/plugins|intercom|zendesk|tawk\.to|crisp\.chat|"
    r"livechat|drift\.com|embedded-viral-link",
    re.I,
)

DISCOVERY_JS = r"""
() => {
  const clean = s => (s == null ? '' : String(s)).replace(/\s+/g, ' ').trim();
  const visible = el => {
    if (!el || !el.getBoundingClientRect) return false;
    const r = el.getBoundingClientRect();
    const cs = getComputedStyle(el);
    return !!(r.width || r.height) && cs.display !== 'none'
        && cs.visibility !== 'hidden' && cs.opacity !== '0';
  };
  const escapeAttr = v => String(v).replace(/\\/g, '\\\\').replace(/"/g, '\\"');
  const count = sel => { try { return document.querySelectorAll(sel).length; } catch (e) { return -1; } };

  const all = [];
  (function walk(root) {
    const tw = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT);
    let n;
    while ((n = tw.nextNode())) {
      all.push(n);
      if (n.shadowRoot) walk(n.shadowRoot);
    }
  })(document);

  const CHROME_WORDS = /\b(navbar|navigation|topbar|top bar|sidebar|side bar|breadcrumb|breadcrumbs|site header|site footer|site nav|page header|page footer|masthead|footer)\b/;
  const isInChrome = el => {
    let cur = el;
    while (cur && cur.nodeType === 1 && cur !== document.documentElement) {
      const t = cur.tagName ? cur.tagName.toLowerCase() : '';
      const role = (cur.getAttribute('role') || '').toLowerCase();
      if (t === 'main' || role === 'main') {
        cur = cur.parentElement;
        continue;
      }
      if (['header', 'nav', 'footer', 'aside'].includes(t)) return true;
      if (['navigation', 'banner', 'contentinfo', 'complementary', 'menu',
           'menubar', 'search'].includes(role)) return true;
      const rawClass = typeof cur.className === 'string' ? cur.className : '';
      // If container explicitly indicates inactive or hidden navigation, it is NOT active chrome
      if (/nav[-_ ]?(hidden|inactive|banner[-_ ]?inactive)/i.test(rawClass)) {
        cur = cur.parentElement;
        continue;
      }
      // If element contains primary application form fields, it is NEVER chrome!
      if (cur.querySelector && cur.querySelector('input[name="name"], #first-name, input[name="email"], input[type="file"], input[name="surname"], input#phone, textarea[name="cover_letter"]')) {
        cur = cur.parentElement;
        continue;
      }
      const cleanClass = rawClass.replace(/var\([^)]*\)/g, '').replace(/[-_\[\]]+/g, ' ');
      const cid = (cleanClass + ' ' + (cur.id || '')).toLowerCase().replace(/[-_]+/g, ' ').replace(/\s+/g, ' ');
      if (CHROME_WORDS.test(cid) || /\bnav\b/.test(cid)) {
        if (/\bnav (height|offset|width|padding|margin|spacing|size)\b/.test(cid)) {
          // Sizing or utility reference, not actual navigation chrome
        } else {
          return true;
        }
      }
      cur = cur.parentElement;
    }
    return false;
  };

  const EXCLUDE_ROLES = new Set(['option', 'listbox', 'menu', 'menubar', 'menuitem',
      'menuitemcheckbox', 'menuitemradio', 'presentation', 'none', 'grid', 'gridcell',
      'row', 'rowgroup', 'tree', 'treeitem', 'separator', 'status', 'alert', 'log',
      'marquee', 'timer', 'toolbar', 'searchbox']);
  const POPUP_SEL = '[role="listbox"], [role="menu"], [role="tree"], [role="grid"], [role="menubar"], [role="toolbar"]';
  const POPUP_WORDS = /drop[-_ ]?body|drop[-_ ]?down[-_ ]?(body|menu|list|panel)|dropdown[-_ ]?(body|menu|list|panel)|popup|listbox|menu[-_ ]?(list|panel)/i;
  const isInPopup = el => {
    let cur = parentOf(el), hops = 0;
    while (cur && hops < 15) {
      const t = cur.tagName ? cur.tagName.toLowerCase() : '';
      const id = (cur.id || '').toLowerCase();
      const cls = typeof cur.className === 'string' ? cur.className.toLowerCase() : '';
      if (POPUP_WORDS.test(t + ' ' + id + ' ' + cls)) return true;
      cur = parentOf(cur);
      hops++;
    }
    return false;
  };

  const isInMarketingOrNewsletterPopup = el => {
    let cur = el, hops = 0;
    while (cur && hops < 15) {
      if (cur === document.body || cur === document.documentElement) break;
      const t = cur.tagName ? cur.tagName.toLowerCase() : '';
      const isOverlay = t === 'dialog' || cur.getAttribute('role') === 'dialog';
      let pos = '';
      try { pos = window.getComputedStyle(cur).position; } catch(e) {}
      if (isOverlay || pos === 'fixed' || pos === 'absolute') {
        const hasResume = !!cur.querySelector('input[type="file"], input[name*="resume" i], input[name*="cv" i]');
        const inps = cur.querySelectorAll('input:not([type="hidden"]), select, textarea').length;
        if (!hasResume && inps < 4) {
          const txt = (cur.innerText || '').toLowerCase();
          if (txt.includes('newsletter') || txt.includes('zero noise') || txt.includes('architect') ||
              txt.includes('subscribe') || txt.includes('job alert') || txt.includes('talent community') ||
              txt.includes('editorial briefing') || txt.includes('inner circle')) {
            return true;
          }
        }
      }
      cur = parentOf(cur);
      hops++;
    }
    return false;
  };


  const hasForm = !!document.querySelector('form');
  const SUBMIT_NAME_RE = /submit|apply|save|send|register|continue|proceed|sign in|log in|login|next/i;
  const SEARCH_RE = /search|filter|type to search|find/i;
  const WIDGET_RE = /easy|quick|social|oauth|login|signin|sign-in|facebook|linkedin|google|apple|microsoft/i;

  const isSearchInput = el => {
    const role = (el.getAttribute('role') || '').toLowerCase();
    if (role === 'searchbox') return true;
    const s = ((el.getAttribute('placeholder') || '') + ' ' + (el.getAttribute('aria-label') || '')).toLowerCase();
    return SEARCH_RE.test(s);
  };

  const parentOf = el => {
    if (el && el.parentElement) return el.parentElement;
    const root = el && el.getRootNode ? el.getRootNode() : null;
    return (root && root.host) ? root.host : null;
  };

  const isFieldWrapper = el => {
    const t = el.tagName ? el.tagName.toLowerCase() : '';
    if (t === 'fieldset') return true;
    const cls = typeof el.className === 'string' ? el.className.toLowerCase() : '';
    return /(form[-_ ]?(field|group|row|item)|field[-_ ]?(wrapper|container)|input[-_ ]?wrapper|form-input|form-group|form-field)/.test(cls);
  };

  const LABEL_ATTRS = ['cx-prop-label', 'data-zcqa', 'data-label', 'data-qa',
      'aria-label', 'label', 'title', 'data-field', 'data-testid', 'name'];
  const hasLabelAttr = el => {
    if (!el || !el.getAttribute) return false;
    for (const a of LABEL_ATTRS) {
      const v = el.getAttribute(a);
      if (v && String(v).trim()) return true;
    }
    return false;
  };
  const SUBWIDGET_RE = /input|dropdown|autocomplete|picklist|combobox|listbox|select|file-?upload|checkbox|radio|drop-|drophead|dropbody|dropbutton|dropbox|dropitem|menu|toolbar|switch|slider|button|yield|template/i;

  const fieldComponent = el => {
    let cur = el, hops = 0;
    while (cur && hops < 25) {
      const t = cur.tagName ? cur.tagName.toLowerCase() : '';
      if (t.includes('-')) {
        if (hasLabelAttr(cur)) return cur;
        if (!SUBWIDGET_RE.test(t)) return cur;
        cur = parentOf(cur); hops++; continue;
      }
      if (isFieldWrapper(cur)) return cur;
      cur = parentOf(cur); hops++;
    }
    return null;
  };

  const componentAncestor = fieldComponent;

  const isInWidget = el => {
    let cur = el;
    while (cur && cur !== document.documentElement) {
      const t = cur.tagName ? cur.tagName.toLowerCase() : '';
      if (t.includes('-') && WIDGET_RE.test(t)) return true;
      cur = cur.parentElement;
    }
    return false;
  };

  const isSocialAuthButton = el => {
    if (!el) return false;
    const text = clean(el.innerText || el.textContent || el.getAttribute('aria-label') || el.getAttribute('value') || '').toLowerCase();
    const cls = typeof el.className === 'string' ? el.className.toLowerCase() : '';
    const id = (el.id || '').toLowerCase();
    return /apply\s+with\s+(linkedin|indeed|google|facebook|apple)|sign\s*in\s*with|log\s*in\s*with|social[-_]button|linkedin[-_]button|indeed[-_]apply/i.test(text + ' ' + cls + ' ' + id);
  };

  const isSubmitButton = el => {
    if (isSocialAuthButton(el)) return false;
    const name = ((el.getAttribute('aria-label') || el.getAttribute('value') || el.getAttribute('name') || el.textContent) || '').toLowerCase();
    if (!SUBMIT_NAME_RE.test(name)) return false;
    if (hasForm && !el.closest('form, [class*="widget" i], [class*="app" i]')) return false;
    return true;
  };

  const isFileTrigger = el => {
    const tag = el.tagName ? el.tagName.toLowerCase() : '';
    const type = (el.getAttribute('type') || '').toLowerCase();
    if (tag === 'input' && type === 'file') return true;
    if (tag === 'label') {
      const forId = el.getAttribute('for');
      if (forId) {
        const tgt = document.getElementById(forId);
        if (tgt && tgt.tagName && tgt.tagName.toLowerCase() === 'input'
            && (tgt.getAttribute('type') || '').toLowerCase() === 'file') return true;
      }
      if (el.querySelector && el.querySelector('input[type="file"]')) return true;
    }
    const role = (el.getAttribute('role') || '').toLowerCase();
    if (tag === 'button' || role === 'button' || tag === 'a') {
      if (el.querySelector && el.querySelector('input[type="file"]')) return true;
      const idClass = ((el.id || '') + ' ' + (typeof el.className === 'string' ? el.className : '')).toLowerCase();
      if (/openresume|uploadresume|resumeupload|btn-upload-resume|btn-upload-photo/i.test(idClass)) return true;
      const text = clean(el.innerText || el.textContent || el.getAttribute('aria-label') || '').toLowerCase();
      if (/\b(upload|attach|browse)\b/i.test(text) && /\b(resume|cv|file|document)\b/i.test(text)) return true;
      const parentGrp = el.closest('.form-group, .form-row, fieldset, [class*="group"], [class*="field"]');
      if (parentGrp) {
        const grpLbl = parentGrp.querySelector('label');
        if (grpLbl) {
          const lt = clean(grpLbl.innerText || grpLbl.textContent).toLowerCase();
          const lfor = (grpLbl.getAttribute('for') || '').toLowerCase();
          if ((lfor === 'resume' || /\b(upload\s+resume|resume|cv)\b/i.test(lt)) && /\b(upload|browse|choose|select)\b/i.test(text)) {
            return true;
          }
        }
      }
    }
    return false;
  };

  const DATEPICKER_CLASS_RE = /(datepicker|date-picker|calendar|hasDatepicker|ki-calendar|month-picker)/i;
  const DATEPICKER_CONTAINER_RE = /(input-calendar|datepicker|date-picker|calendar|month-picker)/i;
  const isWidgetTrigger = el => {
    if (!el) return false;
    const tag = (el.tagName || '').toLowerCase();
    const type = (el.getAttribute('type') || '').toLowerCase();
    const testId = (el.getAttribute('data-testid') || '').toLowerCase();
    const cls = typeof el.className === 'string' ? el.className : '';
    const ariaPopup = (el.getAttribute('aria-haspopup') || '').toLowerCase();

    if (tag === 'input' && (type === 'date' || type === 'month' || type === 'datetime-local')) {
      return true;
    }
    const idName = ((el.id || '') + ' ' + (el.getAttribute('name') || '') + ' ' + (el.getAttribute('data-content') || '')).toLowerCase();
    const DATE_ID_NAME_RE = /(date[-_ ]?(of[-_ ]?)?(birth|joining|relieving|completion|graduation|start|end)|birth[-_ ]?date|join[-_ ]?date|\bdob\b)/i;
    if (/date|calendar|picker/i.test(testId) || DATEPICKER_CLASS_RE.test(cls) || DATE_ID_NAME_RE.test(idName)) {
      return true;
    }
    if ((tag === 'button' || tag === 'input') && (ariaPopup === 'dialog' || ariaPopup === 'grid')) {
      const text = (el.innerText || el.value || '').toLowerCase();
      const lab = labelFor(el).toLowerCase();
      if (/date|calendar|month|year|day/i.test(text + ' ' + lab)) return true;
    }
    if (tag === 'input' && (!type || type === 'text')) {
      let cur = el.parentElement, hops = 0;
      while (cur && hops < 3) {
        const ccls = typeof cur.className === 'string' ? cur.className : '';
        const cid = cur.id || '';
        if (DATEPICKER_CONTAINER_RE.test(ccls + ' ' + cid)) return true;
        cur = cur.parentElement; hops++;
      }
    }
    return false;
  };

  const isHiddenHoneypot = el => {
    const t = (el && el.tagName) ? el.tagName.toLowerCase() : '';
    if (t === 'select') return false;
    let cur = el;
    while (cur && cur.nodeType === 1 && cur !== document.documentElement) {
      const curTag = (cur.tagName || '').toLowerCase();
      if (curTag === 'select') return false;
      if (cur.getAttribute('aria-hidden') === 'true') return true;
      const cls = typeof cur.className === 'string' ? cur.className.toLowerCase() : '';
      if (/\b(sr-only|visually-hidden|hidden-honeypot|screen-reader-only)\b/.test(cls)) return true;
      cur = cur.parentElement;
    }
    return false;
  };

  const isChatOrSupport = el => {
    const text = ((el.getAttribute('aria-label') || '') + ' ' + (el.id || '') + ' ' + (typeof el.className === 'string' ? el.className : '')).toLowerCase();
    return /\b(chat|intercom|zendesk|drift|tawk|crisp|livechat|support-widget|feedback-widget)\b/.test(text);
  };

  const isControl = el => {
    if (isChatOrSupport(el)) return false;
    if (isSocialAuthButton(el)) return false;
    const tag = el.tagName ? el.tagName.toLowerCase() : '';
    const type = (el.getAttribute('type') || '').toLowerCase();
    const role = (el.getAttribute('role') || '').toLowerCase();
    if (type === 'hidden') return false;
    if (tag === 'a') return false;
    if (type !== 'file' && isHiddenHoneypot(el)) return false;
    // Ignore Google Translate widget elements
    if (el.closest && el.closest('.goog-te-banner-frame, #google_translate_element, [id*="goog-gt-"], [class*="goog-te-"]')) {
      return false;
    }
    if (isInChrome(el)) return false;
    if (isInWidget(el)) return false;
    if (isInMarketingOrNewsletterPopup(el)) return false;
    if (EXCLUDE_ROLES.has(role)) return false;
    if (el.closest(POPUP_SEL)) return false;
    if (isInPopup(el)) return false;
    if (tag === 'input' && /search.*(country|dial|code)/i.test(
        (el.getAttribute('placeholder') || '') + ' ' + (el.getAttribute('aria-label') || ''))) return false;

    // Ignore internal presentation elements inside custom select containers (e.g. select2, chosen)
    if (el.closest('.select2, .select2-container, .chosen-container, [class*="select2-container"]')) {
      return false;
    }

    // Exclude phone country flag button from standalone controls ONLY if a native <select> already exists in that container
    if (tag === 'button' && !isSubmitButton(el) && (el.closest('.PhoneInput, .iti, [class*="phone-input" i], [class*="tel-input" i]') || (el.parentElement && el.parentElement.tagName.toLowerCase() !== 'form' && el.parentElement.children.length <= 4 && el.parentElement.querySelector('input[type="tel"], input.PhoneInputInput, input[placeholder*="phone" i]')))) {
      const parentContainer = el.closest('.PhoneInput, .iti, [class*="phone-input" i], [class*="tel-input" i]') || el.parentElement;
      if (parentContainer && parentContainer.querySelector('select')) {
        return false;
      }
      return true;
    }

    // Generic Custom Dropdown/Combobox Trigger Button detection
    const isCustomSelectTrigger = (tag === 'button' || role === 'button') && (
      role === 'combobox' || role === 'listbox' ||
      el.hasAttribute('aria-haspopup') || el.hasAttribute('aria-expanded') ||
      !!(el.id && (el.getRootNode ? el.getRootNode() : document).querySelector(`label[for="${CSS.escape(el.id)}"]`)) ||
      !!el.closest('[data-testid*="select" i], [data-testid*="combobox" i], [class*="select-root" i], [class*="multi-select" i], [class*="custom-select" i]')
    );
    if (isCustomSelectTrigger && !isSubmitButton(el)) {
      return true;
    }

    // Check accessible roles like radio, checkbox, combobox BEFORE plain button check so custom choice buttons are recognized!
    if (['combobox', 'listbox', 'checkbox', 'radio', 'radiogroup', 'switch',
         'spinbutton', 'slider'].includes(role)) return true;

    // Exclude hidden mirror inputs in Ashby yes/no containers
    if (el.closest('.ashby-application-form-input-yesno, [class*="yesno" i]') && tag === 'input') {
      return false;
    }

    // Custom choice / segmented / yesno toggle buttons:
    if ((tag === 'button' || role === 'button') && (el.closest('.ashby-application-form-input-yesno, [class*="yesno" i], [class*="segmented-control" i], [class*="toggle-group" i]') || el.hasAttribute('data-option') || el.hasAttribute('aria-pressed'))) {
      return true;
    }

    if (tag === 'button' || role === 'button' || (tag === 'input' && (type === 'submit' || type === 'button'))) {
      if (isFileTrigger(el)) return true;
      return isSubmitButton(el);
    }
    // Div buttons with class btn (e.g. GoHire submit button: <div class="btn primary-btn gh-widget-btn"><p>Submit</p></div>)
    if (tag === 'div' && el.classList && el.classList.contains('btn') && isSubmitButton(el)) {
      return true;
    }
    if (['input', 'textarea', 'select'].includes(tag)) return true;

    if (role === 'textbox') {
      if (tag === 'input' || tag === 'textarea') return true;
      if (el.getAttribute('contenteditable') === 'true') return true;
      if (el.getAttribute('aria-readonly') === 'true' || el.readOnly === true) return false;
      if (el.closest('[role="combobox"], [role="listbox"]')) return false;
      return true;
    }

    if (el.getAttribute('contenteditable') === 'true') return true;
    if (el.getAttribute('aria-expanded') !== null || el.getAttribute('aria-haspopup')) return true;
    return false;
  };

  const isDynamicId = id => {
    if (!id) return true;
    if (/^(input|textarea|select|field|el|item|root|comp|ember|react|vue|radix|headlessui)-?0?\.\d+/i.test(id)) return true;
    if (/^(input|textarea|select|field|el)-\d{5,}/i.test(id)) return true;
    if (/^:r[0-9a-z]+:$/i.test(id)) return true;
    return false;
  };

  const buildCandidates = el => {
    const tag = el.tagName.toLowerCase();
    const out = [];
    const type = el.getAttribute('type');
    const name = el.getAttribute('name');

    if (el.id && !isDynamicId(el.id)) {
      out.push({ selector: '#' + CSS.escape(el.id), strategy: 'id' });
    }

    const dt = el.getAttribute('data-testid') || el.getAttribute('data-qa');
    if (dt && !/^undefined$/i.test(dt)) {
      const attr = el.getAttribute('data-testid') ? 'data-testid' : 'data-qa';
      out.push({ selector: tag + '[' + attr + '="' + escapeAttr(dt) + '"]', strategy: 'data_testid' });
    }

    if (name) {
      if (type) out.push({ selector: tag + '[name="' + escapeAttr(name) + '"][type="' + escapeAttr(type) + '"]', strategy: 'name_type' });
      out.push({ selector: tag + '[name="' + escapeAttr(name) + '"]', strategy: 'name' });
    }

    const optVal = el.getAttribute('data-option');
    if (optVal) {
      const entry = el.closest('.ashby-application-form-field-entry, [class*="field-entry" i], [class*="fieldEntry" i]');
      if (entry) {
        const entryInp = entry.querySelector('input[name]');
        const entryName = entryInp ? entryInp.getAttribute('name') : null;
        if (entryName) {
          out.push({ selector: `.ashby-application-form-field-entry:has(input[name="${escapeAttr(entryName)}"]) button[data-option="${escapeAttr(optVal)}"]`, strategy: 'ashby_yesno' });
        }
      }
      out.push({ selector: tag + '[data-option="' + escapeAttr(optVal) + '"]', strategy: 'data_option' });
    }

    const al = el.getAttribute('aria-label');
    if (al) {
      if (/Change country/i.test(al) || (el.className && typeof el.className === 'string' && el.className.includes('iti__selected-country'))) {
        out.push({ selector: tag + '[aria-label*="Change country"]', strategy: 'aria_label_partial' });
        if (el.className && typeof el.className === 'string' && el.className.includes('iti__selected-country')) {
          out.push({ selector: 'button.iti__selected-country', strategy: 'class' });
        }
      } else {
        out.push({ selector: tag + '[aria-label="' + escapeAttr(al) + '"]', strategy: 'aria_label' });
      }
    }

    const ph = el.getAttribute('placeholder');
    if (ph) out.push({ selector: tag + '[placeholder="' + escapeAttr(ph) + '"]', strategy: 'placeholder' });

    let anc = el.parentElement;
    for (let i = 0; i < 8 && anc; i++, anc = anc.parentElement) {
      if (anc.id && !isDynamicId(anc.id) && count('#' + CSS.escape(anc.id)) === 1) {
        const base = '#' + CSS.escape(anc.id);
        const local = name ? tag + '[name="' + escapeAttr(name) + '"]' : (ph ? tag + '[placeholder="' + escapeAttr(ph) + '"]' : tag);
        out.push({ selector: base + ' ' + local, strategy: 'ancestor_id' });
        break;
      }
    }

    const parts = [];
    let cur = el;
    for (let i = 0; i < 10 && cur && cur !== document.documentElement; i++) {
      let s = cur.tagName.toLowerCase();
      if (cur.id && !isDynamicId(cur.id)) { s += '#' + CSS.escape(cur.id); parts.unshift(s); break; }
      const p = cur.parentElement;
      if (p) {
        const same = Array.from(p.children).filter(c => c.tagName === cur.tagName);
        if (same.length > 1) s += ':nth-of-type(' + (same.indexOf(cur) + 1) + ')';
      }
      parts.unshift(s);
      cur = p;
    }
    out.push({ selector: parts.join(' > '), strategy: 'dom_path' });

    if (el.id && isDynamicId(el.id)) {
      out.push({ selector: '#' + CSS.escape(el.id), strategy: 'dynamic_id' });
    }
    return out;
  };

  const labelFor = el => {
    const tLower = el.tagName ? el.tagName.toLowerCase() : '';
    const typeLower = (el.getAttribute('type') || '').toLowerCase();
    if (tLower === 'button' || typeLower === 'submit' || typeLower === 'button') {
      const bt = clean(el.innerText || el.value || el.textContent);
      if (bt && bt.length >= 2 && bt.length <= 100) return bt;
    }

    // 1. Explicit bound labels (<label for="id">, el.labels, aria-label, aria-labelledby, el.closest('label'))
    const explicit = [];
    if (el.labels) for (const l of el.labels) { const t = clean(l.innerText || l.textContent); if (t) explicit.push(t); }
    if (el.id) {
      const root = el.getRootNode ? el.getRootNode() : document;
      if (root && root.querySelector) {
        const lbl = root.querySelector('label[for="' + CSS.escape(el.id) + '"]');
        if (lbl) { const t = clean(lbl.innerText || lbl.textContent); if (t) explicit.push(t); }
      }
    }
    const labelledby = el.getAttribute('aria-labelledby');
    if (labelledby) for (const id of labelledby.split(/\s+/)) {
      const n = document.getElementById(id);
      if (n) { const t = clean(n.innerText || n.textContent); if (t) explicit.push(t); }
    }
    if (el.getAttribute('aria-label')) {
      const al = clean(el.getAttribute('aria-label'));
      if (al) explicit.push(al);
    }
    const cl = el.closest('label');
    if (cl) {
      const clText = clean(cl.innerText || cl.textContent);
      if (clText) explicit.push(clText);
    }
    const rsControl = el.closest ? el.closest('.react-select__control, [class*="control"]') : null;
    if (rsControl) {
      const phEl = rsControl.querySelector('.react-select__placeholder, [class*="placeholder"], [id$="-placeholder"]');
      if (phEl) {
        const phTxt = clean(phEl.innerText || phEl.textContent);
        if (phTxt) explicit.push(phTxt);
      }
    }

    const antItem = el.closest ? el.closest('.ant-form-item, [class*="form-item"]') : null;
    if (antItem) {
      const antLbl = antItem.querySelector('.ant-form-item-label label, [class*="form-item-label"] label');
      if (antLbl) {
        const t = clean(antLbl.innerText || antLbl.textContent);
        if (t) explicit.push(t);
      }
    }

    const cleanExplicit = [...new Set(explicit.filter(Boolean))]
      .map(t => t.replace(/^[\s*✱]+|[\s*✱]+$/g, '').trim())
      .filter(t => t && t.length >= 2 && !/^(error|validation|mb-)/i.test(t));
    if (cleanExplicit.length > 0) {
      cleanExplicit.sort((a, b) => b.length - a.length);
      return cleanExplicit[0];
    }

    const ashbyEntry = el.closest && el.closest('.ashby-application-form-field-entry, [class*="field-entry" i], [class*="fieldEntry" i]');
    if (ashbyEntry) {
      const titleEl = ashbyEntry.querySelector('h1, h2, h3, h4, [class*="title" i], [class*="heading" i], [class*="label" i], p');
      if (titleEl) {
        const t = clean(titleEl.innerText || titleEl.textContent).replace(/^[\s*✱]+|[\s*✱]+$/g, '').trim();
        if (t && t.length <= 400 && !/^(error|validation)/i.test(titleEl.className || '')) {
          return t;
        }
      }
    }

    const bits = [];
    const ph = el.getAttribute('placeholder');
    let phDirect = null;
    if (ph) {
      const cleanPh = clean(ph).replace(/^[\s*✱]+|[\s*✱]+$/g, '').trim();
      if (cleanPh && !/^(enter|type|select|choose|pick)\b/i.test(cleanPh)) {
        bits.push(cleanPh);
        if (/mobile|phone|email|first\s*name|last\s*name|full\s*name|resume|city|country/i.test(cleanPh)) {
          phDirect = cleanPh;
        }
      }
    }

    let prev = el.previousElementSibling;
    while (prev) {
      if (prev.matches('label, [class*="label"], [class*="title"], [class*="question"], p, span, h3, h4, h5')) {
        const pt = clean(prev.innerText || prev.textContent);
        if (pt && pt.length <= 250) bits.push(pt);
        break;
      }
      const sub = prev.querySelector('label, [class*="label"], [class*="title"], [class*="question"]');
      if (sub) {
        const st = clean(sub.innerText || sub.textContent);
        if (st && st.length <= 250) bits.push(st);
        break;
      }
      prev = prev.previousElementSibling;
    }

    let next = el.nextElementSibling;
    while (next) {
      if (next.matches('label, [class*="label"], [class*="title"], [class*="question"], p, span, h3, h4, h5')) {
        const nt = clean(next.innerText || next.textContent);
        if (nt && nt.length <= 250) bits.push(nt);
        break;
      }
      const sub = next.querySelector('label, [class*="label"], [class*="title"], [class*="question"]');
      if (sub) {
        const st = clean(sub.innerText || sub.textContent);
        if (st && st.length <= 250) bits.push(st);
        break;
      }
      next = next.nextElementSibling;
    }

    if (el.parentElement) {
      const parentTxt = clean(el.parentElement.innerText || el.parentElement.textContent);
      if (parentTxt && parentTxt.length <= 250) bits.push(parentTxt);
    }
    const host = (el.getRootNode && el.getRootNode().host);
    if (host) {
      const hostTxt = clean(host.innerText || host.textContent);
      if (hostTxt && hostTxt.length <= 250) bits.push(hostTxt);
    }

    let directLabel = null;
    let fc = fieldComponent(el);
    let fchops = 0;
    while (fc && fc.getAttribute && fchops < 4) {
      for (const a of ['cx-prop-label', 'aria-label', 'data-label', 'title', 'data-qa']) {
        const v = fc.getAttribute(a);
        if (v && v.trim()) {
          const cv = clean(v).replace(/^[\s*✱]+|[\s*✱]+$/g, '').trim();
          bits.push(cv);
          if (!directLabel) directLabel = cv;
        }
      }
      fc = parentOf(fc);
      fchops++;
    }
    if (directLabel) return directLabel;

    const dc = el.getAttribute('data-content');
    if (dc) {
      const baseDc = dc.replace(/_\d+$/, '');
      const root = el.getRootNode ? el.getRootNode() : document;
      if (root && root.querySelector) {
        const ml = root.querySelector(`label[for="${CSS.escape(baseDc)}"]`) || root.querySelector(`label[data-content*="${CSS.escape(baseDc)}" i]`);
        if (ml) {
          const mlt = clean(ml.innerText || ml.textContent || ml.getAttribute('data-content'));
          if (mlt && !/^(error|validation|mb-)/i.test(ml.className || '')) bits.push(mlt);
        }
      }
    }

    let cur = el.parentElement;
    for (let h = 0; h < 6 && cur; h++) {
      if (cur.matches && cur.matches('form, body, html')) break;
      const lbls = Array.from(cur.querySelectorAll('label, legend, [class*="label" i], [class*="title" i], [class*="question" i], [class*="heading" i], [class*="important" i], [class*="caption" i], [class*="name" i], h3, h4, h5, h6, p, span, strong, b'));
      let foundInHop = false;
      for (const l of lbls) {
        if (l !== el && !el.contains(l) && !l.contains(el)) {
          if (l.compareDocumentPosition(el) & Node.DOCUMENT_POSITION_FOLLOWING) {
            const t = clean(l.innerText || l.textContent);
            if (t && t.length <= 250 && !/^(error|validation|mb-)/i.test(l.className || '') && !/^(yes|no|true|false|\*)$/i.test(t)) {
              bits.push(t);
              foundInHop = true;
              break;
            }
          }
        }
      }
      if (foundInHop) break;
      cur = cur.parentElement;
    }

    const uniq = [...new Set(bits.filter(Boolean))]
        .map(t => t.replace(/^[\s*✱]+|[\s*✱]+$/g, '').trim()).filter(Boolean);
    uniq.sort((a, b) => b.length - a.length);
    return uniq.find(x => x.length <= 350) || uniq[0] || null;
  };

  const groupLabelFor = el => {
    const ashbyEntry = el.closest && el.closest('.ashby-application-form-field-entry, [class*="field-entry" i], [class*="fieldEntry" i]');
    if (ashbyEntry) {
      const titleEl = ashbyEntry.querySelector('h1, h2, h3, h4, [class*="title" i], [class*="heading" i], [class*="label" i], p');
      if (titleEl) {
        const t = clean(titleEl.innerText || titleEl.textContent).replace(/^[\s*✱]+|[\s*✱]+$/g, '').trim();
        if (t && !/^(yes|no|true|false|agree|disagree|n\/a|na)$/i.test(t)) return t;
      }
    }
    const bits = [];
    const root = el.getRootNode ? el.getRootNode() : document;
    const name = el.getAttribute('name');
    if (name) {
      const forLbl = root.querySelector(`label[for="${CSS.escape(name)}"]`);
      if (forLbl) {
        const ft = clean(forLbl.innerText || forLbl.textContent);
        if (ft && !/^(yes|no|true|false|agree|disagree|n\/a|na)$/i.test(ft)) bits.push(ft);
      }
    }
    const rg = el.closest && el.closest('[role="radiogroup"], [role="group"]');
    if (rg) {
      if (rg.getAttribute('aria-label')) bits.push(clean(rg.getAttribute('aria-label')));
      const lb = rg.getAttribute('aria-labelledby');
      if (lb) for (const id of lb.split(/\s+/)) {
        const n = document.getElementById(id);
        if (n) bits.push(clean(n.innerText || n.textContent));
      }
    }
    const fs = el.closest && el.closest('fieldset');
    if (fs) {
      const lg = fs.querySelector(':scope > legend, legend');
      if (lg) {
        const lt = clean(lg.innerText || lg.textContent).replace(/^[\s*✱]+|[\s*✱]+$/g, '').trim();
        if (lt && !/^(yes|no|true|false|agree|disagree|n\/a|na)$/i.test(lt)) return lt;
      }
    }
    const qCard = el.closest && el.closest('.application-question, [class*="application-question" i], [class*="form-group" i], [class*="question-container" i], [class*="field-group" i], [data-qa*="question" i]');
    if (qCard) {
      const ql = qCard.querySelector('.application-label, [class*="application-label" i], [class*="question-label" i], [class*="field-label" i], [class*="question-text" i], [class*="label-text" i], [data-qa*="label" i]');
      if (ql && !ql.contains(el)) {
        const qt = clean(ql.innerText || ql.textContent).replace(/^[\s*✱]+|[\s*✱]+$/g, '').trim();
        if (qt && !/^(yes|no|true|false|agree|disagree|n\/a|na)$/i.test(qt)) return qt;
      }
    }
    let anc = el.parentElement;
    for (let i = 0; i < 7 && anc; i++, anc = anc.parentElement) {
      if (anc.matches && anc.matches('form, body, html, main, section, ul.application-questions')) break;
      if (qCard && anc === qCard.parentElement) break;
      const q = anc.querySelector && anc.querySelector(':scope > h1, :scope > h2, :scope > label, :scope > legend, :scope > .application-label, :scope > [class*="label" i], :scope > [class*="question" i], :scope > [class*="title" i], :scope > [class*="important" i], :scope > h3, :scope > h4, :scope > h5, :scope > span, :scope > p, :scope > div > span, :scope > div > p');
      if (q && q !== el && !el.contains(q) && !q.contains(el)) {
        if (q.matches('label') && q.querySelector('input, [role="radio"], [role="checkbox"]')) continue;
        if (q.matches('.checkbox, .radio, [class*="option" i], [class*="choice" i], .application-answer-alternative')) continue;
        const t = clean(q.innerText || q.textContent);
        if (t && t.length <= 300 && !/^(yes|no|true|false|agree|disagree|n\/a|na)$/i.test(t)) {
          bits.push(t);
        }
      }
    }
    if (phDirect) return phDirect;
    const uniq = [...new Set(bits.filter(Boolean))].map(t => t.replace(/^[\s*✱]+|[\s*✱]+$/g, '').trim()).filter(Boolean);
    const valid = uniq.filter(x => !/^(yes|no|true|false|agree|disagree|n\/a|na)$/i.test(x));
    valid.sort((a, b) => b.length - a.length);
    return valid.find(x => x.length <= 350) || valid[0] || (uniq[0] || null);
  };

  const raw = [];
  for (const el of all) {
    if (!isControl(el)) continue;
    const tag = el.tagName.toLowerCase();
    const type = (el.getAttribute('type') || '').toLowerCase();
    const role = (el.getAttribute('role') || '').toLowerCase();

    const idName = (el.id || '') + ' ' + (el.getAttribute('name') || '');
    if (/g-recaptcha-response|recaptcha|h-captcha-response|hcaptcha|cf-turnstile-response|turnstile/i.test(idName)) continue;

    const r = el.getBoundingClientRect();
    let options = null;
    if (tag === 'select') {
      options = Array.from(el.options).map(o => {
        const text = clean(o.textContent);
        const title = clean(o.getAttribute('title') || o.getAttribute('label') || o.title);
        let label = text || o.value;
        if (title && text && title !== text) {
          label = `${title} (${text})`;
        } else if (title && !text) {
          label = title;
        }
        return {
          label: label,
          value: o.value,
          selected: !!o.selected,
          disabled: !!o.disabled,
        };
      });
    }

    const isYesNoBtn = (tag === 'button' || role === 'button') && (el.closest('.ashby-application-form-input-yesno, [class*="yesno" i]') || el.hasAttribute('data-option'));
    const entryForBtn = isYesNoBtn ? el.closest('.ashby-application-form-field-entry, [class*="field-entry" i]') : null;
    const entryInp = entryForBtn ? entryForBtn.querySelector('input[name]') : null;
    const entryName = entryInp ? entryInp.getAttribute('name') : null;

    const groupLabel = (type === 'radio' || type === 'checkbox' || role === 'radio' || role === 'checkbox' || isYesNoBtn)
        ? groupLabelFor(el) : null;

    const isDate = isWidgetTrigger(el);
    const haspopup = (el.getAttribute('aria-haspopup') || '').toLowerCase();
    const isCustomSelect = !isDate && (tag === 'button' || role === 'button') && (
      role === 'combobox' || role === 'listbox' ||
      el.hasAttribute('aria-haspopup') || el.hasAttribute('aria-expanded') ||
      !!(el.id && (el.getRootNode ? el.getRootNode() : document).querySelector(`label[for="${CSS.escape(el.id)}"]`)) ||
      !!el.closest('[data-testid*="select" i], [data-testid*="combobox" i], [class*="select-root" i], [class*="multi-select" i], [class*="custom-select" i]')
    );
    const needsClick = !isDate && (role === 'combobox' || role === 'listbox' || haspopup === 'listbox' || haspopup === 'menu' || haspopup === 'true'
                        || isCustomSelect);

    const comp = componentAncestor(el);
    const fcomp = fieldComponent(el);
    const zcqa = (fcomp && fcomp.getAttribute) ? fcomp.getAttribute('data-zcqa') : null;

    const currentValue = (tag === 'input' && type === 'file') ? null : (el.value || null);

    const fileTrigger = isFileTrigger(el);

    raw.push({
      el: el,
      data: {
        tag: isYesNoBtn ? 'input' : tag,
        type: isYesNoBtn ? 'radio' : (isDate ? 'date' : (fileTrigger ? 'file' : (type || role || (tag === 'button' ? (isCustomSelect ? 'select' : 'button') : (tag === 'select' ? 'select' : 'text'))))),
        role: isYesNoBtn ? 'radio' : (role || null),
        id: el.id || null,
        name: el.getAttribute('name') || entryName || null,
        data_testid: el.getAttribute('data-testid') || null,
        label: isYesNoBtn ? (el.getAttribute('data-option') || clean(el.innerText || el.textContent)) : labelFor(el),
        group_label: groupLabel,
        placeholder: el.getAttribute('placeholder') || null,
        required: el.required === true || el.getAttribute('aria-required') === 'true' || Boolean(el.closest && el.closest('.Mui-required, [class*="required"], [required]')) || Boolean(el.parentElement && el.parentElement.querySelector('.MuiFormLabel-asterisk, [class*="asterisk"]')) || Boolean(entryForBtn && (entryForBtn.innerText.includes('*') || entryForBtn.querySelector('[class*="required" i], [class*="asterisk" i]'))),
        disabled: el.disabled === true || el.getAttribute('aria-disabled') === 'true',
        checked: el.checked === true || el.getAttribute('aria-checked') === 'true' || el.getAttribute('aria-pressed') === 'true',
        value: isYesNoBtn ? (el.getAttribute('data-option') || clean(el.innerText || el.textContent)) : currentValue,
        options: options,
        needs_click: needsClick,
        haspopup: el.getAttribute('aria-haspopup') || null,
        readonly: el.readOnly === true || el.getAttribute('aria-readonly') === 'true',
        is_file_trigger: fileTrigger,
        is_datepicker: isWidgetTrigger(el),
        component: comp ? comp.tagName.toLowerCase() : null,
        field_component: fcomp ? fcomp.tagName.toLowerCase() : null,
        data_zcqa: zcqa || null,
        selector_candidates: buildCandidates(el),
        visible: visible(el),
        rect: { x: Math.round(r.x), y: Math.round(r.y), width: Math.round(r.width), height: Math.round(r.height) },
        in_shadow: el.getRootNode() !== document,
      },
    });
  }

  const isNativeC = c => ['input', 'textarea', 'select'].includes(c.data.tag);
  const drop = new Set();

  raw.forEach((c, i) => {
    if (isNativeC(c) && c.data.readonly && !c.data.needs_click && !c.data.is_datepicker && c.data.type !== 'date') drop.add(i);
  });

  raw.forEach((c, i) => {
    if (!isNativeC(c) || c.data.tag !== 'input') return;
    const comp = ((c.data.component || '') + ' ' + (c.data.field_component || '')).toLowerCase();
    if (/search|filter/.test(comp)) drop.add(i);
  });

  raw.forEach((c, i) => {
    if (isNativeC(c)) return;
    if (raw.some((o, j) => j !== i && isNativeC(o) && c.el.contains(o.el))) { drop.add(i); return; }
    if (c.el.querySelector && c.el.querySelector('[role="option"], [role="menuitem"]')) drop.add(i);
  });

  raw.forEach((c, i) => {
    if (isNativeC(c)) return;
    if (raw.some((o, j) => j !== i && !isNativeC(o) && !drop.has(j) && c.el.contains(o.el))) drop.add(i);
  });

  const fcompGroups = new Map();
  raw.forEach((c, i) => {
    if (drop.has(i)) return;
    const fc = fieldComponent(c.el);
    if (!fc) return;
    if (!fcompGroups.has(fc)) fcompGroups.set(fc, []);
    fcompGroups.get(fc).push(i);
  });
  fcompGroups.forEach(idxs => {
    if (idxs.length < 2 || idxs.length > 4) return;
    const native = idxs.filter(i => isNativeC(raw[i]));
    const primaryNative = native.filter(i =>
      raw[i].data.visible && (raw[i].data.rect.width > 0 || raw[i].data.rect.height > 0)
      && !isSearchInput(raw[i].el)
      && (!raw[i].data.readonly || raw[i].data.is_datepicker || raw[i].data.type === 'date')
      && !/allow[-_ ]?other|other[-_ ]?(text|specify|val)/i.test(raw[i].data.name || '')
    );
    const combos = idxs.filter(i => !isNativeC(raw[i]) && raw[i].data.needs_click);
    if (primaryNative.length >= 1 && (combos.length >= 1 || native.length > primaryNative.length)) {
      const keep = new Set(primaryNative);
      idxs.forEach(i => { if (!keep.has(i)) drop.add(i); });
    } else if (native.length >= 1 && combos.length >= 1) {
      const keep = new Set(combos);
      idxs.forEach(i => { if (!keep.has(i)) drop.add(i); });
    }
  });

  return raw.filter((c, i) => !drop.has(i)).map(c => c.data);
}
"""


OPTIONS_HARVEST_JS = r"""
(sel) => {
  const clean = s => (s == null ? '' : String(s)).replace(/\s+/g, ' ').trim();
  const escapeAttr = v => String(v).replace(/\\/g, '\\\\').replace(/"/g, '\\"');
  let trigger = document.querySelector(sel);
  if (!trigger) {
    for (const host of document.querySelectorAll('*')) {
      if (host.shadowRoot) {
        try {
          const found = host.shadowRoot.querySelector(sel);
          if (found) { trigger = found; break; }
        } catch (_) {}
      }
    }
  }
  if (!trigger) return { options: [], selected_label: null, widget_kind: null };

  if (trigger.tagName.toLowerCase() === 'select') {
    const out = [];
    let selected_label = null;
    for (const o of trigger.options) {
      const text = clean(o.textContent);
      const title = clean(o.getAttribute('title') || o.getAttribute('label') || o.title);
      let label = text || o.value;
      if (title && text && title !== text) {
        label = `${title} (${text})`;
      } else if (title && !text) {
        label = title;
      }
      if (o.selected) selected_label = label;
      out.push({ label: label, value: o.value, selected: !!o.selected, disabled: !!o.disabled });
    }
    return { options: out, selected_label: selected_label, widget_kind: 'native_select' };
  }

  const visible = el => {
    if (!el) return false;
    const r = el.getBoundingClientRect(), cs = getComputedStyle(el);
    return !!(r.width || r.height) && cs.display !== 'none'
        && cs.visibility !== 'hidden' && cs.opacity !== '0';
  };

  const roots = [];
  for (const attr of ['aria-controls', 'aria-owns']) {
    for (const id of (trigger.getAttribute(attr) || '').split(/\s+/)) {
      const n = document.getElementById(id);
      if (n && visible(n)) roots.push(n);
    }
  }
  if (!roots.length) {
    let p = trigger.parentElement;
    for (let i = 0; i < 5 && p; i++, p = p.parentElement) {
      const near = p.querySelector('[role="listbox"]:not([hidden]), [role="menu"]:not([hidden])');
      if (near && visible(near)) { roots.push(near); break; }
    }
  }
  if (!roots.length) {
    document.querySelectorAll('[role="listbox"], [role="menu"], .ant-select-dropdown, [class*="select-dropdown" i], .select2-dropdown, .dropdown-menu').forEach(lb => { if (visible(lb)) roots.push(lb); });
  }

  const isCalendarTable = el => {
    if (!el || el.tagName !== 'TABLE') return false;
    const cls = (typeof el.className === 'string' ? el.className : '') +
                ' ' + (el.parentElement ? (el.parentElement.className || '') : '');
    return /datepicker|calendar|ui-datepicker|pika|flatpickr/i.test(cls);
  };
  const calendarRoots = [];
  if (!roots.length) {
    let p = trigger.parentElement;
    for (let i = 0; i < 6 && p; i++, p = p.parentElement) {
      const t = p.querySelector && p.querySelector('table');
      if (t && isCalendarTable(t) && visible(t)) { calendarRoots.push(t); break; }
    }
    if (!calendarRoots.length) {
      document.querySelectorAll('table').forEach(t => {
        if (isCalendarTable(t) && visible(t)) calendarRoots.push(t);
      });
    }
  }

  const out = [];
  const seen = new Set();
  let selected_label = null;
  let widget_kind = null;

  for (const root of roots) {
    const candidates = [];
    if (root.matches && (root.matches('[role="option"]') || root.matches('[role="menuitem"]'))) candidates.push(root);
    candidates.push(...root.querySelectorAll('[role="option"], [role="menuitem"], li, [class*="option" i], [class*="item" i]'));
    for (const el of candidates) {
      if (!visible(el)) continue;
      if (el.disabled || el.getAttribute('aria-disabled') === 'true') continue;
      if (el.querySelector('[role="option"], [role="menuitem"]')) continue;
      const text = clean(el.innerText || el.textContent || el.getAttribute('aria-label'));
      if (!text || text.length > 250) continue;
      if (/^(loading|searching|no options|select an option|choose an option|type to search|please select)$/i.test(text)) continue;
      const value = el.getAttribute('data-value') || el.getAttribute('value')
          || el.getAttribute('aria-label') || text;
      const key = (value + '|' + text).toLowerCase();
      if (seen.has(key)) continue;
      seen.add(key);

      let osel = '';
      if (el.id) osel = '#' + CSS.escape(el.id);
      else if (el.getAttribute('role') === 'option' || el.getAttribute('role') === 'menuitem') {
        const rName = el.getAttribute('role');
        osel = `[role="${rName}"][aria-label="` + escapeAttr(el.getAttribute('aria-label') || text) + '"]';
      } else {
        osel = el.tagName.toLowerCase() + ':has-text("' + escapeAttr(text) + '")';
      }
      const is_sel = el.getAttribute('aria-selected') === 'true'
                     || el.getAttribute('aria-checked') === 'true';
      if (is_sel && !selected_label) selected_label = text;
      out.push({
        label: text,
        value: value,
        selected: is_sel,
        disabled: false,
        selector: osel,
      });
      if (out.length >= 300) break;
    }
    if (out.length) { widget_kind = root.getAttribute('role') || 'listbox'; break; }
  }

  if (!out.length && calendarRoots.length) {
    widget_kind = 'datepicker';
    for (const tbl of calendarRoots) {
      const cells = tbl.querySelectorAll('td, [role="gridcell"]');
      for (const cell of cells) {
        if (!visible(cell)) continue;
        const text = clean(cell.innerText || cell.textContent);
        if (!text || !/^\d{1,2}$/.test(text)) continue;
        const cls = (typeof cell.className === 'string' ? cell.className : '');
        const is_sel = /(ui-datepicker-current-day|selected|active|current)/i.test(cls)
                       || cell.getAttribute('aria-selected') === 'true';
        if (is_sel && !selected_label) selected_label = text;
        const key = 'day:' + text + ':' + (cell.dataset ? (cell.dataset.date || cell.dataset.value || '') : '');
        if (seen.has(key)) continue;
        seen.add(key);
        out.push({
          label: text,
          value: text,
          selected: is_sel,
          disabled: false,
          selector: null,
        });
      }
    }
  }

  if (!out.length) {
    const v = clean(trigger.value || trigger.textContent);
    if (v) {
      out.push({ label: v, value: v, selected: true, disabled: false, selector: null });
      selected_label = v;
      widget_kind = 'display_only';
    }
  }

  return { options: out, selected_label: selected_label, widget_kind: widget_kind };
}
"""

class FormFieldExtractor:
    """Generic, site-agnostic form field extractor."""

    def __init__(self, url, use_bedrock=True, headless=False):
        self.url = url
        self.use_bedrock = use_bedrock
        self.headless = headless
        self.client = None
        self.validation_log = {
            "selector_issues": [],
            "dropdown_failures": [],
            "manual_review": [],
            "semantic_rejections": [],
            "duplicate_rejections": [],
        }
        self._setup_bedrock()

    # ------------------------------------------------------------------ utils
    def clean_text(self, value):
        if value is None:
            return None
        value = str(value)
        value = re.sub(r"\s+", " ", value).strip()
        return value or None

    def normalize_text(self, value):
        value = self.clean_text(value)
        return value.lower() if value else ""

    # --------------------------------------------------------------- bedrock
    def _setup_bedrock(self):
        if not self.use_bedrock:
            return
        load_dotenv()
        region = os.getenv("AWS_REGION", "us-east-1")
        try:
            self.client = boto3.client(
                "bedrock-runtime",
                region_name=region,
                config=Config(read_timeout=180, connect_timeout=20,
                              retries={"max_attempts": 3, "mode": "standard"}),
            )
        except Exception as exc:
            print(f"  Bedrock unavailable ({exc}); using heuristic classification.")
            self.use_bedrock = False

    def _bedrock_classify(self, fields):
        compact = []
        for i, f in enumerate(fields):
            compact.append({
                "_index": i,
                "tag": f.get("tag"),
                "type": f.get("type"),
                "role": f.get("role"),
                "label": f.get("question") or f.get("label"),
                "placeholder": f.get("placeholder"),
                "options": [o.get("label") for o in (f.get("options") or [])][:15],
            })
        payload = json.dumps(compact, ensure_ascii=False, separators=(",", ":"))
        prompt = textwrap.dedent("""
            You are a job-application form field classifier.
            Given a list of form controls (JSON), return a JSON object mapping
            each "_index" to a "field_key" string (snake_case) that describes
            the applicant data field, e.g. first_name, last_name, email, phone,
            current_company, experience, notice_period, current_salary,
            expected_salary, skills, resume, gender, city, state, country,
            graduation_college, graduation_degree, graduation_year, custom_question.
            Return only valid JSON like {"0": "first_name", "3": "email"}.
            Use only the provided controls; do not invent indexes.
        """)
        answer = ""
        for attempt in range(1, 4):
            try:
                response = self.client.converse(
                    modelId=MODEL_ID,
                    messages=[{"role": "user",
                               "content": [{"text": prompt + "\n\n" + payload}]}],
                    inferenceConfig={"temperature": 0, "maxTokens": 4000},
                )
                for block in response["output"]["message"]["content"]:
                    if "text" in block:
                        answer += block["text"]
                break
            except (ReadTimeoutError, EndpointConnectionError) as exc:
                print(f"  Bedrock attempt {attempt} failed: {exc}")
                if attempt < 3:
                    time.sleep(2 * attempt)
            except Exception as exc:
                print(f"  Bedrock error: {exc}")
                break
        if not answer:
            return {}
        answer = answer.strip()
        answer = re.sub(r"^```(?:json)?\s*", "", answer, flags=re.I)
        answer = re.sub(r"\s*```$", "", answer).strip()
        m = re.search(r"\{[\s\S]*\}", answer)
        if m:
            answer = m.group(0)
        try:
            parsed = json.loads(answer)
        except json.JSONDecodeError:
            return {}
        mapping = {}
        for k, v in parsed.items():
            try:
                mapping[int(k)] = str(v).strip()
            except (ValueError, TypeError):
                continue
        return mapping

    # ------------------------------------------------------- control typing
    def _control_type(self, tag, type_, role):
        tag = (tag or "").lower()
        type_ = (type_ or "").lower()
        role = (role or "").lower()
        if tag == "select":
            return "select"
        if type_ == "file":
            return "file"
        if type_ == "radio":
            return "radio"
        if type_ == "checkbox":
            return "checkbox"
        if type_ == "tel":
            return "phone"
        if type_ in {"email", "number", "date", "url", "password", "search"}:
            return type_
        if tag == "textarea":
            return "textarea"
        if role in {"combobox", "listbox"}:
            return "combobox"
        if role == "radio":
            return "radio"
        if role in {"checkbox", "switch"}:
            return role
        if role in {"textbox", "searchbox"}:
            return "text"
        if role in {"spinbutton", "slider"}:
            return role
        if tag == "button":
            if type_ in {"date", "datepicker"}:
                return "date"
            if role in {"combobox", "listbox"} or type_ in {"select", "combobox"}:
                return "combobox"
            if type_ == "submit":
                return "submit_button"
            return "button"
        if type_ == "submit":
            return "submit_button"
        if type_ == "text":
            return "text"
        return tag or "text"

    # ------------------------------------------------------------- grouping
    def _make_group(self, members, control_type):
        options = []
        question = None
        for m in members:
            label = m.get("label") or m.get("value") or ""
            options.append({
                "label": label,
                "value": m.get("value"),
                "checked": bool(m.get("checked")),
                "selector": m.get("selector"),
            })
            if not question and m.get("group_label"):
                question = m["group_label"]
        first = members[0]
        return {
            "tag": first.get("tag"),
            "type": "radio" if control_type == "radio_group" else "checkbox",
            "role": first.get("role"),
            "control_type": control_type,
            "id": None,
            "name": first.get("name"),
            "label": question,
            "question": question,
            "required": any(m.get("required") for m in members),
            "disabled": all(m.get("disabled") for m in members),
            "options": options,
            "selector": first.get("selector"),
            "selector_strategy": first.get("selector_strategy"),
            "visible": any(m.get("visible") for m in members),
            "rect": first.get("rect"),
            "frame_index": first.get("frame_index", 0),
            "frame_url": first.get("frame_url", ""),
            "frame_name": first.get("frame_name", ""),
            "in_shadow": first.get("in_shadow"),
        }

    def _group_controls(self, controls):
        radios = {}
        checkboxes = {}
        singles = []
        for c in controls:
            tag = (c.get("tag") or "").lower()
            type_ = (c.get("type") or "").lower()
            role = (c.get("role") or "").lower()
            name = c.get("name")
            grp_lbl = c.get("group_label") or c.get("question") or ""
            is_radio = (tag == "input" and type_ == "radio") or role == "radio" or c.get("control_type") == "radio"
            is_checkbox = (tag == "input" and type_ == "checkbox") or role == "checkbox" or c.get("control_type") == "checkbox"
            if is_radio:
                key = (c.get("frame_index", 0), name or grp_lbl or "radio_group")
                radios.setdefault(key, []).append(c)
            elif is_checkbox and (name or grp_lbl):
                key = (c.get("frame_index", 0), name or grp_lbl)
                checkboxes.setdefault(key, []).append(c)
            else:
                singles.append(c)

        result = list(singles)
        for group in radios.values():
            if len(group) >= 2:
                result.append(self._make_group(group, "radio_group"))
            else:
                result.extend(group)
        for group in checkboxes.values():
            if len(group) >= 2:
                result.append(self._make_group(group, "checkbox_group"))
            else:
                result.extend(group)
        return result

    # ------------------------------------------------------------ discovery
    def _is_ignorable_frame(self, frame):
        url = (getattr(frame, "url", "") or "")
        name = (getattr(frame, "name", "") or "")
        return bool(IGNORABLE_FRAME_RE.search(f"{url} {name}"))

    async def _resolve_selector(self, frame, candidates, expected_tag):
        fallback = None
        for cand in candidates:
            sel = cand.get("selector")
            if not sel:
                continue
            try:
                count = await frame.locator(sel).count()
            except Exception:
                continue
            if count != 1:
                continue
            try:
                tag = await frame.locator(sel).first.evaluate(
                    "el => el.tagName ? el.tagName.toLowerCase() : ''")
            except Exception:
                tag = ""
            if tag and tag == expected_tag:
                return cand
            if fallback is None:
                fallback = cand
        return fallback or (candidates[-1] if candidates else None)

    async def discover_form(self, page):
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=30000)
        except Exception:
            pass

        try:
            # Scroll anchor or form into view to trigger lazy-loaded forms and Framer Motion / IntersectionObserver animations
            await page.evaluate("""async () => {
                const hash = window.location.hash;
                let target = hash && hash.length > 1 ? document.querySelector(hash) : null;
                if (!target) target = document.querySelector('form, #apply, [id*="apply" i], [class*="apply" i]');
                if (target) {
                    target.scrollIntoView({ behavior: 'instant', block: 'center' });
                } else {
                    window.scrollTo(0, document.body.scrollHeight / 3);
                }
            }""")
            await page.wait_for_timeout(400)
        except Exception:
            pass

        for _ in range(25):
            inputs = await self._count_visible_inputs(page)
            apply_btn = await page.locator(':is(button, a, [role="button"]):has-text("Apply")').count()
            if inputs >= 3 or apply_btn > 0:
                break
            await page.wait_for_timeout(300)

        await self._ensure_application_form_open(page)

        for _ in range(20):
            inputs = await self._count_visible_inputs(page)
            if inputs >= 5:
                break
            await page.wait_for_timeout(300)

        await self._expand_repeating_sections(page)
        await page.wait_for_timeout(1000)

        try:
            with open(OUTPUT_HTML, "w", encoding="utf-8") as f:
                f.write(await page.content())
        except Exception:
            pass

        controls = []
        print("\n" + "=" * 60)
        print("LIVE DOM FIELD DISCOVERY")
        print("=" * 60)

        for frame_idx, frame in enumerate(page.frames):
            if self._is_ignorable_frame(frame):
                continue
            frame_url = getattr(frame, "url", "") or ""
            frame_name = getattr(frame, "name", "") or ""
            try:
                raw = await frame.evaluate(DISCOVERY_JS)
            except Exception as exc:
                print(f"  Frame {frame_idx} ({frame_name or 'main'}): discovery error: {exc}")
                continue

            for ctrl in raw:
                tag = (ctrl.get("tag") or "").lower()
                ctype = self._control_type(tag, ctrl.get("type"), ctrl.get("role"))
                if ctrl.get("needs_click") and ctype not in {"select", "combobox"} \
                        and tag != "a":
                    ctype = "combobox"
                ctrl["control_type"] = ctype
                ctrl["frame_index"] = frame_idx
                ctrl["frame_url"] = frame_url
                ctrl["frame_name"] = frame_name
                ctrl["detected_by"] = ["live_dom"]

                resolved = await self._resolve_selector(
                    frame, ctrl.get("selector_candidates") or [], tag)
                ctrl.pop("selector_candidates", None)
                if resolved:
                    ctrl["selector"] = resolved["selector"]
                    ctrl["selector_strategy"] = resolved["strategy"]
                    ctrl["selector_unique"] = True
                else:
                    ctrl["selector"] = None
                    ctrl["selector_unique"] = False

                label = ctrl.get("label")
                group_label = ctrl.get("group_label")
                ctype_lower = str(ctrl.get("control_type") or ctrl.get("type") or "").lower()
                if ctype_lower in {"checkbox", "radio"}:
                    if group_label and label and group_label != label:
                        ctrl["question"] = f"{group_label} - {label}"
                    else:
                        ctrl["question"] = label or group_label
                else:
                    ctrl["question"] = group_label or label
                if not ctrl.get("label"):
                    ctrl["label"] = ctrl["question"]
                controls.append(ctrl)

            print(f"  Frame {frame_idx} ({frame_name or 'main'}): "
                  f"{len(raw)} controls found")

        seen = set()
        unique = []
        for c in controls:
            key = (c.get("frame_index"), c.get("selector") or c.get("id") or str(c.get("rect")))
            if key in seen:
                self.validation_log["duplicate_rejections"].append({
                    "selector": c.get("selector"), "reason": "duplicate live control"})
                continue
            seen.add(key)
            unique.append(c)

        fields = self._group_controls(unique)
        for f in fields:
            f["confidence"] = self._compute_confidence(f)

        # NEW: semantic collapse BEFORE harvesting, so we never open the same
        # logical field twice (Keka repeats Date of Joining across rows).
        before_sem = len(fields)
        fields = self._collapse_semantic_duplicates(fields)
        if len(fields) < before_sem:
            print(f"  Semantic duplicates collapsed: {before_sem} -> {len(fields)} fields")

        print(f"\n--- OPTION HARVESTING (click-to-open dropdowns) ---")
        fields = await self._harvest_options(page, fields)

        before_collapse = len(fields)
        fields = self._collapse_repeating_rows(fields)
        if len(fields) < before_collapse:
            print(f"  Repeating rows collapsed: {before_collapse} -> {len(fields)} fields")

        print(f"\n  Total unique fields: {len(fields)}")
        return fields

    def _collapse_semantic_duplicates(self, fields):
        """Safely collapse true duplicate controls (same selector or identical DOM position).
        CRITICAL: Never drop fields that are required, have distinct selectors (like :nth-of-type(1) vs :nth-of-type(2)),
        or belong to separate repeatable rows (like multiple Education or Experience blocks).
        """
        seen_selectors = {}
        unique = []
        dropped_count = 0
        for f in fields:
            sel = (f.get("selector") or "").strip()
            frame = f.get("frame_index", 0)
            is_req = bool(f.get("required") or f.get("aria-required") or "*" in str(f.get("label") or ""))

            # If element has a valid unique selector, check if it truly collides
            if sel:
                key = (frame, sel)
                if key in seen_selectors:
                    # True selector duplicate (same DOM target resolved twice)
                    dropped_count += 1
                    continue
                seen_selectors[key] = f

            unique.append(f)

        if dropped_count > 0:
            self.validation_log["manual_review"].append({
                "reason": "collapsed exact selector duplicates",
                "dropped": dropped_count,
            })
        return unique

    def _collapse_repeating_rows(self, fields):
        """Preserve all valid repeatable rows needed for candidate resume matching.
        Never drop required fields or valid form rows.
        """
        return fields

    # ---------------------------------------------------- option harvesting
    async def _harvest_options(self, page, fields):
        """Open each select/combobox/datepicker and read the options that
        load after a click. Never opens a file picker.

        Dedup is done on the LOGICAL field identity (question + selector),
        not on the live element rect — this fixes the Keka bug where the
        repeating Experience rows each had a distinct rect and were opened
        independently.
        """
        harvested_logical = set()   # logical keys already harvested
        harvested_live = set()      # live element keys (id/name/rect) already opened
        static_count = 0
        clicked_count = 0

        for field in fields:
            ctype = (field.get("control_type") or "").lower()
            tag = (field.get("tag") or "").lower()
            ftype = (field.get("type") or "").lower()
            selector = field.get("selector")
            if not selector:
                continue

            if ctype == "file" or (tag == "input" and ftype == "file") \
                    or field.get("is_file_trigger"):
                field["options_discovery"] = {
                    "status": "skipped_file_upload",
                    "method": "n/a", "count": 0,
                    "reason": "file inputs must not be clicked",
                }
                continue

            if ctype == "date" or field.get("is_datepicker"):
                field["options_discovery"] = {
                    "status": "skipped_datepicker",
                    "method": "n/a", "count": 0,
                    "reason": "datepickers are calendar popups, not select dropdowns",
                }
                continue

            if tag == "select":
                if field.get("options"):
                    field["options_discovery"] = {
                        "status": "captured", "method": "static",
                        "count": len(field["options"]),
                    }
                    static_count += 1
                    continue
            elif ctype not in {"combobox", "custom_dropdown"}:
                continue

            # NEW: logical dedup key — same question + same selector = same field.
            logical_label = self.normalize_text(
                field.get("question") or field.get("label") or "")
            logical_key = (logical_label, selector)
            if logical_label and logical_key in harvested_logical:
                field["options_discovery"] = {
                    "status": "skipped_already_harvested",
                    "method": "n/a", "count": 0,
                    "reason": "same logical field already harvested",
                }
                continue
            if logical_label:
                harvested_logical.add(logical_key)

            fidx = field.get("frame_index", 0)
            frames = list(page.frames)
            if fidx < 0 or fidx >= len(frames):
                continue
            frame = frames[fidx]

            loc = frame.locator(selector).first
            try:
                if await loc.count() == 0:
                    continue
            except Exception:
                continue

            try:
                is_file_live = await loc.evaluate(
                    "el => { const t = (el.getAttribute && el.getAttribute('type')) || '';"
                    " return (el.tagName||'').toLowerCase() === 'input' && t.toLowerCase() === 'file'; }"
                )
            except Exception:
                is_file_live = False
            if is_file_live:
                field["options_discovery"] = {
                    "status": "skipped_file_upload",
                    "method": "n/a", "count": 0,
                    "reason": "live element is <input type=file>",
                }
                continue

            try:
                live_key = await loc.evaluate(
                    """el => {
                        if (el.id) return 'id:' + el.id;
                        const n = el.getAttribute && el.getAttribute('name');
                        if (n) return 'name:' + n;
                        const r = el.getBoundingClientRect();
                        return 'rect:' + Math.round(r.x) + ',' + Math.round(r.y) + ','
                              + Math.round(r.width) + ',' + Math.round(r.height);
                    }"""
                )
            except Exception:
                live_key = None
            if live_key and live_key in harvested_live:
                field["options_discovery"] = {
                    "status": "skipped_already_harvested",
                    "method": "n/a", "count": 0,
                    "reason": "same live element was already opened and harvested",
                }
                continue
            if live_key:
                harvested_live.add(live_key)

            file_chooser_opened = {"flag": False}

            def _swallow(chooser):
                file_chooser_opened["flag"] = True
                try:
                    chooser.cancel()
                except Exception:
                    pass

            page.on("filechooser", _swallow)
            try:
                already_open = False
                try:
                    already_open = await loc.evaluate(
                        """el => {
                            if ((el.getAttribute('aria-expanded') || '').toLowerCase() === 'true') return true;
                            const ctl = el.getAttribute('aria-controls') || el.getAttribute('aria-owns');
                            if (ctl) {
                              const p = document.getElementById(ctl.split(/\\s+/)[0]);
                              if (p) {
                                const r = p.getBoundingClientRect();
                                const cs = getComputedStyle(p);
                                if (r.width && r.height && cs.display !== 'none'
                                    && cs.visibility !== 'hidden') return true;
                              }
                            }
                            return false;
                        }"""
                    )
                except Exception:
                    already_open = False

                if not already_open:
                    for action in ("click", "force_click", "focus_enter", "focus_arrow"):
                        try:
                            if action == "click":
                                await loc.scroll_into_view_if_needed(timeout=1500)
                                await loc.click(timeout=1800)
                            elif action == "force_click":
                                await loc.click(timeout=1800, force=True)
                            elif action == "focus_enter":
                                await loc.focus(timeout=800)
                                await loc.press("Enter", timeout=800)
                            else:
                                await loc.focus(timeout=800)
                                await loc.press("ArrowDown", timeout=800)
                            await page.wait_for_timeout(700)
                            break
                        except Exception:
                            continue
                else:
                    await page.wait_for_timeout(150)
            finally:
                try:
                    page.remove_listener("filechooser", _swallow)
                except Exception:
                    pass

            if file_chooser_opened["flag"]:
                field["options_discovery"] = {
                    "status": "skipped_file_upload",
                    "method": "n/a", "count": 0,
                    "reason": "click triggered a native file chooser (aborted)",
                }
                try:
                    await page.keyboard.press("Escape")
                except Exception:
                    pass
                continue

            result = {"options": [], "selected_label": None, "widget_kind": None}
            try:
                result = await frame.evaluate(OPTIONS_HARVEST_JS, selector) or result
            except Exception as exc:
                self.validation_log["dropdown_failures"].append({
                    "selector": selector,
                    "field": field.get("question") or field.get("label"),
                    "reason": f"option read error: {str(exc)[:160]}",
                })

            options = result.get("options") or []
            selected_label = result.get("selected_label")
            widget_kind = result.get("widget_kind")
            field["widget_kind"] = widget_kind
            if selected_label:
                field["current_selection_label"] = selected_label

            closed = False
            sel_opt = next((o for o in options if o.get("selected") and o.get("selector")), None)
            if sel_opt:
                try:
                    await frame.locator(sel_opt["selector"]).first.click(timeout=1500)
                    await page.wait_for_timeout(150)
                    closed = True
                except Exception:
                    pass

            if not closed:
                try:
                    await page.keyboard.press("Escape")
                    await page.wait_for_timeout(120)
                except Exception:
                    pass

                try:
                    still_open = await frame.evaluate(
                        """() => {
                            const open = Array.from(document.querySelectorAll('[role="listbox"]'))
                                .some(el => {
                                    const r = el.getBoundingClientRect();
                                    const cs = getComputedStyle(el);
                                    return r.width && r.height && cs.display !== 'none'
                                        && cs.visibility !== 'hidden';
                                });
                            const cal = Array.from(document.querySelectorAll('table'))
                                .some(t => {
                                    const cls = (typeof t.className === 'string' ? t.className : '')
                                        + ' ' + (t.parentElement ? (t.parentElement.className || '') : '');
                                    if (!/datepicker|calendar|ui-datepicker|pika|flatpickr/i.test(cls)) return false;
                                    const r = t.getBoundingClientRect();
                                    const cs = getComputedStyle(t);
                                    return r.width && r.height && cs.display !== 'none'
                                        && cs.visibility !== 'hidden';
                                });
                            return open || cal;
                        }"""
                    )
                except Exception:
                    still_open = False
                if still_open:
                    try:
                        await loc.click(timeout=1200)
                        await page.wait_for_timeout(120)
                    except Exception:
                        try:
                            await loc.press("Escape", timeout=600)
                        except Exception:
                            pass

            clicked_count += 1
            if options:
                field["options"] = options
                field["options_discovery"] = {
                    "status": "captured",
                    "method": "click_to_open_live_dom",
                    "widget_kind": widget_kind,
                    "count": len(options),
                    "possibly_capped": len(options) >= 300,
                }
                print(f"  [options] {field.get('question') or field.get('label') or selector}: "
                      f"{len(options)} options captured"
                      + (f" (kind={widget_kind})" if widget_kind else ""))
            elif field.get("options"):
                field["options_discovery"] = {
                    "status": "captured_static_only",
                    "method": "click_to_open_live_dom",
                    "widget_kind": widget_kind,
                    "count": len(field["options"]),
                }
            else:
                field["options_discovery"] = {
                    "status": "opened_no_options",
                    "method": "click_to_open_live_dom",
                    "widget_kind": widget_kind,
                    "count": 0,
                }
                self.validation_log["dropdown_failures"].append({
                    "selector": selector,
                    "field": field.get("question") or field.get("label"),
                    "reason": "dropdown opened but no options found",
                })

        if clicked_count == 0 and static_count > 0:
            print(f"  All {static_count} dropdown(s) already captured directly from DOM <select> tags (click-to-open not needed).")
        elif clicked_count > 0:
            print(f"  Option harvesting completed: {clicked_count} dynamic dropdown(s) opened, {static_count} static native select(s).")
        return fields

    # ------------------------------------------------ apply / expand (generic)
    async def _count_visible_inputs(self, page):
        total = 0
        for frame in page.frames:
            if self._is_ignorable_frame(frame):
                continue
            try:
                total += await frame.locator(
                    "input:not([aria-hidden='true']):visible, textarea:not([aria-hidden='true']):visible, select:not([aria-hidden='true']):visible, "
                    "[contenteditable='true']:visible, [role='textbox']:visible, [role='radio']:visible, [role='radiogroup']:visible, [role='combobox']:visible, .choice:visible, "
                    "input[type='file'], [class*='upload' i]:visible, [class*='drop' i]:visible").count()
            except Exception:
                pass
        return total

    async def _is_file_trigger_locator(self, loc):
        try:
            return await loc.evaluate(
                """el => {
                    const tag = (el.tagName || '').toLowerCase();
                    const type = ((el.getAttribute && el.getAttribute('type')) || '').toLowerCase();
                    if (tag === 'input' && type === 'file') return true;
                    if (tag === 'label') {
                        const forId = el.getAttribute('for');
                        if (forId) {
                            const t = document.getElementById(forId);
                            if (t && (t.tagName||'').toLowerCase() === 'input'
                                && ((t.getAttribute('type')||'').toLowerCase() === 'file')) return true;
                        }
                        if (el.querySelector && el.querySelector('input[type="file"]')) return true;
                    }
                    const role = (el.getAttribute('role') || '').toLowerCase();
                    if ((tag === 'button' || role === 'button' || tag === 'a')
                        && el.querySelector && el.querySelector('input[type="file"]')) return true;
                    return false;
                }"""
            )
        except Exception:
            return False

    async def _ensure_application_form_open(self, page):
        try:
            # Dismiss non-application popups, newsletters, cookie overlays
            try:
                await page.evaluate("""() => {
                    try { localStorage.setItem("cynbit_newsletter_seen", "true"); } catch(e) {}
                    const candidates = [...document.querySelectorAll('dialog[open], [role="dialog"], div, section, aside')].filter(el => {
                        if (!el || el === document.body || el === document.documentElement) return false;
                        const style = window.getComputedStyle(el);
                        if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
                        const pos = style.position;
                        const z = parseInt(style.zIndex, 10) || 0;
                        return el.tagName === 'DIALOG' || el.getAttribute('role') === 'dialog' || ((pos === 'fixed' || (pos === 'absolute' && z >= 50)) && el.offsetWidth > 100 && el.offsetHeight > 100);
                    });
                    for (const d of candidates) {
                        // GENERIC SAFEGUARD 1: Never close active job application modal or form container!
                        const hasAppForm = !!d.querySelector('form, [class*="step" i], [class*="wizard" i], [class*="progress" i]');
                        const hasAppControls = d.querySelectorAll('input:not([type="hidden"]), select, textarea').length > 0;
                        const hasAppButtons = [...d.querySelectorAll('button, [role="button"], a')].some(b => {
                            const t = (b.innerText || '').trim().toLowerCase();
                            return /\\b(next|continue|submit|confirm|previous|back|save|apply)\\b/i.test(t);
                        });
                        const txt = (d.innerText || '').toLowerCase();
                        const isAppModal = /apply|application|candidate|resume|applicant|confirm/i.test(txt);
                        if ((hasAppForm || hasAppButtons || isAppModal) && hasAppControls) {
                            continue; // Preserve active application forms
                        }

                        // GENERIC SAFEGUARD 2: Never close login / registration modal
                        if ((txt.includes('sign in') || txt.includes('log in') || txt.includes('create account') || txt.includes('register')) && (d.querySelector('input[type="password"]') || txt.includes('google') || txt.includes('password'))) continue;

                        if (txt.includes('newsletter') || txt.includes('zero noise') || txt.includes('architect') || txt.includes('subscribe') || txt.includes('digest') || txt.includes('job alert') || txt.includes('talent community') || txt.includes('cookie') || txt.includes('privacy notice')) {
                            const btns = [...d.querySelectorAll('button, [role="button"], a, span, svg')];
                            let closed = false;
                            for (const b of btns) {
                                const bt = (b.innerText || '').trim().toLowerCase();
                                const aria = (b.getAttribute('aria-label') || '').toLowerCase();
                                const cls = (b.className || '').toString().toLowerCase();
                                if (bt === '✕' || bt === '×' || bt === 'x' || bt === 'close' || bt === 'no thanks' || bt === 'not now' || bt === 'skip' || bt === 'accept' || bt === 'accept all' || aria.includes('close') || aria.includes('dismiss') || (b.tagName === 'SPAN' && bt === 'close' && cls.includes('material-symbols'))) {
                                    try { b.click(); closed = true; break; } catch(e) {}
                                }
                            }
                            if (!closed) { d.style.display = 'none'; }
                        }
                    }
                }""")
            except Exception:
                pass
            for sel in ('button:has-text("Accept")', 'button:has-text("Accept All")',
                        'button:has-text("I Agree")', 'button:has-text("Allow")',
                        'button:has-text("OK")', '[class*="cookie"] button',
                        '[class*="consent"] button'):
                try:
                    loc = page.locator(sel).first
                    if await loc.count() > 0:
                        await loc.click(timeout=2000, force=True)
                        await page.wait_for_timeout(500)
                        break
                except Exception:
                    pass

            # GENERIC CHECK: Is an application form, modal, or wizard ALREADY active and open?
            is_already_open = False
            try:
                is_already_open = await page.evaluate("""() => {
                    // 1. Any active dialog/modal with form fields or workflow navigation
                    const dialogs = [...document.querySelectorAll('dialog[open], [role="dialog"], [class*="modal" i]:not([class*="mask"]):not([class*="root"])')].filter(el => {
                        const style = window.getComputedStyle(el);
                        if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
                        return el.offsetWidth > 60 && el.offsetHeight > 60;
                    });
                    for (const d of dialogs) {
                        const hasInputs = d.querySelectorAll('input:not([type="hidden"]), select, textarea').length > 0;
                        const hasNav = [...d.querySelectorAll('button, [role="button"], a')].some(b => {
                            const t = (b.innerText || b.textContent || '').trim().toLowerCase();
                            return /\\b(next|continue|submit|confirm|previous|back|save)\\b/i.test(t);
                        });
                        const hasSteps = d.querySelectorAll('[class*="step" i], [class*="progress" i]').length > 0;
                        if (hasInputs || hasNav || hasSteps) return true;
                    }

                    // 2. Any active form on the page with visible inputs
                    const forms = [...document.querySelectorAll('form')].filter(f => {
                        const style = window.getComputedStyle(f);
                        if (style.display === 'none' || style.visibility === 'hidden') return false;
                        return f.querySelectorAll('input:not([type="hidden"]), select, textarea').length > 0;
                    });
                    if (forms.length > 0) return true;

                    // 3. Multi-step navigation buttons present and visible
                    const isVisBtn = el => {
                        if (!el) return false;
                        const r = el.getBoundingClientRect();
                        if (r.width <= 0 || r.height <= 0) return false;
                        const s = window.getComputedStyle(el);
                        return s.display !== 'none' && s.visibility !== 'hidden' && s.opacity !== '0';
                    };
                    const navBtns = [...document.querySelectorAll('button, [role="button"], a')].some(b => {
                        if (!isVisBtn(b)) return false;
                        const t = (b.innerText || b.textContent || '').trim().toLowerCase();
                        return /\\b(next|continue|save & continue|submit application|confirm)\\b/i.test(t);
                    });
                    return navBtns;
                }""")
            except Exception:
                pass

            if is_already_open:
                return True

            before = await self._count_visible_inputs(page)
            if before < 1:
                for _ in range(3):
                    await page.wait_for_timeout(500)
                    before = await self._count_visible_inputs(page)
                    if before >= 1:
                        break
            if before >= 2:
                return True
            # Priority 1: Check targeted Apply buttons/links across frames
            contexts = [page] + [f for f in page.frames if not self._is_ignorable_frame(f)]
            for ctx in contexts:
                try:
                    priority_loc = ctx.locator(":is(button, a, [role='button'], input[type='button'], input[type='submit']):is(:has-text('Apply'), :has-text('Start'), :has-text('Get Started'), :has-text('Begin'))")
                    p_count = await priority_loc.count()
                    for i in range(p_count):
                        el = priority_loc.nth(i)
                        try:
                            if not await el.is_visible():
                                continue
                            if await self._is_file_trigger_locator(el):
                                continue
                            text = self.clean_text(await el.inner_text()) or ""
                            if APPLY_BAD_RE.search(text):
                                continue
                            try:
                                attr_check = await el.evaluate("el => (el.id || '') + ' ' + (el.className || '') + ' ' + (el.getAttribute('data-action') || '') + ' ' + (el.getAttribute('aria-label') || '')")
                                if any(k in attr_check.lower() for k in ["linkedin", "indeed", "oauth", "social", "sign-in", "login"]):
                                    continue
                            except Exception:
                                pass
                            href = await el.get_attribute("href") or ""
                            print(f"  Clicking targeted Apply CTA: '{text[:40]}' (href: {href[:60]})")
                            url_before = page.url
                            pages_before = len(page.context.pages) if page.context else 1
                            try:
                                await el.scroll_into_view_if_needed(timeout=2000)
                                await el.evaluate("el => { if (typeof window.apply === 'function') window.apply(); else el.click(); }")
                            except Exception:
                                await el.evaluate("el => el.click()")

                            # Wait and check for new tab, URL change, or DOM change
                            for _ in range(10):
                                await page.wait_for_timeout(500)
                                if page.context and len(page.context.pages) > pages_before:
                                    print(f"  New tab opened by Apply CTA: {page.context.pages[-1].url[:70]}")
                                    return True
                                if page.url != url_before:
                                    print(f"  Page navigated to: {page.url[:70]}")
                                    return True
                                after = await self._count_visible_inputs(page)
                                if after > before:
                                    print(f"  Form opened ({before} -> {after} visible inputs)")
                                    return True
                                if any("gohire" in f.url or "ashby" in f.url or "greenhouse" in f.url or "lever" in f.url or "widget" in f.url for f in page.frames):
                                    print(f"  Embedded application iframe opened!")
                                    return True

                            # Fallback: if href is a real external career/job link and no navigation occurred, navigate to it
                            if href and href.startswith("http") and href != url_before:
                                print(f"  Navigating directly to Apply CTA href: {href[:70]}")
                                await page.goto(href, wait_until="domcontentloaded")
                                return True
                        except Exception:
                            continue
                except Exception:
                    pass

            # Priority 2: Broad scan of interactive elements
            for ctx in contexts:
                try:
                    loc = ctx.locator(":is(button, a, [role='button'], input[type='button'], input[type='submit'])")
                    count = await loc.count()
                except Exception:
                    continue
                for i in range(min(count, 120)):
                    el = loc.nth(i)
                    try:
                        if not await el.is_visible():
                            continue
                        if await self._is_file_trigger_locator(el):
                            continue
                        text = self.clean_text(await el.inner_text()) or ""
                        attrs = await el.evaluate(
                            "el => (el.id||'')+' '+(el.className||'')+' '+(el.getAttribute('data-target')||'')+' '+(el.getAttribute('href')||'')")
                    except Exception:
                        continue
                    if not APPLY_TEXT_RE.search(text + " " + attrs):
                        continue
                    if APPLY_BAD_RE.search(text):
                        continue
                    print(f"  Clicking application CTA: '{text[:40] or el}'")
                    try:
                        await el.scroll_into_view_if_needed(timeout=2000)
                        await el.click(timeout=3000)
                    except Exception:
                        try:
                            await el.evaluate("el => el.click()")
                        except Exception:
                            continue
                    for _ in range(10):
                        await page.wait_for_timeout(400)
                        try:
                            if page.context and len(page.context.pages) > 1:
                                newest = page.context.pages[-1]
                                if not newest.is_closed() and newest != page:
                                    print(f"  New tab opened by Apply CTA: {newest.url[:70]}")
                                    return True
                        except Exception:
                            pass
                        after = await self._count_visible_inputs(page)
                        if after > before:
                            print(f"  Form opened ({before} -> {after} visible inputs)")
                            return True
                    return True
        except Exception as exc:
            print(f"  Notice (apply CTA): {exc}")
        return before >= 1

    async def _expand_repeating_sections(self, page):
        """Click generic '+ Add ...' buttons/links (bounded).

        NEW: we deduplicate by NORMALIZED BUTTON TEXT, so the same logical
        '+ Add Experience Details' button is clicked at most once, even if
        the page re-renders a fresh copy of it after each click (Keka does
        this inside the newly-added row).
        """
        expanded = 0
        clicked_labels = set()          # normalized text already clicked
        clicked_live_keys = set()       # stable live keys of clicked buttons
        MAX_EXPANSIONS = 8

        contexts = [page] + [f for f in page.frames if not self._is_ignorable_frame(f)]
        for ctx in contexts:
            try:
                loc = ctx.locator(":is(button, a, [role='button'], [class*='pointer'], [class*='cursor'], [class*='btn'], span, div):has-text('Add')")
                count = await loc.count()
            except Exception:
                continue
            for i in range(min(count, 60)):
                if expanded >= MAX_EXPANSIONS:
                    return expanded
                el = loc.nth(i)
                try:
                    if not await el.is_visible():
                        continue
                    if await self._is_file_trigger_locator(el):
                        continue
                    text = self.clean_text(await el.inner_text()) or ""
                except Exception:
                    continue
                if not text or not re.match(r"^\s*\+?\s*add\b", text, re.I):
                    continue
                if len(text) > 60:
                    continue
                if any(x in text.lower() for x in ["file", "attachment", "document", "resume", "photo", "cv"]):
                    continue
                if re.search(r"\b(another|more)\b", text, re.I):
                    continue
                try:
                    nearby_inputs = await el.evaluate("""el => {
                        const wrap = el.closest('section, fieldset, .form-group, div[class*="section"], div[class*="space-y"], form') || el.parentElement;
                        if (!wrap) return 0;
                        return wrap.querySelectorAll('input:not([type="hidden"]), select, textarea').length;
                    }""")
                    if nearby_inputs >= 3:
                        continue
                except Exception:
                    pass

                try:
                    has_child = await el.evaluate("""el => {
                        return Array.from(el.querySelectorAll(':is(button, a, [role="button"], [class*="pointer"], span, div)')).some(c => {
                            const t = (c.innerText || '').trim();
                            return /^\\s*\\+?\\s*add\\b/i.test(t) && t.length < 60;
                        });
                    }""")
                    if has_child:
                        continue
                except Exception:
                    pass

                norm = self.normalize_text(text).strip(" +*→←")
                if norm in clicked_labels:
                    # Same logical "add" already handled — skip silently.
                    continue

                try:
                    live_key = await el.evaluate(
                        """el => {
                            if (el.id) return 'id:' + el.id;
                            const n = el.getAttribute && el.getAttribute('name');
                            if (n) return 'name:' + n;
                            const cls = (typeof el.className === 'string' ? el.className : '');
                            const txt = (el.innerText || el.textContent || '').trim();
                            return 'txt:' + txt + '|cls:' + cls;
                        }"""
                    )
                except Exception:
                    live_key = None
                if live_key and live_key in clicked_live_keys:
                    continue

                print(f"  Expanding section: '{text[:50]}'")
                try:
                    await el.scroll_into_view_if_needed(timeout=1500)
                    await el.click(timeout=1500)
                except Exception:
                    try:
                        await el.evaluate("el => el.click()")
                    except Exception:
                        continue
                clicked_labels.add(norm)
                if live_key:
                    clicked_live_keys.add(live_key)
                expanded += 1
                await page.wait_for_timeout(600)
        if expanded:
            await page.wait_for_timeout(800)
        return expanded

    # --------------------------------------------------------- classification
    def _compute_confidence(self, field):
        score = 0
        if field.get("selector_unique"):
            score += 40
        label = field.get("label") or field.get("question")
        if label and len(str(label)) > 1:
            score += 30
        if field.get("role"):
            score += 15
        if field.get("required"):
            score += 10
        if field.get("visible", True):
            score += 5
        return min(score, 100)

    def _is_navigation_label(self, label):
        if not label:
            return False
        norm = self.normalize_text(label).strip(" *.:|→←")
        return norm in NAVIGATION_LABELS or bool(SECTION_ACTION_RE.match(label))

    def heuristic_classify(self, field):
        ctype = (field.get("control_type") or "").lower()
        tag = (field.get("tag") or "").lower()
        is_date = ctype == "date" or field.get("is_datepicker") or field.get("type") == "date"

        # Early return for standard HTML5 email/tel/file inputs
        if tag == "input" and (ctype == "email" or field.get("type") == "email" or "email" in str(field.get("autocomplete") or "").lower()):
            return "email"
        if tag == "input" and (ctype == "tel" or field.get("type") == "tel"):
            return "phone"

        def _split_camel(s: str) -> str:
            if not s:
                return ""
            return re.sub(r"([a-z])([A-Z])", r"\1 \2", str(s))

        label = self.normalize_text(field.get("label") or field.get("question") or "")
        placeholder = self.normalize_text(field.get("placeholder") or "")
        name = self.normalize_text(field.get("name") or "")
        component = self.normalize_text(field.get("component"))
        fcomponent = self.normalize_text(field.get("field_component"))
        zcqa = self.normalize_text(field.get("data_zcqa") or field.get("data_qa"))
        zcqa = re.sub(r"^(rec|manual|cx|prop|lt|apply|easy|field)[_\-]?", "", zcqa).replace("_", " ")
        data_testid = self.normalize_text(field.get("data_testid") or "")
        fid = self.normalize_text(field.get("id") or "")
        selector = self.normalize_text(field.get("selector") or "")

        # Clean selector to remove CSS attribute selector keys ([name=, [type=, [id=, etc.)
        # so literal attribute syntax like [name="email"] does not pollute hay with "name"
        clean_selector = re.sub(r"\[[a-zA-Z0-9_\-]+=", " ", selector)

        split_name = _split_camel(field.get("name") or "")
        split_id = _split_camel(field.get("id") or "")
        split_sel = _split_camel(clean_selector)

        hay = f"{label} {placeholder} {name} {split_name} {component} {fcomponent} {zcqa} {data_testid} {fid} {split_id} {clean_selector} {split_sel}".strip()

        # Check for explicit lone "name"
        norm_label = re.sub(r"[\s*.:|]+", " ", label).strip().lower()
        norm_name = re.sub(r"[\s*.:|]+", " ", name).strip().lower()
        if norm_label in ("name", "full name", "your name", "candidate name", "applicant name") or norm_name in ("name", "fullname", "candidatename"):
            if not any(x in hay.lower() for x in ("school", "college", "university", "institute", "company", "employer", "organization", "firm", "linkedin", "github", "portfolio", "website", "url", "user", "file", "sur", "first", "last", "middle")):
                return "full_name"

        if is_date:
            hay_lower = hay.lower()
            is_edu = any(x in hay_lower for x in ("school", "college", "university", "institute", "degree", "education", "course", "qualification", "specialization", "completion", "study", "academic"))
            is_start = any(re.search(r"(?i)\b" + re.escape(w) + r"\b", hay) for w in ["date of joining", "joining date", "from date", "start date", "start_date", "start", "from"])
            is_end = any(re.search(r"(?i)\b" + re.escape(w) + r"\b", hay) for w in ["date of relieving", "relieving date", "end date", "end_date", "enddate", "date of completion", "completion date", "end of course", "to date", "end", "to"])

            if any(re.search(r"(?i)\b" + re.escape(w) + r"\b", hay) for w in ["date of birth", "dob", "birth date", "birthdate"]):
                return "date_of_birth"
            if any(re.search(r"(?i)\b" + re.escape(w) + r"\b", hay) for w in ["available from", "availablefrom", "earliest joining", "available date", "availability"]):
                return "earliest_joining_date"
            if is_start:
                if is_edu:
                    return "education_start_date"
                return "work_start_date"
            if is_end:
                if is_edu:
                    return "education_end_date"
                return "work_end_date"
            if is_edu:
                return "education_end_date"
            return "work_end_date"

        if ctype == "file":
            if "cover" in hay:
                return "cover_letter"
            if any(w in hay for w in ("photo", "image", "picture", "passport", "avatar", "profile pic")):
                return "photo"
            return "resume"

        # Check phone country before nationality / options
        id_sel_name_check = f"{field.get('id') or ''} {field.get('name') or ''} {field.get('selector') or ''}".lower()
        full_phone_hay = f"{id_sel_name_check} {hay}".lower()
        clean_full_phone = re.sub(r"[\s_\-\.\\]+", "", full_phone_hay)
        if any(k in clean_full_phone for k in ("countrycode", "dialcode", "phonecode", "callingcode", "countrydial", "phonecountry", "mobilecountry")):
            return "phone_country_code"
        if re.search(r"phone|mobile|tel", full_phone_hay) and re.search(r"country|code|dial", full_phone_hay):
            return "phone_country_code"
        options = field.get("options") or []
        curr_val = str(field.get("value") or "") + " " + str(field.get("current_selection_label") or "")
        has_dial_in_val = bool(re.search(r"\+\d{1,4}\s+[A-Z]{2}", curr_val))
        has_dial_in_opts = bool(options and sum(1 for o in options[:10] if re.search(r"\+\d{1,4}", str(o.get("label") or "") + " " + str(o.get("value") or ""))) >= 2)
        if has_dial_in_val or has_dial_in_opts:
            return "phone_country_code"
        if options and any(re.search(r"\+\d{1,4}", str(o.get("label") or "") + " " + str(o.get("value") or "")) for o in options[:10]):
            if any(k in clean_full_phone for k in ("country", "code", "dial", "calling", "phone", "mobile", "search")):
                return "phone_country_code"

        # Check options signatures for select/combobox/options
        options = field.get("options") or []
        if options and (ctype in {"select", "combobox"} or len(options) > 0):
            opt_labels = [self.normalize_text(o.get("label") or "") for o in options]
            opt_values = [self.normalize_text(o.get("value") or "") for o in options]
            all_opts = set(opt_labels + opt_values)

            # Currency signature (word boundary required so 'neurodiverse' is not matched as 'euro')
            currency_codes = {"usd", "eur", "gbp", "inr", "cad", "aud", "jpy", "chf", "cny", "sgd", "hkd", "thb"}
            if len(all_opts & currency_codes) >= 2 or any(re.search(r"\b(dollars?|euros?|inr|usd|gbp|rupees?)\b", o, re.I) for o in opt_labels[:5]):
                return "currency"

            # Gender signature
            if {"male", "female"}.issubset(all_opts) or ("gender" in hay.lower() and ("male" in all_opts or "female" in all_opts)):
                return "gender"

            # Disability signature
            if any(w in hay.lower() for w in ("disability", "differently abled", "handicap", "impairment", "neurodiverse")):
                return "disability_status"

            # Salary frequency signature
            freq_codes = {"month", "monthly", "year", "annually", "annual", "hour", "hourly", "week", "weekly", "day", "daily"}
            if len(all_opts & freq_codes) >= 2:
                return "salary_frequency"

            # Salutation signature
            if all_opts & {"mr.", "mrs.", "ms.", "miss", "dr.", "prof.", "sir", "madam"}:
                return "salutation"

            # Notice period signature
            if any("immediately" in o or "immediate" in o for o in opt_labels) and any("days" in o or "month" in o for o in opt_labels):
                return "notice_period"

            # Experience signature
            id_sel_name_check = f"{field.get('id') or ''} {field.get('name') or ''} {field.get('selector') or ''}".lower()
            if "exp" in id_sel_name_check or "experience" in hay.lower():
                if "month" in id_sel_name_check or "month" in hay.lower():
                    return "experience_months"
                if "year" in id_sel_name_check or "year" in hay.lower():
                    return "experience_years"
            if any("year" in o for o in opt_labels) and any("experience" in o or "+" in o for o in opt_labels):
                return "experience"

            # Languages signature
            common_langs = {"english", "spanish", "french", "german", "hindi", "arabic", "mandarin", "russian", "japanese", "bengali", "portuguese"}
            if len(all_opts & common_langs) >= 3 or "language" in hay:
                return "language"

            # Country / Nationality signature
            common_countries = {"united states", "india", "united kingdom", "canada", "australia", "germany", "france"}
            if len(all_opts & common_countries) >= 3 or "country" in hay or "nationality" in hay or "citizenship" in hay:
                if any(w in hay.lower() for w in ["nationality", "citizenship"]) and not any(w in hay.lower() for w in ["country", "current country"]):
                    return "nationality"
                return "country"

        # Check terms / agreement
        if any(re.search(r"\b" + re.escape(w) + r"\b", hay, re.I) for w in ("terms and conditions", "privacy policy", "terms of service", "consent", "i agree", "agree to")):
            return "agreement"

        # Check salary amount before generic keyword loops
        if any(w in hay for w in ["current salary", "current ctc", "current pay", "current annual"]):
            return "current_salary"
        if any(w in hay for w in ["expected salary", "expected ctc", "expected pay", "expected annual"]):
            return "expected_salary"

        t_low = (field.get("type") or "").lower()
        id_low = (field.get("id") or "").lower()
        phone_check = f"{field.get('id') or ''} {field.get('name') or ''} {component} {fcomponent} {field.get('placeholder') or ''} {label}".lower()
        if t_low == "tel" or id_low in {"candidate_phone", "phone", "mobile", "mobile_phone"} or ctype == "phone":
            return "phone"
        if re.search(r"phone|mobile|telephone|cellphone", phone_check):
            if not re.search(r"\b(country|dial)\b", phone_check):
                return "phone"
        # Specific file/resume triggers
        if ctype == "file" or field.get("is_file_trigger") or (tag == "input" and ctype == "file"):
            hay_lower = hay.lower()
            if any(x in hay_lower for x in ["additional file", "additional files", "additional document", "other document", "other attachment", "attachments"]):
                return "additional_files"
            if any(x in hay_lower for x in ["cover letter", "cover_letter", "coverletter"]):
                return "cover_letter"
            if any(x in hay_lower for x in ["portfolio", "work sample"]):
                return "portfolio_file"
            return "resume"

        # DOB split signatures
        id_sel_name = f"{field.get('id') or ''} {field.get('name') or ''} {field.get('selector') or ''}".lower()
        if re.search(r"\b(year|dob_year|birth_year)\b", id_sel_name) or (options and len(options) >= 15 and all(re.match(r"^\d{4}$", str(o.get("value") or o.get("label") or "")) for o in options[:5])):
            if any(x in hay.lower() for x in ("birth", "dob", "born")):
                return "dob_year"
        if re.search(r"\b(month|dob_month|birth_month)\b", id_sel_name) or (options and len(options) in (12, 13) and any(str(o.get("label")) in {"January", "Jan", "February"} for o in options)):
            if any(x in hay.lower() for x in ("birth", "dob", "born", "month")):
                return "dob_month"
        if re.search(r"\b(day|dob_day|birth_day)\b", id_sel_name) or (options and len(options) in (31, 32) and any(str(o.get("value")) in {"1", "31"} for o in options)):
            if any(x in hay.lower() for x in ("birth", "dob", "born", "day")):
                return "dob_day"

        hay_words = re.sub(r"[_\-\.\/]+", " ", hay)
        full_hay = f"{hay} {hay_words}"

        for key, words in FIELD_KEYWORD_RULES:
            for w in words:
                if re.search(r"(?i)\b" + re.escape(w) + r"\b", full_hay):
                    if key == "current_company" and any(x in full_hay.lower() for x in ("notice", "salary", "ctc", "pay", "compensation", "experience", "role", "designation", "title")):
                        continue
                    if key == "full_name":
                        if any(x in full_hay.lower() for x in ("school", "college", "university", "institute", "company", "employer", "organization", "firm", "linkedin", "github", "portfolio", "website", "url")):
                            continue
                    if key in {"start_date", "end_date"}:
                        if any(x in full_hay.lower() for x in ("school", "college", "university", "institute", "degree", "education", "course", "qualification", "specialization")):
                            return f"education_{key}"
                        if any(x in full_hay.lower() for x in ("company", "employer", "organization", "job", "work", "experience", "employment")):
                            return f"work_{key}"
                    return key
        return "custom_question"

    def classify_fields(self, fields):
        mapping = {}
        if self.use_bedrock and self.client:
            try:
                mapping = self._bedrock_classify(fields)
            except Exception as exc:
                print(f"  Bedrock classification failed: {exc}")
                mapping = {}

        for i, field in enumerate(fields):
            ctype = (field.get("control_type") or "").lower()
            tag = (field.get("tag") or "").lower()
            if tag in {"input", "textarea", "select"} and ctype not in {"button", "submit_button", "reset"}:
                pass
            elif self._is_navigation_label(field.get("label") or field.get("question")):
                field["skip"] = True
                continue
            ctype = (field.get("control_type") or "").lower()
            tag = (field.get("tag") or "").lower()
            f_ident = f"{field.get('label') or ''} {field.get('question') or ''} {field.get('name') or ''} {field.get('id') or ''} {field.get('selector') or ''} {field.get('data_testid') or ''}".lower()
            is_submit_btn = (
                ctype == "submit_button"
                or (
                    (tag in {"button", "div"} or ctype in {"button", "submit_button"} or field.get("role") == "button")
                    and (
                        re.search(r"\b(submit(\s+application|\s+form)?|apply\s+for\s+this\s+(position|job|role)|apply\s+now)\b", f_ident)
                        or any(w in f_ident for w in ["apply-button", "apply_button", "submit-button", "submit_button", "gh-widget-btn", "btn-submit"])
                    )
                )
            )
            if is_submit_btn and not any(x in f_ident for x in ["linkedin", "indeed", "oauth", "social", "apply with"]):
                field["field_key"] = "submit_button"
                field["control_type"] = "submit_button"
            else:
                field["field_key"] = self.heuristic_classify(field)
                if field["field_key"] == "custom_question":
                    field["field_key"] = mapping.get(str(i)) or mapping.get(i) or "custom_question"
            k = field.get("field_key")
            if k in {"start_date", "end_date"}:
                prev_keys = [fields[j].get("field_key") for j in range(max(0, i - 4), i)]
                if any(pk in {"graduation_college", "graduation_degree", "graduation_branch", "education_start_date"} for pk in prev_keys):
                    field["field_key"] = f"education_{k}"
                elif any(pk in {"current_company", "company_name", "job_title", "work_start_date"} for pk in prev_keys):
                    field["field_key"] = f"work_{k}"
            is_file_item = ctype == "file" or field.get("is_file_trigger") or field.get("type") == "file"
            if field["field_key"] in {"resume", "cover_letter"} and not is_file_item:
                field["field_key"] = "custom_question"
            field["field_type"] = "file" if is_file_item else (field.get("control_type") or field.get("type") or "text")
            field["verified"] = False
            field["filled"] = False
            field["value_verified"] = False
        return fields

    # ------------------------------------------------------ live verification
    async def verify_selectors(self, page, fields):
        for field in fields:
            field["selector_verified"] = False
            field["selector_matches"] = 0
            sel = field.get("selector")
            if not sel:
                self.validation_log["selector_issues"].append(
                    {"field_key": field.get("field_key"), "reason": "missing selector"})
                continue
            frames = list(page.frames)
            fidx = field.get("frame_index", 0)
            if fidx < 0 or fidx >= len(frames):
                continue
            frame = frames[fidx]
            try:
                count = await frame.locator(sel).count()
            except Exception:
                count = 0
            field["selector_matches"] = count
            if count == 1:
                field["selector_verified"] = True
            elif count == 0:
                self.validation_log["selector_issues"].append(
                    {"field_key": field.get("field_key"), "selector": sel,
                     "reason": "selector resolves to zero elements"})
            else:
                self.validation_log["selector_issues"].append(
                    {"field_key": field.get("field_key"), "selector": sel,
                     "reason": f"selector matches {count} elements (ambiguous)"})
        return fields

    # --------------------------------------------------------------- outputs

    def build_page_inventory(self, fields):
        controls = []
        for idx, f in enumerate(fields):
            ctrl = {
                "field_id": f"field_{idx}",
                "tag": f.get("tag"), "type": f.get("type"),
                "control_type": f.get("control_type"),
                "id": f.get("id"), "name": f.get("name"),
                "role": f.get("role"),
                "question": f.get("question") or f.get("label"),
                "label": f.get("label"), "placeholder": f.get("placeholder"),
                "required": bool(f.get("required")),
                "disabled": bool(f.get("disabled")),
                "visible": f.get("visible", True),
                "selector": f.get("selector"),
                "selector_strategy": f.get("selector_strategy"),
                "frame_index": f.get("frame_index", 0),
                "frame_url": f.get("frame_url", ""),
                "frame_name": f.get("frame_name", ""),
                "detected_by": f.get("detected_by", []),
                "confidence": f.get("confidence", 0),
                "data_testid": f.get("data_testid"),
            }
            if f.get("options"):
                ctrl["options"] = f["options"]
            if f.get("rect"):
                ctrl["rect"] = f["rect"]
            controls.append({k: v for k, v in ctrl.items() if v is not None})
        return {
            "page_url": self.url,
            "total_fields": len(controls),
            "required_fields": len([c for c in controls if c.get("required")]),
            "controls": controls,
        }

    def build_important_fields(self, fields):
        output = []
        for idx, field in enumerate(fields):
            if field.get("skip") or not field.get("field_key") or field.get("field_key") == "submit_button":
                continue
            item = {
                "element_index": idx,
                "field_key": field.get("field_key"),
                "field_type": field.get("field_type") or field.get("control_type") or "text",
                "question": field.get("question") or field.get("label"),
                "selector": field.get("selector"),
                "frame_index": field.get("frame_index", 0),
                "required": bool(field.get("required")),
                "selector_verified": bool(field.get("selector_verified")),
                "filled": bool(field.get("filled", False)),
                "value_verified": bool(field.get("value_verified", False)),
            }
            if field.get("is_datepicker") or field.get("control_type") == "date":
                item["is_datepicker"] = True
            if field.get("current_selection_label"):
                item["current_selection"] = field["current_selection_label"]
            opts = field.get("options") or []
            if opts and field.get("control_type") in {
                    "select", "combobox", "radio", "radio_group", "checkbox", "checkbox_group"}:
                compact = []
                for o in opts:
                    entry = {"label": o.get("label"), "value": o.get("value")}
                    if o.get("selector"):
                        entry["selector"] = o["selector"]
                    compact.append(entry)
                item["options"] = compact
            output.append({k: v for k, v in item.items() if v is not None})
        return output

    async def main(self, page):
            fields = await self.discover_form(page)
            with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
                json.dump(fields, f, indent=4, ensure_ascii=False)
            print(f"Saved {len(fields)} fields to {OUTPUT_JSON}")      
            inventory = self.build_page_inventory(fields)  
            with open(OUTPUT_INVENTORY, "w", encoding="utf-8") as f:
                json.dump(inventory, f, indent=4, ensure_ascii=False)
            print(f"Saved page inventory to {OUTPUT_INVENTORY}")             
            fields = self.classify_fields(fields)
            fields = await self.verify_selectors(page, fields)
            important = self.build_important_fields(fields)
            with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
                json.dump({"fields": important}, f, indent=4, ensure_ascii=False)
            print(f"Saved {len(important)} important fields to {OUTPUT_FILE}")
            return important

if __name__ == "__main__":
    url = input("Enter the url :- ")
    extractor = FormFieldExtractor(url)
    asyncio.run(extractor.main())