"""Main sync orchestration - runs the full pipeline."""

from dotenv import load_dotenv
from pathlib import Path
load_dotenv(Path(__file__).parent.parent / ".env")

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
    with open(config.ERRORS_LOG_PATH, "a") as f:
        f.write(f"[{datetime.utcnow().isoformat()}] {msg}\n")


def _is_ci_mode() -> bool:
    return os.environ.get("CI") == "true" and bool(config.get_spreadsheet_id())


def run_sync(is_initial: bool = False) -> dict:
    use_sheet_mode = _is_ci_mode()
    if not use_sheet_mode:
        database.init_database()

    if is_initial:
        months_back = config.INITIAL_SCAN_MONTHS
        days_back = None
    else:
        months_back = None
        days_back = config.DAILY_SCAN_DAYS

    spreadsheet_id = config.get_spreadsheet_id()
    if spreadsheet_id:
        print(f"Loaded SPREADSHEET_ID: {spreadsheet_id} from .env")
    if not spreadsheet_id:
        spreadsheet_id = sheets_sync.create_new_spreadsheet()
        config.save_spreadsheet_id_to_env(spreadsheet_id)
        print(f"\n>>> CREATED NEW SPREADSHEET <<<")
        print(f"Saved SPREADSHEET_ID to .env: {spreadsheet_id}")

    if use_sheet_mode:
        skip_forever_ids = sheets_sync.read_processed_emails(spreadsheet_id)
        print(f"Found {len(skip_forever_ids)} emails to skip forever")
        print(f"Found 0 emails to retry from previous run")
        print(f"Gemini API calls today: N/A (Sheet mode)")
    else:
        skip_forever_ids = database.get_skip_forever_ids()
        retry_ids = database.get_retry_ids()
        daily_count = database.get_daily_gemini_count()
        print(f"Found {len(skip_forever_ids)} emails to skip forever")
        print(f"Found {len(retry_ids)} emails to retry from previous run")
        print(f"Gemini API calls today: {daily_count}/{config.GEMINI_DAILY_QUOTA_LIMIT}")

    print("Starting Gmail fetch...")
    emails = list(gmail_client.fetch_emails(months_back=months_back or 0, days_back=days_back))
    emails_scanned = len(emails)

    to_process = [e for e in emails if e["id"] not in skip_forever_ids]
    newly_processed_ids = []
    new_applications = 0
    statuses_updated = 0
    emails_skipped = 0
    skip_reasons = []

    if use_sheet_mode:
        existing_apps = sheets_sync.read_applications_from_sheet(spreadsheet_id)
        existing_apps.sort(key=lambda a: a.get("last_updated", ""), reverse=True)
    else:
        existing_apps = database.get_all_applications()

    for idx, email in enumerate(to_process):
        if idx > 0 and idx % 3 == 0:
            print(f"Progress: {idx + 1} emails processed, {new_applications + statuses_updated} applications found so far")

        if not use_sheet_mode:
            if database.get_daily_gemini_count() >= config.GEMINI_DAILY_QUOTA_LIMIT:
                print(f"\nDaily Gemini quota nearly reached ({config.GEMINI_DAILY_QUOTA_LIMIT}/1500). Stopping. Resume tomorrow.")
                break

        reject_reason = pre_filter.pre_filter(email)
        if reject_reason:
            emails_skipped += 1
            skip_reasons.append(f"pre-filter: {reject_reason[:80]}")
            if use_sheet_mode:
                newly_processed_ids.append(email["id"])
            else:
                database.mark_email_pre_filter_rejected(email["id"])
            continue

        status, parsed = ai_parser.parse_email_with_ai(email)

        if status == "quota":
            print(f"\nDaily Gemini quota nearly reached ({config.GEMINI_DAILY_QUOTA_LIMIT}/1500). Stopping. Resume tomorrow.")
            break

        if status == "rate_limit_fail":
            emails_skipped += 1
            skip_reasons.append(f"ai-rate-limit-fail: {email.get('id', '?')}")
            if use_sheet_mode:
                newly_processed_ids.append(email["id"])
            else:
                database.mark_email_ai_failed_rate_limit(email["id"])
            continue

        if status == "error":
            emails_skipped += 1
            skip_reasons.append(f"ai-error: {email.get('id', '?')}")
            if use_sheet_mode:
                newly_processed_ids.append(email["id"])
            else:
                database.mark_email_ai_failed_rate_limit(email["id"])
            continue

        if status == "success" and parsed is None:
            emails_skipped += 1
            skip_reasons.append(f"ai-null: {email.get('id', '?')}")
            if use_sheet_mode:
                newly_processed_ids.append(email["id"])
            else:
                database.mark_email_ai_completed(email["id"])
            continue

        if status != "success" or not parsed:
            continue

        date_applied = parsed.get("date") or email.get("date", "") or datetime.utcnow().strftime("%Y-%m-%d")
        app_type = "Internship" if parsed.get("is_internship") else "Full-time"
        company = deduplication.normalize_company(parsed["company"])
        role = parsed["role"]
        stage = parsed["stage"]
        notes = parsed.get("notes", "")

        match = deduplication.find_matching_application(company, role, existing_apps)

        if match:
            current_stage = match["stage"]
            if deduplication.should_upgrade_stage(current_stage, stage):
                new_stage = stage
                new_notes = notes
            else:
                new_stage = current_stage
                existing_notes = match.get("notes") or ""
                new_notes = f"{existing_notes}; {notes}".strip("; ") if existing_notes else notes

            print(f"DEDUP MATCH: {company}/{role} matched existing {match.get('company')}/{match.get('role')}")
            print(f"UPDATED: company={company} stage {current_stage}→{new_stage}")

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
                    company=company, role=role, stage=stage,
                    app_type=app_type, date_applied=date_applied, notes=notes,
                )
            print(f"NEW APP: company={company} role={role} stage={stage}")
            new_applications += 1

        if use_sheet_mode:
            newly_processed_ids.append(email["id"])
        else:
            database.mark_email_ai_completed(email["id"])
            existing_apps = database.get_all_applications()

    skip_reasons_str = "\n".join(skip_reasons[:50])
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
    import argparse
    parser = argparse.ArgumentParser(description="Job Application Tracker Sync")
    parser.add_argument("--initial", action="store_true", help="Initial run: scan 8 months")
    parser.add_argument("--export", action="store_true", help="Recreate Google Sheet from DB")
    args = parser.parse_args()

    if args.export:
        database.init_database()
        spreadsheet_id = sheets_sync.create_new_spreadsheet()
        config.save_spreadsheet_id_to_env(spreadsheet_id)
        sheets_sync.sync_all(spreadsheet_id)
        print(f"\n>>> RECREATED SPREADSHEET <<<")
        print(f"Saved SPREADSHEET_ID to .env: {spreadsheet_id}")
        _print_urls(spreadsheet_id)
        return

    is_initial = args.initial
    stats = run_sync(is_initial=is_initial)

    print("\n--- Sync Complete ---")
    print(f"Emails scanned: {stats['emails_scanned']}")
    print(f"New applications: {stats['new_applications']}")
    print(f"Statuses updated: {stats['statuses_updated']}")
    print(f"Emails skipped: {stats['emails_skipped']}")
    _print_urls(stats["spreadsheet_id"])


def _print_urls(spreadsheet_id: str) -> None:
    print(f"\nGoogle Sheet: {sheets_sync.get_sheet_url(spreadsheet_id)}")
    print(f"Excel Download: {sheets_sync.get_excel_download_url(spreadsheet_id)}")


if __name__ == "__main__":
    main()
