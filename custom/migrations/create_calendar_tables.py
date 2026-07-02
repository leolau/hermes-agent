#!/usr/bin/env python3
"""
Create calendar tables in the unified SQLite database.

Tables:
  - calendar_accounts: OAuth2-linked Google Calendar accounts
  - calendar_events: Synced calendar events
  - calendar_attendees: Event attendees with contact linking
  - calendar_reminders: Scheduled Telegram reminders
"""

import os
import sqlite3
from datetime import datetime, timezone

DB_PATH = os.environ.get('DB_PATH', '/opt/data/whatsapp-messages/whatsapp_data.db')


def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def create_tables(db):
    """Create all calendar-related tables."""

    db.execute("""CREATE TABLE IF NOT EXISTS calendar_accounts (
        id TEXT PRIMARY KEY,
        email TEXT NOT NULL,
        label TEXT,
        sync_token TEXT,
        last_synced TEXT,
        enabled INTEGER DEFAULT 1
    )""")

    db.execute("""CREATE TABLE IF NOT EXISTS calendar_events (
        id TEXT PRIMARY KEY,
        google_event_id TEXT NOT NULL,
        account_id TEXT NOT NULL,
        calendar_id TEXT DEFAULT 'primary',
        summary TEXT,
        description TEXT,
        location TEXT,
        start_time TEXT,
        end_time TEXT,
        all_day INTEGER DEFAULT 0,
        timezone TEXT,
        status TEXT DEFAULT 'confirmed',
        organizer_email TEXT,
        organizer_name TEXT,
        recurring_event_id TEXT,
        html_link TEXT,
        conference_link TEXT,
        raw_json TEXT,
        importance TEXT,
        triage_notes TEXT,
        triaged INTEGER DEFAULT 0,
        contact_id TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE(google_event_id, account_id),
        FOREIGN KEY (account_id) REFERENCES calendar_accounts(id),
        FOREIGN KEY (contact_id) REFERENCES unified_contacts(id)
    )""")

    db.execute("""CREATE TABLE IF NOT EXISTS calendar_attendees (
        id TEXT PRIMARY KEY,
        event_id TEXT NOT NULL,
        email TEXT,
        display_name TEXT,
        response_status TEXT DEFAULT 'needsAction',
        organizer INTEGER DEFAULT 0,
        self INTEGER DEFAULT 0,
        contact_id TEXT,
        FOREIGN KEY (event_id) REFERENCES calendar_events(id),
        FOREIGN KEY (contact_id) REFERENCES unified_contacts(id)
    )""")

    db.execute("""CREATE TABLE IF NOT EXISTS calendar_reminders (
        id TEXT PRIMARY KEY,
        event_id TEXT NOT NULL,
        remind_at TEXT NOT NULL,
        lead_minutes INTEGER NOT NULL,
        sent INTEGER DEFAULT 0,
        sent_at TEXT,
        FOREIGN KEY (event_id) REFERENCES calendar_events(id)
    )""")

    # Indexes for common queries
    db.execute("CREATE INDEX IF NOT EXISTS idx_cal_events_start ON calendar_events(start_time)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_cal_events_account ON calendar_events(account_id)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_cal_events_status ON calendar_events(status)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_cal_events_triaged ON calendar_events(triaged)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_cal_events_google_id ON calendar_events(google_event_id, account_id)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_cal_attendees_event ON calendar_attendees(event_id)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_cal_attendees_email ON calendar_attendees(email)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_cal_attendees_contact ON calendar_attendees(contact_id)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_cal_reminders_remind ON calendar_reminders(remind_at, sent)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_cal_reminders_event ON calendar_reminders(event_id)")

    db.commit()


def seed_accounts(db):
    """Seed the 3 Google Calendar accounts."""
    now = datetime.now(timezone.utc).isoformat()
    accounts = [
        ('gcal1', 'leo11lau@gmail.com', 'Personal'),
        ('gcal2', 'leolau@joyaether.com', 'Joyaether'),
        ('gcal3', 'leolau@snappopapp.com', 'SnappoPop'),
    ]
    for acc_id, email, label in accounts:
        db.execute(
            """INSERT OR IGNORE INTO calendar_accounts (id, email, label, enabled)
               VALUES (?, ?, ?, 1)""",
            (acc_id, email, label)
        )
    db.commit()


def main():
    print("[calendar] Creating calendar tables...")
    db = get_db()
    create_tables(db)
    seed_accounts(db)

    # Verify
    tables = db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'calendar_%'"
    ).fetchall()
    print(f"[calendar] Created {len(tables)} tables: {[t['name'] for t in tables]}")

    accounts = db.execute("SELECT id, email, label FROM calendar_accounts").fetchall()
    print(f"[calendar] Seeded {len(accounts)} accounts:")
    for a in accounts:
        print(f"  - {a['id']}: {a['email']} ({a['label']})")

    db.close()
    print("[calendar] Done.")


if __name__ == '__main__':
    main()
