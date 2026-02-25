"""Stage 1: Pre-filter - discard obvious junk before AI (no cost, instant)."""

import re
from datetime import datetime
from typing import Optional

import config


# Hard reject subject phrases (case insensitive)
HARD_REJECT_SUBJECTS = [
    "job alert",
    "jobs you might like",
    "recommended jobs",
    "new jobs matching",
    "people are applying",
    "jobs near you",
    "top job picks",
    "weekly digest",
    "newsletter",
    "salary insights",
    "viewed your profile",
    "connection request",
    "grow your network",
    "who's hiring",
    "jobs based on your profile",
    "open to work",
    "x people applied",
    "see who's hiring",
]

# Must-pass subject phrases (at least one required)
MUST_PASS_SUBJECTS = [
    "application",
    "applied",
    "interview",
    "offer",
    "internship",
    "position",
    "role",
    "hiring",
    "candidate",
    "recruiting",
    "assessment",
    "screening",
    "unfortunately",
    "next steps",
    "moving forward",
    "thank you for applying",
    "we received",
    "application update",
    "your application",
    "application confirmation",
    "job offer",
    "pleased to",
    "decision",
    "consideration",
    "move forward",
    "background check",
    "onboarding",
]

# Known ATS/recruiting platform domains
ATS_DOMAINS = [
    "greenhouse.io",
    "lever.co",
    "workday.com",
    "myworkdayjobs.com",
    "ashbyhq.com",
    "smartrecruiters.com",
    "jobvite.com",
    "icims.com",
    "taleo.net",
    "successfactors.com",
    "brassring.com",
    "jazz.co",
    "recruitee.com",
    "bamboohr.com",
    "rippling.com",
    "dover.com",
]

# Personal email domains (reject)
PERSONAL_DOMAINS = [
    "gmail.com",
    "yahoo.com",
    "hotmail.com",
    "outlook.com",
    "icloud.com",
    "aol.com",
    "protonmail.com",
    "live.com",
]

# Job board blast rules: (domain, subject_contains_any)
JOB_BOARD_BLAST_RULES = [
    ("linkedin.com", ["alert", "recommendation", "digest", "jobs", "people", "network", "profile"]),
    ("indeed.com", ["alert", "matches", "digest"]),
    ("glassdoor.com", ["alert", "matches"]),
]

# Always reject these domains (blast emails)
ALWAYS_REJECT_DOMAINS = [
    "ziprecruiter.com",
    "dice.com",
    "monster.com",
]


def _extract_domain(email_addr: str) -> str:
    """Extract domain from email address."""
    match = re.search(r"@([\w.-]+)", email_addr, re.IGNORECASE)
    return match.group(1).lower() if match else ""


def _log_rejection(email_id: str, reason: str) -> None:
    """Log rejection to errors.log."""
    with open(config.ERRORS_LOG_PATH, "a") as f:
        f.write(f"[{datetime.utcnow().isoformat()}] PRE-FILTER REJECT [{email_id}]: {reason}\n")


def pre_filter(email: dict) -> Optional[str]:
    """
    Run pre-filter on email. Returns None if PASS, or rejection reason string if REJECT.
    """
    email_id = email.get("id", "?")
    subject = (email.get("subject") or "").lower()
    from_addr = (email.get("from") or "").lower()
    domain = _extract_domain(from_addr)

    # HARD REJECT: subject contains junk phrases
    for phrase in HARD_REJECT_SUBJECTS:
        if phrase in subject:
            reason = f"subject contains '{phrase}'"
            _log_rejection(email_id, reason)
            return reason

    # HARD REJECT: personal domains
    if domain in PERSONAL_DOMAINS:
        reason = f"sender domain is personal: {domain}"
        _log_rejection(email_id, reason)
        return reason

    # HARD REJECT: always reject job board blasts
    if domain in ALWAYS_REJECT_DOMAINS:
        reason = f"sender is job board blast: {domain}"
        _log_rejection(email_id, reason)
        return reason

    # HARD REJECT: LinkedIn/Indeed/Glassdoor with blast-like subjects
    for blast_domain, blast_subjects in JOB_BOARD_BLAST_RULES:
        if blast_domain in domain:
            for phrase in blast_subjects:
                if phrase in subject:
                    reason = f"from {domain} and subject contains '{phrase}' (job board blast)"
                    _log_rejection(email_id, reason)
                    return reason

    # MUST PASS: subject must contain at least one relevant phrase
    subject_passes = any(phrase in subject for phrase in MUST_PASS_SUBJECTS)

    # OR: sender is known ATS
    domain_is_ats = any(ats in domain for ats in ATS_DOMAINS)

    if not subject_passes and not domain_is_ats:
        reason = "subject does not contain application-related keywords and sender is not known ATS"
        _log_rejection(email_id, reason)
        return reason

    return None  # PASS
