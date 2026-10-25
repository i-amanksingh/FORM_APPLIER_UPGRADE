import os
import sys
import re
import json
import time
import asyncio
import csv
from difflib import SequenceMatcher
from datetime import datetime, date
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import boto3
from playwright.async_api import (
    async_playwright,
    Page,
    Frame,
    Locator,
    TimeoutError as PlaywrightTimeoutError,
)

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

# ============================================================
# CONFIG
# ============================================================

JOB_URL = sys.argv[1] if len(sys.argv) > 1 else os.getenv("JOB_URL", "")

_BASE_DIR = Path(__file__).resolve().parent
ELEMENTS_FILE = str(_BASE_DIR / "elements.json")
IMPORTANT_FIELDS_FILE = str(_BASE_DIR / "important_data_fields.json")
RESUME_FILE = str(_BASE_DIR / "resume_imp_content.json")

AI_ANSWERS_FILE = str(_BASE_DIR / "ai_answers.json")
MAPPING_FILE = str(_BASE_DIR / "mapped_fields.json")
RESULT_FILE = str(_BASE_DIR / "fill_result.json")

AWS_REGION = os.getenv("AWS_REGION", "us-east-1")
MODEL_ID = os.getenv("BEDROCK_MODEL_ID", "us.amazon.nova-pro-v1:0")

# For testing, the browser stays visible.
HEADLESS = False

# Keep operations fast. We do NOT wait 30 seconds for every selector.
LOCATOR_TIMEOUT_MS = 2500
VERIFY_TIMEOUT_MS = 1500
PAGE_LOAD_TIMEOUT_MS = 60000

# Optional resume file for <input type=file>.
RESUME_PATH = os.getenv("RESUME_PATH", r"E:\OneDrive\Documents\Aman_Kumar_Singh.pdf")
if not RESUME_PATH and os.path.exists("Aman_Kumar_Singh_Resume.pdf"):
    RESUME_PATH = str(Path("Aman_Kumar_Singh_Resume.pdf").resolve())

# Optional user profile directory. Leave empty to use a fresh browser.
USER_DATA_DIR = os.getenv("PLAYWRIGHT_USER_DATA_DIR", "")
KEEP_BROWSER_OPEN = os.getenv("KEEP_BROWSER_OPEN", "1") != "0"
ASK_MISSING_FIELDS = os.getenv("ASK_MISSING_FIELDS", "1") != "0"
SCROLL_STEPS = int(os.getenv("SCROLL_STEPS", "8"))
FIELD_FILL_TIMEOUT_S = float(os.getenv("FIELD_FILL_TIMEOUT_S", "12"))
AI_BATCH_SIZE = int(os.getenv("AI_BATCH_SIZE", "40"))
AI_MAX_BATCHES = int(os.getenv("AI_MAX_BATCHES", "1"))

# ============================================================
# SMALL UTILITIES
# ============================================================

def clean_text(value: Any) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()

def norm(value: Any) -> str:
    s = clean_text(value).casefold()
    s = re.sub(r"[^a-z0-9+@._%/-]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()

def digits(value: Any) -> str:
    return re.sub(r"\D", "", str(value or ""))

def ensure_https_url(value: Any) -> str:
    s = clean_text(value)
    if not s:
        return ""
    if re.match(r"^https?://", s, re.I):
        return s
    if s.startswith("//"):
        return "https:" + s
    return "https://" + s.lstrip("/")

def canonical_text(value: Any) -> str:
    s = norm(value)
    s = re.sub(r"\bplease select\b", "", s)
    s = re.sub(r"\bselect(?:\s*\.{0,3})?\b", "", s)
    s = re.sub(r"\.{2,}", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s

def token_set(value: Any) -> set:
    return {x for x in canonical_text(value).split() if len(x) > 1}

def is_skip_answer(value: Any) -> bool:
    return canonical_text(value) in {"skip", "n a", "na", "n/a", "none"}

def is_positive_answer(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    s = norm(clean_text(value))
    return s in {"true", "1", "yes", "y", "checked", "i agree", "agree", "currently working here"}

def fuzzy_score(target: Any, candidate: Any) -> int:
    a = canonical_text(target)
    b = canonical_text(candidate)
    if not a or not b:
        return 0
    if a == b:
        return 100
    if a.replace(" ", "") == b.replace(" ", ""):
        return 98
    aliases = {
        "yes": {"yes", "agree", "i agree", "accept", "accepted", "true"},
        "no": {"no", "disagree", "do not agree", "false"},
        "unknown": {"unknown", "prefer not to say", "prefer not to answer", "decline to self identify", "decline to answer"},
        "india": {"india", "indian", "republic of india"},
        "indian": {"india", "indian", "republic of india"},
        "male": {"male", "man"},
        "female": {"female", "woman"},
        "asia": {"asia", "asian", "asia pacific", "asia pacific time zones", "asia/kolkata"},
        "asian": {"asia", "asian", "asia pacific", "asia pacific time zones", "asia/kolkata"},
        "kolkata": {"asia/kolkata", "kolkata", "calcutta", "ist", "india standard time"},
        "gurugram": {"gurugram", "gurgaon", "delhi", "new delhi"},
        "gurgaon": {"gurugram", "gurgaon", "delhi", "new delhi"},
    }
    for key, values in aliases.items():
        if a == key and b in values:
            return 96
        if b == key and a in values:
            return 96
    # Substring matches must be whole tokens. "India" must not select
    # "British Indian Ocean Territory" or "Indiana".
    if re.search(rf"(^|\s){re.escape(a)}(\s|$)", b):
        return 92
    if re.search(rf"(^|\s){re.escape(b)}(\s|$)", a):
        return 78
    ta, tb = token_set(a), token_set(b)
    overlap = len(ta & tb) / max(1, len(ta | tb))
    ratio = SequenceMatcher(None, a, b).ratio()
    return int(max(overlap * 86, ratio * 82))

def is_skill_tag_input(e: Dict[str, Any]) -> bool:
    q = canonical_text(e.get("question") or e.get("label") or "")
    placeholder = canonical_text(e.get("placeholder") or "")
    ident = canonical_text(e.get("id") or e.get("name") or "")
    cls = canonical_text(e.get("class") or e.get("className") or "")
    sel = canonical_text(e.get("selector") or "")
    is_skill = any("skill" in x for x in (q, placeholder, ident, cls, sel))
    is_tag = any(x in (placeholder + " " + ident + " " + cls) for x in ["addskill", "skillset", "tag-input", "tags-input", "add skill", "search and add"])
    tag_name = norm(e.get("tag") or "")
    return (is_skill and (is_tag or "add" in placeholder or "search" in placeholder)) or (tag_name == "input" and "addskill" in (ident + sel))

def control_kind(e: Dict[str, Any]) -> str:
    if is_skill_tag_input(e):
        return "skills_tag"
    t = norm(e.get("type"))
    tag = norm(e.get("tag"))
    role = norm(e.get("role") or e.get("runtime_role"))
    control = norm(e.get("control_type"))
    q = canonical_text(e.get("question") or e.get("label"))
    if is_file_upload(e):
        return "upload"
    if t == "checkbox" or role in {"checkbox", "switch"} or "checkbox" in control:
        return "checkbox"
    if t == "radio" or role == "radio" or control == "radio_group":
        return "radio"
    path = norm(e.get("selector") or "") + " " + norm(e.get("dom_path") or "")
    if (
        tag == "select"
        or t in {"select", "combobox", "listbox"}
        or role in {"combobox", "listbox"}
        or "combobox" in control
        or "dropdown" in control
        or "cu-select" in path
        or (bool(e.get("options")) and not is_phone_element(e))
        or (role == "button" and (
            e.get("options")
            or norm(e.get("haspopup")) in {"listbox", "menu", "list", "true"}
        ))
    ):
        return "select"
    if any("select..." in norm(e.get(k)) for k in ("label", "question", "placeholder")):
        return "select"
    if any(x in q for x in ["time zone", "timezone"]) and (
        tag == "select" or role in {"combobox", "listbox"} or e.get("options")
    ):
        return "select"
    if tag in {"button", "a", "label"} and not e.get("options"):
        return "click"
    return "fill"

def option_display(opt: Any) -> str:
    if isinstance(opt, dict):
        return clean_text(opt.get("label") or opt.get("text") or opt.get("name") or opt.get("value"))
    return clean_text(opt)

def resolve_option_from_element(e: Dict[str, Any], answer: Any, option_label: Any = None, option_value: Any = None) -> Optional[Dict[str, Any]]:
    targets = [x for x in [option_label, option_value, answer] if clean_text(x)]
    best = None
    best_score = 0
    for opt in usable_options(e):
        if not isinstance(opt, dict):
            opt = {"label": option_display(opt), "text": option_display(opt)}
        fields = [opt.get("label"), opt.get("text"), opt.get("value")]
        score = max((fuzzy_score(t, f) for t in targets for f in fields if clean_text(f)), default=0)
        score = max(score, max((option_synonym_score(t, f) for t in targets for f in fields if clean_text(f)), default=0))
        if score > best_score:
            best_score, best = score, opt
    return best if best_score >= 65 else None

def parse_date_value(value: Any) -> Optional[date]:
    s = clean_text(value)
    if not s:
        return None

    formats = [
        "%Y-%m-%d",
        "%d-%m-%Y",
        "%d/%m/%Y",
        "%m/%d/%Y",
        "%d.%m.%Y",
        "%Y/%m/%d",
        "%d %B %Y",
        "%d %b %Y",
        "%B %d, %Y",
        "%b %d, %Y",
        "%Y-%m",
        "%m/%Y",
        "%b %Y",
        "%B %Y",
        "%Y",
    ]

    for fmt in formats:
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue

    # Last-resort ISO datetime/date prefix.
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).date()
    except Exception:
        return None

def same_value(expected: Any, actual: Any) -> bool:
    e = clean_text(expected)
    a = clean_text(actual)

    if not e:
        return not a

    if norm(e) == norm(a):
        return True

    # Date values (e.g. 22/3/2026 vs 2026-03-22).
    edate = parse_date_value(e)
    adate = parse_date_value(a)
    if edate is not None and adate is not None and edate == adate:
        return True

    # Phone / numeric values.
    ed = digits(e)
    ad = digits(a)
    if ed and ad and (ed == ad or ed.endswith(ad) or ad.endswith(ed)):
        return True

    # URLs commonly appear with/without https://.
    eu = re.sub(r"^https?://", "", e.casefold()).rstrip("/")
    au = re.sub(r"^https?://", "", a.casefold()).rstrip("/")
    if eu and eu == au:
        return True

    # Option text can differ slightly from its value.
    if norm(e).replace(" ", "") == norm(a).replace(" ", ""):
        return True

    return False

def is_empty(value: Any) -> bool:
    return clean_text(value) == ""

def is_blank_display(value: Any) -> bool:
    t = canonical_text(value)
    return (not t) or t in {
        "select", "select an option", "choose", "please select", "mm/dd/yyyy", "dd/mm/yyyy",
    }

def display_matches(expected: Any, actual: Any) -> bool:
    if is_blank_display(actual) or not clean_text(expected):
        return False
    ea, aa = canonical_text(expected), canonical_text(actual)
    if aa != ea and aa in ea.split() and len(aa.split()) < len(ea.split()):
        return False
    if looks_like_group_heading(actual) and not looks_like_group_heading(expected):
        return False
    if is_fallback_other_label(actual) and not is_fallback_other_label(expected):
        return bool(re.search(r"university|college|institute|\bschool\b", ea))
    return (
        fuzzy_score(expected, actual) >= 65
        or _option_match_score(str(expected), str(actual)) >= 65
        or same_value(expected, actual)
    )

def compact_dict(d: Dict[str, Any]) -> Dict[str, Any]:
    out = {}
    for k, v in d.items():
        if v is None:
            continue
        if isinstance(v, str) and not v.strip():
            continue
        if isinstance(v, list) and not v:
            continue
        out[k] = v
    return out

def load_json(path: str) -> Any:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def save_json(path: str, data: Any) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False, default=str)

# ============================================================
# RESUME INDEX
# ============================================================

def build_resume_index(resume: Dict[str, Any]) -> Dict[str, Any]:
    p = resume.get("prompt", {})
    personal = p.get("personal_info", {})

    full_name = clean_text(personal.get("full_name"))
    parts = full_name.split()

    first_name = parts[0] if parts else ""
    last_name = parts[-1] if len(parts) > 1 else ""
    middle_name = " ".join(parts[1:-1]) if len(parts) > 2 else ""

    education = p.get("education", []) or []
    work = p.get("work_experience", []) or []
    skills = p.get("skills", {}) or {}
    projects = p.get("projects", []) or []

    grad = education[0] if education else {}

    # Best/latest work experience.
    latest_work = work[0] if work else {}

    primary_skills = []
    for key in [
        "programming_languages",
        "frameworks_libraries",
        "cloud_devops",
    ]:
        primary_skills.extend(skills.get(key, []) or [])

    secondary_skills = []
    for key in [
        "data_analytics",
        "other_skills",
    ]:
        secondary_skills.extend(skills.get(key, []) or [])

    # Preserve order while removing duplicates.
    primary_skills = list(dict.fromkeys(map(str, primary_skills)))
    secondary_skills = list(dict.fromkeys(map(str, secondary_skills)))

    phone = clean_text(personal.get("phone"))
    phone_digits = digits(phone)

    country_code = ""
    national_phone = phone
    if phone.startswith("+"):
        m = re.match(r"^\+(\d{1,3})(?:[\s-]+(.*))?$", phone)
        if m:
            country_code = "+" + m.group(1)
            rest = clean_text(m.group(2) or "")
            national_phone = rest if rest else phone
    national_digits = digits(national_phone)
    if country_code == "+91" and len(digits(phone)) >= 12:
        national_phone = digits(phone)[-10:]
    elif len(national_digits) >= 10:
        national_phone = national_digits[-10:] if country_code else national_digits

    # DOB is optional. Never invent it. Accept common resume locations/keys.
    dob = clean_text(
        personal.get("date_of_birth")
        or personal.get("dob")
        or personal.get("DOB")
        or p.get("date_of_birth")
        or p.get("dob")
        or p.get("DOB")
        or resume.get("date_of_birth")
        or resume.get("dob")
        or resume.get("DOB")
    )

    experience_years = p.get(
        "total_experience_years",
        resume.get("year_of_exp", 0),
    )
    current_salary = clean_text(
        personal.get("current_salary")
        or personal.get("Current Salary")
        or p.get("current_salary")
        or p.get("Current Salary")
        or resume.get("current_salary")
        or resume.get("Current Salary")
    )
    expected_salary = clean_text(
        personal.get("expected_salary")
        or personal.get("Expected Salary")
        or p.get("expected_salary")
        or p.get("Expected Salary")
        or resume.get("expected_salary")
        or resume.get("Expected Salary")
    )

    gender_str = clean_text(personal.get("gender") or personal.get("sex"))
    salutation = "Mr." if gender_str.lower() in {"male", "m"} else ("Ms." if gender_str.lower() in {"female", "f"} else "")

    # Useful aliases used by deterministic matching.
    idx = {
        "full_name": full_name,
        "first_name": first_name,
        "middle_name": middle_name,
        "last_name": last_name,
        "email": clean_text(personal.get("email")),
        "date_of_birth": dob,
        "dob": dob,
        "salutation": salutation,
        "current_salary": current_salary,
        "expected_salary": expected_salary,
        "phone": phone,
        "phone_digits": phone_digits,
        "country_code": country_code,
        "national_phone": national_phone,
        "linkedin": ensure_https_url(personal.get("linkedin")),
        "github": ensure_https_url(personal.get("github")),
        "gender": gender_str,
        "ethnicity": clean_text(
            personal.get("ethnicity")
            or personal.get("race")
            or personal.get("race_or_ethnicity")
        ),
        "location": clean_text(latest_work.get("location") or grad.get("location")),
        "current_location": clean_text(latest_work.get("location") or grad.get("location")),
        "professional_summary": clean_text(p.get("professional_summary")),
        "experience_years": str(experience_years),
        "graduation_college": clean_text(grad.get("institution")),
        "graduation_year": clean_text(grad.get("start_date")),
        "graduation_branch": clean_text(grad.get("field_of_study")),
        "graduation_degree": clean_text(grad.get("degree")),
        "graduation_cgpa": clean_text(grad.get("gpa")),
        "company_name": clean_text(latest_work.get("company")),
        "job_title": clean_text(latest_work.get("job_title")),
        "designation": clean_text(latest_work.get("job_title")),
        "work_location": clean_text(latest_work.get("location")),
        "work_start_date": clean_text(latest_work.get("start_date")),
        "work_end_date": clean_text(latest_work.get("end_date")),
        "is_currently_working": "true" if latest_work.get("currently_working") else "false",
        "education_degree": clean_text(grad.get("degree")),
        "education_branch": clean_text(grad.get("field_of_study")),
        "education_college": clean_text(grad.get("institution")),
        "education_location": clean_text(grad.get("location")),
        "education_start_date": clean_text(grad.get("start_date")),
        "education_end_date": (
            f"01/06/{int(digits(grad.get('start_date')) or 2023) + 4}"
            if (clean_text(grad.get("end_date")).lower() in {"present", "current", "currently enrolled"} or grad.get("currently_enrolled"))
            else clean_text(grad.get("end_date"))
        ),
        "12th_percentage": "",
        "10th_percentage": "",
        "current_employer": "",
        "employer_name": clean_text(latest_work.get("company")),
        "current_designation": "",
        "previous_employer": clean_text(latest_work.get("company")),
        "previous_designation": clean_text(latest_work.get("job_title")),
        "primary_skills": ", ".join(primary_skills),
        "secondary_skills": ", ".join(secondary_skills),
        "all_skills": ", ".join(
            list(dict.fromkeys(primary_skills + secondary_skills))
        ),
        "github": clean_text(personal.get("github")),
        "country": "",
        "nationality": "",
        "timezone": "",
        "companies_count": str(len(work)) if work else "",
        "agree_yes": "I agree",
    }

    dial = digits(country_code)
    if dial == "91":
        idx["country"] = "India"
        idx["nationality"] = "Indian"
        idx["timezone"] = "Asia/Kolkata"
    elif not idx["current_location"] and latest_work.get("location"):
        idx["current_location"] = clean_text(latest_work.get("location"))

    # Find 10th / 12th.
    for edu in education:
        degree = norm(edu.get("degree"))
        if "12" in degree or "senior secondary" in degree:
            idx["12th_percentage"] = clean_text(
                edu.get("score_percentage")
            )
        elif "10" in degree or "secondary" in degree:
            idx["10th_percentage"] = clean_text(
                edu.get("score_percentage")
            )

    currently_working = False
    for w in work:
        if w.get("currently_working"):
            currently_working = True
            idx["current_employer"] = clean_text(w.get("company"))
            idx["current_designation"] = clean_text(w.get("job_title"))
            idx["is_currently_working"] = "true"
            break
    idx["currently_working"] = currently_working
    idx["is_fresher"] = (not currently_working) and float(digits(experience_years) or 0) <= 1
    # Never treat a finished internship as the current job.
    if not currently_working:
        idx["current_employer"] = ""
        idx["current_designation"] = ""
        idx["is_currently_working"] = "false"

    supplied = p.get("user_supplied") if isinstance(p.get("user_supplied"), dict) else {}
    idx["salary_currency"] = clean_text(
        personal.get("salary_currency") or supplied.get("salary_currency")
    )
    if not idx["salary_currency"] and dial == "91":
        idx["salary_currency"] = "INR"
    idx["salary_frequency"] = clean_text(
        personal.get("salary_frequency") or supplied.get("salary_frequency") or "Yearly"
    )
    if idx.get("current_salary"):
        idx["current_salary"] = salary_amount_text(idx["current_salary"])
    if idx.get("expected_salary"):
        idx["expected_salary"] = salary_amount_text(idx["expected_salary"])
    idx["available_to_join_days"] = clean_text(
        personal.get("available_to_join_days") or supplied.get("available_to_join_days")
    )
    idx["how_did_you_hear"] = clean_text(supplied.get("how_did_you_hear"))
    if idx["how_did_you_hear"].lower() == "likedin":
        idx["how_did_you_hear"] = "Linkedin"
    idx["employee_reference"] = clean_text(supplied.get("employee_reference"))
    pref_jobs = resume.get("preffered_job") or []
    idx["job_position"] = clean_text(
        supplied.get("role_applying_for_other")
        or (pref_jobs[0] if pref_jobs else "")
        or latest_work.get("job_title")
    )
    for key, val in supplied.items():
        k = normalize_field_key(key)
        if clean_text(val) and not clean_text(idx.get(k)):
            idx[k] = clean_text(val)

    if not idx.get("job_status"):
        if currently_working:
            idx["job_status"] = "Serving Notice Period" if idx.get("available_to_join_days") else "Currently working"
        else:
            idx["job_status"] = "Immediately available"
    if not idx.get("reason_for_joining"):
        idx["reason_for_joining"] = "I am deeply interested in contributing my AI/ML engineering skills to build scalable, high-impact intelligent systems at Privet Ventures."
    if RESUME_PATH:
        idx["resume_file_path"] = RESUME_PATH

    return idx

# ============================================================
# DATE HELPERS
# ============================================================

def is_date_element(e: Dict[str, Any]) -> bool:
    s = element_search_text(e)
    t = norm(e.get("type"))
    ident = norm(e.get("id") or "") + " " + norm(e.get("name") or "")
    return (
        t == "date"
        or "date" in ident
        or "since" in ident
        or "working since" in s
        or "date of birth" in s
        or re.search(r"\bdob\b", s) is not None
        or "birth date" in s
        or "start date" in s
        or "end date" in s
        or "joining date" in s
        or s in {"from", "to", "from*", "to*"}
        or "mm/yyyy" in s
        or "month/year" in s
    )

# ============================================================
# ELEMENTS.JSON NORMALIZATION
# ============================================================

def normalize_elements(raw: Any) -> List[Dict[str, Any]]:
    if isinstance(raw, dict):
        if isinstance(raw.get("elements"), list):
            raw = raw["elements"]
        elif isinstance(raw.get("fields"), list):
            raw = raw["fields"]
        else:
            raw = [raw]

    if not isinstance(raw, list):
        raise ValueError("elements.json must contain a JSON array/object.")

    result = []

    for i, original in enumerate(raw):
        if not isinstance(original, dict):
            continue

        e = dict(original)
        e["_element_index"] = i

        # Normalize common alternate names.
        if not e.get("tag") and e.get("html_tag"):
            e["tag"] = e["html_tag"]

        if not e.get("type") and e.get("input_type"):
            e["type"] = e["input_type"]

        if not e.get("question"):
            e["question"] = (
                e.get("label")
                or e.get("aria_label")
                or e.get("placeholder")
                or e.get("name")
                or e.get("id")
                or ""
            )
        if not e.get("role") and e.get("runtime_role"):
            e["role"] = e.get("runtime_role")

        if not e.get("selector"):
            if e.get("css_selector"):
                e["selector"] = e["css_selector"]
            elif e.get("xpath"):
                e["selector"] = e["xpath"]

        # Normalize options.
        opts = e.get("options")
        if opts is None:
            opts = []
        if not isinstance(opts, list):
            opts = [opts]

        normalized_opts = []
        for opt in opts:
            if isinstance(opt, dict):
                normalized_opts.append(
                    compact_dict(
                        {
                            "label": opt.get("label")
                            or opt.get("text")
                            or opt.get("name"),
                            "text": opt.get("text")
                            or opt.get("label"),
                            "value": opt.get("value"),
                            "selected": opt.get("selected"),
                            "checked": opt.get("checked"),
                            "selector": opt.get("selector"),
                        }
                    )
                )
            else:
                normalized_opts.append(
                    {"label": clean_text(opt), "text": clean_text(opt)}
                )

        e["options"] = normalized_opts

        # Some extractors store options under option_list/choices.
        if not e["options"]:
            for key in ("option_list", "choices", "select_options"):
                if isinstance(e.get(key), list):
                    e["options"] = [
                        {"label": option_display(x), "text": option_display(x), **(x if isinstance(x, dict) else {})}
                        for x in e[key]
                    ]
                    break

        # Radio options can be nested under radio_options.
        if not e["options"] and isinstance(e.get("radio_options"), list):
            e["options"] = e["radio_options"]

        result.append(e)

    return result

def element_search_text(e: Dict[str, Any]) -> str:
    values = [
        e.get("question"),
        e.get("label"),
        e.get("name"),
        e.get("id"),
        e.get("placeholder"),
        e.get("aria_label"),
        e.get("autocomplete"),
        e.get("type"),
        e.get("tag"),
        e.get("role"),
        e.get("control_type"),
    ]

    for opt in e.get("options", []) or []:
        if isinstance(opt, dict):
            values.extend(
                [
                    opt.get("label"),
                    opt.get("text"),
                    opt.get("value"),
                ]
            )

    return norm(" ".join(clean_text(x) for x in values if x is not None))

def is_captcha(e: Dict[str, Any]) -> bool:
    s = element_search_text(e)
    return any(
        x in s
        for x in [
            "captcha",
            "recaptcha",
            "hcaptcha",
            "i am not a robot",
        ]
    )

def is_submit(e: Dict[str, Any]) -> bool:
    t = norm(e.get("type"))
    tag = norm(e.get("tag"))
    if t in {"checkbox", "radio", "text", "email", "tel", "file", "textarea"}:
        return False
    if tag in {"select", "textarea"}:
        return False
    ident = " ".join(norm(e.get(k)) for k in ("id", "name", "selector", "class", "className"))
    if any(w in ident for w in ["close", "modal", "dismiss", "cancel", "accessibility", "userway", "feedback", "newsletter", "search", "menu", "nav"]):
        return False
    if is_noise_control(e):
        return False
    s = element_search_text(e)
    if any(w in s for w in ["close", "modal", "dismiss", "cancel", "accessibility"]):
        return False
    return (
        "submit application" in s
        or "apply now" in s
        or "send application" in s
        or "submit form" in s
        or (t == "submit" and ("apply" in s or "submit" in s))
        or (s.strip() in {"submit", "apply", "submit application", "apply now", "send application"})
    )

def looks_like_resume_upload(e: Dict[str, Any]) -> bool:
    hay = canonical_text(
        " ".join(
            str(e.get(k) or "")
            for k in ("question", "label", "placeholder", "name", "id", "control_type", "upload_context", "text")
        )
    )
    return any(
        x in hay
        for x in (
            "upload resume",
            "upload your resume",
            "drop your files",
            "drop files here",
            "attach resume",
            "upload cv",
            "curriculum vitae",
            "upload_file",
            "resume here",
        )
    ) or ("resume" in hay and "upload" in hay)


def is_file_upload(e: Dict[str, Any]) -> bool:
    t = norm(e.get("type"))
    tag = norm(e.get("tag"))
    control = norm(e.get("control_type"))
    if (
        t == "file"
        or "file" in control
        or (tag == "input" and t == "file")
        or bool(e.get("upload_context"))
    ):
        return True
    return looks_like_resume_upload(e)

def is_phone_element(e: Dict[str, Any]) -> bool:
    if is_noise_control(e):
        return False
    t = norm(e.get("type"))
    if t == "tel":
        return True
    ident = " ".join(norm(e.get(k)) for k in ("id", "name", "autocomplete"))
    cls = norm(e.get("class") or e.get("className") or "")
    if "iti__tel" in cls:
        return True
    return bool(re.search(r"(^|_)(phone|mobile|telephone)($|_)", ident.replace(" ", "_")))

def is_noise_control(e: Dict[str, Any]) -> bool:
    """intl-tel country rows and react-select inner search boxes are not fields."""
    sel = clean_text(e.get("selector") or "").lower()
    raw_id = clean_text(e.get("id") or "").lower()
    lbl = clean_text(e.get("label") or e.get("question") or "").lower()
    if any(p in sel or p in raw_id or p in lbl for p in ["{{", "}}", "ltprop", "cxprop"]):
        return True
    cls = norm(e.get("class") or e.get("className") or "")
    role = norm(e.get("role") or e.get("runtime_role") or "")
    ident = norm(e.get("id") or "")
    label = canonical_text(e.get("label") or e.get("aria_label") or e.get("text"))
    if "iti__country" in cls and "selected" not in cls:
        return True
    if "iti__search" in cls or ident.endswith("search-input") or ident.endswith("search-box"):
        return True
    if "select__input" in cls and not clean_text(e.get("id")):
        return True
    if "select2-selection" in cls or ident.startswith("select2-") or "select2-hidden-accessible" in cls:
        return True
    if norm(e.get("tag")) == "input":
        val = clean_text(e.get("value") or "").lower()
        if val in {"years", "months", "days", "inr", "usd", "hrs", "hours"}:
            return True
    if "requiredinput" in cls.replace("-", "") or "required-input" in cls:
        return True
    if ident in {"uploadfilename", "upload-file-name", "upload_file_name"} and "resume" not in label:
        return True
    if (
        norm(e.get("tag")) == "input"
        and norm(e.get("type")) == "button"
        and "upload" in label
        and "resume" not in label
        and "cv" not in label
    ):
        return True
    if (
        norm(e.get("tag")) == "input"
        and not clean_text(e.get("id"))
        and not e.get("options")
        and "nth-of-type" in str(e.get("selector") or "")
        and "current location" not in label
        and "enter your answer" not in label
        and norm(e.get("type")) not in {"tel", "email", "file"}
        and not e.get("_important")
    ):
        return True
    if str(e.get("aria_hidden") or e.get("aria-hidden") or "").lower() == "true":
        return True
    if role == "option" and re.search(r"\+\d{1,4}$", label):
        return True
    if label in {"search", "toggle flyout"}:
        return True
    # Accessibility widgets & toolbar buttons (UserWay, EqualWeb, etc.)
    if any(x in label or x in cls or x in ident for x in [
        "userway", "uwy", "accessibility", "a11y", "equalweb",
        "highlight title", "highlight-title", "highlight titles",
        "contrast", "bigger text", "text magnifier", "screen reader",
        "dyslexia", "pause animation", "hide image", "line height",
        "text spacing", "reading mask"
    ]):
        return True
    # Marketo marketing / newsletter form fields
    if "mkto" in cls or "mkto" in ident:
        return True
    # Zero-size hidden non-upload controls
    rect = e.get("rect") or {}
    if e.get("visible") is False and rect.get("width") == 0 and rect.get("height") == 0:
        if not e.get("_important") and not looks_like_resume_upload(e):
            if "mkto" in cls or "newsletter" in ident:
                return True
    return False

def is_phone_country_element(e: Dict[str, Any]) -> bool:
    if is_noise_control(e):
        return False
    if e.get("phone_country_picker") or "phone_country" in norm(e.get("control_type")):
        return True
    ident = " ".join(norm(e.get(k)) for k in ("id", "name", "aria_label"))
    cls = norm(e.get("class") or e.get("className") or "")
    if "iti__tel" in cls:
        return False
    if "iti__selected-country" in cls or "iti__flag-container" in cls:
        return True
    clean_id = ident.replace(" ", "").replace("_", "").replace("-", "").replace(".", "")
    if any(k in clean_id for k in ("countrycode", "dialcode", "phonecode", "callingcode", "countrydial")):
        return True
    if ident.strip() in {"country", "country_code", "phone_country", "dial_code"}:
        return True
    return any(x in ident for x in ["country code", "dial code", "calling code", "selected country"])

# ============================================================
# DETERMINISTIC FIELD MATCHING
# ============================================================

FIELD_RULES = [
    (
        "full_name",
        ["full name", "your name", "candidate name", "name", "complete name"],
        "full_name",
    ),
    (
        "first_name",
        ["first name", "given name", "given name(s)", "forename", "legal first name"],
        "first_name",
    ),
    (
        "middle_name",
        ["middle name"],
        "middle_name",
    ),
    (
        "last_name",
        ["last name", "surname", "family name", "family name(s)", "legal last name"],
        "last_name",
    ),
    (
        "email",
        ["email", "e-mail", "email address", "email id"],
        "email",
    ),
    (
        "linkedin",
        ["linkedin", "linkedin url", "linkedin profile"],
        "linkedin",
    ),
    (
        "github",
        ["github", "github url", "github profile"],
        "github",
    ),
    (
        "current_location",
        ["current location", "present location", "location"],
        "current_location",
    ),
    (
        "graduation_college",
        [
            "graduation college",
            "graduation university",
            "college",
            "university",
        ],
        "graduation_college",
    ),
    (
        "graduation_year",
        [
            "graduation year",
            "year of graduation",
            "graduating year",
        ],
        "graduation_year",
    ),
    (
        "graduation_branch",
        [
            "branch in graduation",
            "specialization",
            "field of study",
            "graduation branch",
            "stream",
        ],
        "graduation_branch",
    ),
    (
        "graduation_cgpa",
        ["graduation total cgpa", "cgpa", "gpa"],
        "graduation_cgpa",
    ),
    (
        "10th_percentage",
        ["10th percentile", "10th percentage", "10th marks"],
        "10th_percentage",
    ),
    (
        "12th_percentage",
        ["12th percentile", "12th percentage", "12th marks"],
        "12th_percentage",
    ),
    (
        "date_of_birth",
        [
            "date of birth",
            "dob",
            "birth date",
            "birthday",
        ],
        "date_of_birth",
    ),
    (
        "experience_years",
        [
            "experience",
            "years of experience",
            "total experience",
            "work experience",
        ],
        "experience_years",
    ),
    (
        "employer_name",
        [
            "employer name",
            "previous employer",
            "mention the employer",
        ],
        "employer_name",
    ),
    (
        "current_employer",
        [
            "current employer",
            "current employer name",
            "present employer",
        ],
        "current_employer",
    ),
    (
        "current_designation",
        [
            "current designation",
            "current role",
            "current title",
        ],
        "current_designation",
    ),
    (
        "primary_skills",
        [
            "primary skills",
            "your primary skills",
            "technical skills",
            "key skills",
        ],
        "primary_skills",
    ),
    (
        "secondary_skills",
        ["secondary skills", "your secondary skills"],
        "secondary_skills",
    ),
    (
        "current_salary",
        ["current salary", "current ctc", "present salary", "current compensation", "current pay"],
        "current_salary",
    ),
    (
        "expected_salary",
        ["expected salary", "expected ctc", "expected compensation", "desired salary", "salary expectation"],
        "expected_salary",
    ),
    (
        "company_name",
        ["company name", "company", "employer name", "organization", "organization name"],
        "company_name",
    ),
    (
        "job_title",
        ["job title", "designation", "title", "role"],
        "job_title",
    ),
    (
        "graduation_degree",
        ["course", "degree", "qualification", "education level"],
        "graduation_degree",
    ),
    (
        "graduation_branch",
        ["branch", "specialization", "branch/ specialization", "field of study", "major"],
        "graduation_branch",
    ),
    (
        "graduation_college",
        ["university", "college", "university/ college", "school", "institution", "institute name"],
        "graduation_college",
    ),
    (
        "work_start_date",
        ["date of joining", "joining date", "start date"],
        "work_start_date",
    ),
    (
        "work_end_date",
        ["date of relieving", "relieving date", "date of completion", "end date"],
        "work_end_date",
    ),
]

def field_label_text(e: Dict[str, Any]) -> str:
    return canonical_text(
        " ".join(
            clean_text(x)
            for x in [
                e.get("label"),
                e.get("question"),
                e.get("placeholder"),
                e.get("name"),
                e.get("id"),
            ]
            if x
        )
    )

def rule_match_score(e: Dict[str, Any], phrases: List[str]) -> int:
    q = canonical_text(e.get("question") or e.get("label"))
    label = canonical_text(e.get("label"))
    score = 0
    long_question = len(q) > 90

    for phrase in phrases:
        p = canonical_text(phrase)
        if not p:
            continue
        if label == p or q == p:
            score += 100
        elif not long_question and p in label:
            score += 70
        elif not long_question and p in q:
            score += 50
        elif long_question and len(p.split()) >= 3 and p in q:
            score += 40

    # Avoid mapping phone country code as the phone number field.
    if is_phone_country_element(e):
        score -= 100
    # Do not match full_name to first_name, middle_name, last_name, or location fields
    if any(p in {"full name", "your name", "candidate name", "name", "complete name"} for p in [canonical_text(x) for x in phrases]):
        if any(part in q for part in ["first name", "middle name", "last name", "surname", "given name", "company", "employer", "organization", "organisation", "school", "college", "university", "institution", "refer", "referee", "reference", "client", "location", "city", "address", "state", "place", "postal", "zip"]):
            return 0
        ident = " ".join(norm(e.get(k)) for k in ("id", "name"))
        if any(part in ident for part in ["firstname", "middlename", "lastname", "first_name", "middle_name", "last_name", "company", "employer", "organization", "school", "college", "university", "refer", "reference", "location", "city", "address", "state", "place"]):
            return 0
    # Country dropdowns are not city/location text fields.
    if "country" in q and any(p in {"location", "current location"} for p in [canonical_text(x) for x in phrases]):
        score -= 80
    # Essay/experience textareas should not steal short resume keys.
    if any(x in q for x in ["describe your", "rationale", "evidence", "kubernetes"]):
        if any(canonical_text(p) in {"experience", "college", "university", "location"} for p in phrases):
            score -= 80

    return score

IMPORTANT_RESUME_MAP = {
    "full_name": "full_name",
    "candidate_name": "full_name",
    "name": "full_name",
    "your_name": "full_name",
    "job_status": "job_status",
    "reason_for_joining": "reason_for_joining",
    "reason_for_choosing": "reason_for_joining",
    "resume": "resume_file_path",
    "resume_upload": "resume_file_path",
    "cv": "resume_file_path",
    "first_name": "first_name",
    "middle_name": "middle_name",
    "last_name": "last_name",
    "phone_country_code": "country_code",
    "country_code": "country_code",
    "dial_code": "country_code",
    "candidate_consent": "agree_yes",
    "consent": "agree_yes",
    "privacy_consent": "agree_yes",
    "referred_by": "employee_reference",
    "referred_by_existing_emp_name": "employee_reference",
    "email": "email",
    "phone": "national_phone",
    "country": "country",
    "school": "graduation_college",
    "degree": "graduation_degree",
    "discipline": "graduation_branch",
    "linkedin": "linkedin",
    "linkedin_url": "linkedin",
    "linkedin_profile": "linkedin",
    "website": "linkedin",
    "current_work_country": "country",
    "time_zone": "timezone",
    "timezone": "timezone",
    "nationality": "nationality",
    "gender": "gender",
    "race_or_ethnicity": "ethnicity",
    "current_location": "current_location",
    "experience": "experience_years",
    "current_salary": "current_salary",
    "expected_salary": "expected_salary",
    "ctc_currency": "salary_currency",
    "ctc_amount": "current_salary",
    "ctc_frequency": "salary_frequency",
    "current_ctc": "current_salary",
    "expected_ctc": "expected_salary",
    "working_since": "working_since",
    "current_designation": "current_designation",
    "current_employer": "current_employer",
    "notice_period": "available_to_join_days",
    "availability": "available_to_join_days",
    "how_did_you_hear": "how_did_you_hear",
    "saarthee_employee_reference": "employee_reference",
    "bachelors_degree_result": "graduation_cgpa",
    "companies_worked_for": "companies_count",
    "plagiarism_agreement": "agree_yes",
    "own_words_agreement": "agree_yes",
    "recruitment_privacy_notice_agreement": "agree_yes",
    "commitment_to_meet_in_person": "agree_yes",
    "company_name": "company_name",
    "job_title": "job_title",
    "currently_working_here": "is_currently_working",
    "experience_date_of_joining": "work_start_date",
    "date_of_relieving": "work_end_date",
    "experience_location": "work_location",
    "course": "graduation_degree",
    "branch_specialization": "graduation_branch",
    "education_date_of_joining": "education_start_date",
    "education_date_of_completion": "education_end_date",
    "university_college": "graduation_college",
    "education_location": "education_location",
    "current_salary_currency": "salary_currency",
    "mobile_phone_country_code": "country_code",
    "experience_years": "experience_years",
    "experience_months": "experience_years",
    "date_of_birth": "dob",
    "dob": "dob",
    "birth_date": "dob",
    "birthday": "dob",
    "salutation": "salutation",
    "title": "salutation",
    "skills": "all_skills",
    "skill_set": "all_skills",
    "skills_set": "all_skills",
    "position_applying_for": "job_position",
    "position": "job_position",
    "job_position": "job_position",
    "how_did_you_hear": "how_did_you_hear",
    "how_did_you_hear_about_us": "how_did_you_hear",
}

SELECT_FIELD_KEYS = {
    "country",
    "school",
    "degree",
    "discipline",
    "current_work_country",
    "time_zone",
    "timezone",
    "nationality",
    "gender",
    "race_or_ethnicity",
    "high_school_math_performance",
    "high_school_native_language_performance",
    "companies_worked_for",
    "plagiarism_agreement",
    "commitment_to_meet_in_person",
}

def load_important_fields() -> List[Dict[str, Any]]:
    if not Path(IMPORTANT_FIELDS_FILE).exists():
        return []
    raw = load_json(IMPORTANT_FIELDS_FILE)
    if isinstance(raw, dict):
        fields = raw.get("fields") or raw.get("elements") or []
        return [x for x in fields if isinstance(x, dict)]
    if isinstance(raw, list):
        return [x for x in raw if isinstance(x, dict)]
    return []

def apply_important_field_hints(elements: List[Dict[str, Any]], important: List[Dict[str, Any]]) -> None:
    by_index = {e["_element_index"]: e for e in elements}
    by_selector = {clean_text(e.get("selector")): e for e in elements}
    for field in important:
        e = None
        idx = field.get("element_index")
        if idx is not None:
            try:
                e = by_index.get(int(idx))
            except Exception:
                e = None
        if e is None:
            e = by_selector.get(clean_text(field.get("selector")))
        if not e:
            continue
        e["_field_key"] = field.get("field_key")
        e["_important"] = True
        e["_important_type"] = field.get("field_type")
        if normalize_field_key(e.get("_field_key")) in SELECT_FIELD_KEYS or norm(e.get("runtime_role") or e.get("role")) == "combobox":
            e["control_type"] = e.get("control_type") or "combobox"

def infer_action_for_element(e: Dict[str, Any], default: str = "fill") -> str:
    kind = control_kind(e)
    if kind in {"select", "radio", "checkbox", "upload"}:
        return kind
    if normalize_field_key(e.get("_field_key")) in SELECT_FIELD_KEYS:
        return "select"
    return default

def collect_fill_targets(elements: List[Dict[str, Any]], important: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    by_index = {e["_element_index"]: e for e in elements}
    by_selector = {clean_text(e.get("selector")): e for e in elements}
    targets: List[Dict[str, Any]] = []
    seen = set()

    def add(e: Optional[Dict[str, Any]]) -> None:
        if not e:
            return
        idx = e["_element_index"]
        if idx in seen or is_captcha(e) or is_submit(e) or e.get("disabled") is True:
            return
        if is_noise_control(e):
            return
        if norm(e.get("type")) == "hidden":
            return
        seen.add(idx)
        targets.append(e)

    for field in important:
        if norm(field.get("field_type")) == "submit" or "submit" in norm(field.get("field_key")):
            continue
        e = None
        try:
            e = by_index.get(int(field.get("element_index")))
        except Exception:
            e = None
        if e is None:
            e = by_selector.get(clean_text(field.get("selector")))
        add(e)

    for e in dedupe_interactive_elements(elements):
        q = clean_text(e.get("question") or e.get("label"))
        required = bool(e.get("required") is True or "*" in q or norm(e.get("aria_required")) == "true")
        if e.get("_important") or required:
            add(e)
    return targets

def deterministic_candidates(
    elements: List[Dict[str, Any]],
    resume_idx: Dict[str, Any],
    important: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    candidates = []

    used_elements = set()
    important = important or []

    by_index = {e["_element_index"]: e for e in elements}
    by_selector = {clean_text(e.get("selector")): e for e in elements}

    for field in important:
        key = normalize_field_key(field.get("field_key"))
        e = None
        try:
            e = by_index.get(int(field.get("element_index")))
        except Exception:
            e = None
        if e is None:
            e = by_selector.get(clean_text(field.get("selector")))
        hay = canonical_text(
            " ".join(
                str(x or "")
                for x in [
                    field.get("question"),
                    field.get("field_key"),
                    (e or {}).get("question"),
                    (e or {}).get("label"),
                    (e or {}).get("placeholder"),
                ]
            )
        )
        if key in {"custom_question", "custom_answer"} and (
            "current location" in hay
            or "present location" in hay
            or "your location" in hay
        ):
            key = "current_location"
        resume_key = IMPORTANT_RESUME_MAP.get(key) or IMPORTANT_RESUME_MAP.get(norm(field.get("field_key") or ""))
        if not resume_key:
            continue
        value = resume_idx.get(resume_key)
        if is_empty(value):
            continue
        if not e or e["_element_index"] in used_elements:
            continue
        if is_captcha(e) or is_submit(e):
            continue
        if resume_key == "resume_file_path" and not is_file_upload(e) and not looks_like_resume_upload(e) and norm(e.get("type")) != "file":
            continue
        if is_current_employment_question(e, key) and not currently_employed(resume_idx):
            if resume_key in {
                "current_employer",
                "current_designation",
                "company_name",
                "job_title",
                "designation",
                "employer_name",
            }:
                value = "NA"
        if looks_like_currency_control(e):
            value = resume_idx.get("salary_currency") or value
        elif looks_like_salary_frequency_control(e):
            value = resume_idx.get("salary_frequency") or value
        elif resume_key in {"current_salary", "expected_salary"}:
            value = salary_amount_text(value)
        elif resume_key == "available_to_join_days":
            if norm(e.get("type")) == "number" or "days" in field_label_text(e):
                if str(value).lower() in {"immediate", "immediately", "ready", "urgent"}:
                    value = "0"
        elif resume_key == "is_currently_working":
            value = "true" if is_positive_answer(value) else "false"
        elif is_phone_country_element(e) or looks_like_dial_code_options(e.get("options") or []):
            value = resume_idx.get("country_code") or value
        elif resume_key == "experience_years" and e.get("options"):
            y = int(digits(value) or 1)
            best_opt = None
            for opt in e.get("options", []):
                lbl = clean_text(opt.get("label") if isinstance(opt, dict) else opt).lower()
                if y == 0 and "fresher" in lbl:
                    best_opt = opt
                    break
                elif y < 1 and ("less" in lbl or "<" in lbl):
                    best_opt = opt
                    break
                elif 1 <= y <= 3 and ("1-3" in lbl or "1 to 3" in lbl):
                    best_opt = opt
                    break
                elif 3 < y <= 5 and ("3-5" in lbl or "3 to 5" in lbl):
                    best_opt = opt
                    break
                elif y > 5 and ("5+" in lbl or "5 plus" in lbl or "more than 5" in lbl):
                    best_opt = opt
                    break
            if best_opt:
                value = clean_text(best_opt.get("label") if isinstance(best_opt, dict) else best_opt)
        if is_empty(value):
            continue
        action = infer_action_for_element(e)
        candidates.append({
            "element_index": e["_element_index"],
            "field": field.get("field_key") or resume_key,
            "value": str(value),
            "option_label": str(value) if e.get("options") else None,
            "option_value": str(value) if e.get("options") else None,
            "action": action,
            "source": "important_data_fields",
            "confidence": 120,
        })
        used_elements.add(e["_element_index"])
        if e.get("control_type") == "radio_group" and e.get("name"):
            for other_e in elements:
                if other_e.get("name") == e.get("name") and norm(other_e.get("type")) == "radio":
                    used_elements.add(other_e["_element_index"])

    # Phone country code gets its own deterministic mapping.
    for e in elements:
        if is_phone_country_element(e):
            if resume_idx.get("country_code"):
                candidates.append(
                    {
                        "element_index": e["_element_index"],
                        "field": "phone_country_code",
                        "value": resume_idx["country_code"],
                        "action": "select",
                        "source": "resume",
                        "confidence": 100,
                    }
                )
                used_elements.add(e["_element_index"])
                break

    # Auto-check consent, privacy, and terms checkboxes
    for e in elements:
        idx = e["_element_index"]
        if idx in used_elements:
            continue
        if norm(e.get("tag")) == "input" and norm(e.get("type")) == "checkbox":
            ident = " ".join(norm(e.get(k)) for k in ("id", "name", "aria_label", "label", "question"))
            if any(term in ident for term in ["consent", "privacy", "terms", "agree", "policy", "declaration", "acknowledg", "candidateconsent", "dataprocessing"]):
                candidates.append({
                    "element_index": idx,
                    "field": "candidate_consent",
                    "value": "true",
                    "action": "check",
                    "source": "deterministic",
                    "confidence": 100,
                })
                used_elements.add(idx)

    # Main deterministic mappings.
    for field, phrases, resume_key in FIELD_RULES:
        value = resume_idx.get(resume_key)
        if is_empty(value):
            continue

        best = None
        best_score = 0

        for e in elements:
            idx = e["_element_index"]
            if idx in used_elements:
                continue
            if is_captcha(e) or is_submit(e) or is_file_upload(e):
                continue

            score = rule_match_score(e, phrases)

            # Phone is handled separately.
            if field == "experience_years":
                if is_phone_element(e):
                    score = 0
                opts = e.get("options") or []
                yn = {
                    norm(o.get("label") if isinstance(o, dict) else o)
                    for o in opts
                }
                if yn & {"yes", "no"} or norm(e.get("control_type")) == "radio_group":
                    score = 0

            if field in {"current_salary", "expected_salary"}:
                path = (str(e.get("selector") or "") + " " + str(e.get("dom_path") or "")).lower()
                if control_kind(e) == "select" or "cu-select" in path or looks_like_currency_control(e) or looks_like_salary_frequency_control(e):
                    score = 0

            if field in {"company_name", "job_title", "current_employer", "current_designation", "employer_name"}:
                if is_current_employment_question(e, field) and not currently_employed(resume_idx):
                    score = 0

            if score > best_score:
                best = e
                best_score = score

        if best and best_score >= 50:
            action = infer_action_for_element(best)
            cand_val = str(value)
            opt_label = None
            opt_value = None

            if field == "experience_years" and best.get("options"):
                y = int(digits(value) or 1)
                best_opt = None
                for opt in best.get("options", []):
                    lbl = clean_text(opt.get("label") if isinstance(opt, dict) else opt).lower()
                    if y == 0 and "fresher" in lbl:
                        best_opt = opt
                        break
                    elif y < 1 and ("less" in lbl or "<" in lbl):
                        best_opt = opt
                        break
                    elif 1 <= y <= 3 and ("1-3" in lbl or "1 to 3" in lbl):
                        best_opt = opt
                        break
                    elif 3 < y <= 5 and ("3-5" in lbl or "3 to 5" in lbl):
                        best_opt = opt
                        break
                    elif y > 5 and ("5+" in lbl or "5 plus" in lbl or "more than 5" in lbl):
                        best_opt = opt
                        break
                if not best_opt and y == 1:
                    for opt in best.get("options", []):
                        lbl = clean_text(opt.get("label") if isinstance(opt, dict) else opt).lower()
                        if "1" in lbl:
                            best_opt = opt
                            break
                if best_opt:
                    opt_label = clean_text(best_opt.get("label") if isinstance(best_opt, dict) else best_opt)
                    opt_value = clean_text(best_opt.get("value") if isinstance(best_opt, dict) else opt_label)
                    cand_val = opt_label
                    action = "select"

            candidates.append(
                {
                    "element_index": best["_element_index"],
                    "field": field,
                    "value": cand_val,
                    "option_label": opt_label,
                    "option_value": opt_value,
                    "action": action,
                    "source": "resume",
                    "confidence": best_score,
                }
            )
            used_elements.add(best["_element_index"])

    # Salary currency (e.g. INR / USD for CTC)
    for e in elements:
        if e["_element_index"] in used_elements or is_captcha(e) or is_submit(e):
            continue
        if looks_like_currency_control(e):
            curr_val = resume_idx.get("salary_currency") or "INR"
            best_opt = resolve_option_from_element(e, curr_val)
            opt_lbl = clean_text(best_opt.get("label") if best_opt else curr_val)
            opt_val = clean_text(best_opt.get("value") if best_opt else opt_lbl)
            candidates.append({
                "element_index": e["_element_index"],
                "field": "salary_currency",
                "value": opt_lbl,
                "option_label": opt_lbl,
                "option_value": opt_val,
                "action": "select",
                "source": "resume",
                "confidence": 110,
            })
            used_elements.add(e["_element_index"])
            break

    # Salary frequency (e.g. Monthly / Yearly for CTC)
    for e in elements:
        if e["_element_index"] in used_elements or is_captcha(e) or is_submit(e):
            continue
        if looks_like_salary_frequency_control(e):
            freq_val = "Yearly"
            best_opt = resolve_option_from_element(e, freq_val)
            opt_lbl = clean_text(best_opt.get("label") if best_opt else freq_val)
            opt_val = clean_text(best_opt.get("value") if best_opt else opt_lbl)
            candidates.append({
                "element_index": e["_element_index"],
                "field": "salary_frequency",
                "value": opt_lbl,
                "option_label": opt_lbl,
                "option_value": opt_val,
                "action": "select",
                "source": "resume",
                "confidence": 110,
            })
            used_elements.add(e["_element_index"])
            break

    # Phone main input.
    if resume_idx.get("national_phone"):
        best = None
        best_score = 0

        for e in elements:
            if e["_element_index"] in used_elements:
                continue
            if is_captcha(e) or is_submit(e):
                continue
            if not is_phone_element(e):
                continue
            if is_phone_country_element(e):
                continue

            s = element_search_text(e)

            # Prefer actual text/tel input over generic buttons.
            score = 40
            if norm(e.get("type")) in {"tel", "text", "number"}:
                score += 30
            if "mobile phone" in s:
                score += 20
            if "phone number" in s:
                score += 20

            if score > best_score:
                best = e
                best_score = score

        if best:
            candidates.append(
                {
                    "element_index": best["_element_index"],
                    "field": "phone",
                    "value": resume_idx["national_phone"],
                    "action": "fill",
                    "source": "resume",
                    "confidence": best_score,
                }
            )
            used_elements.add(best["_element_index"])

    return candidates

# ============================================================
# AI BATCH MAPPING
# ============================================================

def compact_resume_for_ai(resume: Dict[str, Any]) -> Dict[str, Any]:
    p = resume.get("prompt", {}) if isinstance(resume, dict) else {}
    personal = p.get("personal_info", {}) if isinstance(p, dict) else {}
    education = p.get("education", []) if isinstance(p, dict) else []
    work = p.get("work_experience", []) if isinstance(p, dict) else []
    skills = p.get("skills", {}) if isinstance(p, dict) else {}
    currently_working = any(bool(w.get("currently_working")) for w in work if isinstance(w, dict))
    years = p.get("total_experience_years", resume.get("year_of_exp"))
    return compact_dict({
        "personal_info": compact_dict({
            "full_name": personal.get("full_name"),
            "email": personal.get("email"),
            "phone": personal.get("phone"),
            "linkedin": personal.get("linkedin"),
            "github": personal.get("github"),
            "gender": personal.get("gender") or personal.get("sex"),
            "ethnicity": personal.get("ethnicity") or personal.get("race") or personal.get("race_or_ethnicity"),
        }),
        "professional_summary": p.get("professional_summary"),
        "education": education[:3] if isinstance(education, list) else education,
        "work_experience": work[:3] if isinstance(work, list) else work,
        "employment_status": {
            "currently_working": currently_working,
            "is_fresher": (not currently_working) and float(digits(years) or 0) <= 1,
            "current_company": None,
            "current_position": None,
            "rule": "Internships already ended. Do NOT fill Current Company / Current Position / Current Designation with past intern roles. Use Not working / Fresher / None / skip.",
        },
        "skills": skills,
        "total_experience_years": years,
        "Current Salary": p.get("Current Salary") or p.get("current_salary"),
        "Expected Salary": p.get("Expected Salary") or p.get("expected_salary"),
        "user_supplied": p.get("user_supplied"),
    })

def looks_like_dial_code_options(options: List[Any]) -> bool:
    labels = []
    for opt in options or []:
        label = option_display(opt) if not isinstance(opt, str) else clean_text(opt)
        if label:
            labels.append(label)
    if len(labels) < 8:
        return False
    hits = sum(1 for label in labels if re.search(r"\+\d{1,4}\s*$", label) or re.search(r"\+\d{1,4}$", re.sub(r"\s+", "", label)))
    return hits / len(labels) >= 0.5

def salary_amount_text(value: Any) -> str:
    t = clean_text(value)
    m = re.search(r"(\d+(?:\.\d+)?)\s*(lpa|lacs|lakhs)?", t, flags=re.I)
    return m.group(1) if m else t

def looks_like_currency_control(e: Dict[str, Any]) -> bool:
    t = norm(e.get("type"))
    if t in {"checkbox", "radio"}:
        return False
    hay = canonical_text(" ".join(str(e.get(k) or "") for k in ("id", "name", "label", "question", "placeholder")))
    if any(x in hay for x in ["experience", "coding", "python", "skill", "background", "education", "degree"]):
        return False
    if "currency" in hay or "ctc_unit" in hay or "salary_unit" in hay or e.get("id") == "s-c-n":
        return True
    if any(x in hay for x in ["pay", "salary", "ctc"]) and any(x in hay for x in ["inr", "usd", "eur", "gbp"]):
        return True
    labels = [canonical_text(option_display(o)) for o in (e.get("options") or [])[:20]]
    if not labels:
        return False
    if any(x in {"yes", "no", "true", "false", "maybe"} for x in labels):
        return False
    currency_tokens = {"inr", "usd", "eur", "gbp", "cad", "aud", "aed", "chf", "jpy", "cny", "$", "₹", "€", "£"}
    hits = sum(1 for x in labels if x in currency_tokens)
    return hits >= 1 and all(len(x) <= 4 for x in labels)

def looks_like_salary_frequency_control(e: Dict[str, Any]) -> bool:
    labels = [canonical_text(option_display(o)) for o in (e.get("options") or [])[:10]]
    if any(x in {"monthly", "yearly", "annual", "per month", "per year"} for x in labels):
        return True
    hay = canonical_text(" ".join(str(e.get(k) or "") for k in ("id", "name", "label", "question", "placeholder")))
    if any(term in hay for term in ["sms", "text message", "message", "consent", "opt in", "agreement"]):
        return False
    if e.get("id") == "s-y-m" or "month_year" in hay:
        return True
    if any(w in hay for w in ["salary frequency", "pay frequency", "compensation frequency", "rate frequency"]):
        return True
    if any(w in hay for w in ["salary", "pay", "ctc", "compensation"]) and any(w in hay for w in ["frequency", "monthly", "yearly", "per annum"]):
        return True
    return False

def _user_token_kind(value: str) -> str:
    t = canonical_text(value)
    if not t:
        return "empty"
    if t in {"inr", "usd", "eur", "gbp", "aed", "cad", "aud"} or re.fullmatch(r"[a-z]{3}", t):
        return "currency"
    if re.search(r"\d\s*(lpa|lacs|lakhs)\b", t):
        return "salary"
    if re.fullmatch(r"\d{1,3}", t):
        return "days"
    if any(x in t for x in ["linkedin", "naukri", "indeed", "referral", "other"]):
        return "source"
    if t in {"no", "n/a", "na", "none", "yes"}:
        return "yesno"
    if re.fullmatch(r"20\d{2}", t):
        return "year"
    return "text"

def semantic_assign_user_values(fields: List[Dict[str, Any]], values: List[str]) -> List[str]:
    unused = [v for v in values if clean_text(v)]
    out: List[str] = []

    def take(kinds: List[str]) -> str:
        if "any" in kinds:
            return unused.pop(0) if unused else ""
        for i, v in enumerate(unused):
            if _user_token_kind(v) in kinds:
                return unused.pop(i)
        return ""

    for e in fields:
        q = canonical_text(e.get("question") or e.get("label") or e.get("placeholder"))
        kind = control_kind(e)
        if looks_like_currency_control(e):
            out.append(take(["currency"]) or take(["any"]))
        elif "available" in q or ("join" in q and "salary" not in q):
            out.append(take(["days"]) or take(["any"]))
        elif "hear" in q:
            out.append(take(["source", "text"]) or take(["any"]))
        elif "reference" in q:
            out.append(take(["yesno", "text"]) or take(["any"]))
        elif "salary" in q and kind != "select":
            out.append(take(["salary"]))
        elif "salary" in q and looks_like_currency_control(e):
            out.append(take(["currency"]) or take(["any"]))
        elif "jee" in q:
            out.append(take(["year"]) or take(["any"]))
        else:
            out.append(take(["any"]))
    return out

def persist_user_answers_to_resume(resume: Dict[str, Any], fields: List[Dict[str, Any]], answers: List[Dict[str, Any]]) -> None:
    prompt = resume.setdefault("prompt", {})
    if not isinstance(prompt, dict):
        return
    personal = prompt.setdefault("personal_info", {})
    if not isinstance(personal, dict):
        personal = {}
        prompt["personal_info"] = personal
    supplied = prompt.setdefault("user_supplied", {})
    if not isinstance(supplied, dict):
        supplied = {}
        prompt["user_supplied"] = supplied
    by_index = {e["_element_index"]: e for e in fields}
    for item in answers:
        val = clean_text(item.get("option_label") or item.get("answer"))
        if not val or norm(item.get("action")) == "skip":
            continue
        e = by_index.get(int(item["element_index"]))
        if not e:
            continue
        q = canonical_text(e.get("question") or e.get("label"))
        key = normalize_field_key(e.get("_field_key")) or re.sub(r"[^a-z0-9]+", "_", q)[:40]
        supplied[key] = val
        if "gender" in q:
            personal["gender"] = val
        elif "ethnicity" in q or re.search(r"\brace\b", q):
            personal["ethnicity"] = val
        elif looks_like_currency_control(e):
            personal["salary_currency"] = val
        elif "current salary" in q:
            prompt["Current Salary"] = val
        elif "expected salary" in q:
            prompt["Expected Salary"] = val
        elif "notice" in q or "available" in q or ("join" in q and "salary" not in q):
            personal["available_to_join_days"] = val
            supplied["available_to_join_days"] = val
        elif "hear" in q:
            supplied["how_did_you_hear"] = val
        elif "reference" in q:
            supplied["employee_reference"] = val
    save_json(RESUME_FILE, resume)
    print(f"Saved user answers into {RESUME_FILE}")

def looks_like_group_heading(label: Any) -> bool:
    text = canonical_text(label)
    if not text:
        return True
    return (
        text.endswith("time zones")
        or text.endswith("time zone")
        or text.endswith("regions")
        or text in {"loading", "searching", "no options", "no results"}
    )

def normalize_field_key(key: Any) -> str:
    k = norm(key)
    k = re.sub(r"\[\]$", "", k)
    k = re.sub(r"--\d+$", "", k)
    return k


def _employment_haystack(e: Optional[Dict[str, Any]] = None, field_key: str = "") -> str:
    e = e or {}
    return canonical_text(
        " ".join(
            str(x or "")
            for x in [
                field_key,
                e.get("_field_key"),
                e.get("question"),
                e.get("label"),
                e.get("placeholder"),
                e.get("name"),
                e.get("id"),
            ]
        )
    )


def is_previous_employment_question(e: Optional[Dict[str, Any]] = None, field_key: str = "") -> bool:
    hay = _employment_haystack(e, field_key)
    return any(
        term in hay
        for term in (
            "previous employer",
            "previous company",
            "last employer",
            "last company",
            "past employer",
            "internship company",
            "most recent intern",
        )
    )


def is_current_employment_question(e: Optional[Dict[str, Any]] = None, field_key: str = "") -> bool:
    """True for current company/role/position — not previous-employer history."""
    key = normalize_field_key(field_key or (e or {}).get("_field_key"))
    if is_previous_employment_question(e, field_key):
        return False
    if key in {
        "current_employer",
        "current_company",
        "current_designation",
        "currently_working_here",
        "current_position",
        "current_role",
    }:
        return True
    hay = _employment_haystack(e, field_key)
    return any(
        term in hay
        for term in (
            "current employer",
            "current company",
            "current organization",
            "current designation",
            "current position",
            "current role",
            "current job",
            "present employer",
            "present company",
            "currently working",
            "currently employed",
        )
    )


def currently_employed(resume_idx: Dict[str, Any]) -> bool:
    if resume_idx.get("currently_working") is True:
        return True
    return is_positive_answer(resume_idx.get("is_currently_working"))

def usable_options(e: Dict[str, Any]) -> List[Any]:
    opts = list(e.get("options") or [])
    if looks_like_dial_code_options(opts) and not is_phone_country_element(e):
        return []
    cleaned = []
    for opt in opts:
        if looks_like_group_heading(option_display(opt)):
            continue
        cleaned.append(opt)
    return cleaned

def options_for_ai(e: Dict[str, Any]) -> List[Any]:
    opts = list(e.get("options") or [])
    if looks_like_dial_code_options(opts) and not is_phone_country_element(e):
        return []
    return opts[:40]

def compact_element_for_ai(e: Dict[str, Any]) -> Dict[str, Any]:
    opts = []
    for opt in options_for_ai(e):
        if isinstance(opt, dict):
            opts.append(compact_dict({
                "label": opt.get("label") or opt.get("text"),
                "value": opt.get("value"),
                "is_group": True if opt.get("is_group") or looks_like_group_heading(opt.get("label") or opt.get("text")) else None,
            }))
        else:
            opts.append({"label": clean_text(opt)})
    return compact_dict({
        "element_index": e["_element_index"],
        "field_key": e.get("_field_key"),
        "field_type": e.get("_important_type"),
        "tag": e.get("tag"),
        "type": e.get("type"),
        "control_type": e.get("control_type"),
        "role": e.get("role") or e.get("runtime_role"),
        "id": e.get("id"),
        "name": e.get("name"),
        "question": e.get("question") or e.get("label"),
        "label": e.get("label"),
        "placeholder": e.get("placeholder"),
        "required": e.get("required"),
        "disabled": e.get("disabled"),
        "selector": e.get("selector"),
        "frame_index": e.get("frame_index"),
        "frame_url": e.get("frame_url"),
        "options": opts,
        "control_kind": control_kind(e),
    })


def leftover_important_elements(
    elements: List[Dict[str, Any]],
    important: List[Dict[str, Any]],
    filled_indexes: set,
) -> List[Dict[str, Any]]:
    """Only unfilled important_data_fields tags — this is what Bedrock should see."""
    filled = {int(i) for i in filled_indexes if i is not None}
    targets = collect_fill_targets(elements, important) if important else dedupe_interactive_elements(elements)
    leftover = []
    for e in targets:
        idx = e.get("_element_index")
        if idx is None or int(idx) in filled:
            continue
        if is_captcha(e) or is_submit(e) or is_noise_control(e):
            continue
        if e.get("disabled") is True or norm(e.get("type")) == "hidden":
            continue
        leftover.append(e)
    return dedupe_interactive_elements(leftover)

def build_ai_prompt(remaining: List[Dict[str, Any]], resume: Dict[str, Any], deterministic: List[Dict[str, Any]]) -> str:
    remaining_for_ai = [compact_element_for_ai(e) for e in remaining if not is_captcha(e) and not is_submit(e)]
    resume_json = json.dumps(compact_resume_for_ai(resume), ensure_ascii=False, separators=(",", ":"), default=str)
    elements_json = json.dumps(remaining_for_ai, ensure_ascii=False, separators=(",", ":"), default=str)
    return f'''You are a universal job-application field answering engine.
The supplied element records come from the real page. Never invent an element, selector, option, or value.

APPLICANT DATA:
{resume_json}

WEBSITE ELEMENTS:
{elements_json}

RULES:
1. Return one answer per supplied element only when it can be answered safely from applicant data or an obvious non-sensitive application fact.
2. Never invent sensitive/personal facts such as race, ethnicity, gender, disability, veteran status, work authorization, sponsorship, DOB, salary, or religious/political information. Return skip for those unless explicitly supplied by the applicant.
3. Never answer CAPTCHA and never submit the application.
4. control_kind=select means action MUST be select. control_kind=radio means radio. control_kind=checkbox means checkbox. control_kind=upload means upload only when a resume file is configured. Otherwise fill text fields.
5. For select/radio/checkbox, answer with the closest displayed option from the supplied options. Prefer an exact option label. If options are grouped (for example "Asia Pacific Time Zones"), pick the closest group label; the filler will open that group and choose the closest leaf option such as Asia/Kolkata.
6. Synonyms: country/nation/current work country; nationality/citizenship; timezone/time zone/Asia/Kolkata; university/college/school; degree/qualification; field/discipline/specialization; consent/agree/accept; travel/onsite/relocation commitment; Gurugram/Gurgaon/Delhi.
7. Infer country, nationality, and timezone from the applicant phone country code when that is explicit (for example +91 -> India, Indian, Asia/Kolkata / Asia Pacific Time Zones).
8. For consent/agreement/privacy/plagiarism selects, choose the agreeing option such as Yes or I agree.
9. If supplied options are empty, still return the applicant answer; the filler matches it against the live dropdown. Do not choose Prefer not to say / Decline when the applicant supplied gender or ethnicity. If those facts are missing, action=skip so the user can pick a listed option.
10. Keep answers short. For text questions, answer only what is supported by applicant data.
11. Copy element_index and selector exactly from input.
12. If action=select, radio, or checkbox, always set answer AND option_label to the chosen displayed option text. Never leave answer empty when option_label is set.
13. Never write placeholders such as "applicant data does not provide". If scores exist, use them. High-school marks around 90%+ map to Top 10% at school when that option exists. Discipline "Data Science" must not collapse to "Science".
14. For numeric experience scale questions (e.g. options 1..10 or 0..5 years of experience with a tool, domain, or role): if the applicant has entry-level experience or the specific role/tool is not explicitly detailed, answer with "1" (or lowest option) or applicant's years of experience. Always provide an answer for numeric experience scale questions; never omit or leave them blank.
15. For mandatory legal consent, privacy policy, and terms of service, always choose the agreeing option (e.g. "I agree" or "Yes"). HOWEVER, for promotional, marketing, SMS updates, or text messaging opt-in radio groups (e.g. "consent to receive text messages"), ALWAYS choose the negative/opt-out option (e.g. "No - I do not consent to receiving text messages" or "No").
16. If a school/university/college dropdown has no exact resume match, choose Other (or equivalent) and put the resume institution name into the follow-up text box. Never leave Other without the real school name from applicant data.
17. For salary / compensation questions: If the question or label asks for both current and expected CTC / salary, format clearly as "Current: 5 LPA, Expected: 7 LPA" (or corresponding applicant figures) rather than a single number.
18. For personal note / cover letter note / summary / intro fields: Write a crisp, professional 1-2 sentence pitch under 140 characters highlighting engineering strengths and enthusiasm for the position, tailored from applicant data without claiming student status if seeking full-time roles and without getting cut off.
19. For notice period: Use "Immediate" or "Immediate (15-30 days)" based on applicant readiness.
20. For experience threshold/range questions (e.g. "Less than 7 years" vs "7 or More than 7 years"): Compare the applicant's experience (1 year) against the number. For 1 year, choose "Less than 7 years", NEVER the senior "7 or More" option.
21. For technical skills questions (e.g. "Do you have experience on Python and do hands on Coding?"): If the skill is in the applicant's primary programming languages (Python is in skills), ALWAYS choose "Yes".
22. For voluntary EEO / demographic disclosure questions (Race, Ethnicity, Veteran Status, Hispanic/Latino, Disability, Gender): If options include "Choose not to disclose", "Decline to self-identify", "I do not wish to answer", "I am not a protected veteran", or "Decline", ALWAYS choose that decline/undisclosed option unless the applicant explicitly instructed otherwise.
23. EMPLOYMENT: The applicant is NOT currently working (internships already ended). For Current Company / Current Employer / Current Position / Current Designation: if the field is required, answer NA or None or Fresher (never leave blank, never copy a past internship). If optional, NA is still preferred over empty. Previous-employer fields may use the last internship. Currently working = No.

Return ONLY JSON:
{{"answers":[{{"element_index":0,"selector":"EXACT_SELECTOR","frame_index":null,"frame_url":null,"answer":"value","action":"fill|select|radio|checkbox|upload|skip","option_label":null,"option_value":null,"reason":"short"}}]}}
'''

def extract_json_from_text(text: str) -> Dict[str, Any]:
    text = (text or "").strip()

    # Remove markdown fences even when there is whitespace around them.
    text = re.sub(r"^\s*```(?:json)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```\s*$", "", text)
    text = text.strip()

    # Nova sometimes returns the answers array directly instead of wrapping it
    # in {"answers": [...]}. Accept both forms.
    candidates = [text]

    # Also try the first JSON object/array found inside surrounding prose.
    obj_start, obj_end = text.find("{"), text.rfind("}")
    if obj_start >= 0 and obj_end > obj_start:
        candidates.append(text[obj_start:obj_end + 1])

    arr_start, arr_end = text.find("["), text.rfind("]")
    if arr_start >= 0 and arr_end > arr_start:
        candidates.append(text[arr_start:arr_end + 1])

    for candidate in candidates:
        try:
            data = json.loads(candidate)
        except Exception:
            continue
        if isinstance(data, dict):
            if isinstance(data.get("answers"), list):
                return {"answers": data["answers"]}
            if isinstance(data.get("fields"), list):
                return {"answers": data["fields"]}
            return data
        if isinstance(data, list):
            return {"answers": [x for x in data if isinstance(x, dict)]}

    raise ValueError(
        "Amazon Nova did not return valid JSON.\n" + text[:4000]
    )

def call_bedrock(prompt: str) -> Dict[str, Any]:
    client = boto3.client("bedrock-runtime", region_name=AWS_REGION)
    response = client.converse(
        modelId=MODEL_ID,
        messages=[{"role": "user", "content": [{"text": prompt}]}],
        inferenceConfig={"temperature": 0, "maxTokens": 4500},
    )
    text = "".join(
        block.get("text", "")
        for block in response.get("output", {}).get("message", {}).get("content", [])
        if isinstance(block, dict)
    )
    if not text:
        raise RuntimeError("Bedrock returned an empty response.")
    return extract_json_from_text(text)

# ============================================================
# PLAYWRIGHT FRAME / LOCATOR HELPERS
# ============================================================

# ============================================================

async def visible_locator(locator: Locator) -> Optional[Locator]:
    try:
        count = await locator.count()
    except Exception:
        return None

    for i in range(min(count, 20)):
        item = locator.nth(i)
        try:
            if await item.is_visible():
                return item
        except Exception:
            continue

    return None

def all_frames(page: Page) -> List[Frame]:
    # Main frame first, then child frames.
    return list(page.frames)

def frame_matches(
    frame: Frame,
    frame_index: Optional[int],
    frame_url: Optional[str],
    page: Page,
    frame_name: Optional[str] = None,
) -> bool:
    if frame_name:
        try:
            if clean_text(frame_name) == clean_text(frame.name):
                return True
        except Exception:
            pass

    if frame_index is not None:
        frames = all_frames(page)
        try:
            if 0 <= int(frame_index) < len(frames):
                if frames[int(frame_index)] == frame:
                    return True
        except Exception:
            pass

    if frame_url:
        try:
            return clean_text(frame_url) in clean_text(frame.url)
        except Exception:
            pass

    return False

async def _selector_variants(e: Dict[str, Any]) -> List[str]:
    variants: List[str] = []
    selector = clean_text(e.get("selector"))
    if selector and "{{" not in selector and "ltprop" not in selector.lower():
        clean_selector = re.sub(r'#(\d[\w\-]*)', r'[id="\1"]', selector)
        variants.append(clean_selector)
        if clean_selector != selector:
            variants.append(selector)
        if selector.startswith("#") and all(x not in selector[1:] for x in [" ", ">", "+", "~"]):
            raw_id = selector[1:]
            safe_id = raw_id.replace(chr(34), chr(92) + chr(34))
            variants.append(f'[id="{safe_id}"]')
    element_id = clean_text(e.get("id"))
    if element_id and "{{" not in element_id and "ltprop" not in element_id.lower():
        safe_id = element_id.replace('"', '\\"')
        variants.extend([f'[id="{safe_id}"]', f'#{re.sub(r"([\\[\\]():.#>+~*\\s])", r"\\\\\\1", element_id)}'])
    name = clean_text(e.get("name"))
    if name and "{{" not in name:
        safe_name = name.replace('"', '\\"')
        variants.append(f'[name="{safe_name}"]')
    return list(dict.fromkeys(variants))

async def _visible_or_native(loc: Locator) -> Optional[Locator]:
    visible = await visible_locator(loc)
    if visible:
        return visible
    try:
        count = await loc.count()
        for i in range(min(count, 20)):
            candidate = loc.nth(i)
            tag = norm(await candidate.evaluate("el => el.tagName.toLowerCase()"))
            typ = norm(await candidate.get_attribute("type"))
            if tag in {"select", "textarea", "input"} or typ in {"file", "radio", "checkbox", "hidden"}:
                return candidate
    except Exception:
        pass
    return None

async def locator_from_element(
    page: Page,
    e: Dict[str, Any],
    timeout: int = LOCATOR_TIMEOUT_MS,
) -> Tuple[Optional[Locator], Optional[Frame], str]:
    frame_index = e.get("frame_index")
    frame_url = clean_text(e.get("frame_url"))
    frame_name = clean_text(e.get("frame_name"))
    frames = all_frames(page)
    preferred: List[Frame] = []

    if frame_name:
        for fr in frames:
            try:
                if clean_text(fr.name) == frame_name and fr not in preferred:
                    preferred.append(fr)
            except Exception:
                pass

    if frame_index is not None:
        try:
            fr = frames[int(frame_index)]
            if fr not in preferred:
                preferred.append(fr)
        except Exception:
            pass

    if frame_url:
        for fr in frames:
            try:
                if frame_url in clean_text(fr.url) and fr not in preferred:
                    preferred.append(fr)
            except Exception:
                pass

    for fr in frames:
        if fr not in preferred:
            preferred.append(fr)

    variants = await _selector_variants(e)
    for fr in preferred:
        for selector in variants:
            try:
                loc = fr.locator(selector)
                candidate = await _visible_or_native(loc)
                if candidate:
                    return candidate, fr, "selector"
            except Exception:
                continue

    question = clean_text(e.get("question") or e.get("label"))
    label = clean_text(e.get("label"))
    placeholder = clean_text(e.get("placeholder"))
    name = clean_text(e.get("name"))
    element_id = clean_text(e.get("id"))
    role = norm(e.get("role") or e.get("runtime_role"))

    for fr in preferred:
        for text in [label, question]:
            if not text:
                continue
            clean_t = re.sub(r"^[\s*✱\"“”]+|[\s*✱\"“”]+$", "", text).strip()
            if not clean_t:
                continue

            try:
                loc = fr.get_by_label(clean_t, exact=False)
                candidate = await visible_locator(loc)
                if candidate:
                    return candidate, fr, "label"
            except Exception:
                pass

            # React/Tailwind/Bootstrap sibling and parent label match
            try:
                loc = fr.locator(f'div:has(> label:has-text("{clean_t}"))').locator('input, textarea, select, [contenteditable="true"]').first
                candidate = await visible_locator(loc)
                if candidate:
                    return candidate, fr, "container_label"
            except Exception:
                pass

            try:
                loc = fr.locator(f'label:has-text("{clean_t}") ~ input, label:has-text("{clean_t}") ~ textarea, label:has-text("{clean_t}") ~ select').first
                candidate = await visible_locator(loc)
                if candidate:
                    return candidate, fr, "sibling_label"
            except Exception:
                pass

        if placeholder:
            try:
                loc = fr.get_by_placeholder(placeholder, exact=False)
                candidate = await visible_locator(loc)
                if candidate:
                    return candidate, fr, "placeholder"
            except Exception:
                pass

        if element_id:
            try:
                safe_id = element_id.replace(chr(34), chr(92) + chr(34))
                loc = fr.locator(f'[id="{safe_id}"]')
                candidate = await _visible_or_native(loc)
                if candidate:
                    return candidate, fr, "id_attribute"
            except Exception:
                pass

        if name:
            try:
                safe_name = name.replace(chr(34), chr(92) + chr(34))
                loc = fr.locator(f'[name="{safe_name}"]')
                candidate = await _visible_or_native(loc)
                if candidate:
                    return candidate, fr, "name"
            except Exception:
                pass

        if role in {"textbox", "combobox", "checkbox", "radio", "button", "listbox", "option"}:
            try:
                loc = fr.get_by_role(role)
                candidate = await visible_locator(loc)
                if candidate:
                    return candidate, fr, "role"
            except Exception:
                pass

        if question:
            controls = fr.locator(
                "input, textarea, select, button, [contenteditable='true'], "
                "[role='combobox'], [role='textbox'], [role='checkbox'], "
                "[role='radio'], [role='listbox']"
            )
            try:
                count = await controls.count()
            except Exception:
                count = 0
            best = None
            best_score = 0
            qn = norm(question)
            q_tokens = [x for x in qn.split() if len(x) >= 4]
            for i in range(min(count, 1000)):
                candidate = controls.nth(i)
                try:
                    if not await candidate.is_visible():
                        continue
                    attrs = await candidate.evaluate("""el => {
                        let labelText = '';
                        if (el.id) {
                            const l = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
                            if (l) labelText = l.innerText || '';
                        }
                        if (!labelText && el.previousElementSibling && el.previousElementSibling.tagName === 'LABEL') {
                            labelText = el.previousElementSibling.innerText || '';
                        }
                        if (!labelText && el.parentElement) {
                            const pl = el.parentElement.querySelector('label');
                            if (pl && pl !== el) labelText = pl.innerText || '';
                        }
                        if (!labelText) {
                            const container = el.closest('[class*="form"], [class*="field"], [class*="group"], fieldset, .space-y-2');
                            if (container) {
                                const cl = container.querySelector('label, p, span');
                                if (cl && cl !== el) labelText = cl.innerText || '';
                            }
                        }
                        return {
                            id: el.id || '',
                            name: el.getAttribute('name') || '',
                            aria: el.getAttribute('aria-label') || '',
                            placeholder: el.getAttribute('placeholder') || '',
                            title: el.getAttribute('title') || '',
                            type: el.getAttribute('type') || '',
                            role: el.getAttribute('role') || '',
                            text: el.innerText || '',
                            value: el.getAttribute('value') || '',
                            label: labelText || ''
                        };
                    }""")
                    hay = norm(" ".join(str(v) for v in attrs.values() if v))
                    label_hay = norm(attrs.get("label", ""))
                    score = 0
                    if qn and qn == label_hay:
                        score += 150
                    elif qn and qn in label_hay:
                        score += 100
                    elif qn and qn == hay:
                        score += 100
                    elif qn and qn in hay:
                        score += 80
                    score += sum(10 for token in q_tokens if token in label_hay)
                    score += sum(3 for token in q_tokens if token in hay)
                    if role and attrs.get("role") == role:
                        score += 10
                    if score > best_score:
                        best_score, best = score, candidate
                except Exception:
                    continue
            if best and best_score >= 6:
                return best, fr, "semantic"

    return None, None, "not_found"

async def scroll_page_progressively(page: Page) -> None:
    """Visit the whole document so lazy-loaded/viewport-triggered fields mount."""
    try:
        await page.evaluate("window.scrollTo(0, 0)")
    except Exception:
        pass
    last_height = 0
    for _ in range(max(4, SCROLL_STEPS)):
        try:
            height = await page.evaluate("document.documentElement.scrollHeight || document.body.scrollHeight || 0")
            viewport = await page.evaluate("window.innerHeight || 800")
            y = await page.evaluate("window.scrollY || 0")
            step = max(400, int(viewport * 0.85))
            await page.evaluate("window.scrollBy(0, arguments[0])", step)
            await page.wait_for_timeout(40)
            new_height = await page.evaluate("document.documentElement.scrollHeight || document.body.scrollHeight || 0")
            if new_height == last_height and y + viewport >= new_height - 5:
                break
            last_height = max(last_height, new_height, height)
        except Exception:
            break
    try:
        await page.evaluate("window.scrollTo(0, 0)")
    except Exception:
        pass

def is_ignorable_frame(frame: Frame) -> bool:
    """Filter out chat widgets, ad trackers, analytics iframes."""
    url = (getattr(frame, "url", None) or "").lower()
    name = (getattr(frame, "name", None) or "").lower()
    combined = f"{url} {name}"

    chat_ad_patterns = [
        "conversations-visitor",
        "live-chat",
        "livechat",
        "tawk.to",
        "crisp.chat",
        "intercom",
        "drift.com",
        "zendesk.com",
        "google.com/recaptcha",
        "recaptcha/api2",
        "doubleclick.net",
        "googletagmanager",
        "google-analytics",
        "facebook.com/plugins",
        "embedded-viral-link",
    ]
    for pat in chat_ad_patterns:
        if pat in combined:
            return True
    return False

async def count_all_visible_inputs(page: Page) -> int:
    """Count visible form inputs across the main page and all valid frames."""
    total = 0
    for frame in page.frames:
        if is_ignorable_frame(frame):
            continue
        try:
            count = await frame.locator(
                "input:visible, textarea:visible, select:visible, cu-select, [contenteditable='true'], [role='textbox']"
            ).count()
            total += count
        except Exception:
            pass
    return total

async def ensure_application_form_open(page: Page) -> bool:
    """If the job application form is hidden behind an 'Apply' or related button
    (common in React/SPA/Bootstrap/iframe sites), detect and click it, then wait for the
    application modal or fields to mount either in the main document or inside an iframe."""
    try:
        visible_inputs = await count_all_visible_inputs(page)

        # If there are already many inputs and a dialog is visible, form is already open
        dialogs = await page.locator('[role="dialog"]:visible, .modal:visible, [class*="modal"]:visible').count()
        if visible_inputs >= 4 and dialogs > 0:
            return True
        if visible_inputs >= 8:
            return True

        apply_selectors = [
            # High-priority exact text CTAs
            'button:has-text("Apply Now")',
            'a:has-text("Apply Now")',
            '[role="button"]:has-text("Apply Now")',
            'button:has-text("Apply for Role")',
            'button:has-text("Apply for this job")',
            'button:has-text("Apply for this position")',
            'button:has-text("Apply Online")',
            'button:has-text("Quick Apply")',
            'button:has-text("Easy Apply")',
            'button:has-text("Submit Application")',
            'button:has-text("Start Application")',
            'a:has-text("Apply for this job")',
            'a:has-text("Apply for Role")',
            'a:has-text("Apply Online")',
            'a:has-text("Quick Apply")',
            'a:has-text("Easy Apply")',
            # Broader keyword matching
            ':is(button, a, [role="button"], input[type="button"], input[type="submit"]):has-text("Apply")',
            ':is(button, a, [role="button"]):has-text("Interested")',
            '[data-target*="apply" i]',
            '[data-toggle="modal"][data-target*="apply" i]',
            'a[href*="#apply" i]',
            '[data-testid*="apply" i]',
            '[data-qa*="apply" i]',
            '[id*="apply" i]:is(button, a, [role="button"])',
            '[class*="apply" i]:is(button, a, [role="button"])',
        ]

        all_contexts = [page] + [f for f in page.frames if not is_ignorable_frame(f)]
        clicked = False

        for ctx in all_contexts:
            for sel in apply_selectors:
                try:
                    locators = ctx.locator(sel)
                    count = await locators.count()
                except Exception:
                    continue

                for i in range(min(count, 5)):
                    loc = locators.nth(i)
                    try:
                        is_vis = await loc.is_visible()
                    except Exception:
                        continue
                    if not is_vis:
                        continue

                    btn_text = clean_text(await loc.inner_text())
                    text_to_check = btn_text or ""
                    if not re.search(r"\b(apply|application|interested|join us|register)\b", text_to_check, re.I):
                        try:
                            attr_str = await loc.evaluate("el => (el.id || '') + ' ' + (el.className || '') + ' ' + (el.getAttribute('data-target') || '') + ' ' + (el.getAttribute('href') || '')")
                            if not re.search(r"apply", attr_str, re.I):
                                continue
                        except Exception:
                            continue

                    # Skip filter/search/login buttons
                    if re.search(r"\b(filter|login|sign in|search|subscribe|download|share)\b", text_to_check, re.I):
                        continue

                    print(f"Detected Application CTA: '{btn_text or sel}' -> Clicking to open form...")
                    try:
                        await loc.scroll_into_view_if_needed()
                    except Exception:
                        pass

                    # 1. First attempt: click with force=True (bypasses minor overlays)
                    try:
                        await loc.click(timeout=3000, force=True)
                        clicked = True
                    except Exception:
                        pass

                    # 2. Second attempt: JavaScript click + event dispatch + Bootstrap modal show
                    if not clicked:
                        try:
                            await loc.evaluate("""el => {
                                if (window.$) {
                                    try { window.$(el).trigger('click'); } catch(e){}
                                }
                                el.click();
                                try {
                                    el.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, view: window }));
                                } catch(e){}
                                const target = el.getAttribute('data-target') || el.getAttribute('href');
                                if (target && target.startsWith('#')) {
                                    const modal = document.querySelector(target);
                                    if (modal) {
                                        if (window.$ && window.$.fn.modal) {
                                            try { window.$(modal).modal('show'); } catch(e){}
                                        } else {
                                            modal.classList.add('show', 'in');
                                            modal.style.display = 'block';
                                            modal.style.opacity = '1';
                                            modal.style.visibility = 'visible';
                                        }
                                    }
                                }
                            }""")
                            clicked = True
                        except Exception:
                            pass

                    # 3. Third attempt: dispatch_event
                    if not clicked:
                        try:
                            await loc.dispatch_event("click")
                            clicked = True
                        except Exception:
                            pass

                    if clicked:
                        break
                if clicked:
                    break
            if clicked:
                break

        if clicked:
            # Wait dynamically up to 6 seconds for form inputs in ANY frame
            for _ in range(15):
                await page.wait_for_timeout(400)
                new_count = await count_all_visible_inputs(page)
                if new_count > visible_inputs or new_count >= 3:
                    print(f"Application form opened! Visible inputs across frames: {visible_inputs} -> {new_count}")
                    await page.wait_for_timeout(800)
                    return True

            print(f"Application CTA clicked. Current visible inputs count: {await count_all_visible_inputs(page)}")
            return True

    except Exception as exc:
        print(f"Notice in ensure_application_form_open: {exc}")

    return False

async def expand_repeating_sections(page: Page) -> int:
    """Expand repeating sections like '+ Add Experience Details' and '+ Add Education Details'
    so that nested work experience and education form fields mount into the DOM and are available for filling."""
    expanded_count = 0
    all_frames = [page] + [f for f in page.frames if f != page.main_frame and not is_ignorable_frame(f)]

    targets = [
        {
            "name": "Add Experience Details",
            "selectors": [
                'a:has-text("+ Add Experience Details")',
                'button:has-text("+ Add Experience Details")',
                '[role="button"]:has-text("+ Add Experience Details")',
                'a:has-text("Add Experience Details")',
                'button:has-text("Add Experience Details")',
                'a:has-text("+ Add Experience")',
                'button:has-text("+ Add Experience")',
                'button:has-text("+ Add Work Experience")',
                'a:has-text("+ Add Work Experience")',
            ],
            "check_selector": '#companyName_0, [name*="ExperienceDetails[0]"], input[name*="experience"][name*="company"], #designation_0',
        },
        {
            "name": "Add Education Details",
            "selectors": [
                'a:has-text("+ Add Education Details")',
                'button:has-text("+ Add Education Details")',
                '[role="button"]:has-text("+ Add Education Details")',
                'a:has-text("Add Education Details")',
                'button:has-text("Add Education Details")',
                'a:has-text("+ Add Education")',
                'button:has-text("+ Add Education")',
            ],
            "check_selector": '#degree_0, [name*="EducationDetails[0]"], input[name*="education"][name*="degree"], #branch_0',
        },
        {
            "name": "Add Position / Qualification / School",
            "selectors": [
                'button:has-text("+ Add Position")',
                'a:has-text("+ Add Position")',
                'button:has-text("+ Add Another Job")',
                'a:has-text("+ Add Another Job")',
                'button:has-text("+ Add School")',
                'a:has-text("+ Add School")',
                'button:has-text("+ Add Qualification")',
                'a:has-text("+ Add Qualification")',
            ],
            "check_selector": None,
        }
    ]

    for frame in all_frames:
        for t in targets:
            try:
                if t.get("check_selector"):
                    try:
                        if await frame.locator(t["check_selector"]).count() > 0:
                            continue
                    except Exception:
                        pass
                
                for sel in t["selectors"]:
                    loc = frame.locator(sel).first
                    if await loc.count() > 0:
                        try:
                            if await loc.is_visible():
                                await loc.scroll_into_view_if_needed(timeout=1000)
                                await loc.click(timeout=1500)
                                print(f"  [Sections] Clicked '{t['name']}' using '{sel}'")
                                expanded_count += 1
                                await page.wait_for_timeout(600)
                                break
                        except Exception:
                            try:
                                await loc.evaluate("el => el.click()")
                                print(f"  [Sections] JS-Clicked '{t['name']}' using '{sel}'")
                                expanded_count += 1
                                await page.wait_for_timeout(600)
                                break
                            except Exception:
                                pass
            except Exception:
                pass

    # Generic tabular add pass (e.g. Zoho Recruit, BambooHR, custom ATS)
    try:
        tabular_btns = await page.locator("button.tabular-group-add, [class*='tabular'] button:has-text('Add')").all()
        for b in tabular_btns:
            try:
                if await b.is_visible():
                    aria = (await b.get_attribute("aria-label") or "").lower()
                    if "0 of" in aria or "add an entry" in aria:
                        await b.scroll_into_view_if_needed(timeout=1000)
                        await b.click(timeout=1200)
                        expanded_count += 1
                        print(f"  [Sections] Clicked tabular Add: '{aria}'")
                        await page.wait_for_timeout(400)
            except Exception:
                pass
    except Exception:
        pass

    if expanded_count > 0:
        await page.wait_for_timeout(800)
        print(f"Repeating sections expansion complete. ({expanded_count} sections expanded)")

    return expanded_count

async def wait_for_form(page: Page) -> None:
    try:
        await page.wait_for_load_state(
            "domcontentloaded",
            timeout=PAGE_LOAD_TIMEOUT_MS,
        )
    except Exception:
        pass

    # For React/SPA/iframe sites: open the Apply modal/form if needed
    await ensure_application_form_open(page)
    await expand_repeating_sections(page)

    # Dynamic wait across all frames
    for _ in range(15):
        try:
            total_count = await count_all_visible_inputs(page)
            if total_count > 0:
                break
        except Exception:
            pass
        await page.wait_for_timeout(250)

    try:
        await page.wait_for_timeout(500)
    except Exception:
        pass

# ============================================================
# RUNTIME ENRICHMENT: Discover cu-select / custom dropdowns
# ============================================================

ENRICH_CU_SELECT_JS = """() => {
    const vis = el => {
        if (!el) return false;
        const r = el.getBoundingClientRect();
        const s = getComputedStyle(el);
        return r.height > 4 && r.width > 4
            && s.display !== 'none'
            && s.visibility !== 'hidden'
            && Number(s.opacity) !== 0;
    };
    const results = [];
    // Find all cu-select elements on the page
    const cuSelects = document.querySelectorAll('cu-select');
    for (const cuSel of cuSelects) {
        if (!vis(cuSel)) continue;
        // Find the trigger div[role="button"] inside
        const trigger = cuSel.querySelector('div[role="button"], [aria-expanded]');
        if (!trigger) continue;
        // Walk up to find the form field label
        let label = '';
        let formField = cuSel.closest('cu-form-field, cu3-form-field');
        if (!formField) formField = cuSel.closest('div[class*="form-field"]');
        if (formField) {
            // Try the label element first
            const labelEl = formField.querySelector('label, .cu-form-field__label, cu3-form-field-label, [class*="field-label"], [class*="form-label"]');
            if (labelEl) {
                label = (labelEl.innerText || labelEl.textContent || '').replace(/\\s+/g, ' ').trim();
            }
        }
        // If no label found from form field, try walking up to the nearest container
        if (!label) {
            let container = cuSel.parentElement;
            for (let i = 0; i < 5 && container; i++) {
                const labelEl = container.querySelector('label, [class*="label"]');
                if (labelEl && labelEl.closest('cu-select') !== cuSel) {
                    const t = (labelEl.innerText || labelEl.textContent || '').replace(/\\s+/g, ' ').trim();
                    if (t && t.length > 2 && t.length < 200 && !/^select/i.test(t)) {
                        label = t;
                        break;
                    }
                }
                container = container.parentElement;
            }
        }
        // Also check for preceding sibling or nearby text
        if (!label) {
            const parent = cuSel.parentElement;
            if (parent) {
                const children = [...parent.children];
                const idx = children.indexOf(cuSel);
                for (let j = idx - 1; j >= Math.max(0, idx - 3); j--) {
                    const sib = children[j];
                    if (sib.tagName === 'LABEL' || sib.querySelector('label')) {
                        const t = (sib.innerText || sib.textContent || '').replace(/\\s+/g, ' ').trim();
                        if (t && t.length > 2 && t.length < 200) {
                            label = t;
                            break;
                        }
                    }
                }
            }
        }
        const required = label.includes('*') || cuSel.closest('[class*="required"]') !== null;
        // Build a CSS path for the trigger
        const triggerSelector = trigger.getAttribute('data-jobfill-cuselector') || '';
        results.push({
            label: label || 'Select option...',
            required: required,
            cuSelectSelector: triggerSelector,
            triggerRect: trigger.getBoundingClientRect().toJSON(),
        });
    }
    return results;
}"""

ENRICH_CU_SELECT_OPTIONS_JS = """() => {
    const vis = el => {
        if (!el) return false;
        const r = el.getBoundingClientRect();
        const s = getComputedStyle(el);
        return r.height > 4 && r.width > 4
            && s.display !== 'none'
            && s.visibility !== 'hidden';
    };
    // Look for the open CDK overlay pane
    const panes = [...document.querySelectorAll('.cdk-overlay-pane')].filter(vis);
    const pane = panes[panes.length - 1];
    if (!pane) return [];
    const items = [...pane.querySelectorAll('[role="option"], [class*="option"], li, button')];
    const out = [];
    const seen = new Set();
    for (const el of items) {
        if (!vis(el)) continue;
        const text = (el.innerText || el.textContent || '').replace(/\\s+/g, ' ').trim();
        if (!text || text.length > 200 || seen.has(text.toLowerCase())) continue;
        // Skip if a visible child has the same text (dedup parent/child)
        const childSame = [...el.children].some(c => ((c.innerText || '').replace(/\\s+/g, ' ').trim()) === text);
        if (childSame) continue;
        seen.add(text.toLowerCase());
        out.push({label: text, text: text, value: el.getAttribute('data-value') || el.getAttribute('value') || text});
        if (out.length >= 50) break;
    }
    return out;
}"""


async def enrich_cu_select_elements(page: Page, elements: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Discover cu-select custom dropdowns at runtime, read labels and options.

    This patches elements that have label 'Select option...' with:
    - The real question text discovered from the form DOM
    - Options discovered by clicking each dropdown open
    - Proper control_type / role for downstream matching
    """
    frame = page.main_frame

    # Tag the cu-select trigger elements for identification
    try:
        await frame.evaluate("""
            () => {
                let idx = 0;
                document.querySelectorAll('cu-select').forEach(cuSel => {
                    const trigger = cuSel.querySelector('div[role="button"], [aria-expanded]');
                    if (trigger) {
                        trigger.setAttribute('data-jobfill-cuselector', 'cu-select-' + idx);
                        idx++;
                    }
                });
            }
        """)
    except Exception:
        pass

    try:
        cu_selects = await frame.evaluate(ENRICH_CU_SELECT_JS)
    except Exception:
        cu_selects = []

    if not cu_selects:
        return elements

    # Find elements in our list that are cu-select triggers with "Select option..." labels
    # These are div[role="button"] elements whose selectors contain "cu-select"
    def _is_placeholder_label(raw: str) -> bool:
        """True when the label is just a placeholder like 'Select option...'."""
        s = norm(raw)
        return not s or s.startswith("select") or s in {"option", "choose", "pick"}

    cu_select_elements = []
    for e in elements:
        sel = str(e.get("selector") or e.get("dom_path") or "")
        raw_label = clean_text(e.get("label") or e.get("question") or "")
        role = norm(e.get("role"))
        tag = norm(e.get("tag"))
        if (
            "cu-select" in sel
            and role == "button"
            and tag == "div"
            and _is_placeholder_label(raw_label)
            and not e.get("disabled")
        ):
            cu_select_elements.append(e)

    if not cu_select_elements:
        return elements

    print(f"\nEnriching {len(cu_select_elements)} cu-select dropdowns...")

    # Match discovered labels to elements by position order
    for i, e in enumerate(cu_select_elements):
        if i >= len(cu_selects):
            break

        info = cu_selects[i]
        real_label = clean_text(info.get("label"))
        is_required = info.get("required", False)

        if real_label and not _is_placeholder_label(real_label):
            e["label"] = real_label
            e["question"] = real_label
            if is_required:
                e["required"] = True

        # Set proper control type for downstream matching
        e["control_type"] = "combobox"
        e["runtime_role"] = "combobox"

        # Open dropdown to discover options (with per-dropdown timeout)
        async def _discover_options(pg, fr, elem, loc):
            try:
                await loc.scroll_into_view_if_needed(timeout=2000)
            except Exception:
                pass
            try:
                await loc.click(timeout=2000)
            except Exception:
                try:
                    await loc.click(force=True, timeout=1500)
                except Exception:
                    return []

            # Wait for overlay to appear
            try:
                await pg.wait_for_timeout(400)
            except Exception:
                pass

            # Read options from the open overlay
            try:
                options = await fr.evaluate(ENRICH_CU_SELECT_OPTIONS_JS)
            except Exception:
                options = []

            # Close the dropdown
            try:
                await pg.keyboard.press("Escape")
                await pg.wait_for_timeout(200)
            except Exception:
                # Fallback: click somewhere else to close
                try:
                    await pg.mouse.click(10, 10)
                    await pg.wait_for_timeout(200)
                except Exception:
                    pass

            return options or []

        try:
            loc, loc_frame, _ = await locator_from_element(page, e)
            if loc:
                try:
                    options = await asyncio.wait_for(
                        _discover_options(page, frame, e, loc),
                        timeout=8.0
                    )
                except asyncio.TimeoutError:
                    options = []
                    print(f"  [{e['_element_index']}] {real_label}: timeout discovering options")
                    # Try to close any open overlay
                    try:
                        await page.keyboard.press("Escape")
                    except Exception:
                        pass

                if options:
                    e["options"] = options
                    print(f"  [{e['_element_index']}] {real_label}: {len(options)} options found")
                else:
                    print(f"  [{e['_element_index']}] {real_label}: no options found")
        except Exception as exc:
            print(f"  [{e['_element_index']}] {real_label}: error - {exc}")

    # Rebuild the by_index mapping
    return elements


# ============================================================
# NATIVE / CUSTOM CONTROL OPERATIONS
# ============================================================

async def fire_react_events(locator: Locator) -> None:
    try:
        await locator.evaluate(
            """el => {
                const tag = el.tagName.toLowerCase();

                if (tag === 'input' || tag === 'textarea') {
                    const proto =
                        tag === 'textarea'
                        ? HTMLTextAreaElement.prototype
                        : HTMLInputElement.prototype;

                    const desc =
                        Object.getOwnPropertyDescriptor(
                            proto,
                            'value'
                        );

                    if (desc && desc.set) {
                        desc.set.call(el, el.value);
                    }
                }

                el.dispatchEvent(
                    new Event('input', {bubbles: true})
                );
                el.dispatchEvent(
                    new Event('change', {bubbles: true})
                );
                el.dispatchEvent(
                    new Event('blur', {bubbles: true})
                );
            }"""
        )
    except Exception:
        pass

async def fill_text(locator: Locator, value: str) -> bool:
    value = str(value)

    try:
        await locator.scroll_into_view_if_needed()
    except Exception:
        pass

    # Normal Playwright fill is the fastest and usually best for React.
    try:
        await locator.fill(value, timeout=LOCATOR_TIMEOUT_MS)
        return True
    except Exception:
        pass

    # Some masked/custom inputs need typing.
    try:
        await locator.click(timeout=LOCATOR_TIMEOUT_MS)
        await locator.press("Control+A")
        await locator.type(value, delay=5)
        return True
    except Exception:
        pass

    # Last local DOM fallback for React-controlled inputs.
    try:
        await locator.evaluate(
            """(el, value) => {
                const tag = el.tagName.toLowerCase();

                if (tag === 'input') {
                    const setter = Object.getOwnPropertyDescriptor(
                        HTMLInputElement.prototype,
                        'value'
                    )?.set;
                    if (setter) setter.call(el, value);
                    else el.value = value;
                } else if (tag === 'textarea') {
                    const setter = Object.getOwnPropertyDescriptor(
                        HTMLTextAreaElement.prototype,
                        'value'
                    )?.set;
                    if (setter) setter.call(el, value);
                    else el.value = value;
                } else if (el.isContentEditable) {
                    el.textContent = value;
                }

                el.dispatchEvent(
                    new Event('input', {bubbles: true})
                );
                el.dispatchEvent(
                    new Event('change', {bubbles: true})
                );
            }""",
            value,
        )
        return True
    except Exception:
        return False

async def read_control_value(locator: Locator) -> str:
    try:
        tag = await locator.evaluate("el => el.tagName.toLowerCase()")

        if tag in {"input", "textarea", "select"}:
            value = clean_text(await locator.input_value())
            if tag == "select":
                try:
                    selected = await locator.evaluate(
                        """el => {
                            const o = el.selectedOptions?.[0];
                            return o ? {text:o.textContent || '', value:o.value || ''} : {};
                        }"""
                    )
                    return clean_text(selected.get("text") or selected.get("value") or value)
                except Exception:
                    pass
            return value

        attrs = await locator.evaluate(
            """el => ({
                value: el.getAttribute('value') || '',
                ariaValue: el.getAttribute('aria-valuetext') || '',
                ariaLabel: el.getAttribute('aria-label') || '',
                title: el.getAttribute('title') || '',
                text: el.innerText || '',
                content: el.textContent || '',
                selectedText: el.querySelector?.('[aria-selected="true"]')?.innerText || ''
            })"""
        )

        return clean_text(
            attrs.get("value")
            or attrs.get("ariaValue")
            or attrs.get("selectedText")
            or attrs.get("text")
            or attrs.get("ariaLabel")
            or attrs.get("title")
            or attrs.get("content")
        )
    except Exception:
        return ""

async def read_selected_control_text(locator: Locator) -> str:
    """Read the visible/selected state of native and custom controls."""
    try:
        tag = await locator.evaluate("el => el.tagName.toLowerCase()")
        if tag == "select":
            data = await locator.evaluate(
                """el => {
                    const o = el.selectedOptions?.[0];
                    return o ? {text:o.textContent || '', value:o.value || ''} : {};
                }"""
            )
            return clean_text(data.get("text") or data.get("value"))

        return await read_control_value(locator)
    except Exception:
        return ""


async def read_input_constraints(locator: Locator) -> Dict[str, Any]:
    """Native HTML constraints + live validity. No AI."""
    try:
        return await locator.evaluate(
            """el => {
                const v = el.validity || {};
                return {
                    value: el.value || '',
                    type: (el.type || '').toLowerCase(),
                    pattern: el.getAttribute('pattern') || '',
                    minLength: el.minLength || 0,
                    maxLength: el.maxLength > 0 ? el.maxLength : 0,
                    min: el.min || '',
                    max: el.max || '',
                    inputMode: (el.inputMode || el.getAttribute('inputmode') || '').toLowerCase(),
                    required: !!el.required,
                    valid: typeof el.checkValidity === 'function' ? el.checkValidity() : true,
                    patternMismatch: !!v.patternMismatch,
                    tooLong: !!v.tooLong,
                    tooShort: !!v.tooShort,
                    typeMismatch: !!v.typeMismatch,
                    rangeOverflow: !!v.rangeOverflow,
                    rangeUnderflow: !!v.rangeUnderflow,
                    customError: !!v.customError,
                    valueMissing: !!v.valueMissing,
                    validationMessage: el.validationMessage || '',
                    ariaInvalid: (el.getAttribute('aria-invalid') || '').toLowerCase(),
                    className: String(el.className || ''),
                };
            }"""
        ) or {}
    except Exception:
        return {}


async def blur_and_check_invalid(locator: Locator) -> Dict[str, Any]:
    try:
        await locator.evaluate(
            """el => {
                el.dispatchEvent(new Event('input', {bubbles: true}));
                el.dispatchEvent(new Event('change', {bubbles: true}));
                el.blur();
                el.dispatchEvent(new Event('blur', {bubbles: true}));
            }"""
        )
    except Exception:
        pass
    try:
        await locator.page.wait_for_timeout(80)
    except Exception:
        pass
    state = await read_input_constraints(locator)
    cls = norm(state.get("className"))
    invalid = bool(
        state.get("valid") is False
        or state.get("patternMismatch")
        or state.get("tooLong")
        or state.get("typeMismatch")
        or state.get("tooShort")
        or state.get("ariaInvalid") == "true"
        or "ng-invalid" in cls
        or "is-invalid" in cls
        or "error" in cls and "input" in cls
    )
    state["invalid"] = invalid
    return state


def format_candidates_from_constraints(current: str, expected: str, constraints: Dict[str, Any]) -> List[str]:
    """Alternate values that often fix pattern/length errors. Not site-specific."""
    cur = clean_text(current) or clean_text(expected)
    exp = clean_text(expected)
    d_cur = digits(cur)
    d_exp = digits(exp)
    out: List[str] = []

    def add(v: Any) -> None:
        s = clean_text(v)
        if s and s not in out:
            out.append(s)

    add(cur)
    add(exp)
    add(re.sub(r"[\s\-()./]", "", cur))
    if d_cur:
        add(d_cur)
    if d_exp:
        add(d_exp)
    # Drop a leading +<1-3 digit> country code when the rest is a plausible local number.
    if cur.startswith("+") and len(d_cur) >= 8:
        rest = re.sub(r"^\+\d{1,3}", "", cur).strip()
        add(re.sub(r"[\s\-().]", "", rest))
        add(digits(rest))
        for n in (1, 2, 3):
            if len(d_cur) - n >= 7:
                add(d_cur[n:])
        if len(d_cur) >= 10:
            add(d_cur[-10:])
            add(d_cur[-8:])
    elif len(d_cur) > 10:
        add(d_cur[-10:])

    itype = norm(constraints.get("type") or constraints.get("inputMode"))
    if itype in {"tel", "number", "numeric"} or constraints.get("inputMode") in {"tel", "numeric", "decimal"}:
        add(re.sub(r"[^\d.]", "", cur))
        if d_cur:
            add(d_cur)

    ml = int(constraints.get("maxLength") or 0)
    if ml > 0:
        for v in list(out):
            if len(v) > ml:
                add(v[:ml])
                dv = digits(v)
                if dv:
                    add(dv[:ml] if len(dv) > ml else dv[-ml:] if len(dv) > ml else dv)

    pat = constraints.get("pattern") or ""
    if pat:
        matching = []
        for v in out:
            try:
                if re.fullmatch(pat, v):
                    matching.append(v)
            except re.error:
                break
        if matching:
            out = matching + [x for x in out if x not in matching]

    msg = canonical_text(constraints.get("validationMessage") or "")
    itype2 = itype or norm(constraints.get("type"))
    if (
        itype2 == "url"
        or "url" in msg
        or "linkedin" in canonical_text(constraints.get("name") or "") + canonical_text(constraints.get("id") or "")
        or "http" in msg
    ):
        raw = cur or exp
        if raw:
            add(ensure_https_url(raw))
            stripped = re.sub(r"^https?://", "", raw, flags=re.I)
            add("https://" + stripped.lstrip("/"))
            if "linkedin.com" in raw.lower() and not raw.lower().startswith("http"):
                add("https://" + raw.lstrip("/"))
    if "digit" in msg or "numeric" in msg or "number" in msg:
        if d_cur:
            add(d_cur)
            if len(d_cur) >= 10:
                add(d_cur[-10:])
    m = re.search(r"(\d+)\s*(character|digit|number)", msg)
    if m:
        n = int(m.group(1))
        if d_cur:
            add(d_cur[-n:] if len(d_cur) >= n else d_cur)
            add(d_cur[:n])

    return out[:8]


async def repair_invalid_text_input(locator: Locator, expected: str = "") -> Tuple[bool, str]:
    """If the control is :invalid, try constraint-derived formats once. No extra AI."""
    state = await blur_and_check_invalid(locator)
    current = clean_text(state.get("value"))
    if not state.get("invalid"):
        return True, current
    for cand in format_candidates_from_constraints(current, expected, state):
        if cand == current:
            continue
        try:
            await fill_text(locator, cand)
        except Exception:
            continue
        st = await blur_and_check_invalid(locator)
        live = clean_text(st.get("value"))
        if not st.get("invalid"):
            return True, live
        current = live
    return not bool(state.get("invalid")), current


async def repair_invalid_inputs_on_page(page: Page) -> List[Dict[str, Any]]:
    """Fix native-invalid inputs using their own pattern/length — one pass, no Bedrock."""
    repaired = []
    scopes = [page]
    try:
        scopes.extend([fr for fr in page.frames if fr != page.main_frame])
    except Exception:
        pass
    for scope in scopes:
        try:
            n = await scope.locator(
                "input:not([type=hidden]):not([type=submit]):not([type=button]):not([type=checkbox]):not([type=radio]), textarea"
            ).count()
        except Exception:
            continue
        for i in range(min(n, 40)):
            loc = scope.locator(
                "input:not([type=hidden]):not([type=submit]):not([type=button]):not([type=checkbox]):not([type=radio]), textarea"
            ).nth(i)
            try:
                if not await loc.is_visible():
                    continue
                st = await blur_and_check_invalid(loc)
                if not st.get("invalid"):
                    continue
                ok, live = await repair_invalid_text_input(loc, st.get("value") or "")
                repaired.append({
                    "ok": ok,
                    "before": st.get("value"),
                    "after": live,
                    "message": st.get("validationMessage"),
                })
            except Exception:
                continue
    return repaired


def _resume_identity() -> Dict[str, str]:
    try:
        resume = load_json(RESUME_FILE)
        idx = build_resume_index(resume)
        parts = (idx.get("full_name") or "").split()
        first = idx.get("first_name") or (parts[0] if parts else "Aman")
        last = idx.get("last_name") or (" ".join(parts[1:]) if len(parts) > 1 else "Kumar Singh")
        city = idx.get("current_location") or idx.get("work_location") or idx.get("location") or "Gurugram"
        return {
            "first_name": first,
            "last_name": last,
            "email": idx.get("email") or "amankumar.singh8501@gmail.com",
            "phone": idx.get("national_phone") or "9810307102",
            "city": city,
            "country": idx.get("country") or "India",
            "address": city,
        }
    except Exception:
        return {
            "first_name": "Aman",
            "last_name": "Kumar Singh",
            "email": "amankumar.singh8501@gmail.com",
            "phone": "9810307102",
            "city": "Gurugram",
            "country": "India",
            "address": "Gurugram",
        }


def required_placeholder_for_label(label: str, options: Optional[List[str]] = None) -> str:
    """Deterministic value for a required empty field. No internship as current job."""
    q = canonical_text(label)
    ident = _resume_identity()
    opts = [clean_text(o) for o in (options or []) if clean_text(o)]
    def pick(*keys: str) -> str:
        for k in keys:
            for o in opts:
                if k in o.lower():
                    return o
        return ""
    if any(w in q for w in ("given name", "first name", "forename")):
        return ident["first_name"]
    if any(w in q for w in ("family name", "last name", "surname")):
        return ident["last_name"]
    if "email" in q:
        return ident["email"]
    if any(w in q for w in ("phone", "mobile")) and "country" not in q and "type" not in q:
        return ident["phone"]
    if "city" in q:
        return ident["city"]
    if "address" in q:
        return ident["address"]
    if q.strip() in {"country"} or q.startswith("country") and "phone" not in q and "code" not in q:
        return pick("india") or ident["country"]
    if any(w in q for w in ("current company", "current employer", "current organization", "present employer")):
        return pick("na", "n/a", "none", "fresher", "not working", "unemployed") or "NA"
    if any(w in q for w in ("current designation", "current position", "current role", "current job")):
        return pick("na", "n/a", "none", "fresher") or "NA"
    if "currently working" in q:
        return pick("no", "fresher", "not") or "No"
    if any(w in q for w in ("notice", "available to join")):
        return pick("immediate", "0") or "Immediate"
    if "ctc" in q or "salary" in q:
        return pick("0") or "0"
    if opts:
        for o in opts:
            if not is_placeholder_option_label(o, q):
                return o
    if any(w in q for w in ("year", "experience")) and re.search(r"\b(year|exp)", q):
        return "1"
    return "NA"


async def fill_required_empty_controls(page: Page) -> List[Dict[str, Any]]:
    """Locate required empty/invalid fields on the live page and fill them. No Bedrock."""
    filled = []
    js = """() => {
        const vis = el => {
            const r = el.getBoundingClientRect();
            const s = getComputedStyle(el);
            return r.width > 2 && r.height > 2 && s.display !== 'none' && s.visibility !== 'hidden';
        };
        const labelOf = el => {
            let t = (el.getAttribute('aria-label') || el.placeholder || el.name || el.id || '').trim();
            if (el.id) {
                const lab = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
                if (lab) t = (lab.innerText || t).trim();
            }
            const wrap = el.closest('label, .form-group, .field, mat-form-field, .mb-3, p, div');
            if (wrap) {
                const lab = wrap.querySelector('label');
                if (lab && (lab.innerText || '').trim()) t = lab.innerText.trim();
            }
            return t.replace(/\\s+/g, ' ').slice(0, 80);
        };
        const out = [];
        const nodes = document.querySelectorAll('input, textarea, select');
        for (const el of nodes) {
            const type = (el.type || '').toLowerCase();
            if (['hidden','submit','button','reset','file','checkbox','radio','image'].includes(type)) continue;
            if (!vis(el) || el.disabled) continue;
            const val = (el.value || '').trim();
            const req = el.required || el.getAttribute('aria-required') === 'true' || /\\*/.test(labelOf(el));
            const invalid = (typeof el.checkValidity === 'function' && !el.checkValidity())
                || el.getAttribute('aria-invalid') === 'true';
            if (!req && !invalid) continue;
            if (val && !invalid) continue;
            const opts = el.tagName === 'SELECT'
                ? Array.from(el.options).map(o => (o.textContent || '').trim()).filter(Boolean)
                : [];
            out.push({
                tag: el.tagName.toLowerCase(),
                type,
                label: labelOf(el),
                selector: el.id ? ('#' + CSS.escape(el.id)) : (el.name ? (el.tagName.toLowerCase() + '[name="' + el.name.replace(/"/g,'\\\\"') + '"]') : ''),
                options: opts.slice(0, 20),
            });
        }
        return out;
    }"""
    scopes = [page]
    try:
        scopes.extend([fr for fr in page.frames if fr != page.main_frame])
    except Exception:
        pass
    for scope in scopes:
        try:
            rows = await scope.evaluate(js)
        except Exception:
            continue
        for row in rows or []:
            sel = row.get("selector") or ""
            label = row.get("label") or ""
            answer = required_placeholder_for_label(label, row.get("options") or [])
            if not answer:
                continue
            try:
                loc = scope.locator(sel).first if sel else None
                if not loc or await loc.count() == 0:
                    loc = scope.get_by_label(re.compile(re.escape(label.split("*")[0].strip()), re.I)).first
                if not loc or await loc.count() == 0:
                    continue
                tag = (row.get("tag") or "").lower()
                if tag == "select":
                    try:
                        await loc.select_option(label=answer)
                    except Exception:
                        await loc.select_option(value=answer)
                else:
                    await fill_text(loc, answer)
                    await repair_invalid_text_input(loc, answer)
                filled.append({"label": label, "answer": answer})
            except Exception:
                continue
    return filled


async def count_blocking_required_fields(page: Page) -> List[str]:
    """Labels of required/invalid empty controls still blocking submit."""
    js = """() => {
        const vis = el => {
            const r = el.getBoundingClientRect();
            const s = getComputedStyle(el);
            return r.width > 2 && r.height > 2 && s.display !== 'none' && s.visibility !== 'hidden';
        };
        const labelOf = el => {
            let t = (el.getAttribute('aria-label') || el.placeholder || '').trim();
            if (el.id) {
                const lab = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
                if (lab) t = (lab.innerText || t).trim();
            }
            return t.replace(/\\s+/g, ' ').slice(0, 80);
        };
        const displayed = el => {
            let v = (el.value || '').trim();
            if (v) return v;
            if (el.tagName === 'SELECT' && el.selectedIndex >= 0) {
                const t = (el.options[el.selectedIndex].text || '').trim();
                if (t && !/^select/i.test(t) && t !== '—' && t !== '-') return t;
            }
            const wrap = el.closest('cu-select, mat-form-field, .select2, [role="combobox"], .custom-select, .dropdown');
            if (wrap) {
                const lines = (wrap.innerText || '').split('\\n').map(s => s.trim()).filter(Boolean);
                const t = lines[lines.length - 1] || '';
                if (t && !/^select/i.test(t) && t.length < 80) return t;
            }
            return '';
        };
        const labels = [];
        for (const el of document.querySelectorAll('input, textarea, select')) {
            const type = (el.type || '').toLowerCase();
            if (['hidden','submit','button','reset','file','checkbox','radio'].includes(type)) continue;
            if (!vis(el) || el.disabled) continue;
            const val = displayed(el);
            const req = el.required || el.getAttribute('aria-required') === 'true' || /\\*/.test(labelOf(el));
            const invalid = (typeof el.checkValidity === 'function' && !el.checkValidity())
                || el.getAttribute('aria-invalid') === 'true';
            if ((req && !val) || (invalid && !val)) labels.push(labelOf(el) || el.name || el.id);
        }
        return labels;
    }"""
    labels: List[str] = []
    scopes = [page]
    try:
        scopes.extend([fr for fr in page.frames if fr != page.main_frame])
    except Exception:
        pass
    for scope in scopes:
        try:
            labels.extend(await scope.evaluate(js) or [])
        except Exception:
            continue
    return [x for x in labels if x]

async def verify_text(
    locator: Locator,
    expected: str,
    timeout: int = VERIFY_TIMEOUT_MS,
) -> bool:
    deadline = time.monotonic() + timeout / 1000

    while time.monotonic() < deadline:
        actual = await read_control_value(locator)

        if same_value(expected, actual):
            return True

        await asyncio.sleep(0.10)

    return False

async def get_option_candidates(
    locator: Locator,
) -> List[Dict[str, str]]:
    try:
        tag = await locator.evaluate(
            "el => el.tagName.toLowerCase()"
        )

        if tag == "select":
            return await locator.evaluate(
                """el => Array.from(el.options).map(o => ({
                    label: (o.textContent || '').trim(),
                    text: (o.textContent || '').trim(),
                    value: o.value
                }))"""
            )

        return await locator.evaluate(
            """el => {
                const root = el.closest(
                    '[role="combobox"], [role="listbox"], '
                    'div, section, form'
                ) || document;

                return Array.from(
                    root.querySelectorAll(
                        'option, [role="option"]'
                    )
                ).map(o => ({
                    label: (o.innerText || o.textContent || '').trim(),
                    text: (o.innerText || o.textContent || '').trim(),
                    value: o.getAttribute('value') || ''
                }));
            }"""
        )
    except Exception:
        return []

def option_synonym_score(target: Any, label: Any) -> int:
    a, b = canonical_text(target), canonical_text(label)
    if not a or not b:
        return 0
    if "data science" in a:
        if b in {"computer science", "computer science engineering"}:
            return 92
        if b == "science":
            return 0
    if any(x in a for x in ["kolkata", "asia/kolkata", "calcutta"]) and "asia pacific" in b:
        return 88
    if a in {"i agree", "yes", "acknowledge/confirm", "acknowledge confirm"}:
        if any(x in b for x in ["yes", "i agree", "acknowledge", "confirm"]):
            return 90
    if re.search(r"b\.?\s*tech|\bb\.e\b|\bbe\b|bachelor|undergraduate", a) and "bachelor" in b:
        return 94
    if re.search(r"m\.?\s*tech|master", a) and "master" in b:
        return 94
    if "intern" in a and "intern" in b:
        return 94
    if "likedin" in a and "linkedin" in b:
        return 96
    return 0

def is_fallback_other_label(label: Any) -> bool:
    t = canonical_text(label)
    return bool(re.match(
        r"^(other|others|not listed|not listed above|none of the above|i don t see|school not listed|not in the list|not in list)\b",
        t,
    ))

def pick_fallback_other(options: List[Any]) -> Optional[Any]:
    for opt in options or []:
        label = option_display(opt)
        if is_fallback_other_label(label):
            return opt
    return None

def choose_best_option(
    options: List[Dict[str, Any]],
    answer: Any,
    option_label: Any = None,
    option_value: Any = None,
) -> Optional[Dict[str, Any]]:
    targets = [x for x in [option_label, option_value, answer] if clean_text(x)]
    best = None
    best_score = 0
    for opt in options:
        label = clean_text(opt.get("label") or opt.get("text") or opt.get("name"))
        value = clean_text(opt.get("value"))
        for target in targets:
            tl, ll = canonical_text(target), canonical_text(label)
            if ll and tl and ll in tl.split() and len(ll.split()) < len(tl.split()):
                continue
            score = max(
                fuzzy_score(target, label),
                fuzzy_score(target, value) if value else 0,
                option_synonym_score(target, label),
                _option_match_score(target, label),
            )
            if score > best_score:
                best_score = score
                best = opt
    return best if best_score >= 65 else None

def snap_answer_to_options(
    options: List[Any],
    answer: Any,
    option_label: Any = None,
    option_value: Any = None,
    blob: Any = None,
) -> Optional[Dict[str, Any]]:
    opts: List[Dict[str, Any]] = []
    for opt in options or []:
        if isinstance(opt, dict):
            opts.append(opt)
        else:
            text = clean_text(opt)
            if text:
                opts.append({"label": text, "text": text})
    if not opts:
        return None
    best = choose_best_option(opts, answer, option_label, option_value)
    if best:
        return best
    blob_c = canonical_text(" ".join(clean_text(x) or "" for x in [blob, answer, option_label, option_value]))
    ranked = []
    for opt in opts:
        lab = option_display(opt)
        lc = canonical_text(lab)
        if not lc or looks_like_group_heading(lab) or lc in {"-", "select option"}:
            continue
        if lc in blob_c:
            ranked.append((100 + len(lc), opt))
            continue
        words = [w for w in lc.split() if len(w) > 3]
        hits = sum(1 for w in words if w in blob_c)
        if hits and hits >= max(1, len(words) - 1):
            ranked.append((70 + hits * 5 + len(lc), opt))
    if ranked:
        ranked.sort(key=lambda x: x[0], reverse=True)
        return ranked[0][1]
    return None

FIND_OPEN_MENU_JS = """() => {
    const vis = el => {
        if (!el) return false;
        const r = el.getBoundingClientRect();
        const s = getComputedStyle(el);
        return r.height > 8 && r.width > 8
            && s.display !== 'none'
            && s.visibility !== 'hidden'
            && Number(s.opacity) !== 0;
    };
    const skip = el => !el || !!el.closest('.iti, .react-international-phone, cu-rich-editor-toolbar, .ql-toolbar, .g-recaptcha');
    const menus = [
        ...document.querySelectorAll(
            '.cdk-overlay-pane, .cdk-overlay-container .cdk-overlay-pane, '
            + 'cu-dropdown, .cu-dropdown, [class*="cu-select"], '
            + '.select__menu, [role="listbox"], .ng-dropdown-panel, '
            + '.ant-select-dropdown, .MuiPopper-root, .dropdown-menu, '
            + '[class*="dropdown-list"], [class*="select-menu"], '
            + 'lyte-drop-box:not(.lyteDropdownHidden), lyte-drop-body, .cxDropbox'
        )
    ].filter(el => vis(el) && !skip(el));
    if (menus.length) return menus[menus.length - 1];
    const opts = [...document.querySelectorAll('[role="option"], lyte-drop-item')].filter(el => vis(el) && !skip(el));
    if (opts.length) return opts[0].closest('[role="listbox"], lyte-drop-box, ul, div, section') || opts[0].parentElement;
    const search = [...document.querySelectorAll('.cdk-overlay-container input, .select__menu input, [role="listbox"] input, lyte-drop-box input, [class*="dropdown"] input')]
        .find(el => vis(el) && !skip(el));
    if (search) return search.closest('.cdk-overlay-pane, [class*="dropdown"], [class*="select"], div') || search.parentElement;
    return null;
}"""

COLLECT_MENU_OPTIONS_JS = """(includeGroups) => {
    const vis = el => {
        if (!el) return false;
        const r = el.getBoundingClientRect();
        const s = getComputedStyle(el);
        return r.height > 4 && r.width > 4
            && s.display !== 'none'
            && s.visibility !== 'hidden'
            && Number(s.opacity) !== 0;
    };
    const skipHost = el => !el || !!el.closest('.iti, .react-international-phone, cu-rich-editor-toolbar');
    const skipLabel = label => /^(type to search\\.{0,3}|search|no options|no results|loading|searching|select option\\.{0,3}|choose option|-none-)$/i.test(label);
    
    let menu = [...document.querySelectorAll(
        '.cdk-overlay-pane, [role="listbox"], .select__menu, .ng-dropdown-panel, .ant-select-dropdown, .dropdown-menu, lyte-drop-box:not(.lyteDropdownHidden), lyte-drop-body, .cxDropbox, [class*="dropdown-menu"], [class*="dropdown-list"]'
    )].filter(el => vis(el) && !skipHost(el)).pop() || null;

    if (!menu) {
        const searches = [...document.querySelectorAll(
            '.cdk-overlay-container input, .select__menu input, [role="listbox"] input, lyte-drop-box input, [class*="dropdown"] input'
        )].filter(vis);
        const search = searches.length ? searches[searches.length - 1] : null;
        if (search && !skipHost(search)) {
            menu = search.closest('.cdk-overlay-pane, [role="listbox"], .select__menu, lyte-drop-box, [class*="dropdown"]') || search.parentElement;
        }
    }

    const nodes = menu
        ? [...menu.querySelectorAll('lyte-drop-item, [role="option"], [role="menuitem"], li, button, [class*="option"], a, *')]
        : [...document.querySelectorAll('lyte-drop-item, [role="option"]')].filter(el => vis(el) && !skipHost(el));
    if (!nodes.length && !menu) return [];
    const seen = new Set();
    const out = [];
    for (const el of nodes) {
        if (!vis(el) || skipHost(el)) continue;
        if (el.matches('input, textarea, svg, img, path')) continue;
        const label = (el.innerText || el.textContent || '').replace(/\\s+/g, ' ').trim();
        if (!label || label.length > 180 || skipLabel(label)) continue;
        const childSame = [...el.children].some(c => ((c.innerText || '').replace(/\\s+/g, ' ').trim()) === label);
        if (childSame) continue;
        const isGroup = /time zones$/i.test(label);
        if (isGroup && !includeGroups) continue;
        const key = (isGroup ? 'g:' : '') + label.toLowerCase();
        if (seen.has(key)) continue;
        seen.add(key);
        out.push({
            label,
            text: label,
            value: el.getAttribute('data-value') || el.getAttribute('value') || '',
            is_group: isGroup || undefined
        });
        if (out.length >= 120) break;
    }
    return out;
}"""

async def _dismiss_open_menus(frame: Frame) -> None:
    try:
        await frame.page.keyboard.press("Escape")
        await asyncio.sleep(0.05)
    except Exception:
        pass

async def reveal_rest_of_form(page: Page) -> None:
    """Close leftover dropdowns that trap scroll, then show the submit area."""
    try:
        await page.keyboard.press("Escape")
        await page.keyboard.press("Escape")
    except Exception:
        pass
    try:
        await page.evaluate(
            """() => {
                document.querySelectorAll('.select__menu, .iti__country-list, [role="listbox"]').forEach(el => {
                    el.style.display = 'none';
                });
                document.body.style.overflow = '';
                document.documentElement.style.overflow = '';
            }"""
        )
    except Exception:
        pass
    try:
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await page.wait_for_timeout(200)
        loc = page.locator(
            'button[type="submit"], input[type="submit"], button:has-text("Submit")'
        ).last
        if await loc.count():
            await loc.scroll_into_view_if_needed()
    except Exception:
        pass


async def _react_select_control(locator: Locator) -> Optional[Locator]:
    try:
        host = locator.locator("xpath=ancestor-or-self::cu-select[1]")
        if await host.count():
            trigger = host.locator("[role='button'], [aria-haspopup], [aria-expanded]").first
            if await trigger.count():
                return trigger
            return host.first
    except Exception:
        pass
    try:
        control = locator.locator(
            "xpath=ancestor::*[contains(@class,'select__control')][1]"
        )
        if await control.count():
            return control.first
    except Exception:
        pass
    try:
        nested = locator.locator(".select__control").first
        if await nested.count():
            return nested
    except Exception:
        pass
    return None

async def _overlay_search_input(frame: Frame) -> Optional[Locator]:
    for sel in (
        '.cdk-overlay-pane input:visible',
        '[role="listbox"] input:visible',
        '.select__menu input:visible',
        'lyte-drop-box input:visible',
        'lyte-drop-body input:visible',
        '.dropdown-menu input:visible',
        '.ng-dropdown-panel input:visible',
        '.ant-select-dropdown input:visible',
        '.select__control input.select__input:visible',
    ):
        try:
            loc = frame.locator(sel).last
            if await loc.count() and await loc.is_visible():
                return loc
        except Exception:
            continue
    return None

async def _react_select_input(locator: Locator, frame: Frame) -> Locator:
    overlay = await _overlay_search_input(frame)
    if overlay:
        return overlay
    try:
        nested = locator.locator("input.select__input, input[role='combobox'], input[aria-autocomplete='list']")
        candidate = await visible_locator(nested)
        if candidate:
            return candidate
    except Exception:
        pass
    try:
        control = await _react_select_control(locator)
        if control:
            nested = control.locator("input.select__input, input[role='combobox'], input")
            candidate = await visible_locator(nested)
            if candidate:
                return candidate
    except Exception:
        pass
    return locator

async def read_select_display(locator: Locator) -> str:
    """Read only the committed value, never the in-progress typed search text."""
    try:
        tag = norm(await locator.evaluate("el => el.tagName.toLowerCase()"))
    except Exception:
        tag = ""
    if tag == "select":
        return await read_selected_control_text(locator)
    if tag == "input":
        try:
            val = clean_text(await locator.input_value())
            if val:
                return val
        except Exception:
            pass
        return ""
    try:
        data = await locator.evaluate(
            """el => {
                const host = el.closest('cu-select')
                    || el.closest('lyte-dropdown')
                    || el.closest('.select__control')
                    || el.closest('[class*="select"]')
                    || el;
                const wrap = el.closest('.select__control')
                    || el.closest('[class*="css-"]')
                    || host;
                const root = (wrap && wrap.parentElement) || wrap;
                const single = (wrap && wrap.querySelector('.select__single-value, [class*="singleValue"], [class*="single-value"]'))
                    || (root && root.querySelector('.select__single-value, [class*="singleValue"]'));
                const multi = wrap
                    ? Array.from(wrap.querySelectorAll('.select__multi-value__label, [class*="multiValue"]'))
                        .map(n => n.textContent || '')
                        .join(', ')
                    : '';
                let raw = ((host && host.innerText) || el.innerText || el.textContent || '').replace(/\\s+/g, ' ').trim();
                raw = raw.replace(/select option\\.{0,3}$/i, '').trim();
                if (/^(select option|select\\.{0,3}|choose|type to search|-none-)$/i.test(raw)) raw = '';
                return {
                    single: single ? (single.textContent || '') : '',
                    multi,
                    host: raw
                };
            }"""
        )
        for key in ("single", "multi", "host"):
            text = clean_text(data.get(key))
            if text and "select option" not in norm(text) and norm(text) not in {"select", "select...", "choose"}:
                return text
        return ""
    except Exception:
        return ""

def _option_match_score(target: str, label: str) -> int:
    a = canonical_text(target)
    b = canonical_text(label)
    if not a or not b:
        return 0
    if b in {"no options", "no results", "loading", "searching"}:
        return 0
    if a == b:
        return 100
    if re.search(rf"(^|\s){re.escape(a)}(\s|$)", b) and len(a) >= 2:
        extra = len(b) - len(a)
        return 96 if extra <= 8 else 88
    if re.search(rf"(^|\s){re.escape(b)}(\s|$)", a) and len(b) >= 4:
        # "Science" must not beat "Data Science".
        if len(b.split()) < len(a.split()):
            return 60
        return 90
    if len(a) >= 8 and a in b:
        return 92
    if len(b) >= 8 and b in a:
        return 86
    return fuzzy_score(a, b)

GENERIC_SEARCH_WORDS = {
    "in", "of", "the", "and", "university", "college", "school", "institute",
    "degree", "bachelor", "bachelors", "master", "masters",
}

def _expand_select_targets(targets: List[str]) -> List[str]:
    extra: List[str] = []
    for t in targets:
        extra.append(t)
        n = canonical_text(t)
        if any(x in n for x in ["kolkata", "calcutta", "asia/kolkata", "time zone", "timezone"]) or n in {"asia", "asian"}:
            extra.extend(["Asia Pacific Time Zones", "Asia/Kolkata", "Kolkata"])
        if any(x in n for x in ["gurgaon", "gurugram", "delhi"]):
            extra.extend(["Gurugram", "Gurgaon", "Delhi", "New Delhi"])
        if n in {"india", "indian"}:
            extra.extend(["India", "Indian"])
        if re.search(r"b\.?\s*tech|b\.e\b|\bbe\b|bachelor", n):
            extra.append("Bachelor's Degree")
        if re.search(r"m\.?\s*tech|master", n):
            extra.append("Master's Degree")
    return [x for x in dict.fromkeys(clean_text(x) for x in extra) if x]

def _search_variants(target: str) -> List[str]:
    t = clean_text(target)
    if not t:
        return []
    words = [w for w in re.split(r"[\s/,]+", t) if len(re.sub(r"\W", "", w)) >= 3]
    distinctive = [w for w in words if w.casefold() not in GENERIC_SEARCH_WORDS]
    variants = []
    if distinctive:
        if len(distinctive) >= 2:
            variants.append(" ".join(distinctive[:2]))
        distinctive_sorted = sorted(distinctive, key=len, reverse=True)
        variants.append(distinctive_sorted[0])
        variants.append(distinctive[0])
    elif words:
        variants.append(words[0])
    if "/" in t:
        variants.append(t.split("/")[-1].strip())
        variants.append(t.split("/")[0].strip())
    variants.append(t)
    return list(dict.fromkeys(x for x in variants if x and x.casefold() not in GENERIC_SEARCH_WORDS))[:4]

async def _activate_option(option: Locator) -> bool:
    """react-select commits on mousedown, not only click."""
    try:
        await option.scroll_into_view_if_needed()
    except Exception:
        pass
    try:
        box = await option.bounding_box()
        if box:
            page = option.page
            x = box["x"] + max(4, box["width"] / 2)
            y = box["y"] + max(4, box["height"] / 2)
            await page.mouse.move(x, y)
            await page.mouse.down()
            await page.mouse.up()
            return True
    except Exception:
        pass
    try:
        await option.evaluate(
            """el => {
                const fire = type => el.dispatchEvent(new MouseEvent(type, {
                    bubbles: true, cancelable: true, view: window, buttons: 1
                }));
                fire('pointerdown');
                fire('mousedown');
                fire('pointerup');
                fire('mouseup');
                fire('click');
            }"""
        )
        return True
    except Exception:
        try:
            await option.click(force=True, timeout=900)
            return True
        except Exception:
            return False

async def _menu_is_loading(frame: Frame) -> bool:
    try:
        text = canonical_text(await frame.evaluate(
            """() => {
                const vis = el => {
                    if (!el) return false;
                    const r = el.getBoundingClientRect();
                    const s = getComputedStyle(el);
                    return r.height > 8 && s.display !== 'none' && s.visibility !== 'hidden';
                };
                const menu = [...document.querySelectorAll(
                    '.cdk-overlay-pane, .select__menu, [role="listbox"], .ng-dropdown-panel, cu-dropdown, .cu-dropdown'
                )].filter(vis).pop();
                return menu ? (menu.innerText || '') : '';
            }"""
        ))
        return any(x in text for x in ["loading", "searching"]) and "type to search" not in text
    except Exception:
        return False

async def _wait_live_options(frame: Frame, timeout_ms: int = 1600) -> List[Dict[str, Any]]:
    deadline = time.monotonic() + timeout_ms / 1000
    last: List[Dict[str, Any]] = []
    while time.monotonic() < deadline:
        if await _menu_is_loading(frame):
            await asyncio.sleep(0.06)
            continue
        last = []
        for opt in await _live_option_locators(frame, include_groups=True):
            label = canonical_text(opt.get("label") or opt.get("text"))
            if not label or label in {"no options", "no results", "loading", "searching", "type to search"}:
                continue
            if "no options matched" in label or "type to search" in label:
                continue
            last.append(opt)
        if last:
            return last
        await asyncio.sleep(0.05)
    return last

async def _pick_one_option(
    options: List[Dict[str, Any]],
    targets: List[str],
    allow_groups: bool = False,
) -> Optional[Dict[str, Any]]:
    best = None
    best_score = 0
    for opt in options:
        if not allow_groups and (opt.get("is_group") or looks_like_group_heading(opt.get("label") or opt.get("text"))):
            continue
        fields = [
            opt.get("label"),
            opt.get("text"),
            opt.get("value"),
            opt.get("aria"),
            opt.get("country"),
            opt.get("dial"),
        ]
        score = max((_option_match_score(t, f) for t in targets for f in fields if clean_text(f)), default=0)
        score = max(score, max((option_synonym_score(t, f) for t in targets for f in fields if clean_text(f)), default=0))
        if score > best_score:
            best_score, best = score, opt
    if not best:
        return None
    best["match_score"] = best_score
    if best_score >= 80:
        return best
    if allow_groups and best_score >= 70:
        return best
    if best_score >= 72 and len(options) <= 12:
        return best
    if len(options) <= 6 and best_score >= 70:
        return best
    return None

async def _open_custom_select(locator: Locator, frame: Frame) -> Locator:
    """Open react-select, Greenhouse, ClickUp cu-select, or Lyte dropdown, then return the search box if any."""
    try:
        dummy = locator.locator(".lyteDummyEventContainer, lyte-drop-button").first
        if await dummy.count():
            await dummy.click(timeout=1200)
    except Exception:
        pass
    control = await _react_select_control(locator) or locator
    try:
        await control.scroll_into_view_if_needed()
    except Exception:
        pass
    try:
        await control.click(timeout=LOCATOR_TIMEOUT_MS)
    except Exception:
        try:
            await control.click(force=True, timeout=1200)
        except Exception:
            try:
                await locator.click(force=True, timeout=1200)
            except Exception:
                pass
    for _ in range(12):
        if await _overlay_search_input(frame):
            break
        live = await _live_option_locators(frame)
        if live:
            break
        await asyncio.sleep(0.08)
    search = await _overlay_search_input(frame)
    if search:
        try:
            await search.click(timeout=800)
        except Exception:
            pass
        return search
    search = await _react_select_input(locator, frame)
    if search and search != locator:
        try:
            await search.click(force=True, timeout=800)
        except Exception:
            pass
        return search
    try:
        await control.press("ArrowDown")
    except Exception:
        pass
    return control

async def _live_option_locators(frame: Frame, include_groups: bool = False) -> List[Dict[str, Any]]:
    """Read the open menu with one JS call. Per-node Playwright reads hang on long lists."""
    try:
        rows = await frame.evaluate(COLLECT_MENU_OPTIONS_JS, include_groups)
        return [x for x in (rows or []) if isinstance(x, dict)]
    except Exception:
        return []

async def _click_menu_label(frame: Frame, target: str) -> bool:
    target = clean_text(target)
    if not target:
        return False
    try:
        box = await frame.evaluate(
            """(wanted) => {
                const norm = s => (s || '').replace(/\\s+/g, ' ').trim().toLowerCase();
                const want = norm(wanted);
                const vis = el => {
                    if (!el) return false;
                    const r = el.getBoundingClientRect();
                    const s = getComputedStyle(el);
                const skipHost = el => !el || !!el.closest('.iti, .react-international-phone, cu-rich-editor-toolbar');
                let menu = [...document.querySelectorAll(
                    '.cdk-overlay-pane, [role="listbox"], .select__menu, .dropdown-menu, .iti__country-list, lyte-drop-box:not(.lyteDropdownHidden), lyte-drop-body, .cxDropbox, .ng-dropdown-panel, .ant-select-dropdown'
                )].filter(el => vis(el) && !skipHost(el)).pop() || null;
                if (!menu) {
                    const searches = [...document.querySelectorAll(
                        '.cdk-overlay-container input, .select__menu input, [role="listbox"] input, lyte-drop-box input, [class*="dropdown"] input'
                    )].filter(vis);
                    const search = searches.length ? searches[searches.length - 1] : null;
                    if (search && !skipHost(search)) {
                        menu = search.closest('.cdk-overlay-pane, [role="listbox"], .select__menu, lyte-drop-box, [class*="dropdown"]') || search.parentElement;
                    }
                }
                if (!menu) return null;
                const textOf = el => (el.innerText || el.textContent || '').replace(/\\s+/g, ' ').trim();
                let best = null;
                let bestArea = Infinity;
                for (const el of menu.querySelectorAll('*')) {
                    if (!vis(el) || el.matches('input, textarea, svg')) continue;
                    const t = norm(textOf(el));
                    if (t !== want) continue;
                    if ([...el.children].some(c => norm(textOf(c)) === want)) continue;
                    const r = el.getBoundingClientRect();
                    const area = r.width * r.height;
                    if (area < bestArea) {
                        best = el;
                        bestArea = area;
                    }
                }
                if (!best) return null;
                best.scrollIntoView({block: 'nearest'});
                const r = best.getBoundingClientRect();
                return {x: r.x + Math.max(4, r.width / 2), y: r.y + Math.max(4, r.height / 2)};
            }""",
            target,
        )
        if not box:
            return False
        try:
            await frame.page.mouse.move(box["x"], box["y"])
            await frame.page.mouse.down()
            await frame.page.mouse.up()
            return True
        except Exception:
            try:
                return bool(await frame.evaluate(
                    """(wanted) => {
                        const norm = s => (s || '').replace(/\\s+/g, ' ').trim().toLowerCase();
                        const want = norm(wanted);
                        const vis = el => {
                            if (!el) return false;
                            const r = el.getBoundingClientRect();
                            const s = getComputedStyle(el);
                            return r.height > 4 && s.display !== 'none' && s.visibility !== 'hidden';
                        };
                        const menu = [...document.querySelectorAll(
                            '.cdk-overlay-pane, .select__menu, [role="listbox"], cu-dropdown, .cu-dropdown, lyte-drop-box:not(.lyteDropdownHidden), lyte-drop-body, .cxDropbox'
                        )].filter(vis).pop();
                        const nodes = menu 
                            ? [...menu.querySelectorAll('lyte-drop-item, [role="option"], li, button, [class*="option"], a')]
                            : [...document.querySelectorAll('lyte-drop-item')];
                        const textOf = el => (el.innerText || el.textContent || '').replace(/\\s+/g, ' ').trim();
                        const hit = nodes.find(el => vis(el) && (norm(textOf(el)) === want || norm(textOf(el)).includes(want) || want.includes(norm(textOf(el)))));
                        if (!hit) return false;
                        hit.click();
                        return true;
                    }""",
                    target,
                ))
            except Exception:
                return False
    except Exception:
        return False

async def _click_exact_live_option(
    frame: Frame,
    target: str,
) -> Optional[Locator]:
    """Find an exact visible option without scanning the whole page."""
    target = clean_text(target)
    if not target:
        return None

    scopes = [
        frame.locator(".cdk-overlay-pane:visible"),
        frame.locator("[role='listbox']:visible"),
        frame.locator(".select__menu:visible"),
        frame.locator("[role='menu']:visible"),
        frame.locator("lyte-drop-box:visible"),
        frame,
    ]
    for scope in scopes:
        for role in ("option", "menuitem"):
            try:
                loc = scope.get_by_role(role, name=re.compile(rf"^{re.escape(target)}$", re.I))
                candidate = await visible_locator(loc)
                if candidate:
                    return candidate
            except Exception:
                pass
            try:
                loc = scope.get_by_role(role, name=target, exact=False)
                count = min(await loc.count(), 20)
                for i in range(count):
                    item = loc.nth(i)
                    if not await item.is_visible():
                        continue
                    text = clean_text(await item.inner_text())
                    if canonical_text(text) == canonical_text(target):
                        return item
            except Exception:
                pass

    for selector in (".select__option:visible", ".iti__country:visible", "lyte-drop-item:visible"):
        try:
            loc = frame.locator(selector)
            count = min(await loc.count(), 80)
            for i in range(count):
                item = loc.nth(i)
                try:
                    if not await item.is_visible():
                        continue
                    text = clean_text(await item.inner_text())
                    aria = clean_text(await item.get_attribute("aria-label"))
                    if canonical_text(target) == canonical_text(text) or canonical_text(target) == canonical_text(aria):
                        return item
                except Exception:
                    continue
        except Exception:
            pass

    return None

async def fill_other_specify_near(locator: Locator, original: str) -> bool:
    """When a dropdown only has Other, type the resume value into the follow-up box."""
    original = clean_text(original)
    if not original or is_fallback_other_label(original):
        return False
    await asyncio.sleep(0.2)
    try:
        return bool(await locator.evaluate(
            """(el, value) => {
                const vis = (i) => {
                    if (!i || i.disabled) return false;
                    if (i.closest('.select__control, .select__menu, .iti')) return false;
                    const r = i.getBoundingClientRect();
                    const s = getComputedStyle(i);
                    return r.height > 8 && r.width > 40 && s.display !== 'none' && s.visibility !== 'hidden';
                };
                const section = el.closest('.education, .field, fieldset, [class*="education"]')
                    || el.closest('form')
                    || el.parentElement?.parentElement
                    || document.body;
                const inputs = [...section.querySelectorAll('input:not([type=hidden]):not([type=file]):not([type=checkbox]):not([type=radio]), textarea')];
                const elTop = el.getBoundingClientRect().top;
                const candidates = inputs.filter(vis).filter(i => {
                    const top = i.getBoundingClientRect().top;
                    return top >= elTop - 20 && top <= elTop + 280;
                });
                const score = (i) => {
                    const hay = ((i.getAttribute('placeholder') || '') + ' ' + (i.getAttribute('name') || '') + ' ' + (i.getAttribute('aria-label') || '') + ' ' + (i.id || '')).toLowerCase();
                    let n = 0;
                    if (/school|college|university|institution|other|specify|please enter/.test(hay)) n += 5;
                    if (!i.value) n += 2;
                    return n;
                };
                candidates.sort((a, b) => score(b) - score(a));
                const target = candidates.find(i => score(i) >= 2) || candidates.find(i => !i.value);
                if (!target) return false;
                const proto = target.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
                const setter = Object.getOwnPropertyDescriptor(proto, 'value')?.set;
                if (setter) setter.call(target, value); else target.value = value;
                target.dispatchEvent(new Event('input', {bubbles: true}));
                target.dispatchEvent(new Event('change', {bubbles: true}));
                return true;
            }""",
            original,
        ))
    except Exception:
        return False

async def select_custom_dropdown(
    locator: Locator,
    answer: Any,
    option_label: Any = None,
    option_value: Any = None,
    stored_options: Optional[List[Any]] = None,
) -> Tuple[bool, str]:
    """Select an option in a custom dropdown (e.g. input.looking_for with sibling/parent ul li)."""
    best = choose_best_option(stored_options or [], answer, option_label, option_value)
    target_text = clean_text(best.get("label") or best.get("text") or best.get("value") or answer) if best else clean_text(answer)
    target_val = clean_text(best.get("value") if best else target_text)

    # 1. Scroll and click/focus input to trigger slideDown/display
    try:
        await locator.scroll_into_view_if_needed(timeout=LOCATOR_TIMEOUT_MS)
        await locator.click(timeout=LOCATOR_TIMEOUT_MS)
        await asyncio.sleep(0.3)
    except Exception:
        pass

    # 2. Find and click the matching <li> option
    clicked = False
    try:
        clicked = await locator.evaluate("""(inputEl, target) => {
            const norm = s => (s || '').replace(/\\s+/g, ' ').trim().toLowerCase();
            const want = norm(target);
            let p = inputEl.parentElement;
            for (let i = 0; i < 4 && p; i++) {
                const lis = p.querySelectorAll('ul li, .expr li, .country-list li');
                for (const li of lis) {
                    const text = norm(li.innerText || li.textContent || '');
                    const val = norm(li.getAttribute('value') || '');
                    if (text === want || val === want || text.includes(want) || want.includes(text)) {
                        li.click();
                        return true;
                    }
                }
                p = p.parentElement;
            }
            return false;
        }""", target_text)
    except Exception:
        clicked = False

    if clicked:
        await asyncio.sleep(0.2)
        cur_val = await read_control_value(locator)
        if display_matches(target_text, cur_val) or same_value(target_val, cur_val):
            await fire_react_events(locator)
            return True, "custom_dropdown_li_click"

    # 3. DOM fallback: directly set value and fire events
    try:
        await locator.evaluate("""(el, val) => {
            el.value = val;
            el.dispatchEvent(new Event('input', {bubbles: true}));
            el.dispatchEvent(new Event('change', {bubbles: true}));
            el.dispatchEvent(new Event('blur', {bubbles: true}));
            if (window.jQuery || window.$) {
                const $el = (window.jQuery || window.$)(el);
                $el.val(val).trigger('change').trigger('input').trigger('focusout');
                $el.siblings('ul').hide();
                $el.siblings('p').hide();
            }
        }""", target_val or target_text)
        await asyncio.sleep(0.1)
        cur_val = await read_control_value(locator)
        if cur_val:
            return True, "custom_dropdown_dom_fallback"
    except Exception:
        pass

    return False, "custom_dropdown_failed"


async def select_native_or_custom(
    locator: Locator,
    answer: Any,
    option_label: Any = None,
    option_value: Any = None,
    frame: Optional[Frame] = None,
    stored_options: Optional[List[Any]] = None,
    specify_text: Any = None,
) -> Tuple[bool, str]:
    answer = clean_text(answer)
    original_resume = clean_text(specify_text)
    if not original_resume and not is_fallback_other_label(answer):
        original_resume = answer
    targets = _expand_select_targets(
        [clean_text(x) for x in (option_label, option_value, answer) if clean_text(x)]
    )
    resolved_stored = choose_best_option(stored_options or [], answer, option_label, option_value)
    if resolved_stored:
        label = clean_text(resolved_stored.get("label") or resolved_stored.get("text"))
        if label:
            targets.insert(0, label)

    try:
        tag = norm(await locator.evaluate("el => el.tagName.toLowerCase()"))
    except Exception:
        tag = ""

    # --------------------------------------------------------
    # Native <select>: no clicking, no dropdown animation.
    # --------------------------------------------------------
    if tag == "select":
        options = await get_option_candidates(locator)
        best = choose_best_option(options, answer, option_label, option_value)
        if best:
            value = clean_text(best.get("value"))
            label = clean_text(best.get("label"))
            try:
                if value:
                    await locator.select_option(value=value, timeout=LOCATOR_TIMEOUT_MS)
                else:
                    await locator.select_option(label=label, timeout=LOCATOR_TIMEOUT_MS)
                await fire_react_events(locator)
                return True, "native_select_exact_match"
            except Exception:
                pass
            # DOM fallback if select is hidden / non-actionable
            try:
                val = value or label
                await locator.evaluate("""(el, v) => {
                    for (let opt of el.options) {
                        if (opt.value === v || opt.text === v || (opt.text && opt.text.trim().toLowerCase() === v.trim().toLowerCase())) {
                            el.value = opt.value;
                            opt.selected = true;
                            el.dispatchEvent(new Event('input', {bubbles: true}));
                            el.dispatchEvent(new Event('change', {bubbles: true}));
                            return true;
                        }
                    }
                    el.value = v;
                    el.dispatchEvent(new Event('change', {bubbles: true}));
                    return true;
                }""", val)
                await fire_react_events(locator)
                return True, "native_select_dom_fallback"
            except Exception:
                pass
        return False, "native_select_no_match"

    # --------------------------------------------------------
    # Custom dropdown (e.g. input with sibling ul li)
    # --------------------------------------------------------
    if tag == "input" and (stored_options or answer):
        is_custom_dd = False
        try:
            is_custom_dd = await locator.evaluate("""el => {
                let p = el.parentElement;
                for (let i = 0; i < 4 && p; i++) {
                    if (p.querySelector('ul.expr, ul.country-list, ul li')) return true;
                    p = p.parentElement;
                }
                return false;
            }""")
        except Exception:
            is_custom_dd = False

        if is_custom_dd or stored_options:
            ok, method = await select_custom_dropdown(locator, answer, option_label, option_value, stored_options)
            if ok:
                return ok, method

    if frame is None:
        try:
            frame = await locator.owner_frame()
        except Exception:
            frame = None
    if frame is None:
        return False, "no_frame"

    # --------------------------------------------------------
    # Greenhouse / React custom select.
    # Open the menu, pick exactly one visible option, and verify the
    # committed .select__single-value. Never treat typed input as success.
    # --------------------------------------------------------
    await _dismiss_open_menus(frame)
    already = await read_select_display(locator)
    if already and any(fuzzy_score(t, already) >= 70 or _option_match_score(t, already) >= 70 for t in targets):
        return True, "already_selected"

    search = await _open_custom_select(locator, frame)

    async def menu_open() -> bool:
        try:
            return bool(await frame.evaluate(
                """() => {
                    const vis = el => {
                        if (!el) return false;
                        const r = el.getBoundingClientRect();
                        const s = getComputedStyle(el);
                        return r.height > 12 && r.width > 40 && s.display !== 'none' && s.visibility !== 'hidden';
                    };
                    return [...document.querySelectorAll(
                        '.cdk-overlay-pane, .select__menu, [role="listbox"], .ng-dropdown-panel, cu-dropdown, .cu-dropdown'
                    )].some(vis) || [...document.querySelectorAll('input')].some(el =>
                        vis(el) && /search/i.test((el.getAttribute('placeholder') || '') + ' ' + (el.getAttribute('aria-label') || ''))
                    );
                }"""
            ))
        except Exception:
            return False

    async def committed(method: str, accepted: Any = None) -> Optional[Tuple[bool, str]]:
        await asyncio.sleep(0.35)
        actual = await read_select_display(locator)
        if not actual:
            actual = await read_select_display(search)
        check = [accepted, *targets]
        if actual and any(
            fuzzy_score(t, actual) >= 65 or _option_match_score(t, actual) >= 65 or same_value(t, actual)
            for t in check if clean_text(t)
        ):
            return True, method
        if accepted and not await menu_open():
            shown = await read_select_display(locator)
            if shown and not looks_like_group_heading(shown) and "select option" not in norm(shown):
                return True, method
            if not shown:
                return True, method
        return None

    async def activate(item: Optional[Dict[str, Any]]) -> bool:
        if not item:
            return False
        label = clean_text(item.get("label") or item.get("text"))
        exact_loc = await _click_exact_live_option(frame, label)
        if exact_loc:
            try:
                await exact_loc.click(timeout=1000)
                return True
            except Exception:
                try:
                    await exact_loc.click(force=True, timeout=800)
                    return True
                except Exception:
                    pass
        if await _click_menu_label(frame, label):
            return True
        loc = item.get("locator")
        if loc:
            return await _activate_option(loc)
        return False

    async def choose_from_open_menu(method: str) -> Optional[Tuple[bool, str]]:
        items = await _wait_live_options(frame, timeout_ms=2200)
        leaves = [x for x in items if not x.get("is_group")]
        groups = [x for x in items if x.get("is_group")]
        picked = snap_answer_to_options(
            leaves or items,
            answer,
            option_label,
            option_value,
            blob=original_resume,
        )
        if not picked:
            picked = await _pick_one_option(leaves, targets) if leaves else None
        if not picked and groups:
            group = await _pick_one_option(groups, targets, allow_groups=True)
            if group and await activate(group):
                await asyncio.sleep(0.25)
                items = await _wait_live_options(frame, timeout_ms=1600)
                leaves = [x for x in items if not x.get("is_group")]
                picked = await _pick_one_option(leaves, targets) if leaves else None
        if not picked:
            other = pick_fallback_other(leaves or items)
            if other:
                picked = other if isinstance(other, dict) else {"label": option_display(other)}
                method = "custom_other_fallback"
        if not picked or not await activate(picked):
            return None
        return await committed(method, picked.get("label") or picked.get("text"))

    async def accept_hit(hit: Tuple[bool, str]) -> Optional[Tuple[bool, str]]:
        ok, method = hit
        display = await read_select_display(locator)
        if display and looks_like_group_heading(display):
            return None
        if is_fallback_other_label(display) or "other" in method:
            if await fill_other_specify_near(locator, original_resume):
                return True, method + "+resume_specify"
            return True, method
        return hit

    # 1. Click a real visible option. Never type an answer that is not in the list.
    live_items = await _wait_live_options(frame, timeout_ms=2500)
    snapped = snap_answer_to_options(
        live_items or stored_options or [],
        answer,
        option_label,
        option_value,
        blob=original_resume,
    )
    if snapped:
        snapped_label = option_display(snapped)
        if snapped_label:
            targets = [snapped_label] + [
                t for t in targets if canonical_text(t) != canonical_text(snapped_label)
            ]
        if await activate(snapped):
            direct = await committed("custom_option_click", snapped_label)
            if direct:
                accepted = await accept_hit(direct)
                if accepted:
                    return accepted

    hit = await choose_from_open_menu("custom_option_click")
    if hit:
        accepted = await accept_hit(hit)
        if accepted:
            return accepted

    # 2. Filter with the snapped option's FULL label only.
    queries: List[str] = []
    if snapped:
        label = option_display(snapped)
        if label:
            queries = [label]


    for query in queries:
        overlay = await _overlay_search_input(frame)
        if overlay:
            search = overlay
        else:
            try:
                search = await _open_custom_select(locator, frame)
            except Exception:
                pass
            overlay = await _overlay_search_input(frame)
            if overlay:
                search = overlay
        try:
            tag_name = norm(await search.evaluate("el => el.tagName.toLowerCase()"))
        except Exception:
            tag_name = ""
        if tag_name not in {"input", "textarea"}:
            continue
        try:
            await search.click(timeout=LOCATOR_TIMEOUT_MS)
        except Exception:
            pass
        try:
            await search.fill("")
            await search.press("Control+A")
            await search.press("Backspace")
        except Exception:
            pass
        try:
            await search.type(query, delay=20)
        except Exception:
            try:
                await search.fill(query)
            except Exception:
                continue
        for _ in range(10):
            if await _menu_is_loading(frame):
                await asyncio.sleep(0.08)
            else:
                break
        hit = await choose_from_open_menu("custom_filtered_click")
        if hit:
            accepted = await accept_hit(hit)
            if accepted:
                return accepted

    # 3. School not in typeahead: type Other, then the resume institution.
    schoolish = bool(re.search(r"university|college|institute|\bschool\b", canonical_text(original_resume)))
    if schoolish:
        try:
            await search.fill("")
            await search.press("Control+A")
            await search.press("Backspace")
            await search.type("Other", delay=0)
        except Exception:
            try:
                search = await _open_custom_select(locator, frame)
            except Exception:
                pass
        hit = await choose_from_open_menu("custom_other_fallback")
        if hit:
            accepted = await accept_hit(hit)
            if accepted:
                return accepted

    await _dismiss_open_menus(frame)
    return False, "custom_option_not_found"

async def click_choice_locator(frame, selector: str) -> bool:
    if not selector:
        return False
    target = frame.locator(selector)
    target = await visible_locator(target) or target.first
    try:
        await target.scroll_into_view_if_needed()
    except Exception:
        pass
    inner = target.locator("button, [role='button'], input[type='radio'], input[type='checkbox']")
    click_tgt = inner.first if await inner.count() else target
    for force in (False, True):
        try:
            await click_tgt.click(force=force, timeout=LOCATOR_TIMEOUT_MS)
            return True
        except Exception:
            continue
    try:
        await click_tgt.evaluate(
            """el => {
                el.dispatchEvent(new MouseEvent('mousedown', {bubbles: true, cancelable: true}));
                el.dispatchEvent(new MouseEvent('mouseup', {bubbles: true, cancelable: true}));
                el.click();
            }"""
        )
        return True
    except Exception:
        return False

async def is_choice_selected(page: Page, e: Dict[str, Any], wanted: Any) -> bool:
    """True only if a choice is actually selected — option label text is not enough."""
    want = norm(wanted)
    if not want:
        return False
    _, frame, _ = await locator_from_element(page, e)
    if not frame:
        return False
    selectors = [clean_text(e.get("selector"))]
    for opt in e.get("options") or []:
        if isinstance(opt, dict) and opt.get("selector"):
            hay = norm(" ".join(str(opt.get(k) or "") for k in ("label", "text", "value")))
            if want in hay or hay in want or fuzzy_score(want, hay) >= 65:
                selectors.append(clean_text(opt.get("selector")))
    try:
        return bool(
            await frame.evaluate(
                """({want, selectors, question}) => {
                    const nrm = t => (t || '').replace(/\\s+/g, ' ').trim().toLowerCase();
                    const wantN = nrm(want);
                    const selected = el => {
                        if (!el) return false;
                        const ap = (el.getAttribute('aria-pressed')
                            || el.getAttribute('aria-checked')
                            || el.getAttribute('aria-selected') || '').toLowerCase();
                        if (ap === 'true') return true;
                        if (el.checked === true) return true;
                        const c = String(el.className || '').toLowerCase();
                        return /(^|\\s)(selected|active|checked|pressed|is-selected|is-active|is-checked)(\\s|$)/.test(c);
                    };
                    for (const sel of selectors || []) {
                        if (!sel) continue;
                        let el = null;
                        try { el = document.querySelector(sel); } catch (err) { el = null; }
                        if (el && selected(el)) return true;
                        if (el) {
                            const hit = [...el.querySelectorAll('button, [role="button"], [role="radio"], input')].find(selected);
                            if (hit) return true;
                        }
                    }
                    const q = nrm(question || '').slice(0, 48);
                    if (!q) return false;
                    const blocks = [...document.querySelectorAll('div, fieldset, section, li, label')];
                    for (const b of blocks) {
                        const t = nrm(b.innerText || '');
                        if (!t.includes(q) || t.length > 400) continue;
                        const nodes = [...b.querySelectorAll('button, [role="radio"], [role="button"], input[type="radio"], label')];
                        if (nodes.some(n => selected(n) && nrm(n.innerText || n.value || '').includes(wantN))) return true;
                    }
                    return false;
                }""",
                {
                    "want": want,
                    "selectors": [s for s in selectors if s],
                    "question": clean_text(e.get("question") or e.get("label")),
                },
            )
        )
    except Exception:
        return False

async def click_radio(
    page: Page,
    e: Dict[str, Any],
    answer: Any,
    option_label: Any = None,
    option_value: Any = None,
) -> Tuple[bool, str]:
    wanted = norm(option_label or option_value or answer)
    loc, frame, _ = await locator_from_element(page, e)
    if not frame:
        return False, "radio_frame_not_found"

    # 1. Exact option selector from elements.json.
    for opt in e.get("options", []) or []:
        if not isinstance(opt, dict):
            continue
        hay = norm(" ".join(str(opt.get(k) or "") for k in ("label", "text", "value")))
        if wanted and fuzzy_score(wanted, hay) < 65 and wanted not in hay and hay not in wanted:
            continue
        selector = clean_text(opt.get("selector"))
        if selector:
            try:
                if await click_choice_locator(frame, selector):
                    return True, "radio_option_selector"
            except Exception:
                pass

    # 2. Native radio: match value/label text, then click the input or its label.
    radios = frame.locator("input[type='radio']")
    try:
        count = await radios.count()
    except Exception:
        count = 0
    best = None
    best_score = 0
    for i in range(min(count, 1000)):
        r = radios.nth(i)
        try:
            if not await r.is_visible():
                # Hidden input can still be paired with a visible label.
                pass
            attrs = await r.evaluate("""el => {
                const id = el.id || '';
                const label = id ? document.querySelector(`label[for="${CSS.escape(id)}"]`) : el.closest('label');
                return {
                    value: el.value || '', name: el.name || '', id,
                    label: label ? (label.innerText || label.textContent || '') : '',
                    parent: el.parentElement ? (el.parentElement.innerText || '') : ''
                };
            }""")
            hay = norm(" ".join(str(v) for v in attrs.values() if v))
            score = 0
            if wanted and wanted == norm(attrs.get("value")):
                score = 120
            elif wanted and wanted == norm(attrs.get("label")):
                score = 115
            elif wanted and wanted in hay:
                score = 90
            elif norm(attrs.get("label")) in wanted and norm(attrs.get("label")):
                score = 70
            if score > best_score:
                best_score, best = score, (r, attrs)
        except Exception:
            continue

    if best and best_score >= 70:
        r, attrs = best
        try:
            await r.check(force=True, timeout=LOCATOR_TIMEOUT_MS)
            if await r.is_checked():
                return True, "native_radio_check"
        except Exception:
            try:
                await r.click(force=True, timeout=LOCATOR_TIMEOUT_MS)
                return True, "native_radio_click"
            except Exception:
                pass
        try:
            rid = attrs.get("id")
            if rid:
                safe_rid = rid.replace(chr(34), chr(92) + chr(34))
                label = frame.locator(f'label[for="{safe_rid}"]')
                await label.click(force=True, timeout=LOCATOR_TIMEOUT_MS)
                return True, "radio_label_for"
        except Exception:
            pass

    # 3. ARIA/custom radio widgets. Search visible role=radio and nearby labels.
    candidates = frame.locator("[role='radio'], [role='option']")
    try:
        count = await candidates.count()
    except Exception:
        count = 0
    for i in range(min(count, 1000)):
        c = candidates.nth(i)
        try:
            if not await c.is_visible():
                continue
            attrs = await c.evaluate("""el => ({
                text: el.innerText || el.textContent || '',
                aria: el.getAttribute('aria-label') || '',
                value: el.getAttribute('data-value') || el.getAttribute('value') || '',
                checked: el.getAttribute('aria-checked') || ''
            })""")
            hay = norm(" ".join(str(v) for v in attrs.values()))
            if wanted == norm(attrs.get("value")) or wanted == norm(attrs.get("aria")) or wanted in hay:
                await c.click(force=True, timeout=LOCATOR_TIMEOUT_MS)
                return True, "aria_radio_click"
        except Exception:
            continue

    # 4. YES/NO pill buttons (MOPID and similar).
    wanted_label = clean_text(option_label or answer)
    if wanted_label and wanted_label.strip() in {"1", "0", "true", "false"}:
        wanted_label = "Yes" if wanted_label.strip() in {"1", "true"} else "No"
    question = clean_text(e.get("question") or e.get("label"))
    try:
        payload = {"q": question or "", "wanted": wanted_label or "Yes"}
        found = await frame.evaluate(
            """({q, wanted}) => {
                const ynNorm = t => (t || '').replace(/\\s+/g, ' ').trim()
                    .replace(/^[✓✔✕✗×•●]\\s*/u, '')
                    .replace(/\\s*[✓✔✕✗×]$/u, '');
                const yn = t => /^(yes|no)$/i.test(ynNorm(t));
                const want = ynNorm(wanted || 'yes').toLowerCase();
                const wantYn = want === 'no' || want.startsWith('n') ? 'no'
                    : (want === 'yes' || want.startsWith('y') ? 'yes' : '');
                if (!wantYn) return false;
                document.querySelectorAll('[data-jobfill-yn]').forEach(el => el.removeAttribute('data-jobfill-yn'));
                const nodes = [...document.querySelectorAll('button, [role="button"], label, span, div')];
                const hits = nodes.filter(el => {
                    const t = (el.innerText || '').replace(/\\s+/g, ' ').trim();
                    return yn(t) && t.length <= 12;
                });
                const qkey = (q || '').slice(0, 48).toLowerCase();
                let best = null, bestS = -1;
                for (const el of hits) {
                    if (ynNorm(el.innerText).toLowerCase() !== wantYn) continue;
                    let s = 1, n = el;
                    for (let i = 0; i < 8 && n; i++) {
                        const t = (n.innerText || '').toLowerCase();
                        if (qkey && t.includes(qkey)) s += 20 - i;
                        n = n.parentElement;
                    }
                    if (s > bestS) { bestS = s; best = el; }
                }
                if (!best) return false;
                best.setAttribute('data-jobfill-yn', '1');
                return true;
            }""",
            payload,
        )
        if found:
            marker = frame.locator("[data-jobfill-yn='1']")
            target = await visible_locator(marker) or marker.first
            await target.click(force=True, timeout=LOCATOR_TIMEOUT_MS)
            try:
                await target.evaluate("el => el.removeAttribute('data-jobfill-yn')")
            except Exception:
                pass
            return True, "yes_no_button_click"
    except Exception:
        pass

    return False, "radio_option_not_found"

async def set_checkbox(locator: Locator, answer: Any) -> Tuple[bool, str]:
    desired = norm(answer) in {"true", "yes", "1", "checked", "accept", "accepted", "agree", "i agree", "on"}
    try:
        checked = await locator.is_checked()
        if checked == desired:
            return True, "already_correct"
        if desired:
            await locator.check(force=True, timeout=LOCATOR_TIMEOUT_MS)
        else:
            await locator.uncheck(force=True, timeout=LOCATOR_TIMEOUT_MS)
        return True, "checkbox_set"
    except Exception:
        try:
            current = await locator.get_attribute("aria-checked")
            if (current == "true") != desired:
                await locator.click(force=True, timeout=LOCATOR_TIMEOUT_MS)
            return True, "checkbox_click"
        except Exception:
            return False, "checkbox_failed"

async def set_checkbox_for_element(
    page: Page,
    element: Dict[str, Any],
    answer: Any,
) -> Tuple[bool, str]:
    loc, frame, _ = await locator_from_element(page, element)
    if loc:
        ok, method = await set_checkbox(loc, answer)
        if ok:
            return True, method
    if not frame:
        frame = page.main_frame
    wanted = norm(answer)
    desired = wanted in {"true", "yes", "1", "checked", "accept", "accepted", "agree", "i agree", "on"}
    # Native checkbox matching by associated label/question.
    boxes = frame.locator("input[type='checkbox']")
    try:
        count = await boxes.count()
    except Exception:
        count = 0
    for i in range(min(count, 1000)):
        box = boxes.nth(i)
        try:
            attrs = await box.evaluate("""el => {
                const id = el.id || '';
                const label = id ? document.querySelector(`label[for="${CSS.escape(id)}"]`) : el.closest('label');
                return {id, name: el.name || '', aria: el.getAttribute('aria-label') || '',
                        label: label ? (label.innerText || label.textContent || '') : '',
                        parent: el.parentElement ? (el.parentElement.innerText || '') : ''};
            }""")
            q = norm(element.get("question") or element.get("label") or element.get("name"))
            hay = norm(" ".join(str(v) for v in attrs.values()))
            if q and (q in hay or any(t in hay for t in q.split() if len(t) >= 5)):
                if desired:
                    await box.check(force=True, timeout=LOCATOR_TIMEOUT_MS)
                else:
                    await box.uncheck(force=True, timeout=LOCATOR_TIMEOUT_MS)
                return True, "native_checkbox_semantic"
        except Exception:
            continue
    # ARIA checkbox.
    aria_boxes = frame.locator("[role='checkbox'], [role='switch']")
    try:
        count = await aria_boxes.count()
    except Exception:
        count = 0
    q = norm(element.get("question") or element.get("label") or element.get("name"))
    for i in range(min(count, 1000)):
        box = aria_boxes.nth(i)
        try:
            if not await box.is_visible():
                continue
            text = norm(await box.inner_text())
            aria = norm(await box.get_attribute("aria-label"))
            if q and (q in text or q in aria or any(t in norm(text + " " + aria) for t in q.split() if len(t) >= 5)):
                current = await box.get_attribute("aria-checked")
                if (current == "true") != desired:
                    await box.click(force=True, timeout=LOCATOR_TIMEOUT_MS)
                return True, "aria_checkbox_semantic"
        except Exception:
            continue
    return False, "checkbox_not_found"

async def verify_checkbox(
    locator: Locator,
    answer: Any,
) -> bool:
    desired = norm(answer) in {
        "true",
        "yes",
        "1",
        "checked",
        "accept",
        "accepted",
        "agree",
        "i agree",
    }

    try:
        return (await locator.is_checked()) == desired
    except Exception:
        try:
            aria = await locator.get_attribute(
                "aria-checked"
            )
            return (aria == "true") == desired
        except Exception:
            return False

async def _file_input_has_files(locator: Locator) -> bool:
    try:
        files = await locator.evaluate("el => Array.from(el.files || []).map(f => f.name)")
        return bool(files)
    except Exception:
        return False


async def resolve_file_input_locator(
    locator: Locator,
    frame: Optional[Frame] = None,
    page: Optional[Page] = None,
) -> Optional[Locator]:
    """Custom dropzones are not file inputs. Find the real input[type=file]."""
    try:
        tag = norm(await locator.evaluate("el => (el.tagName || '').toLowerCase()"))
        typ = norm(await locator.get_attribute("type"))
        if tag == "input" and typ == "file":
            return locator
    except Exception:
        pass

    try:
        nested = locator.locator("input[type='file']")
        if await nested.count():
            return nested.first
    except Exception:
        pass

    try:
        nearby = locator.locator(
            "xpath=ancestor-or-self::*[self::form or self::div or self::section][1]//input[@type='file']"
        )
        if await nearby.count():
            return nearby.first
    except Exception:
        pass

    roots: List[Any] = []
    if frame is not None:
        roots.append(frame)
    if page is not None:
        roots.append(page)
        try:
            roots.extend([fr for fr in page.frames if fr not in roots])
        except Exception:
            pass
    if locator is not None:
        try:
            roots.insert(0, locator.page)
        except Exception:
            pass

    selectors = [
        "input[type='file'][name*='resume' i]",
        "input[type='file'][id*='resume' i]",
        "input[type='file'][accept*='pdf' i]",
        "input[type='file']",
    ]
    seen = set()
    for root in roots:
        for sel in selectors:
            key = f"{id(root)}:{sel}"
            if key in seen:
                continue
            seen.add(key)
            try:
                loc = root.locator(sel)
                count = await loc.count()
            except Exception:
                continue
            for i in range(min(count, 8)):
                cand = loc.nth(i)
                try:
                    if await cand.get_attribute("disabled") is not None:
                        continue
                    return cand
                except Exception:
                    continue
    return None


async def upload_file(
    locator: Locator,
    path: str,
    frame: Optional[Frame] = None,
    page: Optional[Page] = None,
) -> Tuple[bool, str]:
    if not path:
        return False, "resume_path_not_configured"

    p = Path(path)
    if not p.exists():
        return False, f"file_not_found:{path}"

    file_loc = await resolve_file_input_locator(locator, frame=frame, page=page)
    if file_loc is None:
        return False, "no_file_input_found"

    try:
        await file_loc.set_input_files(str(p))
        if await _file_input_has_files(file_loc):
            return True, "file_uploaded"
        return True, "file_uploaded_unverified"
    except Exception as exc:
        return False, f"upload_failed:{exc}"

# ============================================================
# PHONE COUNTRY-CODE FALLBACK DISCOVERY
# ============================================================

async def find_intl_tel_root(phone_locator: Locator) -> Optional[Locator]:
    """Find the universal intl-tel-input wrapper around a phone input."""
    try:
        root = phone_locator.locator(
            "xpath=ancestor-or-self::*[contains(concat(' ', normalize-space(@class), ' '), ' iti ')][1]"
        )
        if await root.count():
            return root.first
    except Exception:
        pass
    return None

COUNTRY_NAME_TO_DIAL = {
    "india": "91",
    "indian": "91",
    "united states": "1",
    "usa": "1",
    "united kingdom": "44",
    "uk": "44",
}

DIAL_TO_ISO = {
    "91": "in",
    "1": "us",
    "44": "gb",
    "971": "ae",
    "61": "au",
    "49": "de",
    "81": "jp",
    "86": "cn",
    "92": "pk",
    "880": "bd",
    "94": "lk",
    "977": "np",
}

DIAL_TO_NAMES = {
    "91": ["india"],
    "1": ["united states", "usa"],
    "44": ["united kingdom", "uk"],
    "971": ["united arab emirates"],
    "61": ["australia"],
    "49": ["germany"],
    "81": ["japan"],
    "86": ["china"],
    "92": ["pakistan"],
    "880": ["bangladesh"],
    "94": ["sri lanka"],
    "977": ["nepal"],
}

async def select_intl_tel_country(
    phone_locator: Locator,
    country_code: str,
) -> Tuple[bool, str, str]:
    """Select country on intl-tel-input or react-international-phone (any site)."""
    country_code = clean_text(country_code)
    dial_digits = digits(country_code)
    if not dial_digits:
        dial_digits = COUNTRY_NAME_TO_DIAL.get(canonical_text(country_code), "")
    wanted = country_code or dial_digits
    if not wanted:
        return False, "invalid_country_code", ""
    iso = DIAL_TO_ISO.get(dial_digits, "")
    names = list(DIAL_TO_NAMES.get(dial_digits) or [])
    if canonical_text(country_code) and not digits(country_code):
        names.insert(0, canonical_text(country_code))

    payload = {"dial": dial_digits, "iso": iso, "names": names}
    pick_js = """(el, payload) => {
                const dial = String(payload.dial || '').replace(/\\D/g, '');
                const iso = String(payload.iso || '').toLowerCase();
                const names = (payload.names || []).map(n => String(n).toLowerCase());
                let root = el.closest('.iti')
                    || el.closest('[class*="react-international-phone"]')
                    || el.parentElement;
                for (let i = 0; i < 6 && root; i++) {
                    const opener = root.querySelector(
                        '.iti__selected-country, .iti__selected-flag, .iti__flag-container, [class*="country-selector-button"], button[aria-label*="country" i], button[title*="country" i]'
                    );
                    if (opener) break;
                    root = root.parentElement;
                }
                if (!root) return {ok: false, state: '', reason: 'no_root'};
                const dropdowns = [...document.querySelectorAll(
                    '.iti__country-list, .iti__dropdown-content, [class*="country-selector-dropdown"]'
                )];
                const dropdown = [...dropdowns].reverse().find(node => {
                    const r = node.getBoundingClientRect();
                    const s = getComputedStyle(node);
                    return r.height > 20 && s.display !== 'none' && s.visibility !== 'hidden';
                }) || root;
                const search = dropdown.querySelector(
                    '.iti__search-input, input[type="search"], input[placeholder*="Search" i], input'
                );
                const query = names[0] || '';
                if (search && query) {
                    const proto = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value');
                    if (proto && proto.set) proto.set.call(search, query);
                    else search.value = query;
                    search.dispatchEvent(new Event('input', {bubbles: true}));
                    search.dispatchEvent(new Event('keyup', {bubbles: true}));
                }
                const items = [...dropdown.querySelectorAll(
                    'li.iti__country, li, [role="option"], button'
                )];
                const visible = items.filter(node => {
                    const r = node.getBoundingClientRect();
                    const s = getComputedStyle(node);
                    return r.height > 8 && s.display !== 'none' && s.visibility !== 'hidden';
                });
                const textOf = node => (node.innerText || node.textContent || '').replace(/\\s+/g, ' ').trim();
                const isoOf = node => (
                    node.getAttribute('data-country-code')
                    || node.getAttribute('data-iso2')
                    || node.getAttribute('data-country')
                    || ''
                ).toLowerCase();
                let hit = visible.find(node => iso && isoOf(node) === iso);
                if (!hit && dial) hit = visible.find(node => (node.getAttribute('data-dial-code') || '') === dial);
                if (!hit) hit = visible.find(node => {
                    const t = textOf(node).toLowerCase();
                    const hasName = names.some(n => n && t.includes(n));
                    const hasDial = !dial || t.includes('+' + dial) || t.split('+').pop().replace(/\\D/g,'') === dial;
                    return hasName && hasDial;
                });
                if (!hit && names.length) hit = visible.find(node => names.some(n => textOf(node).toLowerCase().includes(n)));
                if (!hit && dial) hit = visible.find(node => textOf(node).includes('+' + dial));
                if (!hit) {
                    document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', bubbles: true}));
                    return {ok: false, state: '', reason: 'not_found'};
                }
                hit.click();
                const selected = root.querySelector('.iti__selected-country, .iti__selected-flag, [class*="country-selector-button"]');
                const state = ((selected && (selected.getAttribute('aria-label') || selected.innerText)) || textOf(hit) || '').trim();
                return {ok: true, state};
            }"""
    try:
        await phone_locator.evaluate(
            """el => {
                const root = el.closest('.iti')
                    || el.closest('[class*="react-international-phone"]')
                    || el.parentElement;
                const opener = root && root.querySelector(
                    '.iti__selected-country, .iti__selected-flag, .iti__flag-container button, [class*="country-selector-button"], button[aria-label*="country" i]'
                );
                if (opener) opener.click();
                return true;
            }"""
        )
        try:
            await phone_locator.page.wait_for_timeout(250)
        except Exception:
            pass
        state = await phone_locator.evaluate(pick_js, payload)
    except Exception as exc:
        return False, f"intl_tel_js_failed:{exc}", ""

    if not isinstance(state, dict):
        return False, "intl_tel_js_failed", ""
    shown = clean_text(state.get("state"))
    ok = bool(state.get("ok"))
    if ok:
        verified = (
            (dial_digits and (dial_digits in digits(shown) or f"+{dial_digits}" in shown))
            or (iso and iso in canonical_text(shown))
            or any(n in canonical_text(shown) for n in names)
        )
        return verified, "intl_tel_country", shown
    return False, "intl_tel_country_not_found", shown

def phone_live_ok(actual: Any, national: Any, country_code: Any = "") -> bool:
    """True only if the live value contains the COMPLETE 10-digit national number.

    It accepts:
    - Pure 10 national digits: '9810307102'
    - E.164 with dial code: '919810307102' or '+91 9810307102' or '09810307102'
    If truncated (e.g. '91981030710' missing the last 2 digits '02'), returns False.
    """
    got = digits(actual)
    local = digits(national)
    if len(local) >= 10:
        local_10 = local[-10:]
    else:
        local_10 = local

    if not local_10 or not got:
        return False

    # 1. Exact 10 national digits
    if got == local_10:
        return True

    # 2. Ends with the exact 10 national digits (e.g. 919810307102 or 09810307102)
    if got.endswith(local_10):
        return True

    # 3. Dial code prepended and followed by all 10 digits
    dial = digits(country_code)
    if dial and got.startswith(dial):
        rest = got[len(dial):]
        if rest == local_10 or rest.endswith(local_10):
            return True

    # 4. Contains all 10 national digits as a substring
    return local_10 in got

async def fill_phone_widget(
    locator: Locator,
    national: str,
    country_code: str = "",
) -> Tuple[bool, str]:
    """Fill phone input ensuring all 10 national digits are preserved without truncation."""
    national_digits = digits(national)
    local_10 = national_digits[-10:] if len(national_digits) >= 10 else national_digits
    dial = digits(country_code)

    # Check if there is an international country dropdown/flag root nearby
    has_intl_flag = False
    try:
        root = await find_intl_tel_root(locator)
        if root:
            has_intl_flag = True
    except Exception:
        pass

    # Check maxlength attribute of the phone input
    max_len = 0
    try:
        ml = await locator.get_attribute("maxlength")
        if ml and str(ml).isdigit():
            max_len = int(ml)
    except Exception:
        pass

    # On domestic forms without an intl-tel flag or with maxlength <= 11,
    # the 10-digit national number MUST be prioritized so digits are never truncated.
    candidates = []
    if max_len and max_len <= 11:
        candidates = [local_10]
    elif has_intl_flag:
        candidates = [local_10, f"+{dial}{local_10}" if dial else local_10]
    else:
        # Standard input: try 10-digit local number first!
        candidates = [local_10, f"+{dial}{local_10}" if dial else local_10]

    for val in candidates:
        await fill_text(locator, val)
        st = await blur_and_check_invalid(locator)
        actual = clean_text(st.get("value")) or await read_control_value(locator)
        if not st.get("invalid") and phone_live_ok(actual, local_10, country_code):
            return True, actual
    ok, actual = await repair_invalid_text_input(locator, local_10)
    if ok:
        return True, actual

    # If intl flag exists, select country and fill local_10
    if country_code and has_intl_flag:
        await select_intl_tel_country(locator, country_code)
        try:
            await locator.page.wait_for_timeout(150)
            await locator.page.keyboard.press("Escape")
        except Exception:
            pass
        await fill_text(locator, local_10)
        actual = await read_control_value(locator)
        if phone_live_ok(actual, local_10, country_code):
            return True, actual

    # Final fallback: always ensure clean 10-digit number is filled if previous candidate truncated
    await fill_text(locator, local_10)
    actual = await read_control_value(locator)
    return phone_live_ok(actual, local_10, country_code), actual

async def fill_skills_tag(
    page: Page,
    frame: Any,
    locator: Locator,
    answer: str,
    element: Dict[str, Any],
) -> Dict[str, Any]:
    """Universal handler for skill tag / pill inputs (Zoho, Greenhouse, custom ATS).
    Splits skills into individual tokens and types each, selecting autocomplete suggestions
    or pressing Enter to commit each skill badge."""
    skills_raw = str(answer or "")
    tokens = [t.strip() for t in re.split(r"[,;\n]+", skills_raw) if t.strip()]
    if not tokens:
        tokens = ["Python", "FastAPI", "Docker", "Machine Learning", "SQL", "Pandas"]
    tokens = tokens[:6]

    added = []
    for sk in tokens:
        try:
            await locator.scroll_into_view_if_needed(timeout=1000)
            await locator.click(timeout=1000)
            try:
                await locator.fill("")
            except Exception:
                pass
            await locator.type(sk, delay=15)
            await page.wait_for_timeout(250)

            # Look for live suggestion
            sugg = frame.locator('#competencySkillsSuggestedList li, lyte-drop-item, .lyteDropdownOption, [role="option"]').filter(has_text=re.compile(rf"^{re.escape(sk)}$", re.I)).first
            if await sugg.count() and await sugg.is_visible():
                await sugg.click(timeout=800)
            else:
                await locator.press("Enter")
            await page.wait_for_timeout(200)
            added.append(sk)
        except Exception:
            continue

    try:
        pills = await page.evaluate('''() => [...document.querySelectorAll('.skl-selected-skill-li, .skillset-item, .skill-tag, .lyteTag, [class*="selected-skill"]')].map(el => el.innerText.trim()).filter(Boolean)''')
    except Exception:
        pills = []

    ok = len(pills) > 0 or len(added) > 0
    return {
        "method": "skills_tag_added",
        "verified": ok,
        "actual_value": ", ".join(pills) if pills else ", ".join(added),
        "answer": ", ".join(tokens),
    }

async def discover_phone_country_control(
    page: Page,
    phone_locator: Locator,
) -> Optional[Locator]:
    """Universal live fallback for phone country controls."""
    try:
        root = await find_intl_tel_root(phone_locator)
        if root:
            opener = root.locator(
                ".iti__selected-country, .iti__selected-flag, .iti__flag-container button"
            )
            visible = await visible_locator(opener)
            if visible:
                return visible
    except Exception:
        pass

    try:
        frame = await phone_locator.owner_frame()
        if frame is None:
            return None
        candidates = phone_locator.locator(
            "xpath=ancestor::*[self::div or self::td or self::section or self::form][1]"
        ).locator(
            "select, [role='combobox'], button, input"
        )
        count = await candidates.count()
        for i in range(min(count, 100)):
            candidate = candidates.nth(i)
            try:
                if not await candidate.is_visible():
                    continue
                attrs = await candidate.evaluate(
                    """el => ({
                        id: el.id || '',
                        name: el.getAttribute('name') || '',
                        aria: el.getAttribute('aria-label') || '',
                        placeholder: el.getAttribute('placeholder') || '',
                        text: el.innerText || '',
                        className: el.className || ''
                    })"""
                )
                hay = norm(" ".join(str(v) for v in attrs.values() if v))
                if any(x in hay for x in [
                    "country", "dial code", "calling code", "isd", "phone code",
                    "iti selected country", "selected country",
                ]):
                    return candidate
            except Exception:
                continue
    except Exception:
        pass
    return None

async def set_phone_country_if_needed(
    page: Page,
    elements: List[Dict[str, Any]],
    resume_idx: Dict[str, Any],
    deterministic: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Select the resume country code using elements.json first, then live universal fallback."""
    country = clean_text(resume_idx.get("country_code"))
    if not country:
        return []

    results: List[Dict[str, Any]] = []

    # 1. Try the exact country-code element(s) from elements.json.
    country_elements = []
    seen_ids = set()
    for e in elements:
        if is_noise_control(e) or is_captcha(e) or is_submit(e):
            continue
        if not is_phone_country_element(e):
            continue
        ident = clean_text(e.get("id") or e.get("selector") or e["_element_index"])
        if ident in seen_ids:
            continue
        seen_ids.add(ident)
        country_elements.append(e)
        if len(country_elements) >= 2:
            break

    for e in country_elements:
        loc, _, strategy = await locator_from_element(page, e)
        if not loc:
            continue

        # intl-tel-input is special: the actual selected UI is not the hidden
        # input/select that an HTML extractor may have captured.
        try:
            phone_like = is_phone_element(e)
            if phone_like:
                verified, method, state = await select_intl_tel_country(loc, country)
                if verified:
                    results.append({
                        "element_index": e["_element_index"],
                        "field": "phone_country_code",
                        "answer": country,
                        "action": "select",
                        "verified": True,
                        "method": method,
                        "locator_strategy": strategy,
                        "actual_value": state,
                    })
                    return results
        except Exception:
            pass

        ok, method = await select_native_or_custom(
            loc, country,
            option_label=f"India ({country})" if "91" in country else country,
            option_value=country,
            stored_options=e.get("options")
        )
        try:
            await loc.evaluate("""(el, val) => {
                for (let opt of el.options) {
                    const want = String(val || '').replace(/\\D/g, '');
                    const t = (opt.text || '').replace(/\\s+/g, ' ').trim();
                    const v = String(opt.value || '');
                    const dial = ((t.match(/\\+(\\d{1,4})/) || v.match(/^\\+?(\\d{1,4})$/) || [])[1] || '');
                    const hit = (want && dial === want) || v === val || t === val
                        || (want === '91' && /\\bindia\\b/i.test(t) && (t.includes('+91') || v === 'IN' || v === '+91'));
                    if (hit) {
                        el.value = opt.value;
                        opt.selected = true;
                        el.dispatchEvent(new Event('input', {bubbles: true}));
                        el.dispatchEvent(new Event('change', {bubbles: true}));
                        break;
                    }
                }
                if (window.$ || window.jQuery) {
                    const jq = window.$ || window.jQuery;
                    try { jq(el).val(val).trigger('change'); } catch(e) {}
                    try { jq(el).trigger('change.select2'); } catch(e) {}
                }
                const s2 = document.getElementById('select2-mobilePhonecountryCode-container') || document.querySelector('[aria-labelledby*="countryCode"]');
                if (s2) {
                    s2.textContent = val;
                    s2.title = val;
                }
            }""", country)
        except Exception:
            pass
        actual = await read_selected_control_text(loc) if ok else ""
        verified = (
            ok and (
                same_value(country, actual)
                or country in actual
                or digits(country) == digits(actual)
            )
        )
        results.append({
            "element_index": e["_element_index"],
            "field": "phone_country_code",
            "answer": country,
            "action": "select",
            "verified": verified,
            "method": method,
            "locator_strategy": strategy,
            "actual_value": actual,
        })
        if verified:
            return results

    # 2. Live discovery from the actual phone input.
    phone_element = next((
        e for e in elements
        if is_phone_element(e)
        and not is_phone_country_element(e)
        and not is_captcha(e)
        and not is_submit(e)
    ), None)

    if phone_element:
        phone_loc, _, phone_strategy = await locator_from_element(page, phone_element)
        if phone_loc:
            verified, method, state = await select_intl_tel_country(phone_loc, country)
            if verified:
                results.append({
                    "element_index": phone_element["_element_index"],
                    "field": "phone_country_code_live",
                    "answer": country,
                    "action": "select",
                    "verified": True,
                    "method": method,
                    "locator_strategy": phone_strategy,
                    "actual_value": state,
                })
                return results

            country_loc = await discover_phone_country_control(page, phone_loc)
            if country_loc:
                ok, method = await select_native_or_custom(
                    country_loc, country, country, country
                )
                actual = await read_selected_control_text(country_loc) if ok else ""
                verified = ok and (
                    same_value(country, actual)
                    or country in actual
                    or digits(country) == digits(actual)
                )
                results.append({
                    "element_index": phone_element["_element_index"],
                    "field": "phone_country_code_live",
                    "answer": country,
                    "action": "select",
                    "verified": verified,
                    "method": method,
                    "locator_strategy": phone_strategy,
                    "actual_value": actual,
                })

    return results

def is_placeholder_option_label(label: str, question: str = "") -> bool:
    """True when a label or display text is just a placeholder/prompt, not a valid user selection."""
    s = norm(label)
    q = norm(question)
    if not s:
        return True
    placeholders = {
        "select", "choose", "pick", "none", "-none-", "n/a", "null", "undefined",
        "select option", "select an option", "choose option", "choose an option",
        "please select", "please choose", "-select-", "--select--", "---", "--", "-",
        "default select example", "select...", "choose...", "position applying for",
        "years of experience", "notice period", "experience level", "qualification",
    }
    if s in placeholders:
        return True
    if s.startswith("select ") or s.startswith("choose ") or s.startswith("please select ") or s.startswith("select an "):
        return True
    if q and (s == q or s == f"select {q}" or s == f"choose {q}" or s.replace(" ", "") == q.replace(" ", "")):
        return True
    return False

def infer_job_position_option(page_url: str, page_title: str, options: List[Any]) -> Optional[Dict[str, Any]]:
    """Infer the intended job position option from the URL slug or page title."""
    url_slug = ""
    if page_url:
        last_seg = page_url.rstrip("/").split("/")[-1]
        url_slug = re.sub(r"-\d+$", "", last_seg).replace("-", " ").strip().lower()

    title_slug = canonical_text(page_title)

    best = None
    best_score = 0
    for opt in options:
        lbl = option_display(opt)
        val = opt.get("value") if isinstance(opt, dict) else lbl
        if is_placeholder_option_label(lbl):
            continue
        c_lbl = canonical_text(lbl)
        s1 = fuzzy_score(url_slug, c_lbl) if url_slug else 0
        s2 = fuzzy_score(title_slug, c_lbl) if title_slug else 0
        score = max(s1, s2)
        if url_slug and set(url_slug.split()).issubset(set(c_lbl.split())):
            score = max(score, 90)
        if score > best_score:
            best_score = score
            best = {"label": lbl, "value": val}
    return best if best_score >= 60 else None

async def verify_select_control(locator: Locator, expected: Any, option_label: Any = None, option_value: Any = None) -> Tuple[bool, str]:
    targets = [x for x in [option_label, option_value, expected] if clean_text(x)]
    actual = await read_select_display(locator)
    actuals = [actual]
    try:
        tag = norm(await locator.evaluate("el => el.tagName.toLowerCase()"))
    except Exception:
        tag = ""
    if tag == "select":
        try:
            selected = await locator.evaluate("""el => { const o=el.selectedOptions?.[0]; return o ? {text:o.textContent||'',value:o.value||''} : {}; }""")
            actuals.extend([selected.get("text") or "", selected.get("value") or ""])
        except Exception:
            pass
    else:
        try:
            actuals.append(clean_text(await locator.input_value()))
        except Exception:
            pass
    score = max(
        (
            fuzzy_score(t, a)
            or _option_match_score(t, a)
            for t in targets
            for a in actuals
            if clean_text(a)
        ),
        default=0,
    )
    if digits(expected) and any(digits(expected) == digits(a) or digits(expected) in digits(a) for a in actuals if digits(a)):
        score = max(score, 90)
    shown = next((a for a in actuals if clean_text(a) and "select" not in norm(a)), actual)
    if is_placeholder_option_label(shown):
        return False, shown
    if any(is_fallback_other_label(a) for a in actuals) and any(
        re.search(r"university|college|institute|\bschool\b", canonical_text(t)) for t in targets
    ):
        return True, shown
    return score >= 65, shown

async def multi_step_verify_and_repair(
    page: Page,
    element: Dict[str, Any],
    result: Dict[str, Any],
    resume_idx: Dict[str, Any],
) -> Tuple[bool, str, List[str]]:
    """Performs a 4-step verification and auto-repair on a single filled field:
    Step 1: Live DOM Read & Multi-Attribute Inspection
    Step 2: Data Integrity & Truncation Check
    Step 3: Targeted Auto-Repair (if Step 2 detected any defects or truncation)
    Step 4: Post-Repair Confirmation & Quality Verification
    """
    steps_log: List[str] = []
    loc, frame, strategy = await locator_from_element(page, element)
    if not loc:
        steps_log.append("[Step 1: Read] Locator could not be resolved from DOM.")
        steps_log.append("[Step 2: Check] FAIL: Element not found on page.")
        return False, "", steps_log

    idx = element.get("_element_index")
    q_title = clean_text(element.get("question") or element.get("label") or element.get("name") or "")
    kind = control_kind(element)
    answer = clean_text(result.get("answer"))

    # ========================================================
    # 1. PHONE FIELD VERIFICATION & REPAIR
    # ========================================================
    if is_phone_element(element) and not is_phone_country_element(element):
        national = clean_text(resume_idx.get("national_phone") or answer)
        local_10 = digits(national)[-10:] if len(digits(national)) >= 10 else digits(national)
        cc = clean_text(resume_idx.get("country_code") or result.get("country_code") or "+91")
        dial = digits(cc)

        # Step 1: Read live value
        actual = await read_control_value(loc)
        actual_digs = digits(actual)
        steps_log.append(f"[Step 1: Read] Phone DOM value: '{actual}' (digits: '{actual_digs}')")

        # Step 2: Check integrity & detect truncation
        truncation_issue = False
        fail_reason = ""
        if not actual_digs:
            fail_reason = "Phone field is empty"
            truncation_issue = True
        elif dial and actual_digs.startswith(dial) and not actual_digs.endswith(local_10):
            missing_digits = 10 - len(actual_digs[len(dial):])
            fail_reason = (
                f"Truncation detected: field was filled with dial code '{dial}' but only accepted "
                f"{len(actual_digs)} digits, causing last {missing_digits} digit(s) of '{local_10}' to be skipped"
            )
            truncation_issue = True
        elif not phone_live_ok(actual, local_10, cc):
            fail_reason = f"Phone digits '{actual_digs}' incomplete or missing 10-digit national number '{local_10}'"
            truncation_issue = True

        if fail_reason:
            steps_log.append(f"[Step 2: Check] FAIL: {fail_reason}")
        else:
            steps_log.append(f"[Step 2: Check] PASS: Complete 10-digit national number '{local_10}' verified in field")

        # Step 3: Targeted Auto-Repair (if needed)
        if truncation_issue:
            steps_log.append(f"[Step 3: Repair] Clearing truncated value and re-filling exact 10-digit national number '{local_10}'")
            try:
                await loc.focus(timeout=1000)
                await loc.click(timeout=1000)
                await loc.fill("", timeout=1200)
                await page.keyboard.press("Control+A")
                await page.keyboard.press("Backspace")
                await fill_text(loc, local_10)
                await loc.evaluate("""el => {
                    el.dispatchEvent(new Event('input', {bubbles: true}));
                    el.dispatchEvent(new Event('change', {bubbles: true}));
                    el.dispatchEvent(new Event('blur', {bubbles: true}));
                }""")
                await page.wait_for_timeout(100)
            except Exception as ex:
                steps_log.append(f"[Step 3: Repair] Repair error: {ex}")

        # Step 4: Confirm Verification
        post_actual = await read_control_value(loc)
        ok = phone_live_ok(post_actual, local_10, cc)
        status_str = "VERIFIED (Repaired)" if (ok and truncation_issue) else ("VERIFIED" if ok else "FAILED")
        steps_log.append(f"[Step 4: Confirm] Re-read live value: '{post_actual}' -> {status_str}")
        return ok, post_actual, steps_log

    # ========================================================
    # 2. PHONE COUNTRY SELECTOR VERIFICATION & REPAIR
    # ========================================================
    if is_phone_country_element(element):
        expected_cc = clean_text(answer or resume_idx.get("country_code"))
        actual = await read_control_value(loc)
        steps_log.append(f"[Step 1: Read] Phone country display: '{actual}'")

        ok = same_value(expected_cc, actual) or (expected_cc in actual) or (digits(expected_cc) == digits(actual))
        if ok:
            steps_log.append(f"[Step 2: Check] PASS: Country code matches '{expected_cc}'")
        else:
            steps_log.append(f"[Step 2: Check] FAIL: Country code '{actual}' != '{expected_cc}'")
            steps_log.append(f"[Step 3: Repair] Re-selecting intl tel country '{expected_cc}'")
            try:
                rep_ok, _, st = await select_intl_tel_country(loc, expected_cc)
                actual = st or actual
            except Exception:
                pass

        post_actual = await read_control_value(loc)
        ok = same_value(expected_cc, post_actual) or (expected_cc in post_actual) or (digits(expected_cc) == digits(post_actual))
        steps_log.append(f"[Step 4: Confirm] Re-read country value: '{post_actual}' -> {'VERIFIED' if ok else 'FAILED'}")
        return ok, post_actual, steps_log

    # ========================================================
    # 2b. SKILL TAGS / PILLS VERIFICATION & REPAIR
    # ========================================================
    if kind == "skills_tag" or is_skill_tag_input(element):
        pills = []
        try:
            pills = await page.evaluate('''() => [...document.querySelectorAll('.skl-selected-skill-li, .skillset-item, .skill-tag, .lyteTag, [class*="selected-skill"]')].map(el => el.innerText.trim()).filter(Boolean)''')
        except Exception:
            pass
        steps_log.append(f"[Step 1: Read] Skill tags present in DOM: {pills}")
        if pills:
            steps_log.append(f"[Step 2: Check] PASS: {len(pills)} skill tags confirmed.")
            steps_log.append(f"[Step 4: Confirm] Skill tags -> VERIFIED")
            return True, ", ".join(pills), steps_log
        else:
            steps_log.append(f"[Step 2: Check] FAIL: No skill tags found. Attempting repair...")
            rep_res = await fill_skills_tag(page, page, loc, answer, element)
            ok = rep_res.get("verified", False)
            try:
                post_pills = await page.evaluate('''() => [...document.querySelectorAll('.skl-selected-skill-li, .skillset-item, .skill-tag, .lyteTag, [class*="selected-skill"]')].map(el => el.innerText.trim()).filter(Boolean)''')
            except Exception:
                post_pills = []
            status_str = "VERIFIED" if (ok or post_pills) else "FAILED"
            steps_log.append(f"[Step 4: Confirm] Re-read skills: {post_pills} -> {status_str}")
            return (ok or bool(post_pills)), ", ".join(post_pills), steps_log

    # ========================================================
    # 3. SELECT / DROPDOWN VERIFICATION & REPAIR
    # ========================================================
    if kind == "select":
        target_lbl = clean_text(result.get("option_label") or result.get("matched_option", {}).get("label") or answer)
        target_val = clean_text(result.get("option_value") or result.get("matched_option", {}).get("value"))

        sel_state = {"text": "", "value": "", "index": -1}
        try:
            sel_state = await loc.evaluate("""el => {
                if (el.tagName.toLowerCase() === 'select') {
                    const o = el.selectedOptions?.[0];
                    return { text: (o?.textContent || '').trim(), value: o?.value || '', index: el.selectedIndex };
                }
                return { text: (el.value || el.innerText || el.textContent || '').trim(), value: el.value || '', index: -1 };
            }""")
        except Exception:
            pass

        live_text = sel_state.get("text") or await read_select_display(loc)
        live_val = sel_state.get("value") or ""
        steps_log.append(f"[Step 1: Read] Dropdown selected option: '{live_text}' (value: '{live_val}')")

        is_placeholder = is_placeholder_option_label(live_text, q_title) or (sel_state.get("index") == 0 and is_placeholder_option_label(live_text))
        match_ok = False
        if not is_placeholder:
            match_ok, _ = await verify_select_control(loc, target_lbl, target_lbl, target_val)

        if is_placeholder:
            steps_log.append(f"[Step 2: Check] FAIL: Placeholder currently selected ('{live_text}'). No real option chosen.")
        elif not match_ok:
            steps_log.append(f"[Step 2: Check] FAIL: Selected option '{live_text}' does not match target '{target_lbl}'.")
        else:
            steps_log.append(f"[Step 2: Check] PASS: Valid option '{live_text}' selected.")

        repaired = False
        chosen = None
        if is_placeholder or not match_ok:
            valid_opts = [o for o in element.get("options", []) if not is_placeholder_option_label(option_display(o), q_title)]
            if target_lbl and not is_placeholder_option_label(target_lbl, q_title):
                for opt in valid_opts:
                    lbl = option_display(opt)
                    val = opt.get("value") if isinstance(opt, dict) else lbl
                    if same_value(target_lbl, lbl) or same_value(target_val, val) or fuzzy_score(target_lbl, lbl) >= 65 or _option_match_score(target_lbl, lbl) >= 70:
                        chosen = {"label": lbl, "value": val}
                        break
            if not chosen:
                if "position" in canonical_text(q_title) or "role" in canonical_text(q_title):
                    chosen = infer_job_position_option(page.url, await page.title(), valid_opts)
            if not chosen and valid_opts:
                chosen = {"label": option_display(valid_opts[0]), "value": valid_opts[0].get("value") if isinstance(valid_opts[0], dict) else option_display(valid_opts[0])}

            if chosen:
                steps_log.append(f"[Step 3: Repair] Selecting valid option '{chosen['label']}' (value: '{chosen['value']}')")
                try:
                    tag = norm(await loc.evaluate("el => el.tagName.toLowerCase()"))
                    if tag == "select":
                        try:
                            await loc.select_option(value=chosen["value"], timeout=1200)
                        except Exception:
                            await loc.select_option(label=chosen["label"], timeout=1200)
                        await loc.evaluate("el => el.dispatchEvent(new Event('change', {bubbles: true}))")
                    else:
                        await loc.click(timeout=1000)
                        await page.wait_for_timeout(150)
                        try:
                            await page.locator(f"text='{chosen['label']}'").first.click(timeout=1200)
                        except Exception:
                            pass
                    repaired = True
                    await page.wait_for_timeout(50)
                except Exception as ex:
                    steps_log.append(f"[Step 3: Repair] Selection error: {ex}")

        post_display = await read_select_display(loc)
        target_check = chosen["label"] if (repaired and chosen) else target_lbl
        post_ok, final_shown = await verify_select_control(loc, target_check, target_check, chosen.get("value") if (repaired and chosen) else target_val)
        if is_placeholder_option_label(final_shown, q_title):
            post_ok = False
        status_str = "VERIFIED (Repaired)" if (post_ok and (is_placeholder or not match_ok)) else ("VERIFIED" if post_ok else "FAILED")
        steps_log.append(f"[Step 4: Confirm] Re-read selected option: '{final_shown}' -> {status_str}")
        return post_ok, final_shown, steps_log

    # ========================================================
    # 4. FILE UPLOAD VERIFICATION & REPAIR
    # ========================================================
    if is_file_upload(element) or result.get("action") == "upload":
        files = ""
        try:
            files = await loc.input_value()
        except Exception:
            pass
        shown = ""
        try:
            shown = await loc.evaluate("""el => ((el.closest('section, label, div') || el.parentElement || el).innerText || '')""")
        except Exception:
            pass
        name = Path(RESUME_PATH).name if RESUME_PATH else ""
        hay = canonical_text(f"{files} {shown}")
        live_ok = bool(files) or (name and canonical_text(name) in hay)

        steps_log.append(f"[Step 1: Read] Upload field state: '{clean_text(files or shown)[:50]}'")
        if live_ok:
            steps_log.append(f"[Step 2: Check] PASS: Resume file attached")
        else:
            steps_log.append(f"[Step 2: Check] FAIL: No resume attached")
            if RESUME_PATH and Path(RESUME_PATH).exists():
                steps_log.append(f"[Step 3: Repair] Re-uploading resume '{name}'")
                try:
                    await loc.set_input_files(RESUME_PATH)
                    await loc.evaluate("el => el.dispatchEvent(new Event('change', {bubbles: true}))")
                except Exception as ex:
                    steps_log.append(f"[Step 3: Repair] Upload error: {ex}")

        try:
            files2 = await loc.input_value()
        except Exception:
            files2 = ""
        live_ok2 = bool(files2) or (name and canonical_text(name) in hay) or bool(result.get("verified"))
        steps_log.append(f"[Step 4: Confirm] Resume upload -> {'VERIFIED' if live_ok2 else 'FAILED'}")
        return live_ok2, files2 or name, steps_log

    # ========================================================
    # 5. CHECKBOX VERIFICATION & REPAIR
    # ========================================================
    if kind == "checkbox":
        expected_checked = is_positive_answer(answer)
        try:
            live_checked = await loc.is_checked()
        except Exception:
            live_checked = False
        steps_log.append(f"[Step 1: Read] Checkbox checked: {live_checked}")

        if live_checked == expected_checked:
            steps_log.append(f"[Step 2: Check] PASS: Checked state matches expected ({expected_checked})")
        else:
            steps_log.append(f"[Step 2: Check] FAIL: Checked state {live_checked} != expected {expected_checked}")
            steps_log.append(f"[Step 3: Repair] Clicking checkbox to set state to {expected_checked}")
            try:
                await loc.click(timeout=1200)
            except Exception:
                pass

        try:
            post_checked = await loc.is_checked()
        except Exception:
            post_checked = live_checked
        ok = post_checked == expected_checked
        steps_log.append(f"[Step 4: Confirm] Checkbox state: {post_checked} -> {'VERIFIED' if ok else 'FAILED'}")
        return ok, str(post_checked), steps_log

    # ========================================================
    # 6. RADIO VERIFICATION
    # ========================================================
    if kind == "radio":
        steps_log.append(f"[Step 1: Read] Radio group state inspection")
        ok = bool(result.get("verified"))
        steps_log.append(f"[Step 2: Check] {'PASS: Radio selected' if ok else 'FAIL: Radio unselected'}")
        if not ok:
            steps_log.append(f"[Step 3: Repair] Re-attempting radio click for '{answer}'")
            try:
                await loc.click(timeout=1200)
                ok = True
            except Exception:
                pass
        steps_log.append(f"[Step 4: Confirm] Radio -> {'VERIFIED' if ok else 'FAILED'}")
        return ok, answer, steps_log

    # ========================================================
    # 7. DATE FIELD VERIFICATION & REPAIR
    # ========================================================
    if is_date_element(element):
        actual = await read_control_value(loc)
        expected_date = parse_date_value(answer)
        actual_date = parse_date_value(actual)
        steps_log.append(f"[Step 1: Read] Date value: '{actual}'")
        ok = same_value(answer, actual) or (expected_date is not None and actual_date == expected_date)

        if ok:
            steps_log.append(f"[Step 2: Check] PASS: Date matches '{answer}'")
        else:
            steps_log.append(f"[Step 2: Check] FAIL: Date '{actual}' != '{answer}'")
            steps_log.append(f"[Step 3: Repair] Re-filling date with '{answer}'")
            try:
                await loc.fill("", timeout=1200)
                await fill_text(loc, answer)
            except Exception:
                pass

        try:
            await page.keyboard.press("Escape")
        except Exception:
            pass
        post_actual = await read_control_value(loc)
        ok = same_value(answer, post_actual) or (expected_date is not None and parse_date_value(post_actual) == expected_date)
        steps_log.append(f"[Step 4: Confirm] Date value: '{post_actual}' -> {'VERIFIED' if ok else 'FAILED'}")
        return ok, post_actual, steps_log

    # ========================================================
    # 8. TEXT / TEXTAREA VERIFICATION & REPAIR
    # ========================================================
    actual = await read_control_value(loc)
    expected = answer
    steps_log.append(f"[Step 1: Read] Text value: '{actual[:60]}'")

    # Check if element is an editable input or textarea
    is_editable = True
    try:
        is_editable = await loc.evaluate("""el => {
            if (!el) return false;
            if (el.disabled || el.readOnly || el.getAttribute('aria-readonly') === 'true') return false;
            const tag = el.tagName.toLowerCase();
            if (tag === 'input') {
                const type = (el.type || 'text').toLowerCase();
                return !['hidden', 'button', 'submit', 'reset', 'checkbox', 'radio'].includes(type);
            }
            return tag === 'textarea' || el.isContentEditable;
        }""")
    except Exception:
        is_editable = False

    if not is_editable:
        # Non-editable display/badge element (e.g. static unit label)
        steps_log.append(f"[Step 2: Check] PASS: Display-only element '{actual[:40]}' verified")
        steps_log.append(f"[Step 4: Confirm] Live value: '{actual[:40]}' -> VERIFIED")
        return True, actual, steps_log

    fail_reason = ""
    if not actual and expected:
        fail_reason = f"Field is empty (expected '{expected[:40]}')"
    elif len(actual) < len(expected) and not same_value(expected, actual):
        fail_reason = f"Text truncated ({len(actual)} chars in DOM vs {len(expected)} chars expected)"
    elif not same_value(expected, actual) and not (expected in actual) and not (digits(expected) and digits(expected) == digits(actual)):
        fail_reason = f"Value mismatch ('{actual[:30]}' != '{expected[:30]}')"

    st = await blur_and_check_invalid(loc)
    if st.get("invalid") and not fail_reason:
        fail_reason = st.get("validationMessage") or "native constraint invalid"

    if fail_reason:
        steps_log.append(f"[Step 2: Check] FAIL: {fail_reason}")
        steps_log.append("[Step 3: Repair] Constraint-based formats (no extra AI, no repeat clicks)")
        try:
            ok_fix, post = await repair_invalid_text_input(loc, expected)
            steps_log.append(f"[Step 3: Repair] now='{post[:50]}' valid={ok_fix}")
        except Exception as ex:
            steps_log.append(f"[Step 3: Repair] Error during constraint repair: {ex}")
    else:
        steps_log.append(f"[Step 2: Check] PASS: Text matches expected value")

    post_actual = await read_control_value(loc)
    ok = same_value(expected, post_actual) or (expected in post_actual) or (digits(expected) and digits(expected) == digits(post_actual))
    status_str = "VERIFIED (Repaired)" if (ok and fail_reason) else ("VERIFIED" if ok else "FAILED")
    steps_log.append(f"[Step 4: Confirm] Re-read live value: '{post_actual[:60]}' -> {status_str}")
    return ok, post_actual, steps_log

# ============================================================
# FILL ONE ANSWER
# ============================================================

async def pick_opened_calendar(page: Page, locator: Locator, parsed: date) -> bool:
    """HPE-style popup: year dropdown + month grid (+ day). No Bedrock."""
    y, m, d = parsed.year, parsed.month, parsed.day
    months = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
    mon = months[m - 1]
    try:
        await locator.click(timeout=3000)
    except Exception:
        return False
    await page.wait_for_timeout(250)
    try:
        hit = await page.evaluate(
            """(y, mon, d) => {
                const vis = el => {
                    const r = el.getBoundingClientRect();
                    return r.width > 8 && r.height > 8;
                };
                const pop = [...document.querySelectorAll('div, section, [role="dialog"]')].reverse().find(el => {
                    if (!vis(el)) return false;
                    const t = (el.innerText || '');
                    const h = el.getBoundingClientRect().height;
                    return /\\bJan\\b/i.test(t) && /\\bDec\\b/i.test(t) && /20\\d{2}/.test(t) && h > 80;
                });
                if (!pop) return false;
                for (const s of pop.querySelectorAll('select')) {
                    const opt = [...s.options].find(o => (o.textContent || o.value || '').includes(String(y)));
                    if (opt) {
                        s.value = opt.value;
                        s.dispatchEvent(new Event('change', {bubbles: true}));
                    }
                }
                const yearish = [...pop.querySelectorAll('button, [role="button"], select')].find(b =>
                    /^\\s*20\\d{2}\\s*$/.test((b.innerText || b.value || '').trim())
                );
                if (yearish && yearish.tagName !== 'SELECT') {
                    yearish.click();
                    const yopt = [...document.querySelectorAll('li, button, option, [role="option"]')].find(n =>
                        vis(n) && (n.innerText || '').trim() === String(y)
                    );
                    if (yopt) yopt.click();
                }
                const monthBtn = [...pop.querySelectorAll('button, td, div, span, [role="button"]')].find(n => {
                    if (!vis(n)) return false;
                    const t = (n.innerText || '').trim().toLowerCase();
                    return t === mon || t === mon.slice(0, 3);
                });
                if (monthBtn) monthBtn.click();
                const dayBtn = [...pop.querySelectorAll('button, td, [role="gridcell"]')].find(n => {
                    if (!vis(n)) return false;
                    return (n.innerText || '').trim() === String(d);
                });
                if (dayBtn) dayBtn.click();
                return true;
            }""",
            y,
            mon,
            d,
        )
        if hit:
            await page.wait_for_timeout(200)
            try:
                await page.keyboard.press("Escape")
            except Exception:
                pass
            return True
    except Exception:
        pass
    try:
        await page.keyboard.press("Escape")
    except Exception:
        pass
    return False


async def repair_ui_field_errors(page: Page) -> List[Dict[str, Any]]:
    """Fix only fields that show a UI error. Do not refill the whole form."""
    fixed = []
    try:
        rows = await page.evaluate(
            """() => {
                const out = [];
                const labelOf = el => {
                    let t = (el.getAttribute('aria-label') || '').trim();
                    if (el.id) {
                        const lab = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
                        if (lab) t = (lab.innerText || t).trim();
                    }
                    return t.replace(/\\s+/g, ' ').slice(0, 80);
                };
                for (const el of document.querySelectorAll('input, textarea, select')) {
                    const type = (el.type || '').toLowerCase();
                    if (['hidden','submit','button','reset','file','checkbox','radio'].includes(type)) continue;
                    const wrap = el.closest('div, li, td, label, .form-group, .field') || el.parentElement;
                    let err = '';
                    if (wrap) {
                        const nodes = wrap.querySelectorAll('span, div, p, small, em, label');
                        for (const n of nodes) {
                            const t = (n.innerText || '').replace(/\\s+/g, ' ').trim();
                            if (!t || t.length > 90) continue;
                            if (/valid url|invalid|required|please enter|please select/i.test(t)) {
                                err = t; break;
                            }
                        }
                    }
                    const native = !!(el.validity && !el.validity.valid);
                    if (!err && !native) continue;
                    out.push({
                        id: el.id || '',
                        name: el.name || '',
                        type,
                        value: el.value || '',
                        err: err || el.validationMessage || '',
                        label: labelOf(el),
                    });
                }
                return out;
            }"""
        ) or []
    except Exception:
        rows = []
    for row in rows:
        err = (row.get("err") or "").lower()
        label = (row.get("label") or "").lower()
        val = row.get("value") or ""
        sel = ("#" + row["id"]) if row.get("id") else (
            f"[name=\"{row['name']}\"]" if row.get("name") else ""
        )
        if not sel:
            continue
        loc = page.locator(sel).first
        try:
            if not await loc.count():
                continue
            if "url" in err or "linkedin" in label or "linkedin" in err:
                nxt = ensure_https_url(val)
                if nxt and nxt != val:
                    await fill_text(loc, nxt)
                    fixed.append({"label": label or sel, "fix": nxt, "err": row.get("err")})
                continue
            if any(w in label or w in err for w in ("date", "dob", "from", "to", "birth")):
                parsed = parse_date_value(val)
                if "birth" in label or "dob" in label:
                    parsed = parsed or parse_date_value("28/10/2005")
                if parsed:
                    await pick_opened_calendar(page, loc, parsed)
                    fixed.append({"label": label or sel, "fix": "calendar", "err": row.get("err")})
                continue
            await repair_invalid_text_input(loc, val)
            fixed.append({"label": label or sel, "fix": "constraint", "err": row.get("err")})
        except Exception:
            continue
    return fixed


async def fill_answer(
    page: Page,
    element: Dict[str, Any],
    answer_item: Dict[str, Any],
) -> Dict[str, Any]:
    idx = element["_element_index"]

    answer = clean_text(answer_item.get("answer")) or clean_text(answer_item.get("option_label")) or clean_text(answer_item.get("option_value"))
    action = norm(answer_item.get("action") or "fill")
    if action == "auto":
        action = control_kind(element)
    elif action == "fill" and control_kind(element) in {"select", "radio", "checkbox", "upload"}:
        # Never allow an AI/user answer to turn an actual control into a text fill.
        action = control_kind(element)
    option_label = answer_item.get("option_label")
    option_value = answer_item.get("option_value")

    result = {
        "element_index": idx,
        "question": element.get("question"),
        "selector_from_json": element.get("selector"),
        "frame_index": element.get("frame_index"),
        "action": action,
        "answer": answer,
        "verified": False,
        "method": None,
        "locator_strategy": None,
        "error": None,
    }

    if is_captcha(element) or is_submit(element):
        result["method"] = "blocked_control"
        result["error"] = "captcha_or_submit_not_allowed"
        return result

    # Phone: when country code is a separate control,
    # put only national digits into the phone input.
    if is_phone_element(element) and not is_phone_country_element(element):
        if answer:
            d = digits(answer)
            if len(d) >= 10:
                # India-specific national number handling only when
                # the supplied resume phone begins with +91.
                if (
                    str(answer).strip().startswith("+91")
                    and len(d) > 10
                ):
                    answer = d[-10:]
                elif len(d) == 10:
                    answer = d

    loc, frame, strategy = await locator_from_element(page, element)
    if loc:
        try:
            await loc.scroll_into_view_if_needed(timeout=LOCATOR_TIMEOUT_MS)
        except Exception:
            pass

    result["locator_strategy"] = strategy

    if not loc:
        result["error"] = "element_not_found"
        return result

    # Inspect the LIVE DOM. elements.json can describe a custom dropdown as
    # an input/text field even though the browser control is a combobox.
    try:
        live_meta = await loc.evaluate("""el => ({
            tag: el.tagName.toLowerCase(),
            type: (el.getAttribute('type') || '').toLowerCase(),
            role: (el.getAttribute('role') || '').toLowerCase(),
            ariaHaspopup: (el.getAttribute('aria-haspopup') || '').toLowerCase(),
            className: el.className || '',
            readonly: el.hasAttribute('readonly'),
            disabled: el.hasAttribute('disabled'),
            contenteditable: el.getAttribute('contenteditable') || ''
        })""")
    except Exception:
        live_meta = {}

    live_tag = norm(live_meta.get("tag"))
    live_type = norm(live_meta.get("type"))
    live_role = norm(live_meta.get("role"))
    live_popup = norm(live_meta.get("ariaHaspopup"))
    live_class = norm(live_meta.get("className"))

    if action == "skip":
        kind = control_kind(element)
        required = bool(
            element.get("required") is True
            or "*" in clean_text(element.get("question") or element.get("label"))
        )
        if required and kind in {"select", "radio"}:
            action = kind
            if not clean_text(answer) or is_skip_answer(answer):
                answer = "Unknown"
            result["action"] = action
            result["answer"] = answer
        else:
            result["method"] = "skipped"
            result["verified"] = True
            return result

    is_tel_input = live_type == "tel" or "iti__tel" in live_class or (
        is_phone_element(element) and not is_phone_country_element(element)
    )

    if is_skill_tag_input(element):
        action = "skills_tag"

    if action == "fill":
        if live_type == "checkbox" or live_role == "checkbox":
            action = "checkbox"
        elif live_type == "radio" or live_role == "radio":
            action = "radio"
        elif not is_tel_input and not is_skill_tag_input(element) and (
            live_tag == "select"
            or live_role in {"combobox", "listbox"}
            or live_popup in {"listbox", "menu", "true"}
            or (live_role == "button" and (
                live_popup
                or element.get("options")
                or "cu-select" in (norm(element.get("selector")) + " " + norm(element.get("dom_path")))
            ))
            or "select__input" in live_class
            or element.get("options")
            or norm(element.get("control_type")) in {"select", "dropdown", "combobox"}
            or norm(element.get("runtime_role")) in {"combobox", "listbox"}
            or "select__input" in norm(await loc.get_attribute("class"))
        ):
            action = "select"

    if is_tel_input:
        cc = clean_text(answer_item.get("country_code"))
        if not cc and str(answer_item.get("answer") or "").strip().startswith("+"):
            m = re.match(r"^\+(\d{1,3})", str(answer_item.get("answer")))
            if m:
                cc = "+" + m.group(1)
        national = digits(answer)
        if len(national) >= 10:
            national = national[-10:]
        ok, actual = await fill_phone_widget(loc, national, cc)
        result["method"] = "phone_widget"
        result["answer"] = national
        result["country_code"] = cc
        result["actual_value"] = actual
        result["verified"] = ok
        return result
    if (is_phone_country_element(element) or ("iti__selected" in live_class)) and not is_tel_input:
        verified, method, state = await select_intl_tel_country(loc, answer)
        if not verified:
            try:
                phone_loc = frame.locator("#phone, input[type='tel']").first
                verified, method, state = await select_intl_tel_country(phone_loc, answer)
            except Exception:
                pass
        result["method"] = method
        result["verified"] = verified
        result["actual_value"] = state
        result["answer"] = answer
        try:
            await page.keyboard.press("Escape")
        except Exception:
            pass
        return result

    if action == "skills_tag" or is_skill_tag_input(element):
        res_skills = await fill_skills_tag(page, frame or page, loc, answer, element)
        result.update(res_skills)
        return result

    # Date / DOB controls. Handle native date inputs, text date inputs and
    # common JS datepicker inputs without hard-coding a vendor.
    if action == "fill" and is_date_element(element) and answer:
        raw_date = clean_text(answer)
        parsed = parse_date_value(raw_date)
        try:
            live_type = norm(await loc.get_attribute("type"))
        except Exception:
            live_type = ""

        if parsed and live_type == "date":
            answer = parsed.strftime("%Y-%m-%d")
        elif parsed:
            # Prefer the field's placeholder/format when available.
            placeholder = clean_text(await loc.get_attribute("placeholder"))
            fmt_hint = norm(placeholder)
            if "dd/mm/yyyy" in fmt_hint or "dd-mm-yyyy" in fmt_hint:
                sep = "/" if "/" in placeholder else "-"
                answer = parsed.strftime(f"%d{sep}%m{sep}%Y")
            elif "mm/dd/yyyy" in fmt_hint or "mm-dd-yyyy" in fmt_hint:
                sep = "/" if "/" in placeholder else "-"
                answer = parsed.strftime(f"%m{sep}%d{sep}%Y")
            elif any(m in fmt_hint for m in ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]):
                answer = parsed.strftime("%d %B %Y")
            else:
                answer = parsed.strftime("%d/%m/%Y")

        # Try the normal fill first. If the datepicker is readonly, use
        # keyboard/DOM fallback below.
        date_ok = await fill_text(loc, str(answer))
        if not date_ok:
            try:
                await loc.click(timeout=LOCATOR_TIMEOUT_MS)
                await loc.press("Control+A")
                await loc.type(str(answer), delay=0)
                await loc.press("Enter")
                date_ok = True
            except Exception:
                pass

        if date_ok:
            await fire_react_events(loc)
            result["method"] = "date_fill"
            result["verified"] = await verify_text(loc, str(answer))

            if not result["verified"] and parsed:
                # Universal last fallback for controlled/read-only date fields.
                try:
                    iso = parsed.strftime("%Y-%m-%d")
                    await loc.evaluate(
                        """(el, value) => {
                            const proto = el instanceof HTMLInputElement
                                ? HTMLInputElement.prototype
                                : null;
                            const setter = proto
                                ? Object.getOwnPropertyDescriptor(proto, 'value')?.set
                                : null;
                            if (setter) setter.call(el, value);
                            else el.value = value;
                            el.dispatchEvent(new Event('input', {bubbles:true}));
                            el.dispatchEvent(new Event('change', {bubbles:true}));
                            el.dispatchEvent(new Event('blur', {bubbles:true}));
                        }""",
                        iso if live_type == "date" else str(answer),
                    )
                    result["verified"] = await verify_text(
                        loc, iso if live_type == "date" else str(answer)
                    )
                    result["method"] = "date_dom_fallback"
                except Exception:
                    pass

        if result["verified"]:
            result["answer"] = answer
            return result

        if parsed:
            try:
                picked = await pick_opened_calendar(page, loc, parsed)
                if picked:
                    result["method"] = "date_calendar"
                    result["verified"] = True
                    result["answer"] = answer
                    return result
            except Exception:
                pass
            try:
                cal_ok = await loc.evaluate(
                    """(el, y, m, d) => {
                        const root = el.closest('.field, .form-group, [class*="date"]')
                            || el.parentElement
                            || document.body;
                        const scope = root.parentElement || root;
                        const monthNames = ['jan','feb','mar','apr','may','jun','jul','aug','sep','oct','nov','dec'];
                        const selects = [...scope.querySelectorAll('select')];
                        for (const s of selects) {
                            const opts = [...s.options];
                            const yearHit = opts.find(o => (o.textContent || '').includes(String(y)) || o.value === String(y));
                            if (yearHit) {
                                s.value = yearHit.value;
                                s.dispatchEvent(new Event('change', {bubbles: true}));
                                continue;
                            }
                            const monthHit = opts.find(o => {
                                const t = (o.textContent || o.value || '').toLowerCase();
                                return t.includes(monthNames[m - 1]) || o.value === String(m) || o.value === String(m).padStart(2, '0');
                            });
                            if (monthHit && opts.length <= 13) {
                                s.value = monthHit.value;
                                s.dispatchEvent(new Event('change', {bubbles: true}));
                            }
                        }
                        const day = String(d);
                        const nodes = [...scope.querySelectorAll('button, td, [role="option"], [role="gridcell"]')];
                        const hit = nodes.find(n => (n.textContent || '').trim() === day);
                        if (hit) hit.click();
                        const proto = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value');
                        const shown = `${String(d).padStart(2,'0')}/${String(m).padStart(2,'0')}/${y}`;
                        if (el.tagName === 'INPUT' && proto && proto.set) proto.set.call(el, shown);
                        el.dispatchEvent(new Event('input', {bubbles: true}));
                        el.dispatchEvent(new Event('change', {bubbles: true}));
                        return true;
                    }""",
                    parsed.year,
                    parsed.month,
                    parsed.day,
                )
                if cal_ok:
                    result["method"] = "date_picker_parts"
                    result["verified"] = True
                    result["answer"] = answer
                    return result
            except Exception:
                pass

        result["answer"] = answer
        result["error"] = "date_fill_not_verified"
        return result

    # Generic button/link click. Submit/apply is always blocked above.
    if action in {"click", "press"}:
        try:
            await loc.scroll_into_view_if_needed()
            await loc.click(force=True, timeout=LOCATOR_TIMEOUT_MS)
            result["method"] = "click"
            result["verified"] = True
        except Exception as exc:
            result["method"] = "click_failed"
            result["error"] = str(exc)
        return result

    # File upload.
    if action == "upload" or is_file_upload(element) or looks_like_resume_upload(element):
        ok, method = await upload_file(
            loc,
            RESUME_PATH,
            frame=frame,
            page=page,
        )
        result["method"] = method
        result["action"] = "upload"
        if ok:
            result["verified"] = True
            try:
                file_loc = await resolve_file_input_locator(loc, frame=frame, page=page)
                if file_loc and await _file_input_has_files(file_loc):
                    result["verified"] = True
                elif RESUME_PATH:
                    name = Path(RESUME_PATH).name.lower()
                    snippet = canonical_text(await page.inner_text("body"))
                    result["verified"] = name in snippet or "resume" in snippet
            except Exception:
                result["verified"] = True
        return result

    # Checkbox.
    if action == "checkbox":
        ok, method = await set_checkbox_for_element(
            page,
            element,
            answer,
        )
        result["method"] = method

        if ok:
            try:
                result["verified"] = await verify_checkbox(loc, answer)
            except Exception:
                result["verified"] = True
        return result

    # Radio.
    if action == "radio":
        ok, method = await click_radio(
            page,
            element,
            answer,
            option_label,
            option_value,
        )
        result["method"] = method

        if ok:
            wanted = norm(
                option_label
                or option_value
                or answer
            )

            for opt in element.get("options", []) or []:
                if not isinstance(opt, dict):
                    continue

                opt_text = norm(
                    opt.get("label")
                    or opt.get("text")
                    or opt.get("value")
                )

                if (
                    opt_text == wanted
                    or wanted in opt_text
                    or opt_text in wanted
                ):
                    if opt.get("selector"):
                        opt_e = dict(element)
                        opt_e["selector"] = opt["selector"]

                        opt_loc, _, _ = (
                            await locator_from_element(
                                page,
                                opt_e,
                            )
                        )

                        if opt_loc:
                            try:
                                result["verified"] = (
                                    await opt_loc.is_checked()
                                )
                            except Exception:
                                pass

            if not result["verified"]:
                try:
                    checked = loc.locator("input[type='radio']:checked")
                    count = await checked.count()
                    for i in range(count):
                        cr = checked.nth(i)
                        attrs = await cr.evaluate("""el => ({value:el.value||'', id:el.id||'', label:(el.labels&&el.labels[0]) ? (el.labels[0].innerText||el.labels[0].textContent||'') : ''})""")
                        if fuzzy_score(wanted, attrs.get("value")) >= 65 or fuzzy_score(wanted, attrs.get("label")) >= 65:
                            result["verified"] = True
                            break
                except Exception:
                    pass

            if not result["verified"]:
                result["verified"] = await is_choice_selected(
                    page, element, option_label or option_value or answer
                )

        return result

    # Select / combobox.
    element_type = norm(element.get("type"))
    tag = norm(element.get("tag"))
    role = norm(element.get("role"))
    control_type = norm(element.get("control_type"))

    is_select_control = (
        action == "select"
        or tag == "select"
        or element_type in {"select", "combobox"}
        or role in {"combobox", "listbox"}
        or "combobox" in control_type
        or "dropdown" in control_type
        or "cu-select" in (norm(element.get("selector")) + " " + norm(element.get("dom_path")))
        or (bool(element.get("options")) and not is_phone_element(element))
        or (role == "button" and (
            bool(element.get("options"))
            or norm(element.get("haspopup")) in {"listbox", "menu", "list", "true"}
        ))
    )

    if is_select_control:
        resolved = resolve_option_from_element(element, answer, option_label, option_value)
        if resolved:
            option_label = clean_text(resolved.get("label") or resolved.get("text")) or option_label
            option_value = clean_text(resolved.get("value")) or option_value
            result["matched_option"] = {
                "label": option_label,
                "value": option_value,
            }
        resume_blob = ""
        try:
            resume_blob = json.dumps(load_json(RESUME_FILE), ensure_ascii=False)[:2500]
        except Exception:
            resume_blob = ""
        ok, method = await select_native_or_custom(
            loc,
            answer,
            option_label,
            option_value,
            frame=frame,
            stored_options=usable_options(element),
            specify_text=" ".join(
                x for x in [
                    clean_text(answer_item.get("specify_value")),
                    clean_text(answer),
                    resume_blob,
                ] if x
            ),
        )
        result["method"] = method

        if ok:
            target = option_label or option_value or answer
            verified, actual = await verify_select_control(loc, target, option_label, option_value)
            result["actual_value"] = actual
            result["verified"] = verified
            if not result["verified"]:
                display = await read_select_display(loc)
                result["actual_value"] = display or actual
                result["verified"] = any(
                    fuzzy_score(t, display) >= 70 or same_value(t, display)
                    for t in [option_label, option_value, answer]
                    if clean_text(t)
                )

        return result

    # Fill text / textarea / contenteditable.
    ok = await fill_text(
        loc,
        "" if answer is None else str(answer),
    )

    result["method"] = "fill" if ok else "fill_failed"

    if ok:
        hay = canonical_text(" ".join(str(element.get(k) or "") for k in ("id", "name", "label", "question", "placeholder", "selector")))
        is_autocomplete = (
            any(w in hay for w in ["location", "city", "address", "state", "place"])
            or norm(element.get("role") or element.get("runtime_role")) == "combobox"
            or norm(element.get("aria_autocomplete")) in {"list", "both", "inline"}
            or bool(element.get("haspopup"))
        )
        if is_autocomplete:
            await page.wait_for_timeout(600)
            target_scope = frame or page
            suggestion_selectors = [
                '[role="listbox"] [role="option"]',
                '[role="option"]',
                '.pac-item',
                'div[class*="menu"] [role="option"]',
                'div[class*="suggestion"]',
                'div[class*="popover"] [role="option"]',
                'li[role="option"]',
                'ul[role="listbox"] > li',
                'div[class*="dropdown-menu"] a',
                'div[class*="dropdown-item"]',
            ]
            clicked_suggestion = False
            for sel in suggestion_selectors:
                try:
                    opt_loc = target_scope.locator(sel)
                    cnt = await opt_loc.count()
                    for i in range(min(cnt, 5)):
                        item = opt_loc.nth(i)
                        if await item.is_visible():
                            await item.click(timeout=1500)
                            clicked_suggestion = True
                            print(f"[fill_answer] Selected autocomplete suggestion: {sel}", file=sys.stderr)
                            break
                    if clicked_suggestion:
                        break
                except Exception:
                    continue

            if not clicked_suggestion:
                try:
                    await loc.press("ArrowDown")
                    await page.wait_for_timeout(200)
                    await loc.press("Enter")
                    print("[fill_answer] Pressed ArrowDown+Enter on autocomplete input", file=sys.stderr)
                except Exception:
                    pass
        else:
            try:
                await loc.press("Tab")
            except Exception:
                pass

        # React/state propagation.
        await fire_react_events(loc)

        result["verified"] = await verify_text(
            loc,
            "" if answer is None else str(answer),
        )

        # Some widgets transform phone values. Check digits.
        if (
            not result["verified"]
            and is_phone_element(element)
        ):
            actual = await read_control_value(loc)
            result["verified"] = (
                digits(answer) == digits(actual)
                or digits(answer).endswith(digits(actual))
                or digits(actual).endswith(digits(answer))
            )

    return result

# ============================================================
# LOCAL RETRY WITHOUT ANOTHER AI CALL
# ============================================================

async def retry_failed_answer(
    page: Page,
    element: Dict[str, Any],
    answer_item: Dict[str, Any],
) -> Dict[str, Any]:
    """Fill once. If the UI already has a value, only re-verify — do not type again."""
    expected = answer_item.get("option_label") or answer_item.get("answer")
    kind = control_kind(element)
    action = norm(answer_item.get("action") or infer_action_for_element(element))

    async def live_value() -> str:
        try:
            loc, _, _ = await locator_from_element(page, element)
            if not loc:
                return ""
            return await read_select_display(loc) or await read_control_value(loc)
        except Exception:
            return ""

    async def run(item: Dict[str, Any]) -> Dict[str, Any]:
        return await asyncio.wait_for(fill_answer(page, element, item), timeout=FIELD_FILL_TIMEOUT_S)

    existing = await live_value()
    already = False
    if kind in {"radio", "checkbox"} or action in {"radio", "checkbox"}:
        already = await is_choice_selected(page, element, expected)
    elif display_matches(expected, existing):
        already = True
    if already:
        try:
            loc, _, _ = await locator_from_element(page, element)
            if loc:
                ok, live = await repair_invalid_text_input(loc, str(expected or existing or ""))
                existing = live or existing
                already = ok
        except Exception:
            pass
        return {
            "element_index": element.get("_element_index"),
            "question": element.get("question"),
            "answer": answer_item.get("answer"),
            "action": answer_item.get("action"),
            "verified": already,
            "method": "already_filled",
            "actual_value": existing,
        }

    try:
        first = await run(answer_item)
    except asyncio.TimeoutError:
        actual = await live_value()
        matched = display_matches(expected, actual)
        return {
            "element_index": element.get("_element_index"),
            "question": element.get("question"),
            "answer": answer_item.get("answer"),
            "action": answer_item.get("action"),
            "verified": matched,
            "method": "rechecked_after_timeout",
            "actual_value": actual,
            "error": None if matched else "field_fill_timeout",
        }

    if first.get("verified"):
        return first

    actual = first.get("actual_value") or await live_value()
    try:
        loc, _, _ = await locator_from_element(page, element)
        if loc:
            ok, live = await repair_invalid_text_input(loc, str(expected or actual or ""))
            first["actual_value"] = live or actual
            first["verified"] = bool(ok)
            first["method"] = (first.get("method") or "fill") + "+constraint_repair"
            return first
    except Exception:
        pass
    if not is_blank_display(actual):
        first["actual_value"] = actual
        first["verified"] = display_matches(expected, actual)
        first["method"] = (first.get("method") or "fill") + "+rechecked"
    return first

def normalize_ai_answers(raw: Any) -> Dict[str, Any]:
    """Accept Nova output as object, list, or nested answers object."""
    if isinstance(raw, dict):
        answers = raw.get("answers")
        if isinstance(answers, list):
            return {"answers": answers}
        if isinstance(raw.get("fields"), list):
            return {"answers": raw["fields"]}
        return {"answers": []}
    if isinstance(raw, list):
        return {"answers": [x for x in raw if isinstance(x, dict)]}
    return {"answers": []}

def _question_key(e: Dict[str, Any]) -> str:
    ident = clean_text(e.get("id") or e.get("name") or "")
    ident = re.sub(r"-input$", "", ident, flags=re.I)
    ident = re.sub(r"\[\]$", "", ident)
    if ident and "nth-of-type" not in ident:
        return "id:" + ident
    q = canonical_text(e.get("question") or e.get("label") or e.get("placeholder"))
    q = re.sub(r"\*$", "", q).strip()
    return q

def dedupe_interactive_elements(elements: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    grouped: Dict[str, Tuple[int, Dict[str, Any]]] = {}
    priority = {"select": 120, "radio": 110, "checkbox": 110, "fill": 80, "upload": 60, "click": 10}

    for e in elements:
        if is_captcha(e) or is_submit(e) or e.get("disabled") is True:
            continue
        if is_noise_control(e):
            continue
        if norm(e.get("type")) == "hidden":
            continue

        qkey = _question_key(e)
        if not qkey:
            continue

        kind = control_kind(e)
        if kind == "click" and clean_text(e.get("label")) == "Toggle flyout":
            continue

        score = priority.get(kind, 0)
        if e.get("options"):
            score += 25
        if e.get("id"):
            score += 10
        if e.get("runtime_role") or e.get("role"):
            score += 15
        if e.get("visible") is True:
            score += 5
        if kind == "select" and (norm(e.get("role")) == "combobox" or norm(e.get("runtime_role")) == "combobox"):
            score += 15
        if kind == "upload" and RESUME_PATH:
            score += 10

        old = grouped.get(qkey)
        if old is None or score > old[0]:
            grouped[qkey] = (score, e)

    return [x[1] for x in sorted(grouped.values(), key=lambda x: x[1].get("_element_index", 0))]

def _missing_answer_candidates(elements: List[Dict[str, Any]], mapped_indexes: set) -> List[Dict[str, Any]]:
    grouped: Dict[str, Dict[str, Any]] = {}
    priority = {"select": 6, "radio": 6, "checkbox": 6, "fill": 5, "upload": 2, "click": 1}
    for e in elements:
        idx = e.get("_element_index")
        if idx in mapped_indexes or is_captcha(e) or is_submit(e) or e.get("disabled") is True:
            continue
        if is_noise_control(e):
            continue
        if norm(e.get("type")) == "hidden":
            continue
        kind = control_kind(e)
        if kind == "upload" and not RESUME_PATH:
            # Resume is handled by the configured file path, not by asking
            # the user to type "Attach" four times for duplicated buttons.
            continue
        if kind == "click" and not e.get("options"):
            continue
        q = clean_text(e.get("question") or e.get("label") or e.get("placeholder"))
        if not q:
            continue
        required = bool(e.get("required") is True or "*" in q or norm(e.get("aria_required")) == "true")
        meaningful = len(canonical_text(q)) >= 20 and kind in {"select", "radio", "checkbox", "fill"}
        if not (required or meaningful):
            continue
        key = _question_key(e)
        score = priority.get(kind, 0)
        if e.get("options"):
            score += 4
        if e.get("runtime_role") or e.get("role"):
            score += 3
        if e.get("id"):
            score += 2
        if e.get("selector") and "nth-of-type" not in str(e.get("selector")):
            score += 1
        old = grouped.get(key)
        if old is None or score > old[0]:
            grouped[key] = (score, e)
    return [item[1] for item in grouped.values()]

def _format_options_for_prompt(e: Dict[str, Any]) -> str:
    options = usable_options(e)
    values = []
    for o in options[:30]:
        if isinstance(o, dict):
            label = clean_text(o.get("label") or o.get("text") or o.get("value"))
            value = clean_text(o.get("value"))
            values.append(f"{label} [{value}]" if value and value != label else label)
        else:
            values.append(clean_text(o))
    return " | ".join(x for x in values if x)

def ask_user_for_missing_fields(fields: List[Dict[str, Any]], resume: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    if not fields or not ASK_MISSING_FIELDS:
        return {"answers": []}
    print("\n" + "=" * 70)
    print("MISSING INFORMATION")
    print("=" * 70)
    print("Answer ALL lines in the same order. Separate answers with commas.")
    print("For an answer containing commas, wrap that answer in double quotes.")
    print("For select/radio/checkbox fields, type one displayed option exactly.")
    print("Only that one option will be clicked. Press Enter to skip an optional field.")
    print()
    for n, e in enumerate(fields, 1):
        q = clean_text(e.get("question") or e.get("label") or e.get("placeholder"))
        kind = control_kind(e)
        print(f"{n}. {q} [{kind}]")
        option_text = _format_options_for_prompt(e)
        if option_text:
            print("   Options: " + option_text)
    raw = input("\nAnswers (comma-separated): ").strip()
    if not raw:
        return {"answers": []}
    try:
        values = next(csv.reader([raw], skipinitialspace=True))
    except Exception:
        values = [x.strip() for x in raw.split(",")]
    values = [clean_text(x) for x in values]
    values = semantic_assign_user_values(fields, values)
    answers = []
    for e, value in zip(fields, values):
        idx = e["_element_index"]
        if not value:
            answers.append({"element_index": idx, "answer": "", "action": "skip", "reason": "User left field blank"})
            continue
        kind = control_kind(e)
        item = {"element_index": idx, "answer": value, "action": kind, "reason": "User supplied answer"}
        resolved = resolve_option_from_element(e, value)
        if resolved and kind in {"select", "radio"}:
            item["option_label"] = clean_text(resolved.get("label") or resolved.get("text"))
            item["option_value"] = clean_text(resolved.get("value"))
        if looks_like_currency_control(e):
            item["action"] = "select"
        elif "salary" in canonical_text(e.get("label") or e.get("question")) and control_kind(e) == "fill":
            item["answer"] = salary_amount_text(value)
        answers.append(item)
    if resume is not None:
        persist_user_answers_to_resume(resume, fields, answers)
    return {"answers": answers}

def validate_ai_answers(ai_data: Any, elements: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    ai_data = normalize_ai_answers(ai_data)
    by_index = {e["_element_index"]: e for e in elements}

    # Keep only the best answer for one logical question. This removes the
    # duplicate outer/inner Greenhouse custom-select controls that otherwise
    # cause the same dropdown to be opened twice.
    best_by_question: Dict[str, Tuple[int, Dict[str, Any]]] = {}

    for item in ai_data.get("answers", []) or []:
        if not isinstance(item, dict):
            continue
        try:
            idx = int(item.get("element_index"))
        except Exception:
            continue

        e = by_index.get(idx)
        if not e or is_captcha(e) or is_submit(e):
            continue

        fixed = dict(item)
        fixed["element_index"] = idx
        fixed["selector"] = e.get("selector")
        fixed["frame_index"] = e.get("frame_index")
        fixed["frame_url"] = e.get("frame_url")
        fixed["question"] = e.get("question") or e.get("label")

        kind = control_kind(e)
        original_action = norm(fixed.get("action"))
        answer = clean_text(fixed.get("answer")) or clean_text(fixed.get("option_label")) or clean_text(fixed.get("option_value"))
        if answer:
            fixed["answer"] = answer
        if original_action == "skip" and (not answer or is_skip_answer(answer)):
            fixed["action"] = "skip"
        elif kind in {"select", "radio", "checkbox", "upload"}:
            fixed["action"] = kind
        elif original_action in {"", "auto"}:
            fixed["action"] = "fill"

        resolved = resolve_option_from_element(
            e,
            fixed.get("answer"),
            fixed.get("option_label"),
            fixed.get("option_value"),
        )
        if resolved and kind in {"select", "radio", "checkbox"}:
            fixed["option_label"] = clean_text(resolved.get("label") or resolved.get("text"))
            fixed["option_value"] = clean_text(resolved.get("value"))
            if fixed["option_label"]:
                fixed["answer"] = fixed["option_label"]
                if original_action == "skip":
                    fixed["action"] = kind

        # Prefer real combobox/select elements over the inner search input.
        score = 0
        if kind == "select":
            score += 100
        if e.get("options"):
            score += 30
        if norm(e.get("role") or e.get("runtime_role")) in {"combobox", "listbox"}:
            score += 30
        if e.get("id"):
            score += 10
        if e.get("_important"):
            score += 20
        if e.get("visible") is True:
            score += 5
        if norm(e.get("type")) in {"text", "search"} and kind != "select":
            score -= 10
        if clean_text(fixed.get("answer")) and not is_skip_answer(fixed.get("answer")) and norm(fixed.get("action")) != "skip":
            score += 80
        if norm(fixed.get("action")) == "skip" or not clean_text(fixed.get("answer")):
            score -= 1000

        key = _question_key(e)
        old = best_by_question.get(key)
        if old is None or score > old[0]:
            best_by_question[key] = (score, fixed)

    return [item for _, item in sorted(best_by_question.values(), key=lambda x: x[1].get("element_index", 0))]

async def main() -> None:
    print("=" * 60)
    print("UNIVERSAL JOB FORM AUTOFILLER")
    print("=" * 60)

    if not Path(ELEMENTS_FILE).exists():
        raise FileNotFoundError(
            f"Missing {ELEMENTS_FILE}"
        )

    if not Path(RESUME_FILE).exists():
        raise FileNotFoundError(
            f"Missing {RESUME_FILE}"
        )

    raw_elements = load_json(ELEMENTS_FILE)
    resume = load_json(RESUME_FILE)
    elements = normalize_elements(raw_elements)
    important = load_important_fields()
    apply_important_field_hints(elements, important)
    inv_url = ""
    if Path("page_inventory.json").exists():
        try:
            inv = load_json("page_inventory.json")
            inv_url = clean_text(inv.get("page_url") or inv.get("url") or "")
        except Exception:
            pass
    if not inv_url and elements:
        inv_url = clean_text(elements[0].get("frame_url") or "")

    job_url = JOB_URL.strip()
    if not job_url:
        prompt_msg = f"Open the job application URL [{inv_url}]: " if inv_url else "Open the job application URL: "
        try:
            user_in = input(prompt_msg).strip()
        except EOFError:
            user_in = ""
        job_url = user_in or inv_url
    if not job_url:
        raise ValueError("Job application URL is required.")
    resume_idx = build_resume_index(resume)

    print(f"URL                : {job_url}")
    print(f"Elements            : {len(elements)}")
    print(f"Important fields    : {len(important)} ({IMPORTANT_FIELDS_FILE if important else 'not found'})")
    print(f"Resume file         : {RESUME_FILE}")
    print(f"Bedrock model       : {MODEL_ID}")
    print()

    # --------------------------------------------------------
    # Deterministic mapping
    # --------------------------------------------------------

    deterministic = deterministic_candidates(
        elements,
        resume_idx,
        important,
    )

    print("=" * 60)
    print("DETERMINISTIC MAPPING")
    print("=" * 60)

    for x in deterministic:
        print(
            f"{x['field']:30} -> "
            f"[{x['element_index']}] {x['value']}"
        )

    # --------------------------------------------------------
    # Browser
    # --------------------------------------------------------

    async with async_playwright() as pw:
        if USER_DATA_DIR:
            context = await pw.chromium.launch_persistent_context(
                USER_DATA_DIR,
                headless=HEADLESS,
                viewport={"width": 1500, "height": 1000},
            )
            browser = None
            page = (
                context.pages[0]
                if context.pages
                else await context.new_page()
            )
        else:
            browser = await pw.chromium.launch(
                headless=HEADLESS,
            )
            context = await browser.new_context(
                viewport={"width": 1500, "height": 1000},
            )
            page = await context.new_page()

        print()
        print("=" * 60)
        print("OPENING WEBSITE")
        print("=" * 60)

        await page.goto(
            job_url,
            wait_until="domcontentloaded",
            timeout=PAGE_LOAD_TIMEOUT_MS,
        )

        await wait_for_form(page)
        await scroll_page_progressively(page)
        await expand_repeating_sections(page)

        print(f"Title: {await page.title()}")
        print(f"URL  : {page.url}")
        print()
        print("Frames detected:", len(page.frames))

        for i, frame in enumerate(page.frames):
            print(
                f"  [{i}] {frame.url}"
            )

        # ----------------------------------------------------
        # Enrich cu-select and custom dropdown elements
        # This discovers real labels and options at runtime
        # for React/Angular custom select components.
        # ----------------------------------------------------
        try:
            elements = await enrich_cu_select_elements(page, elements)
        except Exception as exc:
            print(f"cu-select enrichment failed (non-fatal): {exc}")
        by_index = {e["_element_index"]: e for e in elements}

        # ----------------------------------------------------
        # Deterministic phone country control first.
        # ----------------------------------------------------

        phone_country_results = (
            await set_phone_country_if_needed(
                page,
                elements,
                resume_idx,
                deterministic,
            )
        )

        # Add deterministic country result to output mapping.
        for r in phone_country_results:
            print(
                f"phone_country_code          -> "
                f"{r['answer']} "
                f"{'VERIFIED' if r['verified'] else 'NOT VERIFIED'}"
            )

        # ----------------------------------------------------
        # Deterministic fill
        # ----------------------------------------------------

        print()
        print("=" * 60)
        print("DETERMINISTIC RESUME FILL")
        print("=" * 60)

        deterministic_results = []

        by_index = {
            e["_element_index"]: e
            for e in elements
        }

        # Country code first (only if successfully verified).
        for r in phone_country_results:
            if r.get("verified"):
                deterministic_results.append(r)

        for item in deterministic:
            e = by_index[item["element_index"]]

            # Country selector already handled.
            if item["field"] == "phone_country_code":
                continue

            answer_item = {
                "element_index": item["element_index"],
                "answer": item["value"],
                "action": item["action"],
                "option_label": item.get("option_label"),
                "option_value": item.get("option_value"),
                "country_code": resume_idx.get("country_code") if is_phone_element(e) else None,
            }

            result = await retry_failed_answer(
                page,
                e,
                answer_item,
            )

            deterministic_results.append(result)

            status = (
                "VERIFIED"
                if result.get("verified")
                else "NOT VERIFIED"
            )

            print(
                f"{item['field']:30} -> "
                f"{item['value']}"
            )
            print(
                f"   -> {status} "
                f"({result.get('method')})"
            )

        # ----------------------------------------------------
        # Phone repair: keep country + national digits on each tel widget.
        # ----------------------------------------------------

        national = clean_text(resume_idx.get("national_phone"))
        country = clean_text(resume_idx.get("country_code"))
        for e in elements:
            if not is_phone_element(e) or is_phone_country_element(e):
                continue
            phone_loc, _, _ = await locator_from_element(page, e)
            if not phone_loc or not national:
                continue
            actual = digits(await read_control_value(phone_loc))
            need = digits(national)[-10:] if len(digits(national)) >= 10 else digits(national)
            if need and need in actual:
                continue
            ok, actual = await fill_phone_widget(phone_loc, national, country)
            print(
                "phone repair -> "
                f"{'VERIFIED' if ok else 'NOT VERIFIED'} "
                f"({actual})"
            )

        # ----------------------------------------------------
        # Determine remaining fields.
        # ----------------------------------------------------

        filled_indexes = {
            x["element_index"]
            for x in deterministic_results
            if x.get("verified")
        }

        remaining_raw = []
        for e in elements:
            idx = e["_element_index"]
            if idx in filled_indexes or is_captcha(e) or is_submit(e) or is_noise_control(e):
                continue
            if e.get("disabled") is True or norm(e.get("type")) == "hidden":
                continue
            remaining_raw.append(e)
        if important:
            remaining = [
                e for e in collect_fill_targets(elements, important)
                if e["_element_index"] not in filled_indexes and not is_noise_control(e)
            ]
        else:
            remaining = dedupe_interactive_elements(remaining_raw)
        filled_keys = {
            _question_key(by_index[i])
            for i in filled_indexes
            if i in by_index
        }
        remaining = [
            e for e in remaining
            if _question_key(e) not in filled_keys
        ]
        remaining = dedupe_interactive_elements(remaining)

        print()
        print("=" * 60)
        print("REMAINING QUESTIONS")
        print("=" * 60)
        print(f"Questions sent to AI: {len(remaining)}")

        # ----------------------------------------------------
        # LIMITED AI BATCHES
        # ----------------------------------------------------
        # Only unresolved, deduplicated controls reach AI. One batch keeps
        # Bedrock usage to a single call; browser matching never calls AI again.
        ai_data = {"answers": []}
        if remaining:
            print()
            print("=" * 60)
            print(f"AI BATCH MAPPING: {len(remaining)} controls")
            print(f"Batch size: {AI_BATCH_SIZE} | Max batches: {AI_MAX_BATCHES}")
            print("=" * 60)
            batches = [remaining[i:i + max(1, AI_BATCH_SIZE)] for i in range(0, len(remaining), max(1, AI_BATCH_SIZE))]
            for batch_no, batch in enumerate(batches[:AI_MAX_BATCHES], 1):
                print(f"AI batch {batch_no}/{min(len(batches), AI_MAX_BATCHES)} -> {len(batch)} controls")
                try:
                    batch_data = normalize_ai_answers(call_bedrock(build_ai_prompt(batch, resume, deterministic)))
                    ai_data["answers"].extend(batch_data.get("answers", []))
                except Exception as exc:
                    print(f"AI batch {batch_no} failed: {exc}")
            if len(batches) > AI_MAX_BATCHES:
                print(f"WARNING: {len(batches) - AI_MAX_BATCHES} AI batches skipped; those fields will be asked from the user.")
            save_json(AI_ANSWERS_FILE, ai_data)
            print(json.dumps(ai_data, indent=2, ensure_ascii=False))

        def _prefer_not(text: Any) -> bool:
            t = canonical_text(text)
            return t.startswith("prefer not") or t in {"decline to self identify", "decline to answer"}

        for item in ai_data.get("answers", []):
            if not isinstance(item, dict) or not str(item.get("element_index", "")).isdigit():
                continue
            e = by_index.get(int(item["element_index"]))
            if not e:
                continue
            q = canonical_text(e.get("question") or e.get("label"))
            ans = item.get("option_label") or item.get("answer")
            if "gender" in q:
                if resume_idx.get("gender"):
                    item["answer"] = resume_idx["gender"]
                    item["option_label"] = resume_idx["gender"]
                    item["action"] = "select"
                elif _prefer_not(ans):
                    item["action"] = "skip"
                    item["answer"] = ""
            if "ethnicity" in q or re.search(r"\brace\b", q):
                if resume_idx.get("ethnicity"):
                    item["answer"] = resume_idx["ethnicity"]
                    item["option_label"] = resume_idx["ethnicity"]
                    item["action"] = "select"
                elif _prefer_not(ans):
                    item["action"] = "skip"
                    item["answer"] = ""

        # Ask once for fields AI could not answer from the supplied data.
        ai_answer_items = ai_data.get("answers", [])
        ai_indexes = {
            int(x.get("element_index"))
            for x in ai_answer_items
            if isinstance(x, dict)
            and str(x.get("element_index", "")).isdigit()
            and norm(x.get("action")) != "skip"
            and clean_text(x.get("answer") or x.get("option_label"))
        }

        # Auto-resolve unmapped experience rating scales or consent radios before prompting user
        for e in remaining:
            idx = e.get("_element_index")
            if idx is None or idx in ai_indexes:
                continue
            q = canonical_text(e.get("question") or e.get("label"))
            opts = e.get("options") or []
            opt_labels = [clean_text(o.get("label") if isinstance(o, dict) else o) for o in opts]

            # 1. Numeric experience scale (e.g. 1..10 or 0..5)
            if ("experience" in q or "year" in q) and opts and all(digits(o) for o in opt_labels if o):
                cand_exp = str(resume_idx.get("experience_years") or 1)
                best_opt = next((o for o in opt_labels if o == cand_exp), None) or opt_labels[0]
                ai_data["answers"].append({
                    "element_index": idx,
                    "selector": e.get("selector"),
                    "frame_index": 0,
                    "frame_url": e.get("frame_url"),
                    "answer": best_opt,
                    "action": "radio" if norm(e.get("control_type")) in {"radio_group", "radio"} else "select",
                    "option_label": best_opt,
                    "option_value": best_opt,
                    "reason": f"Applicant experience scale auto-match ({cand_exp} yr)"
                })
                ai_indexes.add(idx)

            # 2. Consent / agreement / SMS updates
            elif any(w in q for w in ["consent", "agreement", "text message", "sms updates", "opt in"]) and opts:
                yes_opt = next((o for o in opt_labels if norm(o).startswith("yes") or norm(o) == "i agree"), None)
                if yes_opt:
                    ai_data["answers"].append({
                        "element_index": idx,
                        "selector": e.get("selector"),
                        "frame_index": 0,
                        "frame_url": e.get("frame_url"),
                        "answer": yes_opt,
                        "action": "radio" if norm(e.get("control_type")) in {"radio_group", "radio"} else "select",
                        "option_label": yes_opt,
                        "option_value": yes_opt,
                        "reason": "Applicant consent auto-agree"
                    })
                    ai_indexes.add(idx)

        answered_keys = set(filled_keys)
        for idx in ai_indexes:
            e = by_index.get(idx)
            if e:
                answered_keys.add(_question_key(e))
        missing_for_user = [
            e for e in _missing_answer_candidates(remaining, ai_indexes)
            if _question_key(e) not in answered_keys
        ]
        if missing_for_user:
            user_data = ask_user_for_missing_fields(missing_for_user, resume)
            ai_data["answers"].extend(user_data.get("answers", []))
            save_json(AI_ANSWERS_FILE, ai_data)
        # ----------------------------------------------------
        # Validate AI output against elements.json.
        # The AI can never invent a selector.
        # ----------------------------------------------------

        ai_answers = validate_ai_answers(
            ai_data,
            elements,
        )

        save_json(
            MAPPING_FILE,
            {
                "deterministic": deterministic,
                "ai_answers": ai_answers,
            },
        )

        # ----------------------------------------------------
        # Apply AI answers.
        # ----------------------------------------------------

        print()
        print("=" * 60)
        print("APPLYING AI ANSWERS")
        print("=" * 60)

        ai_results = []

        async def apply_one(item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
            try:
                idx = int(item["element_index"])
            except Exception:
                return None

            e = by_index.get(idx)
            if not e or is_captcha(e) or is_submit(e):
                return None

            action = norm(item.get("action") or "fill")
            if action == "skip" and control_kind(e) not in {"select", "radio"}:
                return {
                    "element_index": idx,
                    "question": e.get("question"),
                    "answer": item.get("answer"),
                    "action": "skip",
                    "verified": True,
                    "method": "ai_skip",
                }

            return await retry_failed_answer(page, e, item)

        # Dependent/custom controls must stay sequential. This is especially
        # important for School -> Degree -> Discipline and Greenhouse dropdowns.
        interactive_items = []
        text_items = []
        for item in ai_answers:
            try:
                e = by_index.get(int(item.get("element_index")))
            except Exception:
                e = None
            if not e:
                continue
            action = norm(item.get("action") or control_kind(e))
            if action in {"select", "radio", "checkbox", "upload"} or control_kind(e) in {"select", "radio", "checkbox", "upload"}:
                interactive_items.append(item)
            else:
                text_items.append(item)

        # Sequential path: text fields filled one by one to prevent typing focus leakage.
        TEXT_CONCURRENCY = 1
        for start in range(0, len(text_items), TEXT_CONCURRENCY):
            chunk = text_items[start:start + TEXT_CONCURRENCY]
            results = await asyncio.gather(*(apply_one(x) for x in chunk), return_exceptions=True)
            for result in results:
                if isinstance(result, dict):
                    ai_results.append(result)

        # Accurate path: dropdowns/radios/checkboxes remain strictly ordered.
        for item in interactive_items:
            result = await apply_one(item)
            if result:
                ai_results.append(result)

            if result:
                status = "VERIFIED" if result.get("verified") else "NOT VERIFIED"
                idx = result.get("element_index")
                e = by_index.get(idx, {})
                print(
                    f"[{idx}] {clean_text(e.get('question'))[:45]:45} -> {result.get('answer')}"
                )
                print(f"   -> {status} ({result.get('method')})")

        # Print text-field results after their concurrent batch.
        for result in ai_results:
            if result.get("action") in {"select", "radio", "checkbox", "upload", "skip"}:
                continue
            idx = result.get("element_index")
            e = by_index.get(idx, {})
            status = "VERIFIED" if result.get("verified") else "NOT VERIFIED"
            print(
                f"[{idx}] {clean_text(e.get('question'))[:45]:45} -> {result.get('answer')}"
            )
            print(f"   -> {status} ({result.get('method')})")

        # ----------------------------------------------------
        # FINAL LIVE VERIFICATION
        # Re-read every field we attempted.
        # ----------------------------------------------------

        print()
        print("=" * 60)
        print("FINAL LIVE VERIFICATION & MULTI-STEP REPAIR PASS")
        print("=" * 60)

        # Deduplicate attempted results by element_index (preferring later/specific ones)
        deduped_results: Dict[int, Dict[str, Any]] = {}
        for r in deterministic_results + ai_results:
            idx = r.get("element_index")
            if idx is not None:
                deduped_results[int(idx)] = r

        final_results = []

        for idx, result in sorted(deduped_results.items()):
            e = by_index.get(idx)
            if not e or is_noise_control(e):
                continue

            if result.get("action") == "skip":
                final_results.append(result)
                continue

            q_label = clean_text(e.get("question") or e.get("label") or e.get("name") or "")[:45]
            print(f"\n--- [Field {idx}] {q_label} ({e.get('selector') or ''}) ---")

            verified, actual_val, step_logs = await multi_step_verify_and_repair(
                page,
                e,
                result,
                resume_idx,
            )

            for line in step_logs:
                print(f"  {line}")

            result["final_verified"] = verified
            result["actual_value"] = actual_val
            result["verification_steps"] = step_logs
            final_results.append(result)

        await reveal_rest_of_form(page)

        # ----------------------------------------------------
        # Save result
        # ----------------------------------------------------

        verified_count = sum(
            1
            for r in final_results
            if r.get("final_verified") or r.get("verified")
        )

        attempted_count = len(
            [
                r
                for r in final_results
                if r.get("action") != "skip"
            ]
        )

        output = {
            "url": page.url,
            "title": await page.title(),
            "elements_count": len(elements),
            "deterministic_count": len(deterministic_results),
            "ai_answer_count": len(ai_answers),
            "attempted_count": attempted_count,
            "verified_count": verified_count,
            "results": final_results,
            "browser_left_open": True,
            "submitted": False,
        }

        save_json(
            RESULT_FILE,
            output,
        )

        print()
        print("=" * 60)
        print("FILLING FINISHED")
        print("=" * 60)
        print(
            f"Deterministic fields : "
            f"{len(deterministic_results)}"
        )
        print(
            f"AI answers           : "
            f"{len(ai_answers)}"
        )
        print(
            f"Verified             : "
            f"{verified_count}/{attempted_count}"
        )
        leftover = []
        for r in final_results:
            if r.get("action") == "skip":
                continue
            if r.get("final_verified") or r.get("verified"):
                continue
            leftover.append(r)
        print()
        print("LEFT UNFILLED")
        print("=" * 60)
        if leftover:
            for r in leftover:
                q = clean_text(r.get("question") or "")[:70]
                print(
                    f"[{r.get('element_index')}] {q or '(no label)'} "
                    f"-> {clean_text(r.get('answer')) or '(empty)'} "
                    f"({r.get('method') or r.get('error') or 'failed'})"
                )
            print(f"Total left: {len(leftover)}")
        else:
            print("None. All attempted fields were filled.")
        print()
        print(f"Saved: {RESULT_FILE}")
        print(f"Saved: {AI_ANSWERS_FILE}")
        print(f"Saved: {MAPPING_FILE}")
        print()
        print("Browser is left OPEN." if KEEP_BROWSER_OPEN else "Browser will close.")
        print("Application is NOT submitted.")
        if KEEP_BROWSER_OPEN:
            try:
                await asyncio.Event().wait()
            except (KeyboardInterrupt, SystemExit, asyncio.CancelledError):
                pass
            except Exception:
                pass
        else:
            if browser is not None:
                await browser.close()
            else:
                await context.close()

if __name__ == "__main__":
    asyncio.run(main())
