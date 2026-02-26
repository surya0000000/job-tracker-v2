"""AI parsing with Gemini 2.0 Flash-Lite (15 RPM, 1000 RPD - quota friendly)."""

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


def parse_email_with_ai(email: dict) -> tuple[str, Optional[dict]]:
    """Always returns (status, result). status: success|rate_limit_fail|quota|error"""
    try:
        from src import database

        if not config.get_gemini_api_key():
            return ("error", None)

        if database.get_daily_gemini_count() >= config.GEMINI_DAILY_QUOTA_LIMIT:
            return ("quota", None)

        time.sleep(config.MIN_SECONDS_BETWEEN_CALLS)

        from google import genai
        from google.genai.types import GenerateContentConfig
        from src.email_cleaner import clean_body

        client = genai.Client(api_key=config.get_gemini_api_key())
        body_clean = clean_body(email.get("body"))
        prompt = f"Subject: {email.get('subject','')}\nFrom: {email.get('from','')}\n\nBody:\n{body_clean}"

        for attempt in range(4):
            try:
                if database.get_daily_gemini_count() >= config.GEMINI_DAILY_QUOTA_LIMIT:
                    return ("quota", None)
                resp = client.models.generate_content(
                    model=config.GEMINI_MODEL,
                    contents=f"{PROMPT}\n\n{prompt}",
                    config=GenerateContentConfig(temperature=0.1),
                )
                database.increment_daily_gemini_count()
                text = resp.text if resp and hasattr(resp, "text") else ""
                result = _parse_response(text, email.get("id", "?"))
                return ("success", result)
            except Exception as e:
                err = str(e).lower()
                if "429" in err or "resource" in err and attempt < 3:
                    _log(f"RATE LIMIT: wait 60s retry {attempt + 1}/3")
                    time.sleep(60)
                else:
                    _log(f"AI error: {e}")
                    return ("rate_limit_fail", None)
        return ("rate_limit_fail", None)
    except Exception as e:
        _log(f"AI unexpected: {e}")
        return ("error", None)
