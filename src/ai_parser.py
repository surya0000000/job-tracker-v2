"""Stage 2: AI parsing with Gemini 2.0 Flash (google-genai package)."""

from dotenv import load_dotenv
from pathlib import Path
load_dotenv(Path(__file__).parent.parent / ".env")

import json
import re
import time
from datetime import datetime
from typing import Optional

import config


SYSTEM_INSTRUCTION = """You are a strict email classifier for a personal job application tracker.

Your only job is to determine if this email is a direct response to a job or internship application that the email recipient personally submitted.

RETURN null FOR ALL OF THESE — absolutely no exceptions:
- Cold recruiter outreach where the user did NOT apply first
- Job alert emails of any kind
- LinkedIn notifications of any kind  
- Newsletters, digests, or promotional emails
- Emails from job boards that are not direct application responses
- Any email where the company name is not clearly and explicitly stated
- Any email where the job title is not clearly and explicitly stated
- Any email where you are less than 70% confident is a real application response
- Emails from universities, professors, or academic institutions
- Emails about internship programs that are generic info, not a response to your application

STAGE must be exactly one of these — no variations, no synonyms:
  Applied             = application confirmed or received by company
  In Review           = company says they are actively reviewing it
  OA/Assessment       = online test, coding challenge, or take-home assignment sent
  Phone Screen        = initial screening call scheduled or completed
  Interview Scheduled = technical, behavioral, or onsite interview scheduled
  Interviewed         = interview completed, now waiting for decision
  Offer               = job or internship offer explicitly extended
  Rejected            = application declined at any stage for any reason
  Withdrawn           = candidate withdrew the application

COMPANY NAME RULES:
  - Extract exactly as the company refers to itself in the email
  - Strip all legal suffixes: LLC, Inc, Corp, Ltd, Co., L.L.C., 
    Incorporated, Corporation, Limited, PLC, LLP, LP, GmbH, AG
  - If the email is from jobs@amazon.com, company = Amazon
  - If the email is from recruiting@stripe.com, company = Stripe  
  - Never use the ATS platform name as the company 
    (Greenhouse, Lever, Workday are platforms, not companies)
  - If you cannot determine the real company name, return null

ROLE NAME RULES:
  - Extract the exact job title as written in the email
  - Do not abbreviate or expand
  - If you cannot determine the role title, return null

Return ONLY this JSON structure or the exact word null.
No markdown. No code blocks. No explanation. No preamble:
{
  company: string,
  role: string,
  stage: string,
  date: YYYY-MM-DD,
  notes: one sentence describing what this email says,
  confidence: float between 0.0 and 1.0,
  is_internship: true or false
}

When in doubt: return null.
A missed email is always better than a wrong entry."""

VALID_STAGES = {
    "Applied", "In Review", "OA/Assessment", "Phone Screen",
    "Interview Scheduled", "Interviewed", "Offer", "Rejected", "Withdrawn",
}

MIN_CONFIDENCE = 0.70
RATE_LIMIT_WAIT_SECONDS = 60
RATE_LIMIT_MAX_RETRIES = 3


def _log_skip(email_id: str, reason: str) -> None:
    with open(config.ERRORS_LOG_PATH, "a") as f:
        f.write(f"[{datetime.utcnow().isoformat()}] AI SKIP [{email_id}]: {reason}\n")


def log_info(msg: str) -> None:
    line = f"[{datetime.utcnow().isoformat()}] {msg}"
    print(line)
    with open(config.ERRORS_LOG_PATH, "a") as f:
        f.write(line + "\n")


def _parse_ai_response(response_text: str, email_id: str) -> Optional[dict]:
    """Parse AI response. Returns dict or None."""
    text = (response_text or "").strip()
    if text.lower() == "null" or not text:
        return None

    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    end = -1
    for i, c in enumerate(text[start:], start):
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                end = i
                break
    if end == -1:
        return None
    json_str = text[start : end + 1]

    try:
        data = json.loads(json_str)
    except json.JSONDecodeError:
        return None

    company = (data.get("company") or "").strip()
    role = (data.get("role") or "").strip()
    stage = (data.get("stage") or "").strip()
    confidence = float(data.get("confidence", 0))

    if not company or company.lower() == "unknown":
        _log_skip(email_id, "company field empty, null, or Unknown")
        return None
    if not role or role.lower() == "unknown":
        _log_skip(email_id, "role field empty, null, or Unknown")
        return None
    if stage not in VALID_STAGES:
        _log_skip(email_id, f"invalid stage: {stage}")
        return None
    if confidence < MIN_CONFIDENCE:
        _log_skip(email_id, f"low confidence: {confidence}")
        return None

    date_str = data.get("date") or ""
    if not re.match(r"\d{4}-\d{2}-\d{2}", date_str):
        date_str = ""

    return {
        "company": company,
        "role": role,
        "stage": stage,
        "date": date_str,
        "notes": (data.get("notes") or "").strip(),
        "confidence": confidence,
        "is_internship": bool(data.get("is_internship", False)),
    }


def _is_429_error(e: Exception) -> bool:
    err_str = str(e).lower()
    return "429" in err_str or "rate limit" in err_str or "resource exhausted" in err_str


def parse_email_with_ai(email: dict) -> tuple[str, Optional[dict]]:
    """
    Parse single email with Gemini. ALWAYS returns (status, result).
    status: "success" | "rate_limit_fail" | "quota" | "error"
    result: dict or None
    """
    try:
        from src import database

        api_key = config.get_gemini_api_key()
        if not api_key:
            _log_skip(email.get("id", "?"), "GEMINI_API_KEY not set")
            return ("error", None)

        count = database.get_daily_gemini_count()
        if count >= config.GEMINI_DAILY_QUOTA_LIMIT:
            return ("quota", None)

        time.sleep(config.MIN_SECONDS_BETWEEN_CALLS)

        from google import genai
        from google.genai.types import GenerateContentConfig

        client = genai.Client(api_key=api_key)

        prompt = f"""Subject: {email.get('subject', '')}
From: {email.get('from', '')}
Date: {email.get('date', '')}

Body (first 3000 chars):
{(email.get('body') or '')[:3000]}"""

        def _do_call(prompt_text: str) -> str:
            count = database.get_daily_gemini_count()
            if count >= config.GEMINI_DAILY_QUOTA_LIMIT:
                raise ValueError("DAILY_QUOTA_REACHED")
            response = client.models.generate_content(
                model=config.GEMINI_MODEL,
                contents=prompt_text,
                config=GenerateContentConfig(
                    system_instruction=SYSTEM_INSTRUCTION,
                    temperature=0.1,
                ),
            )
            database.increment_daily_gemini_count()
            return response.text if response and hasattr(response, "text") else ""

        text = ""
        for attempt in range(RATE_LIMIT_MAX_RETRIES + 1):
            try:
                text = _do_call(prompt)
                break
            except ValueError as e:
                if "DAILY_QUOTA_REACHED" in str(e):
                    return ("quota", None)
                raise
            except Exception as e:
                if _is_429_error(e) and attempt < RATE_LIMIT_MAX_RETRIES:
                    log_info(f"RATE LIMIT: waiting {RATE_LIMIT_WAIT_SECONDS}s before retry {attempt + 1}/{RATE_LIMIT_MAX_RETRIES}")
                    time.sleep(RATE_LIMIT_WAIT_SECONDS)
                else:
                    _log_skip(email.get("id", "?"), f"AI error: {e}")
                    return ("rate_limit_fail", None)

        result = _parse_ai_response(text, email.get("id", "?"))

        if result:
            log_info(f"AI PARSED: company={result['company']} role={result['role']} stage={result['stage']}")

        if result is None and text and text.lower() != "null":
            time.sleep(config.MIN_SECONDS_BETWEEN_CALLS)
            retry_prompt = prompt + "\n\nIMPORTANT: Respond with ONLY valid JSON or the exact word null. No other text."
            try:
                text = _do_call(retry_prompt)
                result = _parse_ai_response(text, email.get("id", "?"))
            except ValueError:
                return ("quota", None)
            except Exception as e:
                if _is_429_error(e):
                    return ("rate_limit_fail", None)
                raise

        return ("success", result)

    except Exception as e:
        _log_skip(email.get("id", "?"), f"Unexpected error: {e}")
        return ("error", None)
