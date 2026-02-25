"""Stage 2: AI parsing with Gemini 2.0 Flash."""

from pathlib import Path
_env_path = Path(__file__).parent.parent / ".env"
if _env_path.exists():
    from dotenv import load_dotenv
    load_dotenv(_env_path)

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
    "Applied",
    "In Review",
    "OA/Assessment",
    "Phone Screen",
    "Interview Scheduled",
    "Interviewed",
    "Offer",
    "Rejected",
    "Withdrawn",
}

BATCH_SIZE = 3
BATCH_PAUSE_SECONDS = 2
MIN_CONFIDENCE = 0.70


def _log_skip(email_id: str, reason: str) -> None:
    """Log skip to errors.log."""
    with open(config.ERRORS_LOG_PATH, "a") as f:
        f.write(f"[{datetime.utcnow().isoformat()}] AI SKIP [{email_id}]: {reason}\n")


def log_info(msg: str) -> None:
    """Log info (also to stdout for visibility)."""
    line = f"[{datetime.utcnow().isoformat()}] {msg}"
    print(line)
    with open(config.ERRORS_LOG_PATH, "a") as f:
        f.write(line + "\n")


def _parse_ai_response(response_text: str, email_id: str) -> Optional[dict]:
    """Parse AI response. Returns dict or None."""
    text = (response_text or "").strip()

    # Check for null
    if text.lower() == "null" or not text:
        return None

    # Try to extract JSON (handle nested braces)
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

    # Validate required fields
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

    # Parse date
    date_str = data.get("date") or ""
    if not re.match(r"\d{4}-\d{2}-\d{2}", date_str):
        date_str = ""  # Will use email date as fallback

    return {
        "company": company,
        "role": role,
        "stage": stage,
        "date": date_str,
        "notes": (data.get("notes") or "").strip(),
        "confidence": confidence,
        "is_internship": bool(data.get("is_internship", False)),
    }


def parse_email_with_ai(email: dict) -> Optional[dict]:
    """
    Parse single email with Gemini. Returns parsed dict or None.
    """
    api_key = config.get_gemini_api_key()
    if not api_key:
        _log_skip(email.get("id", "?"), "GEMINI_API_KEY not set")
        return None

    try:
        import google.generativeai as genai

        genai.configure(api_key=api_key)
        model = genai.GenerativeModel(
            "gemini-2.0-flash",
            system_instruction=SYSTEM_INSTRUCTION,
            generation_config=genai.types.GenerationConfig(
                temperature=0.1,
            ),
        )

        prompt = f"""Subject: {email.get('subject', '')}
From: {email.get('from', '')}
Date: {email.get('date', '')}

Body (first 3000 chars):
{(email.get('body') or '')[:3000]}"""

        response = model.generate_content(prompt)
        text = response.text if response else ""

        result = _parse_ai_response(text, email.get("id", "?"))

        if result:
            log_info(f"AI PARSED: company={result['company']} role={result['role']} stage={result['stage']}")

        # Retry once if response wasn't valid JSON
        if result is None and text and text.lower() != "null":
            # Maybe it was malformed - retry with stricter prompt
            retry_prompt = prompt + "\n\nIMPORTANT: Respond with ONLY valid JSON or the exact word null. No other text."
            response = model.generate_content(retry_prompt)
            text = response.text if response else ""
            result = _parse_ai_response(text, email.get("id", "?"))

        return result

    except Exception as e:
        _log_skip(email.get("id", "?"), f"AI error: {e}")
        return None


def parse_emails_batch(emails: list[dict]) -> list[tuple[dict, Optional[dict]]]:
    """
    Parse emails in batches of 3 with 2 second pause. Returns list of (email, parsed_result).
    """
    results = []
    for i in range(0, len(emails), BATCH_SIZE):
        batch = emails[i : i + BATCH_SIZE]
        for email in batch:
            parsed = parse_email_with_ai(email)
            results.append((email, parsed))
        if i + BATCH_SIZE < len(emails):
            time.sleep(BATCH_PAUSE_SECONDS)
    return results
