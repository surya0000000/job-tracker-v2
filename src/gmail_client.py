"""Gmail API client for fetching job application emails."""

from dotenv import load_dotenv
from pathlib import Path
load_dotenv(Path(__file__).parent.parent / ".env")

import base64
import json
import os
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Iterator, Optional

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

import config


def get_gmail_credentials():
    """Get or refresh Gmail credentials. Supports both local files and env vars."""
    creds = None

    # Try environment variable first (GitHub Actions)
    creds_json = config.get_google_credentials()
    token_json = config.get_google_token()

    if creds_json and token_json:
        try:
            token_data = json.loads(token_json)
            creds = Credentials.from_authorized_user_info(token_data, config.GMAIL_SCOPES)
        except Exception:
            pass

    # Try local files (first-time setup)
    if not creds and config.CREDENTIALS_PATH.exists():
        flow = InstalledAppFlow.from_client_secrets_file(
            str(config.CREDENTIALS_PATH), config.GMAIL_SCOPES
        )
        creds = flow.run_local_server(port=0)

        # Save token for next run
        with open(config.TOKEN_PATH, "w") as f:
            f.write(creds.to_json())

    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())

    return creds


def get_gmail_service():
    """Build Gmail API service."""
    creds = get_gmail_credentials()
    if not creds:
        raise ValueError(
            "No credentials. Run locally first with credentials.json, "
            "or set GOOGLE_CREDENTIALS and GOOGLE_TOKEN secrets."
        )
    return build("gmail", "v1", credentials=creds)


# Gmail query: filter AT API level - fewer emails fetched (jobseeker-analytics)
def _build_gmail_filter_query(after_str: str) -> str:
    """Build Gmail search - only fetch emails likely to be job-related."""
    # (subject terms OR from domains) AND not spam
    q = f'after:{after_str} -in:trash (subject:application OR subject:applied OR subject:interview OR subject:assessment OR subject:offer OR subject:unfortunately OR from:greenhouse OR from:lever OR from:workday OR from:ashbyhq) -subject:newsletter -subject:"job alert"'
    return q


def build_search_query(months_back: int) -> str:
    """Build Gmail search query for the given time range."""
    after_date = datetime.utcnow() - timedelta(days=months_back * 30)
    after_str = after_date.strftime("%Y/%m/%d")
    return _build_gmail_filter_query(after_str)


def fetch_emails(
    months_back: int = config.INITIAL_SCAN_MONTHS,
    days_back: Optional[int] = None,
) -> Iterator[dict]:
    """
    Fetch emails from Gmail. Yields dicts with id, thread_id, subject, from, date, body.
    If days_back is set, use that for incremental scan; else use months_back.
    """
    service = get_gmail_service()

    if days_back is not None:
        after_date = datetime.utcnow() - timedelta(days=days_back)
        after_str = after_date.strftime("%Y/%m/%d")
        query = _build_gmail_filter_query(after_str)
    else:
        query = build_search_query(months_back or config.INITIAL_SCAN_MONTHS)

    # Fetch message IDs
    results = service.users().messages().list(
        userId="me",
        q=query,
        maxResults=500,
    ).execute()

    messages = results.get("messages", [])
    page_token = results.get("nextPageToken")

    while messages or page_token:
        for msg_ref in messages:
            try:
                msg = service.users().messages().get(
                    userId="me",
                    id=msg_ref["id"],
                    format="full",
                ).execute()

                headers = {h["name"].lower(): h["value"] for h in msg.get("payload", {}).get("headers", [])}
                subject = headers.get("subject", "")
                from_addr = headers.get("from", "")

                # Parse date
                date_str = headers.get("date", "")
                try:
                    dt = parsedate_to_datetime(date_str)
                    date_iso = dt.strftime("%Y-%m-%d")
                except Exception:
                    date_iso = datetime.utcnow().strftime("%Y-%m-%d")

                # Get body
                body = _extract_body(msg.get("payload", {}))

                yield {
                    "id": msg["id"],
                    "thread_id": msg.get("threadId", ""),
                    "subject": subject,
                    "from": from_addr,
                    "date": date_iso,
                    "body": body,
                }
            except Exception as e:
                # Log but continue
                _log_error(f"Failed to fetch email {msg_ref.get('id', '?')}: {e}")
                continue

        if not page_token:
            break

        results = service.users().messages().list(
            userId="me",
            q=query,
            maxResults=500,
            pageToken=page_token,
        ).execute()
        messages = results.get("messages", [])
        page_token = results.get("nextPageToken")


def _extract_body(payload: dict) -> str:
    """Extract plain text body from email payload."""
    if "body" in payload and payload["body"].get("data"):
        return base64.urlsafe_b64decode(
            payload["body"]["data"].encode()
        ).decode("utf-8", errors="replace")

    for part in payload.get("parts", []):
        if part.get("mimeType") == "text/plain" and part.get("body", {}).get("data"):
            return base64.urlsafe_b64decode(
                part["body"]["data"].encode()
            ).decode("utf-8", errors="replace")

    return ""


def _log_error(msg: str) -> None:
    """Log error to errors.log."""
    with open(config.ERRORS_LOG_PATH, "a") as f:
        f.write(f"[{datetime.utcnow().isoformat()}] {msg}\n")
