"""Job Application Tracker - Gmail to Google Sheets. Quota-friendly: rules first, AI only when needed."""

from dotenv import load_dotenv
from pathlib import Path
load_dotenv(Path(__file__).parent.parent / ".env")

import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import config
from src import database
from src import gmail_client
from src import pre_filter
from src import ai_parser
from src import rule_extractor
from src import deduplication
from src import sheets_sync


def _is_ci() -> bool:
    return os.environ.get("CI") == "true" and bool(config.get_spreadsheet_id())


def run_sync(is_initial: bool = False) -> dict:
    use_sheet = _is_ci()
    if not use_sheet:
        database.init_database()

    months = config.INITIAL_SCAN_MONTHS if is_initial else 0
    days = None if is_initial else config.DAILY_SCAN_DAYS

    spreadsheet_id = config.get_spreadsheet_id()
    if spreadsheet_id:
        print(f"Loaded SPREADSHEET_ID: {spreadsheet_id} from .env")
    else:
        spreadsheet_id = sheets_sync.create_new_spreadsheet()
        config.save_spreadsheet_id_to_env(spreadsheet_id)
        print(f"Created sheet, saved to .env: {spreadsheet_id}")

    if use_sheet:
        skip_ids = sheets_sync.read_processed_emails(spreadsheet_id)
        retry_ids = set()
    else:
        skip_ids = database.get_skip_forever_ids()
        retry_ids = database.get_retry_ids()
    print(f"Found {len(skip_ids)} emails to skip forever")
    print(f"Found {len(retry_ids)} emails to retry from previous run")
    if not use_sheet:
        prov = config.get_ai_provider()
        limit = config.GROQ_DAILY_QUOTA_LIMIT if prov == "groq" else config.GEMINI_DAILY_QUOTA_LIMIT
        print(f"AI: {prov.upper()} | Calls today: {database.get_daily_gemini_count()}/{limit}")

    print("Fetching Gmail...")
    emails = list(gmail_client.fetch_emails(months_back=months, days_back=days))
    to_process = [e for e in emails if e["id"] not in skip_ids]

    new_apps = 0
    updated = 0
    skipped = 0
    newly_processed = []  # For sheet mode

    if use_sheet:
        existing = sheets_sync.read_applications_from_sheet(spreadsheet_id)
        existing.sort(key=lambda a: a.get("last_updated", ""), reverse=True)
    else:
        existing = database.get_all_applications()

    for idx, email in enumerate(to_process):
        quota = config.GROQ_DAILY_QUOTA_LIMIT if config.get_ai_provider() == "groq" else config.GEMINI_DAILY_QUOTA_LIMIT
        if not use_sheet and database.get_daily_gemini_count() >= quota:
            print(f"\nDaily AI quota reached. Resume tomorrow.")
            break

        if pre_filter.pre_filter(email):
            skipped += 1
            if use_sheet:
                newly_processed.append(email["id"])
            else:
                database.mark_email_pre_filter_rejected(email["id"])
            continue

        # Try rule-based extraction first (NO AI)
        parsed = rule_extractor.try_extract(email)

        if not parsed:
            status, parsed = ai_parser.parse_email_with_ai(email)
            if status == "quota":
                print(f"\nDaily AI quota reached. Stopping. Resume tomorrow.")
                break
            if status in ("rate_limit_fail", "error"):
                if not use_sheet:
                    database.mark_email_ai_failed_rate_limit(email["id"])
                else:
                    newly_processed.append(email["id"])
                skipped += 1
                continue
            if status == "success" and parsed is None:
                parsed = rule_extractor.try_extract(email)
                if not parsed:
                    if use_sheet:
                        newly_processed.append(email["id"])
                    else:
                        database.mark_email_ai_completed(email["id"])
                    skipped += 1
                    continue

        if not parsed:
            skipped += 1
            continue

        date_applied = parsed.get("date") or email.get("date", "")[:10] or datetime.utcnow().strftime("%Y-%m-%d")
        app_type = "Internship" if parsed.get("is_internship") else "Full-time"
        company = deduplication.normalize_company(parsed["company"])
        role = parsed["role"]
        stage = parsed["stage"]
        notes = parsed.get("notes", "")

        match = deduplication.find_matching_application(company, role, existing)

        if match:
            cur = match["stage"]
            if deduplication.should_upgrade_stage(cur, stage):
                new_stage, new_notes = stage, notes
            else:
                new_stage = cur
                prev = match.get("notes") or ""
                new_notes = f"{prev}; {notes}".strip("; ") if prev else notes

            if use_sheet:
                for i, a in enumerate(existing):
                    if a.get("company") == match.get("company") and a.get("role") == match.get("role"):
                        existing[i] = {**a, "stage": new_stage, "notes": new_notes}
                        break
            else:
                database.update_application(match["id"], new_stage, new_notes)  # Immediate save
            updated += 1
        else:
            # New application: save to DB immediately (do not batch) — survives mid-run stops
            if use_sheet:
                now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
                new_app = {
                    "company": company, "role": role, "stage": stage, "type": app_type,
                    "date_applied": date_applied, "last_updated": now, "notes": notes,
                }
                existing.insert(0, new_app)
            else:
                database.upsert_application(company, role, stage, app_type, date_applied, notes)  # Immediate save
            new_apps += 1

        if not use_sheet:
            database.mark_email_ai_completed(email["id"])
            existing = database.get_all_applications()
        else:
            newly_processed.append(email["id"])

        if (idx + 1) % 5 == 0:
            print(f"Progress: {idx + 1} processed, {new_apps + updated} applications")

    if not use_sheet:
        database.log_sync(len(emails), new_apps, updated, skipped, skip_reasons="", is_initial_run=is_initial)

    if use_sheet:
        sheets_sync.append_processed_emails(spreadsheet_id, newly_processed)
        existing.sort(key=lambda a: a.get("last_updated", ""), reverse=True)
        logs = sheets_sync.read_sync_log_from_sheet(spreadsheet_id)
        entry = {
            "timestamp": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
            "emails_scanned": len(emails),
            "new_applications": new_apps,
            "statuses_updated": updated,
            "emails_skipped": skipped,
            "skip_reasons": "",
            "is_initial_run": is_initial,
        }
        sheets_sync.sync_all(spreadsheet_id, applications=existing, sync_logs_from_sheet=[entry] + logs[:99])
    else:
        sheets_sync.sync_all(spreadsheet_id)

    return {"spreadsheet_id": spreadsheet_id, "emails_scanned": len(emails), "new_applications": new_apps, "statuses_updated": updated, "emails_skipped": skipped}


def main():
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--initial", action="store_true")
    p.add_argument("--export", action="store_true")
    args = p.parse_args()

    if args.export:
        database.init_database()
        sid = sheets_sync.create_new_spreadsheet()
        config.save_spreadsheet_id_to_env(sid)
        sheets_sync.sync_all(sid)
        print(f"Recreated sheet: {sid}")
        print(f"URL: {sheets_sync.get_sheet_url(sid)}")
        return

    stats = run_sync(is_initial=args.initial)
    print("\n--- Done ---")
    print(f"Scanned: {stats['emails_scanned']}, New: {stats['new_applications']}, Updated: {stats['statuses_updated']}, Skipped: {stats['emails_skipped']}")
    print(f"Sheet: {sheets_sync.get_sheet_url(stats['spreadsheet_id'])}")


if __name__ == "__main__":
    main()
