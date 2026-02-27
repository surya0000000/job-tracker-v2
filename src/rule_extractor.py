"""Rule-based extraction - NO AI. Extract company/role/stage from patterns."""

from dotenv import load_dotenv
from pathlib import Path
load_dotenv(Path(__file__).parent.parent / ".env")

import re
from typing import Optional

import config
from src.deduplication import normalize_company

# Local part (before @) for myworkday -> company when domain is myworkday.com/workday
MYWORKDAY_LOCAL_TO_COMPANY = {
    "disney": "Walt Disney Company",
    "statestreet": "State Street",
    "activision": "Activision Blizzard King",
    "relx": "Elsevier",
    "tmobile": "T-Mobile",
    "abcfitness": "ABC Fitness Solutions",
    "abcworkday": "ABC Fitness Solutions",
}

# Sender domain -> company (common patterns)
DOMAIN_TO_COMPANY = {
    "brex.com": "Brex",
    "launchdarkly.com": "LaunchDarkly",
    "bytedance.com": "ByteDance",
    "careers.bytedance.com": "ByteDance",
    "sigmacomputing.com": "Sigma Computing",
    "zoox.com": "Zoox",
    "scale.com": "Scale AI",
    "multiplylabs.com": "Multiply Labs",
    "spotandtango.com": "Spot & Tango",
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


def _local_part(from_addr: str) -> str:
    """Get part before @ from email address."""
    m = re.search(r"^([^@]+)@", from_addr or "", re.IGNORECASE)
    return (m.group(1) or "").lower()


def _domain_to_company(domain: str, from_addr: str = "") -> Optional[str]:
    if not domain:
        return None
    base = domain.split("/")[0].lower()

    # myworkday.com / workday: company often in local part (disney@myworkday.com)
    if "myworkday" in base or "workday" in base:
        local = _local_part(from_addr)
        for key, company in MYWORKDAY_LOCAL_TO_COMPANY.items():
            if key in local or local.startswith(key):
                return company
        if local and local not in ("noreply", "no-reply", "donotreply"):
            return local.replace(".", " ").replace("-", " ").replace("_", " ").title()

    # Direct match
    for d, c in DOMAIN_TO_COMPANY.items():
        if d in base or base.endswith("." + d):
            return c
    # ATS patterns (skip hire.lever.co, jobs.lever.co - company comes from body)
    if "lever.co" in base and base.startswith("hire."):
        pass
    else:
        for pattern, group in ATS_DOMAIN_PATTERNS:
            m = re.search(pattern, base)
            if m:
                name = m.group(group).replace("-", " ").title()
                if len(name) > 2 and name.lower() not in ("hire", "jobs", "careers"):
                    return name
    # jobs@, recruiting@, careers@ + domain → use domain as company
    if any(base.startswith(x) for x in ["jobs.", "recruiting.", "careers.", "talent."]):
        core = re.sub(r"^(jobs|recruiting|careers|talent)\.", "", base)
        core = core.split(".")[0] if "." in core else core
        if core and len(core) > 2:
            return core.replace("-", " ").title()
    # company.com (e.g. launchdarkly.com, brex.com) - use domain
    if base.count(".") >= 1 and not any(x in base for x in ["greenhouse", "lever", "workday", "ashby"]):
        first = base.split(".")[0]
        if first not in ("mail", "email", "no-reply", "noreply", "careers", "jobs", "hire", "us") and len(first) > 2:
            return first.replace("-", " ").title()
    return None


def _stage_from_text(text: str) -> str:
    """Detect stage from subject+body keywords (Kenza, track-app, jobseeker)."""
    t = (text or "").lower()
    rej = ["unfortunately", "not selected", "not moving forward", "declined", "we've decided",
           "other candidates", "position filled", "pursue other", "not be considered"]
    if any(x in t for x in rej):
        return "Rejected"
    if any(x in t for x in ["offer", "pleased to offer", "we'd like to extend"]):
        return "Offer"
    if any(x in t for x in ["interview", "phone screen", "onsite", "technical interview", "schedule a call", "video call"]):
        return "Interview Scheduled"
    if any(x in t for x in ["assessment", "coding challenge", "online test", "codesignal", "hackerrank"]):
        return "OA/Assessment"
    if any(x in t for x in ["application received", "we received your", "thank you for applying", "submitted successfully"]):
        return "Applied"
    return "Applied"


def _role_from_subject(subject: str) -> Optional[str]:
    """Extract role from common subject patterns (Kenza, track-app)."""
    s = (subject or "").strip()
    patterns = [
        r"thank\s+you\s+for\s+your\s+interest\s+[-–—]\s+([^,\n]+?)(?:\s*,\s*Summer\s+\d{4}|\s+\d{6,}|\s*$)",
        r"(?:thank you for your interest in|opening here at)\s+[\w\s]+:\s*([^.\n]{10,80})",
        r"(?:application|applied)\s+(?:for|to)\s+(?:the\s+)?(.+?)\s+(?:position\s+)?(?:at|@)",
        r"(.+?)\s+[-–—]\s+(?:application|applied)",
        r"update\s+for\s+REQ\d+\s+(.+?)(?:\s*$|!)",
        r"(?:position|role):\s*(.+?)(?:\s+at|\s*$)",
        r"(.+?)\s+(?:intern|engineer|developer|analyst|manager)\s*(?:position|role)?\s*(?:at|@)",
        r"your\s+application\s+for\s+(.+?)(?:\s+at|\s*$)",
        r"for\s+the\s+([\w\s,-]{5,60}?)\s+(?:role|position)",
        r"(?:role:\s*|position:\s*)([\w\s,-]{5,60}?)(?:\s+at|\s*[-–|]|$)",
        r"(?:we've got your)\s+[\w\s]+\s+application\s+[-–—]?\s*(.+?)(?:\s*$|!)",
        r"(software engineer|data engineer|product manager|ml engineer|machine learning|data scientist|backend|frontend|full.?stack|technical product management intern)",
    ]
    for p in patterns:
        m = re.search(p, s, re.IGNORECASE)
        if m:
            role = m.group(1).strip()
            if len(role) > 3 and len(role) < 100:
                role = re.sub(r"\s+\d{6,}\s*$", "", role).strip()
                return role if role else None
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
    company = _domain_to_company(domain, from_addr)

    # Need at least company from sender
    if not company:
        # Greenhouse/Lever: "Thanks for applying to X" or "application to X" in body
        if "greenhouse" in from_addr.lower() or "lever" in from_addr.lower() or "karat" in from_addr.lower():
            for p in [
                r"(?:thanks? for applying to|thank you for applying to)\s+([A-Za-z0-9\s&]+?)(?:\.|\.\s|Your|\s+Your)",
                r"opening\s+here\s+at\s+([A-Za-z0-9\s&]+?)(?:\s*\.|$|\s+Unfortunately)",
                r"application\s+to\s+([A-Z][a-zA-Z0-9\s&.-]{2,40})",
            ]:
                m = re.search(p, body, re.IGNORECASE)
                if m:
                    company = normalize_company(m.group(1).strip())
                    if company and len(company) > 2:
                        break
        if not company:
            m = re.search(r"(?:at|from|@)\s+([A-Z][a-zA-Z0-9\s&.-]{2,40}?)(?:\s+(?:for|–|-|\n)|\.)", body)
            if m:
                company = normalize_company(m.group(1))
        if not company:
            m = re.search(r"(?:for (?:the )?[\w\s]+ (?:position|role) at )([A-Za-z0-9\s&.-]{2,50})", body)
            if m:
                company = normalize_company(m.group(1))
        if not company:
            return None

    role = _role_from_subject(subject)
    if not role:
        body_patterns = [
            r"(?:interest in the)\s+([^.\n]{5,80}?)(?:\s+position\.|\s*\.)",
            r"([\w\s,-]+(?:intern|engineer|manager|analyst|developer|pm|product management)[\w\s,-]*)\s+opening\s+here\s+at",
            r"(?:applying for|application for|we received your application for|reviewing your application for)\s+([^.\n]{5,80}?)(?:\s+position|\s+at|\s+here|\s*$|\.)",
            r"(?:position|role):\s*([^\n,]{5,80})",
            r"([\w\s-]+(?:intern|engineer|manager|analyst|developer))(?:\s+position|\s+at|\s*$)",
            r"([\w\s&]+(?:intern|engineer|manager|analyst|developer)[\w\s,/-]*(?:Summer|Fall|Winter)[\s/]*\d{4})",
            r"([\w\s]+(?:Summer|Fall|Winter)\s+\d{4}\s+[-–]\s+[\w\s]+)",
            r"role of\s+([^\n.]{5,80})",
            r"(\d{4}\s+US Summer Internships\s+[-–]\s+[^\n]+)",
        ]
        for p in body_patterns:
            m = re.search(p, body, re.IGNORECASE)
            if m:
                role = m.group(1).strip()[:100]
                if len(role) > 5 and not any(
                    x in role.lower() for x in ("delighted", "interest", " by your", " we ", "thank you")
                ):
                    role = re.sub(r"\s+position\s*$", "", role).strip()
                    break
        if not role or len(role) < 5:
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
