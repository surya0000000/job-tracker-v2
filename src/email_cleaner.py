"""Email body cleaning - reduce tokens 60-80% (from track-app, jobseeker-analytics)."""

import re
from typing import Optional

MAX_BODY_WORDS = 400  # Truncate to save tokens


def clean_body(body: Optional[str]) -> str:
    """
    Strip HTML, footers, quoted content. Reduces token usage.
    """
    if not body:
        return ""

    text = body

    # Strip HTML tags (regex - no BeautifulSoup dependency)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"&amp;", "&", text)
    text = re.sub(r"&lt;", "<", text)
    text = re.sub(r"&gt;", ">", text)

    lines = text.split("\n")
    cleaned = []

    for line in lines:
        line = line.strip()
        if not line:
            continue
        if line.startswith(">") or line.startswith("|"):
            continue
        if re.match(r"^On .+ wrote:?$", line):
            break
        # Skip lines that are only footers (don't strip lines that contain useful content)
        footer_only = [
            r"^unsubscribe\s*$", r"^privacy policy\s*$", r"^terms of service\s*$",
            r"^all rights reserved\s*$", r"^manage your email preferences\s*$",
        ]
        if len(line) < 100 and any(re.search(p, line, re.IGNORECASE) for p in footer_only):
            continue
        cleaned.append(line)

    result = "\n".join(cleaned)
    result = re.sub(r"\n{3,}", "\n\n", result)
    result = re.sub(r" {2,}", " ", result)

    words = result.split()
    if len(words) > MAX_BODY_WORDS:
        result = " ".join(words[:MAX_BODY_WORDS]) + "\n[...truncated...]"

    return result.strip()
