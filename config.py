"""Configuration for Job Application Tracker."""

from pathlib import Path

# Load .env from project root before any os.environ.get()
BASE_DIR = Path(__file__).parent
_env_path = BASE_DIR / ".env"
if _env_path.exists():
    from dotenv import load_dotenv
    load_dotenv(_env_path)

import os

# Paths
CREDENTIALS_PATH = BASE_DIR / "credentials.json"
TOKEN_PATH = BASE_DIR / "token.json"
DATABASE_PATH = BASE_DIR / "applications.db"
ERRORS_LOG_PATH = BASE_DIR / "errors.log"

# Gmail
GMAIL_SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.file",
]

# Email scan window (months)
INITIAL_SCAN_MONTHS = 8
DAILY_SCAN_DAYS = 7  # For incremental runs, scan last 7 days

# Stage priority (lower = earlier in pipeline)
STAGE_PRIORITY = {
    "Applied": 1,
    "In Review": 2,
    "OA/Assessment": 3,
    "Phone Screen": 4,
    "Interview Scheduled": 5,
    "Interviewed": 6,
    "Offer": 7,
    "Rejected": 8,  # Terminal
    "Withdrawn": 9,  # Terminal
}

# API keys from environment (.env locally, GitHub Secrets in CI)
def get_gemini_api_key() -> str:
    return os.environ.get("GEMINI_API_KEY", "").strip()

def get_google_credentials() -> str:
    return os.environ.get("GOOGLE_CREDENTIALS", "").strip()

def get_google_token() -> str:
    return os.environ.get("GOOGLE_TOKEN", "").strip()

def get_spreadsheet_id() -> str:
    return os.environ.get("SPREADSHEET_ID", "").strip()


def save_spreadsheet_id_to_env(spreadsheet_id: str) -> None:
    """Save SPREADSHEET_ID to .env file so it persists across restarts."""
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
