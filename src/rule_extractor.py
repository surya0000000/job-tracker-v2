"""Rule-based extraction - NO AI. Extract company/role/stage from patterns."""

from dotenv import load_dotenv
from pathlib import Path
load_dotenv(Path(__file__).parent.parent / ".env")

import re
from typing import Optional

import config
from src.deduplication import normalize_company

# Sender domain -> company (common patterns)
DOMAIN_TO_COMPANY = {
    "amazon.com": "Amazon",
    "amazon.jobs": "Amazon",
    "google.com": "Google",
    "meta.com": "Meta",
    "facebook.com": "Meta",
    "microsoft.com": "Microsoft",
    "apple.com": "Apple",
    "stripe.com": "Stripe",
    "uber.com": "Uber",
    "lyft.com": "Lyft",
    "airbnb.com": "Airbnb",
    "netflix.com": "Netflix",
    "adobe.com": "Adobe",
    "salesforce.com": "Salesforce",
    "oracle.com": "Oracle",
    "intel.com": "Intel",
    "nvidia.com": "NVIDIA",
    "amd.com": "AMD",
    "qualcomm.com": "Qualcomm",
    "ibm.com": "IBM",
    "dell.com": "Dell",
    "hp.com": "HP",
    "vmware.com": "VMware",
    "servicenow.com": "ServiceNow",
    "workday.com": "Workday",
    "sap.com": "SAP",
    "jpmorgan.com": "JPMorgan",
    "jpmchase.com": "JPMorgan",
    "goldmansachs.com": "Goldman Sachs",
    "morganstanley.com": "Morgan Stanley",
    "twilio.com": "Twilio",
    "databricks.com": "Databricks",
    "snowflake.com": "Snowflake",
    "mongodb.com": "MongoDB",
    "atlassian.com": "Atlassian",
    "slack.com": "Slack",
    "dropbox.com": "Dropbox",
    "box.com": "Box",
    "zoom.us": "Zoom",
    "roblox.com": "Roblox",
    "unity.com": "Unity",
    "epicgames.com": "Epic Games",
    "tesla.com": "Tesla",
    "spacex.com": "SpaceX",
}

# ATS subdomains often contain company (e.g., google.wd1.myworkdayjobs.com)
ATS_DOMAIN_PATTERNS = [
    (r"([a-z0-9-]+)\.lever\.co", 1),
    (r"([a-z0-9-]+)\.greenhouse\.io", 1),
    (r"([a-z0-9-]+)\.myworkdayjobs\.com", 1),
    (r"([a-z0-9-]+)\.workday\.com", 1),
    (r"([a-z0-9-]+)\.ashbyhq\.com", 1),
    (r"([a-z0-9-]+)\.jobs\.ashbyhq\.com", 1),
    (r"([a-z0-9-]+)\.recruitee\.com", 1),
    (r"([a-z0-9-]+)\.rippling\.com", 1),
    (r"([a-z0-9-]+)\.dover\.io", 1),
]


def _extract_domain(email_addr: str) -> str:
    m = re.search(r"@([\w.-]+)", email_addr or "", re.IGNORECASE)
    return (m.group(1) or "").lower()


def _domain_to_company(domain: str) -> Optional[str]:
    if not domain:
        return None
    base = domain.split("/")[0].lower()
    # Direct match
    for d, c in DOMAIN_TO_COMPANY.items():
        if d in base or base.endswith("." + d):
            return c
    # ATS patterns
    for pattern, group in ATS_DOMAIN_PATTERNS:
        m = re.search(pattern, base)
        if m:
            name = m.group(group).replace("-", " ").title()
            if len(name) > 2:
                return name
    # jobs@, recruiting@, careers@ + domain
    if any(base.startswith(x) for x in ["jobs.", "recruiting.", "careers.", "talent."]):
        core = re.sub(r"^(jobs|recruiting|careers|talent)\.", "", base)
        core = core.split(".")[0] if "." in core else core
        if core and len(core) > 2:
            return core.replace("-", " ").title()
    return None


def _stage_from_text(text: str) -> str:
    """Detect stage from subject+body keywords."""
    t = (text or "").lower()
    if any(x in t for x in ["unfortunately", "not selected", "not moving forward", "declined", "we've decided"]):
        return "Rejected"
    if any(x in t for x in ["offer", "pleased to offer", "we'd like to extend"]):
        return "Offer"
    if any(x in t for x in ["interview", "phone screen", "onsite", "technical interview"]):
        return "Interview Scheduled"
    if any(x in t for x in ["assessment", "coding challenge", "online test"]):
        return "OA/Assessment"
    if any(x in t for x in ["application received", "we received your", "thank you for applying"]):
        return "Applied"
    return "Applied"


def _role_from_subject(subject: str) -> Optional[str]:
    """Extract role from common subject patterns."""
    s = (subject or "").strip()
    patterns = [
        r"(?:application|applied)\s+(?:for|to)\s+(?:the\s+)?(.+?)\s+(?:position\s+)?(?:at|@)",
        r"(.+?)\s+[-–—]\s+(?:application|applied)",
        r"(?:position|role):\s*(.+?)(?:\s+at|\s*$)",
        r"(.+?)\s+(?:intern|engineer|developer|analyst)\s*(?:position|role)?\s*(?:at|@)",
        r"your\s+application\s+for\s+(.+?)(?:\s+at|\s*$)",
    ]
    for p in patterns:
        m = re.search(p, s, re.IGNORECASE)
        if m:
            role = m.group(1).strip()
            if len(role) > 3 and len(role) < 80:
                return role
    return None


def try_extract(email: dict) -> Optional[dict]:
    """
    Try to extract company, role, stage from rules. Returns dict or None.
    If we extract with confidence, use it and skip AI.
    """
    subject = (email.get("subject") or "").strip()
    body = (email.get("body") or "")[:2000]
    from_addr = email.get("from") or ""

    domain = _extract_domain(from_addr)
    company = _domain_to_company(domain)

    # Need at least company from sender
    if not company:
        # Try body for "at Company" or "from Company"
        m = re.search(r"(?:at|from|@)\s+([A-Z][a-zA-Z0-9\s&.-]{2,40}?)(?:\s+(?:for|–|-|\n)|$)", body)
        if m:
            company = normalize_company(m.group(1))
        else:
            return None

    role = _role_from_subject(subject)
    if not role:
        # Try body
        m = re.search(r"(?:position|role|applying for):\s*([^\n,]+)", body, re.IGNORECASE)
        if m:
            role = m.group(1).strip()[:80]
        else:
            role = "Unknown Role"

    stage = _stage_from_text(subject + " " + body[:500])

    return {
        "company": company,
        "role": role,
        "stage": stage,
        "date": (email.get("date") or "")[:10],
        "notes": f"Extracted from: {subject[:80]}",
        "confidence": 0.85,
        "is_internship": "intern" in (subject + body).lower(),
    }
