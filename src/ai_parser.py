"""AI parsing - multi-model fallback by token efficiency: Groq llama-3.1-8b, Groq llama3-8b-8192, Gemini."""

from dotenv import load_dotenv
from pathlib import Path
load_dotenv(Path(__file__).parent.parent / ".env")

import json
import time
from datetime import datetime
from typing import Optional

import config

# Model cascade by token efficiency (500K tokens/day each for Groq)
MODEL_CASCADE = [
    ("groq", "llama-3.1-8b-instant"),
    ("groq", "llama3-8b-8192"),
    ("gemini", "gemini-2.0-flash-lite"),
]

RETRY_DELAY_SECONDS = 60
MAX_RETRIES_TPM = 3  # Retries for "tokens per minute" before giving up on this call

_current_model_index: int = 0

PROMPT = """Extract job application data. Return ONLY this JSON or the word null:
{"company":"Name","role":"Title","stage":"Applied|In Review|OA/Assessment|Phone Screen|Interview Scheduled|Interviewed|Offer|Rejected|Withdrawn","notes":"one line","is_internship":true/false}
If not a real application response, return null. No other text."""

VALID_STAGES = {"Applied", "In Review", "OA/Assessment", "Phone Screen", "Interview Scheduled", "Interviewed", "Offer", "Rejected", "Withdrawn"}


def _log(msg: str) -> None:
    with open(config.ERRORS_LOG_PATH, "a") as f:
        f.write(f"[{datetime.utcnow().isoformat()}] {msg}\n")


def reset_model_cascade() -> None:
    """Reset to first model (call at start of run if desired)."""
    global _current_model_index
    _current_model_index = 0


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


def _call_with_model(email: dict, provider: str, model: str) -> Optional[str]:
    """Call the specified provider/model."""
    from src.email_cleaner import clean_body

    body_clean = clean_body(email.get("body"))
    prompt = f"Subject: {email.get('subject','')}\nFrom: {email.get('from','')}\n\nBody:\n{body_clean}"
    full_prompt = f"{PROMPT}\n\n{prompt}"

    if provider == "groq":
        from groq import Groq
        client = Groq(api_key=config.get_groq_api_key())
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": full_prompt}],
            temperature=0.1,
        )
        return resp.choices[0].message.content if resp.choices else ""
    else:
        from google import genai
        from google.genai.types import GenerateContentConfig
        client = genai.Client(api_key=config.get_gemini_api_key())
        resp = client.models.generate_content(
            model=model,
            contents=full_prompt,
            config=GenerateContentConfig(temperature=0.1),
        )
        return resp.text if resp and hasattr(resp, "text") else ""


def _classify_429(err_msg: str) -> str:
    """Return 'switch' (switch to next model) or 'retry' (wait and retry same model)."""
    lower = err_msg.lower()
    if "tokens per minute" in lower or "tpm" in lower:
        return "retry"
    if "tokens per day" in lower or "tpd" in lower:
        return "switch"
    if "requests per day" in lower or "rpd" in lower:
        return "switch"
    if "rate limit" in lower or "429" in lower:
        return "switch"
    return "switch"


def parse_email_with_ai(email: dict) -> tuple[str, Optional[dict]]:
    """
    Multi-model fallback. Returns (status, result).
    status: "success" | "quota" | "all_exhausted" | "rate_limit_fail" | "error"
    """
    global _current_model_index

    try:
        from src import database

        groq_key = config.get_groq_api_key()
        gemini_key = config.get_gemini_api_key()
        if not groq_key and not gemini_key:
            _log("No GROQ_API_KEY or GEMINI_API_KEY in .env")
            return ("error", None)

        time.sleep(config.get_min_seconds_between_calls())

        while _current_model_index < len(MODEL_CASCADE):
            provider, model = MODEL_CASCADE[_current_model_index]
            if provider == "groq" and not groq_key:
                _current_model_index += 1
                continue
            if provider == "gemini" and not gemini_key:
                _current_model_index += 1
                continue

            tpm_retries = 0
            while tpm_retries <= MAX_RETRIES_TPM:
                try:
                    text = _call_with_model(email, provider, model)
                    database.increment_daily_gemini_count()
                    result = _parse_response(text, email.get("id", "?"))
                    return ("success", result)
                except Exception as e:
                    err = str(e)
                    err_lower = err.lower()
                    if "429" not in err_lower and "rate" not in err_lower and "limit" not in err_lower:
                        _log(f"AI error ({model}): {e}")
                        return ("error", None)

                    action = _classify_429(err)
                    if action == "retry":
                        tpm_retries += 1
                        if tpm_retries <= MAX_RETRIES_TPM:
                            _log(f"RATE LIMIT (tokens/min): wait {RETRY_DELAY_SECONDS}s retry {tpm_retries}/{MAX_RETRIES_TPM} on {model}")
                            time.sleep(RETRY_DELAY_SECONDS)
                        else:
                            _log(f"RATE LIMIT: max retries on {model}, switching")
                            break
                    else:
                        _current_model_index += 1
                        if _current_model_index < len(MODEL_CASCADE):
                            next_provider, next_model = MODEL_CASCADE[_current_model_index]
                            _log(f"Switching to model {next_model}")
                            break
                        else:
                            _log("All AI quotas exhausted")
                            return ("all_exhausted", None)

            if tpm_retries > MAX_RETRIES_TPM:
                _current_model_index += 1
                if _current_model_index < len(MODEL_CASCADE):
                    next_provider, next_model = MODEL_CASCADE[_current_model_index]
                    _log(f"Switching to model {next_model}")
                else:
                    return ("all_exhausted", None)

            continue

        return ("all_exhausted", None)
    except Exception as e:
        _log(f"AI unexpected: {e}")
        return ("error", None)
