"""Stage 3: Deduplication - normalize and match to prevent duplicate rows."""

import re
from typing import Optional

# Legal suffixes to strip
LEGAL_SUFFIXES = [
    r"\s+LLC\b",
    r"\s+Inc\.?\b",
    r"\s+Corp\.?\b",
    r"\s+Ltd\.?\b",
    r"\s+Co\.?\b",
    r"\s+L\.L\.C\.?\b",
    r"\s+Incorporated\b",
    r"\s+Corporation\b",
    r"\s+Limited\b",
    r"\s+PLC\b",
    r"\s+LLP\b",
    r"\s+LP\b",
]

# Company aliases (normalized key -> display name)
COMPANY_ALIASES = {
    "google": "Google",
    "meta platforms": "Meta",
    "meta": "Meta",
    "amazon.com services": "Amazon",
    "amazon": "Amazon",
    "amazon web services": "AWS",
    "aws": "AWS",
    "microsoft corporation": "Microsoft",
    "microsoft": "Microsoft",
    "apple inc": "Apple",
    "apple": "Apple",
    "alphabet": "Google",
    "jpmorgan chase": "JPMorgan",
    "j.p. morgan": "JPMorgan",
    "jpmorgan": "JPMorgan",
    "goldman sachs & co": "Goldman Sachs",
    "goldman sachs": "Goldman Sachs",
    "international business machines": "IBM",
    "ibm": "IBM",
}

# Role normalization: noise words to strip (for matching)
ROLE_NOISE = [
    "intern",
    "internship",
    "co-op",
    "coop",
    "full-time",
    "part-time",
    "fulltime",
    "parttime",
    "remote",
    "hybrid",
    "contract",
    "contractor",
    "i",
    "ii",
    "iii",
    "sr",
    "jr",
    "senior",
    "junior",
    "associate",
    "lead",
    "staff",
    "principal",
]

# Role equivalents for matching (first is canonical)
ROLE_EQUIVALENTS = [
    ("software engineer", "software developer", "swe", "software engineering"),
    ("software engineering intern", "software developer intern", "swe intern"),
    ("product management intern", "product manager intern", "pm intern"),
    ("data science intern", "data scientist intern"),
    ("machine learning", "ml"),
    ("artificial intelligence", "ai"),
    ("full stack", "fullstack", "full-stack"),
    ("front end", "frontend", "front-end"),
    ("back end", "backend", "back-end"),
]

STAGE_PRIORITY = {
    "Applied": 1,
    "In Review": 2,
    "OA/Assessment": 3,
    "Phone Screen": 4,
    "Interview Scheduled": 5,
    "Interviewed": 6,
    "Offer": 7,
    "Rejected": 8,
    "Withdrawn": 9,
}


def normalize_company(raw: str) -> str:
    """Normalize company name for matching. Returns display-ready string."""
    s = (raw or "").strip()
    for pattern in LEGAL_SUFFIXES:
        s = re.sub(pattern, "", s, flags=re.IGNORECASE)
    s = re.sub(r"\s+", " ", s).strip()
    key = s.lower()
    return COMPANY_ALIASES.get(key, s.title() if s else "")


def normalize_company_for_match(raw: str) -> str:
    """Normalize company for matching (lowercase key)."""
    s = normalize_company(raw)
    return s.lower().strip()


def normalize_role_for_match(raw: str) -> str:
    """Normalize role for matching. Strips noise, applies equivalents."""
    s = (raw or "").lower().strip()
    # Strip noise words
    words = s.split()
    words = [w for w in words if w not in ROLE_NOISE]
    s = " ".join(words)
    # Apply equivalents
    for equiv_group in ROLE_EQUIVALENTS:
        canonical = equiv_group[0]
        for variant in equiv_group[1:]:
            if variant in s or s in variant:
                s = canonical
                break
    return s


def role_token_overlap(a: str, b: str) -> float:
    """Compute token overlap between two normalized role strings. Returns 0-1."""
    tokens_a = set(normalize_role_for_match(a).split())
    tokens_b = set(normalize_role_for_match(b).split())
    if not tokens_a or not tokens_b:
        return 0.0
    intersection = len(tokens_a & tokens_b)
    union = len(tokens_a | tokens_b)
    return intersection / union if union else 0.0


def should_upgrade_stage(current: str, incoming: str) -> bool:
    """
    Determine if we should update to incoming stage.
    - Rejected and Withdrawn always apply (terminal)
    - Otherwise only upgrade to higher priority
    - Never overwrite Offer with anything except Withdrawn
    """
    curr_pri = STAGE_PRIORITY.get(current, 0)
    inc_pri = STAGE_PRIORITY.get(incoming, 0)

    if incoming in ("Rejected", "Withdrawn"):
        return True
    if current == "Offer" and incoming != "Withdrawn":
        return False
    return inc_pri > curr_pri


def find_matching_application(
    company: str,
    role: str,
    existing_apps: list[dict],
) -> Optional[dict]:
    """
    Find matching application in existing list.
    Uses: exact company match + 75% role token overlap.
    """
    company_norm = normalize_company_for_match(company)
    role_norm = normalize_role_for_match(role)

    for app in existing_apps:
        app_company_norm = normalize_company_for_match(app.get("company", ""))
        app_role_norm = normalize_role_for_match(app.get("role", ""))

        if company_norm != app_company_norm:
            continue

        if role_norm == app_role_norm:
            return app

        overlap = role_token_overlap(role, app.get("role", ""))
        if overlap >= 0.75:
            return app

    return None
