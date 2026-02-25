"""SQLite database for applications and processed email tracking."""

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
                processed_at TEXT DEFAULT CURRENT_TIMESTAMP
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
    finally:
        conn.close()


def is_email_processed(email_id: str) -> bool:
    """Check if email has already been processed."""
    conn = get_connection()
    try:
        cursor = conn.execute(
            "SELECT 1 FROM processed_emails WHERE email_id = ?", (email_id,)
        )
        return cursor.fetchone() is not None
    finally:
        conn.close()


def mark_email_processed(email_id: str) -> None:
    """Mark email as processed."""
    conn = get_connection()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO processed_emails (email_id) VALUES (?)",
            (email_id,),
        )
        conn.commit()
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
