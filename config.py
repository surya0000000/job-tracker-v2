"""Configuration for Job Application Tracker."""

from dotenv import load_dotenv
from pathlib import Path

load_dotenv(Path(__file__).parent / ".env")

import os

BASE_DIR = Path(__file__).parent
CREDENTIALS_PATH = BASE_DIR / "credentials.json"
TOKEN_PATH = BASE_DIR / "token.json"
DATABASE_PATH = BASE_DIR / "applications.db"
ERRORS_LOG_PATH = BASE_DIR / "errors.log"

GMAIL_SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.file",
]

INITIAL_SCAN_MONTHS = 8
DAILY_SCAN_DAYS = 7

# AI provider: "groq" or "gemini"
# Groq: 30 RPM, 14,400 RPD (llama-3.1-8b) - FREE, no CC, faster
# Gemini: 15 RPM, 1000 RPD - FREE from aistudio.google.com (no Google AI Pro needed!)
AI_PROVIDER = "groq"  # Prefer Groq: more requests, no rate limit hassle
GEMINI_MODEL = "gemini-2.0-flash-lite"
GEMINI_DAILY_QUOTA_LIMIT = 1500     # Actual daily API quota
GROQ_MODEL = "llama-3.1-8b-instant"  # 30 RPM, 14.4K RPD free
GROQ_DAILY_QUOTA_LIMIT = 12000      # Actual daily API quota
# No per-run cap — only daily quotas apply

def get_min_seconds_between_calls() -> int:
    return 3 if get_ai_provider() == "groq" else 6  # Groq 30 RPM; Gemini 15 RPM

STAGE_PRIORITY = {
    "Applied": 1, "In Review": 2, "OA/Assessment": 3, "Phone Screen": 4,
    "Interview Scheduled": 5, "Interviewed": 6, "Offer": 7, "Rejected": 8, "Withdrawn": 9,
}


def get_gemini_api_key() -> str:
    return os.environ.get("GEMINI_API_KEY", "").strip()


def get_groq_api_key() -> str:
    return os.environ.get("GROQ_API_KEY", "").strip()


def get_ai_provider() -> str:
    """groq if GROQ_API_KEY set, else gemini. Groq has 14x more free requests."""
    if get_groq_api_key():
        return "groq"
    return "gemini"


def get_google_credentials() -> str:
    return os.environ.get("GOOGLE_CREDENTIALS", "").strip()


def get_google_token() -> str:
    return os.environ.get("GOOGLE_TOKEN", "").strip()


def get_spreadsheet_id() -> str:
    return os.environ.get("SPREADSHEET_ID", "").strip()


def save_spreadsheet_id_to_env(spreadsheet_id: str) -> None:
    env_path = BASE_DIR / ".env"
    lines = []
    key_found = False
    if env_path.exists():
        with open(env_path) as f:
            for line in f:
                if line.strip().startswith("SPREADSHEET_ID="):
                    lines.append(f"SPREADSHEET_ID={spreadsheet_id}\n")
                    key_found = True
                else:
                    lines.append(line)
    if not key_found:
        lines.append(f"SPREADSHEET_ID={spreadsheet_id}\n")
    with open(env_path, "w") as f:
        f.writelines(lines)
