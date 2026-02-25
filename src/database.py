"""SQLite database for applications and processed email tracking."""

from dotenv import load_dotenv
from pathlib import Path
load_dotenv(Path(__file__).parent.parent / ".env")

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

import config


def get_connection() -> sqlite3.Connection:
    """Get database connection."""
    conn = sqlite3.connect(config.DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_database() -> None:
    """Create tables if they don't exist."""
    conn = get_connection()
    try:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS applications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                company TEXT NOT NULL,
                role TEXT NOT NULL,
                stage TEXT NOT NULL,
                type TEXT NOT NULL,
                date_applied TEXT NOT NULL,
                last_updated TEXT NOT NULL,
                notes TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(company, role)
            );

            CREATE TABLE IF NOT EXISTS processed_emails (
                email_id TEXT PRIMARY KEY,
                processed_at TEXT DEFAULT CURRENT_TIMESTAMP,
                ai_attempted INTEGER DEFAULT 0,
                pre_filter_rejected INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS gemini_daily_usage (
                date_utc TEXT PRIMARY KEY,
                call_count INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS sync_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                emails_scanned INTEGER DEFAULT 0,
                new_applications INTEGER DEFAULT 0,
                statuses_updated INTEGER DEFAULT 0,
                emails_skipped INTEGER DEFAULT 0,
                skip_reasons TEXT,
                is_initial_run INTEGER DEFAULT 0
            );

            CREATE INDEX IF NOT EXISTS idx_applications_company_role 
                ON applications(company, role);
            CREATE INDEX IF NOT EXISTS idx_applications_last_updated 
                ON applications(last_updated DESC);
        """)
        conn.commit()

        # Migration: add columns if they don't exist (for existing databases)
        try:
            conn.execute("ALTER TABLE processed_emails ADD COLUMN ai_attempted INTEGER DEFAULT 0")
            conn.commit()
        except sqlite3.OperationalError:
            pass  # Column already exists
        try:
            conn.execute("ALTER TABLE processed_emails ADD COLUMN pre_filter_rejected INTEGER DEFAULT 0")
            conn.commit()
        except sqlite3.OperationalError:
            pass
    finally:
        conn.close()


def is_email_processed(email_id: str) -> bool:
    """Check if email has already been processed (skip forever)."""
    conn = get_connection()
    try:
        cursor = conn.execute(
            "SELECT 1 FROM processed_emails WHERE email_id = ? AND (ai_attempted = 1 OR pre_filter_rejected = 1)",
            (email_id,),
        )
        return cursor.fetchone() is not None
    finally:
        conn.close()


def get_skip_forever_ids() -> set[str]:
    """Returns IDs where ai_attempted=1 OR pre_filter_rejected=1 (skip these forever)."""
    conn = get_connection()
    try:
        cursor = conn.execute(
            "SELECT email_id FROM processed_emails WHERE ai_attempted = 1 OR pre_filter_rejected = 1"
        )
        return {str(row[0]) for row in cursor.fetchall()}
    finally:
        conn.close()


def get_retry_ids() -> set[str]:
    """Returns IDs where ai_attempted=0 AND pre_filter_rejected=0 (retry these - hit rate limit)."""
    conn = get_connection()
    try:
        cursor = conn.execute(
            "SELECT email_id FROM processed_emails WHERE ai_attempted = 0 AND pre_filter_rejected = 0"
        )
        return {str(row[0]) for row in cursor.fetchall()}
    finally:
        conn.close()


def mark_email_pre_filter_rejected(email_id: str) -> None:
    """Pre-filter rejected: sets pre_filter_rejected=1, ai_attempted=0."""
    conn = get_connection()
    try:
        conn.execute(
            """INSERT OR REPLACE INTO processed_emails (email_id, ai_attempted, pre_filter_rejected)
               VALUES (?, 0, 1)""",
            (email_id,),
        )
        conn.commit()
    finally:
        conn.close()


def mark_email_ai_completed(email_id: str) -> None:
    """Gemini returned successfully: sets ai_attempted=1."""
    conn = get_connection()
    try:
        conn.execute(
            """INSERT OR REPLACE INTO processed_emails (email_id, ai_attempted, pre_filter_rejected)
               VALUES (?, 1, 0)""",
            (email_id,),
        )
        conn.commit()
    finally:
        conn.close()


def mark_email_ai_failed_rate_limit(email_id: str) -> None:
    """AI hit rate limit: sets ai_attempted=0, pre_filter_rejected=0 so it gets retried."""
    conn = get_connection()
    try:
        conn.execute(
            """INSERT OR REPLACE INTO processed_emails (email_id, ai_attempted, pre_filter_rejected)
               VALUES (?, 0, 0)""",
            (email_id,),
        )
        conn.commit()
    finally:
        conn.close()


def get_daily_gemini_count() -> int:
    """Get today's Gemini API call count (UTC). Resets at midnight."""
    conn = get_connection()
    try:
        today = datetime.utcnow().strftime("%Y-%m-%d")
        cursor = conn.execute(
            "SELECT call_count FROM gemini_daily_usage WHERE date_utc = ?", (today,)
        )
        row = cursor.fetchone()
        return row[0] if row else 0
    finally:
        conn.close()


def increment_daily_gemini_count() -> int:
    """Increment today's count, return new total."""
    conn = get_connection()
    try:
        today = datetime.utcnow().strftime("%Y-%m-%d")
        conn.execute(
            """INSERT INTO gemini_daily_usage (date_utc, call_count) VALUES (?, 1)
               ON CONFLICT(date_utc) DO UPDATE SET call_count = call_count + 1""",
            (today,),
        )
        conn.commit()
        cursor = conn.execute(
            "SELECT call_count FROM gemini_daily_usage WHERE date_utc = ?", (today,)
        )
        return cursor.fetchone()[0]
    finally:
        conn.close()


def get_all_applications() -> list[dict]:
    """Get all applications sorted by last_updated descending."""
    conn = get_connection()
    try:
        cursor = conn.execute("""
            SELECT id, company, role, stage, type, date_applied, last_updated, notes
            FROM applications
            ORDER BY last_updated DESC
        """)
        return [dict(row) for row in cursor.fetchall()]
    finally:
        conn.close()


def find_application(company: str, role: str) -> Optional[dict]:
    """Find application by normalized company and role."""
    conn = get_connection()
    try:
        cursor = conn.execute("""
            SELECT id, company, role, stage, type, date_applied, last_updated, notes
            FROM applications
            WHERE LOWER(TRIM(company)) = LOWER(TRIM(?))
            AND LOWER(TRIM(role)) = LOWER(TRIM(?))
        """, (company, role))
        row = cursor.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def find_application_by_id(app_id: int) -> Optional[dict]:
    """Find application by ID."""
    conn = get_connection()
    try:
        cursor = conn.execute(
            "SELECT * FROM applications WHERE id = ?", (app_id,)
        )
        row = cursor.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def upsert_application(
    company: str,
    role: str,
    stage: str,
    app_type: str,
    date_applied: str,
    notes: str,
    existing_id: Optional[int] = None,
) -> tuple[bool, int]:
    """
    Insert or update application. Returns (is_new, application_id).
    """
    conn = get_connection()
    try:
        now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

        if existing_id:
            conn.execute("""
                UPDATE applications
                SET stage = ?, last_updated = ?, notes = ?
                WHERE id = ?
            """, (stage, now, notes, existing_id))
            conn.commit()
            return False, existing_id
        else:
            cursor = conn.execute("""
                INSERT INTO applications (company, role, stage, type, date_applied, last_updated, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (company, role, stage, app_type, date_applied, now, notes))
            conn.commit()
            return True, cursor.lastrowid
    finally:
        conn.close()


def update_application(
    app_id: int,
    stage: str,
    notes: str,
) -> None:
    """Update existing application."""
    conn = get_connection()
    try:
        now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        conn.execute("""
            UPDATE applications
            SET stage = ?, last_updated = ?, notes = ?
            WHERE id = ?
        """, (stage, now, notes, app_id))
        conn.commit()
    finally:
        conn.close()


def log_sync(
    emails_scanned: int,
    new_applications: int,
    statuses_updated: int,
    emails_skipped: int,
    skip_reasons: str = "",
    is_initial_run: bool = False,
) -> None:
    """Log sync run to database."""
    conn = get_connection()
    try:
        now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        conn.execute("""
            INSERT INTO sync_log (timestamp, emails_scanned, new_applications,
                statuses_updated, emails_skipped, skip_reasons, is_initial_run)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (now, emails_scanned, new_applications, statuses_updated,
              emails_skipped, skip_reasons, 1 if is_initial_run else 0))
        conn.commit()
    finally:
        conn.close()


def get_sync_logs(limit: int = 50) -> list[dict]:
    """Get recent sync logs."""
    conn = get_connection()
    try:
        cursor = conn.execute("""
            SELECT timestamp, emails_scanned, new_applications, statuses_updated,
                   emails_skipped, skip_reasons, is_initial_run
            FROM sync_log
            ORDER BY timestamp DESC
            LIMIT ?
        """, (limit,))
        return [dict(row) for row in cursor.fetchall()]
    finally:
        conn.close()
