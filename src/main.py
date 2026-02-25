"""Main sync orchestration - runs the full pipeline."""

import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

import config
from src import database
from src import gmail_client
from src import pre_filter
from src import ai_parser
from src import deduplication
from src import sheets_sync


def log_error(msg: str) -> None:
    """Log to errors.log."""
    with open(config.ERRORS_LOG_PATH, "a") as f:
        f.write(f"[{datetime.utcnow().isoformat()}] {msg}\n")


def _is_ci_mode() -> bool:
    """Detect if running in GitHub Actions (use Sheet as source of truth)."""
    return os.environ.get("CI") == "true" and bool(config.get_spreadsheet_id())


def run_sync(is_initial: bool = False) -> dict:
    """
    Run full sync pipeline. Returns stats dict.
    """
    use_sheet_mode = _is_ci_mode()
    if not use_sheet_mode:
        database.init_database()

    # Determine scan window
    if is_initial:
        months_back = config.INITIAL_SCAN_MONTHS
        days_back = None
    else:
        months_back = None
        days_back = config.DAILY_SCAN_DAYS

    # Get or create spreadsheet
    spreadsheet_id = config.get_spreadsheet_id()
    if not spreadsheet_id:
        spreadsheet_id = sheets_sync.create_new_spreadsheet()
        print(f"\n>>> CREATED NEW SPREADSHEET <<<")
        print(f"Add this to GitHub Secrets as SPREADSHEET_ID: {spreadsheet_id}")

    # Get processed email IDs (from Sheet in CI, from DB locally)
    if use_sheet_mode:
        processed_ids = sheets_sync.read_processed_emails(spreadsheet_id)
    else:
        processed_ids = None  # Will check DB per-email

    # Fetch emails
    emails = list(gmail_client.fetch_emails(months_back=months_back or 0, days_back=days_back))
    emails_scanned = len(emails)

    # Filter out already processed
    to_process = []
    newly_processed_ids = []
    for email in emails:
        if use_sheet_mode:
            if email["id"] not in processed_ids:
                to_process.append(email)
        else:
            if not database.is_email_processed(email["id"]):
                to_process.append(email)

    new_applications = 0
    statuses_updated = 0
    emails_skipped = 0
    skip_reasons = []

    # Get existing applications (from Sheet in CI, from DB locally)
    if use_sheet_mode:
        existing_apps = sheets_sync.read_applications_from_sheet(spreadsheet_id)
        # Sort by last_updated desc for display
        existing_apps.sort(key=lambda a: a.get("last_updated", ""), reverse=True)
    else:
        existing_apps = database.get_all_applications()

    for idx, email in enumerate(to_process):
        # Rate limit: 2 second pause every 3 emails (AI batch pacing)
        if idx > 0 and idx % 3 == 0:
            time.sleep(2)

        # Stage 1: Pre-filter
        reject_reason = pre_filter.pre_filter(email)
        if reject_reason:
            emails_skipped += 1
            skip_reasons.append(f"pre-filter: {reject_reason[:80]}")
            if use_sheet_mode:
                newly_processed_ids.append(email["id"])
            else:
                database.mark_email_processed(email["id"])
            continue

        # Stage 2: AI parsing
        parsed = ai_parser.parse_email_with_ai(email)
        if not parsed:
            emails_skipped += 1
            skip_reasons.append(f"ai-skip: {email.get('id', '?')}")
            if use_sheet_mode:
                newly_processed_ids.append(email["id"])
            else:
                database.mark_email_processed(email["id"])
            continue

        # Use email date if AI didn't return valid date
        date_applied = parsed.get("date") or email.get("date", "")
        if not date_applied:
            date_applied = datetime.utcnow().strftime("%Y-%m-%d")

        app_type = "Internship" if parsed.get("is_internship") else "Full-time"
        company = deduplication.normalize_company(parsed["company"])
        role = parsed["role"]  # Keep original for display
        stage = parsed["stage"]
        notes = parsed.get("notes", "")

        # Stage 3: Deduplication
        match = deduplication.find_matching_application(company, role, existing_apps)

        if match:
            # Update existing
            current_stage = match["stage"]
            if deduplication.should_upgrade_stage(current_stage, stage):
                new_stage = stage
                new_notes = notes
            else:
                new_stage = current_stage  # Don't downgrade
                existing_notes = match.get("notes") or ""
                new_notes = f"{existing_notes}; {notes}".strip("; ") if existing_notes else notes

            if use_sheet_mode:
                for i, app in enumerate(existing_apps):
                    if app.get("company") == match.get("company") and app.get("role") == match.get("role"):
                        existing_apps[i] = {**app, "stage": new_stage, "notes": new_notes}
                        break
            else:
                database.update_application(match["id"], new_stage, new_notes)
            statuses_updated += 1
        else:
            now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
            new_app = {
                "company": company,
                "role": role,
                "stage": stage,
                "type": app_type,
                "date_applied": date_applied,
                "last_updated": now,
                "notes": notes,
            }
            if use_sheet_mode:
                existing_apps.insert(0, new_app)
            else:
                database.upsert_application(
                    company=company,
                    role=role,
                    stage=stage,
                    app_type=app_type,
                    date_applied=date_applied,
                    notes=notes,
                )
            new_applications += 1

        if use_sheet_mode:
            newly_processed_ids.append(email["id"])
        else:
            database.mark_email_processed(email["id"])
            existing_apps = database.get_all_applications()  # Refresh for next iteration

    # Log sync
    skip_reasons_str = "\n".join(skip_reasons[:50])  # Limit length
    sync_log_entry = {
        "timestamp": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
        "emails_scanned": emails_scanned,
        "new_applications": new_applications,
        "statuses_updated": statuses_updated,
        "emails_skipped": emails_skipped,
        "skip_reasons": skip_reasons_str,
        "is_initial_run": is_initial,
    }
    if not use_sheet_mode:
        database.log_sync(
            emails_scanned=emails_scanned,
            new_applications=new_applications,
            statuses_updated=statuses_updated,
            emails_skipped=emails_skipped,
            skip_reasons=skip_reasons_str,
            is_initial_run=is_initial,
        )

    # Sync to Google Sheet
    if use_sheet_mode:
        sheets_sync.append_processed_emails(spreadsheet_id, newly_processed_ids)
        existing_apps.sort(key=lambda a: a.get("last_updated", ""), reverse=True)
        logs = sheets_sync.read_sync_log_from_sheet(spreadsheet_id)
        logs_with_new = [sync_log_entry] + logs[:99]
        sheets_sync.sync_all(
            spreadsheet_id,
            applications=existing_apps,
            sync_logs_from_sheet=logs_with_new,
        )
    else:
        sheets_sync.sync_all(spreadsheet_id)

    return {
        "spreadsheet_id": spreadsheet_id,
        "emails_scanned": emails_scanned,
        "new_applications": new_applications,
        "statuses_updated": statuses_updated,
        "emails_skipped": emails_skipped,
    }


def main() -> None:
    """CLI entry point."""
    import argparse
    parser = argparse.ArgumentParser(description="Job Application Tracker Sync")
    parser.add_argument(
        "--initial",
        action="store_true",
        help="Initial run: scan 8 months, create sheet if needed",
    )
    parser.add_argument(
        "--export",
        action="store_true",
        help="Export/recreate Google Sheet from local database (use if sheet was deleted)",
    )
    args = parser.parse_args()

    # Export mode: recreate sheet from DB (use when sheet was deleted)
    if args.export:
        database.init_database()
        spreadsheet_id = sheets_sync.create_new_spreadsheet()
        sheets_sync.sync_all(spreadsheet_id)
        print(f"\n>>> RECREATED SPREADSHEET <<<")
        print(f"Update GitHub Secret SPREADSHEET_ID to: {spreadsheet_id}")
        _print_urls(spreadsheet_id)
        return

    # Normal sync
    is_initial = args.initial
    stats = run_sync(is_initial=is_initial)

    print("\n--- Sync Complete ---")
    print(f"Emails scanned: {stats['emails_scanned']}")
    print(f"New applications: {stats['new_applications']}")
    print(f"Statuses updated: {stats['statuses_updated']}")
    print(f"Emails skipped: {stats['emails_skipped']}")
    _print_urls(stats["spreadsheet_id"])


def _print_urls(spreadsheet_id: str) -> None:
    """Print sheet URL and Excel download link."""
    sheet_url = sheets_sync.get_sheet_url(spreadsheet_id)
    excel_url = sheets_sync.get_excel_download_url(spreadsheet_id)
    print(f"\nGoogle Sheet: {sheet_url}")
    print(f"Excel Download: {excel_url}")


if __name__ == "__main__":
    main()
