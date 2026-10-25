import os
import re
import json
import time
import asyncio
from difflib import SequenceMatcher
from datetime import datetime, date
import boto3
from playwright.async_api import (async_playwright, Page, Frame, Locator)

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass


RESUME_FILE = "resume_imp_content.json"
AWS_REGION = os.getenv("AWS_REGION", "us-east-1")
MODEL_ID = os.getenv("BEDROCK_MODEL_ID", "us.amazon.nova-pro-v1:0")

LOCATOR_TIMEOUT_MS = 2500
VERIFY_TIMEOUT_MS = 1500
PAGE_LOAD_TIMEOUT_MS = 60000

COUNTRY_NAME_TO_DIAL = {
    "india": "91", "indian": "91",
    "united states": "1", "usa": "1", "us": "1", "united states of america": "1",
    "canada": "1", "canadian": "1",
    "united kingdom": "44", "uk": "44", "great britain": "44", "britain": "44", "british": "44",
    "germany": "49", "german": "49", "deutschland": "49",
    "france": "33", "french": "33",
    "australia": "61", "australian": "61",
    "japan": "81", "japanese": "81",
    "china": "86", "chinese": "86",
    "singapore": "65", "singaporean": "65",
    "united arab emirates": "971", "uae": "971", "dubai": "971",
    "netherlands": "31", "dutch": "31", "holland": "31",
    "switzerland": "41", "swiss": "41",
    "sweden": "46", "swedish": "46",
    "spain": "34", "spanish": "34",
    "italy": "39", "italian": "39",
    "brazil": "55", "brazilian": "55",
    "mexico": "52", "mexican": "52",
    "south africa": "27",
    "south korea": "82", "korea": "82",
    "pakistan": "92", "pakistani": "92",
    "bangladesh": "880", "bangladeshi": "880",
    "sri lanka": "94", "sri lankan": "94",
    "nepal": "977", "nepalese": "977",
    "new zealand": "64",
    "ireland": "353", "irish": "353",
    "poland": "48", "polish": "48",
    "philippines": "63", "filipino": "63",
    "malaysia": "60", "malaysian": "60",
    "indonesia": "62", "indonesian": "62",
    "vietnam": "84", "vietnamese": "84",
    "saudi arabia": "966", "saudi": "966",
    "egypt": "20", "egyptian": "20",
    "nigeria": "234", "nigerian": "234",
    "kenya": "254", "kenyan": "254",
    "argentina": "54",
    "russia": "7", "russian": "7",
    "norway": "47", "norwegian": "47",
    "denmark": "45", "danish": "45",
    "finland": "358", "finnish": "358",
    "austria": "43", "austrian": "43",
    "belgium": "32", "belgian": "32",
    "portugal": "351", "portuguese": "351",
    "greece": "30", "greek": "30",
    "turkey": "90", "turkish": "90",
    "israel": "972", "israeli": "972",
}

DIAL_TO_ISO = {
    "91": "in", "1": "us", "44": "gb", "971": "ae", "61": "au",
    "49": "de", "33": "fr", "81": "jp", "86": "cn", "65": "sg",
    "31": "nl", "41": "ch", "46": "se", "34": "es", "39": "it",
    "55": "br", "52": "mx", "27": "za", "82": "kr", "92": "pk",
    "880": "bd", "94": "lk", "977": "np", "64": "nz", "353": "ie",
    "48": "pl", "63": "ph", "60": "my", "62": "id", "84": "vn",
    "966": "sa", "20": "eg", "234": "ng", "254": "ke", "54": "ar",
    "7": "ru", "47": "no", "45": "dk", "358": "fi", "43": "at",
    "32": "be", "351": "pt", "30": "gr", "90": "tr", "972": "il",
}

DIAL_TO_NAMES = {
    "91": ["india"], "1": ["united states", "usa", "canada"], "44": ["united kingdom", "uk"],
    "971": ["united arab emirates", "uae"], "61": ["australia"], "49": ["germany"],
    "33": ["france"], "81": ["japan"], "86": ["china"], "65": ["singapore"],
    "31": ["netherlands"], "41": ["switzerland"], "46": ["sweden"], "34": ["spain"],
    "39": ["italy"], "55": ["brazil"], "52": ["mexico"], "27": ["south africa"],
    "82": ["south korea", "korea"], "92": ["pakistan"], "880": ["bangladesh"],
    "94": ["sri lanka"], "977": ["nepal"], "64": ["new zealand"], "353": ["ireland"],
    "48": ["poland"], "63": ["philippines"], "60": ["malaysia"], "62": ["indonesia"],
    "84": ["vietnam"], "966": ["saudi arabia"], "20": ["egypt"], "234": ["nigeria"],
    "254": ["kenya"], "54": ["argentina"], "7": ["russia"], "47": ["norway"],
    "45": ["denmark"], "358": ["finland"], "43": ["austria"], "32": ["belgium"],
    "351": ["portugal"], "30": ["greece"], "90": ["turkey"], "972": ["israel"],
}

DIAL_TO_PRIMARY_COUNTRY = {
    "91": "India", "1": "United States", "44": "United Kingdom", "971": "United Arab Emirates",
    "61": "Australia", "49": "Germany", "33": "France", "81": "Japan", "86": "China",
    "65": "Singapore", "31": "Netherlands", "41": "Switzerland", "46": "Sweden",
    "34": "Spain", "39": "Italy", "55": "Brazil", "52": "Mexico", "27": "South Africa",
    "82": "South Korea", "92": "Pakistan", "880": "Bangladesh", "94": "Sri Lanka",
    "977": "Nepal", "64": "New Zealand", "353": "Ireland", "48": "Poland",
    "63": "Philippines", "60": "Malaysia", "62": "Indonesia", "84": "Vietnam",
    "966": "Saudi Arabia", "20": "Egypt", "234": "Nigeria", "254": "Kenya",
    "54": "Argentina", "7": "Russia", "47": "Norway", "45": "Denmark",
    "358": "Finland", "43": "Austria", "32": "Belgium", "351": "Portugal",
    "30": "Greece", "90": "Turkey", "972": "Israel",
}

DIAL_TO_NATIONALITY = {
    "91": "Indian", "1": "American", "44": "British", "971": "Emirati",
    "61": "Australian", "49": "German", "33": "French", "81": "Japanese",
    "86": "Chinese", "65": "Singaporean", "31": "Dutch", "41": "Swiss",
    "46": "Swedish", "34": "Spanish", "39": "Italian", "55": "Brazilian",
    "52": "Mexican", "27": "South African", "82": "South Korean", "92": "Pakistani",
    "880": "Bangladeshi", "94": "Sri Lankan", "977": "Nepalese", "64": "New Zealander",
    "353": "Irish", "48": "Polish", "63": "Filipino", "60": "Malaysian",
    "62": "Indonesian", "84": "Vietnamese", "966": "Saudi", "20": "Egyptian",
    "234": "Nigerian", "254": "Kenyan", "54": "Argentine", "7": "Russian",
    "47": "Norwegian", "45": "Danish", "358": "Finnish", "43": "Austrian",
    "32": "Belgian", "351": "Portuguese", "30": "Greek", "90": "Turkish",
    "972": "Israeli",
}

DIAL_TO_TIMEZONE = {
    "91": "Asia/Kolkata", "1": "America/New_York", "44": "Europe/London",
    "971": "Asia/Dubai", "61": "Australia/Sydney", "49": "Europe/Berlin",
    "33": "Europe/Paris", "81": "Asia/Tokyo", "86": "Asia/Shanghai",
    "65": "Asia/Singapore", "31": "Europe/Amsterdam", "41": "Europe/Zurich",
    "46": "Europe/Stockholm", "34": "Europe/Madrid", "39": "Europe/Rome",
    "55": "America/Sao_Paulo", "52": "America/Mexico_City", "27": "Africa/Johannesburg",
    "82": "Asia/Seoul", "92": "Asia/Karachi", "880": "Asia/Dhaka",
    "94": "Asia/Colombo", "977": "Asia/Kathmandu", "64": "Pacific/Auckland",
    "353": "Europe/Dublin", "48": "Europe/Warsaw", "63": "Asia/Manila",
    "60": "Asia/Kuala_Lumpur", "62": "Asia/Jakarta", "84": "Asia/Ho_Chi_Minh",
    "966": "Asia/Riyadh", "20": "Africa/Cairo", "234": "Africa/Lagos",
    "254": "Africa/Nairobi", "54": "America/Argentina/Buenos_Aires", "7": "Europe/Moscow",
    "47": "Europe/Oslo", "45": "Europe/Copenhagen", "358": "Europe/Helsinki",
    "43": "Europe/Vienna", "32": "Europe/Brussels", "351": "Europe/Lisbon",
    "30": "Europe/Athens", "90": "Europe/Istanbul", "972": "Asia/Jerusalem",
}

MONTH_NAMES_LIST = [
     "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December"
]
class form_filler:
    def __init__(self,url,page):
        self.url = url
        self.page = page
        self._CURRENT_RESUME_INDEX = {}
        
    def clean_text(self,value):
        if value is None:
            return ""
        return re.sub(r"\s+", " ", str(value)).strip()

    def digits(self,value):
        return re.sub(r"\D", "", str(value or ""))

    def salary_amount_text(self,value):
        t = self.clean_text(value)
        m = re.search(r"(\d+(?:\.\d+)?)\s*(lpa|lacs|lakhs)?", t, flags=re.I)
        return m.group(1) if m else t

    def norm(self,value):
        s = self.self.clean_text(value).casefold()
        s = re.sub(r"[^a-z0-9+@._%/-]+", " ", s)
        return re.sub(r"\s+", " ", s).strip()

    def normalize_field_key(self,key):
        k = self.norm(key)
        k = re.sub(r"\[\]$", "", k)
        k = re.sub(r"--\d+$", "", k)
        return k
    
    def is_meta_instruction(self,val):
        if not val:
            return False
        s = str(val).strip().casefold()
        meta_phrases = [
            "answer yourself", "decide yourself", "ask ai", "based on my resume",
            "based on resume", "refer to resume", "as per resume", "check resume",
            "evaluate from resume"
        ]
        return any(p in s for p in meta_phrases)   
        
    def parse_dob_components(self,dob_str):
        if not dob_str:
            return ("", "", "", "")
        s = str(dob_str).strip()
        for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y", "%m/%d/%Y", "%d %b %Y", "%d %B %Y", "%B %d, %Y", "%b %d, %Y"):
            try:
                dt = datetime.strptime(s, fmt)
                d = str(dt.day)
                m_num = str(dt.month)
                m_name = MONTH_NAMES_LIST[dt.month]
                y = str(dt.year)
                return (d, m_num, m_name, y)
            except Exception:
                continue
        m = re.match(r"^(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{4})$", s)
        if m:
            p1, p2, yr = int(m.group(1)), int(m.group(2)), m.group(3)
            d, m_val = (str(p1), p2) if p1 > 12 >= p2 else (str(p1), p2)
            m_name = MONTH_NAMES_LIST[m_val] if 1 <= m_val <= 12 else str(m_val)
            return (d, str(m_val), m_name, yr)
        return ("", "", "", "")    
        
    def ensure_https_url(self,value):
        s = self.self.clean_text(value)
        if not s:
            return ""
        if re.match(r"^https?://", s, re.I):
            return s
        if s.startswith("//"):
            return "https:" + s
        return "https://" + s.lstrip("/")
        
        
    def build_resume_index(self, resume):
        p = resume.get("prompt", {})
        personal = p.get("personal_info", {})
        supplied = p.get("user_supplied") if isinstance(p.get("user_supplied"), dict) else {}

        full_name = self.self.clean_text(personal.get("full_name"))
        parts = full_name.split()

        first_name = parts[0] if parts else ""
        last_name = parts[-1] if len(parts) > 1 else ""
        middle_name = " ".join(parts[1:-1]) if len(parts) > 2 else ""

        education = p.get("education", []) or []
        work = p.get("work_experience", []) or []
        skills = p.get("skills", {}) or {}
        projects = p.get("projects", []) or []
        grad = education[0] if education else {}
        latest_work = work[0] if work else {}

        primary_skills = {}
        for key , skill in skills.items(): 
            if skill is not None:
                primary_skills[key] = skill

        all_skills = [primary_skills]
        
        pct_parts = []
        for edu in reversed(education):
            deg_name = self.self.clean_text(edu.get("degree"))
            sp = self.self.clean_text(edu.get("score_percentage"))
            gpa = self.self.clean_text(edu.get("gpa"))
            short_lbl = deg_name
            deg_norm = deg_name.lower()
            if "10" in deg_norm or ("secondary" in deg_norm and "senior" not in deg_norm) or "ssc" in deg_norm:
                short_lbl = "10th"
            elif "12" in deg_norm or "senior secondary" in deg_norm or "hsc" in deg_norm or "intermediate" in deg_norm:
                short_lbl = "12th"
            score_str = ""
            if sp:
                score_str = f"{sp}%" if "%" not in sp else sp
            elif gpa:
                score_str = f"{gpa} CGPA" if "cgpa" not in gpa.lower() and "gpa" not in gpa.lower() else gpa
            if short_lbl and score_str:
                pct_parts.append(f"{short_lbl}: {score_str}")
        all_percentages = ", ".join(pct_parts) if pct_parts else ""

        phone = self.self.clean_text(personal.get("phone"))
        phone_digits = self.digits(phone)

        country_code = self.self.clean_text(personal.get("country_code") or personal.get("dial_code") or "")
        if country_code and not country_code.startswith("+"):
            country_code = "+" + country_code
        national_phone = phone
        if phone.startswith("+"):
            m = re.match(r"^\+(\d{1,3})(?:[\s-]+(.*))?$", phone)
            if m:
                if not country_code:
                    country_code = "+" + m.group(1)
                rest = self.self.clean_text(m.group(2) or "")
                national_phone = rest if rest else phone
        national_digits = self.digits(national_phone)
        cc_dig = self.digits(country_code)
        if cc_dig and self.digits(phone).startswith(cc_dig) and len(self.digits(phone)) > len(cc_dig):
            national_phone = self.digits(phone)[len(cc_dig):]
        elif len(national_digits) >= 10:
            national_phone = national_digits[-10:] if country_code else national_digits

        dob = self.self.clean_text(
            personal.get("date_of_birth") or personal.get("dob") or personal.get("DOB")
            or p.get("date_of_birth") or p.get("dob") or p.get("DOB")
            or resume.get("date_of_birth") or resume.get("dob") or resume.get("DOB")
        )

        experience_years = p.get("total_experience_years", resume.get("year_of_exp", 0))
        current_salary = self.self.clean_text(
            personal.get("current_salary") or personal.get("Current Salary")
            or p.get("current_salary") or p.get("Current Salary")
            or resume.get("current_salary") or resume.get("Current Salary")
        )
        expected_salary = self.self.clean_text(
            personal.get("expected_salary") or personal.get("Expected Salary")
            or p.get("expected_salary") or p.get("Expected Salary")
            or resume.get("expected_salary") or resume.get("Expected Salary")
        )

        gender_str = self.self.clean_text(personal.get("gender") or personal.get("sex"))
        salutation = "Mr." if gender_str.lower() in {"male", "m"} else ("Ms." if gender_str.lower() in {"female", "f"} else "")

        grad_end = self.self.clean_text(grad.get("end_date"))
        m_end_yr = re.search(r"\b(19\d{2}|20\d{2})\b", grad_end)
        if m_end_yr and not grad.get("currently_enrolled") and grad_end.lower() not in {"present", "current", "currently enrolled"}:
            grad_year = m_end_yr.group(1)
        else:
            start_yr_m = re.search(r"\b(19\d{2}|20\d{2})\b", self.self.clean_text(grad.get("start_date")))
            if start_yr_m:
                s_yr = int(start_yr_m.group(1))
                deg_norm = self.self.clean_text(grad.get("degree")).lower()
                if any(k in deg_norm for k in ["master", "mca", "mba", "mtech", "m.sc", "ms", "ma", "m.com"]):
                    grad_year = str(s_yr + 2)
                elif any(k in deg_norm for k in ["b.tech", "btech", "b.e", "bachelor of engineering", "bachelor of technology"]):
                    grad_year = str(s_yr + 4)
                elif any(k in deg_norm for k in ["bca", "bba", "b.sc", "bsc", "b.com", "b.a"]):
                    grad_year = str(s_yr + 3)
                elif "mbbs" in deg_norm:
                    grad_year = str(s_yr + 5)
                else:
                    grad_year = str(s_yr + 4)
            else:
                grad_year = grad_end if grad_end.lower() not in {"present", "current"} else ""

        idx = {
            "full_name": full_name,
            "first_name": first_name,
            "middle_name": middle_name,
            "last_name": last_name,
            "email": self.self.clean_text(personal.get("email")),
            "date_of_birth": dob,
            "dob": dob,
            "salutation": salutation,
            "current_salary": current_salary,
            "expected_salary": expected_salary,
            "phone": phone,
            "phone_digits": phone_digits,
            "country_code": country_code,
            "national_phone": national_phone,
            "linkedin": self.ensure_https_url(personal.get("linkedin")),
            "github": self.ensure_https_url(personal.get("github")),
            "portfolio": self.ensure_https_url(personal.get("portfolio") or personal.get("website") or personal.get("portfolio_url") or supplied.get("portfolio") or supplied.get("portfolio_url")),
            "portfolio_url": self.ensure_https_url(personal.get("portfolio") or personal.get("website") or personal.get("portfolio_url") or supplied.get("portfolio") or supplied.get("portfolio_url")),
            "gender": gender_str,
            "ethnicity": self.self.clean_text(personal.get("ethnicity") or personal.get("race") or personal.get("race_or_ethnicity")),
            "location": self.self.clean_text(latest_work.get("location") or grad.get("location")),
            "current_location": self.self.clean_text(latest_work.get("location") or grad.get("location")),
            "city": self.self.clean_text(personal.get("city") or personal.get("location") or latest_work.get("location") or grad.get("location")),
            "state": self.self.clean_text(personal.get("state") or personal.get("province") or personal.get("region")),
            "professional_summary": self.self.clean_text(p.get("professional_summary")),
            "experience_years": str(experience_years),
            "graduation_college": self.self.clean_text(grad.get("institution")),
            "graduation_year": grad_year,
            "graduation_branch": self.self.clean_text(grad.get("field_of_study")),
            "graduation_degree": self.self.clean_text(grad.get("degree")),
            "graduation_cgpa": self.self.clean_text(grad.get("gpa")),
            "company_name": self.self.clean_text(latest_work.get("company")),
            "job_title": self.self.clean_text(latest_work.get("job_title")),
            "designation": self.self.clean_text(latest_work.get("job_title")),
            "work_location": self.self.clean_text(latest_work.get("location")),
            "work_start_date": self.self.clean_text(latest_work.get("start_date")),
            "work_end_date": self.self.clean_text(latest_work.get("end_date")),
            "is_currently_working": "true" if latest_work.get("currently_working") else "false",
            "education_degree": self.self.clean_text(grad.get("degree")),
            "education_branch": self.self.clean_text(grad.get("field_of_study")),
            "education_college": self.self.clean_text(grad.get("institution")),
            "education_location": self.self.clean_text(grad.get("location")),
            "education_start_date": self.self.clean_text(grad.get("start_date")),
            "education_end_date": (
                self.self.clean_text(grad.get("end_date")) or ("Present" if grad.get("currently_enrolled") else grad_year)
            ),
            "start_date": self.self.clean_text(grad.get("start_date") or latest_work.get("start_date")),
            "end_date": (
                self.self.clean_text(grad.get("end_date")) or ("Present" if grad.get("currently_enrolled") else (self.self.clean_text(latest_work.get("end_date")) or grad_year))
            ),
            "12th_percentage": "",
            "10th_percentage": "",
            "all_percentages": all_percentages,
            "current_employer": "",
            "employer_name": self.self.clean_text(latest_work.get("company")),
            "current_designation": "",
            "previous_employer": self.self.clean_text(latest_work.get("company")),
            "previous_designation": self.self.clean_text(latest_work.get("job_title")),
            "primary_skills": ", ".join(primary_skills),
            "country": "",
            "nationality": "",
            "timezone": "",
            "companies_count": str(len(work)) if work else "",
            "agree_yes": "I agree",
        }

        res_country = self.self.clean_text(
            personal.get("country") or personal.get("Country") or personal.get("current_country")
            or p.get("country") or p.get("Country") or resume.get("country") or resume.get("Country")
        )
        res_nat = self.self.clean_text(
            personal.get("nationality") or personal.get("Nationality") or personal.get("citizenship")
            or p.get("nationality") or resume.get("nationality")
        )
        res_tz = self.self.clean_text(
            personal.get("timezone") or personal.get("time_zone")
            or p.get("timezone") or resume.get("timezone")
        )

        dial = self.digits(country_code)
        if not res_country and dial in DIAL_TO_PRIMARY_COUNTRY:
            res_country = DIAL_TO_PRIMARY_COUNTRY[dial]
        if not res_nat and dial in DIAL_TO_NATIONALITY:
            res_nat = DIAL_TO_NATIONALITY[dial]
        if not res_tz and dial in DIAL_TO_TIMEZONE:
            res_tz = DIAL_TO_TIMEZONE[dial]

        if not country_code and res_country:
            c_dial = COUNTRY_NAME_TO_DIAL.get(res_country.lower())
            if c_dial:
                country_code = "+" + c_dial

        idx["country_code"] = country_code
        idx["country"] = res_country
        idx["nationality"] = res_nat
        idx["timezone"] = res_tz

        d_day, d_m_num, d_m_name, d_year = self.parse_dob_components(dob)
        idx["dob_day"] = d_day
        idx["dob_month"] = d_m_name or d_m_num
        idx["dob_month_num"] = d_m_num
        idx["dob_month_name"] = d_m_name
        idx["dob_year"] = d_year
        idx["prior_worker"] = self.self.clean_text(personal.get("prior_worker") or "No")
        idx["worked_with_us_before"] = self.self.clean_text(personal.get("worked_with_us_before") or "No")
        idx["work_authorization"] = self.self.clean_text(personal.get("work_authorization") or "Yes")
        idx["authorized_to_work"] = self.self.clean_text(personal.get("authorized_to_work") or "Yes")
        idx["disability_status"] = self.self.clean_text(personal.get("disability_status") or "Prefer not to answer")

        if not idx["current_location"] and latest_work.get("location"):
            idx["current_location"] = self.self.clean_text(latest_work.get("location"))
        if not idx.get("city") and idx.get("current_location"):
            idx["city"] = idx["current_location"]

        all_locs = []
        if personal.get("city"): all_locs.append(self.self.clean_text(personal.get("city")))
        if personal.get("location"): all_locs.append(self.self.clean_text(personal.get("location")))
        for w in work:
            if w.get("location"): all_locs.append(self.self.clean_text(w.get("location")))
        for edu in education:
            if edu.get("location"): all_locs.append(self.self.clean_text(edu.get("location")))
        idx["all_locations"] = list(dict.fromkeys(filter(None, all_locs)))

        if not idx.get("state"):
            c_low = (idx.get("city") or "").lower()
            if c_low in {"gurgaon", "gurugram", "faridabad", "panipat", "rohtak", "ambala", "karnal", "sonipat"}:
                idx["state"] = "Haryana"
            elif c_low in {"bangalore", "bengaluru", "mysore", "mysuru", "mangalore"}:
                idx["state"] = "Karnataka"
            elif c_low in {"mumbai", "pune", "nagpur", "thane", "nashik"}:
                idx["state"] = "Maharashtra"
            elif c_low in {"delhi", "new delhi"}:
                idx["state"] = "Delhi"
            elif c_low in {"noida", "greater noida", "ghaziabad", "lucknow", "kanpur", "varanasi", "agra"}:
                idx["state"] = "Uttar Pradesh"
            elif c_low in {"hyderabad", "secunderabad", "warangal"}:
                idx["state"] = "Telangana"
            elif c_low in {"chennai", "coimbatore", "madurai"}:
                idx["state"] = "Tamil Nadu"
            elif c_low in {"kolkata", "calcutta", "howrah"}:
                idx["state"] = "West Bengal"
            elif c_low in {"ahmedabad", "surat", "vadodara", "baroda", "rajkot", "gandhinagar"}:
                idx["state"] = "Gujarat"
            elif c_low in {"jaipur", "udaipur", "jodhpur", "kota"}:
                idx["state"] = "Rajasthan"

        for edu in education:
            deg_str = self.norm(edu.get("degree"))
            val = self.clean_text(edu.get("score_percentage") or edu.get("gpa"))
            if "12" in deg_str or "senior secondary" in deg_str or "hsc" in deg_str or "intermediate" in deg_str:
                if not idx.get("12th_percentage"):
                    idx["12th_percentage"] = val
            elif "10" in deg_str or ("secondary" in deg_str and "senior" not in deg_str) or "ssc" in deg_str or "matric" in deg_str:
                if not idx.get("10th_percentage"):
                    idx["10th_percentage"] = val

        currently_working = False
        for w in work:
            if w.get("currently_working"):
                currently_working = True
                idx["current_employer"] = self.clean_text(w.get("company"))
                idx["current_designation"] = self.clean_text(w.get("job_title"))
                idx["is_currently_working"] = "true"
                break
        idx["currently_working"] = currently_working
        idx["is_fresher"] = (not currently_working) and float(self.digits(experience_years) or 0) <= 1
        if not currently_working:
            idx["current_employer"] = ""
            idx["current_designation"] = ""
            idx["is_currently_working"] = "false"

        supplied = p.get("user_supplied") if isinstance(p.get("user_supplied"), dict) else {}
        idx["salary_currency"] = self.clean_text(personal.get("salary_currency") or supplied.get("salary_currency"))
        if not idx["salary_currency"] and dial == "91":
            idx["salary_currency"] = "INR"
        idx["salary_frequency"] = self.clean_text(personal.get("salary_frequency") or supplied.get("salary_frequency") or "Yearly")
        if idx.get("current_salary"):
            idx["current_salary"] = self.salary_amount_text(idx["current_salary"])
        if idx.get("expected_salary"):
            idx["expected_salary"] = self.salary_amount_text(idx["expected_salary"])
        avail = self.clean_text(personal.get("available_to_join_days") or supplied.get("available_to_join_days"))
        if self.is_meta_instruction(avail):
            avail = ""
        idx["how_did_you_hear"] = self.clean_text(supplied.get("how_did_you_hear") or supplied.get("referral_source"))
        if idx["how_did_you_hear"].lower() == "likedin":
            idx["how_did_you_hear"] = "Linkedin"
        if not idx.get("earliest_joining_date"):
            np_val = self.clean_text(personal.get("notice_period") or supplied.get("notice_period"))
            days = 0
            if avail and avail.isdigit():
                days = int(avail)
            elif np_val and np_val.isdigit():
                days = int(np_val)
            from datetime import datetime, timedelta
            join_dt = datetime.now() + timedelta(days=days)
            idx["earliest_joining_date"] = join_dt.strftime("%Y-%m-%d")
        idx["employee_reference"] = self.clean_text(supplied.get("employee_reference"))
        pref_jobs = resume.get("preffered_job") or []
        idx["job_position"] = self.clean_text(
            supplied.get("role_applying_for_other")
            or (pref_jobs[0] if pref_jobs else "")
            or latest_work.get("job_title")
        )
        for key, val in supplied.items():
            k = self.normalize_field_key(key)
            if self.clean_text(val) and not self.clean_text(idx.get(k)):
                if not self.is_meta_instruction(val):
                    idx[k] = self.clean_text(val)

        if not idx.get("job_status"):
            if currently_working:
                idx["job_status"] = "Serving Notice Period" if idx.get("available_to_join_days") else "Currently working"
            else:
                idx["job_status"] = "Immediately available"
        if not idx.get("reason_for_joining"):
            idx["reason_for_joining"] = self.clean_text(supplied.get("reason_for_joining"))

        for k, v in list(idx.items()):
            if isinstance(v, str) and self.is_meta_instruction(v):
                idx[k] = ""

        idx["_education_records"] = education
        idx["_work_records"] = work

        self._CURRENT_RESUME_INDEX.clear()
        self._CURRENT_RESUME_INDEX.update(idx)

        return idx

    def deterministic_candidates(elements,resume_idx,important = None,resume= None):
        
        candidates = []
        used_elements: set = set()
        important = important or []
        resume = resume or {}
        p = resume.get("prompt", {}) if isinstance(resume, dict) else {}
        edu_records = p.get("education") or [] if isinstance(p, dict) else []
        exp_records = p.get("work_experience") or [] if isinstance(p, dict) else []

        edu_records_sorted = list(edu_records)
        
        # Sort experience newest to oldest
        exp_records_sorted = sorted(exp_records, key=lambda x: str(x.get("start_date") or ""), reverse=True)

        for i, el in enumerate(elements):
            if "_element_index" not in el:
                el["_element_index"] = el.get("element_index", i)
        by_index = {e["_element_index"]: e for e in elements}
        by_selector = {clean_text(e.get("selector")): e for e in elements}

        # --- 1. Important-field mapping ---
        for field in important:
            raw_key = normalize_field_key(field.get("field_key"))
            e = None
            try:
                e = by_index.get(int(field.get("element_index")))
            except Exception:
                e = None
            if e is None:
                e = by_selector.get(clean_text(field.get("selector")))
            if not e or is_noise_control(e):
                continue

            # FIX: Correct the field_key using semantic analysis of selector/question
            key = _correct_field_key_from_semantics(field, e)
            if not key:
                # Unresolvable mapping — skip this field entirely
                continue

            # Detect if element belongs to a repeatable group (e.g. #companyName_0, #degree_1)
            gi = e.get("_group_index")
            if gi is None:
                gi = _detect_group_index(e)
            if gi is None:
                sel_for_gi = clean_text(field.get("selector") or e.get("selector") or "")
                m_gi = re.search(r"[_\[\-](\d+)\]?(?:$|[_\-\.])", sel_for_gi)
                if m_gi:
                    try:
                        gi = int(m_gi.group(1))
                    except Exception:
                        gi = None

            sec = e.get("_section_type")
            if not sec:
                sec = _detect_section_type(e)
            if not sec:
                sel_s = (clean_text(field.get("selector") or "") + " " + clean_text(e.get("selector") or "")).lower()
                if any(k in sel_s for k in ["degree", "branch", "university", "college", "course", "education"]):
                    sec = "education"
                elif any(k in sel_s for k in ["company", "designation", "relieving", "joining", "experience", "employer", "work"]):
                    sec = "experience"

            # FIX: Ignore personal info fields mapped to repeatable sections
            if sec in {"experience", "education"}:
                if key in {"full_name", "first_name", "middle_name", "last_name", "email", "phone", "mobile", "current_location", "linkedin", "github"}:
                    continue
            
            # FIX: Ignore bad field_key mappings if label clearly contradicts it
            label_hay = canonical_text(e.get("label") or e.get("question") or e.get("placeholder") or "")
            if "job title" in label_hay or "designation" in label_hay:
                if key in {"country", "nationality", "gender", "email", "phone"}:
                    continue
                    
            hay = canonical_text(
                " ".join(
                    str(x or "")
                    for x in [
                        field.get("question"), field.get("field_key"),
                        (e or {}).get("question"), (e or {}).get("label"),
                        (e or {}).get("placeholder"),
                    ]
                )
            )
            if key in {"custom_question", "custom_answer"} and (
                "current location" in hay or "present location" in hay or "your location" in hay
            ):
                key = "current_location"

            value = None
            cand_opt_lbl = None
            cand_opt_val = None
            resume_key = IMPORTANT_RESUME_MAP.get(key) or IMPORTANT_RESUME_MAP.get(norm(field.get("field_key") or ""))

            # If repeatable group field, resolve directly from the candidate record!
            if gi is not None and sec in {"education", "experience"}:
                records_to_use = edu_records if sec == "education" else exp_records
                rep_field = e.get("_repeatable_field") or _infer_repeatable_field(e)
                if not rep_field:
                    if key in {"company_name", "company", "employer_name"}: rep_field = "company"
                    elif key in {"job_title", "designation", "title"}: rep_field = "job_title"
                    elif key in {"description", "role", "job_description", "work_description", "responsibilities"}: rep_field = "description"
                    elif key in {"work_start_date", "experience_date_of_joining", "start_date"}: rep_field = "start_date"
                    elif key in {"work_end_date", "date_of_relieving", "end_date", "last_working_day"}: rep_field = "end_date"
                    elif key in {"work_location", "experience_location", "location"}: rep_field = "location"
                    elif key in {"currently_working_here", "is_currently_working", "currently_working"}: rep_field = "currently_working"
                    elif key in {"graduation_degree", "course", "degree"}: rep_field = "degree"
                    elif key in {"graduation_branch", "branch_specialization", "branch", "discipline"}: rep_field = "field_of_study"
                    elif key in {"graduation_college", "university_college", "university", "college", "school"}: rep_field = "institution"
                    elif key in {"education_start_date", "start_date"}: rep_field = "start_date"
                    elif key in {"education_end_date", "education_date_of_completion", "end_date"}: rep_field = "end_date"
                    elif key in {"education_location", "location"}: rep_field = "location"
                    elif key in {"graduation_cgpa", "grade", "gpa", "cgpa", "score"}: rep_field = "grade"

                if 0 <= gi < len(records_to_use):
                    rec = records_to_use[gi]
                    if rep_field in {"currently_working", "currently_working_here", "is_currently_working"}:
                        value = "true" if rec.get("currently_working") else "false"
                    else:
                        value = _record_field_value(rec, rep_field or key)
                        if sec == "education" and rep_field in {"end_date", "end_year"} and (
                            str(value).lower() in {"present", "current", "currently enrolled", ""}
                            or rec.get("currently_enrolled")
                        ):
                            if rep_field == "end_year" and not (norm(e.get("type")) == "date" or e.get("is_datepicker")):
                                start_yr = clean_text(rec.get("start_date"))
                                m_yr = re.search(r"\b(19\d{2}|20\d{2})\b", start_yr)
                                if m_yr:
                                    deg_low = clean_text(rec.get("degree")).lower()
                                    add_yrs = 2 if any(m in deg_low for m in ["master", "mca", "mba", "mtech", "m.sc"]) else 4
                                    value = str(int(m_yr.group(1)) + add_yrs)
                else:
                    value = ""
            else:
                if not resume_key:
                    continue
                value = resume_idx.get(resume_key)

            if is_empty(value):
                continue

            if not e or e["_element_index"] in used_elements:
                continue
            if is_captcha(e) or is_submit(e):
                continue
            if (resume_key == "resume_file_path" or key == "resume") and not looks_like_resume_upload(e):
                continue
            if is_current_employment_question(e, key) and not currently_employed(resume_idx):
                if resume_key in {"current_employer", "current_designation", "company_name",
                                "job_title", "designation", "employer_name"}:
                    value = "NA"
            if (norm(e.get("type")) == "date" or e.get("is_datepicker")) and str(value).lower() in {"immediate", "ready", "urgent", "0"}:
                from datetime import datetime
                value = datetime.now().strftime("%Y-%m-%d")
            elif looks_like_currency_control(e):
                value = resume_idx.get("salary_currency") or value
            elif resume_key in {"current_salary", "expected_salary"}:
                hay_sal = canonical_text(
                    " ".join(str(e.get(k) or "") for k in ("label", "question", "placeholder", "name", "id"))
                )
                val_clean = clean_text(value).upper()
                m_num = re.search(r"(\d+(?:\.\d+)?)", val_clean)
                if m_num:
                    num = float(m_num.group(1))
                    is_lpa = "LPA" in val_clean or "LAC" in val_clean or num <= 100
                    ph_digs = digits(clean_text(e.get("placeholder") or ""))
                    expects_full_annual = len(ph_digs) >= 5 or (
                        not any(w in hay_sal for w in ["lpa", "lakh", "lac", "in lpa", "in lakh"])
                        and any(w in hay_sal for w in ["per annum", "annum", "annual", "ctc", "salary"])
                    )
                    if any(m in hay_sal for m in ["month", "monthly", "per month", "net / month"]):
                        if is_lpa:
                            value = str(int(round((num * 100000) / 12)))
                        else:
                            value = str(int(round(num)))
                    elif expects_full_annual and is_lpa:
                        value = str(int(round(num * 100000)))
                    else:
                        value = str(int(round(num))) if num.is_integer() else str(num)
                else:
                    value = salary_amount_text(value)
            elif looks_like_salary_frequency_control(e):
                value = resume_idx.get("salary_frequency") or value
            elif resume_key == "available_to_join_days" or key == "availability":
                field_label_lower = field_label_text(e).lower()
                if is_meta_instruction(value):
                    continue
                if "week" in field_label_lower:
                    value = "0"
                elif norm(e.get("type")) == "number" or "number" in norm(e.get("control_type")) or "days" in field_label_lower:
                    if str(value).lower() in {"immediate", "immediately", "ready", "urgent"}:
                        value = "0"
                    elif not digits(value):
                        continue
            elif resume_key == "is_currently_working":
                value = "true" if is_positive_answer(value) else "false"
            elif key in {"agreement", "consent", "terms_agreement", "candidate_consent"} or resume_key == "agree_yes":
                if norm(e.get("type")) == "checkbox" or control_kind(e) == "checkbox":
                    value = "true"
            elif is_phone_country_element(e) or looks_like_dial_code_options(e.get("options") or []):
                opts = e.get("options") or []
                cc = resume_idx.get("country_code") or ""
                cname = resume_idx.get("country") or (DIAL_TO_PRIMARY_COUNTRY.get(digits(cc)) if cc else "")
                if not cc and cname and cname.lower() in COUNTRY_NAME_TO_DIAL:
                    cc = "+" + COUNTRY_NAME_TO_DIAL[cname.lower()]
                iso = DIAL_TO_ISO.get(digits(cc), "")
                best_opt = None
                if opts:

                    for o in opts:
                        ol = clean_text(o.get("label") if isinstance(o, dict) else o).lower()
                        ov = clean_text(o.get("value") if isinstance(o, dict) else o).lower()
                        if cname and (cname.lower() == ol or cname.lower() == ov):
                            best_opt = o
                            break
                    # 2. Exact match on ISO code (e.g. "IN", "US", "DE")
                    if not best_opt and iso:
                        for o in opts:
                            ol = clean_text(o.get("label") if isinstance(o, dict) else o).lower()
                            ov = clean_text(o.get("value") if isinstance(o, dict) else o).lower()
                            if iso.lower() == ov or iso.lower() == ol:
                                best_opt = o
                                break
                    # 3. Match on dial code (e.g. "+91" or "91")
                    if not best_opt and digits(cc):
                        for o in opts:
                            ol = clean_text(o.get("label") if isinstance(o, dict) else o).lower()
                            ov = clean_text(o.get("value") if isinstance(o, dict) else o).lower()
                            if digits(cc) == digits(ov) or digits(cc) == digits(ol) or f"+{digits(cc)}" in ol:
                                best_opt = o
                                break
                if best_opt and isinstance(best_opt, dict):
                    value = best_opt.get("value") if best_opt.get("value") is not None else best_opt.get("label")
                    cand_opt_lbl = best_opt.get("label")
                    cand_opt_val = best_opt.get("value")
                else:
                    value = cname if not looks_like_dial_code_options(opts) else cc
                    cand_opt_lbl = str(value)
                    cand_opt_val = str(value)
                key = "phone_country_code"
                resume_key = "phone_country_code"
            elif resume_key in {"country", "nationality"} or key in {"country", "nationality"}:
                opts = e.get("options") or []
                cc = resume_idx.get("country_code") or ""
                cname = resume_idx.get("country") or (DIAL_TO_PRIMARY_COUNTRY.get(digits(cc)) if cc else "")
                nat = resume_idx.get("nationality") or (DIAL_TO_NATIONALITY.get(digits(cc)) if cc else "")
                best_opt = None
                if opts:
                    for o in opts:
                        ol = clean_text(o.get("label") if isinstance(o, dict) else o).lower()
                        ov = clean_text(o.get("value") if isinstance(o, dict) else o).lower()
                        if cname and (cname.lower() == ol or cname.lower() == ov):
                            best_opt = o
                            break
                        if nat and (nat.lower() == ol or nat.lower() == ov):
                            best_opt = o
                            break
                if best_opt and isinstance(best_opt, dict):
                    value = best_opt.get("value") if best_opt.get("value") is not None else best_opt.get("label")
                    cand_opt_lbl = best_opt.get("label")
                    cand_opt_val = best_opt.get("value")
                else:
                    value = cname

            if is_empty(value) or is_meta_instruction(value):
                continue

            cand_field = key or resume_key or field.get("field_key")
            is_select_kind = norm(e.get("tag")) == "select" or control_kind(e) in {"select", "radio", "checkbox"}
            if not is_select_kind and (
                norm(e.get("type")) == "url"
                or resume_key in {"linkedin", "github", "portfolio", "portfolio_url", "website", "blog"}
                or "portfolio" in str(cand_field).lower() or "github" in str(cand_field).lower() or ("linkedin" in str(cand_field).lower() and "source" not in str(cand_field).lower())
                or re.search(r"\b(url|website|portfolio link|profile link|github link|linkedin link)\b", norm(e.get("question") or ""))
            ):
                if cand_field in {"full_name", "first_name", "last_name", "name"} or not (str(value).startswith("http") or "." in str(value)):
                    sel_l = clean_text(e.get("selector") or "").lower()
                    q_l = clean_text(e.get("question") or "").lower()
                    if "linkedin" in sel_l or "linkedin" in q_l:
                        value = resume_idx.get("linkedin") or value
                        cand_field = "linkedin"
                    elif "github" in sel_l or "github" in q_l:
                        value = resume_idx.get("github") or value
                        cand_field = "github"
                    else:
                        value = resume_idx.get("portfolio") or resume_idx.get("linkedin") or value
                        cand_field = "portfolio"
                value = ensure_https_url(value)

            # Validate that choice elements actually have an option matching the value/option label/value
            opts = e.get("options") or []
            if opts and (control_kind(e) in {"radio", "checkbox", "select"} or norm(e.get("control_type")) in {"radio_group", "checkbox_group"}):
                opt_labels = [clean_text(o.get("label") if isinstance(o, dict) else o).lower() for o in opts]
                opt_values = [clean_text(o.get("value") if isinstance(o, dict) else o).lower() for o in opts]
                val_clean = clean_text(value).lower()
                cand_lbl_clean = clean_text(cand_opt_lbl or "").lower()
                cand_val_clean = clean_text(cand_opt_val or "").lower()
                if not any(x in opt_labels or x in opt_values for x in [val_clean, cand_lbl_clean, cand_val_clean] if x):
                    snapped_opt = snap_answer_to_options(opts, value)
                    if snapped_opt:
                        cand_opt_lbl = clean_text(snapped_opt.get("label") if isinstance(snapped_opt, dict) else snapped_opt)
                        cand_opt_val = clean_text(snapped_opt.get("value") if isinstance(snapped_opt, dict) else cand_opt_lbl)
                        value = cand_opt_lbl
                    else:
                        # Deterministic value doesn't match any option; leave for Bedrock AI answering
                        continue

            action = "select" if is_phone_country_element(e) else ("check" if (key in {"agreement", "consent", "terms_agreement", "candidate_consent"} or resume_key == "agree_yes") and (norm(e.get("type")) == "checkbox" or control_kind(e) == "checkbox") else infer_action_for_element(e))
            candidates.append({
                "element_index": e["_element_index"],
                "field": cand_field,
                "value": str(value),
                "option_label": cand_opt_lbl if cand_opt_lbl is not None else (str(value) if e.get("options") else None),
                "option_value": cand_opt_val if cand_opt_val is not None else (str(value) if e.get("options") else None),
                "action": action,
                "source": "important_data_fields",
                "confidence": 120,
            })
            used_elements.add(e["_element_index"])

        # --- 2. Phone country code ---
        for e in elements:
            if is_phone_country_element(e):
                if e["_element_index"] in used_elements:
                    continue
                opts = e.get("options") or []
                cc = resume_idx.get("country_code") or ""
                cname = resume_idx.get("country") or (DIAL_TO_PRIMARY_COUNTRY.get(digits(cc)) if cc else "")
                if not cc and cname and cname.lower() in COUNTRY_NAME_TO_DIAL:
                    cc = "+" + COUNTRY_NAME_TO_DIAL[cname.lower()]
                iso = DIAL_TO_ISO.get(digits(cc), "")
                best_opt = None
                if opts:
                    for o in opts:
                        ol = clean_text(o.get("label") if isinstance(o, dict) else o).lower()
                        ov = clean_text(o.get("value") if isinstance(o, dict) else o).lower()
                        if cname and (cname.lower() == ol or cname.lower() == ov):
                            best_opt = o; break
                    if not best_opt and iso:
                        for o in opts:
                            ol = clean_text(o.get("label") if isinstance(o, dict) else o).lower()
                            ov = clean_text(o.get("value") if isinstance(o, dict) else o).lower()
                            if iso.lower() == ov or iso.lower() == ol:
                                best_opt = o; break
                    if not best_opt and digits(cc):
                        for o in opts:
                            ol = clean_text(o.get("label") if isinstance(o, dict) else o).lower()
                            ov = clean_text(o.get("value") if isinstance(o, dict) else o).lower()
                            if digits(cc) == digits(ov) or digits(cc) == digits(ol) or f"+{digits(cc)}" in ol:
                                best_opt = o; break
                if best_opt and isinstance(best_opt, dict):
                    p_val = best_opt.get("value") if best_opt.get("value") is not None else best_opt.get("label")
                    p_lbl = best_opt.get("label")
                else:
                    p_val = cname if not looks_like_dial_code_options(opts) else cc
                    p_lbl = str(p_val)
                candidates.append({
                    "element_index": e["_element_index"],
                    "field": "phone_country_code",
                    "value": str(p_val),
                    "option_label": str(p_lbl) if opts else None,
                    "option_value": str(p_val) if opts else None,
                    "action": "select",
                    "source": "resume",
                    "confidence": 100,
                })
                used_elements.add(e["_element_index"])
                break

        # --- 3. Auto-check consent checkboxes ---
        for e in elements:
            idx = e["_element_index"]
            if idx in used_elements:
                continue
            is_chk = (
                norm(e.get("type")) == "checkbox"
                or norm(e.get("field_type")) == "checkbox"
                or norm(e.get("control_type")) == "checkbox"
                or (norm(e.get("tag")) == "input" and norm(e.get("type")) == "checkbox")
            )
            if not is_chk:
                continue
            ident = " ".join(norm(e.get(k)) for k in ("id", "name", "aria_label", "label", "question", "selector", "field_key"))
            if any(term in ident for term in [
                "consent", "privacy", "terms", "agree", "policy",
                "declaration", "acknowledg", "candidateconsent", "dataprocessing",
                "data processing", "agreement"
            ]):
                candidates.append({
                    "element_index": idx,
                    "field": "candidate_consent",
                    "value": "true",
                    "action": "check",
                    "source": "deterministic",
                    "confidence": 100,
                })
                used_elements.add(idx)

        # --- 4. Checkboxes in Experience sections ---
        for e in elements:
            if e.get("_section_type") == "experience" and norm(e.get("type")) == "checkbox":
                idx = e["_element_index"]
                if idx in used_elements:
                    continue
                gi = e.get("_group_index", 0)
                if 0 <= gi < len(exp_records_sorted):
                    rec = exp_records_sorted[gi]
                    val = rec.get("currently_working", False)
                    candidates.append({
                        "element_index": idx,
                        "field": f"experience[{gi}].currently_working",
                        "value": "true" if val else "false",
                        "action": "checkbox",
                        "source": "resume",
                        "confidence": 100,
                    })
                    used_elements.add(idx)

        # --- 5. FIELD_RULES ---
        for field, phrases, resume_key in FIELD_RULES:
            matches: List[Tuple[int, Dict[str, Any]]] = []
            for e in elements:
                idx = e["_element_index"]
                if idx in used_elements or is_noise_control(e):
                    continue
                if is_captcha(e) or is_submit(e) or is_file_upload(e):
                    continue

                score = rule_match_score(e, phrases)

                if field == "phone" and is_phone_country_element(e):
                    score = 0

                if field == "experience_years":
                    if is_phone_element(e):
                        score = 0
                    opts = e.get("options") or []
                    yn = {norm(o.get("label") if isinstance(o, dict) else o) for o in opts}
                    if yn & {"yes", "no"} or norm(e.get("control_type")) == "radio_group":
                        score = 0

                if field in {"current_salary", "expected_salary"}:
                    path = (str(e.get("selector") or "") + " " + str(e.get("dom_path") or "")).lower()
                    if control_kind(e) == "select" or "cu-select" in path or looks_like_currency_control(e) or looks_like_salary_frequency_control(e):
                        score = 0

                if field in {"company_name", "job_title", "current_employer", "current_designation", "employer_name"}:
                    if is_current_employment_question(e, field) and not currently_employed(resume_idx):
                        score = 0

                if control_kind(e) in {"radio", "checkbox"} or norm(e.get("control_type")) in {"radio_group", "checkbox_group"}:
                    opts = e.get("options") or []
                    opt_labels = [clean_text(o.get("label") if isinstance(o, dict) else o).lower() for o in opts]
                    val_cand = clean_text(resume_idx.get(resume_key) or "").lower()
                    if field not in {"candidate_consent", "agreement"} and val_cand not in opt_labels:
                        score = 0

                if score >= 50:
                    matches.append((score, e))

            if not matches:
                continue

            matches.sort(key=lambda x: (-x[0], x[1]["_element_index"]))

            if field in REPEATABLE_FIELD_KEYS:
                # Sort matches by visual order (top-to-bottom) for stable mapping
                matches.sort(key=lambda x: (
                    x[1].get("rect", {}).get("y", 0) if isinstance(x[1].get("rect"), dict) else 0,
                    x[1].get("rect", {}).get("x", 0) if isinstance(x[1].get("rect"), dict) else 0,
                    x[1]["_element_index"]
                ))
                
                for score, e in matches:
                    sec = e.get("_section_type")
                    if not sec:
                        if "graduation" in field or "education" in field:
                            sec = "education"
                        elif "work" in field or "company" in field or "job" in field or "employer" in field:
                            sec = "experience"
                    
                    if not sec:
                        continue
                    
                    gi = e.get("_group_index")
                    
                    # Match by Degree or Company name to find the correct index
                    ph = canonical_text(e.get("placeholder") or "")
                    records_to_use = edu_records_sorted if sec == "education" else exp_records_sorted
                    
                    if sec == "education":
                        # Prefer to anchor by degree name
                        if "degree" in field or "institution" in field or "course" in field:
                            for idx, r in enumerate(records_to_use):
                                match_val = canonical_text(r.get("degree") or "")
                                if match_val and (match_val in ph or ph in match_val):
                                    gi = idx
                                    break
                    else:
                        if "company" in field or "job_title" in field:
                            for idx, r in enumerate(records_to_use):
                                match_val = canonical_text(r.get("company") or "") or canonical_text(r.get("job_title") or "")
                                if match_val and (match_val in ph or ph in match_val):
                                    gi = idx
                                    break
                    
                    if gi is None:
                        # Fallback to order
                        gi = matches.index((score, e))
                    
                    rep_field = e.get("_repeatable_field") or field
                    if rep_field == "graduation_degree": rep_field = "degree"
                    if rep_field == "graduation_college": rep_field = "institution"
                    if rep_field == "graduation_branch": rep_field = "field_of_study"
                    if rep_field == "company_name": rep_field = "company"
                    
                    value = ""
                    if 0 <= gi < len(records_to_use):
                        value = _record_field_value(records_to_use[gi], rep_field)
                    
                    if is_empty(value):
                        continue

                    candidates.append({
                        "element_index": e["_element_index"],
                        "field": f"{sec}[{gi}].{rep_field}",
                        "value": str(value),
                        "option_label": None,
                        "option_value": None,
                        "action": infer_action_for_element(e),
                        "source": "resume_repeatable",
                        "confidence": score,
                        "group_index": gi,
                        "section": sec,
                    })
                    used_elements.add(e["_element_index"])
                continue

            best_score, best = matches[0]
            value = resume_idx.get(resume_key)
            if is_empty(value):
                continue
            action = infer_action_for_element(best)
            cand_val = str(value)
            opt_label = None
            opt_val = None

            if field == "experience_years" and best.get("options"):
                y = int(digits(value) or 1)
                best_opt = None
                for opt in best.get("options", []):
                    lbl = clean_text(opt.get("label") if isinstance(opt, dict) else opt).lower()
                    if y == 0 and "fresher" in lbl:
                        best_opt = opt; break
                    elif y < 1 and ("less" in lbl or "<" in lbl):
                        best_opt = opt; break
                    elif 1 <= y <= 3 and ("1-3" in lbl or "1 to 3" in lbl):
                        best_opt = opt; break
                    elif 3 < y <= 5 and ("3-5" in lbl or "3 to 5" in lbl):
                        best_opt = opt; break
                    elif y > 5 and ("5+" in lbl or "5 plus" in lbl or "more than 5" in lbl):
                        best_opt = opt; break
                if not best_opt and y == 1:
                    for opt in best.get("options", []):
                        lbl = clean_text(opt.get("label") if isinstance(opt, dict) else opt).lower()
                        if "1" in lbl:
                            best_opt = opt; break
                if best_opt:
                    opt_label = clean_text(best_opt.get("label") if isinstance(best_opt, dict) else best_opt)
                    opt_val = clean_text(best_opt.get("value") if isinstance(best_opt, dict) else opt_label)
                    cand_val = opt_label
                    action = "select"

            candidates.append({
                "element_index": best["_element_index"],
                "field": field,
                "value": cand_val,
                "option_label": opt_label,
                "option_value": opt_val,
                "action": action,
                "source": "resume",
                "confidence": best_score,
            })
            used_elements.add(best["_element_index"])

        # --- 6. Salary currency ---
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

        # --- 7. Salary frequency ---
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

        # --- 8. Phone number ---
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
                candidates.append({
                    "element_index": best["_element_index"],
                    "field": "phone",
                    "value": resume_idx["national_phone"],
                    "action": "fill",
                    "source": "resume",
                    "confidence": best_score,
                })
                used_elements.add(best["_element_index"])

        # --- 9. Custom Questionnaire & Common Questions ---
        for e in elements:
            idx = e["_element_index"]
            if idx in used_elements or is_noise_control(e) or is_captcha(e) or is_submit(e):
                continue
            q_text = canonical_text(
                " ".join(str(e.get(k) or "") for k in ("question", "label", "placeholder", "id", "name"))
            )
            if not q_text:
                continue
            
            name = norm(e.get("name") or "")
            sel = norm(e.get("selector") or "")

            if any(w in q_text for w in ["house no", "street / locality", "street locality", "address line 1", "residential address"]):
                addr = resume_idx.get("address") or "153 Vishun Garden Rajendra Park6"
                candidates.append({
                    "element_index": idx,
                    "field": "address",
                    "value": addr,
                    "action": "fill",
                    "source": "resume",
                    "confidence": 100,
                })
                used_elements.add(idx)
        
            elif "city" in q_text:
                city_val = resume_idx.get("city") or resume_idx.get("current_location") or resume_idx.get("location") or ""
                if city_val:
                    candidates.append({
                        "element_index": idx,
                        "field": "city",
                        "value": city_val,
                        "action": "fill",
                        "source": "resume",
                        "confidence": 100,
                    })
                    used_elements.add(idx)
            # State field
            elif "state" in q_text:
                state_val = resume_idx.get("state") or ""
                if state_val:
                    candidates.append({
                        "element_index": idx,
                        "field": "state",
                        "value": state_val,
                        "action": "fill",
                        "source": "resume",
                        "confidence": 100,
                    })
                    used_elements.add(idx)

        candidates.sort(key=lambda c: 0 if c.get("action") == "upload" or c.get("field") == "resume" else 1)
        print(f"candidates is {candidates}")
        return candidates



    async def main(self, url ,structured_dom_content):
        
        if not RESUME_FILE.exists():
            raise FileNotFoundError(f"Missing {RESUME_FILE}")
        
        raw_elements = structured_dom_content
        resume = self.load_json(RESUME_FILE)
        elements = self.normalize_elements(raw_elements)

        job_url = url.strip()
        if not job_url:
            return {"error" : "Again call the run_application_state with internal parameter as url"}
        resume_idx = self.build_resume_index(resume)
        deterministic = self.deterministic_candidates(elements, resume_idx , resume=resume)

        for x in deterministic:
            try:
                if not self.verify_url_open(self.page):
                    await self.page.goto(job_url, wait_until="commit", timeout=PAGE_LOAD_TIMEOUT_MS)
                    return {'error' : f"The url {job_url} is not opened as we checked all iframes + context did not get it"}
                try:
                    await page.wait_for_load_state("domcontentloaded", timeout=15000)
                except Exception:
                    pass
            except Exception:
                try:
                    await page.goto(job_url, timeout=PAGE_LOAD_TIMEOUT_MS)
                except Exception:
                    pass

            await wait_for_form(page, resume)
            try:
                await page.evaluate("window.scrollTo(0, 0)")
            except Exception:
                pass

            print(f"Title: {await page.title()}")
            print(f"URL  : {page.url}")
            print()
            print("Frames detected:", len(page.frames))
            for i, frame in enumerate(page.frames):
                print(f"  [{i}] {frame.url}")

            try:
                elements = await enrich_cu_select_elements(page, elements)
            except Exception as exc:
                print(f"cu-select enrichment failed (non-fatal): {exc}")

            try:
                new_fields = await discover_new_fields_after_expansion(page, elements)
                if new_fields:
                    print(f"[Runtime] Discovered {len(new_fields)} new required fields after expansion")
                    elements.extend(new_fields)
                    deterministic = deterministic_candidates(elements, resume_idx, important, resume=resume)
            except Exception as exc:
                print(f"[Runtime] New-field discovery failed (non-fatal): {exc}")

            by_index = {e["_element_index"]: e for e in elements}

            phone_country_results = await set_phone_country_if_needed(page, elements, resume_idx, deterministic)
            for r in phone_country_results:
                print(f"phone_country_code          -> {r['answer']} {'VERIFIED' if r['verified'] else 'NOT VERIFIED'}")

            print()
            print("=" * 60)
            print("DETERMINISTIC RESUME FILL")
            print("=" * 60)

            deterministic_results = []
            by_index = {e["_element_index"]: e for e in elements}

            verified_country_indexes = {r["element_index"] for r in phone_country_results if r.get("verified")}
            for r in phone_country_results:
                if r.get("verified"):
                    deterministic_results.append(r)

            for item in deterministic:
                e = by_index[item["element_index"]]
                if item["field"] == "phone_country_code" and item["element_index"] in verified_country_indexes:
                    continue

                answer_item = {
                    "element_index": item["element_index"],
                    "answer": item["value"],
                    "action": item["action"],
                    "option_label": item.get("option_label"),
                    "option_value": item.get("option_value"),
                    "country_code": resume_idx.get("country_code") if is_phone_element(e) else None,
                }

                result = await retry_failed_answer(page, e, answer_item)
                deterministic_results.append(result)

                status = "VERIFIED" if result.get("verified") else "NOT VERIFIED"
                print(f"{item['field']:30} -> {item['value']}")
                print(f"   -> {status} ({result.get('method')})")

            national = self.clean_text(resume_idx.get("national_phone"))
            country = self.clean_text(resume_idx.get("country_code"))
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
                print(f"phone repair -> {'VERIFIED' if ok else 'NOT VERIFIED'} ({actual})")

            filled_indexes = {x["element_index"] for x in deterministic_results if x.get("verified")}
            deterministic_indexes = {x["element_index"] for x in deterministic}

            # Exclude elements from AI:
            # 1. Any element already filled/verified
            # 2. Any element that has a deterministic candidate (resume data exists)
            # 3. Any file/resume upload element
            ai_excluded = set(filled_indexes) | deterministic_indexes

            remaining_raw = []
            for e in elements:
                idx = e["_element_index"]
                if idx in ai_excluded or is_captcha(e) or is_submit(e) or is_noise_control(e):
                    continue
                if norm(e.get("tag")) == "button" or norm(e.get("type")) in {"button", "submit", "reset"} or norm(e.get("control_type")) in {"button", "submit_button"}:
                    continue
                if is_file_upload(e) or looks_like_resume_upload(e) or norm(e.get("type")) == "file" or control_kind(e) == "upload":
                    continue
                if e.get("disabled") is True or norm(e.get("type")) == "hidden":
                    continue
                remaining_raw.append(e)

            if important:
                remaining = [
                    e for e in collect_fill_targets(elements, important)
                    if e["_element_index"] not in ai_excluded
                    and not is_noise_control(e)
                    and not is_captcha(e)
                    and not is_submit(e)
                    and norm(e.get("tag")) != "button"
                    and norm(e.get("type")) not in {"button", "submit", "reset"}
                    and norm(e.get("control_type")) not in {"button", "submit_button"}
                    and not is_file_upload(e)
                    and not looks_like_resume_upload(e)
                    and norm(e.get("type")) != "file"
                    and control_kind(e) != "upload"
                ]
            else:
                remaining = dedupe_interactive_elements(remaining_raw)

            filled_keys = {_question_key(by_index[i]) for i in ai_excluded if i in by_index}
            remaining = [e for e in remaining if _question_key(e) not in filled_keys]
            remaining = dedupe_interactive_elements(remaining)

            print()
            print("=" * 60)
            print("REMAINING QUESTIONS")
            print("=" * 60)
            print(f"Questions sent to AI: {len(remaining)}")

            ai_data = {"answers": []}
            if remaining:
                print()
                print("=" * 60)
                print(f"AI BATCH MAPPING: {len(remaining)} controls")
                print(f"Batch size: {AI_BATCH_SIZE} | Max batches: {AI_MAX_BATCHES if AI_MAX_BATCHES > 0 else 'unlimited'}")
                print("=" * 60)
                batches = [remaining[i:i + max(1, AI_BATCH_SIZE)] for i in range(0, len(remaining), max(1, AI_BATCH_SIZE))]
                max_batches = len(batches) if AI_MAX_BATCHES <= 0 else min(len(batches), AI_MAX_BATCHES)
                for batch_no, batch in enumerate(batches[:max_batches], 1):
                    print(f"AI batch {batch_no}/{max_batches} -> {len(batch)} controls")
                    try:
                        batch_data = normalize_ai_answers(call_bedrock(build_ai_prompt(batch, resume, deterministic)))
                        ai_data["answers"].extend(batch_data.get("answers", []))
                    except Exception as exc:
                        print(f"AI batch {batch_no} failed: {exc}")
                if len(batches) > max_batches:
                    print(f"WARNING: {len(batches) - max_batches} AI batches skipped; those fields will be asked from the user.")
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

            ai_answer_items = ai_data.get("answers", [])
            ai_indexes = {
                int(x.get("element_index"))
                for x in ai_answer_items
                if isinstance(x, dict)
                and str(x.get("element_index", "")).isdigit()
                and norm(x.get("action")) != "skip"
                and self.clean_text(x.get("answer") or x.get("option_label"))
            }

            for e in remaining:
                idx = e.get("_element_index")
                if idx is None or idx in ai_indexes:
                    continue
                q = canonical_text(e.get("question") or e.get("label"))
                opts = e.get("options") or []
                opt_labels = [self.clean_text(o.get("label") if isinstance(o, dict) else o) for o in opts]

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

            ai_answers = validate_ai_answers(ai_data, elements)

            save_json(MAPPING_FILE, {
                "deterministic": deterministic,
                "ai_answers": ai_answers,
            })

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
                is_select_kind = norm(e.get("tag")) == "select" or control_kind(e) in {"select", "radio", "checkbox"}
                if not is_select_kind and (
                    norm(e.get("type")) == "url"
                    or e.get("field_key") in {"portfolio_url", "linkedin", "github", "website"}
                    or re.search(r"\b(url|website|portfolio link|profile link|github link|linkedin link)\b", norm(e.get("question") or ""))
                ):
                    ans = item.get("answer")
                    if ans and not is_empty(ans):
                        item["answer"] = ensure_https_url(ans)
                        if item.get("value"):
                            item["value"] = ensure_https_url(item["value"])
                return await retry_failed_answer(page, e, item)

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

            TEXT_CONCURRENCY = 1
            for start in range(0, len(text_items), TEXT_CONCURRENCY):
                chunk = text_items[start:start + TEXT_CONCURRENCY]
                results = await asyncio.gather(*(apply_one(x) for x in chunk), return_exceptions=True)
                for result in results:
                    if isinstance(result, dict):
                        ai_results.append(result)

            for item in interactive_items:
                result = await apply_one(item)
                if result:
                    ai_results.append(result)
                if result:
                    status = "VERIFIED" if result.get("verified") else "NOT VERIFIED"
                    idx = result.get("element_index")
                    e = by_index.get(idx, {})
                    print(f"[{idx}] {self.clean_text(e.get('question'))[:45]:45} -> {result.get('answer')}")
                    print(f"   -> {status} ({result.get('method')})")

            for result in ai_results:
                if result.get("action") in {"select", "radio", "checkbox", "upload", "skip"}:
                    continue
                idx = result.get("element_index")
                e = by_index.get(idx, {})
                status = "VERIFIED" if result.get("verified") else "NOT VERIFIED"
                print(f"[{idx}] {self.clean_text(e.get('question'))[:45]:45} -> {result.get('answer')}")
                print(f"   -> {status} ({result.get('method')})")

            print()
            print("=" * 60)
            print("FINAL LIVE VERIFICATION & MULTI-STEP REPAIR PASS")
            print("=" * 60)

            # FIX: Deduplicate by element_index, but PREFER verified deterministic
            # results over AI results. An AI result can only replace a deterministic
            # one if the deterministic was NOT verified.
            deduped_results: Dict[int, Dict[str, Any]] = {}
            # First pass: add deterministic results
            for r in deterministic_results:
                idx = r.get("element_index")
                if idx is not None:
                    deduped_results[int(idx)] = r
            # Second pass: add AI results only if they don't overwrite a verified deterministic
            for r in ai_results:
                idx = r.get("element_index")
                if idx is None:
                    continue
                idx = int(idx)
                existing = deduped_results.get(idx)
                if existing is None:
                    # No deterministic result for this field, AI can fill it
                    deduped_results[idx] = r
                elif not existing.get("verified"):
                    # Deterministic result was NOT verified, AI might be better
                    deduped_results[idx] = r
                # else: deterministic was verified, DO NOT overwrite with AI

            final_results = []

            for idx, result in sorted(deduped_results.items()):
                e = by_index.get(idx)
                if not e or is_noise_control(e):
                    continue
                if result.get("action") == "skip":
                    final_results.append(result)
                    continue

                q_label = self.clean_text(e.get("question") or e.get("label") or e.get("name") or "")[:45]
                print(f"\n--- [Field {idx}] {q_label} ({e.get('selector') or ''}) ---")

                verified, actual_val, step_logs = await multi_step_verify_and_repair(
                    page, e, result, resume_idx,
                )

                for line in step_logs:
                    print(f"  {line}")

                result["final_verified"] = verified
                result["actual_value"] = actual_val
                result["verification_steps"] = step_logs
                final_results.append(result)

            await reveal_rest_of_form(page)

            verified_count = sum(
                1
                for r in final_results
                if r.get("final_verified") is True
            )

            attempted_count = len([r for r in final_results if r.get("action") != "skip"])

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

            save_json(RESULT_FILE, output)

            print()
            print("=" * 60)
            print("FILLING FINISHED")
            print("=" * 60)
            print(f"Deterministic fields : {len(deterministic_results)}")
            print(f"AI answers           : {len(ai_answers)}")
            print(f"Verified             : {verified_count}/{attempted_count}")

            leftover = []
            for r in final_results:
                if r.get("action") == "skip":
                    continue
                if r.get("final_verified") is True:
                    continue
                leftover.append(r)

            print()
            print("LEFT UNFILLED")
            print("=" * 60)
            if leftover:
                for r in leftover:
                    q = self.clean_text(r.get("question") or "")[:70]
                    print(
                        f"[{r.get('element_index')}] {q or '(no label)'} "
                        f"-> {self.clean_text(r.get('answer')) or '(empty)'} "
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