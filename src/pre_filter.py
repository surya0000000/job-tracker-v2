"""Pre-filter: discard junk BEFORE any AI. Stricter = fewer API calls."""

from dotenv import load_dotenv
from pathlib import Path
load_dotenv(Path(__file__).parent.parent / ".env")

import re
from datetime import datetime
from typing import Optional

import config

# Like auto-job-tracker: only these pass (aggressive filter)
MUST_PASS_SUBJECTS = [
    "applied",
    "application",
    "thanks for applying",
    "thank you for applying",
    "thanks from",
    "follow-up",
    "update",
    "recruiting",
    "we received",
    "interview",
    "offer",
    "unfortunately",
    "next steps",
]

HARD_REJECT = [
    "job alert",
    "jobs you might like",
    "recommended jobs",
    "newsletter",
    "digest",
    "viewed your profile",
    "connection request",
]

PERSONAL_DOMAINS = {"gmail.com", "yahoo.com", "hotmail.com", "outlook.com", "icloud.com", "aol.com"}

ATS_DOMAINS = [
    "greenhouse.io", "lever.co", "workday.com", "myworkdayjobs.com",
    "ashbyhq.com", "smartrecruiters.com", "jobvite.com", "icims.com",
    "jazz.co", "recruitee.com", "bamboohr.com", "rippling.com", "dover.com",
]


def _domain(from_addr: str) -> str:
    m = re.search(r"@([\w.-]+)", (from_addr or "").lower())
    return m.group(1) if m else ""


def pre_filter(email: dict) -> Optional[str]:
    """None = PASS, else rejection reason."""
    subject = (email.get("subject") or "").lower()
    domain = _domain(email.get("from") or "")

    for phrase in HARD_REJECT:
        if phrase in subject:
            return f"reject: {phrase}"

    if domain in PERSONAL_DOMAINS:
        return "reject: personal domain"

    if any(x in domain for x in ["linkedin.com", "indeed.com", "glassdoor.com", "ziprecruiter.com"]):
        return "reject: job board"

    if not any(p in subject for p in MUST_PASS_SUBJECTS) and not any(a in domain for a in ATS_DOMAINS):
        return "reject: no application keywords"

    return None
