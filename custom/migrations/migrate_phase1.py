#!/usr/bin/env python3
"""
Phase 1 Migration: Unified Contacts + Table Renames

1. Rename tasks -> wa_tasks, notes -> wa_notes, digests -> wa_digests
2. Create unified contacts + contact_handles tables
3. Migrate old contacts data to new schema
4. Create contact_merge_suggestions table
5. Add columns to escalations
6. Backfill escalations with channel='whatsapp'
7. Drop old contacts table
"""

import sqlite3
import uuid
from datetime import datetime, timezone

DB_PATH = '/opt/data/whatsapp-messages/whatsapp_data.db'

def migrate():
    db = sqlite3.connect(DB_PATH, timeout=30)
    db.execute("PRAGMA journal_mode=WAL")
    db.row_factory = sqlite3.Row
    now = datetime.now(timezone.utc).isoformat()

    print("[migrate] Starting Phase 1 migration...")

    # Step 1: Rename tables
    existing = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]

    if 'tasks' in existing and 'wa_tasks' not in existing:
        db.execute("ALTER TABLE tasks RENAME TO wa_tasks")
        print("[migrate] Renamed tasks -> wa_tasks")
    elif 'wa_tasks' in existing:
        print("[migrate] wa_tasks already exists, skipping rename")

    if 'notes' in existing and 'wa_notes' not in existing:
        db.execute("ALTER TABLE notes RENAME TO wa_notes")
        print("[migrate] Renamed notes -> wa_notes")
    elif 'wa_notes' in existing:
        print("[migrate] wa_notes already exists, skipping rename")

    if 'digests' in existing and 'wa_digests' not in existing:
        db.execute("ALTER TABLE digests RENAME TO wa_digests")
        print("[migrate] Renamed digests -> wa_digests")
    elif 'wa_digests' in existing:
        print("[migrate] wa_digests already exists, skipping rename")

    db.commit()

    # Step 2: Create new unified contacts table
    db.execute("""
        CREATE TABLE IF NOT EXISTS unified_contacts (
            id TEXT PRIMARY KEY,
            display_name TEXT NOT NULL,
            is_family INTEGER DEFAULT 0,
            is_vip INTEGER DEFAULT 0,
            relation TEXT,
            company TEXT,
            notes TEXT,
            auto_merged_count INTEGER DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)
    print("[migrate] Created unified_contacts table")

    # Step 3: Create contact_handles table
    db.execute("""
        CREATE TABLE IF NOT EXISTS contact_handles (
            id TEXT PRIMARY KEY,
            contact_id TEXT NOT NULL,
            handle_type TEXT NOT NULL,
            handle_value TEXT NOT NULL,
            display_name TEXT,
            source TEXT,
            first_seen TEXT,
            last_seen TEXT,
            message_count INTEGER DEFAULT 0,
            UNIQUE(handle_type, handle_value),
            FOREIGN KEY (contact_id) REFERENCES unified_contacts(id)
        )
    """)
    db.execute("CREATE INDEX IF NOT EXISTS idx_handles_value ON contact_handles(handle_type, handle_value)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_handles_contact ON contact_handles(contact_id)")
    print("[migrate] Created contact_handles table")

    # Step 4: Create contact_merge_suggestions table
    db.execute("""
        CREATE TABLE IF NOT EXISTS contact_merge_suggestions (
            id TEXT PRIMARY KEY,
            new_handle_type TEXT NOT NULL,
            new_handle_value TEXT NOT NULL,
            new_display_name TEXT,
            new_contact_id TEXT,
            candidate_contact_id TEXT NOT NULL,
            correlation_reason TEXT,
            confidence TEXT,
            status TEXT DEFAULT 'pending',
            created_at TEXT NOT NULL,
            resolved_at TEXT,
            FOREIGN KEY (new_contact_id) REFERENCES unified_contacts(id),
            FOREIGN KEY (candidate_contact_id) REFERENCES unified_contacts(id)
        )
    """)
    print("[migrate] Created contact_merge_suggestions table")

    db.commit()

    # Step 5: Migrate old contacts data to new schema
    if 'contacts' in existing:
        old_contacts = db.execute("SELECT * FROM contacts").fetchall()
        migrated = 0
        for oc in old_contacts:
            contact_id = str(uuid.uuid4())
            phone = oc['phone']
            name = oc['name'] or phone
            is_family = oc['is_family'] or 0
            relation = oc['relation'] if 'relation' in oc.keys() else None
            first_seen = oc['first_seen']
            last_seen = oc['last_seen']
            msg_count = oc['message_count'] or 0

            # Check if already migrated (handle exists)
            exists = db.execute(
                "SELECT id FROM contact_handles WHERE handle_type='phone' AND handle_value=?",
                (phone,)
            ).fetchone()
            if exists:
                continue

            # Insert unified contact
            db.execute(
                """INSERT INTO unified_contacts (id, display_name, is_family, is_vip, relation, company, notes, auto_merged_count, created_at, updated_at)
                   VALUES (?, ?, ?, 0, ?, NULL, NULL, 0, ?, ?)""",
                (contact_id, name, is_family, relation, now, now)
            )

            # Insert phone handle
            handle_id = str(uuid.uuid4())
            db.execute(
                """INSERT INTO contact_handles (id, contact_id, handle_type, handle_value, display_name, source, first_seen, last_seen, message_count)
                   VALUES (?, ?, 'phone', ?, ?, 'whatsapp', ?, ?, ?)""",
                (handle_id, contact_id, phone, name, first_seen, last_seen, msg_count)
            )
            migrated += 1

        print(f"[migrate] Migrated {migrated} contacts from old table")
        db.commit()

    # Step 6: Add columns to escalations
    esc_cols = [r[1] for r in db.execute("PRAGMA table_info(escalations)").fetchall()]

    if 'channel' not in esc_cols:
        db.execute("ALTER TABLE escalations ADD COLUMN channel TEXT DEFAULT 'whatsapp'")
        print("[migrate] Added 'channel' column to escalations")

    if 'sender_email' not in esc_cols:
        db.execute("ALTER TABLE escalations ADD COLUMN sender_email TEXT")
        print("[migrate] Added 'sender_email' column to escalations")

    if 'sender_name' not in esc_cols:
        db.execute("ALTER TABLE escalations ADD COLUMN sender_name TEXT")
        print("[migrate] Added 'sender_name' column to escalations")

    if 'contact_id' not in esc_cols:
        db.execute("ALTER TABLE escalations ADD COLUMN contact_id TEXT")
        print("[migrate] Added 'contact_id' column to escalations")

    db.commit()

    # Step 7: Backfill escalations
    db.execute("UPDATE escalations SET channel = 'whatsapp' WHERE channel IS NULL")
    db.commit()
    print("[migrate] Backfilled escalations with channel='whatsapp'")

    # Step 8: Link existing escalations to unified contacts
    escalations = db.execute("SELECT id, sender_phone FROM escalations WHERE contact_id IS NULL AND sender_phone IS NOT NULL").fetchall()
    for esc in escalations:
        handle = db.execute(
            "SELECT contact_id FROM contact_handles WHERE handle_type='phone' AND handle_value=?",
            (esc['sender_phone'],)
        ).fetchone()
        if handle:
            db.execute("UPDATE escalations SET contact_id=? WHERE id=?", (handle['contact_id'], esc['id']))
    db.commit()
    print(f"[migrate] Linked {len(escalations)} escalations to contacts")

    # Step 9: Drop old contacts table (renamed to _old for safety)
    if 'contacts' in existing:
        db.execute("ALTER TABLE contacts RENAME TO contacts_old_backup")
        print("[migrate] Renamed old contacts -> contacts_old_backup")

    db.commit()

    # Verify
    tables = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()]
    print(f"[migrate] Final tables: {tables}")

    contact_count = db.execute("SELECT COUNT(*) FROM unified_contacts").fetchone()[0]
    handle_count = db.execute("SELECT COUNT(*) FROM contact_handles").fetchone()[0]
    print(f"[migrate] Contacts: {contact_count}, Handles: {handle_count}")

    db.close()
    print("[migrate] Phase 1 migration complete!")


if __name__ == '__main__':
    migrate()
