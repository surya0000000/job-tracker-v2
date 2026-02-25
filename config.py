"""Configuration for Job Application Tracker."""

import os
from pathlib import Path

# Paths
BASE_DIR = Path(__file__).parent
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

# API keys from environment (GitHub Secrets)
def get_gemini_api_key() -> str:
    return os.environ.get("GEMINI_API_KEY", "")

def get_google_credentials() -> str:
    return os.environ.get("GOOGLE_CREDENTIALS", "")

def get_google_token() -> str:
    return os.environ.get("GOOGLE_TOKEN", "")

def get_spreadsheet_id() -> str:
    return os.environ.get("SPREADSHEET_ID", "")
