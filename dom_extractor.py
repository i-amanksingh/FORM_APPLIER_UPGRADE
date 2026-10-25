import asyncio
import json
import os
import re
import sys
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
    ("full_name", ["full name", "your name", "name"]),
    ("email", ["email", "e-mail", "mail id", "email id"]),
    ("phone", ["phone", "mobile", "whatsapp", "telephone", "contact number", "tel"]),
    ("date_of_birth", ["date of birth", "dob", "birth date"]),
    ("gender", ["gender", "sex"]),
    ("address", ["address", "street address", "street", "postal address"]),
    ("city", ["city", "town"]),
    ("state", ["state", "province", "region"]),
    ("country", ["country", "nation"]),
    ("pincode", ["pincode", "pin code", "zip", "zipcode", "postal code"]),
    ("current_location", ["current location", "location", "current city"]),
    ("preferred_location", ["preferred location", "preferred city"]),
    ("linkedin_url", ["linkedin"]),
    ("github_url", ["github"]),
    ("portfolio_url", ["portfolio", "website", "personal site"]),
    ("current_company", ["current company", "company", "employer", "organization"]),
    ("job_title", ["job title", "designation", "current role", "position"]),
    ("experience", ["experience", "total experience", "years of experience", "work experience"]),
    ("notice_period", ["notice period", "notice", "joining time"]),
    ("current_salary", ["current salary", "current ctc", "current pay", "current annual"]),
    ("expected_salary", ["expected salary", "expected ctc", "expected pay", "expected annual"]),
    ("skills", ["skills", "skill set", "technical skills", "technologies"]),
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
    r"\b(apply|application|interested|join us|register|quick apply|easy apply)\b",
    re.I,
)
APPLY_BAD_RE = re.compile(r"\b(filter|login|sign in|search|subscribe|download|share)\b", re.I)

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

  const CHROME_WORDS = /\b(navbar|navigation|nav|topbar|top bar|sidebar|side bar|breadcrumb|breadcrumbs|site header|site footer|site nav|page header|page footer|masthead|footer)\b/;
  const isInChrome = el => {
    let cur = el;
    while (cur && cur.nodeType === 1 && cur !== document.documentElement) {
      const t = cur.tagName ? cur.tagName.toLowerCase() : '';
      const role = (cur.getAttribute('role') || '').toLowerCase();
      if (['header', 'nav', 'footer', 'aside'].includes(t)) return true;
      if (['navigation', 'banner', 'contentinfo', 'complementary', 'menu',
           'menubar', 'search'].includes(role)) return true;
      const cid = ((typeof cur.className === 'string' ? cur.className : '') + ' '
                   + (cur.id || '')).toLowerCase().replace(/[-_]+/g, ' ').replace(/\s+/g, ' ');
      if (CHROME_WORDS.test(cid)) return true;
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

  const isSubmitButton = el => {
    const name = ((el.getAttribute('aria-label') || el.getAttribute('value') || el.getAttribute('name') || el.textContent) || '').toLowerCase();
    if (!SUBMIT_NAME_RE.test(name)) return false;
    if (hasForm && !el.closest('form')) return false;
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
    if ((tag === 'button' || role === 'button' || tag === 'a')
        && el.querySelector && el.querySelector('input[type="file"]')) return true;
    return false;
  };

  const DATEPICKER_CLASS_RE = /(datepicker|date-picker|calendar|hasDatepicker|ki-calendar)/i;
  const DATEPICKER_CONTAINER_RE = /(input-calendar|datepicker|date-picker|calendar|select2|chosen|drophead|drop-body|dropdown|combobox|autocomplete)/i;
  const isWidgetTrigger = el => {
    if (!el || el.tagName !== 'INPUT') return false;
    const type = (el.getAttribute('type') || '').toLowerCase();
    if (type && type !== 'text') return false;
    const cls = typeof el.className === 'string' ? el.className : '';
    if (DATEPICKER_CLASS_RE.test(cls)) return true;
    let cur = el.parentElement, hops = 0;
    while (cur && hops < 3) {
      const ccls = typeof cur.className === 'string' ? cur.className : '';
      const cid = cur.id || '';
      if (DATEPICKER_CONTAINER_RE.test(ccls + ' ' + cid)) return true;
      cur = cur.parentElement; hops++;
    }
    return false;
  };

  const isControl = el => {
    const tag = el.tagName ? el.tagName.toLowerCase() : '';
    const type = (el.getAttribute('type') || '').toLowerCase();
    const role = (el.getAttribute('role') || '').toLowerCase();
    if (type === 'hidden') return false;
    if (tag === 'a') return false;
    if (isInChrome(el)) return false;
    if (isInWidget(el)) return false;
    if (EXCLUDE_ROLES.has(role)) return false;
    if (el.closest(POPUP_SEL)) return false;
    if (isInPopup(el)) return false;
    if (tag === 'input' && /search.*(country|dial|code)/i.test(
        (el.getAttribute('placeholder') || '') + ' ' + (el.getAttribute('aria-label') || ''))) return false;
    if (tag === 'button' || role === 'button' || (tag === 'input' && (type === 'submit' || type === 'button'))) {
      return isSubmitButton(el);
    }
    if (['input', 'textarea', 'select'].includes(tag)) return true;
    if (['combobox', 'listbox', 'checkbox', 'radio', 'switch', 'textbox',
         'spinbutton', 'slider'].includes(role)) return true;
    if (el.getAttribute('contenteditable') === 'true') return true;
    if (el.getAttribute('aria-expanded') !== null || el.getAttribute('aria-haspopup')) return true;
    return false;
  };

  const buildCandidates = el => {
    const tag = el.tagName.toLowerCase();
    const out = [];
    const type = el.getAttribute('type');
    const name = el.getAttribute('name');

    if (el.id) out.push({ selector: '#' + CSS.escape(el.id), strategy: 'id' });

    const dt = el.getAttribute('data-testid') || el.getAttribute('data-qa');
    if (dt && !/^undefined$/i.test(dt)) {
      const attr = el.getAttribute('data-testid') ? 'data-testid' : 'data-qa';
      out.push({ selector: tag + '[' + attr + '="' + escapeAttr(dt) + '"]', strategy: 'data_testid' });
    }

    if (name) {
      if (type) out.push({ selector: tag + '[name="' + escapeAttr(name) + '"][type="' + escapeAttr(type) + '"]', strategy: 'name_type' });
      out.push({ selector: tag + '[name="' + escapeAttr(name) + '"]', strategy: 'name' });
    }

    const al = el.getAttribute('aria-label');
    if (al) out.push({ selector: tag + '[aria-label="' + escapeAttr(al) + '"]', strategy: 'aria_label' });

    const ph = el.getAttribute('placeholder');
    if (ph) out.push({ selector: tag + '[placeholder="' + escapeAttr(ph) + '"]', strategy: 'placeholder' });

    let anc = el.parentElement;
    for (let i = 0; i < 8 && anc; i++, anc = anc.parentElement) {
      if (anc.id && count('#' + CSS.escape(anc.id)) === 1) {
        const base = '#' + CSS.escape(anc.id);
        const local = name ? tag + '[name="' + escapeAttr(name) + '"]' : tag;
        out.push({ selector: base + ' ' + local, strategy: 'ancestor_id' });
        break;
      }
    }

    const parts = [];
    let cur = el;
    for (let i = 0; i < 10 && cur && cur !== document.documentElement; i++) {
      let s = cur.tagName.toLowerCase();
      if (cur.id) { s += '#' + CSS.escape(cur.id); parts.unshift(s); break; }
      const p = cur.parentElement;
      if (p) {
        const same = Array.from(p.children).filter(c => c.tagName === cur.tagName);
        if (same.length > 1) s += ':nth-of-type(' + (same.indexOf(cur) + 1) + ')';
      }
      parts.unshift(s);
      cur = p;
    }
    out.push({ selector: parts.join(' > '), strategy: 'dom_path' });
    return out;
  };

  const labelFor = el => {
    const bits = [];
    if (el.labels) for (const l of el.labels) bits.push(clean(l.innerText || l.textContent));
    if (el.id) {
      const root = el.getRootNode();
      if (root && root.querySelector) {
        const lbl = root.querySelector('label[for="' + CSS.escape(el.id) + '"]');
        if (lbl) bits.push(clean(lbl.innerText || lbl.textContent));
      }
    }
    const labelledby = el.getAttribute('aria-labelledby');
    if (labelledby) for (const id of labelledby.split(/\s+/)) {
      const n = document.getElementById(id);
      if (n) bits.push(clean(n.innerText || n.textContent));
    }
    if (el.getAttribute('aria-label')) bits.push(clean(el.getAttribute('aria-label')));
    const ph = el.getAttribute('placeholder');
    if (ph) bits.push(clean(ph));
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
    const start = (el.getRootNode && el.getRootNode().host) || el;
    if (start.closest) {
      const container = start.closest(
        '.crc-form-row, [class*="form-field"], [class*="form-group"], ' +
        '[class*="field-container"], [class*="form-row"], fieldset');
      if (container) {
        const q = container.querySelector('label, legend');
        if (q && q !== el && !el.contains(q)) {
          const t = clean(q.innerText || q.textContent);
          if (t) bits.push(t);
        }
      }
    }
    const uniq = [...new Set(bits.filter(Boolean))]
        .map(t => t.replace(/^[\s*✱]+|[\s*✱]+$/g, '').trim()).filter(Boolean);
    uniq.sort((a, b) => b.length - a.length);
    return uniq.find(x => x.length <= 350) || uniq[0] || null;
  };

  const groupLabelFor = el => {
    const bits = [];
    const fs = el.closest && el.closest('fieldset');
    if (fs) { const lg = fs.querySelector('legend'); if (lg) bits.push(clean(lg.innerText || lg.textContent)); }
    const rg = el.closest && el.closest('[role="radiogroup"], [role="group"]');
    if (rg) {
      if (rg.getAttribute('aria-label')) bits.push(clean(rg.getAttribute('aria-label')));
      const lb = rg.getAttribute('aria-labelledby');
      if (lb) for (const id of lb.split(/\s+/)) {
        const n = document.getElementById(id);
        if (n) bits.push(clean(n.innerText || n.textContent));
      }
    }
    let anc = el.parentElement;
    for (let i = 0; i < 5 && anc; i++, anc = anc.parentElement) {
      const q = anc.querySelector && anc.querySelector('legend, label, [class*="label"], [class*="question"], [class*="title"]');
      if (q && q !== el && !el.contains(q)) {
        const t = clean(q.innerText || q.textContent);
        if (t && t.length <= 300) { bits.push(t); break; }
      }
    }
    const uniq = [...new Set(bits.filter(Boolean))].map(t => t.replace(/^[\s*✱]+|[\s*✱]+$/g, '').trim()).filter(Boolean);
    uniq.sort((a, b) => b.length - a.length);
    return uniq.find(x => x.length <= 350) || uniq[0] || null;
  };

  const raw = [];
  for (const el of all) {
    if (!isControl(el)) continue;
    const tag = el.tagName.toLowerCase();
    const type = (el.getAttribute('type') || '').toLowerCase();
    const role = (el.getAttribute('role') || '').toLowerCase();

    const idName = (el.id || '') + ' ' + (el.getAttribute('name') || '');
    if (/g-recaptcha-response|recaptcha/i.test(idName)) continue;

    const r = el.getBoundingClientRect();
    let options = null;
    if (tag === 'select') {
      options = Array.from(el.options).map(o => ({
        label: clean(o.textContent) || o.value,
        value: o.value,
        selected: !!o.selected,
        disabled: !!o.disabled,
      }));
    }

    const groupLabel = (type === 'radio' || type === 'checkbox' || role === 'radio' || role === 'checkbox')
        ? groupLabelFor(el) : null;

    const haspopup = (el.getAttribute('aria-haspopup') || '').toLowerCase();
    const needsClick = (role === 'combobox' || role === 'listbox' || haspopup === 'listbox'
                        || isWidgetTrigger(el));

    const comp = componentAncestor(el);
    const fcomp = fieldComponent(el);
    const zcqa = (fcomp && fcomp.getAttribute) ? fcomp.getAttribute('data-zcqa') : null;

    const currentValue = (tag === 'input' && type === 'file') ? null : (el.value || null);

    raw.push({
      el: el,
      data: {
        tag: tag,
        type: type || role || (tag === 'button' ? 'button' : (tag === 'select' ? 'select' : 'text')),
        role: role || null,
        id: el.id || null,
        name: el.getAttribute('name') || null,
        data_testid: el.getAttribute('data-testid') || null,
        label: labelFor(el),
        group_label: groupLabel,
        placeholder: el.getAttribute('placeholder') || null,
        required: el.required === true || el.getAttribute('aria-required') === 'true',
        disabled: el.disabled === true || el.getAttribute('aria-disabled') === 'true',
        checked: el.checked === true || el.getAttribute('aria-checked') === 'true',
        value: currentValue,
        options: options,
        needs_click: needsClick,
        haspopup: el.getAttribute('aria-haspopup') || null,
        readonly: el.readOnly === true || el.getAttribute('aria-readonly') === 'true',
        is_file_trigger: isFileTrigger(el),
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
    if (isNativeC(c) && c.data.readonly && !c.data.needs_click) drop.add(i);
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
    const primaryNative = native.filter(i => !isSearchInput(raw[i].el) && !raw[i].data.readonly);
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
  const trigger = document.querySelector(sel);
  if (!trigger) return { options: [], selected_label: null, widget_kind: null };

  if (trigger.tagName.toLowerCase() === 'select') {
    const out = [];
    let selected_label = null;
    for (const o of trigger.options) {
      const label = clean(o.textContent) || o.value;
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
      const near = p.querySelector('[role="listbox"]:not([hidden])');
      if (near && visible(near)) { roots.push(near); break; }
    }
  }
  if (!roots.length) {
    document.querySelectorAll('[role="listbox"]').forEach(lb => { if (visible(lb)) roots.push(lb); });
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
    if (root.matches && root.matches('[role="option"]')) candidates.push(root);
    candidates.push(...root.querySelectorAll('[role="option"], li, [class*="option" i]'));
    for (const el of candidates) {
      if (!visible(el)) continue;
      if (el.disabled || el.getAttribute('aria-disabled') === 'true') continue;
      if (el.querySelector('[role="option"]')) continue;
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
      else if (el.getAttribute('role') === 'option') {
        osel = '[role="option"][aria-label="' + escapeAttr(el.getAttribute('aria-label') || text) + '"]';
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
      if (out.length >= 100) break;
    }
    if (out.length) { widget_kind = 'listbox'; break; }
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
        if tag == "button" or type_ == "submit":
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
            name = c.get("name")
            if tag == "input" and type_ == "radio" and name:
                radios.setdefault((c.get("frame_index", 0), name), []).append(c)
            elif tag == "input" and type_ == "checkbox" and name:
                checkboxes.setdefault((c.get("frame_index", 0), name), []).append(c)
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
        await page.wait_for_timeout(1500)

        await self._ensure_application_form_open(page)
        await self._expand_repeating_sections(page)
        await page.wait_for_timeout(1000)
        
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
                ctrl["question"] = group_label or label
                if not ctrl.get("label"):
                    ctrl["label"] = group_label or label
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
        """Collapse fields that are the *same logical field* repeated on the
        page (Keka-style repeating Experience rows all share the same
        name/label but have different rects and no numbered suffix).

        Identity = (normalized question or label, tag, control_type, name).
        Only collapses when a group has >= 2 identical-identity members AND
        at least one of them lacks a unique id (i.e. the page isn't offering
        a legitimate way to distinguish them).
        """
        groups = {}
        for i, f in enumerate(fields):
            # Skip radio/checkbox groups — those legitimately repeat.
            ctype = (f.get("control_type") or "").lower()
            if ctype in {"radio_group", "checkbox_group", "radio", "checkbox"}:
                continue
            label = self.normalize_text(f.get("question") or f.get("label") or "")
            name = self.normalize_text(f.get("name") or "")
            tag = (f.get("tag") or "").lower()
            if not label and not name:
                continue
            # Build a semantic key. If the field has a unique id we trust it
            # (id is a legitimate discriminator), otherwise fall back to
            # label+name+tag+control_type.
            if f.get("id"):
                key = ("id", f["id"])
            else:
                key = (label, name, tag, ctype)
            groups.setdefault(key, []).append(i)

        drop = set()
        for key, idxs in groups.items():
            if len(idxs) < 2:
                continue
            # If the group key is based on an id, id collisions are impossible
            # by definition — skip.
            if key[0] == "id":
                continue
            # Keep the first (topmost in DOM order — fields are appended in
            # discovery order, which follows document order).
            keep = idxs[0]
            for i in idxs[1:]:
                drop.add(i)
        if drop:
            self.validation_log["manual_review"].append({
                "reason": "collapsed semantic duplicates (same label+name+tag)",
                "dropped": len(drop),
            })
            return [f for i, f in enumerate(fields) if i not in drop]
        return fields

    def _collapse_repeating_rows(self, fields):
        row_re = re.compile(r"_(\d+)(?=[_\s>#]|$)")
        groups = {}
        for i, f in enumerate(fields):
            src = None
            for k in ("name", "id", "selector"):
                v = str(f.get(k) or "")
                if v and row_re.search(v):
                    src = v
                    break
            if not src:
                continue
            m = row_re.search(src)
            row = int(m.group(1))
            if row > 100:
                continue
            base = src[: m.start()]
            suffix = src[m.end():]
            groups.setdefault((base, suffix), {})[row] = i

        drop = set()
        for (base, suffix), rows in groups.items():
            if len(rows) < 3:
                continue
            keep_row = min(rows)
            for row, i in rows.items():
                if row != keep_row:
                    drop.add(i)
        if drop:
            self.validation_log["manual_review"].append({
                "reason": "collapsed repeating rows (kept first row)",
                "dropped": len(drop),
            })
            return [f for i, f in enumerate(fields) if i not in drop]
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

            if tag == "select":
                if field.get("options"):
                    field["options_discovery"] = {
                        "status": "captured", "method": "static",
                        "count": len(field["options"]),
                    }
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

            if options:
                field["options"] = options
                field["options_discovery"] = {
                    "status": "captured",
                    "method": "click_to_open_live_dom",
                    "widget_kind": widget_kind,
                    "count": len(options),
                    "possibly_capped": len(options) >= 100,
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
        return fields

    # ------------------------------------------------ apply / expand (generic)
    async def _count_visible_inputs(self, page):
        total = 0
        for frame in page.frames:
            if self._is_ignorable_frame(frame):
                continue
            try:
                total += await frame.locator(
                    "input:visible, textarea:visible, select:visible, "
                    "[contenteditable='true'], [role='textbox']").count()
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

            before = await self._count_visible_inputs(page)
            if before >= 8:
                return True
            contexts = [page] + [f for f in page.frames if not self._is_ignorable_frame(f)]
            for ctx in contexts:
                try:
                    loc = ctx.locator(":is(button, a, [role='button'], input[type='button'], input[type='submit'])")
                    count = await loc.count()
                except Exception:
                    continue
                for i in range(min(count, 30)):
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
                        after = await self._count_visible_inputs(page)
                        if after > before:
                            print(f"  Form opened ({before} -> {after} visible inputs)")
                            return True
                    return True
        except Exception as exc:
            print(f"  Notice (apply CTA): {exc}")
        return False

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
                loc = ctx.locator(":is(button, a, [role='button'])")
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

                norm = self.normalize_text(text).strip(" +*→←")
                if norm in clicked_labels:
                    # Same logical "add" already handled — skip silently.
                    continue

                # Stable live key so identical elements aren't re-clicked
                # even if their text differs slightly (e.g. trailing spaces).
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
                await page.wait_for_timeout(500)
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
        label = self.normalize_text(field.get("label") or field.get("question") or field.get("placeholder") or "")
        name = self.normalize_text(field.get("name") or "")
        ctype = (field.get("control_type") or "").lower()
        component = self.normalize_text(field.get("component"))
        fcomponent = self.normalize_text(field.get("field_component"))
        zcqa = self.normalize_text(field.get("data_zcqa") or field.get("data_qa"))
        zcqa = re.sub(r"^(rec|manual|cx|prop|lt|apply|easy|field)[_\-]?", "", zcqa).replace("_", " ")
        hay = f"{label} {name} {component} {fcomponent} {zcqa}"

        if ctype == "file":
            if "cover" in hay:
                return "cover_letter"
            if any(w in hay for w in ("photo", "image", "picture", "passport", "avatar", "profile pic")):
                return "photo"
            return "resume"
        if (ctype == "phone" or "tel" in name or "phone" in component
                or "phone" in fcomponent or "telephone" in component or "mobile" in component):
            return "phone"
        if ctype == "email" or "email" in component or "email" in fcomponent or "e-mail" in component:
            return "email"
        if ctype in {"select", "combobox"} or field.get("options"):
            opts = " ".join(self.normalize_text(o.get("label") or "") for o in (field.get("options") or []))
            if set(opts.split()) & {"mr.", "mrs.", "ms.", "miss", "dr.", "prof.", "sir", "madam"}:
                return "salutation"
            hay = f"{hay} {opts}"

        for key, words in FIELD_KEYWORD_RULES:
            if any(w in hay for w in words):
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
            if self._is_navigation_label(field.get("label") or field.get("question")):
                field["skip"] = True
                continue
            ctype = (field.get("control_type") or "").lower()
            if ctype == "submit_button":
                field["field_key"] = "submit_button"
            else:
                field["field_key"] = self.heuristic_classify(field)
                if field["field_key"] == "custom_question" and mapping.get(i):
                    field["field_key"] = mapping[i]
            if field["field_key"] in {"resume", "cover_letter"} and ctype != "file":
                field["field_key"] = "custom_question"
            field["field_type"] = field.get("control_type") or field.get("type") or "text"
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
        for field in fields:
            if field.get("skip") or not field.get("field_key"):
                continue
            item = {
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
            inventory = self.build_page_inventory(fields)   
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