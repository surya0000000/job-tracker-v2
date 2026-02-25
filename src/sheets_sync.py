"""Google Sheets sync - 3 tabs: Applications, Summary, Sync Log."""

import json
import os
from datetime import datetime
from typing import Optional

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

import config
from src import database


# Stage colors (hex for Google Sheets)
STAGE_COLORS = {
    "Applied": "#E8F0FE",          # Light blue
    "In Review": "#D2E3FC",
    "OA/Assessment": "#A8DADC",     # Teal
    "Phone Screen": "#FEF3C7",      # Yellow
    "Interview Scheduled": "#FDE68A",
    "Interviewed": "#FCD34D",
    "Offer": "#86EFAC",             # Green
    "Rejected": "#FCA5A5",          # Red
    "Withdrawn": "#D1D5DB",         # Gray
}


def get_sheets_credentials():
    """Get credentials for Sheets API (same as Gmail)."""
    creds = None
    creds_json = config.get_google_credentials()
    token_json = config.get_google_token()

    if creds_json and token_json:
        try:
            token_data = json.loads(token_json)
            creds = Credentials.from_authorized_user_info(token_data, config.GMAIL_SCOPES)
        except Exception:
            pass

    if not creds and config.CREDENTIALS_PATH.exists() and config.TOKEN_PATH.exists():
        creds = Credentials.from_authorized_user_file(str(config.TOKEN_PATH), config.GMAIL_SCOPES)

    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())

    return creds


def get_sheets_service():
    """Build Sheets API service."""
    creds = get_sheets_credentials()
    if not creds:
        raise ValueError("No credentials for Sheets API")
    return build("sheets", "v4", credentials=creds)


def create_new_spreadsheet(title: str = "Job Application Tracker") -> str:
    """Create new Google Sheet, return spreadsheet ID."""
    service = get_sheets_service()

    spreadsheet = {
        "properties": {"title": title},
        "sheets": [
            {"properties": {"title": "Applications", "gridProperties": {"frozenRowCount": 1}}},
            {"properties": {"title": "Summary", "gridProperties": {"frozenRowCount": 1}}},
            {"properties": {"title": "Sync Log", "gridProperties": {"frozenRowCount": 1}}},
            {"properties": {"title": "ProcessedEmails", "hidden": True}},  # Internal: processed email IDs
        ],
    }

    sheet = service.spreadsheets().create(body=spreadsheet).execute()
    spreadsheet_id = sheet["spreadsheetId"]
    # Sheet is private by default — only the authenticated user (owner) can access
    return spreadsheet_id


def _sync_applications_from_data(spreadsheet_id: str, apps: list[dict]) -> None:
    """Sync applications tab from provided data (for CI mode)."""
    _write_applications(spreadsheet_id, apps)


def _sync_summary_from_data(spreadsheet_id: str, apps: list[dict]) -> None:
    """Sync summary tab from provided data (for CI mode)."""
    service = get_sheets_service()
    total = len(apps)
    terminal = [a for a in apps if a["stage"] in ("Rejected", "Withdrawn", "Offer")]
    active = total - len(terminal)
    offers = len([a for a in apps if a["stage"] == "Offer"])
    rejected = len([a for a in apps if a["stage"] == "Rejected"])
    interviewed = len([a for a in apps if a["stage"] in ("Interviewed", "Offer")])
    interview_rate = (interviewed / total * 100) if total else 0
    offer_rate = (offers / total * 100) if total else 0
    rejection_rate = (rejected / total * 100) if total else 0
    stage_counts = {}
    for app in apps:
        s = app["stage"]
        stage_counts[s] = stage_counts.get(s, 0) + 1
    stage_order = [
        "Applied", "In Review", "OA/Assessment", "Phone Screen",
        "Interview Scheduled", "Interviewed", "Offer", "Rejected", "Withdrawn",
    ]
    stage_rows = [["Stage", "Count", "Bar"]]
    max_count = max(stage_counts.values()) if stage_counts else 1
    for stage in stage_order:
        count = stage_counts.get(stage, 0)
        bar = "█" * int(count / max_count * 20) if max_count else ""
        stage_rows.append([stage, count, bar])
    from collections import defaultdict
    monthly = defaultdict(int)
    for app in apps:
        date = app.get("date_applied", "")[:7]
        if date:
            monthly[date] += 1
    monthly_rows = [["Month", "Applications"]]
    for month in sorted(monthly.keys(), reverse=True)[:12]:
        monthly_rows.append([month, monthly[month]])
    rows = [
        ["Job Application Tracker - Summary"], [],
        ["Total Applications", total],
        ["Active Pipeline", active],
        ["Interview Rate", f"{interview_rate:.1f}%"],
        ["Offer Rate", f"{offer_rate:.1f}%"],
        ["Rejection Rate", f"{rejection_rate:.1f}%"],
        [], ["Breakdown by Stage"],
    ]
    rows.extend(stage_rows)
    rows.append([])
    rows.append(["Monthly Breakdown"])
    rows.extend(monthly_rows)
    service.spreadsheets().values().clear(
        spreadsheetId=spreadsheet_id, range="Summary!A1:C",
    ).execute()
    service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id, range="Summary!A1",
        valueInputOption="USER_ENTERED", body={"values": rows},
    ).execute()


def _write_applications(spreadsheet_id: str, apps: list[dict]) -> None:
    """Write applications to sheet with colors."""
    service = get_sheets_service()
    headers = ["Company", "Role", "Stage", "Type", "Date Applied", "Last Updated", "Notes"]
    rows = [headers]
    for app in apps:
        rows.append([
            app.get("company", ""),
            app.get("role", ""),
            app.get("stage", ""),
            app.get("type", ""),
            app.get("date_applied", ""),
            app.get("last_updated", ""),
            app.get("notes") or "",
        ])
    range_name = "Applications!A1:G"
    service.spreadsheets().values().clear(
        spreadsheetId=spreadsheet_id, range=range_name,
    ).execute()
    if rows:
        service.spreadsheets().values().update(
            spreadsheetId=spreadsheet_id, range=range_name,
            valueInputOption="USER_ENTERED", body={"values": rows},
        ).execute()
    # Apply colors
    def hex_to_rgb(hex_str):
        hex_str = hex_str.lstrip("#")
        return tuple(int(hex_str[i:i+2], 16) / 255 for i in (0, 2, 4))
    if len(rows) > 1:
        color_requests = []
        for i, app in enumerate(apps):
            row_idx = i + 2
            hex_color = STAGE_COLORS.get(app.get("stage", ""), "#FFFFFF")
            r, g, b = hex_to_rgb(hex_color)
            color_requests.append({
                "repeatCell": {
                    "range": {"sheetId": 0, "startRowIndex": row_idx - 1, "endRowIndex": row_idx},
                    "cell": {"userEnteredFormat": {"backgroundColor": {"red": r, "green": g, "blue": b}}},
                    "fields": "userEnteredFormat.backgroundColor",
                }
            })
        if color_requests:
            service.spreadsheets().batchUpdate(
                spreadsheetId=spreadsheet_id, body={"requests": color_requests},
            ).execute()


def sync_applications_tab(spreadsheet_id: str) -> None:
    """Sync Tab 1: All applications, color coded, sorted by last_updated DESC."""
    service = get_sheets_service()
    apps = database.get_all_applications()

    _write_applications(spreadsheet_id, apps)


def sync_summary_tab(spreadsheet_id: str) -> None:
    """Sync Tab 2: Summary dashboard."""
    service = get_sheets_service()
    apps = database.get_all_applications()

    total = len(apps)
    terminal = [a for a in apps if a["stage"] in ("Rejected", "Withdrawn", "Offer")]
    active = total - len(terminal)
    offers = len([a for a in apps if a["stage"] == "Offer"])
    rejected = len([a for a in apps if a["stage"] == "Rejected"])
    interviewed = len([a for a in apps if a["stage"] in ("Interviewed", "Offer")])

    interview_rate = (interviewed / total * 100) if total else 0
    offer_rate = (offers / total * 100) if total else 0
    rejection_rate = (rejected / total * 100) if total else 0

    # Stage breakdown
    stage_counts = {}
    for app in apps:
        s = app["stage"]
        stage_counts[s] = stage_counts.get(s, 0) + 1

    stage_order = [
        "Applied", "In Review", "OA/Assessment", "Phone Screen",
        "Interview Scheduled", "Interviewed", "Offer", "Rejected", "Withdrawn",
    ]
    stage_rows = [["Stage", "Count", "Bar"]]
    max_count = max(stage_counts.values()) if stage_counts else 1
    for stage in stage_order:
        count = stage_counts.get(stage, 0)
        bar = "█" * int(count / max_count * 20) if max_count else ""
        stage_rows.append([stage, count, bar])

    # Monthly breakdown
    from collections import defaultdict
    monthly = defaultdict(int)
    for app in apps:
        date = app.get("date_applied", "")[:7]  # YYYY-MM
        if date:
            monthly[date] += 1
    monthly_rows = [["Month", "Applications"]]
    for month in sorted(monthly.keys(), reverse=True)[:12]:
        monthly_rows.append([month, monthly[month]])

    # Build summary tab
    rows = [
        ["Job Application Tracker - Summary"],
        [],
        ["Total Applications", total],
        ["Active Pipeline", active],
        ["Interview Rate", f"{interview_rate:.1f}%"],
        ["Offer Rate", f"{offer_rate:.1f}%"],
        ["Rejection Rate", f"{rejection_rate:.1f}%"],
        [],
        ["Breakdown by Stage"],
    ]
    rows.extend(stage_rows)
    rows.append([])
    rows.append(["Monthly Breakdown"])
    rows.extend(monthly_rows)

    range_name = "Summary!A1:C"
    service.spreadsheets().values().clear(
        spreadsheetId=spreadsheet_id,
        range=range_name,
    ).execute()
    service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id,
        range="Summary!A1",
        valueInputOption="USER_ENTERED",
        body={"values": rows},
    ).execute()


def read_sync_log_from_sheet(spreadsheet_id: str) -> list[dict]:
    """Read sync log from Sheet (for CI mode)."""
    service = get_sheets_service()
    try:
        result = service.spreadsheets().values().get(
            spreadsheetId=spreadsheet_id, range="Sync Log!A2:G",
        ).execute()
        values = result.get("values", [])
        logs = []
        for row in values:
            if len(row) >= 6:
                logs.append({
                    "timestamp": row[0] if len(row) > 0 else "",
                    "emails_scanned": int(row[1]) if len(row) > 1 and str(row[1]).isdigit() else 0,
                    "new_applications": int(row[2]) if len(row) > 2 and str(row[2]).isdigit() else 0,
                    "statuses_updated": int(row[3]) if len(row) > 3 and str(row[3]).isdigit() else 0,
                    "emails_skipped": int(row[4]) if len(row) > 4 and str(row[4]).isdigit() else 0,
                    "skip_reasons": row[5] if len(row) > 5 else "",
                    "is_initial_run": str(row[6]).lower() == "yes" if len(row) > 6 else False,
                })
        return logs
    except Exception:
        return []


def sync_log_tab(spreadsheet_id: str, new_entry: dict | None = None, logs_from_sheet: list[dict] | None = None) -> None:
    """Sync Tab 3: Sync log. If logs_from_sheet provided (CI), use those; else from DB."""
    service = get_sheets_service()
    if logs_from_sheet is not None:
        logs = logs_from_sheet
    else:
        logs = database.get_sync_logs(limit=100)
    if new_entry:
        logs = [new_entry] + logs[:99]  # Prepend new, keep last 99
    rows = [["Timestamp", "Emails Scanned", "New Apps", "Status Updates", "Skipped", "Skip Reasons", "Initial Run"]]
    for log in logs[:100]:
        rows.append([
            log.get("timestamp", ""),
            log.get("emails_scanned", 0),
            log.get("new_applications", 0),
            log.get("statuses_updated", 0),
            log.get("emails_skipped", 0),
            (log.get("skip_reasons") or "")[:500],
            "Yes" if log.get("is_initial_run") else "No",
        ])
    range_name = "Sync Log!A1:G"
    service.spreadsheets().values().clear(
        spreadsheetId=spreadsheet_id, range=range_name,
    ).execute()
    if rows:
        service.spreadsheets().values().update(
            spreadsheetId=spreadsheet_id, range=range_name,
            valueInputOption="USER_ENTERED", body={"values": rows},
        ).execute()


def _ensure_processed_emails_tab(spreadsheet_id: str) -> None:
    """Add ProcessedEmails tab if it doesn't exist (for sheets created before this was added)."""
    service = get_sheets_service()
    meta = service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
    titles = [s["properties"]["title"] for s in meta.get("sheets", [])]
    if "ProcessedEmails" not in titles:
        service.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={"requests": [{"addSheet": {"properties": {"title": "ProcessedEmails", "hidden": True}}}]},
        ).execute()


def read_processed_emails(spreadsheet_id: str) -> set[str]:
    """Read processed email IDs from hidden ProcessedEmails tab."""
    service = get_sheets_service()
    try:
        result = service.spreadsheets().values().get(
            spreadsheetId=spreadsheet_id,
            range="ProcessedEmails!A:A",
        ).execute()
        values = result.get("values", [])
        return {str(row[0]).strip() for row in values if row and str(row[0]).strip()}
    except Exception:
        _ensure_processed_emails_tab(spreadsheet_id)
        return set()


def append_processed_emails(spreadsheet_id: str, email_ids: list[str]) -> None:
    """Append new processed email IDs to ProcessedEmails tab."""
    if not email_ids:
        return
    _ensure_processed_emails_tab(spreadsheet_id)
    service = get_sheets_service()
    rows = [[eid] for eid in email_ids]
    service.spreadsheets().values().append(
        spreadsheetId=spreadsheet_id,
        range="ProcessedEmails!A:A",
        valueInputOption="RAW",
        insertDataOption="INSERT_ROWS",
        body={"values": rows},
    ).execute()


def read_applications_from_sheet(spreadsheet_id: str) -> list[dict]:
    """Read applications from Sheet (for CI when no local DB)."""
    service = get_sheets_service()
    try:
        result = service.spreadsheets().values().get(
            spreadsheetId=spreadsheet_id,
            range="Applications!A2:G",
        ).execute()
        values = result.get("values", [])
        apps = []
        for row in values:
            if len(row) >= 6:
                apps.append({
                    "id": len(apps) + 1,  # Dummy id for matching
                    "company": row[0] if len(row) > 0 else "",
                    "role": row[1] if len(row) > 1 else "",
                    "stage": row[2] if len(row) > 2 else "",
                    "type": row[3] if len(row) > 3 else "Full-time",
                    "date_applied": row[4] if len(row) > 4 else "",
                    "last_updated": row[5] if len(row) > 5 else "",
                    "notes": row[6] if len(row) > 6 else "",
                })
        return apps
    except Exception:
        return []


def sync_all(
    spreadsheet_id: str,
    applications: list[dict] | None = None,
    sync_log_entry: dict | None = None,
    sync_logs_from_sheet: list[dict] | None = None,
) -> None:
    """Sync all 3 tabs to Google Sheet. If applications provided, use those; else from DB."""
    if applications is not None:
        _sync_applications_from_data(spreadsheet_id, applications)
        _sync_summary_from_data(spreadsheet_id, applications)
    else:
        sync_applications_tab(spreadsheet_id)
        sync_summary_tab(spreadsheet_id)
    sync_log_tab(spreadsheet_id, new_entry=sync_log_entry, logs_from_sheet=sync_logs_from_sheet)


def get_sheet_url(spreadsheet_id: str) -> str:
    """Get view URL for the sheet."""
    return f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}"


def get_excel_download_url(spreadsheet_id: str) -> str:
    """Get Excel download URL."""
    return f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/export?format=xlsx"
