"""AI parsing - Groq (30 RPM, 14.4K RPD) or Gemini (15 RPM, 1K RPD). Both FREE."""

from dotenv import load_dotenv
from pathlib import Path
load_dotenv(Path(__file__).parent.parent / ".env")

import json
import re
import time
from datetime import datetime
from typing import Optional

import config

PROMPT = """Extract job application data. Return ONLY this JSON or the word null:
{"company":"Name","role":"Title","stage":"Applied|In Review|OA/Assessment|Phone Screen|Interview Scheduled|Interviewed|Offer|Rejected|Withdrawn","notes":"one line","is_internship":true/false}
If not a real application response, return null. No other text."""

VALID_STAGES = {"Applied", "In Review", "OA/Assessment", "Phone Screen", "Interview Scheduled", "Interviewed", "Offer", "Rejected", "Withdrawn"}


def _log(msg: str) -> None:
    with open(config.ERRORS_LOG_PATH, "a") as f:
        f.write(f"[{datetime.utcnow().isoformat()}] {msg}\n")


def _parse_response(text: str, email_id: str) -> Optional[dict]:
    text = (text or "").strip()
    if text.lower() == "null" or not text:
        return None
    start = text.find("{")
    if start == -1:
        return None
    depth, end = 0, -1
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
    try:
        data = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return None
    company = (data.get("company") or "").strip()
    role = (data.get("role") or "").strip()
    stage = (data.get("stage") or "").strip()
    if not company or not role or stage not in VALID_STAGES:
        return None
    return {
        "company": company,
        "role": role,
        "stage": stage,
        "date": (data.get("date") or "")[:10],
        "notes": (data.get("notes") or "").strip(),
        "is_internship": bool(data.get("is_internship")),
    }


def _call_groq(email: dict) -> Optional[str]:
    """Groq: 30 RPM, 14.4K RPD - free, no subscription."""
    from src.email_cleaner import clean_body
    from groq import Groq

    client = Groq(api_key=config.get_groq_api_key())
    body_clean = clean_body(email.get("body"))
    prompt = f"Subject: {email.get('subject','')}\nFrom: {email.get('from','')}\n\nBody:\n{body_clean}"
    full_prompt = f"{PROMPT}\n\n{prompt}"

    resp = client.chat.completions.create(
        model=config.GROQ_MODEL,
        messages=[{"role": "user", "content": full_prompt}],
        temperature=0.1,
    )
    return resp.choices[0].message.content if resp.choices else ""


def _call_gemini(email: dict) -> Optional[str]:
    """Gemini: 15 RPM, 1000 RPD - free from aistudio.google.com (no Google AI Pro!)."""
    from google import genai
    from google.genai.types import GenerateContentConfig
    from src.email_cleaner import clean_body

    client = genai.Client(api_key=config.get_gemini_api_key())
    body_clean = clean_body(email.get("body"))
    prompt = f"Subject: {email.get('subject','')}\nFrom: {email.get('from','')}\n\nBody:\n{body_clean}"
    full_prompt = f"{PROMPT}\n\n{prompt}"

    resp = client.models.generate_content(
        model=config.GEMINI_MODEL,
        contents=full_prompt,
        config=GenerateContentConfig(temperature=0.1),
    )
    return resp.text if resp and hasattr(resp, "text") else ""


def parse_email_with_ai(email: dict) -> tuple[str, Optional[dict]]:
    """Always returns (status, result). Uses Groq if available (more free requests), else Gemini."""
    try:
        from src import database

        provider = config.get_ai_provider()
        api_key = config.get_groq_api_key() if provider == "groq" else config.get_gemini_api_key()
        if not api_key:
            _log("No GROQ_API_KEY or GEMINI_API_KEY in .env")
            return ("error", None)

        quota_limit = config.GROQ_DAILY_QUOTA_LIMIT if provider == "groq" else config.GEMINI_DAILY_QUOTA_LIMIT
        if database.get_daily_gemini_count() >= quota_limit:
            return ("quota", None)

        time.sleep(config.get_min_seconds_between_calls())

        for attempt in range(4):
            try:
                if database.get_daily_gemini_count() >= quota_limit:
                    return ("quota", None)
                text = _call_groq(email) if provider == "groq" else _call_gemini(email)
                database.increment_daily_gemini_count()
                result = _parse_response(text, email.get("id", "?"))
                return ("success", result)
            except Exception as e:
                err = str(e).lower()
                if ("429" in err or "rate" in err or "limit" in err) and attempt < 3:
                    _log(f"RATE LIMIT: wait 60s retry {attempt + 1}/3")
                    time.sleep(60)
                else:
                    _log(f"AI error: {e}")
                    return ("rate_limit_fail", None)
        return ("rate_limit_fail", None)
    except Exception as e:
        _log(f"AI unexpected: {e}")
        return ("error", None)
