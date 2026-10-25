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
from datetime import datetime, timedelta
from playwright.async_api import (async_playwright,Page,Frame,Locator,TimeoutError as PlaywrightTimeoutError,)
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

_BASE_DIR = Path(__file__).resolve()
RESUME_FILE = "resume_imp_content.json"
RESUME_PATH = "Aman_Kumar_Singh.pdf"
ELEMENTS_FILE = "elements.json"
IMPORTANT_FIELDS_FILE =  "important_data_fields.json"
AWS_REGION = os.getenv("AWS_REGION", "us-east-1")
MODEL_ID = os.getenv("BEDROCK_MODEL_ID", "us.amazon.nova-pro-v1:0")
LOCATOR_TIMEOUT_MS = 2500
VERIFY_TIMEOUT_MS = 1500
PAGE_LOAD_TIMEOUT_MS = 60000
SCROLL_STEPS = int(os.getenv("SCROLL_STEPS", "8"))
FIELD_FILL_TIMEOUT_S = float(os.getenv("FIELD_FILL_TIMEOUT_S", "15"))
AI_BATCH_SIZE = int(os.getenv("AI_BATCH_SIZE", "80"))
AI_MAX_BATCHES = int(os.getenv("AI_MAX_BATCHES", "0")) 
_CURRENT_RESUME_INDEX: Dict[str, Any] = {}
MONTH_NAMES_LIST = [
        "", "January", "February", "March", "April", "May", "June",
        "July", "August", "September", "October", "November", "December"
    ]

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

_SECTION_KEYWORD_MAP: Dict[str, List[str]] = {
        "education": ["education", "degree", "school", "university", "academic",
                    "qualification", "institute", "college", "course"],
        "experience": ["experience", "employment", "job", "work", "company",
                    "designation", "employer", "organization", "organisation",
                    "job profile"],
        "certification": ["certification", "certificate", "license"],
        "project": ["project"],
        "language": ["language"],
    }

REPEATABLE_FIELD_KEYS = {
        "company_name", "job_title", "employer_name", "current_employer",
        "current_designation", "work_start_date", "work_end_date", "work_location",
        "graduation_college", "graduation_degree", "graduation_branch",
        "graduation_year", "graduation_cgpa", "education_start_date",
        "education_end_date", "education_college", "education_degree",
        "education_branch", "education_location",
    }
    
FIELD_RULES = [
        ("full_name", ["full name", "your name", "candidate name", "name", "complete name"], "full_name"),
        ("first_name", ["first name", "given name", "given name(s)", "forename", "legal first name"], "first_name"),
        ("middle_name", ["middle name"], "middle_name"),
        ("last_name", ["last name", "surname", "family name", "family name(s)", "legal last name"], "last_name"),
        ("email", ["email", "e-mail", "email address", "email id"], "email"),
        ("phone", ["phone", "phone number", "mobile", "mobile number", "contact number", "telephone", "cell phone"], "national_phone"),
        ("linkedin", ["linkedin", "linkedin url", "linkedin profile"], "linkedin"),
        ("github", ["github", "github url", "github profile"], "github"),
        ("current_location", ["current location", "present location", "location"], "current_location"),
        ("graduation_college", ["graduation college", "graduation university", "college", "university"], "graduation_college"),
        ("graduation_year", ["graduation year", "year of graduation", "graduating year"], "graduation_year"),
        ("graduation_branch", ["branch in graduation", "specialization", "field of study", "graduation branch", "academic stream"], "graduation_branch"),
        ("graduation_cgpa", ["graduation total cgpa", "cgpa", "gpa"], "graduation_cgpa"),
        ("10th_percentage", ["10th percentile", "10th percentage", "10th marks"], "10th_percentage"),
        ("12th_percentage", ["12th percentile", "12th percentage", "12th marks"], "12th_percentage"),
        ("all_percentages", ["percentages and cgpa", "all percentages", "marks from 10th"], "all_percentages"),
        ("date_of_birth", ["date of birth", "dob", "birth date", "birthday"], "date_of_birth"),
        ("experience_years", ["experience", "years of experience", "total experience", "work experience"], "experience_years"),
        ("employer_name", ["employer name", "previous employer", "mention the employer"], "employer_name"),
        ("current_employer", ["current employer", "current employer name", "present employer"], "current_employer"),
        ("current_designation", ["current designation", "current role", "current title"], "current_designation"),
        ("primary_skills", ["primary skills", "your primary skills", "technical skills", "key skills"], "primary_skills"),
        ("secondary_skills", ["secondary skills", "your secondary skills"], "secondary_skills"),
        ("current_salary", ["current salary", "current ctc", "present salary", "current compensation", "current pay"], "current_salary"),
        ("expected_salary", ["expected salary", "expected ctc", "expected compensation", "desired salary", "salary expectation"], "expected_salary"),
        ("company_name", ["company name", "company", "employer name", "organization", "organization name"], "company_name"),
        ("job_title", ["job title", "designation", "title", "role"], "job_title"),
        ("graduation_degree", ["course", "degree", "qualification", "education level"], "graduation_degree"),
        ("graduation_branch", ["branch", "specialization", "branch/ specialization", "field of study", "major"], "graduation_branch"),
        ("graduation_college", ["university", "college", "university/ college", "school", "institution", "institute name"], "graduation_college"),
        ("work_start_date", ["date of joining", "joining date", "start date"], "work_start_date"),
        ("work_end_date", ["date of relieving", "relieving date", "date of completion", "end date"], "work_end_date"),
        ("position_applying_for", ["role applying for", "position applying for", "position applied for", "role applied for", "which role", "applying for"], "job_position"),
    ]
    

IMPORTANT_RESUME_MAP = {
        "full_name": "full_name", "candidate_name": "full_name", "name": "full_name",
        "your_name": "full_name", "job_status": "job_status",
        "reason_for_joining": "reason_for_joining", "reason_for_choosing": "reason_for_joining",
        "resume": "resume_file_path", "resume_upload": "resume_file_path", "cv": "resume_file_path",
        "first_name": "first_name", "middle_name": "middle_name", "last_name": "last_name",
        "phone_country_code": "country_code", "country_code": "country_code", "dial_code": "country_code",
        "phone_country": "country", "phone_country_name": "country",
        "candidate_consent": "agree_yes", "consent": "agree_yes", "privacy_consent": "agree_yes", "agreement": "agree_yes", "terms_agreement": "agree_yes",
        "referred_by": "employee_reference", "referred_by_existing_emp_name": "employee_reference",
        "email": "email", "phone": "national_phone", "mobile": "national_phone", "mobile_number": "national_phone",
        "mobilenumber": "national_phone", "contact_number": "national_phone", "phone_number": "national_phone",
        "mobile_no": "national_phone", "phone_no": "national_phone", "cell_phone": "national_phone", "country": "country",
        "school": "graduation_college", "degree": "graduation_degree", "discipline": "graduation_branch",
        "linkedin": "linkedin", "linkedin_url": "linkedin", "linkedin_profile": "linkedin", "website": "linkedin",
        "github": "github", "github_url": "github", "github_profile": "github",
        "portfolio": "portfolio", "portfolio_url": "portfolio", "personal_website": "portfolio",
        "current_work_country": "country", "time_zone": "timezone", "timezone": "timezone",
        "nationality": "nationality", "gender": "gender", "race_or_ethnicity": "ethnicity",
        "current_location": "current_location", "experience": "experience_years",
        "current_salary": "current_salary", "expected_salary": "expected_salary",
        "ctc_currency": "salary_currency", "ctc_amount": "current_salary",
        "ctc_frequency": "salary_frequency", "current_ctc": "current_salary", "expected_ctc": "expected_salary",
        "offered_ctc": "expected_salary", "last_working_day": "work_end_date",
        "earliest_joining": "available_to_join_days",
        "all_percentages": "all_percentages", "percentages": "all_percentages",
        "12th_percentage": "12th_percentage", "10th_percentage": "10th_percentage",
        "working_since": "working_since", "current_designation": "current_designation",
        "current_employer": "current_employer", "notice_period": "available_to_join_days",
        "availability": "available_to_join_days", "how_did_you_hear": "how_did_you_hear",
        "saarthee_employee_reference": "employee_reference",
        "bachelors_degree_result": "graduation_cgpa", "companies_worked_for": "companies_count",
        "plagiarism_agreement": "agree_yes", "own_words_agreement": "agree_yes",
        "recruitment_privacy_notice_agreement": "agree_yes",
        "commitment_to_meet_in_person": "agree_yes", "company_name": "company_name",
        "job_title": "job_title", "currently_working_here": "is_currently_working",
        "experience_date_of_joining": "work_start_date", "date_of_relieving": "work_end_date",
        "experience_location": "work_location", "course": "graduation_degree",
        "branch_specialization": "graduation_branch", "education_date_of_joining": "education_start_date",
        "education_date_of_completion": "education_end_date",
        "education_start_date": "education_start_date", "education_end_date": "education_end_date",
        "work_start_date": "work_start_date", "work_end_date": "work_end_date",
        "start_date": "education_start_date", "end_date": "education_end_date",
        "university_college": "graduation_college", "education_location": "education_location",
        "graduation_degree": "graduation_degree", "graduation_branch": "graduation_branch",
        "graduation_college": "graduation_college", "graduation_year": "graduation_year",
        "graduation_cgpa": "graduation_cgpa", "cgpa": "graduation_cgpa", "gpa": "graduation_cgpa",
        "current_salary_currency": "salary_currency", "mobile_phone_country_code": "country_code",
        "experience_years": "experience_years", "experience_months": "experience_years",
        "date_of_birth": "dob", "dob": "dob", "birth_date": "dob", "birthday": "dob",
        "dob_day": "dob_day", "dob_month": "dob_month", "dob_year": "dob_year",
        "day": "dob_day", "month": "dob_month", "year": "dob_year",
        "birth_day": "dob_day", "birth_month": "dob_month", "birth_year": "dob_year",
        "prior_worker": "prior_worker", "worked_with_us_before": "prior_worker",
        "work_authorization": "work_authorization", "authorized_to_work": "work_authorization",
        "disability_status": "disability_status",
        "preferred_location": "current_location", "desired_location": "current_location",
        "base_location": "current_location", "job_location": "current_location",
        "current_company": "current_employer",
        "salutation": "salutation", "title": "salutation", "skills": "all_skills",
        "skill_set": "all_skills", "skills_set": "all_skills",
        "position_applying_for": "job_position", "position": "job_position",
        "role_applying_for": "job_position", "role": "job_position", "target_role": "job_position",
        "job_position": "job_position", "how_did_you_hear_about_us": "how_did_you_hear",
        "earliest_joining_date": "earliest_joining_date", "available_from": "earliest_joining_date",
        "referral_source": "how_did_you_hear", "how_did_you_hear": "how_did_you_hear",
    }

SELECT_FIELD_KEYS = {
        "country", "school", "degree", "discipline", "current_work_country",
        "time_zone", "timezone", "nationality", "gender", "race_or_ethnicity",
        "high_school_math_performance", "high_school_native_language_performance",
        "companies_worked_for", "plagiarism_agreement", "commitment_to_meet_in_person",
        "dob_day", "dob_month", "dob_year", "prior_worker", "work_authorization", "disability_status",
        "preferred_location", "desired_location", "base_location", "job_location",
    }
    
_SELECTOR_SEMANTIC_MAP = [
        # Experience section selectors
        (r"companyName|company_name|employer_name|employerName|current_company|EmployerName", "company_name"),
        (r"designation|jobTitle|job_title|position_title|roleTitle|Designation", "job_title"),
        (r"experienceDateOfJoining|dateOfJoining|joinDate|startDate.*experience|experience.*start|Exp_StartYear", "work_start_date"),
        (r"dateOfRelieving|relievingDate|endDate.*experience|experience.*end|last_working_day|Exp_EndYear", "work_end_date"),
        (r"experienceLocation|workLocation|work_location", "work_location"),
        (r"currentlyWorking|currently_working|isCurrentlyWorking|WorkExperiences.*Current", "currently_working_here"),
        (r"WorkExperiences.*Role|role$|role_description|job_description", "description"),
        # Education section selectors
        (r"degree_|#degree|educationDegree|course_|Qualifications.*Name", "graduation_degree"),
        (r"branch_|#branch|specialization_|educationBranch", "graduation_branch"),
        (r"university_|college_|institution_|educationCollege|educationInstitution|InstituteName", "graduation_college"),
        (r"educationDateOfJoining|courseStart|startOfCourse|education.*start|Edu_StartYear", "education_start_date"),
        (r"dateOfCompletion|completionDate|educationDateOfCompletion|courseEnd|endOfCourse|education.*end|Edu_EndYear", "education_end_date"),
        (r"educationLocation|education.*location", "education_location"),
        (r"educationGrade|educationCgpa|educationGpa|educationScore|Qualifications.*Result", "graduation_cgpa"),
        # General form selectors
        (r"availableFrom|available_from|earliest_start|earliest_join|available_date|availability_date", "earliest_joining_date"),
        (r"referralSource|referral_source|howDidYouHear|how_heard|hear_about", "referral_source"),
        (r"joining_in_week|joininginweek|earliest_joining", "available_to_join_days"),
        (r"ctcNegotiable|negotiable|is_negotiable", "salary_negotiable"),
        (r"offered_ctc|offeredctc", "expected_salary"),
        (r"current_ctc|currentctc", "current_salary"),
        (r"expected_ctc|expectedctc", "expected_salary"),
        (r"#day\b|id=['\"]?day\b", "dob_day"),
        (r"#month\b|id=['\"]?month\b", "dob_month"),
        (r"#year\b|id=['\"]?year\b", "dob_year"),
        (r"#openResume|openResume|uploadResume|btn-upload-resume", "resume"),
        (r"linkedin|linkedinUrl|linkedin_url|linkedin_profile", "linkedin"),
        (r"github|githubUrl|github_url|github_profile", "github"),
        (r"portfolio|portfolioUrl|website|personalSite", "portfolio"),
        (r"mobileNumber|mobile_number|phoneNumber|phone_number|contactNumber|contact_number|cellPhone|cell_phone|mobile_no|phone_no", "phone"),
        (r"phoneCountry|phone_country|countryCode|dialCode", "phone_country_code"),
        (r"currentCountry|current_country|currentCountryId", "country"),
        (r"consent|agreement|terms|privacy.*policy", "agreement"),
    ]

_QUESTION_SEMANTIC_MAP = [
        # Experience questions
        (["name of employer", "company name", "employer name", "organization name", "organisation name", "employer / company", "name of company", "employer"], "company_name"),
        (["job title", "designation", "position title", "role title", "job designation", "designation / job title"], "job_title"),
        (["description of role", "summary of job", "job responsibilities", "summary of job responsibilities", "role description"], "description"),
        (["date of joining", "joining date", "work start date", "experience start date"], "work_start_date"),
        (["date of relieving", "relieving date", "last working day", "work end date", "experience end date"], "work_end_date"),
        (["currently working", "present employer", "working here", "i currently work here"], "currently_working_here"),
        # Education questions
        (["mention your percentage", "percentages and cgpa", "10th to the highest", "percentages from 10th", "10th to highest", "cgpa from 10th"], "all_percentages"),
        (["start of course", "course start", "education start"], "education_start_date"),
        (["end of course", "course end", "education end", "course completion"], "education_end_date"),
        (["branch", "specialization", "field of study", "major", "academic stream", "discipline"], "graduation_branch"),
        (["degree", "course", "qualification", "education level", "degree / diploma / certificate name"], "graduation_degree"),
        (["name of institute", "university", "college", "institution", "institute", "school", "name of institute / university"], "graduation_college"),
        (["first class", "distinction", "first class / distinction", "grade", "gpa", "cgpa", "score", "percentage", "marks", "result"], "graduation_cgpa"),
        # General questions
        (["last working day"], "work_end_date"),
        (["earliest joining date", "available from", "when can you start", "available to join", "earliest start date", "possible start date"], "earliest_joining_date"),
        (["how did you hear about us", "how did you hear", "referral source", "where did you hear", "source of referral", "how heard"], "referral_source"),
        (["earliest joining", "joining in week", "joining (weeks)"], "available_to_join_days"),
        (["offered ctc"], "expected_salary"),
        (["current ctc", "current salary"], "current_salary"),
        (["expected ctc", "expected salary", "add salary"], "expected_salary"),
        (["bundling tool", "bundling"], "bundling_tool"),
        (["skills", "skill set", "technical skills", "tell us more about your skills", "more about your skills"], "skills"),
        (["notice period", "notice", "how many days notice", "joining time"], "notice_period"),
        (["which languages do you know", "languages known", "languages you know", "languages"], "language"),
        (["terms and conditions", "privacy policy", "terms of service", "i agree", "consent"], "agreement"),
        (["years of experience", "how many years of experience", "total experience"], "experience"),
        (["current company", "what is your current company"], "current_company"),
        (["current position", "what is your current position"], "job_title"),
        (["linkedin", "linkedin url", "linkedin profile"], "linkedin"),
        (["github", "github url", "github profile"], "github"),
        (["portfolio", "personal website", "portfolio link"], "portfolio"),
        (["phone country", "phone number country", "mobile country", "country calling code", "dialing code"], "phone_country_code"),
        (["what is your full name", "enter your full name"], "full_name"),
        (["enter a phone number", "what is your phone number", "enter mobile number", "mobile number", "mobile", "phone number", "phone", "contact number", "contact"], "phone"),
        (["what is your email", "what is your email address"], "email"),
        (["what is your current location"], "current_location"),
        (["add degree"], "graduation_degree"),
        (["add specialization"], "graduation_branch"),
        (["enter your final grade", "final grade"], "graduation_cgpa"),
        (["enter your score type", "score type"], "cgpa"),
        (["have you worked with us before", "worked with us before", "previously employed by", "previous employee"], "prior_worker"),
        (["legally authorized to work", "authorized to work in", "eligible to work in"], "work_authorization"),
        (["disability", "disabilities", "handicapped"], "disability_status"),
    ]
  

ENRICH_CU_SELECT_JS = """() => {
        const vis = el => {
            if (!el) return false;
            const r = el.getBoundingClientRect();
            const s = getComputedStyle(el);
            return r.height > 4 && r.width > 4
                && s.display !== 'none' && s.visibility !== 'hidden'
                && Number(s.opacity) !== 0;
        };
        const results = [];
        const cuSelects = document.querySelectorAll('cu-select');
        for (const cuSel of cuSelects) {
            if (!vis(cuSel)) continue;
            const trigger = cuSel.querySelector('div[role="button"], [aria-expanded]');
            if (!trigger) continue;
            let label = '';
            let formField = cuSel.closest('cu-form-field, cu3-form-field');
            if (!formField) formField = cuSel.closest('div[class*="form-field"]');
            if (formField) {
                const labelEl = formField.querySelector('label, .cu-form-field__label, cu3-form-field-label, [class*="field-label"], [class*="form-label"]');
                if (labelEl) {
                    label = (labelEl.innerText || labelEl.textContent || '').replace(/\\s+/g, ' ').trim();
                }
            }
            if (!label) {
                let container = cuSel.parentElement;
                for (let i = 0; i < 5 && container; i++) {
                    const labelEl = container.querySelector('label, [class*="label"]');
                    if (labelEl && labelEl.closest('cu-select') !== cuSel) {
                        const t = (labelEl.innerText || labelEl.textContent || '').replace(/\\s+/g, ' ').trim();
                        if (t && t.length > 2 && t.length < 200 && !/^select/i.test(t)) { label = t; break; }
                    }
                    container = container.parentElement;
                }
            }
            if (!label) {
                const parent = cuSel.parentElement;
                if (parent) {
                    const children = [...parent.children];
                    const idx = children.indexOf(cuSel);
                    for (let j = idx - 1; j >= Math.max(0, idx - 3); j--) {
                        const sib = children[j];
                        if (sib.tagName === 'LABEL' || sib.querySelector('label')) {
                            const t = (sib.innerText || sib.textContent || '').replace(/\\s+/g, ' ').trim();
                            if (t && t.length > 2 && t.length < 200) { label = t; break; }
                        }
                    }
                }
            }
            const required = label.includes('*') || cuSel.closest('[class*="required"]') !== null;
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
                && s.display !== 'none' && s.visibility !== 'hidden';
        };
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
            const childSame = [...el.children].some(c => ((c.innerText || '').replace(/\\s+/g, ' ').trim()) === text);
            if (childSame) continue;
            seen.add(text.toLowerCase());
            out.push({label: text, text: text, value: el.getAttribute('data-value') || el.getAttribute('value') || text});
            if (out.length >= 50) break;
        }
        return out;
    }"""
  
FIND_OPEN_MENU_JS = """() => {
        const vis = el => {
            if (!el) return false;
            const r = el.getBoundingClientRect();
            const s = getComputedStyle(el);
            return r.height > 8 && r.width > 8
                && s.display !== 'none' && s.visibility !== 'hidden'
                && Number(s.opacity) !== 0;
        };
        const skip = el => !el || !!el.closest('.iti, .react-international-phone, cu-rich-editor-toolbar, .ql-toolbar, .g-recaptcha');
        const menus = [...document.querySelectorAll(
            '.cdk-overlay-pane, .cdk-overlay-container .cdk-overlay-pane, '
            + 'cu-dropdown, .cu-dropdown, [class*="cu-select"], '
            + '.select__menu, [role="listbox"], .ng-dropdown-panel, '
            + '.ant-select-dropdown, .MuiPopper-root, .dropdown-menu, '
            + '[class*="dropdown-list"], [class*="select-menu"], '
            + 'lyte-drop-box:not(.lyteDropdownHidden), lyte-drop-body, .cxDropbox'
        )].filter(el => vis(el) && !skip(el));
        if (menus.length) return menus[menus.length - 1];
        const opts = [...document.querySelectorAll('[role="option"], lyte-drop-item')].filter(el => vis(el) && !skip(el));
        if (opts.length) return opts[0].closest('[role="listbox"], lyte-drop-box, ul, div, section') || opts[0].parentElement;
        const search = [...document.querySelectorAll('.cdk-overlay-container input, .select__menu input, [role="listbox"] input, lyte-drop-box input, [class*="dropdown"] input')].find(el => vis(el) && !skip(el));
        if (search) return search.closest('.cdk-overlay-pane, [class*="dropdown"], [class*="select"], div') || search.parentElement;
        return null;
    }"""

COLLECT_MENU_OPTIONS_JS = """(includeGroups) => {
        const vis = el => {
            if (!el) return false;
            const r = el.getBoundingClientRect();
            const s = getComputedStyle(el);
            return r.height > 4 && r.width > 4
                && s.display !== 'none' && s.visibility !== 'hidden'
                && Number(s.opacity) !== 0;
        };
        const skipHost = el => !el || !!el.closest('.iti, .react-international-phone, cu-rich-editor-toolbar');
        const skipLabel = label => /^(type to search\\.{0,3}|search|no options|no results|loading|searching|select option\\.{0,3}|choose option|-none-)$/i.test(label);

        let menu = [...document.querySelectorAll(
            '.cdk-overlay-pane, [role="listbox"], [role="menu"], [role="tree"], .select__menu, .ng-dropdown-panel, .ant-select-dropdown, .dropdown-menu, .select2-dropdown, lyte-drop-box:not(.lyteDropdownHidden), lyte-drop-body, .cxDropbox, [class*="dropdown-menu"], [class*="dropdown-list"]'
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
            ? [...menu.querySelectorAll('lyte-drop-item, [role="option"], [role="menuitem"], [role="treeitem"], .select2-results__option, li, button, [class*="option"], a, *')]
            : [...document.querySelectorAll('lyte-drop-item, [role="option"], [role="treeitem"], .select2-results__option')].filter(el => vis(el) && !skipHost(el));
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
                label, text: label,
                value: el.getAttribute('data-value') || el.getAttribute('value') || '',
                is_group: isGroup || undefined
            });
            if (out.length >= 120) break;
        }
        return out;
    }"""
      
GENERIC_SEARCH_WORDS = {
        "in", "of", "the", "and", "university", "college", "school", "institute",
        "degree", "bachelor", "bachelors", "master", "masters",
    }
 
MONTH_EXPANSIONS = {
                "1": "January", "2": "February", "3": "March", "4": "April",
                "5": "May", "6": "June", "7": "July", "8": "August",
                "9": "September", "10": "October", "11": "November", "12": "December"
            }
INV_MONTH_EXP = {v.lower(): k for k, v in MONTH_EXPANSIONS.items()}
INV_MONTH_EXP.update({
                "jan": "1", "feb": "2", "mar": "3", "apr": "4", "may": "5", "jun": "6",
                "jul": "7", "aug": "8", "sep": "9", "oct": "10", "nov": "11", "dec": "12"
            })        
_CALENDAR_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
INDIAN_STATES = [
        "andhra pradesh", "arunachal pradesh", "assam", "bihar", "chhattisgarh",
        "goa", "gujarat", "haryana", "himachal pradesh", "jharkhand", "karnataka",
        "kerala", "madhya pradesh", "maharashtra", "manipur", "meghalaya", "mizoram",
        "nagaland", "odisha", "punjab", "rajasthan", "sikkim", "tamil nadu",
        "telangana", "tripura", "uttar pradesh", "uttarakhand", "west bengal", "delhi"
    ]
AUTOCOMPLETE_SUGGESTION_SELECTORS = [
        # Lever & modern location typeahead leaf items
        'div.dropdown-location',
        '[id^="location-"]:not(input)',
        '.dropdown-results > .dropdown-location',
        '.dropdown-results > div:not([class*="results"])',
        # ARIA / standard listbox & combobox options
        '[role="listbox"] > [role="option"]',
        '[role="listbox"] [role="option"]',
        'ul[role="listbox"] > li',
        '[role="option"]',
        'li[role="option"]',
        'div[role="option"]',
        # Location specific typeaheads
        'ul.location-results > li',
        'ul.location-results li',
        'li.location-result',
        'ul.ibe-dropdown-list > li',
        '.geosuggest__suggests > li',
        '.geosuggest__item',
        # Bootstrap & Ripplehire typeahead
        'ul.typeahead > li > a',
        'ul.typeahead > li',
        '.typeahead > li',
        '.tt-suggestion',
        '.tt-menu .tt-suggestion',
        # Google Places
        '.pac-container .pac-item',
        '.pac-item',
        'ul.ui-autocomplete > li',
        '.ui-autocomplete > li',
        '.autocomplete-result',
        # Generic dropdown menus
        'ul[class*="dropdown"] > li > a',
        'ul[class*="dropdown"] > li',
        '.dropdown-menu > li > a',
        '.dropdown-menu > li',
        'div[class*="dropdown-item"]',
        '.select2-results__option',
    ]

class FormFiller:
    def __init__(self, url, page):
        self.url = url
        self.page = page

    def clean_text(self, value: Any) -> str:
        if value is None:
            return ""
        return re.sub(r"\s+", " ", str(value)).strip()

    def norm(self, value: Any) -> str:
        s = self.clean_text(value).casefold()
        s = re.sub(r"[^a-z0-9+@._%/-]+", " ", s)
        return re.sub(r"\s+", " ", s).strip()

    def digits(self, value: Any) -> str:
        return re.sub(r"\D", "", str(value or ""))

    def ensure_https_url(self,value: Any) -> str:
        s = self.clean_text(value)
        if not s:
            return ""
        if re.match(r"^https?://", s, re.I):
            return s
        if s.startswith("//"):
            return "https:" + s
        return "https://" + s.lstrip("/")

    def canonical_text(self, value: Any) -> str:
        s = self.norm(value)
        s = re.sub(r"\bplease select\b", "", s)
        s = re.sub(r"\bselect(?:\s*\.{0,3})?\b", "", s)
        s = re.sub(r"\.{2,}", " ", s)
        s = re.sub(r"\s+", " ", s).strip()
        return s

    def token_set(self, value: Any) -> set:
        return {x for x in self.canonical_text(value).split() if len(x) > 1}

    def is_skip_answer(self, value: Any) -> bool:
        return self.canonical_text(value) in {"skip", "n a", "na", "n/a", "none"}

    def is_meta_instruction(self, val: Any) -> bool:
        if not val:
            return False
        s = str(val).strip().casefold()
        meta_phrases = [
            "answer yourself", "decide yourself", "ask ai", "based on my resume",
            "based on resume", "refer to resume", "as per resume", "check resume",
            "evaluate from resume"
        ]
        return any(p in s for p in meta_phrases)

    def is_positive_answer(self,value: Any) -> bool:
        if isinstance(value, bool):
            return value
        s = self.norm(self.clean_text(value))
        return s in {"true", "1", "yes", "y", "checked", "i agree", "agree", "currently working here"}

    def fuzzy_score(self,target: Any, candidate: Any) -> int:
        a = self.canonical_text(target)
        b = self.canonical_text(candidate)
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
        if re.search(rf"(^|\s){re.escape(a)}(\s|$)", b):
            return 92
        if re.search(rf"(^|\s){re.escape(b)}(\s|$)", a):
            return 78
        ta, tb = self.token_set(a), self.token_set(b)
        overlap = len(ta & tb) / max(1, len(ta | tb))
        ratio = SequenceMatcher(None, a, b).ratio()
        return int(max(overlap * 86, ratio * 82))

    def is_skill_tag_input(self,e: Dict[str, Any]) -> bool:
        q = self.canonical_text(e.get("question") or e.get("label") or "")
        placeholder = self.canonical_text(e.get("placeholder") or "")
        ident = self.canonical_text(e.get("id") or e.get("name") or "")
        cls = self.canonical_text(e.get("class") or e.get("className") or "")
        sel = self.canonical_text(e.get("selector") or "")
        tag_name = self.norm(e.get("tag") or "")
        hay = placeholder + " " + ident + " " + cls + " " + sel

        is_skill = any("skill" in x for x in (q, placeholder, ident, cls, sel))
        is_tag = any(
            x in (placeholder + " " + ident + " " + cls)
            for x in ["addskill", "skillset", "tag-input", "tags-input", "add skill", "search and add"]
        )
        if (is_skill and (is_tag or "add" in placeholder or "search" in placeholder)) or (tag_name == "input" and "addskill" in (ident + sel) ):
            return True

        if tag_name != "input":
            return False
        edu_context = any(
            x in (q + " " + hay)
            for x in ["education", "certification", "course", "qualification", "degree", "award", "language"]
        )
        tag_hint = (
            "tag" in cls
            or "chip" in cls
            or "pill" in cls
            or "token" in cls
            or any(
                x in hay
                for x in [
                    "add-education", "education-tag", "add-certification", "add-course",
                    "add-language", "add-entry", "add-item",
                    "tagify", "tagsify", "chip-input", "pill-input", "token-input",
                ]
            )
        )
        return edu_context and tag_hint

    def control_kind(self,e: Dict[str, Any]) -> str:
        if self.is_skill_tag_input(e):
            return "skills_tag"
        t = self.norm(e.get("type"))
        tag = self.norm(e.get("tag"))
        role = self.norm(e.get("role") or e.get("runtime_role"))
        control = self.norm(e.get("control_type"))
        q = self.canonical_text(e.get("question") or e.get("label"))
        if self.is_file_upload(e):
            return "upload"
        if tag == "select":
            return "select"
        if t == "date" or e.get("is_datepicker") is True or self.is_date_element(e):
            return "fill"
        if t == "checkbox" or role in {"checkbox", "switch"} or "checkbox" in control:
            return "checkbox"
        if t == "radio" or role == "radio" or control == "radio_group":
            return "radio"
        path = self.norm(e.get("selector") or "") + " " + self.norm(e.get("dom_path") or "")
        if (
            tag == "select"
            or t in {"select", "combobox", "listbox"}
            or role in {"combobox", "listbox"}
            or "combobox" in control
            or "dropdown" in control
            or "cu-select" in path
            or (bool(e.get("options")) and not self.is_phone_element(e))
            or (role == "button" and (
                e.get("options")
                or self.norm(e.get("haspopup")) in {"listbox", "menu", "list", "true"}
            ))
        ):
            return "select"
        if any("select..." in self.norm(e.get(k)) for k in ("label", "question", "placeholder")):
            return "select"
        if any(x in q for x in ["time zone", "timezone"]) and (
            tag == "select" or role in {"combobox", "listbox"} or e.get("options")
        ):
            return "select"
        if tag in {"button", "a", "label"} and not e.get("options"):
            return "click"
        return "fill"

    def option_display(self,opt: Any) -> str:
        if isinstance(opt, dict):
            return self.clean_text(opt.get("label") or opt.get("text") or opt.get("name") or opt.get("value"))
        return self.clean_text(opt)

    def resolve_option_from_element(self,e: Dict[str, Any], answer: Any, option_label: Any = None, option_value: Any = None) -> Optional[Dict[str, Any]]:
        targets = [x for x in [option_label, option_value, answer] if self.clean_text(x)]
        best = None
        best_score = 0
        for opt in self.usable_options(e):
            if not isinstance(opt, dict):
                opt = {"label": self.option_display(opt), "text": self.option_display(opt)}
            fields = [opt.get("label"), opt.get("text"), opt.get("value")]
            score = max((self.fuzzy_score(t, f) for t in targets for f in fields if self.clean_text(f)), default=0)
            score = max(score, max((self.option_synonym_score(t, f) for t in targets for f in fields if self.clean_text(f)), default=0))
            if score > best_score:
                best_score, best = score, opt
        return best if best_score >= 65 else None

    def parse_date_value(self,value: Any) -> Optional[date]:
        s = self.clean_text(value)
        if not s:
            return None
        if s.lower() in {"present", "current", "now", "currently enrolled"}:
            return datetime.now().date()

        formats = [
            "%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%m/%d/%Y", "%d.%m.%Y",
            "%Y/%m/%d", "%d %B %Y", "%d %b %Y", "%B %d, %Y", "%b %d, %Y",
            "%Y-%m", "%m/%Y", "%b %Y", "%B %Y", "%Y", "%B %Y", "%b %Y"
        ]

        for fmt in formats:
            try:
                return datetime.strptime(s, fmt).date()
            except ValueError:
                continue

        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00")).date()
        except Exception:
            return None

    def is_date_match(self,expected: Any, actual: Any) -> bool:
        
        e = self.clean_text(expected).lower()
        a = self.clean_text(actual).lower()
        if not e or not a: 
            return False
        if e == a: 
            return True
        if e in a or a in e: 
            return True
        
        ed = self.parse_date_value(e)
        ad = self.parse_date_value(a)
        if ed and ad:
            if ed == ad: return True
            if len(e) == 4 and ed.year == ad.year: return True
            if len(a) == 4 and ed.year == ad.year: return True
            if ed.year == ad.year and ed.month == ad.month: return True
        return False

    def same_value(self,expected: Any, actual: Any) -> bool:
        if self.is_date_element({"question": expected}) or self.is_date_match(expected, actual):
            if self.is_date_match(expected, actual): return True

        e = self.clean_text(expected)
        a = self.clean_text(actual)

        if not e:
            return not a
        if self.norm(e) == self.norm(a):
            return True

        ed = self.digits(e)
        ad = self.digits(a)
        if ed and ad and (ed == ad or ed.endswith(ad) or ad.endswith(ed)):
            return True

        eu = re.sub(r"^https?://", "", e.casefold()).rstrip("/")
        au = re.sub(r"^https?://", "", a.casefold()).rstrip("/")
        if eu and eu == au:
            return True

        if self.norm(e).replace(" ", "") == self.norm(a).replace(" ", ""):
            return True

        try:
            if abs(float(re.sub(r"[^\d.]", "", e)) - float(re.sub(r"[^\d.]", "", a))) < 0.01:
                return True
        except Exception:
            pass
        
        return False

    def is_empty(self, value: Any) -> bool:
        return self.clean_text(value) == ""

    def is_blank_display(self,value: Any) -> bool:
        t = self.canonical_text(value)
        return (not t) or t in {
            "select", "select an option", "choose", "please select", "mm/dd/yyyy", "dd/mm/yyyy",
        }

    def display_matches(self,expected: Any, actual: Any, element: Optional[Dict[str, Any]] = None) -> bool:
        if self.is_blank_display(actual) or not self.clean_text(expected):
            return False
        if element and self.is_date_element(element):
            if self.is_date_match(expected, actual): return True

        ea, aa = self.canonical_text(expected), self.canonical_text(actual)
        if aa != ea and aa in ea.split() and len(aa.split()) < len(ea.split()):
            return False
        if self.looks_like_group_heading(actual) and not self.looks_like_group_heading(expected):
            return False
        if self.is_fallback_other_label(actual) and not self.is_fallback_other_label(expected):
            return bool(re.search(r"university|college|institute|\bschool\b", ea))
        return (
            self.fuzzy_score(expected, actual) >= 65
            or self._option_match_score(str(expected), str(actual)) >= 65
            or self.same_value(expected, actual)
        )

    def compact_dict(self,d: Dict[str, Any]) -> Dict[str, Any]:
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

    def load_json(self,path: str) -> Any:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    def save_json(self, path: str, data: Any) -> None:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False, default=str)

    # ============================================================
    # RESUME INDEX
    # ============================================================

    def parse_dob_components(self,dob_str: str) -> Tuple[str, str, str, str]:
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

    def build_resume_index(self,resume: Dict[str, Any]) -> Dict[str, Any]:
        
        p = resume.get("prompt", {})
        personal = p.get("personal_info", {})
        supplied = p.get("user_supplied") if isinstance(p.get("user_supplied"), dict) else {}

        full_name = self.clean_text(personal.get("full_name"))
        parts = full_name.split()

        first_name = parts[0] if parts else ""
        last_name = parts[-1] if len(parts) > 1 else ""
        middle_name = " ".join(parts[1:-1]) if len(parts) > 2 else ""

        education = p.get("education", []) or []
        work = p.get("work_experience", []) or []
        skills_raw = p.get("skills") or resume.get("skills") or p.get("key_skills") or p.get("technical_skills") or {}
        projects = p.get("projects", []) or []

        # Education is ordered from highest qualification to lowest in resume
        grad = education[0] if education else {}
        latest_work = work[0] if work else {}

        # Universal, field-agnostic skill extraction (works for BBA, B.Com, B.Tech, MBA, Finance, etc.)
        def _extract_skills(data):
            res = []
            if isinstance(data, dict):
                for k, val in data.items():
                    if isinstance(val, (list, tuple)):
                        for item in val:
                            if isinstance(item, (str, int, float)) and str(item).strip():
                                res.append(str(item).strip())
                            elif isinstance(item, dict):
                                res.extend(_extract_skills(item))
                    elif isinstance(val, str) and val.strip():
                        parts = [s.strip() for s in re.split(r"[,;\n•|]+", val) if s.strip()]
                        res.extend(parts)
                    elif isinstance(val, dict):
                        res.extend(_extract_skills(val))
            elif isinstance(data, (list, tuple)):
                for item in data:
                    if isinstance(item, (str, int, float)) and str(item).strip():
                        res.append(str(item).strip())
                    elif isinstance(item, dict):
                        res.extend(_extract_skills(item))
            elif isinstance(data, str) and data.strip():
                parts = [s.strip() for s in re.split(r"[,;\n•|]+", data) if s.strip()]
                res.extend(parts)
            return res

        all_skills_list = list(dict.fromkeys(_extract_skills(skills_raw)))
        print(f"skills is {all_skills_list}")
        all_skills_str = ", ".join(all_skills_list)

        pct_parts = []
        for edu in reversed(education):
            deg_name = self.clean_text(edu.get("degree"))
            sp = self.clean_text(edu.get("score_percentage"))
            gpa = self.clean_text(edu.get("gpa"))
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

        phone = self.clean_text(personal.get("phone"))
        phone_digits = self.digits(phone)

        country_code = self.clean_text(personal.get("country_code") or personal.get("dial_code") or "")
        if country_code and not country_code.startswith("+"):
            country_code = "+" + country_code
        national_phone = phone
        if phone.startswith("+"):
            m = re.match(r"^\+(\d{1,3})(?:[\s-]+(.*))?$", phone)
            if m:
                if not country_code:
                    country_code = "+" + m.group(1)
                rest = self.clean_text(m.group(2) or "")
                national_phone = rest if rest else phone
        national_digits = self.digits(national_phone)
        cc_dig = self.digits(country_code)
        if cc_dig and self.digits(phone).startswith(cc_dig) and len(self.digits(phone)) > len(cc_dig):
            national_phone = self.digits(phone)[len(cc_dig):]
        elif len(national_digits) >= 10:
            national_phone = national_digits[-10:] if country_code else national_digits

        dob = self.clean_text(
            personal.get("date_of_birth") or personal.get("dob") or personal.get("DOB")
            or p.get("date_of_birth") or p.get("dob") or p.get("DOB")
            or resume.get("date_of_birth") or resume.get("dob") or resume.get("DOB")
        )

        experience_years = p.get("total_experience_years", resume.get("year_of_exp", 0))
        current_salary = self.clean_text(
            personal.get("current_salary") or personal.get("Current Salary")
            or p.get("current_salary") or p.get("Current Salary")
            or resume.get("current_salary") or resume.get("Current Salary")
        )
        expected_salary = self.clean_text(
            personal.get("expected_salary") or personal.get("Expected Salary")
            or p.get("expected_salary") or p.get("Expected Salary")
            or resume.get("expected_salary") or resume.get("Expected Salary")
        )

        gender_str = self.clean_text(personal.get("gender") or personal.get("sex"))
        salutation = "Mr." if gender_str.lower() in {"male", "m"} else ("Ms." if gender_str.lower() in {"female", "f"} else "")

        grad_end = self.clean_text(grad.get("end_date"))
        m_end_yr = re.search(r"\b(19\d{2}|20\d{2})\b", grad_end)
        if m_end_yr and not grad.get("currently_enrolled") and grad_end.lower() not in {"present", "current", "currently enrolled"}:
            grad_year = m_end_yr.group(1)
        else:
            start_yr_m = re.search(r"\b(19\d{2}|20\d{2})\b", self.clean_text(grad.get("start_date")))
            if start_yr_m:
                s_yr = int(start_yr_m.group(1))
                deg_norm = self.clean_text(grad.get("degree")).lower()
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
            "email": self.clean_text(personal.get("email")),
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
            "ethnicity": self.clean_text(personal.get("ethnicity") or personal.get("race") or personal.get("race_or_ethnicity")),
            "location": self.clean_text(latest_work.get("location") or grad.get("location")),
            "current_location": self.clean_text(latest_work.get("location") or grad.get("location")),
            "city": self.clean_text(personal.get("city") or personal.get("location") or latest_work.get("location") or grad.get("location")),
            "state": self.clean_text(personal.get("state") or personal.get("province") or personal.get("region")),
            "professional_summary": self.clean_text(p.get("professional_summary")),
            "experience_years": str(experience_years),
            "graduation_college": self.clean_text(grad.get("institution")),
            "graduation_year": grad_year,
            "graduation_branch": self.clean_text(grad.get("field_of_study")),
            "graduation_degree": self.clean_text(grad.get("degree")),
            "graduation_cgpa": self.clean_text(grad.get("gpa")),
            "company_name": self.clean_text(latest_work.get("company")),
            "job_title": self.clean_text(latest_work.get("job_title")),
            "designation": self.clean_text(latest_work.get("job_title")),
            "work_location": self.clean_text(latest_work.get("location")),
            "work_start_date": self.clean_text(latest_work.get("start_date")),
            "work_end_date": self.clean_text(latest_work.get("end_date")),
            "is_currently_working": "true" if latest_work.get("currently_working") else "false",
            "education_degree": self.clean_text(grad.get("degree")),
            "education_branch": self.clean_text(grad.get("field_of_study")),
            "education_college": self.clean_text(grad.get("institution")),
            "education_location": self.clean_text(grad.get("location")),
            "education_start_date": self.clean_text(grad.get("start_date")),
            "education_end_date": (self.clean_text(grad.get("end_date")) or ("Present" if grad.get("currently_enrolled") else grad_year)),
            "start_date": self.clean_text(grad.get("start_date") or latest_work.get("start_date")),
            "end_date": (self.clean_text(grad.get("end_date")) or ("Present" if grad.get("currently_enrolled") else (self.clean_text(latest_work.get("end_date")) or grad_year))),
            "12th_percentage": "",
            "10th_percentage": "",
            "all_percentages": all_percentages,
            "current_employer": "",
            "employer_name": self.clean_text(latest_work.get("company")),
            "current_designation": "",
            "previous_employer": self.clean_text(latest_work.get("company")),
            "previous_designation": self.clean_text(latest_work.get("job_title")),
            "skills": all_skills_str,
            "all_skills": all_skills_str,
            "primary_skills": all_skills_str,
            "key_skills": all_skills_str,
            "secondary_skills": all_skills_str,
            "country": "",
            "nationality": "",
            "timezone": "",
            "companies_count": str(len(work)) if work else "",
            "agree_yes": "I agree",
        }

        res_country = self.clean_text(
            personal.get("country") or personal.get("Country") or personal.get("current_country")
            or p.get("country") or p.get("Country") or resume.get("country") or resume.get("Country")
        )
        res_nat = self.clean_text(
            personal.get("nationality") or personal.get("Nationality") or personal.get("citizenship")
            or p.get("nationality") or resume.get("nationality")
        )
        res_tz = self.clean_text(
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
        idx["prior_worker"] = self.clean_text(personal.get("prior_worker") or "No")
        idx["worked_with_us_before"] = self.clean_text(personal.get("worked_with_us_before") or "No")
        idx["work_authorization"] = self.clean_text(personal.get("work_authorization") or "Yes")
        idx["authorized_to_work"] = self.clean_text(personal.get("authorized_to_work") or "Yes")
        idx["disability_status"] = self.clean_text(personal.get("disability_status") or "Prefer not to answer")

        if not idx["current_location"] and latest_work.get("location"):
            idx["current_location"] = self.clean_text(latest_work.get("location"))
        if not idx.get("city") and idx.get("current_location"):
            idx["city"] = idx["current_location"]

        all_locs = []
        if personal.get("city"): all_locs.append(self.clean_text(personal.get("city")))
        if personal.get("location"): all_locs.append(self.clean_text(personal.get("location")))
        for w in work:
            if w.get("location"): all_locs.append(self.clean_text(w.get("location")))
        for edu in education:
            if edu.get("location"): all_locs.append(self.clean_text(edu.get("location")))
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
            elif c_low in {"ludhiana", "amritsar", "jalandhar", "patiala", "bathinda", "mohali", "s.a.s. nagar", "hoshiarpur", "batala", "pathankot", "moga", "abohar", "malerkotla", "khanna", "firozpur", "barnala", "muktsar"}:
                idx["state"] = "Punjab"
            elif c_low in {"indore", "bhopal", "jabalpur", "gwalior", "ujjain", "sagar", "dewas", "satna", "ratlam", "rewa", "murwara", "singrauli", "burhanpur", "khandwa", "bhind", "chhindwara", "guna", "shivpuri", "vidisha", "chhatarpur", "damoh", "mandsaur", "khargone", "neemuch", "pithampur"}:
                idx["state"] = "Madhya Pradesh"
            elif c_low in {"kochi", "cochin", "thiruvananthapuram", "trivandrum", "kozhikode", "calicut", "thrissur", "kollam", "alappuzha", "palakkad", "kannur", "kottayam", "malappuram", "guruvayur"}:
                idx["state"] = "Kerala"
            elif c_low in {"bhubaneswar", "cuttack", "rourkela", "brahmapur", "berhampur", "sambalpur", "puri", "baleshwar", "balasore", "bhadrak", "baripada"}:
                idx["state"] = "Odisha"
            elif c_low in {"patna", "gaya", "bhagalpur", "muzaffarpur", "darbhanga", "purnia", "arrah", "begusarai", "katihar", "munger", "bihar sharif", "chapra", "sasaram", "hajipur"}:
                idx["state"] = "Bihar"
            elif c_low in {"ranchi", "jamshedpur", "dhanbad", "bokaro", "deoghar", "hazaribagh", "giridih", "ramgarh", "medininagar"}:
                idx["state"] = "Jharkhand"
            elif c_low in {"raipur", "bhilai", "bilaspur", "korba", "durg", "rajnandgaon", "jagdalpur", "raigarh", "ambikapur"}:
                idx["state"] = "Chhattisgarh"
            elif c_low in {"guwahati", "silchar", "dibrugarh", "jorhat", "tezpur", "nagaon", "tinsukia", "bongaigaon"}:
                idx["state"] = "Assam"
            elif c_low in {"dehradun", "haridwar", "roorkee", "haldwani", "rudrapur", "kashipur", "nainital", "rishikesh", "mussoorie"}:
                idx["state"] = "Uttarakhand"
            elif c_low in {"shimla", "dharamshala", "solan", "mandi", "kullu", "manali", "baddi", "palampur", "una", "bilaspur"}:
                idx["state"] = "Himachal Pradesh"
            elif c_low in {"panaji", "panjim", "margao", "madgaon", "vasco da gama", "mapusa", "ponda"}:
                idx["state"] = "Goa"
            elif c_low in {"srinagar", "jammu", "anantnag", "baramulla", "udhampur", "kathua", "sopore", "pulwama"}:
                idx["state"] = "Jammu & Kashmir"
                
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
        if not idx.get("resume_file_path"):
            cand = self.clean_text(resume.get("resume_path") or resume.get("pdf_path") or resume.get("file_path") or (resume.get("prompt", {}).get("resume_path") if isinstance(resume.get("prompt"), dict) else ""))
            if cand and os.path.exists(cand):
                idx["resume_file_path"] = str(Path(cand).resolve())
        if not idx.get("resume_file_path") and RESUME_PATH:
            idx["resume_file_path"] = RESUME_PATH

        for k, v in list(idx.items()):
            if isinstance(v, str) and self.is_meta_instruction(v):
                idx[k] = ""

        # Store raw records so the entire workflow uses one consistent source
        idx["_education_records"] = education
        idx["_work_records"] = work

        _CURRENT_RESUME_INDEX.clear()
        _CURRENT_RESUME_INDEX.update(idx)
        return idx

    # ============================================================
    # DATE HELPERS
    # ============================================================

    def is_date_element(self,e: Dict[str, Any]) -> bool:
        if self.norm(e.get("tag")) == "select" or self.norm(e.get("field_type")) == "select" or self.norm(e.get("control_type")) == "select":
            return False
        s = self.element_search_text(e)
        t = self.norm(e.get("type"))
        ft = self.norm(e.get("field_type") or "")
        ct = self.norm(e.get("control_type") or "")
        fk = self.norm(e.get("field_key") or "")
        if ft == "date" or ct == "date" or e.get("is_datepicker") is True:
            return True
        if fk in {"education_start_date", "education_end_date", "work_start_date", "work_end_date", "start_date", "end_date", "date_of_birth", "dob", "earliest_joining_date", "available_from", "joining_date"}:
            return True
        ident = self.norm(e.get("id") or "") + " " + self.norm(e.get("name") or "")
        return (
            t == "date"
            or e.get("is_datepicker") is True
            or "date" in ident
            or "availablefrom" in ident
            or "available_from" in ident
            or "startyear" in ident
            or "endyear" in ident
            or "start_year" in ident
            or "end_year" in ident
            or "since" in ident
            or "working since" in s
            or "date of birth" in s
            or re.search(r"\bdob\b", s) is not None
            or "birth date" in s
            or "start date" in s
            or "end date" in s
            or "joining date" in s
            or "earliest joining" in s
            or "available from" in s
            or s in {"from", "to", "from*", "to*"}
            or "from (month)" in s
            or "to (month)" in s
            or "from (year)" in s
            or "to (year)" in s
            or "mm/yyyy" in s
            or "month/year" in s
            or "start of course" in s
            or "end of course" in s
            or "date of joining" in s
            or "date of relieving" in s
        )

    # ============================================================
    # REPEATABLE GROUP DETECTION
    # ============================================================

    def _detect_group_index(self,e: Dict[str, Any]) -> Optional[int]:
        for key in ("id", "name", "selector"):
            val = self.clean_text(e.get(key))
            if not val:
                continue
            for pat in (
                r"[_\[\-](\d+)\]?(?:$|[_\-\.])",
                r"\[(\d+)\]",
                r"data-index\s*=\s*[\"']?(\d+)",
            ):
                m = re.search(pat, val)
                if m:
                    try:
                        return int(m.group(1))
                    except Exception:
                        pass
        return None

    def _detect_section_type(self,e: Dict[str, Any]) -> Optional[str]:
        hay = self.canonical_text(
            " ".join(
                str(e.get(k) or "")
                for k in ("id", "name", "label", "question", "placeholder",
                        "section_key", "section", "group_type")))
        
        if not hay:
            return None
        for section, keywords in _SECTION_KEYWORD_MAP.items():
            if any(kw in hay for kw in keywords):
                return section
        return None

    def _infer_repeatable_field(self,e: Dict[str, Any]) -> Optional[str]:
        hay = self.canonical_text(
            " ".join(
                str(e.get(k) or "")
                for k in ("label", "question", "placeholder", "name", "id", "aria_label", "selector")
            )
        )
        if not hay:
            return None
        patterns = [
            ("company", ["name of employer", "company", "employer", "organization", "organisation", "employername"]),
            ("description", ["description of role", "summary of job", "job responsibilities", "role description", "description", "responsibilities", "duties", ".role", "role"]),
            ("job_title", ["designation / job title", "job title", "designation", "position title", "role title", "position"]),
            ("start_date", ["start date", "from date", "joining date", "date of joining", "from (month)", "exp_startyear", "edu_startyear", "startyear", "start"]),
            ("end_date", ["end date", "to date", "relieving date", "date of relieving", "to (month)", "exp_endyear", "edu_endyear", "endyear", "end", "completion"]),
            ("location", ["location", "city"]),
            ("grade", ["first class", "distinction", "class / distinction", "result", "grade", "gpa", "cgpa", "score", "percentage", "division"]),
            ("institution", ["name of institute", "institution", "university", "college", "school", "institute", "institutename"]),
            ("degree", ["degree / diploma / certificate name", "degree", "qualification", "course", "diploma"]),
            ("field_of_study", ["field of study", "discipline", "specialization", "stream", "branch", "major"]),
            ("start_year", ["start year", "year of joining", "from year"]),
            ("end_year", ["end year", "graduation year", "year of completion", "passing year"]),
        ]
        for field_name, needles in patterns:
            if any(n in hay for n in needles):
                return field_name
        return None

    def _record_field_value(self,record: Dict[str, Any], field_name: str) -> str:
        if not isinstance(record, dict):
            return ""
        if field_name in {"description", "role", "job_description", "experience_description"}:
            achievements = record.get("achievements")
            if isinstance(achievements, list) and achievements:
                return "\n".join(f"- {self.clean_text(a)}" for a in achievements if self.clean_text(a))
            desc = self.clean_text(record.get("description") or record.get("summary") or record.get("responsibilities"))
            if desc:
                return desc
        if field_name in {"grade", "result", "graduation_cgpa", "graduation_grade"}:
            gpa = self.clean_text(record.get("gpa") or record.get("cgpa"))
            pct = self.clean_text(record.get("score_percentage") or record.get("percentage"))
            if gpa:
                try:
                    gpa_f = float(gpa)
                    if gpa_f >= 8.0:
                        return f"First Class with Distinction ({gpa} CGPA)"
                    elif gpa_f >= 6.5:
                        return f"First Class ({gpa} CGPA)"
                    return f"{gpa} CGPA"
                except Exception:
                    return f"{gpa} CGPA"
            elif pct:
                try:
                    pct_f = float(pct)
                    if pct_f >= 75:
                        return f"First Class with Distinction ({pct}%)"
                    elif pct_f >= 60:
                        return f"First Class ({pct}%)"
                    return f"{pct}%"
                except Exception:
                    return f"{pct}%"

        aliases = {
            "company": ["company", "employer_name", "employer", "organization"],
            "job_title": ["job_title", "designation", "title", "position"],
            "start_date": ["start_date", "from_date", "joining_date", "date_of_joining"],
            "end_date": ["end_date", "to_date", "relieving_date", "date_of_relieving"],
            "location": ["location", "city", "work_location"],
            "description": ["description", "responsibilities", "summary", "achievements"],
            "institution": ["institution", "college", "university", "school", "institute"],
            "degree": ["degree", "qualification", "education_degree", "course", "graduation_degree"],
            "field_of_study": ["field_of_study", "discipline", "specialization", "stream", "branch", "major", "graduation_branch"],
            "grade": ["gpa", "cgpa", "grade", "score", "percentage", "score_percentage", "graduation_cgpa", "result"],
            "start_year": ["start_year", "start_date", "from_year"],
            "end_year": ["end_year", "end_date", "graduation_year", "completion_year"],
        }
        for key in aliases.get(field_name, [field_name]):
            v = self.clean_text(record.get(key))
            if v:
                return v
        return ""

    # ============================================================
    # ELEMENTS.JSON NORMALIZATION
    # ============================================================

    def normalize_elements(self,raw: Any) -> List[Dict[str, Any]]:
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
            orig_idx = original.get("_element_index")
            if orig_idx is None:
                orig_idx = original.get("element_index")
            e["_element_index"] = int(orig_idx) if orig_idx is not None and str(orig_idx).isdigit() else i

            if not e.get("tag") and e.get("html_tag"):
                e["tag"] = e["html_tag"]
            if not e.get("type") and e.get("input_type"):
                e["type"] = e["input_type"]
            if not e.get("type") and e.get("field_type"):
                e["type"] = e["field_type"]
            if not e.get("tag") and e.get("field_type"):
                ft = self.norm(e["field_type"])
                if ft == "select":
                    e["tag"] = "select"
                elif ft in {"checkbox", "radio", "text", "date", "number", "email", "tel", "file", "url"}:
                    e["tag"] = "input"
                    e["type"] = ft
                elif ft == "textarea":
                    e["tag"] = "textarea"
                elif ft == "button":
                    e["tag"] = "button"
            if not e.get("control_type") and e.get("field_type"):
                e["control_type"] = e["field_type"]
            if not e.get("id") and self.clean_text(e.get("selector", "")).startswith("#"):
                m_id = re.match(r"^#([A-Za-z0-9_.:\\-]+)", self.clean_text(e.get("selector", "")))
                if m_id:
                    e["id"] = m_id.group(1).replace("\\", "")
            if not e.get("question"):
                e["question"] = (
                    e.get("label") or e.get("aria_label") or e.get("placeholder")
                    or e.get("name") or e.get("id") or ""
                )
            if not e.get("role") and e.get("runtime_role"):
                e["role"] = e.get("runtime_role")
            if not e.get("selector"):
                if e.get("css_selector"):
                    e["selector"] = e["css_selector"]
                elif e.get("xpath"):
                    e["selector"] = e["xpath"]

            opts = e.get("options")
            if opts is None:
                opts = []
            if not isinstance(opts, list):
                opts = [opts]

            normalized_opts = []
            for opt in opts:
                if isinstance(opt, dict):
                    normalized_opts.append(
                        self.compact_dict(
                            {
                                "label": opt.get("label") or opt.get("text") or opt.get("name"),
                                "text": opt.get("text") or opt.get("label"),
                                "value": opt.get("value"),
                                "selected": opt.get("selected"),
                                "checked": opt.get("checked"),
                                "selector": opt.get("selector"),
                            }
                        )
                    )
                else:
                    normalized_opts.append({"label": self.clean_text(opt), "text": self.clean_text(opt)})
            e["options"] = normalized_opts

            if not e["options"]:
                for key in ("option_list", "choices", "select_options"):
                    if isinstance(e.get(key), list):
                        e["options"] = [
                            {"label": self.option_display(x), "text": self.option_display(x), **(x if isinstance(x, dict) else {})}
                            for x in e[key]
                        ]
                        break

            if not e["options"] and isinstance(e.get("radio_options"), list):
                e["options"] = e["radio_options"]

            try:
                gi = self._detect_group_index(e)
                if gi is not None:
                    e["_group_index"] = gi
                    sec = self._detect_section_type(e)
                    if sec:
                        e["_section_type"] = sec
                    rep_field = self._infer_repeatable_field(e)
                    if rep_field:
                        e["_repeatable_field"] = rep_field
            except Exception:
                pass
            result.append(e)
        return result

    def element_search_text(self,e: Dict[str, Any]) -> str:
        
        values = [
            e.get("question"), e.get("label"), e.get("name"), e.get("id"),
            e.get("placeholder"), e.get("aria_label"), e.get("autocomplete"),
            e.get("type"), e.get("tag"), e.get("role"), e.get("control_type"),
        ]
        for opt in e.get("options", []) or []:
            if isinstance(opt, dict):
                values.extend([opt.get("label"), opt.get("text"), opt.get("value")])
        return self.norm(" ".join(self.clean_text(x) for x in values if x is not None))

    def is_captcha(self, e: Dict[str, Any]) -> bool:
        sel = self.clean_text(e.get("selector") or "").lower()
        raw_id = self.clean_text(e.get("id") or "").lower()
        raw_name = self.clean_text(e.get("name") or "").lower()
        if any(x in sel or x in raw_id or x in raw_name for x in ["captcha", "recaptcha", "hcaptcha", "turnstile", "cf-turnstile"]):
            return True
        s = self.element_search_text(e)
        return any(x in s for x in ["captcha", "recaptcha", "hcaptcha", "turnstile", "i am not a robot"])

    def is_submit(self,e: Dict[str, Any]) -> bool:
        
        t = self.norm(e.get("type"))
        tag = self.norm(e.get("tag"))
        if t in {"checkbox", "radio", "text", "email", "tel", "file", "textarea"}:
            return False
        if tag in {"select", "textarea"}:
            return False
        ident = " ".join(self.norm(e.get(k)) for k in ("id", "name", "selector", "class", "className"))
        if any(w in ident for w in ["close", "modal", "dismiss", "cancel", "accessibility", "userway", "feedback", "newsletter", "search", "menu", "nav"]):
            return False
        if self.is_noise_control(e):
            return False
        s = self.element_search_text(e)
        if any(w in s for w in ["close", "modal", "dismiss", "cancel", "accessibility"]):
            return False
        if any(w in ident for w in ["submitbutton", "submit-button", "submit_button", "btnsubmit", "btn-submit", "applybutton"]):
            return True
        if "submit" in ident and any(w in ident for w in ["button", "btn", "jobapplication"]):
            return True
        return (
            "submit application" in s
            or "apply now" in s
            or "send application" in s
            or "submit form" in s
            or (t == "submit" and ("apply" in s or "submit" in s))
            or (s.strip() in {"submit", "apply", "submit application", "apply now", "send application"})
        )

    def looks_like_resume_upload(self,e: Dict[str, Any]) -> bool:
        t = self.norm(e.get("type"))
        tag = self.norm(e.get("tag"))
        ctype = self.norm(e.get("control_type"))
        if t in {"checkbox", "radio", "submit", "button", "reset"} or tag == "button" or ctype in {"checkbox", "radio", "button", "submit_button", "checkbox_group", "radio_group"}:
            return False
        if tag == "input" and t in {"text", "hidden", "password", "email", "number", "tel"} and not e.get("is_file_trigger") and not e.get("upload_context"):
            return False

        raw_id = self.clean_text(e.get("id") or "").lower()
        raw_name = self.clean_text(e.get("name") or "").lower()
        sel = self.clean_text(e.get("selector") or "").lower()
        lbl = self.clean_text(e.get("label") or e.get("question") or "").lower()

        # Profile picture, avatar, photo, attachments are NOT resume upload!
        if any(p in raw_id or p in raw_name or p in sel or p in lbl for p in [
            "profilepic", "profile_pic", "picture", "photo", "avatar", "headshot",
            "attachmentsupload", "other_attachments", "other attachments",
        ]):
            return False
        
        if "attachment" in lbl and "resume" not in lbl and "cv" not in lbl:
            return False

        if (e.get("field_key") == "resume" or e.get("field_name") == "resume") and (t == "file" or bool(e.get("is_file_trigger")) or bool(e.get("upload_context"))):
            return True

        hay = self.canonical_text(
            " ".join(
                str(e.get(k) or "")
                for k in ("question", "label", "placeholder", "name", "id", "control_type", "upload_context", "text", "selector", "data_testid")
            )
        )
        is_file = t == "file" or (tag == "input" and t == "file") or bool(e.get("upload_context")) or bool(e.get("is_file_trigger")) or "file" in self.norm(e.get("control_type"))
        if not is_file and not any(re.search(r"\b" + re.escape(w) + r"\b", hay) for w in ("drag", "drop", "browse", "upload")):
            return False

        if is_file and ("resume" in hay or "cv" in hay or "curriculum vitae" in hay):
            return True

        if "resume" in raw_id or "resume" in raw_name or "resumepath" in raw_id or "cv" in raw_id:
            return is_file

        return any(
            x in hay
            for x in (
                "upload resume", "upload your resume", "drop your files", "drop files here",
                "attach resume", "upload cv", "curriculum vitae", "upload_file", "resume here",
            )
        ) or (("resume" in hay or "cv" in hay) and any(re.search(r"\b" + re.escape(w) + r"\b", hay) for w in ("upload", "browse", "attach", "file", "attachment", "select", "choose", "drop")))


    def is_file_upload(self,e: Dict[str, Any]) -> bool:
        t = self.norm(e.get("type"))
        tag = self.norm(e.get("tag"))
        control = self.norm(e.get("control_type"))
        ft = self.norm(e.get("field_type") or "")
        if t == "file" or ft == "file" or "file" in control or (tag == "input" and t == "file") or bool(e.get("upload_context")) or bool(e.get("is_file_trigger")):
            return True
        return self.looks_like_resume_upload(e)


    def is_phone_element(self,e: Dict[str, Any]) -> bool:
        if self.is_noise_control(e):
            return False
        tag = self.norm(e.get("tag"))
        t = self.norm(e.get("type"))
        ctrl = self.norm(e.get("control_type"))
        if tag == "select" or t in {"select", "radio", "checkbox"} or ctrl in {"select", "radio", "radio_group", "checkbox", "checkbox_group"}:
            return False
        if self.is_phone_country_element(e):
            return False
        if e.get("field_key") in {"phone", "mobile", "mobile_number", "mobilenumber", "contact_number"} or e.get("field_name") in {"phone", "mobile", "mobile_number", "mobilenumber"}:
            return True
        t = self.norm(e.get("type"))
        if t in {"tel", "phone"}:
            return True
        ctrl = self.norm(e.get("control_type"))
        if ctrl == "phone":
            return True
        question = self.canonical_text(e.get("question") or e.get("label") or e.get("placeholder") or "")
        if any(k in question for k in ("phone number", "mobile number", "contact number", "telephone", "cell number", "enter a phone", "enter mobile", "mobile")):
            return True
        ident = " ".join(self.norm(e.get(k)) for k in ("id", "name", "autocomplete"))
        cls = self.norm(e.get("class") or e.get("className") or "")
        if "iti__tel" in cls or "phoneinput" in cls:
            return True
        return bool(re.search(r"phone|mobile|telephone|contact_?num", ident))

    def is_noise_control(self,e: Dict[str, Any]) -> bool:
        sel = self.clean_text(e.get("selector") or "").lower()
        raw_id = self.clean_text(e.get("id") or "").lower()
        lbl = self.clean_text(e.get("label") or e.get("question") or "").lower()
        raw_name = self.clean_text(e.get("name") or "").lower()

        if "hp_" in raw_id or "hp_" in raw_name or "hp_" in sel:
            return True
        if raw_id.startswith("hp") and any(x in raw_id for x in ["name", "email", "phone", "title", "job"]):
            return True
        # Internal hidden radio state inputs (e.g. #qRadio + 0, #qRadio + 2)
        if raw_id.startswith("qradio") or "qradio" in raw_name:
            return True
        if any(p in sel or p in raw_id or p in lbl for p in ["{{", "}}", "ltprop", "cxprop"]):
            return True
        
        cls = self.norm(e.get("class") or e.get("className") or "")
        role = self.norm(e.get("role") or e.get("runtime_role") or "")
        ident = self.norm(e.get("id") or "")
        label = self.canonical_text(e.get("label") or e.get("aria_label") or e.get("text"))
        if "iti__country" in cls and "selected" not in cls:
            return True
        if "iti__search" in cls or ident.endswith("search-input") or ident.endswith("search-box"):
            return True
        if "select__input" in cls and not self.clean_text(e.get("id")):
            return True
        if "select2-selection" in cls or ident.startswith("select2-") or "select2-hidden-accessible" in cls:
            return True
        if self.norm(e.get("tag")) == "input":
            val = self.clean_text(e.get("value") or "").lower()
            if val in {"years", "months", "days", "inr", "usd", "hrs", "hours"}:
                return True
        if "requiredinput" in cls.replace("-", "") or "required-input" in cls:
            return True
        if ident in {"uploadfilename", "upload-file-name", "upload_file_name"} and "resume" not in label:
            return True
        if (self.norm(e.get("tag")) == "input" and self.norm(e.get("type")) == "button"
                and "upload" in label and "resume" not in label and "cv" not in label):
            return True
        if (self.norm(e.get("tag")) == "input" and not self.clean_text(e.get("id"))
                and not e.get("options") and "nth-of-type" in str(e.get("selector") or "")
                and not label and not self.clean_text(e.get("question")) and not self.clean_text(e.get("placeholder"))
                and "current location" not in label and "enter your answer" not in label
                and self.norm(e.get("type")) not in {"tel", "email", "file"} and not e.get("_important")):
            return True
        if str(e.get("aria_hidden") or e.get("aria-hidden") or "").lower() == "true":
            return True
        if role == "option" and re.search(r"\+\d{1,4}$", label):
            return True
        if label in {"search", "toggle flyout"}:
            return True
        if any(x in label or x in cls or x in ident for x in [
            "userway", "uwy", "accessibility", "a11y", "equalweb",
            "highlight title", "highlight-title", "highlight titles",
            "contrast", "bigger text", "text magnifier", "screen reader",
            "dyslexia", "pause animation", "hide image", "line height",
            "text spacing", "reading mask", "ask about", "chat with us", "live chat", "chat-widget"
        ]):
            return True
        if "mkto" in cls or "mkto" in ident:
            return True
        rect = e.get("rect") or {}
        if e.get("visible") is False and rect.get("width") == 0 and rect.get("height") == 0:
            if not self.looks_like_resume_upload(e) and self.norm(e.get("type")) != "file":
                return True
        # Ignore the read-only filename input shown after upload
        if ident == "resume" and self.norm(e.get("type")) == "text" and e.get("readonly"):
            return True
        return False


    def is_phone_country_element(self,e: Dict[str, Any]) -> bool:
        
        if self.is_noise_control(e):
            return False
        if e.get("phone_country_picker") or "phone_country" in self.norm(e.get("control_type")):
            return True
        ident = " ".join(self.norm(e.get(k)) for k in ("id", "name", "aria_label", "selector", "question", "label"))
        cls = self.norm(e.get("class") or e.get("className") or "")
        if "iti__tel" in cls:
            return False
        if "iti__selected-country" in cls or "iti__flag-container" in cls:
            return True
        clean_id = ident.replace(" ", "").replace("_", "").replace("-", "").replace(".", "").replace("\\", "")
        if any(k in clean_id for k in ("countrycode", "dialcode", "phonecode", "callingcode", "countrydial", "phonecountry", "mobilecountry")):
            return True
        if ident.strip() in {"country", "country_code", "phone_country", "dial_code", "phonecountry"}:
            return True
        return any(x in ident for x in ["country code", "dial code", "calling code", "selected country", "phone number country", "phone country"])


    # ============================================================
    # DETERMINISTIC FIELD MATCHING
    # ============================================================
    def field_label_text(self,e: Dict[str, Any]) -> str:
        return self.canonical_text(
            " ".join(
                self.clean_text(x)
                for x in [
                    e.get("label"), e.get("question"), e.get("placeholder"),
                    e.get("name"), e.get("id"),
                ]
                if x
            )
        )

    def rule_match_score(self,e: Dict[str, Any], phrases: List[str]) -> int:
        q = self.canonical_text(e.get("question") or e.get("label"))
        label = self.canonical_text(e.get("label"))
        score = 0
        long_question = len(q) > 90

        if ("applying for" in q or "applied for" in q or "which role" in q) and any(p in {"job title", "designation", "title", "role"} for p in phrases):
            return 0

        for phrase in phrases:
            p = self.canonical_text(phrase)
            if not p:
                continue
            if label == p or q == p:
                score += 100
            elif not long_question and re.search(r"\b" + re.escape(p) + r"\b", label):
                score += 70
            elif not long_question and re.search(r"\b" + re.escape(p) + r"\b", q):
                score += 50
            elif long_question and len(p.split()) >= 3 and re.search(r"\b" + re.escape(p) + r"\b", q):
                score += 40

        if self.is_phone_country_element(e):
            score -= 100
        if any(p in {"full name", "your name", "candidate name", "name", "complete name"} for p in [self.canonical_text(x) for x in phrases]):
            if any(part in q for part in ["first name", "middle name", "last name", "surname", "given name", "company", "employer", "organization", "organisation", "school", "college", "university", "institution", "refer", "referee", "reference", "client", "location", "city", "address", "state", "place", "postal", "zip"]):
                return 0
            ident = " ".join(self.norm(e.get(k)) for k in ("id", "name"))
            if any(part in ident for part in ["firstname", "middlename", "lastname", "first_name", "middle_name", "last_name", "company", "employer", "organization", "school", "college", "university", "refer", "reference", "location", "city", "address", "state", "place"]):
                return 0
        if "country" in q and any(p in {"location", "current location"} for p in [self.canonical_text(x) for x in phrases]):
            score -= 80
        if any(x in q for x in ["describe your", "rationale", "evidence", "kubernetes"]):
            if any(self.canonical_text(p) in {"experience", "college", "university", "location"} for p in phrases):
                score -= 80

        return score

    def load_important_fields(self) -> List[Dict[str, Any]]:
        if not Path(IMPORTANT_FIELDS_FILE).exists():
            return []
        raw = self.load_json(IMPORTANT_FIELDS_FILE)
        if isinstance(raw, dict):
            fields = raw.get("fields") or raw.get("elements") or []
            return [x for x in fields if isinstance(x, dict)]
        if isinstance(raw, list):
            return [x for x in raw if isinstance(x, dict)]
        return []


    # ============================================================
    # SEMANTIC FIELD-KEY CORRECTION
    # ============================================================
    # The extraction can assign wrong field_key values (e.g. field_key="full_name"
    # for a Company Name input, or field_key="country" for a Designation input).
    # This function inspects the selector, question, and context to derive the
    # true semantic meaning and returns a corrected field_key.

    def _correct_field_key_from_semantics(
        self,
        field: Dict[str, Any],
        element: Optional[Dict[str, Any]] = None,
    ) -> str:

        original_key = self.normalize_field_key(field.get("field_key"))
        selector = self.clean_text(field.get("selector") or (element or {}).get("selector") or "")
        question = self.clean_text(
            field.get("question")
            or (element or {}).get("question")
            or (element or {}).get("label")
            or ""
        )
        q_lower = question.lower()
        sel_lower = selector.lower()
        el = element or {}

        # Check if wrongly classified as work/education date when it's actually availability / joining date
        if any(k in sel_lower for k in ["availablefrom", "available_from", "earliest_joining", "earliest_start", "available_date"]):
            return "earliest_joining_date"
        if any(k in q_lower for k in ["earliest joining", "available from", "when can you start"]):
            return "earliest_joining_date"

        # Check if wrongly classified as phone/email/name when it's a select dropdown for referral source
        if original_key in {"phone", "email", "full_name"} and (self.norm(el.get("tag")) == "select" or self.norm(el.get("type")) == "select" or self.control_kind(el) == "select"):
            if any(k in sel_lower for k in ["referral", "source", "heard", "how"]):
                return "referral_source"
            if any(k in q_lower for k in ["how did you hear", "referral", "source", "hear about"]):
                return "referral_source"
            return "custom_question"

        if original_key in {"education_start_date", "education_end_date", "work_start_date", "work_end_date"}:
            return original_key

        # 0. Check for resume file upload first (prevent incorrect mappings to full_name)
        el = element or {}
        el_type = self.norm(el.get("type"))
        if el_type not in {"checkbox", "radio"} and self.control_kind(el) not in {"checkbox", "radio"}:
            if self.looks_like_resume_upload(el) or (
                (self.is_file_upload(el) or el_type == "file" or bool(el.get("is_file_trigger"))) and (
                    any(k in sel_lower for k in ["#resume", "resume-upload", "upload-resume", "cv-upload", "attach-resume"])
                    or any(k in q_lower for k in ["upload resume", "attach resume", "drop your resume", "upload cv"])
                )
            ):
                return "resume"

        if (el_type == "checkbox" or self.control_kind(el) == "checkbox") and any(k in sel_lower or k in q_lower for k in ["consent", "privacy policy", "terms and conditions", "i agree", "agree to"]):
            return "agreement"

        if original_key == "full_name" and (el_type == "url" or any(x in sel_lower or x in q_lower for x in ["url", "link", "linkedin", "github", "portfolio", "site"])):
            if "linkedin" in sel_lower or "linkedin" in q_lower:
                return "linkedin"
            if "github" in sel_lower or "github" in q_lower:
                return "github"
            return "portfolio"

        # 1. Check selector patterns first (most reliable)
        for pattern, corrected_key in _SELECTOR_SEMANTIC_MAP:
            if re.search(pattern, sel_lower, re.I):
                if corrected_key != original_key:
                    return corrected_key
                return original_key  # selector confirms the key is right

        # 2. Check question text
        for phrases, corrected_key in _QUESTION_SEMANTIC_MAP:
            if (self.norm(el.get("type")) in {"radio", "checkbox"} or self.control_kind(el) in {"radio", "checkbox"}) and corrected_key in {"graduation_branch", "graduation_degree", "graduation_college", "company_name", "job_title", "first_name", "last_name", "full_name", "email"}:
                continue
            if any(re.search(r"\b" + re.escape(p) + r"\b", q_lower) for p in phrases):
                if corrected_key != original_key:
                    return corrected_key
                return original_key

        # 3. Detect experience vs education section from selector index pattern
        # e.g. #companyName_0 or #WorkExperiences[0].EmployerName
        m_idx = re.search(r"[_\[\-](\d+)", sel_lower)
        if m_idx:
            if any(k in sel_lower for k in ["experience", "company", "designation", "joining", "relieving", "employer", "work"]):
                if "employer" in sel_lower or "company" in sel_lower:
                    return "company_name"
                if "designation" in sel_lower or "title" in sel_lower:
                    return "job_title"
                if "role" in sel_lower or "description" in sel_lower or "responsibilit" in sel_lower:
                    return "description"
                if "exp_start" in sel_lower or "startyear" in sel_lower or "joining" in sel_lower:
                    return "work_start_date"
                if "exp_end" in sel_lower or "endyear" in sel_lower or "relieving" in sel_lower:
                    return "work_end_date"
                if "current" in sel_lower:
                    return "currently_working_here"
                if "location" in sel_lower:
                    return "work_location"
                if original_key in {"full_name", "first_name", "last_name", "email", "phone",
                                    "country", "nationality", "gender", "current_location"}:
                    return ""  # unresolvable, skip this field
            if any(k in sel_lower for k in ["education", "degree", "branch", "college", "university", "course", "qualification"]):
                if "name" in sel_lower or "degree" in sel_lower or "course" in sel_lower:
                    return "graduation_degree"
                if "institute" in sel_lower or "college" in sel_lower or "university" in sel_lower:
                    return "graduation_college"
                if "result" in sel_lower or "grade" in sel_lower or "cgpa" in sel_lower or "score" in sel_lower:
                    return "graduation_cgpa"
                if "edu_start" in sel_lower or "startyear" in sel_lower:
                    return "education_start_date"
                if "edu_end" in sel_lower or "endyear" in sel_lower:
                    return "education_end_date"
                if "branch" in sel_lower or "special" in sel_lower:
                    return "graduation_branch"
                if "location" in sel_lower:
                    return "education_location"
                if original_key in {"full_name", "first_name", "last_name", "email", "phone",
                                    "country", "nationality", "gender"}:
                    return ""  # unresolvable

        return original_key

    def apply_important_field_hints(self,elements: List[Dict[str, Any]], important: List[Dict[str, Any]]) -> None:
        by_index = {e["_element_index"]: e for e in elements}
        by_selector = {self.clean_text(e.get("selector")): e for e in elements}
        for field in important:
            e = None
            idx = field.get("element_index")
            if idx is not None:
                try:
                    e = by_index.get(int(idx))
                except Exception:
                    e = None
            if e is None:
                e = by_selector.get(self.clean_text(field.get("selector")))
            if not e:
                continue
            # Apply semantic correction to field_key before storing
            corrected_key = self._correct_field_key_from_semantics(field, e)
            e["_field_key"] = corrected_key or field.get("field_key")
            e["_field_key_original"] = field.get("field_key")  # keep original for diagnostics
            e["_important"] = True
            e["_important_type"] = field.get("field_type")
            if self.normalize_field_key(e.get("_field_key")) in SELECT_FIELD_KEYS or self.norm(e.get("runtime_role") or e.get("role")) == "combobox":
                e["control_type"] = e.get("control_type") or "combobox"

    def infer_action_for_element(self,e: Dict[str, Any], default: str = "fill") -> str:
        kind = self.control_kind(e)
        if kind in {"select", "radio", "checkbox", "upload"}:
            return kind
        if self.normalize_field_key(e.get("_field_key")) in SELECT_FIELD_KEYS:
            return "select"
        return default

    def collect_fill_targets(self, elements: List[Dict[str, Any]], important: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        
        by_index = {e["_element_index"]: e for e in elements}
        by_selector = {self.clean_text(e.get("selector")): e for e in elements}
        targets: List[Dict[str, Any]] = []
        seen = set()

        def add(e: Optional[Dict[str, Any]]) -> None:
            if not e:
                return
            idx = e["_element_index"]
            if idx in seen or self.is_captcha(e) or self.is_submit(e) or e.get("disabled") is True:
                return
            if self.is_noise_control(e):
                return
            if self.norm(e.get("type")) == "hidden":
                return
            seen.add(idx)
            targets.append(e)

        for field in important:
            if self.norm(field.get("field_type")) == "submit" or "submit" in self.norm(field.get("field_key")):
                continue
            e = None
            try:
                e = by_index.get(int(field.get("element_index")))
            except Exception:
                e = None
            if e is None:
                e = by_selector.get(self.clean_text(field.get("selector")))
            add(e)

        for e in self.dedupe_interactive_elements(elements):
            q = self.clean_text(e.get("question") or e.get("label"))
            required = bool(e.get("required") is True or "*" in q or self.norm(e.get("aria_required")) == "true")
            if e.get("_important") or required:
                add(e)
        return targets

    def deterministic_candidates(
        self,
        elements: List[Dict[str, Any]],
        resume_idx: Dict[str, Any],
        important: Optional[List[Dict[str, Any]]] = None,
        resume: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        candidates: List[Dict[str, Any]] = []

        used_elements: set = set()
        important = important or []
        resume = resume or {}
        p = resume.get("prompt", {}) if isinstance(resume, dict) else {}
        edu_records = p.get("education") or [] if isinstance(p, dict) else []
        exp_records = p.get("work_experience") or [] if isinstance(p, dict) else []

        # Education is already ordered higher-to-lower in resume
        edu_records_sorted = list(edu_records)
        
        # Sort experience newest to oldest
        exp_records_sorted = sorted(exp_records, key=lambda x: str(x.get("start_date") or ""), reverse=True)

        for i, el in enumerate(elements):
            if "_element_index" not in el:
                el["_element_index"] = el.get("element_index", i)
        by_index = {e["_element_index"]: e for e in elements}
        by_selector = {self.clean_text(e.get("selector")): e for e in elements}

        # --- 1. Important-field mapping ---
        for field in important:
            raw_key = self.normalize_field_key(field.get("field_key"))
            e = None
            try:
                e = by_index.get(int(field.get("element_index")))
            except Exception:
                e = None
            if e is None:
                e = by_selector.get(self.clean_text(field.get("selector")))
            if not e or self.is_noise_control(e):
                continue

            # FIX: Correct the field_key using semantic analysis of selector/question
            key = self._correct_field_key_from_semantics(field, e)
            if not key:
                continue

            # Detect if element belongs to a repeatable group (e.g. #companyName_0, #degree_1)
            gi = e.get("_group_index")
            if gi is None:
                gi = self._detect_group_index(e)
            if gi is None:
                sel_for_gi = self.clean_text(field.get("selector") or e.get("selector") or "")
                m_gi = re.search(r"[_\[\-](\d+)\]?(?:$|[_\-\.])", sel_for_gi)
                if m_gi:
                    try:
                        gi = int(m_gi.group(1))
                    except Exception:
                        gi = None

            sec = e.get("_section_type")
            if not sec:
                sec = self._detect_section_type(e)
            if not sec:
                sel_s = (self.clean_text(field.get("selector") or "") + " " + self.clean_text(e.get("selector") or "")).lower()
                if any(k in sel_s for k in ["degree", "branch", "university", "college", "course", "education"]):
                    sec = "education"
                elif any(k in sel_s for k in ["company", "designation", "relieving", "joining", "experience", "employer", "work"]):
                    sec = "experience"

            # FIX: Ignore personal info fields mapped to repeatable sections
            if sec in {"experience", "education"}:
                if key in {"full_name", "first_name", "middle_name", "last_name", "email", "phone", "mobile", "current_location", "linkedin", "github"}:
                    continue
            
            # FIX: Ignore bad field_key mappings if label clearly contradicts it
            label_hay = self.canonical_text(e.get("label") or e.get("question") or e.get("placeholder") or "")
            if "job title" in label_hay or "designation" in label_hay:
                if key in {"country", "nationality", "gender", "email", "phone"}:
                    continue
                    
            hay = self.canonical_text(
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
            resume_key = IMPORTANT_RESUME_MAP.get(key) or IMPORTANT_RESUME_MAP.get(self.norm(field.get("field_key") or ""))

            # If repeatable group field, resolve directly from the candidate record!
            if gi is not None and sec in {"education", "experience"}:
                records_to_use = edu_records if sec == "education" else exp_records
                rep_field = e.get("_repeatable_field") or self._infer_repeatable_field(e)
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
                        value = self._record_field_value(rec, rep_field or key)
                        if sec == "education" and rep_field in {"end_date", "end_year"} and (
                            str(value).lower() in {"present", "current", "currently enrolled", ""}
                            or rec.get("currently_enrolled")
                        ):
                            if rep_field == "end_year" and not (self.norm(e.get("type")) == "date" or e.get("is_datepicker")):
                                start_yr = self.clean_text(rec.get("start_date"))
                                m_yr = re.search(r"\b(19\d{2}|20\d{2})\b", start_yr)
                                if m_yr:
                                    deg_low = self.clean_text(rec.get("degree")).lower()
                                    add_yrs = 2 if any(m in deg_low for m in ["master", "mca", "mba", "mtech", "m.sc"]) else 4
                                    value = str(int(m_yr.group(1)) + add_yrs)
                else:
                    value = ""
            else:
                if not resume_key:
                    continue
                value = resume_idx.get(resume_key)

            if self.is_empty(value):
                continue

            if not e or e["_element_index"] in used_elements:
                continue
            if self.is_captcha(e) or self.is_submit(e):
                continue
            if (resume_key == "resume_file_path" or key == "resume") and not self.looks_like_resume_upload(e):
                continue
            if self.is_current_employment_question(e, key) and not self.currently_employed(resume_idx):
                if resume_key in {"current_employer", "current_designation", "company_name",
                                "job_title", "designation", "employer_name"}:
                    value = "NA"
            if (self.norm(e.get("type")) == "date" or e.get("is_datepicker")) and str(value).lower() in {"immediate", "ready", "urgent", "0"}:
                from datetime import datetime
                value = datetime.now().strftime("%Y-%m-%d")
            elif self.looks_like_currency_control(e):
                value = resume_idx.get("salary_currency") or value
            elif resume_key in {"current_salary", "expected_salary"}:
                hay_sal = self.canonical_text(
                    " ".join(str(e.get(k) or "") for k in ("label", "question", "placeholder", "name", "id"))
                )
                val_clean = self.clean_text(value).upper()
                m_num = re.search(r"(\d+(?:\.\d+)?)", val_clean)
                if m_num:
                    num = float(m_num.group(1))
                    is_lpa = "LPA" in val_clean or "LAC" in val_clean or num <= 100
                    ph_digs = self.digits(self.clean_text(e.get("placeholder") or ""))
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
                    value = self.salary_amount_text(value)
            elif self.looks_like_salary_frequency_control(e):
                value = resume_idx.get("salary_frequency") or value
            elif resume_key == "available_to_join_days" or key == "availability":
                field_label_lower = self.field_label_text(e).lower()
                if self.is_meta_instruction(value):
                    continue
                if "week" in field_label_lower:
                    value = "0"
                elif self.norm(e.get("type")) == "number" or "number" in self.norm(e.get("control_type")) or "days" in field_label_lower:
                    if str(value).lower() in {"immediate", "immediately", "ready", "urgent"}:
                        value = "0"
                    elif not self.digits(value):
                        continue
            elif resume_key == "is_currently_working":
                value = "true" if self.is_positive_answer(value) else "false"
            elif key in {"agreement", "consent", "terms_agreement", "candidate_consent"} or resume_key == "agree_yes":
                if self.norm(e.get("type")) == "checkbox" or self.control_kind(e) == "checkbox":
                    value = "true"
            elif self.is_phone_country_element(e) or self.looks_like_dial_code_options(e.get("options") or []):
                opts = e.get("options") or []
                cc = resume_idx.get("country_code") or ""
                cname = resume_idx.get("country") or (DIAL_TO_PRIMARY_COUNTRY.get(self.digits(cc)) if cc else "")
                if not cc and cname and cname.lower() in COUNTRY_NAME_TO_DIAL:
                    cc = "+" + COUNTRY_NAME_TO_DIAL[cname.lower()]
                iso = DIAL_TO_ISO.get(self.digits(cc), "")
                best_opt = None
                if opts:

                    for o in opts:
                        ol = self.clean_text(o.get("label") if isinstance(o, dict) else o).lower()
                        ov = self.clean_text(o.get("value") if isinstance(o, dict) else o).lower()
                        if cname and (cname.lower() == ol or cname.lower() == ov):
                            best_opt = o
                            break
                    # 2. Exact match on ISO code (e.g. "IN", "US", "DE")
                    if not best_opt and iso:
                        for o in opts:
                            ol = self.clean_text(o.get("label") if isinstance(o, dict) else o).lower()
                            ov = self.clean_text(o.get("value") if isinstance(o, dict) else o).lower()
                            if iso.lower() == ov or iso.lower() == ol:
                                best_opt = o
                                break
                    # 3. Match on dial code (e.g. "+91" or "91")
                    if not best_opt and self.digits(cc):
                        for o in opts:
                            ol = self.clean_text(o.get("label") if isinstance(o, dict) else o).lower()
                            ov = self.clean_text(o.get("value") if isinstance(o, dict) else o).lower()
                            if self.digits(cc) == self.digits(ov) or self.digits(cc) == self.digits(ol) or f"+{self.digits(cc)}" in ol:
                                best_opt = o
                                break
                if best_opt and isinstance(best_opt, dict):
                    value = best_opt.get("value") if best_opt.get("value") is not None else best_opt.get("label")
                    cand_opt_lbl = best_opt.get("label")
                    cand_opt_val = best_opt.get("value")
                else:
                    value = cname if not self.looks_like_dial_code_options(opts) else cc
                    cand_opt_lbl = str(value)
                    cand_opt_val = str(value)
                key = "phone_country_code"
                resume_key = "phone_country_code"
            elif resume_key in {"country", "nationality"} or key in {"country", "nationality"}:
                opts = e.get("options") or []
                cc = resume_idx.get("country_code") or ""
                cname = resume_idx.get("country") or (DIAL_TO_PRIMARY_COUNTRY.get(self.digits(cc)) if cc else "")
                nat = resume_idx.get("nationality") or (DIAL_TO_NATIONALITY.get(self.digits(cc)) if cc else "")
                best_opt = None
                if opts:
                    for o in opts:
                        ol = self.clean_text(o.get("label") if isinstance(o, dict) else o).lower()
                        ov = self.clean_text(o.get("value") if isinstance(o, dict) else o).lower()
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

            if self.is_empty(value) or self.is_meta_instruction(value):
                continue

            cand_field = key or resume_key or field.get("field_key")
            is_select_kind = self.norm(e.get("tag")) == "select" or self.control_kind(e) in {"select", "radio", "checkbox"}
            if not is_select_kind and (
                self.norm(e.get("type")) == "url"
                or resume_key in {"linkedin", "github", "portfolio", "portfolio_url", "website", "blog"}
                or "portfolio" in str(cand_field).lower() or "github" in str(cand_field).lower() or ("linkedin" in str(cand_field).lower() and "source" not in str(cand_field).lower())
                or re.search(r"\b(url|website|portfolio link|profile link|github link|linkedin link)\b", self.norm(e.get("question") or ""))
            ):
                if cand_field in {"full_name", "first_name", "last_name", "name"} or not (str(value).startswith("http") or "." in str(value)):
                    sel_l = self.clean_text(e.get("selector") or "").lower()
                    q_l = self.clean_text(e.get("question") or "").lower()
                    if "linkedin" in sel_l or "linkedin" in q_l:
                        value = resume_idx.get("linkedin") or value
                        cand_field = "linkedin"
                    elif "github" in sel_l or "github" in q_l:
                        value = resume_idx.get("github") or value
                        cand_field = "github"
                    else:
                        value = resume_idx.get("portfolio") or resume_idx.get("linkedin") or value
                        cand_field = "portfolio"
                value = self.ensure_https_url(value)

            # Validate that choice elements actually have an option matching the value/option label/value
            opts = e.get("options") or []
            if opts and (self.control_kind(e) in {"radio", "checkbox", "select"} or self.norm(e.get("control_type")) in {"radio_group", "checkbox_group"}):
                opt_labels = [self.clean_text(o.get("label") if isinstance(o, dict) else o).lower() for o in opts]
                opt_values = [self.clean_text(o.get("value") if isinstance(o, dict) else o).lower() for o in opts]
                val_clean = self.clean_text(value).lower()
                cand_lbl_clean = self.clean_text(cand_opt_lbl or "").lower()
                cand_val_clean = self.clean_text(cand_opt_val or "").lower()
                if not any(x in opt_labels or x in opt_values for x in [val_clean, cand_lbl_clean, cand_val_clean] if x):
                    snapped_opt = self.snap_answer_to_options(opts, value)
                    if snapped_opt:
                        cand_opt_lbl = self.clean_text(snapped_opt.get("label") if isinstance(snapped_opt, dict) else snapped_opt)
                        cand_opt_val = self.clean_text(snapped_opt.get("value") if isinstance(snapped_opt, dict) else cand_opt_lbl)
                        value = cand_opt_lbl
                    else:
                        continue

            action = "select" if self.is_phone_country_element(e) else ("check" if (key in {"agreement", "consent", "terms_agreement", "candidate_consent"} or resume_key == "agree_yes") and (self.norm(e.get("type")) == "checkbox" or self.control_kind(e) == "checkbox") else self.infer_action_for_element(e))
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
            if self.is_phone_country_element(e):
                if e["_element_index"] in used_elements:
                    continue
                opts = e.get("options") or []
                cc = resume_idx.get("country_code") or ""
                cname = resume_idx.get("country") or (DIAL_TO_PRIMARY_COUNTRY.get(self.digits(cc)) if cc else "")
                if not cc and cname and cname.lower() in COUNTRY_NAME_TO_DIAL:
                    cc = "+" + COUNTRY_NAME_TO_DIAL[cname.lower()]
                iso = DIAL_TO_ISO.get(self.digits(cc), "")
                best_opt = None
                if opts:
                    for o in opts:
                        ol = self.clean_text(o.get("label") if isinstance(o, dict) else o).lower()
                        ov = self.clean_text(o.get("value") if isinstance(o, dict) else o).lower()
                        if cname and (cname.lower() == ol or cname.lower() == ov):
                            best_opt = o; break
                    if not best_opt and iso:
                        for o in opts:
                            ol = self.clean_text(o.get("label") if isinstance(o, dict) else o).lower()
                            ov = self.clean_text(o.get("value") if isinstance(o, dict) else o).lower()
                            if iso.lower() == ov or iso.lower() == ol:
                                best_opt = o; break
                    if not best_opt and self.digits(cc):
                        for o in opts:
                            ol = self.clean_text(o.get("label") if isinstance(o, dict) else o).lower()
                            ov = self.clean_text(o.get("value") if isinstance(o, dict) else o).lower()
                            if self.digits(cc) == self.digits(ov) or self.digits(cc) == self.digits(ol) or f"+{self.digits(cc)}" in ol:
                                best_opt = o; break
                if best_opt and isinstance(best_opt, dict):
                    p_val = best_opt.get("value") if best_opt.get("value") is not None else best_opt.get("label")
                    p_lbl = best_opt.get("label")
                else:
                    p_val = cname if not self.looks_like_dial_code_options(opts) else cc
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
                self.norm(e.get("type")) == "checkbox"
                or self.norm(e.get("field_type")) == "checkbox"
                or self.norm(e.get("control_type")) == "checkbox"
                or (self.norm(e.get("tag")) == "input" and self.norm(e.get("type")) == "checkbox")
            )
            if not is_chk:
                continue
            ident = " ".join(self.norm(e.get(k)) for k in ("id", "name", "aria_label", "label", "question", "selector", "field_key"))
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
            if e.get("_section_type") == "experience" and self.norm(e.get("type")) == "checkbox":
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
                if idx in used_elements or self.is_noise_control(e):
                    continue
                if self.is_captcha(e) or self.is_submit(e) or self.is_file_upload(e):
                    continue

                score = self.rule_match_score(e, phrases)

                if field == "phone" and self.is_phone_country_element(e):
                    score = 0

                if field == "experience_years":
                    if self.is_phone_element(e):
                        score = 0
                    opts = e.get("options") or []
                    yn = {self.norm(o.get("label") if isinstance(o, dict) else o) for o in opts}
                    if yn & {"yes", "no"} or self.norm(e.get("control_type")) == "radio_group":
                        score = 0

                if field in {"current_salary", "expected_salary"}:
                    path = (str(e.get("selector") or "") + " " + str(e.get("dom_path") or "")).lower()
                    if self.control_kind(e) == "select" or "cu-select" in path or self.looks_like_currency_control(e) or self.looks_like_salary_frequency_control(e):
                        score = 0

                if field in {"company_name", "job_title", "current_employer", "current_designation", "employer_name"}:
                    if self.is_current_employment_question(e, field) and not self.currently_employed(resume_idx):
                        score = 0

                if self.control_kind(e) in {"radio", "checkbox"} or self.norm(e.get("control_type")) in {"radio_group", "checkbox_group"}:
                    opts = e.get("options") or []
                    opt_labels = [self.clean_text(o.get("label") if isinstance(o, dict) else o).lower() for o in opts]
                    val_cand = self.clean_text(resume_idx.get(resume_key) or "").lower()
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
                    ph = self.canonical_text(e.get("placeholder") or "")
                    records_to_use = edu_records_sorted if sec == "education" else exp_records_sorted
                    
                    if sec == "education":
                        # Prefer to anchor by degree name
                        if "degree" in field or "institution" in field or "course" in field:
                            for idx, r in enumerate(records_to_use):
                                match_val = self.canonical_text(r.get("degree") or "")
                                if match_val and (match_val in ph or ph in match_val):
                                    gi = idx
                                    break
                    else:
                        if "company" in field or "job_title" in field:
                            for idx, r in enumerate(records_to_use):
                                match_val = self.canonical_text(r.get("company") or "") or self.canonical_text(r.get("job_title") or "")
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
                        value = self._record_field_value(records_to_use[gi], rep_field)
                    
                    if self.is_empty(value):
                        continue

                    candidates.append({
                        "element_index": e["_element_index"],
                        "field": f"{sec}[{gi}].{rep_field}",
                        "value": str(value),
                        "option_label": None,
                        "option_value": None,
                        "action": self.infer_action_for_element(e),
                        "source": "resume_repeatable",
                        "confidence": score,
                        "group_index": gi,
                        "section": sec,
                    })
                    used_elements.add(e["_element_index"])
                continue

            best_score, best = matches[0]
            value = resume_idx.get(resume_key)
            if self.is_empty(value):
                continue
            action = self.infer_action_for_element(best)
            cand_val = str(value)
            opt_label = None
            opt_val = None

            if field == "experience_years" and best.get("options"):
                y = int(self.digits(value) or 1)
                best_opt = None
                for opt in best.get("options", []):
                    lbl = self.clean_text(opt.get("label") if isinstance(opt, dict) else opt).lower()
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
                        lbl = self.clean_text(opt.get("label") if isinstance(opt, dict) else opt).lower()
                        if "1" in lbl:
                            best_opt = opt; break
                if best_opt:
                    opt_label = self.clean_text(best_opt.get("label") if isinstance(best_opt, dict) else best_opt)
                    opt_val = self.clean_text(best_opt.get("value") if isinstance(best_opt, dict) else opt_label)
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
            if e["_element_index"] in used_elements or self.is_captcha(e) or self.is_submit(e):
                continue
            if self.looks_like_currency_control(e):
                curr_val = resume_idx.get("salary_currency") or "INR"
                best_opt = self.resolve_option_from_element(e, curr_val)
                opt_lbl = self.clean_text(best_opt.get("label") if best_opt else curr_val)
                opt_val = self.clean_text(best_opt.get("value") if best_opt else opt_lbl)
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
            if e["_element_index"] in used_elements or self.is_captcha(e) or self.is_submit(e):
                continue
            if self.looks_like_salary_frequency_control(e):
                freq_val = "Yearly"
                best_opt = self.resolve_option_from_element(e, freq_val)
                opt_lbl = self.clean_text(best_opt.get("label") if best_opt else freq_val)
                opt_val = self.clean_text(best_opt.get("value") if best_opt else opt_lbl)
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
                if self.is_captcha(e) or self.is_submit(e):
                    continue
                if not self.is_phone_element(e):
                    continue
                if self.is_phone_country_element(e):
                    continue
                s = self.element_search_text(e)
                score = 40
                if self.norm(e.get("type")) in {"tel", "text", "number"}:
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
            if idx in used_elements or self.is_noise_control(e) or self.is_captcha(e) or self.is_submit(e):
                continue
            q_text = self.canonical_text(
                " ".join(str(e.get(k) or "") for k in ("question", "label", "placeholder", "id", "name"))
            )
            if not q_text:
                continue
            
            name = self.norm(e.get("name") or "")
            sel = self.norm(e.get("selector") or "")

            if any(w in q_text for w in ["house no", "street / locality", "street locality", "address line 1", "residential address"]):
                addr = resume_idx.get("address") or "153 Vishun Garden Rajendra Park"
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

    # ============================================================
    # AI BATCH MAPPING
    # ============================================================

    def compact_resume_for_ai(self,resume: Dict[str, Any]) -> Dict[str, Any]:
        p = resume.get("prompt", {}) if isinstance(resume, dict) else {}
        personal = p.get("personal_info", {}) if isinstance(p, dict) else {}
        education = p.get("education", []) if isinstance(p, dict) else []
        work = p.get("work_experience", []) if isinstance(p, dict) else []
        skills = p.get("skills", {}) if isinstance(p, dict) else {}
        currently_working = any(bool(w.get("currently_working")) for w in work if isinstance(w, dict))
        years = p.get("total_experience_years", resume.get("year_of_exp"))
        return self.compact_dict({
            "personal_info": self.compact_dict({
                "full_name": personal.get("full_name"),
                "email": personal.get("email"),
                "phone": personal.get("phone"),
                "city": personal.get("city"),
                "state": personal.get("state"),
                "country": personal.get("country"),
                "current_location": personal.get("current_location") or personal.get("city"),
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
                "is_fresher": (not currently_working) and float(self.digits(years) or 0) <= 1,
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

    def looks_like_dial_code_options(self,options: List[Any]) -> bool:
        labels = []
        for opt in options or []:
            label = self.option_display(opt) if not isinstance(opt, str) else self.clean_text(opt)
            if label:
                labels.append(label)
        if len(labels) < 8:
            return False
        hits = sum(1 for label in labels if re.search(r"\+\d{1,4}\s*$", label) or re.search(r"\+\d{1,4}$", re.sub(r"\s+", "", label)))
        return hits / len(labels) >= 0.5

    def salary_amount_text(self,value: Any) -> str:
        t = self.clean_text(value)
        m = re.search(r"(\d+(?:\.\d+)?)\s*(lpa|lacs|lakhs)?", t, flags=re.I)
        return m.group(1) if m else t

    def looks_like_currency_control(self,e: Dict[str, Any]) -> bool:
        t = self.norm(e.get("type"))
        if t in {"checkbox", "radio"}:
            return False
        if self.norm(e.get("field_key")) == "currency":
            return True
        hay = self.canonical_text(" ".join(str(e.get(k) or "") for k in ("id", "name", "label", "question", "placeholder")))
        if any(x in hay for x in ["experience", "coding", "python", "skill", "background", "education", "degree"]):
            return False
        if "currency" in hay or "ctc_unit" in hay or "salary_unit" in hay or e.get("id") == "s-c-n":
            return True
        if any(x in hay for x in ["pay", "salary", "ctc"]) and any(x in hay for x in ["inr", "usd", "eur", "gbp"]):
            return True
        opts = e.get("options") or []
        if not opts:
            return False
        tokens = set()
        for o in opts[:30]:
            val = self.clean_text(o.get("value") if isinstance(o, dict) else "").upper()
            lbl = self.clean_text(o.get("label") if isinstance(o, dict) else str(o)).upper()
            if val:
                tokens.add(val)
            if lbl:
                tokens.add(lbl[:3])
                tokens.add(self.canonical_text(lbl))
        currency_tokens = {"INR", "USD", "EUR", "GBP", "CAD", "AUD", "AED", "CHF", "JPY", "CNY", "THB", "SGD", "$", "₹", "€", "£", "inr", "usd", "eur", "gbp"}
        hits = sum(1 for x in tokens if x in currency_tokens)
        return hits >= 2

    def looks_like_salary_frequency_control(self,e: Dict[str, Any]) -> bool:
        t = self.norm(e.get("type"))
        tag = self.norm(e.get("tag"))
        hay = self.canonical_text(" ".join(str(e.get(k) or "") for k in ("id", "name", "label", "question", "placeholder")))
        if tag == "input" and t in {"text", "number", ""}:
            if not (e.get("options")):
                if not any(w in hay for w in ["salary frequency", "pay frequency", "compensation frequency", "rate frequency", "salary_freq", "pay_freq"]):
                    return False
        labels = [self.canonical_text(self.option_display(o)) for o in (e.get("options") or [])[:10]]
        if any(x in {"monthly", "yearly", "annual", "per month", "per year"} for x in labels):
            return True
        if any(term in hay for term in ["sms", "text message", "message", "consent", "opt in", "agreement"]):
            return False
        if e.get("id") == "s-y-m" or "month_year" in hay:
            return True
        if any(w in hay for w in ["salary frequency", "pay frequency", "compensation frequency", "rate frequency"]):
            return True
        if any(w in hay for w in ["salary", "pay", "ctc", "compensation"]) and any(w in hay for w in ["salary frequency", "pay frequency", "compensation frequency"]):
            return True
        return False

    def _user_token_kind(self,value: str) -> str:
        t = self.canonical_text(value)
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

    def semantic_assign_user_values(self,fields: List[Dict[str, Any]], values: List[str]) -> List[str]:
        unused = [v for v in values if self.clean_text(v)]
        out: List[str] = []

        def take(kinds: List[str]) -> str:
            if "any" in kinds:
                return unused.pop(0) if unused else ""
            for i, v in enumerate(unused):
                if self._user_token_kind(v) in kinds:
                    return unused.pop(i)
            return ""

        for e in fields:
            q = self.canonical_text(e.get("question") or e.get("label") or e.get("placeholder"))
            kind = self.control_kind(e)
            if self.looks_like_currency_control(e):
                out.append(take(["currency"]) or take(["any"]))
            elif "available" in q or ("join" in q and "salary" not in q):
                out.append(take(["days"]) or take(["any"]))
            elif "hear" in q:
                out.append(take(["source", "text"]) or take(["any"]))
            elif "reference" in q:
                out.append(take(["yesno", "text"]) or take(["any"]))
            elif "salary" in q and kind != "select":
                out.append(take(["salary"]))
            elif "salary" in q and self.looks_like_currency_control(e):
                out.append(take(["currency"]) or take(["any"]))
            elif "jee" in q:
                out.append(take(["year"]) or take(["any"]))
            else:
                out.append(take(["any"]))
        return out

    def persist_user_answers_to_resume(self, resume: Dict[str, Any], fields: List[Dict[str, Any]], answers: List[Dict[str, Any]]) -> None:
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
            val = self.clean_text(item.get("option_label") or item.get("answer"))
            if not val or self.norm(item.get("action")) == "skip":
                continue
            e = by_index.get(int(item["element_index"]))
            if not e:
                continue
            q = self.canonical_text(e.get("question") or e.get("label"))
            key = self.normalize_field_key(e.get("_field_key")) or re.sub(r"[^a-z0-9]+", "_", q)[:40]
            supplied[key] = val
            if "gender" in q:
                personal["gender"] = val
            elif "ethnicity" in q or re.search(r"\brace\b", q):
                personal["ethnicity"] = val
            elif self.looks_like_currency_control(e):
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
        print(f"Saved user answers into {RESUME_FILE}")

    def looks_like_group_heading(self,label: Any) -> bool:
        text = self.canonical_text(label)
        if not text:
            return True
        return (
            text.endswith("time zones")
            or text.endswith("time zone")
            or text.endswith("regions")
            or text in {"loading", "searching", "no options", "no results"}
        )

    def normalize_field_key(self,key: Any) -> str:
        k = self.norm(key)
        k = re.sub(r"\[\]$", "", k)
        k = re.sub(r"--\d+$", "", k)
        return k

    def _employment_haystack(self,e: Optional[Dict[str, Any]] = None, field_key: str = "") -> str:
        e = e or {}
        return self.canonical_text(
            " ".join(
                str(x or "")
                for x in [
                    field_key, e.get("_field_key"), e.get("question"),
                    e.get("label"), e.get("placeholder"),
                    e.get("name"), e.get("id"),
                ] ))

    def is_previous_employment_question(self,e: Optional[Dict[str, Any]] = None, field_key: str = "") -> bool:
        hay = self._employment_haystack(e, field_key)
        return any(
            term in hay
            for term in (
                "previous employer", "previous company", "last employer",
                "last company", "past employer", "internship company", "most recent intern",
            )
        )

    def is_current_employment_question(self,e: Optional[Dict[str, Any]] = None, field_key: str = "") -> bool:
        key = self.normalize_field_key(field_key or (e or {}).get("_field_key"))
        if self.is_previous_employment_question(e, field_key):
            return False
        if key in {
            "current_employer", "current_company", "current_designation",
            "currently_working_here", "current_position", "current_role",
        }:
            return True
        hay = self._employment_haystack(e, field_key)
        return any(
            term in hay
            for term in (
                "current employer", "current company", "current organization",
                "current designation", "current position", "current role",
                "current job", "present employer", "present company",
                "currently working", "currently employed",
            )
        )

    def currently_employed(self,resume_idx: Dict[str, Any]) -> bool:
        if resume_idx.get("currently_working") is True:
            return True
        return self.is_positive_answer(resume_idx.get("is_currently_working"))

    def usable_options(self,e: Dict[str, Any]) -> List[Any]:
        opts = list(e.get("options") or [])
        if self.looks_like_dial_code_options(opts) and not self.is_phone_country_element(e):
            return []
        cleaned = []
        for opt in opts:
            if self.looks_like_group_heading(self.option_display(opt)):
                continue
            cleaned.append(opt)
        return cleaned

    def options_for_ai(self , e: Dict[str, Any]) -> List[Any]:
        opts = list(e.get("options") or [])
        if self.looks_like_dial_code_options(opts) and not self.is_phone_country_element(e):
            return []
        return opts[:40]

    def compact_element_for_ai(self , e: Dict[str, Any]) -> Dict[str, Any]:
        opts = []
        for opt in self.options_for_ai(e):
            if isinstance(opt, dict):
                opts.append(self.compact_dict({
                    "label": opt.get("label") or opt.get("text"),
                    "value": opt.get("value"),
                    "is_group": True if opt.get("is_group") or self.looks_like_group_heading(opt.get("label") or opt.get("text")) else None,
                }))
            else:
                opts.append({"label": self.clean_text(opt)})
        return self.compact_dict({
            "element_index": e.get("_element_index") if e.get("_element_index") is not None else e.get("element_index"),
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
            "control_kind": self.control_kind(e),
        })

    def leftover_important_elements(
        self,
        elements: List[Dict[str, Any]],
        important: List[Dict[str, Any]],
        filled_indexes: set,
    ) -> List[Dict[str, Any]]:
        filled = {int(i) for i in filled_indexes if i is not None}
        targets = self.collect_fill_targets(elements, important) if important else self.dedupe_interactive_elements(elements)
        leftover = []
        for e in targets:
            idx = e.get("_element_index")
            if idx is None or int(idx) in filled:
                continue
            if self.is_captcha(e) or self.is_submit(e) or self.is_noise_control(e):
                continue
            if e.get("disabled") is True or self.norm(e.get("type")) == "hidden":
                continue
            leftover.append(e)
        return self.dedupe_interactive_elements(leftover)

    def build_ai_prompt(self, remaining: List[Dict[str, Any]], resume: Dict[str, Any], deterministic: List[Dict[str, Any]]) -> str:
        remaining_for_ai = [self.compact_element_for_ai(e) for e in remaining if not self.is_captcha(e) and not self.is_submit(e)]
        resume_json = json.dumps(self.compact_resume_for_ai(resume), ensure_ascii=False, separators=(",", ":"), default=str)
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
    20. For experience threshold / minimum requirement questions (e.g. "Do you have at least 4 yrs of ... experience?", "5+ years of experience", "Less than 7 years" vs "7 or More"): Compare the applicant's total experience (~1 year) against the required number. If a yes/no question requires at least X years (where X > 1) and applicant's experience is less than X, choose "No" (e.g. at least 4 yrs -> "No"). For range options, choose the lowest/entry range (e.g. "Less than 7 years", "0-1 years", "1-2 years").
    21. For technical skills questions (e.g. "Do you have experience on Python and do hands on Coding?"): If the skill is in the applicant's primary programming languages (Python is in skills), ALWAYS choose "Yes".
    22. For voluntary EEO / demographic disclosure questions (Race, Ethnicity, Veteran Status, Hispanic/Latino, Disability, Gender): If options include "Choose not to disclose", "Decline to self-identify", "I do not wish to answer", "I am not a protected veteran", or "Decline", ALWAYS choose that decline/undisclosed option unless the applicant explicitly instructed otherwise.
    23. EMPLOYMENT: The applicant is NOT currently working (internships already ended). For Current Company / Current Employer / Current Position / Current Designation: if the field is required, answer NA or None or Fresher (never leave blank, never copy a past internship). If optional, NA is still preferred over empty. Previous-employer fields may use the last internship. Currently working = No.
    24. For questions asking for multiple percentages/CGPA (e.g., "Mention percentages from 10th to highest"), list them all separated by commas (e.g., "10th: 92%, 12th: 83%, B.Tech: 8.02 CGPA"). DO NOT output the degree name (e.g., "Senior Secondary") in this field.
    25. Job Title / Designation / Role questions must be filled with the exact job title from the resume (e.g., 'AI/ML Developer Intern'). NEVER output 'India' or 'Other' unless specifically requested.
    26. For location / onsite / WFO (Work from office) questions (e.g. "Are you comfortable WFO - Gurgaon?"): Compare office location against applicant's city/current_location. If applicant is based in that city (e.g. applicant lives in Gurugram/Gurgaon and office is Gurgaon), choose "Yes".
    27. For Tier-1 engineering college questions (e.g. "Have you completed your graduation/ post-graduation from Tier 1 engineering college?"): Evaluate applicant's education institution (Tier 1 includes IITs, NITs, BITS, IIITs, IISc). If applicant graduated from a non-Tier-1 private or state university (e.g. K.R. Mangalam University), choose "No".
    28. For checkbox-based Yes/No questions split into separate " - Yes" and " - No" checkbox elements: If the answer is Yes, set answer="true", action="check" for the Yes element, and action="skip" for the No element. If the answer is No, set action="skip" for the Yes element and answer="true", action="check" for the No element.
    29. Try to get answer of each question on based of resume understant user preference -> like where understand experience + salary if not given on based of experience 
    Return ONLY JSON:
    {{"answers":[{{"element_index":0,"selector":"EXACT_SELECTOR","frame_index":null,"frame_url":null,"answer":"value","action":"fill|select|radio|checkbox|upload|skip","option_label":null,"option_value":null,"reason":"short"}}]}}
    '''

    def extract_json_from_text(self, text: str) -> Dict[str, Any]:
        text = (text or "").strip()
        text = re.sub(r"^\s*```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```\s*$", "", text)
        text = text.strip()

        candidates = [text]

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

        raise ValueError("Amazon Nova did not return valid JSON.\n" + text[:4000])

    def call_bedrock(self, prompt: str) -> Dict[str, Any]:
        
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
        return self.extract_json_from_text(text)

    # ============================================================
    # PLAYWRIGHT FRAME / LOCATOR HELPERS
    # ============================================================

    async def visible_locator(self,locator: Locator) -> Optional[Locator]:
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

    def all_frames(self, page: Page) -> List[Frame]:
        return list(page.frames)

    def frame_matches(
        self,
        frame: Frame,
        frame_index: Optional[int],
        frame_url: Optional[str],
        page: Page,
        frame_name: Optional[str] = None,
    ) -> bool:
        if frame_name:
            try:
                if self.clean_text(frame_name) == self.clean_text(frame.name):
                    return True
            except Exception:
                pass
        if frame_index is not None:
            frames = self.all_frames(page)
            try:
                if 0 <= int(frame_index) < len(frames):
                    if frames[int(frame_index)] == frame:
                        return True
            except Exception:
                pass
        if frame_url:
            try:
                return self.clean_text(frame_url) in self.clean_text(frame.url)
            except Exception:
                pass
        return False

    async def _selector_variants(self, e: Dict[str, Any]) -> List[str]:
        variants: List[str] = []
        selector = self.clean_text(e.get("selector"))
        if selector and "{{" not in selector and "ltprop" not in selector.lower():
            clean_selector = re.sub(r'#(\d[\w\-]*)', r'[id="\1"]', selector)
            variants.append(clean_selector)
            if clean_selector != selector:
                variants.append(selector)
            if selector.startswith("#") and all(x not in selector[1:] for x in [" ", ">", "+", "~"]):
                raw_id = selector[1:]
                safe_id = raw_id.replace(chr(34), chr(92) + chr(34))
                variants.append(f'[id="{safe_id}"]')
        element_id = self.clean_text(e.get("id"))
        if element_id and "{{" not in element_id and "ltprop" not in element_id.lower():
            safe_id = element_id.replace('"', '\\"')
            variants.extend([f'[id="{safe_id}"]', f'#{re.sub(r"([\\[\\]():.#>+~*\\s])", r"\\\\\\1", element_id)}'])
        name = self.clean_text(e.get("name"))
        if name and "{{" not in name:
            safe_name = name.replace('"', '\\"')
            variants.append(f'[name="{safe_name}"]')
        return list(dict.fromkeys(variants))

    async def _visible_or_native(self, loc: Locator) -> Optional[Locator]:
        visible = await self.visible_locator(loc)
        if visible:
            return visible
        try:
            count = await loc.count()
            for i in range(min(count, 20)):
                candidate = loc.nth(i)
                tag = self.norm(await candidate.evaluate("el => el.tagName.toLowerCase()"))
                typ = self.norm(await candidate.get_attribute("type"))
                if tag in {"select", "textarea", "input"} or typ in {"file", "radio", "checkbox", "hidden"}:
                    return candidate
        except Exception:
            pass
        return None

    async def locator_from_element(
        self,
        page: Page,
        e: Dict[str, Any],
        timeout: int = LOCATOR_TIMEOUT_MS,
    ) -> Tuple[Optional[Locator], Optional[Frame], str]:
        frame_index = e.get("frame_index")
        frame_url = self.clean_text(e.get("frame_url"))
        frame_name = self.clean_text(e.get("frame_name"))
        frames = self.all_frames(page)
        preferred: List[Frame] = []

        if frame_name:
            for fr in frames:
                try:
                    if self.clean_text(fr.name) == frame_name and fr not in preferred:
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
                    if frame_url in self.clean_text(fr.url) and fr not in preferred:
                        preferred.append(fr)
                except Exception:
                    pass
        for fr in frames:
            if fr not in preferred:
                preferred.append(fr)

        variants = await self._selector_variants(e)
        for fr in preferred:
            for selector in variants:
                try:
                    loc = fr.locator(selector)
                    candidate = await self._visible_or_native(loc)
                    if candidate:
                        return candidate, fr, "selector"
                except Exception:
                    continue

        question = self.clean_text(e.get("question") or e.get("label"))
        label = self.clean_text(e.get("label"))
        placeholder = self.clean_text(e.get("placeholder"))
        name = self.clean_text(e.get("name"))
        element_id = self.clean_text(e.get("id"))
        role = self.norm(e.get("role") or e.get("runtime_role"))

        for fr in preferred:
            for text in [label, question]:
                if not text:
                    continue
                clean_t = re.sub(r"^[\s*✱\"“”]+|[\s*✱\"“”]+$", "", text).strip()
                if not clean_t:
                    continue
                try:
                    loc = fr.get_by_label(clean_t, exact=False)
                    candidate = await self.visible_locator(loc)
                    if candidate:
                        return candidate, fr, "label"
                except Exception:
                    pass
                try:
                    loc = fr.locator(f'div:has(> label:has-text("{clean_t}"))').locator('input, textarea, select, [contenteditable="true"]').first
                    candidate = await self.visible_locator(loc)
                    if candidate:
                        return candidate, fr, "container_label"
                except Exception:
                    pass
                try:
                    loc = fr.locator(f'label:has-text("{clean_t}") ~ input, label:has-text("{clean_t}") ~ textarea, label:has-text("{clean_t}") ~ select').first
                    candidate = await self.visible_locator(loc)
                    if candidate:
                        return candidate, fr, "sibling_label"
                except Exception:
                    pass

            if placeholder:
                try:
                    loc = fr.get_by_placeholder(placeholder, exact=False)
                    candidate = await self.visible_locator(loc)
                    if candidate:
                        return candidate, fr, "placeholder"
                except Exception:
                    pass
            if element_id:
                try:
                    safe_id = element_id.replace(chr(34), chr(92) + chr(34))
                    loc = fr.locator(f'[id="{safe_id}"]')
                    candidate = await self._visible_or_native(loc)
                    if candidate:
                        return candidate, fr, "id_attribute"
                except Exception:
                    pass
            if name:
                try:
                    safe_name = name.replace(chr(34), chr(92) + chr(34))
                    loc = fr.locator(f'[name="{safe_name}"]')
                    candidate = await self._visible_or_native(loc)
                    if candidate:
                        return candidate, fr, "name"
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
                qn = self.norm(question)
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
                        hay = self.norm(" ".join(str(v) for v in attrs.values() if v))
                        label_hay = self.norm(attrs.get("label", ""))
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
                if best and best_score >= 60:
                    return best, fr, "semantic"

        return None, None, "not_found"

    async def scroll_page_progressively(self,page: Page) -> None:
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

    def is_ignorable_frame(self,frame: Frame) -> bool:
        url = (getattr(frame, "url", None) or "").lower()
        name = (getattr(frame, "name", None) or "").lower()
        combined = f"{url} {name}"
        chat_ad_patterns = [
            "conversations-visitor", "live-chat", "livechat", "tawk.to", "crisp.chat",
            "intercom", "drift.com", "zendesk.com", "google.com/recaptcha",
            "recaptcha/api2", "doubleclick.net", "googletagmanager",
            "google-analytics", "facebook.com/plugins", "embedded-viral-link",
        ]
        for pat in chat_ad_patterns:
            if pat in combined:
                return True
        return False

    async def count_all_visible_inputs(self,page: Page) -> int:
        total = 0
        for frame in page.frames:
            if self.is_ignorable_frame(frame):
                continue
            try:
                count = await frame.locator(
                    "input:visible, textarea:visible, select:visible, cu-select, [contenteditable='true'], [role='textbox']"
                ).count()
                total += count
            except Exception:
                pass
        return total

    async def ensure_application_form_open(self,page: Page) -> bool:
        try:
            visible_inputs = await self.count_all_visible_inputs(page)
            dialogs = await page.locator('[role="dialog"]:visible, .modal:visible, [class*="modal"]:visible').count()
            if visible_inputs >= 4 and dialogs > 0:
                return True
            if visible_inputs >= 8:
                return True

            apply_selectors = [
                'button:has-text("Apply Now")', 'a:has-text("Apply Now")',
                '[role="button"]:has-text("Apply Now")', 'button:has-text("Apply for Role")',
                'button:has-text("Apply for this job")', 'button:has-text("Apply for this position")',
                'button:has-text("Apply Online")', 'button:has-text("Quick Apply")',
                'button:has-text("Easy Apply")', 'button:has-text("Submit Application")',
                'button:has-text("Start Application")', 'a:has-text("Apply for this job")',
                'a:has-text("Apply for Role")', 'a:has-text("Apply Online")',
                'a:has-text("Quick Apply")', 'a:has-text("Easy Apply")',
                ':is(button, a, [role="button"], input[type="button"], input[type="submit"]):has-text("Apply")',
                ':is(button, a, [role="button"]):has-text("Interested")',
                '[data-target*="apply" i]', '[data-toggle="modal"][data-target*="apply" i]',
                'a[href*="#apply" i]', '[data-testid*="apply" i]', '[data-qa*="apply" i]',
                '[id*="apply" i]:is(button, a, [role="button"])',
                '[class*="apply" i]:is(button, a, [role="button"])',
            ]

            all_contexts = [page] + [f for f in page.frames if not self.is_ignorable_frame(f)]
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
                        btn_text = self.clean_text(await loc.inner_text())
                        text_to_check = btn_text or ""
                        if not re.search(r"\b(apply|application|interested|join us|register)\b", text_to_check, re.I):
                            try:
                                attr_str = await loc.evaluate("el => (el.id || '') + ' ' + (el.className || '') + ' ' + (el.getAttribute('data-target') || '') + ' ' + (el.getAttribute('href') || '')")
                                if not re.search(r"apply", attr_str, re.I):
                                    continue
                            except Exception:
                                continue
                        if re.search(r"\b(filter|login|sign in|search|subscribe|download|share)\b", text_to_check, re.I):
                            continue
                        print(f"Detected Application CTA: '{btn_text or sel}' -> Clicking to open form...")
                        try:
                            await loc.scroll_into_view_if_needed()
                        except Exception:
                            pass
                        try:
                            await loc.click(timeout=3000, force=True)
                            clicked = True
                        except Exception:
                            pass
                        if not clicked:
                            try:
                                await loc.evaluate("""el => {
                                    if (window.$) { try { window.$(el).trigger('click'); } catch(e){} }
                                    el.click();
                                    try { el.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, view: window })); } catch(e){}
                                    const target = el.getAttribute('data-target') || el.getAttribute('href');
                                    if (target && target.startsWith('#')) {
                                        const modal = document.querySelector(target);
                                        if (modal) {
                                            if (window.$ && window.$.fn.modal) { try { window.$(modal).modal('show'); } catch(e){} }
                                            else { modal.classList.add('show', 'in'); modal.style.display = 'block'; modal.style.opacity = '1'; modal.style.visibility = 'visible'; }
                                        }
                                    }
                                }""")
                                clicked = True
                            except Exception:
                                pass
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
                for _ in range(15):
                    await page.wait_for_timeout(400)
                    new_count = await self.count_all_visible_inputs(page)
                    if new_count > visible_inputs or new_count >= 3:
                        print(f"Application form opened! Visible inputs across frames: {visible_inputs} -> {new_count}")
                        await page.wait_for_timeout(800)
                        return True
                print(f"Application CTA clicked. Current visible inputs count: {await self.count_all_visible_inputs(page)}")
                return True

        except Exception as exc:
            print(f"Notice in ensure_application_form_open: {exc}")

        return False

    # ============================================================
    # REPEATABLE-SECTION DISCOVERY & EXPANSION
    # ============================================================

    async def count_section_groups(self,frame: Frame, section_type: str) -> int:
        keys = _SECTION_KEYWORD_MAP.get(section_type, [section_type])
        try:
            result = await frame.evaluate("""(keys) => {
                const indexes = new Set();
                const inputs = document.querySelectorAll('input, textarea, select');
                for (const el of inputs) {
                    const id = (el.id || '').toLowerCase();
                    const name = (el.getAttribute('name') || '').toLowerCase();
                    const hay = id + ' ' + name;
                    if (!keys.some(k => hay.includes(k))) continue;
                    let m = hay.match(/[\\[_-](\\d+)\\]?(?:_|$|\\W)/);
                    if (m) indexes.add(m[1]);
                }
                if (indexes.size > 0) return indexes.size;

                const vis = el => {
                    if (!el) return false;
                    const r = el.getBoundingClientRect();
                    const s = getComputedStyle(el);
                    return r.width > 4 && r.height > 4
                        && s.display !== 'none' && s.visibility !== 'hidden';
                };
                const containers = new Set();
                const candidates = document.querySelectorAll(
                    '[class*="entry"], [class*="card"], [class*="item"], '
                    + '[class*="group"], fieldset, section, article'
                );
                for (const c of candidates) {
                    if (!vis(c)) continue;
                    const t = (c.innerText || '').toLowerCase();
                    if (!keys.some(k => t.includes(k))) continue;
                    if (!c.querySelector('input, textarea, select')) continue;
                    containers.add(c);
                }
                return containers.size;
            }""", keys)
            return int(result or 0)
        except Exception:
            return 0

    async def find_and_mark_add_for_section(self, frame: Frame, section_type: str) -> bool:
        keys = _SECTION_KEYWORD_MAP.get(section_type, [section_type])
        try:
            return bool(await frame.evaluate("""({keys, sectionType}) => {
                const vis = el => {
                    if (!el) return false;
                    const r = el.getBoundingClientRect();
                    const s = getComputedStyle(el);
                    return r.width > 4 && r.height > 4
                        && s.display !== 'none' && s.visibility !== 'hidden'
                        && Number(s.opacity) !== 0;
                };
                document.querySelectorAll('[data-jobfill-add-btn]')
                    .forEach(b => b.removeAttribute('data-jobfill-add-btn'));

                const buttons = [...document.querySelectorAll(
                    'button, a, [role="button"], input[type="button"], [class*="pointer"], [class*="cursor"], span, div'
                )].filter(vis);

                let best = null, bestScore = 0;
                for (const btn of buttons) {
                    const text = (btn.innerText || btn.textContent || '').replace(/\\s+/g, ' ').trim();
                    const aria = (btn.getAttribute('aria-label') || btn.getAttribute('title') || '').trim();
                    const cls  = String(btn.className || '').toLowerCase();
                    const hay  = (text + ' ' + aria + ' ' + cls).toLowerCase();

                    if (/(number|no\\.?|count|quantity|total|how\\s+many)\\s+(of\\s+)?(post|position|vacanc|opening|hiring|employee|applicant)/i.test(hay)) continue;
                    if (/\\b(delete|remove|close|dismiss|save|submit|apply|cancel|search|login|filter|subscribe|share|download|upload|next|previous|back)\\b/i.test(hay)) continue;

                    const isAdd = (
                        text === '+' || text === 'Add' || text === '+ Add'
                        || text.startsWith('+')
                        || /\\badd\\b/i.test(text)
                        || /\\badd\\b/i.test(aria)
                        || /\\badd\\b/.test(cls)
                    );
                    if (!isAdd) continue;
                    if (text.length > 60) continue;

                    let score = 0;
                    if (keys.some(k => hay.includes(k))) {
                        score = 10;
                    } else {
                        let p = btn.parentElement;
                        let near = false;
                        for (let i = 0; i < 6 && p; i++) {
                            const pt = (p.innerText || '').toLowerCase();
                            if (keys.some(k => pt.includes(k))) { near = true; break; }
                            p = p.parentElement;
                        }
                        if (!near) continue;
                        score = 5;
                    }
                    if (/\\badd\\b/i.test(text)) score += 2;
                    if (score > bestScore) { bestScore = score; best = btn; }
                }
                if (!best) return false;
                try { best.scrollIntoView({block: 'center'}); } catch (e) {}
                best.setAttribute('data-jobfill-add-btn', '1');
                return true;
            }""", {"keys": keys, "sectionType": section_type}))
        except Exception:
            return False

    async def _wait_for_dom_growth(self,page: Page, before_count: int, timeout_ms: int = 2500) -> bool:
        deadline = time.monotonic() + timeout_ms / 1000
        while time.monotonic() < deadline:
            try:
                if await self.count_all_visible_inputs(page) > before_count:
                    return True
            except Exception:
                pass
            await asyncio.sleep(0.15)
        return False

    async def _click_marked_add(self,page: Page, frame: Frame) -> bool:
        marker = frame.locator("[data-jobfill-add-btn='1']").first
        if await marker.count() == 0:
            return False
        try:
            await marker.scroll_into_view_if_needed(timeout=800)
        except Exception:
            pass
        clicked = False
        try:
            await marker.click(timeout=1500, force=True)
            clicked = True
        except Exception:
            try:
                await marker.evaluate("el => el.click()")
                clicked = True
            except Exception:
                pass
        try:
            await marker.evaluate("el => el.removeAttribute('data-jobfill-add-btn')")
        except Exception:
            pass
        return clicked

    async def _expand_repeating_section_generic(self,page: Page, frame: Frame, section_type: str, needed: int) -> int:
        added = 0
        if needed <= 0:
            return 0
        for _ in range(needed + 3):
            current = await self.count_section_groups(frame, section_type)
            if current >= needed:
                break
            before_inputs = await self.count_all_visible_inputs(page)
            if not await self.find_and_mark_add_for_section(frame, section_type):
                break
            if not await self._click_marked_add(page, frame):
                break
            grew = await self._wait_for_dom_growth(page, before_inputs, timeout_ms=2500)
            if not grew:
                await page.wait_for_timeout(300)
            added += 1
        return added

    async def discover_new_fields_after_expansion(
        self,
        page: Page,
        existing_elements: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        known_keys: set = set()
        by_id: Dict[str, Dict[str, Any]] = {}
        by_name: Dict[str, Dict[str, Any]] = {}
        name_counts: Dict[str, int] = {}
        for e in existing_elements:
            q = self.canonical_text(e.get("question") or e.get("label") or "")
            if q:
                known_keys.add(q)
            raw_id = self.clean_text(e.get("id"))
            if raw_id:
                by_id[raw_id] = e
            raw_name = self.clean_text(e.get("name"))
            if raw_name:
                by_name[raw_name] = e
                name_counts[raw_name] = name_counts.get(raw_name, 0) + 1

        new_elements: List[Dict[str, Any]] = []
        next_index = max((int(e.get("_element_index", -1)) for e in existing_elements), default=-1) + 1

        for frame_idx, frame in enumerate(page.frames):
            if self.is_ignorable_frame(frame):
                continue
            try:
                rows = await frame.evaluate("""() => {
                    const vis = el => {
                        if (!el) return false;
                        const r = el.getBoundingClientRect();
                        const s = getComputedStyle(el);
                        return r.width > 4 && r.height > 4
                            && s.display !== 'none' && s.visibility !== 'hidden';
                    };
                    const labelOf = el => {
                        let t = '';
                        if (el.id) {
                            const l = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
                            if (l) t = l.innerText || '';
                        }
                        if (!t && el.closest('label')) t = el.closest('label').innerText || '';
                        if (!t) {
                            const p = el.parentElement;
                            if (p) {
                                const l = p.querySelector('label');
                                if (l) t = l.innerText || '';
                            }
                        }
                        if (!t) t = el.getAttribute('aria-label') || '';
                        return (t || '').replace(/\\s+/g, ' ').trim();
                    };
                    const out = [];
                    let idx = 0;
                    const nodes = document.querySelectorAll(
                        'input:not([type=hidden]):not([type=submit]):not([type=button]):not([type=reset]), textarea, select'
                    );
                    for (const el of nodes) {
                        if (!vis(el)) continue;
                        el.setAttribute('data-jobfill-scan-idx', String(idx));
                        const rect = el.getBoundingClientRect();
                        out.push({
                            scan_idx: idx,
                            tag: el.tagName.toLowerCase(),
                            type: (el.getAttribute('type') || '').toLowerCase(),
                            role: (el.getAttribute('role') || '').toLowerCase(),
                            id: el.id || '',
                            name: el.getAttribute('name') || '',
                            placeholder: el.getAttribute('placeholder') || '',
                            ariaLabel: el.getAttribute('aria-label') || '',
                            label: labelOf(el),
                            required: !!el.required || el.getAttribute('aria-required') === 'true',
                            value: el.value || '',
                            rect: { x: rect.x, y: rect.y, width: rect.width, height: rect.height },
                            options: el.tagName === 'SELECT'
                                ? Array.from(el.options).map(o => ({ label: (o.textContent || '').trim(), value: o.value }))
                                : []
                        });
                        idx++;
                    }
                    return out;
                }""")
            except Exception:
                continue

            for row in (rows or []):
                typ = row.get("type", "")
                if typ in {"file", "checkbox", "radio", "submit", "button", "reset"}:
                    continue
                if row.get("required") is not True:
                    continue
                label = self.clean_text(row.get("label") or row.get("ariaLabel") or row.get("placeholder") or "")
                if not label:
                    continue
                qkey = self.canonical_text(label)

                el_id = self.clean_text(row.get("id"))
                el_name = self.clean_text(row.get("name"))
                matched_existing = by_id.get(el_id) if el_id else None
                if not matched_existing and el_name and name_counts.get(el_name) == 1:
                    matched_existing = by_name.get(el_name)

                if matched_existing is not None:
                    # Update existing element label/question if new label is cleaner/better
                    if label and not any(k in label.lower() for k in ["http", "@", "+91"]):
                        old_lbl = self.clean_text(matched_existing.get("label") or "")
                        if not old_lbl or any(k in old_lbl.lower() for k in ["http", "@", "+91", "negotiable"]):
                            matched_existing["label"] = label
                            matched_existing["question"] = label
                    continue

                if qkey in known_keys:
                    continue

                scan_idx = row.get("scan_idx")
                selector = f'[data-jobfill-scan-idx="{scan_idx}"]'
                new_el = {
                    "_element_index": next_index,
                    "_runtime_discovered": True,
                    "tag": row.get("tag"),
                    "type": typ,
                    "role": row.get("role"),
                    "id": row.get("id"),
                    "name": row.get("name"),
                    "placeholder": row.get("placeholder"),
                    "aria_label": row.get("ariaLabel"),
                    "label": label,
                    "question": label,
                    "selector": selector,
                    "frame_index": frame_idx,
                    "frame_url": frame.url,
                    "required": True,
                    "rect": row.get("rect"),
                    "options": row.get("options") or [],
                }
                try:
                    gi = self._detect_group_index(new_el)
                    if gi is not None:
                        new_el["_group_index"] = gi
                    sec = self._detect_section_type(new_el)
                    if sec:
                        new_el["_section_type"] = sec
                    rep_field = self._infer_repeatable_field(new_el)
                    if rep_field:
                        new_el["_repeatable_field"] = rep_field
                except Exception:
                    pass

                new_elements.append(new_el)
                known_keys.add(qkey)
                next_index += 1

        return new_elements

    async def expand_repeating_sections(self, page: Page, resume: Optional[Dict[str, Any]] = None) -> int:
        resume = resume or {}
        p = resume.get("prompt", {}) if isinstance(resume, dict) else {}

        needed_edu = min(2, len(p.get("education") or [])) if isinstance(p, dict) else 0
        needed_exp = min(2, len(p.get("work_experience") or [])) if isinstance(p, dict) else 0

        targets: List[Tuple[str, int]] = []
        if needed_edu > 0:
            targets.append(("education", needed_edu))
        if needed_exp > 0:
            targets.append(("experience", needed_exp))

        expanded = 0
        all_frames_list = [page] + [f for f in page.frames if f != page.main_frame and not self.is_ignorable_frame(f)]

        for frame in all_frames_list:
            for section_type, needed in targets:
                added = await self._expand_repeating_section_generic(page, frame, section_type, needed)
                expanded += added

        try:
            tabular_btns = await page.locator(
                "button.tabular-group-add, [class*='tabular'] button:has-text('Add')"
            ).all()
            for b in tabular_btns:
                try:
                    if await b.is_visible():
                        aria = (await b.get_attribute("aria-label") or "").lower()
                        if "0 of" in aria or "add an entry" in aria:
                            await b.scroll_into_view_if_needed(timeout=1000)
                            await b.click(timeout=1200)
                            expanded += 1
                            await page.wait_for_timeout(400)
                except Exception:
                    pass
        except Exception:
            pass

        try:
            generic_add_loc = page.locator(":is(button, a, [role='button'], [class*='pointer'], [class*='cursor'], span, div):has-text('Add')")
            cnt = await generic_add_loc.count()
            clicked_generic = set()
            for idx in range(min(cnt, 30)):
                b = generic_add_loc.nth(idx)
                try:
                    if not await b.is_visible():
                        continue
                    txt = self.clean_text(await b.inner_text()) or ""
                    if not re.match(r"^\s*\+?\s*add\b", txt, re.I):
                        continue
                    if len(txt) > 60:
                        continue
                    if any(x in txt.lower() for x in ["file", "attachment", "document", "resume", "photo"]):
                        continue
                    try:
                        has_child = await b.evaluate("""el => {
                            return Array.from(el.querySelectorAll(':is(button, a, [role=\"button\"], [class*=\"pointer\"], span, div)')).some(c => {
                                const t = (c.innerText || '').trim();
                                return /^\\s*\\+?\\s*add\\b/i.test(t) && t.length < 60;
                            });
                        }""")
                        if has_child:
                            continue
                    except Exception:
                        pass
                    norm_txt = re.sub(r"[\s+*]+", " ", txt).strip().lower()
                    if norm_txt in clicked_generic:
                        continue
                    await b.scroll_into_view_if_needed(timeout=1000)
                    await b.click(timeout=1200)
                    clicked_generic.add(norm_txt)
                    expanded += 1
                    await page.wait_for_timeout(600)
                except Exception:
                    pass
        except Exception:
            pass

        if expanded > 0:
            await page.wait_for_timeout(500)
            print(f"Repeating sections expansion complete. ({expanded} sections expanded)")

        return expanded

    async def wait_for_form(self, page: Page, resume: Optional[Dict[str, Any]] = None) -> None:
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=PAGE_LOAD_TIMEOUT_MS)
        except Exception:
            pass

        for _ in range(25):
            try:
                total_count = await self.count_all_visible_inputs(page)
                apply_btn = await page.locator(':is(button, a, [role="button"]):has-text("Apply")').count()
                if total_count >= 3 or apply_btn > 0:
                    break
            except Exception:
                pass
            await page.wait_for_timeout(300)

        await self.ensure_application_form_open(page)

        for _ in range(20):
            try:
                total_count = await self.count_all_visible_inputs(page)
                if total_count >= 5:
                    break
            except Exception:
                pass
            await page.wait_for_timeout(300)

        await self.expand_repeating_sections(page, resume)

        try:
            await page.wait_for_timeout(500)
        except Exception:
            pass

    # ============================================================
    # RUNTIME ENRICHMENT: Discover cu-select / custom dropdowns
    # ============================================================

    async def enrich_cu_select_elements(self,page: Page, elements: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        frame = page.main_frame

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

        def _is_placeholder_label(raw: str) -> bool:
            s = self.norm(raw)
            return not s or s.startswith("select") or s in {"option", "choose", "pick"}

        cu_select_elements = []
        for e in elements:
            sel = str(e.get("selector") or e.get("dom_path") or "")
            raw_label = self.clean_text(e.get("label") or e.get("question") or "")
            role = self.norm(e.get("role"))
            tag = self.norm(e.get("tag"))
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

        for i, e in enumerate(cu_select_elements):
            if i >= len(cu_selects):
                break
            info = cu_selects[i]
            real_label = self.clean_text(info.get("label"))
            is_required = info.get("required", False)
            if real_label and not _is_placeholder_label(real_label):
                e["label"] = real_label
                e["question"] = real_label
                if is_required:
                    e["required"] = True
            e["control_type"] = "combobox"
            e["runtime_role"] = "combobox"

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
                try:
                    await pg.wait_for_timeout(400)
                except Exception:
                    pass
                try:
                    options = await fr.evaluate(ENRICH_CU_SELECT_OPTIONS_JS)
                except Exception:
                    options = []
                try:
                    await pg.keyboard.press("Escape")
                    await pg.wait_for_timeout(200)
                except Exception:
                    try:
                        await pg.mouse.click(10, 10)
                        await pg.wait_for_timeout(200)
                    except Exception:
                        pass
                return options or []

            try:
                loc, loc_frame, _ = await self.locator_from_element(page, e)
                if loc:
                    try:
                        options = await asyncio.wait_for(
                            _discover_options(page, frame, e, loc),
                            timeout=8.0
                        )
                    except asyncio.TimeoutError:
                        options = []
                        print(f"  [{e['_element_index']}] {real_label}: timeout discovering options")
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

        return elements

    # ============================================================
    # NATIVE / CUSTOM CONTROL OPERATIONS
    # ============================================================

    async def fire_react_events(self,locator: Locator) -> None:
        try:
            await locator.evaluate("""el => {
                const tag = el.tagName.toLowerCase();
                if (tag === 'input' || tag === 'textarea') {
                    const proto = tag === 'textarea' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
                    const desc = Object.getOwnPropertyDescriptor(proto, 'value');
                    if (desc && desc.set) { desc.set.call(el, el.value); }
                }
                el.dispatchEvent(new Event('input', {bubbles: true}));
                el.dispatchEvent(new Event('change', {bubbles: true}));
            }""")
        except Exception:
            pass

    async def fill_text(self, locator: Locator, value: str) -> bool:
        value = str(value)
        # Check if target element has a maximum character constraint (e.g. data-maxlength="300")
        try:
            max_len = await locator.evaluate("""el => {
                const dMax = parseInt(el.getAttribute('data-maxlength') || el.getAttribute('data-val-maxlength-max') || el.getAttribute('data-rule-maxlength') || '0', 10) || 0;
                const attrMax = el.maxLength > 0 ? el.maxLength : 0;
                return (attrMax > 0 && dMax > 0) ? Math.min(attrMax, dMax) : (attrMax || dMax);
            }""")
            if max_len and max_len > 0 and len(value) > max_len:
                truncated = value[:max_len]
                last_space = truncated.rfind(" ")
                if last_space > max_len // 2:
                    truncated = truncated[:last_space].rstrip(",;.- \n")
                value = truncated
        except Exception:
            pass

        try:
            is_kendo_num = await locator.evaluate("""(el, val) => {
                if (!window.jQuery) return false;
                let $ = window.jQuery;
                let wrap = el.closest('.k-numeric-wrap') || el.parentElement;
                let widget = $(el).data('kendoNumericTextBox') || $(wrap?.querySelector('input[data-role="numerictextbox"]')).data('kendoNumericTextBox');
                if (widget) {
                    let num = parseFloat(String(val).replace(/,/g, ''));
                    if (!isNaN(num)) {
                        widget.value(num);
                        widget.trigger('change');
                        return true;
                    }
                }
                return false;
            }""", value)
            if is_kendo_num:
                return True
        except Exception:
            pass

        try:
            await locator.scroll_into_view_if_needed()
        except Exception:
            pass
        try:
            await locator.fill(value, timeout=LOCATOR_TIMEOUT_MS)
            return True
        except Exception:
            pass
        try:
            # Removed Control+A to prevent selecting the whole page
            await locator.click(timeout=LOCATOR_TIMEOUT_MS)
            await locator.evaluate("el => el.value = ''")
            await locator.type(value, delay=5)
            return True
        except Exception:
            pass
        try:
            await locator.evaluate("""(el, value) => {
                const tag = el.tagName.toLowerCase();
                if (tag === 'input') {
                    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set;
                    if (setter) setter.call(el, value);
                    else el.value = value;
                } else if (tag === 'textarea') {
                    const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')?.set;
                    if (setter) setter.call(el, value);
                    else el.value = value;
                } else if (el.isContentEditable) {
                    el.textContent = value;
                }
                el.dispatchEvent(new Event('input', {bubbles: true}));
                el.dispatchEvent(new Event('change', {bubbles: true}));
            }""", value)
            return True
        except Exception:
            return False

    async def read_control_value(self,locator: Locator) -> str:
        try:
            # Check if element or parent container has Kendo NumericTextBox or DatePicker attached
            kendo_val = await locator.evaluate("""el => {
                if (!window.jQuery) return null;
                let $ = window.jQuery;
                let num = $(el).data('kendoNumericTextBox') || $(el.closest('.k-numeric-wrap')?.parentElement?.querySelector('input[data-role=\"numerictextbox\"]')).data('kendoNumericTextBox');
                if (num && num.value() !== null && num.value() !== undefined) {
                    return String(num.value());
                }
                let dp = $(el).data('kendoDatePicker') || $(el.closest('.k-picker-wrap')?.parentElement?.querySelector('input[data-role=\"datepicker\"]')).data('kendoDatePicker');
                if (dp && dp.value()) {
                    return el.value || '';
                }
                return null;
            }""")
            if kendo_val is not None and kendo_val != "":
                return self.clean_text(kendo_val)
        except Exception:
            pass

        try:
            tag = await locator.evaluate("el => el.tagName.toLowerCase()")
            if tag == "select":
                try:
                    selected = await locator.evaluate("""el => {
                        const o = el.selectedOptions?.[0];
                        return o ? {text: o.textContent || '', value: o.value || ''} : {value: el.value || ''};
                    }""")
                    sel_val = self.clean_text(selected.get("text") or selected.get("value"))
                    if sel_val:
                        return sel_val
                except Exception:
                    pass
            if tag in {"input", "textarea"}:
                return self.clean_text(await locator.input_value())
            if tag == "select":
                try:
                    val = await locator.evaluate("el => el.value || ''")
                    return self.clean_text(val)
                except Exception:
                    pass
            attrs = await locator.evaluate("""el => ({
                value: el.getAttribute('value') || '',
                ariaValue: el.getAttribute('aria-valuetext') || '',
                ariaLabel: el.getAttribute('aria-label') || '',
                title: el.getAttribute('title') || '',
                text: el.innerText || '',
                content: el.textContent || '',
                selectedText: el.querySelector?.('[aria-selected="true"]')?.innerText || ''
            })""")
            return self.clean_text(
                attrs.get("value") or attrs.get("ariaValue") or attrs.get("selectedText")
                or attrs.get("text") or attrs.get("ariaLabel") or attrs.get("title")
                or attrs.get("content")
            )
        except Exception:
            return ""

    async def read_selected_control_text(self, locator: Locator) -> str:
        try:
            tag = await locator.evaluate("el => el.tagName.toLowerCase()")
            if tag == "select":
                data = await locator.evaluate("""el => {
                    const o = el.selectedOptions?.[0];
                    return o ? {text:o.textContent || '', value:o.value || ''} : {};
                }""")
                return self.clean_text(data.get("text") or data.get("value"))
            return await self.read_control_value(locator)
        except Exception:
            return ""

    async def read_input_constraints(self, locator: Locator) -> Dict[str, Any]:
        try:
            return await locator.evaluate("""el => {
                const v = el.validity || {};
                const dMax = parseInt(el.getAttribute('data-maxlength') || el.getAttribute('data-val-maxlength-max') || el.getAttribute('data-rule-maxlength') || '0', 10) || 0;
                const attrMax = el.maxLength > 0 ? el.maxLength : 0;
                const effMax = (attrMax > 0 && dMax > 0) ? Math.min(attrMax, dMax) : (attrMax || dMax);
                return {
                    value: el.value || '',
                    type: (el.type || '').toLowerCase(),
                    pattern: el.getAttribute('pattern') || '',
                    minLength: el.minLength || 0,
                    maxLength: effMax,
                    min: el.min || '',
                    max: el.max || '',
                    inputMode: (el.inputMode || el.getAttribute('inputmode') || '').toLowerCase(),
                    required: !!el.required,
                    valid: typeof el.checkValidity === 'function' ? el.checkValidity() : true,
                    patternMismatch: !!v.patternMismatch,
                    tooLong: !!v.tooLong || (effMax > 0 && (el.value || '').length > effMax),
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
            }""") or {}
        except Exception:
            return {}

    async def blur_and_check_invalid(self, locator: Locator) -> Dict[str, Any]:
        try:
            await locator.evaluate("""el => {
                el.dispatchEvent(new Event('input', {bubbles: true}));
                el.dispatchEvent(new Event('change', {bubbles: true}));
                el.blur();
                el.dispatchEvent(new Event('blur', {bubbles: true}));
            }""")
        except Exception:
            pass
        try:
            await locator.page.wait_for_timeout(80)
        except Exception:
            pass
        state = await self.read_input_constraints(locator)
        cls = self.norm(state.get("className"))
        invalid = bool(
            state.get("valid") is False
            or state.get("patternMismatch") or state.get("tooLong")
            or state.get("typeMismatch") or state.get("tooShort")
            or state.get("ariaInvalid") == "true"
            or "ng-invalid" in cls or "is-invalid" in cls
            or ("error" in cls and "input" in cls)
        )
        state["invalid"] = invalid
        return state

    def format_candidates_from_constraints(self, current: str, expected: str, constraints: Dict[str, Any]) -> List[str]:
        cur = self.clean_text(current) or self.clean_text(expected)
        exp = self.clean_text(expected)
        d_cur = self.digits(cur)
        d_exp = self.digits(exp)
        out: List[str] = []

        def add(v: Any) -> None:
            s = self.clean_text(v)
            if s and s not in out:
                out.append(s)

        add(cur)
        add(exp)
        # ONLY strip whitespace for short codes, phones, or IDs (<= 35 chars without newlines or periods)
        if len(cur) <= 35 and not any(ch in cur for ch in ["\n", "."]):
            add(re.sub(r"[\s\-()./]", "", cur))
        if d_cur:
            add(d_cur)
        if d_exp:
            add(d_exp)
        if cur.startswith("+") and len(d_cur) >= 8:
            rest = re.sub(r"^\+\d{1,3}", "", cur).strip()
            add(re.sub(r"[\s\-().]", "", rest))
            add(self.digits(rest))
            for n in (1, 2, 3):
                if len(d_cur) - n >= 7:
                    add(d_cur[n:])
            if len(d_cur) >= 10:
                add(d_cur[-10:])
                add(d_cur[-8:])
        elif len(d_cur) > 10:
            add(d_cur[-10:])

        itype = self.norm(constraints.get("type") or constraints.get("inputMode"))
        if itype in {"tel", "number", "numeric"} or constraints.get("inputMode") in {"tel", "numeric", "decimal"}:
            add(re.sub(r"[^\d.]", "", cur))
            if d_cur:
                add(d_cur)

        ml = int(constraints.get("maxLength") or 0)
        if ml > 0:
            for v in list(out):
                if len(v) > ml:
                    truncated = v[:ml]
                    last_space = truncated.rfind(" ")
                    if last_space > ml // 2:
                        truncated = truncated[:last_space].rstrip(",;.- \n")
                    add(truncated)
                    add(v[:ml])
                    dv = self.digits(v)
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

        msg = self.canonical_text(constraints.get("validationMessage") or "")
        itype2 = itype or self.norm(constraints.get("type"))
        if (itype2 == "url" or "url" in msg
                or "linkedin" in self.canonical_text(constraints.get("name") or "") + self.canonical_text(constraints.get("id") or "")
                or "http" in msg):
            raw = cur or exp
            if raw:
                add(self.ensure_https_url(raw))
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

    async def repair_invalid_text_input(self, locator: Locator, expected: str = "") -> Tuple[bool, str]:
        state = await self.blur_and_check_invalid(locator)
        current = self.clean_text(state.get("value"))
        if not state.get("invalid"):
            return True, current
        for cand in self.format_candidates_from_constraints(current, expected, state):
            if cand == current:
                continue
            try:
                await self.fill_text(locator, cand)
            except Exception:
                continue
            st = await self.blur_and_check_invalid(locator)
            live = self.clean_text(st.get("value"))
            if not st.get("invalid"):
                return True, live
            current = live
        return not bool(state.get("invalid")), current

    async def repair_invalid_inputs_on_page(self,page: Page) -> List[Dict[str, Any]]:
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
                    st = await self.blur_and_check_invalid(loc)
                    if not st.get("invalid"):
                        continue
                    ok, live = await self.repair_invalid_text_input(loc, st.get("value") or "")
                    repaired.append({
                        "ok": ok, "before": st.get("value"),
                        "after": live, "message": st.get("validationMessage"),
                    })
                except Exception:
                    continue
        return repaired

    def _resume_identity(self) -> Dict[str, str]:
        try:
            resume = self.load_json(RESUME_FILE)
            idx = self.build_resume_index(resume)
            parts = (idx.get("full_name") or "").split()
            first = idx.get("first_name") or (parts[0] if parts else "")
            last = idx.get("last_name") or (" ".join(parts[1:]) if len(parts) > 1 else "")
            city = idx.get("current_location") or idx.get("work_location") or idx.get("location") or idx.get("city") or ""
            return {
                "first_name": first, "last_name": last,
                "email": idx.get("email") or "",
                "phone": idx.get("national_phone") or idx.get("phone") or "",
                "city": city,
                "country": idx.get("country") or "",
                "address": city,
            }
        except Exception:
            return {
                "first_name": "", "last_name": "",
                "email": "", "phone": "",
                "city": "", "country": "", "address": "",
            }

    def required_placeholder_for_label(self, label: str, options: Optional[List[str]] = None) -> str:
        q = self.canonical_text(label)
        ident = self._resume_identity()
        opts = [self.clean_text(o) for o in (options or []) if self.clean_text(o)]
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
        if q.strip() in {"country"} or (q.startswith("country") and "phone" not in q and "code" not in q):
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
                if not self.is_placeholder_option_label(o, q):
                    return o
        if any(w in q for w in ("year", "experience")) and re.search(r"\b(year|exp)", q):
            return "1"
        return "NA"

    async def fill_required_empty_controls(self, page: Page) -> List[Dict[str, Any]]:
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
                answer = self.required_placeholder_for_label(label, row.get("options") or [])
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
                        await self.fill_text(loc, answer)
                        await self.repair_invalid_text_input(loc, answer)
                    filled.append({"label": label, "answer": answer})
                except Exception:
                    continue
        return filled

    async def count_blocking_required_fields(self, page: Page) -> List[str]:
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

    async def verify_text(self, locator: Locator, expected: str, timeout: int = VERIFY_TIMEOUT_MS) -> bool:
        exp = self.clean_text(expected)
        if not exp:
            return False
        deadline = time.monotonic() + timeout / 1000
        while time.monotonic() < deadline:
            actual = self.clean_text(await self.read_control_value(locator))
            if actual and self.same_value(exp, actual):
                if len(actual) < len(exp) and exp.startswith(actual) and len(actual) < len(exp):
                    if not re.search(r"[/\-\.\s]", exp):
                        await asyncio.sleep(0.10)
                        continue
                return True
            await asyncio.sleep(0.10)
        return False

    async def get_option_candidates(self, locator: Locator) -> List[Dict[str, str]]:
        try:
            tag = await locator.evaluate("el => el.tagName.toLowerCase()")
            if tag == "select":
                return await locator.evaluate("""el => Array.from(el.options).map(o => ({
                    label: (o.textContent || '').trim(),
                    text: (o.textContent || '').trim(),
                    value: o.value
                }))""")
            return await locator.evaluate("""el => {
                const root = el.closest('[role="combobox"], [role="listbox"], div, section, form') || document;
                return Array.from(root.querySelectorAll('option, [role="option"]')).map(o => ({
                    label: (o.innerText || o.textContent || '').trim(),
                    text: (o.innerText || o.textContent || '').trim(),
                    value: o.getAttribute('value') || ''
                }));
            }""")
        except Exception:
            return []

    def option_synonym_score(self, target: Any, label: Any) -> int:
        a, b = self.canonical_text(target), self.canonical_text(label)
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
        if a in {"0", "immediate", "immediately", "ready", "urgent"} and any(x in b for x in ["immediately", "immediate", "ready", "0 days"]):
            return 95
        if any(x in a for x in ["inr", "indian rupee", "rupee"]) and any(x in b for x in ["inr", "rupee", "indian rupee"]):
            return 95
        if any(x in a for x in ["usd", "us dollar", "dollar"]) and any(x in b for x in ["usd", "dollar", "us dollar"]):
            return 95
        if a in {"annually", "annual", "yearly", "year", "per year", "per annum"} and any(x in b for x in ["annually", "annual", "yearly", "year"]):
            return 95
        if a in {"monthly", "month", "per month"} and any(x in b for x in ["monthly", "month"]):
            return 95
        if any(x in a for x in ["gurgaon", "gurugram", "noida", "ghaziabad", "faridabad"]) and any(y in b for y in ["delhi", "delhi ncr", "new delhi", "ncr"]):
            return 92
        if any(x in a for x in ["bangalore", "bengaluru"]) and any(y in b for y in ["bangalore", "bengaluru"]):
            return 95
        if any(x in a for x in ["mumbai", "bombay"]) and any(y in b for y in ["mumbai", "bombay"]):
            return 95
        if any(x in a for x in ["calcutta", "kolkata"]) and any(y in b for y in ["calcutta", "kolkata"]):
            return 95
        if any(x in a for x in ["madras", "chennai"]) and any(y in b for y in ["madras", "chennai"]):
            return 95
        if a.isdigit() and ("year" in b or "yr" in b):
            m = re.search(r"(\d+)", b)
            if m and m.group(1) == a:
                return 95
        if any(k in a for k in ["developer", "software", "backend", "frontend", "sde", "engineer", "data scientist", "ai/ml", "computer science"]):
            if re.search(r"\bsde\s*1\b|\bsde-1\b|\bsoftware engineer\s*1\b|\bsoftware development engineer\s*1\b|\bassociate\b|\bjr\b|\bjunior\b", b):
                return 88
            if re.search(r"\bsde\b|\bsoftware development engineer\b", b) and not re.search(r"[2345]", b):
                return 82
        return 0

    def is_fallback_other_label(self, label: Any) -> bool:
        t = self.canonical_text(label)
        return bool(re.match(
            r"^(other|others|not listed|not listed above|none of the above|i don t see|school not listed|not in the list|not in list)\b",
            t,
        ))

    def pick_fallback_other(self, options: List[Any]) -> Optional[Any]:
        for opt in options or []:
            label = self.option_display(opt)
            if self.is_fallback_other_label(label):
                return opt
        return None

    def choose_best_option(self, options: List[Dict[str, Any]], answer: Any, option_label: Any = None, option_value: Any = None) -> Optional[Dict[str, Any]]:
        targets = [x for x in [option_label, option_value, answer] if self.clean_text(x)]
        best = None
        best_score = 0
        for opt in options:
            label = self.clean_text(opt.get("label") or opt.get("text") or opt.get("name"))
            value = self.clean_text(opt.get("value"))
            for target in targets:
                tl, ll = self.canonical_text(target), self.canonical_text(label)
                if ll and tl and ll in tl.split() and len(ll.split()) < len(tl.split()):
                    continue
                score = max(
                    self.fuzzy_score(target, label),
                    self.fuzzy_score(target, value) if value else 0,
                    self.option_synonym_score(target, label),
                    self._option_match_score(target, label),
                )
                if score > best_score:
                    best_score = score
                    best = opt
        return best if best_score >= 65 else None

    def snap_answer_to_options(self, options: List[Any], answer: Any, option_label: Any = None, option_value: Any = None, blob: Any = None) -> Optional[Dict[str, Any]]:
        opts: List[Dict[str, Any]] = []
        for opt in options or []:
            if isinstance(opt, dict):
                opts.append(opt)
            else:
                text = self.clean_text(opt)
                if text:
                    opts.append({"label": text, "text": text})
        if not opts:
            return None
        best = self.choose_best_option(opts, answer, option_label, option_value)
        if best:
            return best
        blob_c = self.canonical_text(" ".join(self.clean_text(x) or "" for x in [blob, answer, option_label, option_value]))
        ranked = []
        for opt in opts:
            lab = self.option_display(opt)
            lc = self.canonical_text(lab)
            if not lc or self.looks_like_group_heading(lab) or lc in {"-", "select option"}:
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

    async def _dismiss_open_menus(self, frame: Frame) -> None:
        try:
            await frame.page.keyboard.press("Escape")
            await asyncio.sleep(0.05)
        except Exception:
            pass

    async def reveal_rest_of_form(self, page: Page) -> None:
        try:
            await page.keyboard.press("Escape")
            await page.keyboard.press("Escape")
        except Exception:
            pass
        try:
            await page.evaluate("""() => {
                document.querySelectorAll('.select__menu, .iti__country-list, [role="listbox"]').forEach(el => { el.style.display = 'none'; });
                document.body.style.overflow = '';
                document.documentElement.style.overflow = '';
            }""")
        except Exception:
            pass
        try:
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await page.wait_for_timeout(200)
            loc = page.locator('button[type="submit"], input[type="submit"], button:has-text("Submit")').last
            if await loc.count():
                await loc.scroll_into_view_if_needed()
        except Exception:
            pass

    async def _react_select_control(self, locator: Locator) -> Optional[Locator]:
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
            control = locator.locator("xpath=ancestor::*[contains(@class,'select__control')][1]")
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

    async def _overlay_search_input(self, frame: Frame) -> Optional[Locator]:
        for sel in (
            '.cdk-overlay-pane input:visible',
            '[role="listbox"] input:visible',
            '.select__menu input:visible',
            '.select2-dropdown input:visible',
            '.select2-search__field:visible',
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

    async def _react_select_input(self, locator: Locator, frame: Frame) -> Locator:
        overlay = await self._overlay_search_input(frame)
        if overlay:
            return overlay
        try:
            nested = locator.locator("input.select__input, input[role='combobox'], input[aria-autocomplete='list']")
            candidate = await self.visible_locator(nested)
            if candidate:
                return candidate
        except Exception:
            pass
        try:
            control = await self._react_select_control(locator)
            if control:
                nested = control.locator("input.select__input, input[role='combobox'], input")
                candidate = await self.visible_locator(nested)
                if candidate:
                    return candidate
        except Exception:
            pass
        return locator

    async def read_select_display(self, locator: Locator) -> str:
        try:
            tag = self.norm(await locator.evaluate("el => el.tagName.toLowerCase()"))
        except Exception:
            tag = ""
        if tag == "select":
            return await self.read_selected_control_text(locator)
        if tag == "input":
            try:
                val = self.clean_text(await locator.input_value())
                if val:
                    return val
            except Exception:
                pass
            return ""
        try:
            data = await locator.evaluate("""el => {
                const host = el.closest('cu-select') || el.closest('lyte-dropdown') || el.closest('.select__control') || el.closest('[class*="select"]') || el;
                const wrap = el.closest('.select__control') || el.closest('[class*="css-"]') || host;
                const root = el.closest('.select2-container, [id*="-li"], .form-group, div') || (wrap && wrap.parentElement) || wrap;
                const single = (wrap && wrap.querySelector('.select__single-value, [class*="singleValue"], [class*="single-value"], .select2-selection__rendered, [class*="selection__rendered"]'))
                    || (root && root.querySelector('.select__single-value, [class*="singleValue"], .select2-selection__rendered, [class*="selection__rendered"]'));
                const multi = wrap
                    ? Array.from(wrap.querySelectorAll('.select__multi-value__label, [class*="multiValue"]')).map(n => n.textContent || '').join(', ')
                    : '';
                let sText = single ? (single.textContent || single.title || '').replace(/\\s+/g, ' ').trim() : '';
                if (/^(-select-|-none-|select option|select\\.{0,3}|choose|type to search)$/i.test(sText)) sText = '';
                let raw = ((host && host.innerText) || el.innerText || el.textContent || '').replace(/\\s+/g, ' ').trim();
                raw = raw.replace(/select option\\.{0,3}$/i, '').trim();
                if (/^(-select-|-none-|select option|select\\.{0,3}|choose|type to search)$/i.test(raw)) raw = '';
                if (!raw && root) {
                    const under = root.querySelector('select');
                    if (under && under.selectedOptions && under.selectedOptions.length) {
                        const ot = (under.selectedOptions[0].textContent || under.selectedOptions[0].value || '').trim();
                        if (ot && !/^(-select-|-none-|select option|select\\.{0,3}|choose)$/i.test(ot)) raw = ot;
                    }
                }
                return { single: sText, multi, host: raw };
            }""")
            for key in ("single", "multi", "host"):
                text = self.clean_text(data.get(key))
                if text and not re.match(r"^[-–—\\s]*(select|none|choose|type to search)[-–—\\s]*$", text, re.I) and "select option" not in self.norm(text):
                    return text
            return ""
        except Exception:
            return ""

    def _option_match_score(self, target: str, label: str) -> int:
        a = self.canonical_text(target)
        b = self.canonical_text(label)
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
            if len(b.split()) < len(a.split()):
                return 60
            return 90
        if len(a) >= 8 and a in b:
            return 92
        if len(b) >= 8 and b in a:
            return 86
        return self.fuzzy_score(a, b)

    def _expand_select_targets(self, targets: List[str]) -> List[str]:
        extra: List[str] = []
        for t in targets:
            extra.append(t)
            n = self.canonical_text(t)
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
            if t in MONTH_EXPANSIONS:
                extra.append(MONTH_EXPANSIONS[t])
                extra.append(t.zfill(2))
            elif t.isdigit() and str(int(t)) in MONTH_EXPANSIONS:
                extra.append(MONTH_EXPANSIONS[str(int(t))])
                extra.append(str(int(t)))
            elif n in INV_MONTH_EXP:
                m_num = INV_MONTH_EXP[n]
                extra.append(m_num)
                extra.append(m_num.zfill(2))
                extra.append(MONTH_EXPANSIONS.get(m_num, ""))
            if t.isdigit() and 1 <= int(t) <= 31:
                extra.append(str(int(t)))
                extra.append(str(int(t)).zfill(2))
        return [x for x in dict.fromkeys(self.clean_text(x) for x in extra) if x]

    def _search_variants(self,target: str) -> List[str]:
        t = self.clean_text(target)
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

    async def _activate_option(self, option: Locator) -> bool:
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
            await option.evaluate("""el => {
                const fire = type => el.dispatchEvent(new MouseEvent(type, { bubbles: true, cancelable: true, view: window, buttons: 1 }));
                fire('pointerdown'); fire('mousedown'); fire('pointerup'); fire('mouseup'); fire('click');
            }""")
            return True
        except Exception:
            try:
                await option.click(force=True, timeout=900)
                return True
            except Exception:
                return False

    async def _menu_is_loading(self,frame: Frame) -> bool:
        try:
            text = self.canonical_text(await frame.evaluate("""() => {
                const vis = el => {
                    if (!el) return false;
                    const r = el.getBoundingClientRect();
                    const s = getComputedStyle(el);
                    return r.height > 8 && s.display !== 'none' && s.visibility !== 'hidden';
                };
                const menu = [...document.querySelectorAll('.cdk-overlay-pane, .select__menu, [role="listbox"], [role="tree"], .select2-dropdown, .ng-dropdown-panel, cu-dropdown, .cu-dropdown')].filter(vis).pop();
                return menu ? (menu.innerText || '') : '';
            }"""))
            return any(x in text for x in ["loading", "searching"]) and "type to search" not in text
        except Exception:
            return False

    async def _wait_live_options(self, frame: Frame, timeout_ms: int = 1600) -> List[Dict[str, Any]]:
        deadline = time.monotonic() + timeout_ms / 1000
        last: List[Dict[str, Any]] = []
        while time.monotonic() < deadline:
            if await self._menu_is_loading(frame):
                await asyncio.sleep(0.06)
                continue
            last = []
            for opt in await self._live_option_locators(frame, include_groups=True):
                label = self.canonical_text(opt.get("label") or opt.get("text"))
                if not label or label in {"no options", "no results", "loading", "searching", "type to search"}:
                    continue
                if "no options matched" in label or "type to search" in label:
                    continue
                last.append(opt)
            if last:
                return last
            await asyncio.sleep(0.05)
        return last

    async def _pick_one_option(self,options: List[Dict[str, Any]], targets: List[str], allow_groups: bool = False) -> Optional[Dict[str, Any]]:
        best = None
        best_score = 0
        for opt in options:
            if not allow_groups and (opt.get("is_group") or self.looks_like_group_heading(opt.get("label") or opt.get("text"))):
                continue
            fields = [opt.get("label"), opt.get("text"), opt.get("value"), opt.get("aria"), opt.get("country"), opt.get("dial")]
            score = max((self._option_match_score(t, f) for t in targets for f in fields if self.clean_text(f)), default=0)
            score = max(score, max((self.option_synonym_score(t, f) for t in targets for f in fields if self.clean_text(f)), default=0))
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

    async def _open_custom_select(self,locator: Locator, frame: Frame) -> Locator:
        try:
            dummy = locator.locator(".lyteDummyEventContainer, lyte-drop-button").first
            if await dummy.count():
                await dummy.click(timeout=1200)
        except Exception:
            pass
        control = await self._react_select_control(locator) or locator
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
            if await self._overlay_search_input(frame):
                break
            live = await self._live_option_locators(frame)
            if live:
                break
            await asyncio.sleep(0.08)
        search = await self._overlay_search_input(frame)
        if search:
            try:
                await search.click(timeout=800)
            except Exception:
                pass
            return search
        search = await self._react_select_input(locator, frame)
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
        try:
            rows = await frame.evaluate(COLLECT_MENU_OPTIONS_JS,  {"includeGroups": include_groups})
            return [x for x in (rows or []) if isinstance(x, dict)]
        except Exception:
            return []

    async def _click_menu_label(self, frame: Frame, target: str) -> bool:
        target = self.clean_text(target)
        if not target:
            return False
        try:
            box = await frame.evaluate("""(wanted) => {
                const norm = s => (s || '').replace(/\\s+/g, ' ').trim().toLowerCase();
                const want = norm(wanted);
                const vis = el => {
                    if (!el) return false;
                    const r = el.getBoundingClientRect();
                    const s = getComputedStyle(el);
                    return r.height > 4 && s.display !== 'none' && s.visibility !== 'hidden';
                };
                const skipHost = el => !el || !!el.closest('.iti, .react-international-phone, cu-rich-editor-toolbar');
                let menu = [...document.querySelectorAll(
                    '.cdk-overlay-pane, [role="listbox"], [role="menu"], [role="tree"], .select__menu, .dropdown-menu, .select2-dropdown, .iti__country-list, lyte-drop-box:not(.lyteDropdownHidden), lyte-drop-body, .cxDropbox, .ng-dropdown-panel, .ant-select-dropdown'
                )].filter(el => vis(el) && !skipHost(el)).pop() || null;
                if (!menu) {
                    const searches = [...document.querySelectorAll(
                        '.cdk-overlay-container input, .select__menu input, [role="listbox"] input, lyte-drop-box input, [class*="dropdown"] input, .select2-dropdown input'
                    )].filter(vis);
                    const search = searches.length ? searches[searches.length - 1] : null;
                    if (search && !skipHost(search)) {
                        menu = search.closest('.cdk-overlay-pane, [role="listbox"], [role="tree"], .select2-dropdown, .select__menu, lyte-drop-box, [class*="dropdown"]') || search.parentElement;
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
                    if (area < bestArea) { best = el; bestArea = area; }
                }
                if (!best) return null;
                best.scrollIntoView({block: 'nearest'});
                const r = best.getBoundingClientRect();
                return {x: r.x + Math.max(4, r.width / 2), y: r.y + Math.max(4, r.height / 2)};
            }""", target)
            if not box:
                return False
            try:
                await frame.page.mouse.move(box["x"], box["y"])
                await frame.page.mouse.down()
                await frame.page.mouse.up()
                return True
            except Exception:
                try:
                    return bool(await frame.evaluate("""(wanted) => {
                        const norm = s => (s || '').replace(/\\s+/g, ' ').trim().toLowerCase();
                        const want = norm(wanted);
                        const vis = el => {
                            if (!el) return false;
                            const r = el.getBoundingClientRect();
                            const s = getComputedStyle(el);
                            return r.height > 4 && s.display !== 'none' && s.visibility !== 'hidden';
                        };
                        const menu = [...document.querySelectorAll(
                            '.cdk-overlay-pane, .select__menu, [role="listbox"], [role="tree"], .select2-dropdown, cu-dropdown, .cu-dropdown, lyte-drop-box:not(.lyteDropdownHidden), lyte-drop-body, .cxDropbox'
                        )].filter(vis).pop();
                        const nodes = menu
                            ? [...menu.querySelectorAll('lyte-drop-item, [role="option"], [role="treeitem"], .select2-results__option, li, button, [class*="option"], a')]
                            : [...document.querySelectorAll('lyte-drop-item, [role="treeitem"], .select2-results__option')];
                        const textOf = el => (el.innerText || el.textContent || '').replace(/\\s+/g, ' ').trim();
                        const hit = nodes.find(el => vis(el) && (norm(textOf(el)) === want || norm(textOf(el)).includes(want) || want.includes(norm(textOf(el)))));
                        if (!hit) return false;
                        hit.click();
                        return true;
                    }""", target))
                except Exception:
                    return False
        except Exception:
            return False

    async def _click_exact_live_option(self, frame: Frame, target: str) -> Optional[Locator]:
        
        target = self.clean_text(target)
        if not target:
            return None
        scopes = [
            frame.locator(".cdk-overlay-pane:visible"),
            frame.locator("[role='listbox']:visible"),
            frame.locator(".select__menu:visible"),
            frame.locator("[role='menu']:visible"),
            frame.locator("[role='tree']:visible"),
            frame.locator(".select2-dropdown:visible"),
            frame.locator("lyte-drop-box:visible"),
            frame,
        ]
        for scope in scopes:
            for role in ("option", "menuitem", "treeitem"):
                try:
                    loc = scope.get_by_role(role, name=re.compile(rf"^{re.escape(target)}$", re.I))
                    candidate = await self.visible_locator(loc)
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
                        text = self.clean_text(await item.inner_text())
                        if self.canonical_text(text) == self.canonical_text(target):
                            return item
                except Exception:
                    pass
        for selector in (
            ".select__option:visible",
            ".iti__country:visible",
            "lyte-drop-item:visible",
            ".select2-results__option:visible",
            "[role='treeitem']:visible",
        ):
            try:
                loc = frame.locator(selector)
                count = min(await loc.count(), 80)
                for i in range(count):
                    item = loc.nth(i)
                    try:
                        if not await item.is_visible():
                            continue
                        text = self.clean_text(await item.inner_text())
                        aria = self.clean_text(await item.get_attribute("aria-label"))
                        if self.canonical_text(target) == self.canonical_text(text) or self.canonical_text(target) == self.canonical_text(aria):
                            return item
                    except Exception:
                        continue
            except Exception:
                pass
        return None

    async def fill_other_specify_near(self, locator: Locator, original: str) -> bool:
        original = self.clean_text(original)
        if not original or self.is_fallback_other_label(original):
            return False
        await asyncio.sleep(0.2)
        try:
            return bool(await locator.evaluate("""(el, value) => {
                const vis = (i) => {
                    if (!i || i.disabled) return false;
                    if (i.closest('.select__control, .select__menu, .iti')) return false;
                    const r = i.getBoundingClientRect();
                    const s = getComputedStyle(i);
                    return r.height > 8 && r.width > 40 && s.display !== 'none' && s.visibility !== 'hidden';
                };
                const section = el.closest('.education, .field, fieldset, [class*="education"]') || el.closest('form') || el.parentElement?.parentElement || document.body;
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
            }""", original))
        except Exception:
            return False

    async def select_custom_dropdown(
        self,
        locator: Locator,
        answer: Any,
        option_label: Any = None,
        option_value: Any = None,
        stored_options: Optional[List[Any]] = None,
    ) -> Tuple[bool, str]:
        best = self.choose_best_option(stored_options or [], answer, option_label, option_value)
        target_text = self.clean_text(best.get("label") or best.get("text") or best.get("value") or answer) if best else self.clean_text(answer)
        target_val = self.clean_text(best.get("value") if best else target_text)

        try:
            await locator.scroll_into_view_if_needed(timeout=LOCATOR_TIMEOUT_MS)
            await locator.click(timeout=LOCATOR_TIMEOUT_MS)
            await asyncio.sleep(0.3)
        except Exception:
            pass

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
            cur_val = await self.read_control_value(locator)
            if self.display_matches(target_text, cur_val) or self.same_value(target_val, cur_val):
                await self.fire_react_events(locator)
                return True, "custom_dropdown_li_click"

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
            cur_val = await self.read_control_value(locator)
            if cur_val:
                return True, "custom_dropdown_dom_fallback"
        except Exception:
            pass

        return False, "custom_dropdown_failed"

    async def select_native_or_custom(
        self,
        locator: Locator,
        answer: Any,
        option_label: Any = None,
        option_value: Any = None,
        frame: Optional[Frame] = None,
        stored_options: Optional[List[Any]] = None,
        specify_text: Any = None,
    ) -> Tuple[bool, str]:
        answer = self.clean_text(answer)
        original_resume = self.clean_text(specify_text)
        if not original_resume and not self.is_fallback_other_label(answer):
            original_resume = answer
        targets = self._expand_select_targets(
            [self.clean_text(x) for x in (option_label, option_value, answer) if self.clean_text(x)]
        )
        resolved_stored = self.choose_best_option(stored_options or [], answer, option_label, option_value)
        if resolved_stored:
            label = self.clean_text(resolved_stored.get("label") or resolved_stored.get("text"))
            if label:
                targets.insert(0, label)

        try:
            tag = self.norm(await locator.evaluate("el => el.tagName.toLowerCase()"))
        except Exception:
            tag = ""

        if tag == "select":
            options = await self.get_option_candidates(locator)
            best = self.choose_best_option(options, answer, option_label, option_value)
            if best:
                value = self.clean_text(best.get("value"))
                label = self.clean_text(best.get("label"))
                try:
                    if value:
                        await locator.select_option(value=value, timeout=LOCATOR_TIMEOUT_MS)
                    else:
                        await locator.select_option(label=label, timeout=LOCATOR_TIMEOUT_MS)
                    await self.fire_react_events(locator)
                    return True, "native_select_exact_match"
                except Exception:
                    pass
                try:
                    val = value or label
                    await locator.evaluate("""(el, v) => {
                        for (let opt of el.options) {
                            if (opt.value === v || opt.text === v || (opt.text && opt.text.trim().toLowerCase() === v.trim().toLowerCase())) {
                                el.value = opt.value;
                                opt.selected = true;
                                el.dispatchEvent(new Event('input', {bubbles: true}));
                                el.dispatchEvent(new Event('change', {bubbles: true}));
                                break;
                            }
                        }
                        if (window.jQuery || window.$) {
                            const jq = window.jQuery || window.$;
                            try { jq(el).trigger('change'); } catch(e) {}
                            try { jq(el).trigger('change.select2'); } catch(e) {}
                        }
                        if (el.id) {
                            const cleanId = el.id.replace(/\\./g, '');
                            const s2 = document.getElementById('select2-' + cleanId + '-container') || document.querySelector('[aria-labelledby*=\"' + cleanId + '\"]');
                            if (s2) {
                                const o = el.selectedOptions?.[0];
                                if (o) { s2.textContent = o.text || o.value; s2.title = o.text || o.value; }
                            }
                        }
                        return true;
                    }""", val)
                    await self.fire_react_events(locator)
                    return True, "native_select_dom_fallback"
                except Exception:
                    pass
            return False, "native_select_no_match"

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
                ok, method = await self.select_custom_dropdown(locator, answer, option_label, option_value, stored_options)
                if ok:
                    return ok, method

        if frame is None:
            try:
                frame = await locator.owner_frame()
            except Exception:
                frame = None
        if frame is None:
            return False, "no_frame"

        await self._dismiss_open_menus(frame)
        already = await self.read_select_display(locator)
        if already and any(self.fuzzy_score(t, already) >= 70 or self._option_match_score(t, already) >= 70 for t in targets):
            return True, "already_selected"

        search = await self._open_custom_select(locator, frame)

        async def menu_open() -> bool:
            try:
                return bool(await frame.evaluate("""() => {
                    const vis = el => {
                        if (!el) return false;
                        const r = el.getBoundingClientRect();
                        const s = getComputedStyle(el);
                        return r.height > 12 && r.width > 40 && s.display !== 'none' && s.visibility !== 'hidden';
                    };
                    return [...document.querySelectorAll(
                        '.cdk-overlay-pane, .select__menu, [role="listbox"], [role="menu"], [role="tree"], .select2-dropdown, .ng-dropdown-panel, cu-dropdown, .cu-dropdown'
                    )].some(vis) || [...document.querySelectorAll('input')].some(el =>
                        vis(el) && /search/i.test((el.getAttribute('placeholder') || '') + ' ' + (el.getAttribute('aria-label') || ''))
                    );
                }"""))
            except Exception:
                return False

        async def committed(method: str, accepted: Any = None) -> Optional[Tuple[bool, str]]:
            await asyncio.sleep(0.35)
            actual = await self.read_select_display(locator)
            if not actual:
                actual = await self.read_select_display(search)
            check = [accepted, *targets]
            if actual and any(
                self.fuzzy_score(t, actual) >= 65 or self._option_match_score(t, actual) >= 65 or self.same_value(t, actual)
                for t in check if self.clean_text(t)
            ):
                return True, method
            if accepted and not await menu_open():
                shown = await self.read_select_display(locator)
                if shown and not self.looks_like_group_heading(shown) and "select option" not in self.norm(shown):
                    return True, method
                if not shown:
                    return True, method
            return None

        async def activate(item: Optional[Dict[str, Any]]) -> bool:
            if not item:
                return False
            label = self.clean_text(item.get("label") or item.get("text"))
            exact_loc = await self._click_exact_live_option(frame, label)
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
            if await self._click_menu_label(frame, label):
                return True
            loc = item.get("locator")
            if loc:
                return await self._activate_option(loc)
            return False

        async def choose_from_open_menu(method: str) -> Optional[Tuple[bool, str]]:
            items = await self._wait_live_options(frame, timeout_ms=2200)
            leaves = [x for x in items if not x.get("is_group")]
            groups = [x for x in items if x.get("is_group")]
            picked = self.snap_answer_to_options(leaves or items, answer, option_label, option_value, blob=original_resume)
            if not picked:
                picked = await self._pick_one_option(leaves, targets) if leaves else None
            if not picked and groups:
                group = await self._pick_one_option(groups, targets, allow_groups=True)
                if group and await activate(group):
                    await asyncio.sleep(0.25)
                    items = await self._wait_live_options(frame, timeout_ms=1600)
                    leaves = [x for x in items if not x.get("is_group")]
                    picked = await self._pick_one_option(leaves, targets) if leaves else None
            if not picked:
                other = self.pick_fallback_other(leaves or items)
                if other:
                    picked = other if isinstance(other, dict) else {"label": self.option_display(other)}
                    method = "custom_other_fallback"
            if not picked or not await activate(picked):
                return None
            return await committed(method, picked.get("label") or picked.get("text"))

        async def accept_hit(hit: Tuple[bool, str]) -> Optional[Tuple[bool, str]]:
            ok, method = hit
            display = await self.read_select_display(locator)
            if display and self.looks_like_group_heading(display):
                return None
            if self.is_fallback_other_label(display) or "other" in method:
                if await self.fill_other_specify_near(locator, original_resume):
                    return True, method + "+resume_specify"
                return True, method
            return hit

        live_items = await self._wait_live_options(frame, timeout_ms=2500)
        snapped = self.snap_answer_to_options(live_items or stored_options or [], answer, option_label, option_value, blob=original_resume)
        if snapped:
            snapped_label = self.option_display(snapped)
            if snapped_label:
                targets = [snapped_label] + [t for t in targets if self.canonical_text(t) != self.canonical_text(snapped_label)]
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

        queries: List[str] = []
        if snapped:
            label = self.option_display(snapped)
            if label:
                queries = [label]

        for query in queries:
            overlay = await self._overlay_search_input(frame)
            if overlay:
                search = overlay
            else:
                try:
                    search = await self._open_custom_select(locator, frame)
                except Exception:
                    pass
                overlay = await self._overlay_search_input(frame)
                if overlay:
                    search = overlay
            try:
                tag_name = self.norm(await search.evaluate("el => el.tagName.toLowerCase()"))
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
                if await self._menu_is_loading(frame):
                    await asyncio.sleep(0.08)
                else:
                    break
            hit = await choose_from_open_menu("custom_filtered_click")
            if hit:
                accepted = await accept_hit(hit)
                if accepted:
                    return accepted

        schoolish = bool(re.search(r"university|college|institute|\bschool\b", self.canonical_text(original_resume)))
        if schoolish:
            try:
                await search.fill("")
                await search.press("Control+A")
                await search.press("Backspace")
                await search.type("Other", delay=0)
            except Exception:
                try:
                    search = await self._open_custom_select(locator, frame)
                except Exception:
                    pass
            hit = await choose_from_open_menu("custom_other_fallback")
            if hit:
                accepted = await accept_hit(hit)
                if accepted:
                    return accepted

        await self._dismiss_open_menus(frame)
        return False, "custom_option_not_found"

    async def click_choice_locator(self, frame, selector: str) -> bool:
        if not selector:
            return False
        target = frame.locator(selector)
        target = await self.visible_locator(target) or target.first
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
            await click_tgt.evaluate("""el => {
                el.dispatchEvent(new MouseEvent('mousedown', {bubbles: true, cancelable: true}));
                el.dispatchEvent(new MouseEvent('mouseup', {bubbles: true, cancelable: true}));
                el.click();
            }""")
            return True
        except Exception:
            return False

    async def is_choice_selected(self , page: Page, e: Dict[str, Any], wanted: Any) -> bool:
        want = self.canonical_text(wanted)
        if not want:
            return False
        _, frame, _ = await self.locator_from_element(page, e)
        if not frame:
            return False
        name = self.clean_text(e.get("name"))
        question = self.clean_text(e.get("question") or e.get("label"))
        try:
            return bool(await frame.evaluate("""({name, want, question}) => {
                const nrm = t => (t || '').replace(/\\s+/g, ' ').trim().toLowerCase();
                const wantN = nrm(want);
                const qN = nrm(question).slice(0, 48);
                const radios = [...document.querySelectorAll('input[type="radio"]')];
                let group = [];
                if (name) group = radios.filter(r => r.name === name);
                if (!group.length && qN) {
                    const block = [...document.querySelectorAll('fieldset, .form-group, div[class*="field"], div[class*="radio"]')]
                        .find(b => nrm(b.innerText || '').includes(qN));
                    if (block) group = [...block.querySelectorAll('input[type="radio"]')];
                }
                for (const r of group) {
                    if (!r.checked) continue;
                    const lab = r.id ? document.querySelector(`label[for="${CSS.escape(r.id)}"]`) : r.closest('label');
                    const txt = nrm((r.value || '') + ' ' + (lab ? (lab.innerText || lab.textContent || '') : ''));
                    if (txt === wantN || txt.includes(wantN) || wantN.includes(txt)) return true;
                    if ((wantN === 'yes' || wantN === 'true') && (r.value === 'true' || txt.includes('yes'))) return true;
                    if ((wantN === 'no' || wantN === 'false') && (r.value === 'false' || txt.includes('no'))) return true;
                }
                const q = nrm(question);
                if (q) {
                    const blocks = [...document.querySelectorAll('div, fieldset, section, li, form')];
                    for (const b of blocks) {
                        if (!nrm(b.innerText || '').includes(qN)) continue;
                        const nodes = b.querySelectorAll('[role="radio"], [role="option"]');
                        for (const n of nodes) {
                            const chk = (n.getAttribute('aria-checked') || n.getAttribute('aria-selected') || '').toLowerCase();
                            if (chk !== 'true') continue;
                            const t = nrm(n.getAttribute('aria-label') || n.innerText || '');
                            if (t === wantN || t.includes(wantN) || wantN.includes(t)) return true;
                        }
                    }
                }
                return false;
            }""", {"name": name, "want": want, "question": question}))
        except Exception:
            return False

    async def click_radio(
        self,
        page: Page,
        e: Dict[str, Any],
        answer: Any,
        option_label: Any = None,
        option_value: Any = None,
    ) -> Tuple[bool, str]:
        wanted = self.norm(option_label or option_value or answer)
        loc, frame, _ = await self.locator_from_element(page, e)
        if not frame:
            return False, "radio_frame_not_found"

        for opt in e.get("options", []) or []:
            if not isinstance(opt, dict):
                continue
            hay = self.norm(" ".join(str(opt.get(k) or "") for k in ("label", "text", "value")))
            if wanted and self.fuzzy_score(wanted, hay) < 65 and wanted not in hay and hay not in wanted:
                continue
            selector = self.clean_text(opt.get("selector"))
            if selector:
                try:
                    if await self.click_choice_locator(frame, selector):
                        return True, "radio_option_selector"
                except Exception:
                    pass

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
                attrs = await r.evaluate("""el => {
                    const id = el.id || '';
                    const label = id ? document.querySelector(`label[for="${CSS.escape(id)}"]`) : el.closest('label');
                    return {
                        value: el.value || '', name: el.name || '', id,
                        label: label ? (label.innerText || label.textContent || '') : '',
                        parent: el.parentElement ? (el.parentElement.innerText || '') : ''
                    };
                }""")
                expected_name = self.clean_text(e.get("name") or "")
                if expected_name and attrs.get("name") and attrs.get("name") != expected_name:
                    continue
                hay = self.norm(" ".join(str(v) for v in attrs.values() if v))
                score = 0
                if wanted and wanted == self.norm(attrs.get("value")):
                    score = 120
                elif wanted and wanted == self.norm(attrs.get("label")):
                    score = 115
                elif wanted and wanted in hay:
                    score = 90
                elif self.norm(attrs.get("label")) in wanted and self.norm(attrs.get("label")):
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
                hay = self.norm(" ".join(str(v) for v in attrs.values()))
                if wanted == self.norm(attrs.get("value")) or wanted == self.norm(attrs.get("aria")) or wanted in hay:
                    await c.click(force=True, timeout=LOCATOR_TIMEOUT_MS)
                    return True, "aria_radio_click"
            except Exception:
                continue

        wanted_label = self.clean_text(option_label or answer)
        if wanted_label and wanted_label.strip() in {"1", "0", "true", "false"}:
            wanted_label = "Yes" if wanted_label.strip() in {"1", "true"} else "No"
        question = self.clean_text(e.get("question") or e.get("label"))
        try:
            payload = {"q": question or "", "wanted": wanted_label or "Yes"}
            found = await frame.evaluate("""({q, wanted}) => {
                const ynNorm = t => (t || '').replace(/\\s+/g, ' ').trim()
                    .replace(/^[✓✔✕✗×•●]\\s*/u, '').replace(/\\s*[✓✔✕✗×]$/u, '');
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
            }""", payload)
            if found:
                marker = frame.locator("[data-jobfill-yn='1']")
                target = await self.visible_locator(marker) or marker.first
                await target.click(force=True, timeout=LOCATOR_TIMEOUT_MS)
                try:
                    await target.evaluate("el => el.removeAttribute('data-jobfill-yn')")
                except Exception:
                    pass
                return True, "yes_no_button_click"
        except Exception:
            pass

        return False, "radio_option_not_found"

    async def set_checkbox(self,locator: Locator, answer: Any) -> Tuple[bool, str]:
        desired = self.norm(answer) in {"true", "yes", "1", "checked", "accept", "accepted", "agree", "i agree", "on"}
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

    async def set_checkbox_for_element(self, page: Page, element: Dict[str, Any], answer: Any) -> Tuple[bool, str]:
        
        loc, frame, _ = await self.locator_from_element(page, element)
        if loc:
            ok, method = await self.set_checkbox(loc, answer)
            if ok:
                return True, method
        if not frame:
            frame = page.main_frame
        wanted = self.norm(answer)
        desired = wanted in {"true", "yes", "1", "checked", "accept", "accepted", "agree", "i agree", "on"}
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
                q = self.norm(element.get("question") or element.get("label") or element.get("name"))
                hay = self.norm(" ".join(str(v) for v in attrs.values()))
                if q and (q in hay or any(t in hay for t in q.split() if len(t) >= 5)):
                    if desired:
                        await box.check(force=True, timeout=LOCATOR_TIMEOUT_MS)
                    else:
                        await box.uncheck(force=True, timeout=LOCATOR_TIMEOUT_MS)
                    return True, "native_checkbox_semantic"
            except Exception:
                continue
        aria_boxes = frame.locator("[role='checkbox'], [role='switch']")
        try:
            count = await aria_boxes.count()
        except Exception:
            count = 0
        q = self.norm(element.get("question") or element.get("label") or element.get("name"))
        for i in range(min(count, 1000)):
            box = aria_boxes.nth(i)
            try:
                if not await box.is_visible():
                    continue
                text = self.norm(await box.inner_text())
                aria = self.norm(await box.get_attribute("aria-label"))
                if q and (q in text or q in aria or any(t in self.norm(text + " " + aria) for t in q.split() if len(t) >= 5)):
                    current = await box.get_attribute("aria-checked")
                    if (current == "true") != desired:
                        await box.click(force=True, timeout=LOCATOR_TIMEOUT_MS)
                    return True, "aria_checkbox_semantic"
            except Exception:
                continue
        return False, "checkbox_not_found"

    async def verify_checkbox(self,locator: Locator, answer: Any) -> bool:
        desired = self.norm(answer) in {"true", "yes", "1", "checked", "accept", "accepted", "agree", "i agree", "on"}
        try:
            return (await locator.is_checked()) == desired
        except Exception:
            pass
        try:
            aria = (await locator.get_attribute("aria-checked") or "").lower()
            if aria in {"true", "false"}:
                return (aria == "true") == desired
        except Exception:
            pass
        try:
            cls = (await locator.get_attribute("class") or "").lower()
            if re.search(r"\b(checked|is-checked|active)\b", cls):
                return True == desired
            if re.search(r"\b(unchecked|is-unchecked)\b", cls):
                return False == desired
        except Exception:
            pass
        return False

    async def _file_input_has_files(self , locator: Locator) -> bool:
        try:
            files = await locator.evaluate("el => Array.from(el.files || []).map(f => f.name)")
            return bool(files)
        except Exception:
            return False

    async def resolve_file_input_locator(self , locator: Locator, frame: Optional[Frame] = None, page: Optional[Page] = None) -> Optional[Locator]:
        try:
            tag = self.norm(await locator.evaluate("el => (el.tagName || '').toLowerCase()"))
            typ = self.norm(await locator.get_attribute("type"))
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
            nearby = locator.locator("xpath=ancestor-or-self::*[self::form or self::div or self::section][1]//input[@type='file']")
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

    async def upload_file(self,locator: Locator, path: str, frame: Optional[Frame] = None, page: Optional[Page] = None) -> Tuple[bool, str]:
        if not path:
            return False, "resume_path_not_configured"
        p = Path(path)
        if not p.exists():
            return False, f"file_not_found:{path}"

        target_page = page or (locator.page if locator else None)

        # 1. Direct input[type='file'] check
        file_loc = await self.resolve_file_input_locator(locator, frame=frame, page=page)
        if file_loc is not None:
            try:
                await file_loc.set_input_files(str(p))
                await file_loc.evaluate("""el => {
                    el.dispatchEvent(new Event('input', { bubbles: true }));
                    el.dispatchEvent(new Event('change', { bubbles: true }));
                }""")
                if await self._file_input_has_files(file_loc):
                    return True, "file_uploaded"
                return True, "file_uploaded_unverified"
            except Exception:
                pass

        # 2. Trigger button / modal opener check
        if locator is not None:
            try:
                await locator.scroll_into_view_if_needed()
                await locator.click(timeout=2500)
                if target_page:
                    await target_page.wait_for_timeout(800)

                modal_file = None
                if target_page:
                    for sel in [
                        "#myModal input[type='file']",
                        ".modal input[type='file']",
                        "[role='dialog'] input[type='file']",
                        "input[type='file']",
                    ]:
                        cand = target_page.locator(sel).first
                        if await cand.count():
                            modal_file = cand
                            break

                if modal_file:
                    await modal_file.set_input_files(str(p))
                    await modal_file.evaluate("""el => {
                        el.dispatchEvent(new Event('input', { bubbles: true }));
                        el.dispatchEvent(new Event('change', { bubbles: true }));
                    }""")
                    if target_page:
                        await target_page.wait_for_timeout(500)

                    for submit_sel in [
                        "#resumeSumbmit",
                        "button#resumeSumbmit",
                        ".modal button:has-text('Upload')",
                        ".modal button:has-text('Submit')",
                        ".modal button:has-text('Done')",
                        ".modal button:has-text('Save')",
                        "[role='dialog'] button:has-text('Upload')",
                        "[role='dialog'] button[type='submit']",
                    ]:
                        sub_btn = target_page.locator(submit_sel).first
                        if await sub_btn.count() and await sub_btn.is_visible():
                            await sub_btn.click(timeout=2000)
                            await target_page.wait_for_timeout(1000)
                            break

                    return True, "modal_file_uploaded"
            except Exception as exc:
                return False, f"modal_upload_failed:{exc}"

        return False, "no_file_input_found"

    # ============================================================
    # PHONE COUNTRY-CODE FALLBACK DISCOVERY
    # ============================================================

    async def find_intl_tel_root(self, phone_locator: Locator) -> Optional[Locator]:
        try:
            root = phone_locator.locator("xpath=ancestor-or-self::*[contains(concat(' ', normalize-space(@class), ' '), ' iti ')][1]")
            if await root.count():
                return root.first
        except Exception:
            pass
        return None

    async def find_phone_country_button(self, phone_locator: Locator) -> Optional[Locator]:
        page = phone_locator.page
        try:
            root = await self.find_intl_tel_root(phone_locator)
            if root:
                opener = root.locator(".iti__selected-country, .iti__selected-flag, .iti__flag-container button").first
                if await opener.count():
                    return opener
        except Exception:
            pass
        try:
            btn = page.locator("div.PhoneInput button, div:has(> input.PhoneInputInput) button, div:has(> input[type='tel']) button, div:has(> input[placeholder*='phone' i]) button, .iti__selected-country, .iti__selected-flag").first
            if await btn.count():
                return btn
        except Exception:
            pass
        return None

    async def select_intl_tel_country(self, phone_locator: Locator, country_code: str) -> Tuple[bool, str, str]:
        country_code = self.clean_text(country_code)
        dial_digits = self.digits(country_code)
        if not dial_digits:
            dial_digits = COUNTRY_NAME_TO_DIAL.get(self.canonical_text(country_code), "")
        wanted = country_code or dial_digits
        if not wanted:
            return False, "invalid_country_code", ""
        iso = DIAL_TO_ISO.get(dial_digits, "")
        names = list(DIAL_TO_NAMES.get(dial_digits) or [])
        if self.canonical_text(country_code) and not self.digits(country_code):
            names.insert(0, self.canonical_text(country_code))

        # Modern Phone Component Support (Playwright piercing shadow roots)
        try:
            page = phone_locator.page
            btn = await self.find_phone_country_button(phone_locator)
            if btn and await btn.count():
                current_title = await btn.evaluate("""el => {
                    const svg = el.querySelector('svg[title]');
                    if (svg) return svg.getAttribute('title') || '';
                    return el.getAttribute('aria-label') || el.getAttribute('title') || el.innerText || '';
                }""")
                if (iso and iso.upper() in current_title.upper()) or (dial_digits and dial_digits in self.digits(current_title)):
                    return True, "already_set", current_title

                await btn.click(timeout=1500)
                await page.wait_for_timeout(400)

                search_input = page.locator('input[placeholder*="search" i], input[type="search"]:visible').first
                if await search_input.count():
                    search_term = names[0].title() if names else (DIAL_TO_PRIMARY_COUNTRY.get(dial_digits, dial_digits))
                    await search_input.fill(search_term)
                    await page.wait_for_timeout(350)

                match_targets = []
                if names:
                    match_targets.extend([n.title() for n in names] + names)
                if dial_digits:
                    match_targets.extend([f"+{dial_digits}", dial_digits])

                found = False
                for target in match_targets:
                    opt = page.locator(f'div:has(> span:text-is("{target}")), div:has(> span:has-text("{target}")), [role="option"]:has-text("{target}"), [role="menuitem"]:has-text("{target}"), li:has-text("{target}"), button:has-text("{target}")').last
                    if await opt.count():
                        await opt.click(timeout=1500)
                        await page.wait_for_timeout(400)
                        found = True
                        break

                if found:
                    new_state = await btn.evaluate("""el => {
                        const svg = el.querySelector('svg[title]');
                        if (svg) return svg.getAttribute('title') || '';
                        return el.getAttribute('aria-label') || el.getAttribute('title') || el.innerText || '';
                    }""")
                    return True, "modern_phone_country", new_state
        except Exception:
            pass

        payload = {"dial": dial_digits, "iso": iso, "names": names}
        pick_js = """(el, payload) => {
            const dial = String(payload.dial || '').replace(/\\D/g, '');
            const iso = String(payload.iso || '').toLowerCase();
            const names = (payload.names || []).map(n => String(n).toLowerCase());
            let root = el.closest('.iti') || el.closest('[class*="react-international-phone"]') || el.parentElement;
            for (let i = 0; i < 6 && root; i++) {
                const opener = root.querySelector('.iti__selected-country, .iti__selected-flag, .iti__flag-container, [class*="country-selector-button"], button[aria-label*="country" i], button[title*="country" i]');
                if (opener) break;
                root = root.parentElement;
            }
            if (!root) return {ok: false, state: '', reason: 'no_root'};
            const dropdowns = [...document.querySelectorAll('.iti__country-list, .iti__dropdown-content, [class*="country-selector-dropdown"]')];
            const dropdown = [...dropdowns].reverse().find(node => {
                const r = node.getBoundingClientRect();
                const s = getComputedStyle(node);
                return r.height > 20 && s.display !== 'none' && s.visibility !== 'hidden';
            }) || root;
            const search = dropdown.querySelector('.iti__search-input, input[type="search"], input[placeholder*="Search" i], input');
            const query = names[0] || '';
            if (search && query) {
                const proto = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value');
                if (proto && proto.set) proto.set.call(search, query);
                else search.value = query;
                search.dispatchEvent(new Event('input', {bubbles: true}));
                search.dispatchEvent(new Event('keyup', {bubbles: true}));
            }
            const items = [...dropdown.querySelectorAll('li.iti__country, li, [role="option"], [role="menuitem"], button, div[class*="cursor-pointer"]')];
            const visible = items.filter(node => {
                const r = node.getBoundingClientRect();
                const s = getComputedStyle(node);
                return r.height > 8 && s.display !== 'none' && s.visibility !== 'hidden';
            });
            const textOf = node => (node.innerText || node.textContent || '').replace(/\\s+/g, ' ').trim();
            const isoOf = node => (node.getAttribute('data-country-code') || node.getAttribute('data-iso2') || node.getAttribute('data-country') || '').toLowerCase();
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
            await phone_locator.evaluate("""el => {
                const root = el.closest('.iti') || el.closest('[class*="react-international-phone"]') || el.parentElement;
                const opener = root && root.querySelector('.iti__selected-country, .iti__selected-flag, .iti__flag-container button, [class*="country-selector-button"], button[aria-label*="country" i]');
                if (opener) opener.click();
                return true;
            }""")
            try:
                await phone_locator.page.wait_for_timeout(250)
            except Exception:
                pass
            state = await phone_locator.evaluate(pick_js, payload)
        except Exception as exc:
            return False, f"intl_tel_js_failed:{exc}", ""

        if not isinstance(state, dict):
            return False, "intl_tel_js_failed", ""
        shown = self.clean_text(state.get("state"))
        ok = bool(state.get("ok"))
        if ok:
            verified = (
                (dial_digits and (dial_digits in self.digits(shown) or f"+{dial_digits}" in shown))
                or (iso and iso in self.canonical_text(shown))
                or any(n in self.canonical_text(shown) for n in names)
            )
            return verified, "intl_tel_country", shown
        return False, "intl_tel_country_not_found", shown

    def phone_live_ok(self, actual: Any, national: Any, country_code: Any = "") -> bool:
        got = self.digits(actual)
        local = self.digits(national)
        if len(local) >= 10:
            local_10 = local[-10:]
        else:
            local_10 = local
        if not local_10 or not got:
            return False
        dial = self.digits(country_code) or "91"
        act_str = str(actual or "").strip()

        # If the DOM value explicitly starts with '+', the dial code MUST match
        if act_str.startswith("+"):
            if not got.startswith(dial):
                return False
            rest = got[len(dial):]
            return rest == local_10 or rest.endswith(local_10)

        if got == local_10:
            return True
        if got.endswith(local_10):
            if dial and got.startswith(dial):
                rest = got[len(dial):]
                return rest == local_10 or rest.endswith(local_10)
            return True
        return False

    async def fill_phone_widget(self, locator: Locator, national: str, country_code: str = "") -> Tuple[bool, str]:
        national_digits = self.digits(national)
        local_10 = national_digits[-10:] if len(national_digits) >= 10 else national_digits
        dial = self.digits(country_code) or "91"

        has_intl_flag = False
        country_btn = await self.find_phone_country_button(locator)
        if country_btn and await country_btn.count():
            has_intl_flag = True
            try:
                await self.select_intl_tel_country(locator, country_code or "91")
            except Exception:
                pass
        else:
            try:
                root = await self.find_intl_tel_root(locator)
                if root:
                    has_intl_flag = True
            except Exception:
                pass

        max_len = 0
        try:
            ml = await locator.get_attribute("maxlength")
            if ml and str(ml).isdigit():
                max_len = int(ml)
        except Exception:
            pass

        cls_str = self.norm(await locator.get_attribute("class") or "")
        val_str = str(await locator.get_attribute("value") or "").strip()
        has_intl_widget = (
            has_intl_flag
            or "phoneinput" in cls_str
            or "iti" in cls_str
            or val_str.startswith("+")
        )

        # Check if the phone bar or input already includes the country code
        has_bar_country_code = False
        try:
            has_bar_country_code = bool(await locator.evaluate("""(el, dial) => {
                const cleanDial = String(dial || '').replace(/\\D/g, '');
                const val = (el.value || '').trim();
                if (val.startsWith('+') || (cleanDial && val.startsWith(cleanDial))) return true;

                const ph = (el.getAttribute('placeholder') || '').trim();
                if (ph.startsWith('+') || (cleanDial && ph.startsWith('+' + cleanDial))) return true;

                const wrap = el.closest('.iti, .react-international-phone, [class*="phone"], [class*="tel"], .form-group, .input-group, div') || el.parentElement;
                if (wrap) {
                    const dialEl = wrap.querySelector('.iti__selected-dial-code, [class*="selected-dial-code"], [class*="dial-code"], [class*="country-code"], [class*="phone-prefix"], [class*="calling-code"]');
                    if (dialEl) {
                        const txt = (dialEl.innerText || dialEl.textContent || '').trim();
                        if (txt.includes('+') || (cleanDial && txt.includes(cleanDial))) return true;
                    }
                    const prev = el.previousElementSibling;
                    if (prev) {
                        const pTxt = (prev.innerText || prev.textContent || '').trim();
                        if (pTxt.includes('+') || (cleanDial && pTxt.includes(cleanDial))) return true;
                    }
                    const btn = wrap.querySelector('.iti__selected-flag, .iti__selected-country, [class*="country-selector"], [class*="flag-container"]');
                    if (btn) return true;
                }
                return false;
            }""", dial))
        except Exception:
            has_bar_country_code = False

        # Check if there is a separate country code column / dropdown on the form
        has_separate_country = False
        try:
            page = locator.page
            has_separate_country = bool(await page.evaluate("""() => {
                return !!document.querySelector(
                    '#countryCode, [id*="countryCode"], [name*="countryCode"], [id*="country_code"], [name*="country_code"], select[name*="country" i], select[id*="country" i]'
                );
            }"""))
        except Exception:
            has_separate_country = False

        # If the bar already has the country code OR there is a separate country column, enter phone number directly!
        input_type = self.norm(await locator.get_attribute("type") or "")
        bar_has_country = has_bar_country_code or has_intl_widget or has_intl_flag or (input_type == "number")
        if bar_has_country or has_separate_country or (max_len and max_len <= 11):
            candidates = [local_10]
        else:
            candidates = [local_10, f"+{dial}{local_10}"]

        for val in candidates:
            await self.fill_text(locator, val)
            st = await self.blur_and_check_invalid(locator)
            actual = self.clean_text(st.get("value")) or await self.read_control_value(locator)
            if not st.get("invalid") and self.phone_live_ok(actual, local_10, country_code):
                return True, actual

        ok, actual = await self.repair_invalid_text_input(locator, local_10)
        if ok and self.phone_live_ok(actual, local_10, country_code):
            return True, actual

        if country_code and has_intl_flag:
            await self.select_intl_tel_country(locator, country_code)
            try:
                await locator.page.wait_for_timeout(150)
                await locator.page.keyboard.press("Escape")
            except Exception:
                pass
            target_val = local_10
            await self.fill_text(locator, target_val)
            actual = await self.read_control_value(locator)
            if self.phone_live_ok(actual, local_10, country_code):
                return True, actual

        target_val = local_10
        await self.fill_text(locator, target_val)
        actual = await self.read_control_value(locator)
        return self.phone_live_ok(actual, local_10, country_code), actual

    async def fill_skills_tag(self, page: Page, frame: Any, locator: Locator, answer: str, element: Dict[str, Any]) -> Dict[str, Any]:
        skills_raw = str(answer or "")
        tokens = [t.strip() for t in re.split(r"[,;\n]+", skills_raw) if t.strip()]
        if not tokens:
            try:
                r_data = self.load_json(RESUME_FILE)
                r_idx = self.build_resume_index(r_data)
                res_skills = str(r_idx.get("all_skills") or r_idx.get("primary_skills") or "")
                tokens = [t.strip() for t in re.split(r"[,;\n]+", res_skills) if t.strip()]
            except Exception:
                tokens = []
        tokens = tokens[:8]

        added = []
        for sk in tokens:
            try:
                # Check if skill already exists before typing
                existing_pills = await page.evaluate("""() => [...document.querySelectorAll('.skl-selected-skill-li, .skillset-item, .skill-tag, .lyteTag, [class*="selected-skill"]')].map(el => el.innerText.trim())""")
                if any(sk.lower() in p.lower() for p in existing_pills):
                    continue

                await locator.scroll_into_view_if_needed(timeout=1000)
                await locator.click(timeout=1000)
                try:
                    await locator.fill("")
                except Exception:
                    pass
                await locator.type(sk, delay=15)
                await page.wait_for_timeout(250)

                sugg = frame.locator('#competencySkillsSuggestedList li, lyte-drop-item, .lyteDropdownOption, [role="option"]').filter(
                    has_text=re.compile(rf"^{re.escape(sk)}$", re.I)
                ).first
                if await sugg.count() and await sugg.is_visible():
                    await sugg.click(timeout=800)
                else:
                    await locator.press("Enter")
                await page.wait_for_timeout(200)
                added.append(sk)
            except Exception:
                continue

        try:
            pills = await page.evaluate("""() => [...document.querySelectorAll('.skl-selected-skill-li, .skillset-item, .skill-tag, .lyteTag, [class*="selected-skill"]')].map(el => el.innerText.trim()).filter(Boolean)""")
        except Exception:
            pills = []

        ok = len(pills) > 0 or len(added) > 0
        return {
            "method": "skills_tag_added",
            "verified": ok,
            "actual_value": ", ".join(pills) if pills else ", ".join(added),
            "answer": ", ".join(tokens),
        }

    async def discover_phone_country_control(self, page: Page, phone_locator: Locator) -> Optional[Locator]:
        try:
            root = await self.find_intl_tel_root(phone_locator)
            if root:
                opener = root.locator(".iti__selected-country, .iti__selected-flag, .iti__flag-container button")
                visible = await self.visible_locator(opener)
                if visible:
                    return visible
        except Exception:
            pass
        try:
            frame = await phone_locator.owner_frame()
            if frame is None:
                return None
            candidates = phone_locator.locator("xpath=ancestor::*[self::div or self::td or self::section or self::form][1]").locator("select, [role='combobox'], button, input")
            count = await candidates.count()
            for i in range(min(count, 100)):
                candidate = candidates.nth(i)
                try:
                    if not await candidate.is_visible():
                        continue
                    attrs = await candidate.evaluate("""el => ({
                        id: el.id || '',
                        name: el.getAttribute('name') || '',
                        aria: el.getAttribute('aria-label') || '',
                        placeholder: el.getAttribute('placeholder') || '',
                        text: el.innerText || '',
                        className: el.className || ''
                    })""")
                    hay = self.norm(" ".join(str(v) for v in attrs.values() if v))
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
        self,
        page: Page,
        elements: List[Dict[str, Any]],
        resume_idx: Dict[str, Any],
        deterministic: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        country = self.clean_text(resume_idx.get("country_code"))
        if not country:
            return []

        results: List[Dict[str, Any]] = []
        wanted_digits = self.digits(country)

        for e in elements:
            if not self.is_phone_element(e) or self.is_phone_country_element(e):
                continue
            try:
                loc, _, _ = await self.locator_from_element(page, e)
                if not loc:
                    continue
                root = await self.find_intl_tel_root(loc)
                if not root:
                    continue
                state = self.clean_text(await root.evaluate("""el => {
                    const s = el.querySelector('.iti__selected-country, .iti__selected-flag, [class*="country-selector-button"]');
                    return (s && (s.getAttribute('aria-label') || s.innerText)) || '';
                }"""))
                if wanted_digits and wanted_digits in self.digits(state):
                    return results
            except Exception:
                continue

        country_elements = []
        seen_ids = set()
        for e in elements:
            if self.is_noise_control(e) or self.is_captcha(e) or self.is_submit(e):
                continue
            if not self.is_phone_country_element(e):
                continue
            ident = self.clean_text(e.get("id") or e.get("selector") or e["_element_index"])
            if ident in seen_ids:
                continue
            seen_ids.add(ident)
            country_elements.append(e)
            if len(country_elements) >= 2:
                break

        for e in country_elements:
            loc, _, strategy = await self.locator_from_element(page, e)
            if not loc:
                continue
            try:
                phone_like = self.is_phone_element(e)
                if phone_like:
                    verified, method, state = await self.select_intl_tel_country(loc, country)
                    if verified:
                        results.append({
                            "element_index": e["_element_index"],
                            "field": "phone_coself.untry_code",
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

            ok, method = await self.select_native_or_custom(
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
                    if (s2) { s2.textContent = val; s2.title = val; }
                }""", country)
            except Exception:
                pass
            actual = await self.read_selected_control_text(loc) if ok else ""
            verified = bool(
                ok and self.clean_text(actual)
                and (self.same_value(country, actual) or wanted_digits == self.digits(actual) or wanted_digits in self.digits(actual))
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

        phone_element = next((
            e for e in elements
            if self.is_phone_element(e) and not self.is_phone_country_element(e)
            and not self.is_captcha(e) and not self.is_submit(e)
        ), None)

        if phone_element:
            phone_loc, _, phone_strategy = await self.locator_from_element(page, phone_element)
            if phone_loc:
                verified, method, state = await self.select_intl_tel_country(phone_loc, country)
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

                country_loc = await self.discover_phone_country_control(page, phone_loc)
                if country_loc:
                    ok, method = await self.select_native_or_custom(country_loc, country, country, country)
                    actual = await self.read_selected_control_text(country_loc) if ok else ""
                    verified = bool(
                        ok and self.clean_text(actual)
                        and (self.same_value(country, actual) or wanted_digits == self.digits(actual) or wanted_digits in self.digits(actual))
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

    def is_placeholder_option_label(self,label: str, question: str = "") -> bool:
        s = self.norm(label)
        q = self.norm(question)
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

    def infer_job_position_option(self,page_url: str, page_title: str, options: List[Any]) -> Optional[Dict[str, Any]]:
        url_slug = ""
        if page_url:
            last_seg = page_url.rstrip("/").split("/")[-1]
            url_slug = re.sub(r"-\d+$", "", last_seg).replace("-", " ").strip().lower()
        title_slug = self.canonical_text(page_title)
        best = None
        best_score = 0
        for opt in options:
            lbl = self.option_display(opt)
            val = opt.get("value") if isinstance(opt, dict) else lbl
            if self.is_placeholder_option_label(lbl):
                continue
            c_lbl = self.canonical_text(lbl)
            s1 = self.fuzzy_score(url_slug, c_lbl) if url_slug else 0
            s2 = self.fuzzy_score(title_slug, c_lbl) if title_slug else 0
            score = max(s1, s2)
            if url_slug and set(url_slug.split()).issubset(set(c_lbl.split())):
                score = max(score, 90)
            if score > best_score:
                best_score = score
                best = {"label": lbl, "value": val}
        return best if best_score >= 60 else None

    async def verify_select_control(
        self,
        locator: Locator,
        expected: Any,
        option_label: Any = None,
        option_value: Any = None,
        question: str = "",
    ) -> Tuple[bool, str]:
        targets = [x for x in [option_label, option_value, expected] if self.clean_text(x)]
        if not targets:
            return False, ""
        actuals: List[str] = []
        try:
            tag = self.norm(await locator.evaluate("el => el.tagName.toLowerCase()"))
        except Exception:
            tag = ""

        if tag == "select":
            try:
                selected = await locator.evaluate("""el => {
                    const o = el.selectedOptions?.[0];
                    return o ? {text: (o.textContent || '').trim(), value: o.value || ''} : null;
                }""") or {}
                actuals.append(self.clean_text(selected.get("text")))
                actuals.append(self.clean_text(selected.get("value")))
            except Exception:
                pass
        else:
            try:
                actuals.append(await self.read_select_display(locator))
            except Exception:
                pass
            try:
                actuals.append(self.clean_text(await locator.input_value()))
            except Exception:
                pass

        actuals = [a for a in actuals if self.clean_text(a)]
        if not actuals:
            return False, ""
        shown = next((a for a in actuals if self.clean_text(a)), "")

        if self.is_placeholder_option_label(shown, question):
            return False, shown
        if self.looks_like_group_heading(shown):
            return False, shown

        for a in actuals:
            for t in targets:
                if self.same_value(t, a):
                    return True, shown
                if self.canonical_text(t) == self.canonical_text(a) and self.canonical_text(t):
                    return True, shown

        for a in actuals:
            da = self.digits(a)
            for t in targets:
                dt = self.digits(t)
                if dt and da and dt == da:
                    return True, shown

        if any(self.is_fallback_other_label(a) for a in actuals) and any(
            re.search(r"university|college|institute|\bschool\b", self.canonical_text(t)) for t in targets
        ):
            return True, shown

        best = 0
        for a in actuals:
            for t in targets:
                best = max(best, self.fuzzy_score(t, a), self._option_match_score(t, a))
        return (best >= 80), shown

    async def multi_step_verify_and_repair(
        self,
        page: Page,
        element: Dict[str, Any],
        result: Dict[str, Any],
        resume_idx: Dict[str, Any],
    ) -> Tuple[bool, str, List[str]]:
        steps_log: List[str] = []
        loc, frame, strategy = await self.locator_from_element(page, element)
        if not loc:
            steps_log.append("[Step 1: Read] Locator could not be resolved from DOM.")
            steps_log.append("[Step 2: Check] FAIL: Element not found on page.")
            return False, "", steps_log

        idx = element.get("_element_index")
        q_title = self.clean_text(element.get("question") or element.get("label") or element.get("name") or "")
        kind = self.control_kind(element)
        answer = self.clean_text(result.get("answer"))

        if self.is_phone_element(element) and not self.is_phone_country_element(element) and result.get("field") != "phone_country_code":
            national = self.clean_text(resume_idx.get("national_phone") or answer)
            local_10 = self.digits(national)[-10:] if len(self.digits(national)) >= 10 else self.digits(national)
            cc = self.clean_text(resume_idx.get("country_code") or result.get("country_code") or "")
            if not cc and resume_idx.get("country"):
                c_low = resume_idx.get("country", "").lower()
                if c_low in COUNTRY_NAME_TO_DIAL:
                    cc = "+" + COUNTRY_NAME_TO_DIAL[c_low]
            dial = self.digits(cc)

            actual = await self.read_control_value(loc)
            actual_digs = self.digits(actual)
            steps_log.append(f"[Step 1: Read] Phone DOM value: '{actual}' (digits: '{actual_digs}')")

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
            elif not self.phone_live_ok(actual, local_10, cc):
                fail_reason = f"Phone digits '{actual_digs}' incomplete or missing 10-digit national number '{local_10}'"
                truncation_issue = True

            if fail_reason:
                steps_log.append(f"[Step 2: Check] FAIL: {fail_reason}")
            else:
                steps_log.append(f"[Step 2: Check] PASS: Complete 10-digit national number '{local_10}' verified in field")

            if truncation_issue:
                steps_log.append(f"[Step 3: Repair] Re-filling phone widget with country '{cc}' and number '{local_10}'")
                try:
                    ok_rep, post_rep = await self.fill_phone_widget(loc, local_10, cc)
                    steps_log.append(f"[Step 3: Repair] Phone widget repair: ok={ok_rep}, val='{post_rep}'")
                except Exception as ex:
                    steps_log.append(f"[Step 3: Repair] Repair error: {ex}")

            post_actual = await self.read_control_value(loc)
            ok = self.phone_live_ok(post_actual, local_10, cc)
            status_str = "VERIFIED (Repaired)" if (ok and truncation_issue) else ("VERIFIED" if ok else "FAILED")
            steps_log.append(f"[Step 4: Confirm] Re-read live value: '{post_actual}' -> {status_str}")
            return ok, post_actual, steps_log

        if self.is_phone_country_element(element) or result.get("field") == "phone_country_code":
            expected_cc = self.clean_text(answer or resume_idx.get("country_code") or "")
            if not expected_cc and resume_idx.get("country"):
                c_low = resume_idx.get("country", "").lower()
                if c_low in COUNTRY_NAME_TO_DIAL:
                    expected_cc = "+" + COUNTRY_NAME_TO_DIAL[c_low]
            actual = await self.read_selected_control_text(loc) or await self.read_control_value(loc)
            steps_log.append(f"[Step 1: Read] Phone country display: '{actual}'")

            ok = self.same_value(expected_cc, actual) or (expected_cc in actual) or (self.digits(expected_cc) == self.digits(actual))
            if ok:
                steps_log.append(f"[Step 2: Check] PASS: Country code matches '{expected_cc}'")
            else:
                steps_log.append(f"[Step 2: Check] FAIL: Country code '{actual}' != '{expected_cc}'")
                steps_log.append(f"[Step 3: Repair] Re-selecting phone country '{expected_cc}'")
                try:
                    rep_ok, _ = await self.select_native_or_custom(
                        loc, expected_cc,
                        option_label=expected_cc,
                        option_value=expected_cc,
                        stored_options=element.get("options")
                    )
                    if not rep_ok:
                        await self.select_intl_tel_country(loc, expected_cc)
                except Exception as ex:
                    steps_log.append(f"[Step 3: Repair] Repair error: {ex}")

            post_actual = await self.read_selected_control_text(loc) or await self.read_control_value(loc)
            ok = self.same_value(expected_cc, post_actual) or (expected_cc in post_actual) or (self.digits(expected_cc) == self.digits(post_actual))
            steps_log.append(f"[Step 4: Confirm] Re-read country value: '{post_actual}' -> {'VERIFIED' if ok else 'FAILED'}")
            return ok, post_actual, steps_log

        if kind == "skills_tag" or self.is_skill_tag_input(element):
            pills = []
            try:
                pills = await page.evaluate("""() => [...document.querySelectorAll('.skl-selected-skill-li, .skillset-item, .skill-tag, .lyteTag, [class*="selected-skill"]')].map(el => el.innerText.trim()).filter(Boolean)""")
            except Exception:
                pass
            steps_log.append(f"[Step 1: Read] Skill tags present in DOM: {pills}")
            if pills:
                steps_log.append(f"[Step 2: Check] PASS: {len(pills)} skill tags confirmed.")
                steps_log.append(f"[Step 4: Confirm] Skill tags -> VERIFIED")
                return True, ", ".join(pills), steps_log
            else:
                steps_log.append(f"[Step 2: Check] FAIL: No skill tags found. Attempting repair...")
                rep_res = await self.fill_skills_tag(page, page, loc, answer, element)
                ok = rep_res.get("verified", False)
                try:
                    post_pills = await page.evaluate("""() => [...document.querySelectorAll('.skl-selected-skill-li, .skillset-item, .skill-tag, .lyteTag, [class*="selected-skill"]')].map(el => el.innerText.trim()).filter(Boolean)""")
                except Exception:
                    post_pills = []
                status_str = "VERIFIED" if (ok or post_pills) else "FAILED"
                steps_log.append(f"[Step 4: Confirm] Re-read skills: {post_pills} -> {status_str}")
                return (ok or bool(post_pills)), ", ".join(post_pills), steps_log

        if self.is_date_element(element) or element.get("is_datepicker"):
            actual = await self.read_control_value(loc)
            expected_date = self.parse_date_value(answer)
            actual_date = self.parse_date_value(actual)
            steps_log.append(f"[Step 1: Read] Date value: '{actual}'")
            ok = self.same_value(answer, actual) or self.is_date_match(answer, actual)

            if ok:
                steps_log.append(f"[Step 2: Check] PASS: Date matches '{answer}'")
            else:
                steps_log.append(f"[Step 2: Check] FAIL: Date '{actual}' != '{answer}'")
                steps_log.append(f"[Step 3: Repair] Re-filling date with '{answer}'")
                parsed = self.parse_date_value(answer)
                fmt = parsed.strftime("%m/%Y") if parsed else str(answer)
                iso = parsed.strftime("%Y-%m-%d") if parsed else str(answer)
                try:
                    await loc.evaluate("""(el, {fmt, iso}) => {
                        el.removeAttribute('readonly');
                        let kendo = window.jQuery ? (window.jQuery(el).data("kendoDatePicker") || window.jQuery(el).data("kendoDateTimePicker")) : null;
                        if (kendo) {
                            let d = new Date(iso);
                            if (!isNaN(d.getTime())) {
                                if (kendo.max && d > kendo.max()) {
                                    kendo.max(new Date(d.getFullYear() + 10, 11, 31));
                                }
                                kendo.value(d);
                                kendo.trigger("change");
                                return;
                            }
                        }
                        let isDateType = (el.type === 'date' || el.getAttribute('type') === 'date');
                        el.value = isDateType ? iso : fmt;
                        el.dispatchEvent(new Event('input', {bubbles: true}));
                        el.dispatchEvent(new Event('change', {bubbles: true}));
                        el.dispatchEvent(new Event('blur', {bubbles: true}));
                    }""", {"fmt": fmt, "iso": iso})
                except Exception:
                    try:
                        is_date_type = (self.norm(element.get("type")) == "date") or (await loc.get_attribute("type") == "date")
                        await self.fill_text(loc, iso if is_date_type else fmt)
                    except Exception:
                        pass
                try:
                    await self.pick_opened_calendar(page, loc, answer)
                except Exception:
                    pass

            try:
                await page.keyboard.press("Escape")
            except Exception:
                pass
            post_actual = await self.read_control_value(loc)
            ok = self.same_value(answer, post_actual) or self.is_date_match(answer, post_actual)
            steps_log.append(f"[Step 4: Confirm] Date value: '{post_actual}' -> {'VERIFIED' if ok else 'FAILED'}")
            return ok, post_actual, steps_log

        if kind == "select":
            target_lbl = self.clean_text(result.get("option_label") or result.get("matched_option", {}).get("label") or answer)
            target_val = self.clean_text(result.get("option_value") or result.get("matched_option", {}).get("value"))

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

            live_text = sel_state.get("text") or await self.read_select_display(loc)
            live_val = sel_state.get("value") or ""
            steps_log.append(f"[Step 1: Read] Dropdown selected option: '{live_text}' (value: '{live_val}')")

            is_placeholder = self.is_placeholder_option_label(live_text, q_title) or (sel_state.get("index") == 0 and self.is_placeholder_option_label(live_text))
            match_ok = False
            if not is_placeholder:
                match_ok, _ = await self.verify_select_control(loc, target_lbl, target_lbl, target_val, question=q_title)

            if is_placeholder:
                steps_log.append(f"[Step 2: Check] FAIL: Placeholder currently selected ('{live_text}'). No real option chosen.")
            elif not match_ok:
                steps_log.append(f"[Step 2: Check] FAIL: Selected option '{live_text}' does not match target '{target_lbl}'.")
            else:
                steps_log.append(f"[Step 2: Check] PASS: Valid option '{live_text}' selected.")

            repaired = False
            chosen = None
            if is_placeholder or not match_ok:
                valid_opts = [o for o in element.get("options", []) if not self.is_placeholder_option_label(self.option_display(o), q_title)]
                if target_lbl and not self.is_placeholder_option_label(target_lbl, q_title):
                    for opt in valid_opts:
                        lbl = self.option_display(opt)
                        val = opt.get("value") if isinstance(opt, dict) else lbl
                        if self.same_value(target_lbl, lbl) or self.same_value(target_val, val) or self.fuzzy_score(target_lbl, lbl) >= 65 or self._option_match_score(target_lbl, lbl) >= 70:
                            chosen = {"label": lbl, "value": val}
                            break
                if not chosen:
                    if "position" in self.canonical_text(q_title) or "role" in self.canonical_text(q_title):
                        chosen = self.infer_job_position_option(page.url, await page.title(), valid_opts)
                if not chosen and valid_opts:
                    chosen = {"label":self.option_display(valid_opts[0]), "value": valid_opts[0].get("value") if isinstance(valid_opts[0], dict) else self.option_display(valid_opts[0])}

                if chosen:
                    steps_log.append(f"[Step 3: Repair] Selecting valid option '{chosen['label']}' (value: '{chosen['value']}')")
                    try:
                        tag = self.norm(await loc.evaluate("el => el.tagName.toLowerCase()"))
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

            post_display = await self.read_select_display(loc)
            target_check = chosen["label"] if (repaired and chosen) else target_lbl
            post_ok, final_shown = await self.verify_select_control(
                loc, target_check, target_check,
                chosen.get("value") if (repaired and chosen) else target_val,
                question=q_title,
            )
            if self.is_placeholder_option_label(final_shown, q_title):
                post_ok = False
            status_str = "VERIFIED (Repaired)" if (post_ok and (is_placeholder or not match_ok)) else ("VERIFIED" if post_ok else "FAILED")
            steps_log.append(f"[Step 4: Confirm] Re-read selected option: '{final_shown}' -> {status_str}")
            return post_ok, final_shown, steps_log

        if self.is_file_upload(element) or result.get("action") == "upload":
            if not self.looks_like_resume_upload(element):
                steps_log.append("[Step 1: Read] Optional non-resume file upload (photo/attachment).")
                steps_log.append("[Step 2: Check] PASS: Skipped uploading resume PDF to non-resume input.")
                return True, "skipped_non_resume", steps_log
            files = ""
            file_loc = await self.resolve_file_input_locator(loc, frame=frame, page=page)
            target_file_loc = file_loc or loc
            try:
                files = await target_file_loc.evaluate("el => (el.value || Array.from(el.files || []).map(f => f.name).join(', '))")
            except Exception:
                files = ""
            name = Path(RESUME_PATH).name if RESUME_PATH else ""    
            shown = ""
            try:
                shown = await page.evaluate("""name => {
                    const els = Array.from(document.querySelectorAll('.resumeFileName, #changeResumeFile, [class*="resume"], [id*="resume"], .k-file-name, .k-filename, [class*="upload-file"]'));
                    return els.map(e => e.innerText || e.textContent || '').join(' ');
                }""", name)
            except Exception:
                pass
            name = Path(RESUME_PATH).name if RESUME_PATH else ""
            hay = self.canonical_text(f"{files} {shown}")
            live_ok = (name and self.canonical_text(name) in hay) or bool(files)

            steps_log.append(f"[Step 1: Read] Upload field state: '{self.clean_text(name if live_ok else (files or shown))[:50]}'")
            if live_ok:
                steps_log.append(f"[Step 2: Check] PASS: Resume file attached")
            else:
                steps_log.append(f"[Step 2: Check] FAIL: No resume attached")
                if RESUME_PATH and Path(RESUME_PATH).exists():
                    steps_log.append(f"[Step 3: Repair] Re-uploading resume '{name}'")
                    try:
                        rep_ok, rep_m = await self.upload_file(loc, RESUME_PATH, frame=frame, page=page)
                        if rep_ok:
                            live_ok = True
                    except Exception as ex:
                        steps_log.append(f"[Step 3: Repair] Upload error: {ex}")

            expected_name = Path(RESUME_PATH).name if RESUME_PATH else ""
            actual_files: List[str] = []
            if target_file_loc:
                try:
                    actual_files = await target_file_loc.evaluate("el => Array.from(el.files || []).map(f => f.name)") or []
                except Exception:
                    actual_files = []
                if not actual_files:
                    try:
                        val_str = await target_file_loc.evaluate("el => el.value || ''")
                        if val_str:
                            actual_files = [val_str.split('\\')[-1].split('/')[-1]]
                    except Exception:
                        pass
            live_ok2 = bool(expected_name) and (
                expected_name in actual_files
                or any(expected_name.lower() in f.lower() for f in actual_files)
                or (name and self.canonical_text(name) in self.canonical_text(shown))
                or live_ok
            )
            steps_log.append(f"[Step 4: Confirm] Resume upload -> {'VERIFIED' if live_ok2 else 'FAILED'}")
            return live_ok2, ", ".join(actual_files) or name, steps_log

        if kind == "checkbox":
            expected_checked = self.is_positive_answer(answer)
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

        if kind == "radio":
            steps_log.append("[Step 1: Read] Radio group state inspection")
            ok = await self.is_choice_selected(page, element, result.get("option_label") or result.get("answer"))
            steps_log.append(f"[Step 2: Check] {'PASS: Radio selected' if ok else 'FAIL: Radio unselected'}")
            if not ok:
                steps_log.append(f"[Step 3: Repair] Re-attempting radio click for '{answer}'")
                try:
                    await self.click_radio(page, element, answer, result.get("option_label"), result.get("option_value"))
                except Exception:
                    pass
                ok = await self.is_choice_selected(page, element, result.get("option_label") or result.get("answer"))
            steps_log.append(f"[Step 4: Confirm] Radio -> {'VERIFIED' if ok else 'FAILED'}")
            return ok, answer, steps_log

        actual = await self.read_control_value(loc)
        expected = answer
        steps_log.append(f"[Step 1: Read] Text value: '{actual[:60]}'")

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
            steps_log.append(f"[Step 2: Check] PASS: Display-only element '{actual[:40]}' verified")
            steps_log.append(f"[Step 4: Confirm] Live value: '{actual[:40]}' -> VERIFIED")
            return True, actual, steps_log

        hay = self.canonical_text(" ".join(str(element.get(k) or "") for k in ("id", "name", "label", "question", "placeholder", "selector")))
        fk = self.norm(element.get("field_key") or "")
        is_loc_field = (
            any(w in hay for w in ["location", "city", "address", "state", "place"])
            or fk in {"current_location", "location", "city", "work_location", "desired_location", "preferred_location", "base_location"}
        )
        is_auto = (
            is_loc_field
            or self.norm(element.get("role") or element.get("runtime_role")) == "combobox"
            or self.norm(element.get("aria_autocomplete")) in {"list", "both", "inline"}
            or bool(element.get("haspopup"))
        )

        loc_score = self.score_location_suggestion(actual, expected, resume_idx) if is_loc_field else 0.0
        match_ok = (
            self.same_value(expected, actual)
            or (expected in actual)
            or (self.digits(expected) and self.digits(expected) == self.digits(actual))
            or self.display_matches(expected, actual, element)
            or (self.fuzzy_score(expected, actual) >= 65)
            or (is_loc_field and loc_score >= 30.0)
        )
        fail_reason = ""
        if not actual and expected:
            fail_reason = f"Field is empty (expected '{expected[:40]}')"
        elif not match_ok:
            if len(actual) < len(expected):
                fail_reason = f"Text truncated ({len(actual)} chars in DOM vs {len(expected)} chars expected)"
            else:
                fail_reason = f"Value mismatch ('{actual[:30]}' != '{expected[:30]}')"

        if not (is_loc_field or is_auto):
            st = await self.blur_and_check_invalid(loc)
            if st.get("invalid") and not fail_reason:
                fail_reason = st.get("validationMessage") or "native constraint invalid"

        if fail_reason:
            steps_log.append(f"[Step 2: Check] FAIL: {fail_reason}")
            if not actual and expected:
                steps_log.append(f"[Step 3: Repair] Re-filling empty field with '{expected[:40]}'")
                try:
                    if is_auto:
                        await loc.focus()
                        await loc.fill("")
                        await loc.type(expected, delay=40)
                        target_scope = frame or page
                        clicked_sugg, best_matched = await self.select_best_autocomplete_suggestion(
                            target_scope, loc, expected, resume_idx=resume_idx
                        )
                        if clicked_sugg:
                            steps_log.append(f"[Step 3: Repair] Selected location suggestion '{best_matched}'")
                    else:
                        await self.fill_text(loc, expected)
                        await loc.evaluate("""el => {
                            el.dispatchEvent(new Event('input', {bubbles: true}));
                            el.dispatchEvent(new Event('change', {bubbles: true}));
                        }""")
                except Exception as ex:
                    steps_log.append(f"[Step 3: Repair] Re-fill error: {ex}")
            else:
                steps_log.append("[Step 3: Repair] Constraint-based formats (no extra AI, no repeat clicks)")
                try:
                    ok_fix, post = await self.repair_invalid_text_input(loc, expected)
                    steps_log.append(f"[Step 3: Repair] now='{post[:50]}' valid={ok_fix}")
                except Exception as ex:
                    steps_log.append(f"[Step 3: Repair] Error during constraint repair: {ex}")
        else:
            steps_log.append(f"[Step 2: Check] PASS: Text matches expected value")

        post_actual = await self.read_control_value(loc)
        post_loc_score = self.score_location_suggestion(post_actual, expected, resume_idx) if is_loc_field else 0.0
        ok = (
            self.same_value(expected, post_actual)
            or (expected in post_actual)
            or (self.digits(expected) and self.digits(expected) == self.digits(post_actual))
            or self.display_matches(expected, post_actual, element)
            or (self.fuzzy_score(expected, post_actual) >= 65)
            or (is_loc_field and post_loc_score >= 30.0)
        )
        status_str = "VERIFIED (Repaired)" if (ok and fail_reason) else ("VERIFIED" if ok else "FAILED")
        steps_log.append(f"[Step 4: Confirm] Re-read live value: '{post_actual[:60]}' -> {status_str}")
        return ok, post_actual, steps_log

    # ============================================================
    # FILL ONE ANSWER
    # ============================================================

    def _parse_target_calendar_date(self, val: Any, default_month: int = 8, default_day: int = 1) -> Tuple[int, int, int]:
        if isinstance(val, (datetime, date)):
            return val.year, val.month, val.day
        val_str = str(val or "").strip()
        if not val_str or val_str.lower() in {"present", "current", "now", "currently enrolled", "immediate", "urgent"}:
            now = datetime.now()
            return now.year, now.month, now.day

        m = re.match(r"^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})$", val_str)
        if m:
            return int(m.group(1)), int(m.group(2)), int(m.group(3))

        m = re.match(r"^(\d{4})[-/.](\d{1,2})$", val_str)
        if m:
            return int(m.group(1)), int(m.group(2)), default_day

        m = re.match(r"^(\d{4})$", val_str)
        if m:
            return int(m.group(1)), default_month, default_day

        m = re.match(r"^(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})$", val_str)
        if m:
            return int(m.group(3)), int(m.group(2)), int(m.group(1))

        return 2023, default_month, default_day

    async def pick_opened_calendar(self , page: Page, locator: Locator, parsed: Any) -> bool:
        target_year, target_month, target_day = self._parse_target_calendar_date(parsed)
        now = datetime.now()

        try:
            await locator.scroll_into_view_if_needed()
        except Exception:
            pass
        try:
            await locator.click(timeout=3000)
        except Exception:
            return False
        await page.wait_for_timeout(350)

        # Locate dialog/popover (Playwright locator pierces shadow roots)
        dlg = page.locator('[role="dialog"]:visible, [role="region"]:visible, .rdp:visible, [data-side]:visible, div.k-animation-container:visible, div.flatpickr-calendar.open:visible, div.datepicker:visible').first
        if not await dlg.count():
            dlg = page.locator('[role="dialog"]').first

        if not await dlg.count():
            return False

        # Check for native or select dropdowns
        try:
            selects = await dlg.locator('select').all()
            for s in selects:
                opts = await s.locator('option').all_text_contents()
                if any(str(target_year) in opt for opt in opts):
                    await s.select_option(label=str(target_year))
                month_abbr = _CALENDAR_MONTHS[target_month - 1].lower()
                for opt in opts:
                    if month_abbr in opt.lower():
                        await s.select_option(label=opt)
                        break
        except Exception:
            pass

        # Check for Month/Year header button (e.g. "October 2026")
        hdr_btn = dlg.locator('button').filter(has_text=re.compile(r'\b(19\d{2}|20\d{2})\b')).first
        if await hdr_btn.count():
            try:
                await hdr_btn.click()
                await page.wait_for_timeout(350)

                # In month view, read current year
                dlg_text = await dlg.inner_text()
                m_curr_yr = re.search(r'\b(19\d{2}|20\d{2})\b', dlg_text)
                current_year = int(m_curr_yr.group(1)) if m_curr_yr else now.year

                diff = target_year - current_year
                if diff != 0:
                    arrow_btns = dlg.locator('button:has(svg), button[aria-label*="prev" i], button[aria-label*="next" i], button[aria-label*="arrow" i]')
                    if await arrow_btns.count() >= 2:
                        step_btn = arrow_btns.nth(1) if diff > 0 else arrow_btns.first
                        for _ in range(abs(diff)):
                            dis = await step_btn.evaluate("el => el.disabled || el.getAttribute('aria-disabled') === 'true' || el.className.includes('disabled')")
                            if dis:
                                break
                            await step_btn.click()
                            await page.wait_for_timeout(150)

                # Pick target month button
                target_month_name = _CALENDAR_MONTHS[target_month - 1]
                month_btn = dlg.locator(f'button:text-is("{target_month_name}")').first
                if await month_btn.count():
                    m_dis = await month_btn.evaluate("el => el.disabled || el.getAttribute('aria-disabled') === 'true' || el.className.includes('disabled')")
                    if not m_dis:
                        await month_btn.click()
                        await page.wait_for_timeout(300)
                    else:
                        avail_months = dlg.locator('button:text-matches("^(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)$"):not([disabled])')
                        if await avail_months.count():
                            await avail_months.last.click()
                            await page.wait_for_timeout(300)
            except Exception:
                pass

        # Pick the day in the day grid
        try:
            enabled_days = dlg.locator('button[name="day"]:not([disabled]):not([class*="disabled"]):not([class*="not-allowed"]), button[role="gridcell"]:not([disabled]):not([class*="disabled"]):not([class*="not-allowed"]), td[role="gridcell"]:not([class*="disabled"]):not([class*="not-allowed"])')
            if await enabled_days.count() == 0:
                # If all days in this view are disabled (e.g. future date), switch to current valid month/year
                hdr = dlg.locator('button').filter(has_text=re.compile(r'\b(19\d{2}|20\d{2})\b')).first
                if await hdr.count():
                    await hdr.click()
                    await page.wait_for_timeout(300)
                    dlg_text = await dlg.inner_text()
                    m_yr = re.search(r'\b(19\d{2}|20\d{2})\b', dlg_text)
                    cur_yr = int(m_yr.group(1)) if m_yr else now.year
                    if cur_yr > now.year:
                        arrow_btns = dlg.locator('button:has(svg), button[aria-label*="prev" i]')
                        if await arrow_btns.count():
                            for _ in range(cur_yr - now.year):
                                await arrow_btns.first.click()
                                await page.wait_for_timeout(150)
                    now_m = _CALENDAR_MONTHS[now.month - 1]
                    m_btn = dlg.locator(f'button:text-is("{now_m}")').first
                    if await m_btn.count():
                        await m_btn.click()
                        await page.wait_for_timeout(300)
                enabled_days = dlg.locator('button[name="day"]:not([disabled]):not([class*="disabled"]):not([class*="not-allowed"]), button[role="gridcell"]:not([disabled]):not([class*="disabled"]):not([class*="not-allowed"]), td[role="gridcell"]:not([class*="disabled"]):not([class*="not-allowed"])')
                target_day = now.day

            target_day_btn = enabled_days.filter(has_text=re.compile(rf'^\s*{target_day}\s*$')).first
            if await target_day_btn.count():
                await target_day_btn.click(timeout=1500)
            elif await enabled_days.count():
                day_1 = enabled_days.filter(has_text=re.compile(r'^\s*1\s*$')).first
                if await day_1.count():
                    await day_1.click(timeout=1500)
                else:
                    await enabled_days.last.click(timeout=1500)
        except Exception:
            pass

        await page.wait_for_timeout(250)
        # Click Apply / OK / Done if present
        try:
            apply_btn = dlg.locator('button:has-text("Apply"), button:has-text("OK"), button:has-text("Done"), button:has-text("Set")').first
            if await apply_btn.count():
                await apply_btn.click(timeout=1500)
                await page.wait_for_timeout(250)
        except Exception:
            pass

        try:
            if await dlg.is_visible():
                await page.keyboard.press("Escape")
        except Exception:
            pass

        return True

    async def repair_ui_field_errors(self, page: Page) -> List[Dict[str, Any]]:
        fixed = []
        try:
            rows = await page.evaluate("""() => {
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
                            if (/valid url|invalid|required|please enter|please select/i.test(t)) { err = t; break; }
                        }
                    }
                    const native = !!(el.validity && !el.validity.valid);
                    if (!err && !native) continue;
                    out.push({
                        id: el.id || '', name: el.name || '', type,
                        value: el.value || '',
                        err: err || el.validationMessage || '',
                        label: labelOf(el),
                    });
                }
                return out;
            }""") or []
        except Exception:
            rows = []

        for row in rows:
            err = (row.get("err") or "").lower()
            label = (row.get("label") or "").lower()
            val = row.get("value") or ""
            sel = ("#" + row["id"]) if row.get("id") else (f"[name=\"{row['name']}\"]" if row.get("name") else "")
            if not sel:
                continue
            loc = page.locator(sel).first
            try:
                if not await loc.count():
                    continue
                if "url" in err or "linkedin" in label or "linkedin" in err:
                    nxt = self.ensure_https_url(val)
                    if nxt and nxt != val:
                        await self.fill_text(loc, nxt)
                        fixed.append({"label": label or sel, "fix": nxt, "err": row.get("err")})
                    continue
                if any(w in label or w in err for w in ("date", "dob", "from", "to", "birth")):
                    parsed = self.parse_date_value(val)
                    if parsed:
                        await self.pick_opened_calendar(page, loc, parsed)
                        fixed.append({"label": label or sel, "fix": "calendar", "err": row.get("err")})
                    continue
                await self.repair_invalid_text_input(loc, val)
                fixed.append({"label": label or sel, "fix": "constraint", "err": row.get("err")})
            except Exception:
                continue
        return fixed

    # ============================================================
    # AUTOCOMPLETE SUGGESTION DISCOVERY & RESUME MATCHING
    # ============================================================

    def _strip_accents(self, text: str) -> str:
        if not text:
            return ""
        import unicodedata
        return "".join(c for c in unicodedata.normalize('NFKD', text) if not unicodedata.combining(c))

    def score_location_suggestion(self,sugg_text: str, query: str, resume_idx: Optional[Dict[str, Any]] = None) -> float:
        
        s_clean = self.clean_text(sugg_text)
        if not s_clean or "\n" in s_clean:
            return 0.0
        s_ascii = self._strip_accents(s_clean).lower()
        q_ascii = self._strip_accents(self.clean_text(query)).lower()

        idx = resume_idx or _CURRENT_RESUME_INDEX or {}
        city = self._strip_accents(self.clean_text(idx.get("city") or "")).lower()
        state = self._strip_accents(self.clean_text(idx.get("state") or "")).lower()
        country = self._strip_accents(self.clean_text(idx.get("country") or "")).lower()
        loc = self._strip_accents(self.clean_text(idx.get("location") or idx.get("current_location") or "")).lower()
        all_locs = [self._strip_accents(self.clean_text(x)).lower() for x in (idx.get("all_locations") or []) if self.clean_text(x)]

        score = SequenceMatcher(None, q_ascii, s_ascii).ratio() * 40.0

        # Prefix match bonus (direct starting match on city name)
        if q_ascii and s_ascii.startswith(q_ascii):
            score += 25.0
        elif city and s_ascii.startswith(city):
            score += 25.0

        # Direct query string match
        if q_ascii and (q_ascii in s_ascii or s_ascii in q_ascii):
            score += 40.0
        if city and (city in s_ascii or s_ascii in city):
            score += 40.0

        # Match any location from work experience or education records
        for al in all_locs:
            if al and (al in s_ascii or s_ascii in al):
                score += 30.0
                break

        # Common city synonyms / alternate spellings (e.g. Gurgaon vs Gurugram, Bangalore vs Bengaluru)
        aliases = {
            "gurgaon": "gurugram", "gurugram": "gurgaon",
            "bangalore": "bengaluru", "bengaluru": "bangalore",
            "bombay": "mumbai", "mumbai": "bombay",
            "calcutta": "kolkata", "kolkata": "calcutta",
            "madras": "chennai", "chennai": "madras",
            "baroda": "vadodara", "vadodara": "baroda",
            "poona": "pune", "pune": "poona",
            "trivandrum": "thiruvananthapuram", "thiruvananthapuram": "trivandrum",
            "cochin": "kochi", "kochi": "cochin",
        }
        for a1, a2 in aliases.items():
            if (a1 in q_ascii or (city and a1 in city)) and a2 in s_ascii:
                score += 40.0
            if (a2 in q_ascii or (city and a2 in city)) and a1 in s_ascii:
                score += 40.0

        # State matching & mismatch penalty
        if state:
            if state in s_ascii:
                score += 50.0
            elif any(w in s_ascii for w in state.split() if len(w) >= 4):
                score += 30.0

            # PENALTY: If suggestion explicitly names a DIFFERENT known state (e.g. candidate is in Haryana, suggestion is in Bihar)
            for other_state in INDIAN_STATES:
                if other_state != state and other_state in s_ascii:
                    score -= 70.0

        # Country matching (India, IND, IN, USA, United States, UK)
        if country:
            if country in s_ascii:
                score += 25.0
            if country == "india" and any(token in s_ascii.replace(",", " ").split() for token in ["ind", "in"]):
                score += 25.0
            elif country in {"united states", "usa"} and any(token in s_ascii.replace(",", " ").split() for token in ["usa", "us"]):
                score += 25.0
            elif country in {"united kingdom", "uk"} and any(token in s_ascii.replace(",", " ").split() for token in ["uk", "gbr"]):
                score += 25.0

        # Full location overlap
        if loc and (loc in s_ascii or s_ascii in loc):
            score += 20.0

        return score

    async def select_best_autocomplete_suggestion(
        self,
        target_scope: Any,
        loc: Locator,
        answer_str: str,
        resume_idx: Optional[Dict[str, Any]] = None,
    ) -> Tuple[bool, str]:
        """
        Looks for autocomplete suggestion options in target_scope.
        If suggestions appear:
        Scores each visible suggestion against the candidate's resume location
        (city, state, country, aliases, all_locations) and clicks the closest matching option.
        If no suggestions appear (forms where location is entered directly without dropdown):
        Returns (False, "") cleanly so the typed text remains as-is without error or clearing.
        """
        page = target_scope.page if hasattr(target_scope, "page") else target_scope

        try:
            await loc.focus()
        except Exception:
            pass

        best_item = None
        best_score = -1.0
        best_text = ""
        best_opt_idx = 0
        seen_texts = set()

        start_time = time.monotonic()
        deadline = start_time + 2.5
        nudged = False

        while time.monotonic() < deadline:
            valid_count = 0
            for sel in AUTOCOMPLETE_SUGGESTION_SELECTORS:
                try:
                    items = target_scope.locator(sel)
                    cnt = await items.count()
                    if not cnt:
                        continue
                    for i in range(min(cnt, 15)):
                        item = items.nth(i)
                        try:
                            tag = await item.evaluate("el => el.tagName.toLowerCase()")
                            if tag in {"input", "textarea"}:
                                continue
                            if not await item.is_visible():
                                continue
                            text = self.clean_text(await item.inner_text(timeout=800))
                        except Exception:
                            continue
                        # Must be a single option, not an entire container with concatenated lines
                        if not text or "\n" in text or len(text) > 120 or text in seen_texts:
                            continue
                        t_low = text.lower()
                        if any(p in t_low for p in ["no location found", "loading", "searching", "try entering a different"]):
                            continue
                        # Ensure element is not a parent container wrapping other suggestion items
                        try:
                            has_sub_options = await item.evaluate("""el => {
                                return el.children.length > 0 && Array.from(el.children).some(c => 
                                    c.classList.contains('dropdown-location') || 
                                    c.getAttribute('role') === 'option' ||
                                    c.id.startsWith('location-') ||
                                    c.tagName === 'LI'
                                );
                            }""")
                            if has_sub_options:
                                continue
                        except Exception:
                            pass

                        seen_texts.add(text)
                        valid_count += 1
                        sc = self.score_location_suggestion(text, answer_str, resume_idx)
                        if sc > best_score:
                            best_score = sc
                            best_item = item
                            best_text = text
                            best_opt_idx = valid_count - 1
                except Exception:
                    continue

            if best_item is not None:
                break

            # If after 500ms nothing appeared, simulate keyboard typing on the last character
            if not nudged and (time.monotonic() - start_time > 0.5) and answer_str:
                nudged = True
                try:
                    await loc.focus()
                    last_char = answer_str[-1]
                    await loc.press("Backspace")
                    await page.wait_for_timeout(100)
                    await loc.type(last_char, delay=60)
                except Exception:
                    try:
                        await loc.press("Space")
                        await loc.press("Backspace")
                    except Exception:
                        pass

            await asyncio.sleep(0.15)

        if best_item is not None and best_score >= 30.0:
            try:
                try:
                    await best_item.scroll_into_view_if_needed(timeout=1000)
                except Exception:
                    pass

                try:
                    await best_item.evaluate("""el => {
                        ['pointerdown', 'mousedown'].forEach(evt => {
                            el.dispatchEvent(new MouseEvent(evt, {bubbles: true, cancelable: true, view: window}));
                        });
                    }""")
                    await page.wait_for_timeout(100)
                except Exception:
                    pass

                # Also dispatch pointerup, mouseup, click
                try:
                    await best_item.evaluate("""el => {
                        ['pointerup', 'mouseup', 'click'].forEach(evt => {
                            el.dispatchEvent(new MouseEvent(evt, {bubbles: true, cancelable: true, view: window}));
                        });
                    }""")
                except Exception:
                    pass

                # Playwright native click as complementary interaction
                try:
                    await best_item.click(timeout=1000, force=True)
                    await page.wait_for_timeout(150)
                except Exception:
                    pass

                actual = await self.read_control_value(loc)
                hidden_val = ""
                try:
                    hidden_val = await page.evaluate("() => document.querySelector('#selected-location, [name*=\"location\" i][type=\"hidden\"]')?.value || ''")
                except Exception:
                    hidden_val = ""

                # Keyboard navigation fallback (ArrowDown + Enter) only if neither actual nor hidden updated
                if not hidden_val and (not actual or actual.lower() != best_text.lower()):
                    try:
                        await loc.focus()
                        for _ in range(max(1, best_opt_idx + 1)):
                            await loc.press("ArrowDown")
                            await page.wait_for_timeout(50)
                        await loc.press("Enter")
                        await page.wait_for_timeout(200)
                    except Exception:
                        pass

                # Ensure any dangling typeahead/dropdown container is closed so subsequent blurs don't wipe it
                try:
                    await page.evaluate("""() => {
                        const dc = document.querySelector('.dropdown-container');
                        if (dc && getComputedStyle(dc).display !== 'none') {
                            dc.style.display = 'none';
                        }
                    }""")
                except Exception:
                    pass

                print(f"[fill_answer] Selected closest location suggestion '{best_text}' (score: {best_score:.1f})", file=sys.stderr)
                return True, best_text
            except Exception as exc:
                print(f"[fill_answer] Click error on suggestion: {exc}", file=sys.stderr)
                try:
                    await loc.press("ArrowDown")
                    await loc.press("Enter")
                    await page.wait_for_timeout(300)
                    return True, best_text
                except Exception:
                    pass

        if seen_texts:
            print(f"[fill_answer] Autocomplete suggestions appeared but none matched candidate location sufficiently; retaining typed '{answer_str}'", file=sys.stderr)
        else:
            print(f"[fill_answer] No autocomplete suggestions displayed; retaining typed '{answer_str}'", file=sys.stderr)

        return False, ""


    async def fill_answer(self, page: Page, element: Dict[str, Any], answer_item: Dict[str, Any]) -> Dict[str, Any]:
        
        idx = element["_element_index"]
        answer = self.clean_text(answer_item.get("answer")) or self.clean_text(answer_item.get("option_label")) or self.clean_text(answer_item.get("option_value"))
        action = self.norm(answer_item.get("action") or "fill")
        if action == "auto":
            action = self.control_kind(element)
        elif action == "fill" and self.control_kind(element) in {"select", "radio", "checkbox", "upload"}:
            action = self.control_kind(element)
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

        if self.is_captcha(element) or self.is_submit(element):
            result["method"] = "blocked_control"
            result["error"] = "captcha_or_submit_not_allowed"
            return result

        if self.is_phone_element(element) and not self.is_phone_country_element(element):
            if answer:
                d = self.digits(answer)
                ans_clean = str(answer).strip()
                if ans_clean.startswith("+"):
                    m_dial = re.match(r"^\+(\d{1,3})(.*)$", ans_clean)
                    if m_dial and self.digits(m_dial.group(2)):
                        answer = self.digits(m_dial.group(2))
                elif len(d) == 10:
                    answer = d

        loc, frame, strategy = await self.locator_from_element(page, element)
        if loc:
            try:
                await loc.scroll_into_view_if_needed(timeout=LOCATOR_TIMEOUT_MS)
            except Exception:
                pass
        result["locator_strategy"] = strategy
        if not loc:
            result["error"] = "element_not_found"
            return result

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

        live_tag = self.norm(live_meta.get("tag"))
        live_type = self.norm(live_meta.get("type"))
        live_role = self.norm(live_meta.get("role"))
        live_popup = self.norm(live_meta.get("ariaHaspopup"))
        live_class = self.norm(live_meta.get("className"))

        if action == "skip":
            kind = self.control_kind(element)
            required = bool(
                element.get("required") is True
                or "*" in self.clean_text(element.get("question") or element.get("label"))
            )
            if required and kind in {"select", "radio"}:
                action = kind
                if not self.clean_text(answer) or self.is_skip_answer(answer):
                    answer = "Unknown"
                result["action"] = action
                result["answer"] = answer
            else:
                result["method"] = "skipped"
                result["verified"] = True
                return result

        is_tel_input = live_type == "tel" or "iti__tel" in live_class or (
            self.is_phone_element(element) and not self.is_phone_country_element(element)
        )

        if self.is_skill_tag_input(element):
            action = "skills_tag"

        if action == "fill":
            if live_type == "checkbox" or live_role == "checkbox":
                action = "checkbox"
            elif live_type == "radio" or live_role == "radio":
                action = "radio"
            elif not is_tel_input and not self.is_skill_tag_input(element) and not self.is_date_element(element) and not element.get("is_datepicker") and (
                live_tag == "select"
                or live_role in {"combobox", "listbox"}
                or live_popup in {"listbox", "menu", "true"}
                or (live_role == "button" and (
                    live_popup or element.get("options")
                    or "cu-select" in (self.norm(element.get("selector")) + " " + self.norm(element.get("dom_path")))
                ))
                or "select__input" in live_class
                or element.get("options")
                or self.norm(element.get("control_type")) in {"select", "dropdown", "combobox"}
                or self.norm(element.get("runtime_role")) in {"combobox", "listbox"}
                or "select__input" in self.norm(await loc.get_attribute("class"))
            ):
                action = "select"

        if is_tel_input:
            cc = self.clean_text(answer_item.get("country_code"))
            if not cc and str(answer_item.get("answer") or "").strip().startswith("+"):
                m = re.match(r"^\+(\d{1,3})", str(answer_item.get("answer")))
                if m:
                    cc = "+" + m.group(1)
            national = self.digits(answer)
            if len(national) >= 10:
                national = national[-10:]
            ok, actual = await self.fill_phone_widget(loc, national, cc)
            result["method"] = "phone_widget"
            result["answer"] = national
            result["country_code"] = cc
            result["actual_value"] = actual
            result["verified"] = ok
            return result

        if (self.is_phone_country_element(element) or ("iti__selected" in live_class)) and not is_tel_input:
            result["method"] = "phone_country"
            result["verified"] = False
            try:
                current = await self.read_selected_control_text(loc)
            except Exception:
                current = ""
            wanted_digits = self.digits(answer)
            if wanted_digits and wanted_digits in self.digits(current):
                result["verified"] = True
                result["actual_value"] = current
                return result
            verified, method, state = await self.select_intl_tel_country(loc, answer)
            if not verified:
                try:
                    phone_loc = frame.locator("#phone, input[type='tel']").first
                    verified, method, state = await self.select_intl_tel_country(phone_loc, answer)
                except Exception:
                    pass
            result["method"] = method
            result["verified"] = bool(verified) and bool(state) and wanted_digits in self.digits(state)
            result["actual_value"] = state
            result["answer"] = answer
            try:
                await page.keyboard.press("Escape")
            except Exception:
                pass
            return result

        if action == "skills_tag" or self.is_skill_tag_input(element):
            res_skills = await self.fill_skills_tag(page, frame or page, loc, answer, element)
            result.update(res_skills)
            return result

        if action == "fill" and (self.is_date_element(element) or element.get("is_datepicker")) and answer:
            raw_date = self.clean_text(answer)
            parsed = self.parse_date_value(raw_date)
            try:
                live_type = self.norm(await loc.get_attribute("type"))
            except Exception:
                live_type = ""
            try:
                live_tag = await loc.evaluate("el => el.tagName.toLowerCase()")
            except Exception:
                live_tag = ""

            formatted_date = str(answer)
            if parsed and live_type == "date":
                formatted_date = parsed.strftime("%Y-%m-%d")
            elif parsed:
                placeholder = self.clean_text(await loc.get_attribute("placeholder"))
                fmt_hint = self.norm(placeholder)
                lbl_hint = self.canonical_text(element.get("label") or element.get("question") or "")
                id_hint = self.norm(element.get("id") or "")
                if "(month)" in fmt_hint or "(month)" in lbl_hint or "startyear" in id_hint or "endyear" in id_hint or "exp_start" in id_hint or "exp_end" in id_hint:
                    formatted_date = parsed.strftime("%m/%Y")
                elif "dd/mm/yyyy" in fmt_hint or "dd-mm-yyyy" in fmt_hint:
                    sep = "/" if "/" in placeholder else "-"
                    formatted_date = parsed.strftime(f"%d{sep}%m{sep}%Y")
                elif "mm/dd/yyyy" in fmt_hint or "mm-dd-yyyy" in fmt_hint:
                    sep = "/" if "/" in placeholder else "-"
                    formatted_date = parsed.strftime(f"%m{sep}%d{sep}%Y")
                elif any(m in fmt_hint for m in ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]):
                    formatted_date = parsed.strftime("%d %B %Y")
                else:
                    formatted_date = parsed.strftime("%m/%Y") if ("(month)" in lbl_hint or "year" in id_hint) else parsed.strftime("%d/%m/%Y")

            answer = formatted_date

            # If trigger is a button datepicker (or non-input), use calendar picker directly
            if live_tag not in {"input", "textarea"}:
                picked = await self.pick_opened_calendar(page, loc, parsed or raw_date)
                actual_val = await self.read_control_value(loc)
                result["method"] = "date_calendar"
                result["actual_value"] = actual_val
                result["answer"] = formatted_date
                result["verified"] = (
                    bool(actual_val and "select" not in actual_val.lower())
                    and (
                        self.is_date_match(raw_date, actual_val)
                        or (parsed and self.is_date_match(parsed, actual_val))
                        or (raw_date.lower() in {"present", "current"} and str(datetime.now().year) in actual_val)
                        or picked
                    )
                )
                return result

            try:
                is_readonly = await loc.get_attribute("readonly")
                if is_readonly is not None:
                    await loc.evaluate("el => el.removeAttribute('readonly')")
            except Exception:
                pass

            iso_val = parsed.strftime("%Y-%m-%d") if parsed else formatted_date
            kendo_set = False
            try:
                res_kendo = await loc.evaluate("""(el, {fmt, iso}) => {
                    el.removeAttribute('readonly');
                    let kendo = window.jQuery ? (window.jQuery(el).data("kendoDatePicker") || window.jQuery(el).data("kendoDateTimePicker")) : null;
                    if (kendo) {
                        let d = new Date(iso);
                        if (!isNaN(d.getTime())) {
                            if (kendo.max && d > kendo.max()) {
                                kendo.max(new Date(d.getFullYear() + 10, 11, 31));
                            }
                            kendo.value(d);
                            kendo.trigger("change");
                            return {ok: true, val: el.value || fmt};
                        }
                    }
                    let isDateType = (el.type === 'date' || el.getAttribute('type') === 'date');
                    el.value = isDateType ? iso : fmt;
                    el.dispatchEvent(new Event('input', {bubbles: true}));
                    el.dispatchEvent(new Event('change', {bubbles: true}));
                    el.dispatchEvent(new Event('blur', {bubbles: true}));
                    return {ok: true, val: el.value};
                }""", {"fmt": formatted_date, "iso": iso_val})
                if res_kendo and res_kendo.get("ok"):
                    kendo_set = True
            except Exception:
                pass

            target_date_val = iso_val if self.norm(element.get("type")) == "date" else (formatted_date or str(answer))
            date_ok = await self.fill_text(loc, target_date_val) if not kendo_set else True
            if not date_ok and not kendo_set:
                try:
                    await loc.click(timeout=LOCATOR_TIMEOUT_MS)
                    await loc.evaluate("el => el.value = ''")
                    await loc.type(target_date_val, delay=0)
                    await loc.press("Enter")
                    date_ok = True
                except Exception:
                    pass

            await self.fire_react_events(loc)
            actual_val = await self.read_control_value(loc)
            result["method"] = "date_fill"
            result["verified"] = self.is_date_match(raw_date, actual_val) or self.is_date_match(formatted_date, actual_val) or self.same_value(formatted_date, actual_val)
            result["actual_value"] = actual_val
            result["answer"] = formatted_date
            if result["verified"]:
                return result

            if parsed:
                try:
                    picked = await self.pick_opened_calendar(page, loc, parsed or raw_date)
                    if picked:
                        live = await self.read_control_value(loc)
                        if self.parse_date_value(live) == parsed or self.is_date_match(raw_date, live) or (raw_date.lower() in {"present", "current"} and str(datetime.now().year) in live):
                            result["method"] = "date_calendar"
                            result["verified"] = True
                            result["answer"] = answer
                            result["actual_value"] = live
                            return result
                except Exception:
                    pass
                try:
                    await loc.evaluate("""(el, y, m, d) => {
                        const scope = (el.closest('.field, .form-group, [class*="date"]') || el.parentElement || document.body);
                        const mn = ['jan','feb','mar','apr','may','jun','jul','aug','sep','oct','nov','dec'];
                        for (const s of scope.querySelectorAll('select')) {
                            const opts = [...s.options];
                            const yh = opts.find(o => (o.textContent||'').includes(String(y)) || o.value === String(y));
                            if (yh) { s.value = yh.value; s.dispatchEvent(new Event('change',{bubbles:true})); continue; }
                            const mh = opts.find(o => {
                                const t = (o.textContent || o.value || '').toLowerCase();
                                return t.includes(mn[m-1]) || o.value === String(m) || o.value === String(m).padStart(2,'0');
                            });
                            if (mh && opts.length <= 13) { s.value = mh.value; s.dispatchEvent(new Event('change',{bubbles:true})); }
                        }
                        const day = String(d);
                        const hit = [...scope.querySelectorAll('button, td, [role="option"], [role="gridcell"]')]
                            .find(n => (n.textContent || '').trim() === day);
                        if (hit) hit.click();
                        const isDateType = (el.type === 'date' || el.getAttribute('type') === 'date');
                        const shown = isDateType ? `${y}-${String(m).padStart(2,'0')}-${String(d).padStart(2,'0')}` : `${String(d).padStart(2,'0')}/${String(m).padStart(2,'0')}/${y}`;
                        if (el.tagName === 'INPUT' && proto && proto.set) proto.set.call(el, shown);
                        el.dispatchEvent(new Event('input',{bubbles:true}));
                        el.dispatchEvent(new Event('change',{bubbles:true}));
                    }""", parsed.year, parsed.month, parsed.day)
                    live = await self.read_control_value(loc)
                    if self.parse_date_value(live) == parsed or self.is_date_match(raw_date, live):
                        result["method"] = "date_picker_parts"
                        result["verified"] = True
                        result["answer"] = answer
                        result["actual_value"] = live
                        return result
                except Exception:
                    pass

            result["answer"] = answer
            result["verified"] = False
            result["error"] = "date_fill_not_verified"
            return result

        if action in {"click", "press"}:
            try:
                before_state = await loc.evaluate("""el => ({
                    pressed: el.getAttribute('aria-pressed'),
                    expanded: el.getAttribute('aria-expanded'),
                    checked: el.getAttribute('aria-checked'),
                    selected: el.getAttribute('aria-selected'),
                    text: (el.innerText || el.textContent || '').trim().slice(0, 200),
                    cls: String(el.className || '')
                })""")
            except Exception:
                before_state = {}

            try:
                before_overlays = await page.locator(
                    '[role="dialog"]:visible, [role="menu"]:visible, '
                    '[role="listbox"]:visible, [role="tooltip"]:visible, '
                    '.modal:visible, .dropdown-menu:visible, .popover:visible'
                ).count()
            except Exception:
                before_overlays = 0

            try:
                await loc.scroll_into_view_if_needed()
                await loc.click(force=True, timeout=LOCATOR_TIMEOUT_MS)
                result["method"] = "click"
            except Exception as exc:
                result["method"] = "click_failed"
                result["error"] = str(exc)
                return result

            try:
                await page.wait_for_timeout(250)
                after_state = await loc.evaluate("""el => ({
                    pressed: el.getAttribute('aria-pressed'),
                    expanded: el.getAttribute('aria-expanded'),
                    checked: el.getAttribute('aria-checked'),
                    selected: el.getAttribute('aria-selected'),
                    text: (el.innerText || el.textContent || '').trim().slice(0, 200),
                    cls: String(el.className || '')
                })""")
            except Exception:
                after_state = {}

            changed = any(
                before_state.get(k) != after_state.get(k)
                for k in ("pressed", "expanded", "checked", "selected", "text", "cls")
            )

            try:
                after_overlays = await page.locator(
                    '[role="dialog"]:visible, [role="menu"]:visible, '
                    '[role="listbox"]:visible, [role="tooltip"]:visible, '
                    '.modal:visible, .dropdown-menu:visible, .popover:visible'
                ).count()
                if after_overlays > before_overlays:
                    changed = True
            except Exception:
                pass

            result["verified"] = bool(changed)
            if not changed:
                result["error"] = "click_no_state_change"
            return result

        if action == "upload" or self.is_file_upload(element) or self.looks_like_resume_upload(element):
            result["action"] = "upload"
            result["verified"] = False

            ok, method = await self.upload_file(loc, RESUME_PATH, frame=frame, page=page)
            result["method"] = method
            result["verified"] = ok
            expected_name = Path(RESUME_PATH).name if RESUME_PATH else ""

            file_loc = await self.resolve_file_input_locator(loc, frame=frame, page=page)
            if file_loc and RESUME_PATH:
                try:
                    names = await file_loc.evaluate("el => Array.from(el.files || []).map(f => f.name)") or []
                    result["actual_value"] = names
                    if expected_name in names:
                        result["verified"] = True
                except Exception:
                    pass

            if not result["verified"] and expected_name and page:
                try:
                    has_ui_indicator = await page.evaluate("""name => {
                        const els = Array.from(document.querySelectorAll('.resumeFileName, #changeResumeFile, [class*="resume"], [id*="resume"]'));
                        return els.some(el => (el.textContent || '').includes(name) || (el.offsetParent !== null && el.id === 'changeResumeFile'));
                    }""", expected_name)
                    if has_ui_indicator:
                        result["verified"] = True
                        result["actual_value"] = [expected_name]
                except Exception:
                    pass

            if not result["verified"] and not ok:
                result["error"] = "no_file_input_found"
            return result

        if action == "checkbox":
            ok, method = await self.set_checkbox_for_element(page, element, answer)
            result["method"] = method
            result["verified"] = False
            if ok:
                try:
                    result["verified"] = await self.verify_checkbox(loc, answer)
                except Exception:
                    result["verified"] = False
            return result

        if action == "radio":
            ok, method = await self.click_radio(page, element, answer, option_label, option_value)
            result["method"] = method
            result["verified"] = False
            if ok:
                wanted = option_label or option_value or answer
                try:
                    result["verified"] = await self.is_choice_selected(page, element, wanted)
                except Exception:
                    result["verified"] = False
            return result

        element_type = self.norm(element.get("type"))
        tag = self.norm(element.get("tag"))
        role = self.norm(element.get("role"))
        control_type = self.norm(element.get("control_type"))

        is_select_control = not self.is_date_element(element) and not element.get("is_datepicker") and not is_tel_input and (
            action == "select"
            or tag == "select"
            or element_type in {"select", "combobox"}
            or role in {"combobox", "listbox"}
            or "combobox" in control_type
            or "dropdown" in control_type
            or "cu-select" in (self.norm(element.get("selector")) + " " + self.norm(element.get("dom_path")))
            or (bool(element.get("options")) and not self.is_phone_element(element))
            or (role == "button" and (
                bool(element.get("options"))
                or self.norm(element.get("haspopup")) in {"listbox", "menu", "list", "true"}
            ))
        )

        if is_select_control:
            resolved = self.resolve_option_from_element(element, answer, option_label, option_value)
            if resolved:
                option_label = self.clean_text(resolved.get("label") or resolved.get("text")) or option_label
                option_value = self.clean_text(resolved.get("value")) or option_value
                result["matched_option"] = {"label": option_label, "value": option_value}
            resume_blob = ""
            try:
                resume_blob = json.dumps(self.load_json(RESUME_FILE), ensure_ascii=False)[:2500]
            except Exception:
                resume_blob = ""
            ok, method = await self.select_native_or_custom(
                loc, answer, option_label, option_value,
                frame=frame,
                stored_options=self.usable_options(element),
                specify_text=" ".join(
                    x for x in [
                        self.clean_text(answer_item.get("specify_value")),
                        self.clean_text(answer),
                        resume_blob,
                    ] if x
                ),
            )
            result["method"] = method

            if ok:
                target = option_label or option_value or answer
                verified, actual = await self.verify_select_control(
                    loc, target, option_label, option_value,
                    question=self.clean_text(element.get("question") or element.get("label")),
                )
                result["actual_value"] = actual
                result["verified"] = verified
            return result

        hay = self.canonical_text(" ".join(str(element.get(k) or "") for k in ("id", "name", "label", "question", "placeholder", "selector")))
        fk = self.norm(element.get("field_key") or "")
        is_loc_field = (
            any(w in hay for w in ["location", "city", "address", "state", "place"])
            or fk in {"current_location", "location", "city", "work_location", "desired_location", "preferred_location", "base_location"}
        )
        is_autocomplete = (
            is_loc_field
            or self.norm(element.get("role") or element.get("runtime_role")) == "combobox"
            or self.norm(element.get("aria_autocomplete")) in {"list", "both", "inline"}
            or bool(element.get("haspopup"))
        )

        expected_str = "" if answer is None else str(answer)

        if is_autocomplete:
            try:
                await loc.scroll_into_view_if_needed()
                await loc.focus()
                await loc.click(timeout=LOCATOR_TIMEOUT_MS)
                await loc.fill("")
                await loc.type(expected_str, delay=40)
                ok = True
            except Exception:
                ok = await self.fill_text(loc, expected_str)
        else:
            ok = await self.fill_text(loc, expected_str)

        result["method"] = "fill" if ok else "fill_failed"

        # Also sync Kendo NumericTextBox widget if present
        try:
            await loc.evaluate("""(el, val) => {
                let num = parseFloat(String(val).replace(/,/g, ''));
                if (isNaN(num)) return;
                let jq = window.jQuery;
                if (!jq) return;
                let k = jq(el).data("kendoNumericTextBox") 
                    || jq(el.closest(".k-numeric-wrap")?.parentElement?.querySelector("input[data-role='numerictextbox']")).data("kendoNumericTextBox")
                    || jq(el.closest(".k-numerictextbox")?.querySelector("input[data-role='numerictextbox']")).data("kendoNumericTextBox");
                if (k) {
                    k.value(num);
                    k.trigger("change");
                }
            }""", answer)
        except Exception:
            pass

        if ok:
            clicked_suggestion = False

            if is_autocomplete:
                target_scope = frame or page
                clicked_suggestion, best_matched = await self.select_best_autocomplete_suggestion(
                    target_scope, loc, expected_str, resume_idx=_CURRENT_RESUME_INDEX
                )
                if clicked_suggestion:
                    result["actual_value"] = best_matched
                    result["verified"] = True
                    result["method"] = "autocomplete_selected"
            else:
                try:
                    await loc.press("Tab")
                except Exception:
                    pass

            if not clicked_suggestion:
                await self.fire_react_events(loc)
                actual_val = await self.read_control_value(loc)
                result["actual_value"] = actual_val or result.get("actual_value") or ""

                if is_loc_field:
                    sc = self.score_location_suggestion(actual_val, expected_str, _CURRENT_RESUME_INDEX)
                    result["verified"] = (
                        await self.verify_text(loc, expected_str)
                        or self.same_value(expected_str, actual_val)
                        or (bool(actual_val) and expected_str.lower() in actual_val.lower())
                        or (bool(actual_val) and sc >= 30.0)
                    )
                else:
                    result["verified"] = await self.verify_text(loc, expected_str)

            if not result["verified"] and self.is_phone_element(element):
                actual = await self.read_control_value(loc)
                result["verified"] = (
                    self.digits(answer) == self.digits(actual)
                    or self.digits(answer).endswith(self.digits(actual))
                    or self.digits(actual).endswith(self.digits(answer))
                )

        return result


    # ============================================================
    # LOCAL RETRY WITHOUT ANOTHER AI CALL
    # ============================================================

    async def retry_failed_answer(self, page: Page, element: Dict[str, Any], answer_item: Dict[str, Any]) -> Dict[str, Any]:
        expected = answer_item.get("option_label") or answer_item.get("answer")
        kind = self.control_kind(element)
        action = self.norm(answer_item.get("action") or self.infer_action_for_element(element))

        async def live_value() -> str:
            try:
                loc, _, _ = await self.locator_from_element(page, element)
                if not loc:
                    return ""
                return await self.read_select_display(loc) or await self.read_control_value(loc)
            except Exception:
                return ""

        existing = await live_value()
        already = False
        if kind in {"radio", "checkbox"} or action in {"radio", "checkbox"}:
            already = await self.is_choice_selected(page, element, expected)
        elif kind == "select":
            if existing and not self.is_placeholder_option_label(existing, self.clean_text(element.get("question") or element.get("label"))):
                already = self.display_matches(expected, existing, element) or self.same_value(expected, existing)
        elif kind == "upload":
            try:
                loc, frame, _ = await self.locator_from_element(page, element)
                file_loc = await self.resolve_file_input_locator(loc, frame=frame, page=page) if loc else None
                if file_loc and RESUME_PATH:
                    names = await file_loc.evaluate("el => Array.from(el.files || []).map(f => f.name)") or []
                    already = Path(RESUME_PATH).name in names
            except Exception:
                already = False
        elif self.display_matches(expected, existing, element):
            already = True

        if already:
            return {
                "element_index": element.get("_element_index"),
                "question": element.get("question"),
                "answer": answer_item.get("answer"),
                "action": answer_item.get("action"),
                "verified": True,
                "method": "already_filled",
                "actual_value": existing,
            }

        try:
            first = await asyncio.wait_for(
                self.fill_answer(page, element, answer_item),
                timeout=FIELD_FILL_TIMEOUT_S,
            )
        except asyncio.TimeoutError:
            actual = await live_value()
            matched = self.display_matches(expected, actual, element)
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
        if kind in {"fill"} or action == "fill":
            try:
                loc, _, _ = await self.locator_from_element(page, element)
                if loc:
                    ok, live = await self.repair_invalid_text_input(loc, str(expected or actual or ""))
                    first["actual_value"] = live or actual
                    first["verified"] = bool(ok)
                    first["method"] = (first.get("method") or "fill") + "+constraint_repair"
                    return first
            except Exception:
                pass

        if not self.is_blank_display(actual):
            first["actual_value"] = actual
            first["verified"] = self.display_matches(expected, actual, element)
            first["method"] = (first.get("method") or "fill") + "+ rechecked"
        return first

    def normalize_ai_answers(self, raw: Any) -> Dict[str, Any]:
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

    def _question_key(self, e: Dict[str, Any]) -> str:
        ident = self.clean_text(e.get("id") or e.get("name") or "")
        ident = re.sub(r"-input$", "", ident, flags=re.I)
        ident = re.sub(r"\[\]$", "", ident)
        if ident and "nth-of-type" not in ident:
            return "id:" + ident
        q = self.canonical_text(e.get("question") or e.get("label") or e.get("placeholder"))
        q = re.sub(r"\*$", "", q).strip()
        return q

    def dedupe_interactive_elements(self, elements: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        
        grouped: Dict[str, Tuple[int, Dict[str, Any]]] = {}
        priority = {"select": 120, "radio": 110, "checkbox": 110, "fill": 80, "upload": 60, "click": 10}

        for e in elements:
            if self.is_captcha(e) or self.is_submit(e) or e.get("disabled") is True:
                continue
            if self.is_noise_control(e):
                continue
            if self.norm(e.get("type")) == "hidden":
                continue

            qkey = self._question_key(e)
            if not qkey:
                continue

            kind = self.control_kind(e)
            if kind == "click" and self.clean_text(e.get("label")) == "Toggle flyout":
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
            if kind == "select" and (self.norm(e.get("role")) == "combobox" or self.norm(e.get("runtime_role")) == "combobox"):
                score += 15
            if kind == "upload" and RESUME_PATH:
                score += 10

            old = grouped.get(qkey)
            if old is None or score > old[0]:
                grouped[qkey] = (score, e)

        return [x[1] for x in sorted(grouped.values(), key=lambda x: x[1].get("_element_index", 0))]

    def _missing_answer_candidates(self, elements: List[Dict[str, Any]], mapped_indexes: set) -> List[Dict[str, Any]]:
        grouped: Dict[str, Dict[str, Any]] = {}
        priority = {"select": 6, "radio": 6, "checkbox": 6, "fill": 5, "upload": 2, "click": 1}
        for e in elements:
            idx = e.get("_element_index")
            if idx in mapped_indexes or self.is_captcha(e) or self.is_submit(e) or e.get("disabled") is True:
                continue
            if self.is_noise_control(e):
                continue
            if self.norm(e.get("type")) == "hidden":
                continue
            ident = " ".join(self.norm(e.get(k)) for k in ("id", "name", "aria_label", "label", "question", "selector", "field_key"))
            if any(term in ident for term in ["consent", "privacy", "terms", "agree", "policy", "declaration", "acknowledg", "candidateconsent", "dataprocessing", "data processing"]):
                continue
            kind = self.control_kind(e)
            if kind == "upload" and not RESUME_PATH:
                continue
            if kind == "click" and not e.get("options"):
                continue
            q = self.clean_text(e.get("question") or e.get("label") or e.get("placeholder"))
            if not q:
                continue
            required = bool(e.get("required") is True or "*" in q or self.norm(e.get("aria_required")) == "true")
            meaningful = len(self.canonical_text(q)) >= 20 and kind in {"select", "radio", "checkbox", "fill"}
            if not (required or meaningful):
                continue
            key = self._question_key(e)
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

    def _format_options_for_prompt(self,e: Dict[str, Any]) -> str:
        options = self.usable_options(e)
        values = []
        for o in options[:30]:
            if isinstance(o, dict):
                label = self.clean_text(o.get("label") or o.get("text") or o.get("value"))
                value = self.clean_text(o.get("value"))
                values.append(f"{label} [{value}]" if value and value != label else label)
            else:
                values.append(self.clean_text(o))
        return " | ".join(x for x in values if x)

    def ask_user_for_missing_fields(self, fields: List[Dict[str, Any]], resume: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        # Use this function to send claude the resume_content + left question + filled_question and format in which answer to get
        if not fields:
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
            q = self.clean_text(e.get("question") or e.get("label") or e.get("placeholder"))
            kind = self.control_kind(e)
            print(f"{n}. {q} [{kind}]")
            option_text = self._format_options_for_prompt(e)
            if option_text:
                print("   Options: " + option_text)
        raw = input("\nAnswers (comma-separated): ").strip()
        if not raw:
            return {"answers": []}
        try:
            values = next(csv.reader([raw], skipinitialspace=True))
        except Exception:
            values = [x.strip() for x in raw.split(",")]
        values = [self.clean_text(x) for x in values]
        values = self.semantic_assign_user_values(fields, values)
        answers = []
        for e, value in zip(fields, values):
            idx = e["_element_index"]
            if not value:
                answers.append({"element_index": idx, "answer": "", "action": "skip", "reason": "User left field blank"})
                continue
            kind = self.control_kind(e)
            item = {"element_index": idx, "answer": value, "action": kind, "reason": "User supplied answer"}
            resolved = self.resolve_option_from_element(e, value)
            if resolved and kind in {"select", "radio"}:
                item["option_label"] = self.clean_text(resolved.get("label") or resolved.get("text"))
                item["option_value"] = self.clean_text(resolved.get("value"))
            if self.looks_like_currency_control(e):
                item["action"] = "select"
            elif "salary" in self.canonical_text(e.get("label") or e.get("question")) and self.control_kind(e) == "fill":
                item["answer"] = self.salary_amount_text(value)
            answers.append(item)
        if resume is not None:
            self.persist_user_answers_to_resume(resume, fields, answers)
        return {"answers": answers}


    def validate_ai_answers(self,ai_data: Any, elements: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        ai_data = self.normalize_ai_answers(ai_data)
        by_index = {e["_element_index"]: e for e in elements}
        best_by_question: Dict[str, Tuple[int, Dict[str, Any]]] = {}

        for item in ai_data.get("answers", []) or []:
            if not isinstance(item, dict):
                continue
            try:
                idx = int(item.get("element_index"))
            except Exception:
                continue

            e = by_index.get(idx)
            if not e or self.is_captcha(e) or self.is_submit(e):
                continue

            fixed = dict(item)
            fixed["element_index"] = idx
            fixed["selector"] = e.get("selector")
            fixed["frame_index"] = e.get("frame_index")
            fixed["frame_url"] = e.get("frame_url")
            fixed["question"] = e.get("question") or e.get("label")

            kind = self.control_kind(e)
            original_action = self.norm(fixed.get("action"))
            answer = self.clean_text(fixed.get("answer")) or self.clean_text(fixed.get("option_label")) or self.clean_text(fixed.get("option_value"))
            if answer:
                fixed["answer"] = answer
            if original_action == "skip" and (not answer or self.is_skip_answer(answer)):
                fixed["action"] = "skip"
            elif kind in {"select", "radio", "checkbox", "upload"}:
                fixed["action"] = kind
            elif original_action in {"", "auto"}:
                fixed["action"] = "fill"

            resolved = self.resolve_option_from_element(
                e, fixed.get("answer"), fixed.get("option_label"), fixed.get("option_value"),
            )
            if resolved and kind in {"select", "radio", "checkbox"}:
                fixed["option_label"] = self.clean_text(resolved.get("label") or resolved.get("text"))
                fixed["option_value"] = self.clean_text(resolved.get("value"))
                if fixed["option_label"]:
                    fixed["answer"] = fixed["option_label"]
                    if original_action == "skip":
                        fixed["action"] = kind
            score = 0
            if kind == "select":
                score += 100
            if e.get("options"):
                score += 30
            if self.norm(e.get("role") or e.get("runtime_role")) in {"combobox", "listbox"}:
                score += 30
            if e.get("id"):
                score += 10
            if e.get("_important"):
                score += 20
            if e.get("visible") is True:
                score += 5
            if self.norm(e.get("type")) in {"text", "search"} and kind != "select":
                score -= 10
            if self.clean_text(fixed.get("answer")) and not self.is_skip_answer(fixed.get("answer")) and self.norm(fixed.get("action")) != "skip":
                score += 80
            if self.norm(fixed.get("action")) == "skip" or not self.clean_text(fixed.get("answer")):
                score -= 1000

            key = self._question_key(e)
            old = best_by_question.get(key)
            if old is None or score > old[0]:
                best_by_question[key] = (score, fixed)
        return [item for _, item in sorted(best_by_question.values(), key=lambda x: x[1].get("element_index", 0))]

    # ============================================================
    # MAIN
    # ============================================================
    async def main(self) -> None:
        
        if not Path(ELEMENTS_FILE).exists():
            print(Path(ELEMENTS_FILE))
            return {"error": "The Dom_extractor_file Not found"}

        if not Path(RESUME_FILE).exists():
            return {"error": "The Resume Json content file Not found"}

        raw_elements = self.load_json(IMPORTANT_FIELDS_FILE)
        resume = self.load_json(RESUME_FILE)
        if Path(ELEMENTS_FILE).exists():
            try:
                full_el = self.load_json(ELEMENTS_FILE)
                full_by_idx = {e.get("_element_index", i): e for i, e in enumerate(full_el)}
                full_by_sel = {e.get("selector"): e for e in full_el if e.get("selector")}
                imp_items = raw_elements.get("fields", []) if isinstance(raw_elements, dict) else raw_elements
                merged_list = []
                for item in imp_items:
                    idx = item.get("element_index")
                    match = full_by_idx.get(idx) or full_by_sel.get(item.get("selector"))
                    merged = dict(match) if match else dict(item)
                    merged.update({k: v for k, v in item.items() if v is not None})
                    merged_list.append(merged)
                raw_elements = merged_list
            except Exception:
                pass
        elements = self.normalize_elements(raw_elements)
        important = self.load_important_fields()
        self.apply_important_field_hints(elements, important)
        inv_url = ""
        if Path("page_inventory.json").exists():
            try:
                inv = self.load_json("page_inventory.json")
                inv_url = self.clean_text(inv.get("page_url") or inv.get("url") or "")
            except Exception:
                pass
        if not inv_url and elements:
            inv_url = self.clean_text(elements[0].get("frame_url") or "")

        job_url = self.url.strip()
        if not job_url:
            prompt_msg = f"Open the job application URL [{inv_url}]: " if inv_url else "Open the job application URL: "
            try:
                user_in = input(prompt_msg).strip()
            except EOFError:
                user_in = ""
            job_url = user_in or inv_url
        if not job_url:
            raise ValueError("Job application URL is required.")
        resume_idx = self.build_resume_index(resume)

        print(f"URL                : {job_url}")
        print(f"Elements            : {len(elements)}")
        print(f"Important fields    : {len(important)} ({IMPORTANT_FIELDS_FILE if important else 'not found'})")
        print(f"Resume file         : {RESUME_FILE}")
        print(f"Bedrock model       : {MODEL_ID}")
        print()

        deterministic = self.deterministic_candidates(elements, resume_idx, important, resume=resume)

        print("=" * 60)
        print("DETERMINISTIC MAPPING")
        print("=" * 60)

        for x in deterministic:
            print(f"{x['field']:30} -> [{x['element_index']}] {x['value']}")
            try:
                await self.page.wait_for_load_state("domcontentloaded", timeout=15000)
            except Exception:
                try:
                    await self.page.goto(job_url, timeout=PAGE_LOAD_TIMEOUT_MS)
                except Exception:
                    pass

            await self.wait_for_form(self.page, resume)
            try:
                await self.page.evaluate("window.scrollTo(0, 0)")
            except Exception:
                pass

            for i, frame in enumerate(self.page.frames):
                print(f"  [{i}] {frame.url}")

            try:
                elements = await self.enrich_cu_select_elements(self.page, elements)
            except Exception as exc:
                print(f"cu-select enrichment failed (non-fatal): {exc}")

            try:
                new_fields = await self.discover_new_fields_after_expansion(self.page, elements)
                if new_fields:
                    print(f"[Runtime] Discovered {len(new_fields)} new required fields after expansion")
                    elements.extend(new_fields)
                    deterministic = self.deterministic_candidates(elements, resume_idx, important, resume=resume)
            except Exception as exc:
                print(f"[Runtime] New-field discovery failed (non-fatal): {exc}")

            by_index = {e["_element_index"]: e for e in elements}

            phone_country_results = await self.set_phone_country_if_needed(self.page, elements, resume_idx, deterministic)
            for r in phone_country_results:
                print(f"phone_country_code-> {r['answer']} {'VERIFIED' if r['verified'] else 'NOT VERIFIED'}")

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
                    "country_code": resume_idx.get("country_code") if self.is_phone_element(e) else None,
                }

                result = await self.retry_failed_answer(self.page, e, answer_item)
                deterministic_results.append(result)

                status = "VERIFIED" if result.get("verified") else "NOT VERIFIED"
                print(f"{item['field']:30} -> {item['value']}")
                print(f"   -> {status} ({result.get('method')})")

            national = self.clean_text(resume_idx.get("national_phone"))
            country = self.clean_text(resume_idx.get("country_code"))
            for e in elements:
                if not self.is_phone_element(e) or self.is_phone_country_element(e):
                    continue
                phone_loc, _, _ = await self.locator_from_element(self.page, e)
                if not phone_loc or not national:
                    continue
                actual = self.digits(await self.read_control_value(phone_loc))
                need = self.digits(national)[-10:] if len(self.digits(national)) >= 10 else self.digits(national)
                if need and need in actual:
                    continue
                ok, actual = await self.fill_phone_widget(phone_loc, national, country)
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
                if idx in ai_excluded or self.is_captcha(e) or self.is_submit(e) or self.is_noise_control(e):
                    continue
                if self.norm(e.get("tag")) == "button" or self.norm(e.get("type")) in {"button", "submit", "reset"} or self.norm(e.get("control_type")) in {"button", "submit_button"}:
                    continue
                if self.is_file_upload(e) or self.looks_like_resume_upload(e) or self.norm(e.get("type")) == "file" or self.control_kind(e) == "upload":
                    continue
                if e.get("disabled") is True or self.norm(e.get("type")) == "hidden":
                    continue
                remaining_raw.append(e)

            if important:
                remaining = [
                    e for e in self.collect_fill_targets(elements, important)
                    if e["_element_index"] not in ai_excluded
                    and not self.is_noise_control(e)
                    and not self.is_captcha(e)
                    and not self.is_submit(e)
                    and self.norm(e.get("tag")) != "button"
                    and self.norm(e.get("type")) not in {"button", "submit", "reset"}
                    and self.norm(e.get("control_type")) not in {"button", "submit_button"}
                    and not self.is_file_upload(e)
                    and not self.looks_like_resume_upload(e)
                    and self.norm(e.get("type")) != "file"
                    and self.control_kind(e) != "upload"
                ]
            else:
                remaining = self.dedupe_interactive_elements(remaining_raw)

            filled_keys = {self._question_key(by_index[i]) for i in ai_excluded if i in by_index}
            remaining = [e for e in remaining if self._question_key(e) not in filled_keys]
            remaining = self.dedupe_interactive_elements(remaining)

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
                        batch_data = self.normalize_ai_answers(self.call_bedrock(self.build_ai_prompt(batch, resume, deterministic)))
                        ai_data["answers"].extend(batch_data.get("answers", []))
                    except Exception as exc:
                        print(f"AI batch {batch_no} failed: {exc}")
                if len(batches) > max_batches:
                    print(f"WARNING: {len(batches) - max_batches} AI batches skipped; those fields will be asked from the user.")
                # self.save_json(AI_ANSWERS_FILE, ai_data)
                print(json.dumps(ai_data, indent=2, ensure_ascii=False))

            def _prefer_not(text: Any) -> bool:
                t = self.canonical_text(text)
                return t.startswith("prefer not") or t in {"decline to self identify", "decline to answer"}

            for item in ai_data.get("answers", []):
                if not isinstance(item, dict) or not str(item.get("element_index", "")).isdigit():
                    continue
                e = by_index.get(int(item["element_index"]))
                if not e:
                    continue
                q = self.canonical_text(e.get("question") or e.get("label"))
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
                and self.norm(x.get("action")) != "skip"
                and self.clean_text(x.get("answer") or x.get("option_label"))
            }

            for e in remaining:
                idx = e.get("_element_index")
                if idx is None or idx in ai_indexes:
                    continue
                q = self.canonical_text(e.get("question") or e.get("label"))
                opts = e.get("options") or []
                opt_labels = [self.clean_text(o.get("label") if isinstance(o, dict) else o) for o in opts]

                if ("experience" in q or "year" in q) and opts and all(self.digits(o) for o in opt_labels if o):
                    cand_exp = str(resume_idx.get("experience_years") or 1)
                    best_opt = next((o for o in opt_labels if o == cand_exp), None) or opt_labels[0]
                    ai_data["answers"].append({
                        "element_index": idx,
                        "selector": e.get("selector"),
                        "frame_index": 0,
                        "frame_url": e.get("frame_url"),
                        "answer": best_opt,
                        "action": "radio" if self.norm(e.get("control_type")) in {"radio_group", "radio"} else "select",
                        "option_label": best_opt,
                        "option_value": best_opt,
                        "reason": f"Applicant experience scale auto-match ({cand_exp} yr)"
                    })
                    ai_indexes.add(idx)
                elif any(w in q for w in ["consent", "agreement", "text message", "sms updates", "opt in"]) and opts:
                    yes_opt = next((o for o in opt_labels if self.norm(o).startswith("yes") or self.norm(o) == "i agree"), None)
                    if yes_opt:
                        ai_data["answers"].append({
                            "element_index": idx,
                            "selector": e.get("selector"),
                            "frame_index": 0,
                            "frame_url": e.get("frame_url"),
                            "answer": yes_opt,
                            "action": "radio" if self.norm(e.get("control_type")) in {"radio_group", "radio"} else "select",
                            "option_label": yes_opt,
                            "option_value": yes_opt,
                            "reason": "Applicant consent auto-agree"
                        })
                        ai_indexes.add(idx)

            answered_keys = set(filled_keys)
            for idx in ai_indexes:
                e = by_index.get(idx)
                if e:
                    answered_keys.add(self._question_key(e))
            missing_for_user = [
                e for e in self._missing_answer_candidates(remaining, ai_indexes)
                if self._question_key(e) not in answered_keys
            ]
            if missing_for_user:
                user_data = self.ask_user_for_missing_fields(missing_for_user, resume)
                ai_data["answers"].extend(user_data.get("answers", []))
                # self.save_json(AI_ANSWERS_FILE, ai_data)

            ai_answers = self.validate_ai_answers(ai_data, elements)

            # self.save_json(MAPPING_FILE, {
            #     "deterministic": deterministic,
            #     "ai_answers": ai_answers,
            # })

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
                if not e or self.is_captcha(e) or self.is_submit(e):
                    return None
                action = self.norm(item.get("action") or "fill")
                if action == "skip" and self.control_kind(e) not in {"select", "radio"}:
                    return {
                        "element_index": idx,
                        "question": e.get("question"),
                        "answer": item.get("answer"),
                        "action": "skip",
                        "verified": True,
                        "method": "ai_skip",
                    }
                is_select_kind = self.norm(e.get("tag")) == "select" or self.control_kind(e) in {"select", "radio", "checkbox"}
                if not is_select_kind and (
                    self.norm(e.get("type")) == "url"
                    or e.get("field_key") in {"portfolio_url", "linkedin", "github", "website"}
                    or re.search(r"\b(url|website|portfolio link|profile link|github link|linkedin link)\b", self.norm(e.get("question") or ""))
                ):
                    ans = item.get("answer")
                    if ans and not self.is_empty(ans):
                        item["answer"] = self.ensure_https_url(ans)
                        if item.get("value"):
                            item["value"] = self.ensure_https_url(item["value"])
                return await self.retry_failed_answer(self.page, e, item)

            interactive_items = []
            text_items = []
            for item in ai_answers:
                try:
                    e = by_index.get(int(item.get("element_index")))
                except Exception:
                    e = None
                if not e:
                    continue
                action = self.norm(item.get("action") or self.control_kind(e))
                if action in {"select", "radio", "checkbox", "upload"} or self.control_kind(e) in {"select", "radio", "checkbox", "upload"}:
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
                if not e or self.is_noise_control(e):
                    continue
                if result.get("action") == "skip":
                    final_results.append(result)
                    continue

                q_label = self.clean_text(e.get("question") or e.get("label") or e.get("name") or "")[:45]
                print(f"\n--- [Field {idx}] {q_label} ({e.get('selector') or ''}) ---")

                verified, actual_val, step_logs = await self.multi_step_verify_and_repair(
                    self.page , e, result, resume_idx,
                )

                for line in step_logs:
                    print(f"  {line}")

                result["final_verified"] = verified
                result["actual_value"] = actual_val
                result["verification_steps"] = step_logs
                final_results.append(result)

            await self.reveal_rest_of_form(self.page)

            verified_count = sum(
                1
                for r in final_results
                if r.get("final_verified") is True
            )

            attempted_count = len([r for r in final_results if r.get("action") != "skip"])

            output = {
                "url": self.url,
                "title": await self.page.title(),
                "elements_count": len(elements),
                "deterministic_count": len(deterministic_results),
                "ai_answer_count": len(ai_answers),
                "attempted_count": attempted_count,
                "verified_count": verified_count,
                "results": final_results,
                "browser_left_open": True,
                "submitted": False,
            }

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
            print("Application is NOT submitted.")

            if True:
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

async def call_playwright(url):
    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=False)
            context = await browser.new_context()
            page = await context.new_page()
            
            await page.goto(url, wait_until="domcontentloaded", timeout=PAGE_LOAD_TIMEOUT_MS)
            
            Form_applier = FormFiller(page.url , page)
            main = await Form_applier.main()
            print(main)
    except Exception as e:
        print(f"error is {e}")
            
if __name__ == "__main__":
    url = input("Enter the url :- ")
    asyncio.run(call_playwright(url))
    