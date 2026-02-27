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
    "thank you for your interest",
    "thanks for your interest",
    "your interest",
    "thanks from",
    "follow-up",
    "update",
    "recruiting",
    "we received",
    "we've got your",
    "your application",
    "application is in",
    "application received",
    "interview",
    "offer",
    "unfortunately",
    "next steps",
    "confirmation",
    "confirmed",
    "careers",
    "position",
    "role",
    "candidate",
]

# From jobseeker-analytics applied_email_filter exclude
HARD_REJECT = [
    "job alert",
    "jobs you might like",
    "recommended jobs",
    "newsletter",
    "digest",
    "viewed your profile",
    "connection request",
    "do you want to finish your application",
    "you have new application updates this week",
    "matched new opportunities",
    "found jobs",
    "mock interview",
    "mock interview",
]

PERSONAL_DOMAINS = {"gmail.com", "yahoo.com", "hotmail.com", "outlook.com", "icloud.com", "aol.com"}

ATS_DOMAINS = [
    "greenhouse.io", "greenhouse-mail.io", "lever.co", "workday.com", "myworkdayjobs.com",
    "ashbyhq.com", "smartrecruiters.com", "jobvite.com", "icims.com",
    "jazz.co", "recruitee.com", "bamboohr.com", "rippling.com", "dover.com",
    "wellfound.com", "cardinalrefer.com", "hire.lever.co", "us.greenhouse-mail.io",
    "myworkday.com", "wd1.myworkday", "wd3.myworkday", "wd5.myworkday",
    "brex.com", "launchdarkly.com", "bytedance.com", "careers.bytedance",
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
