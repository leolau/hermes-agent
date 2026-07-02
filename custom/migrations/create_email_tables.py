#!/usr/bin/env python3
"""Create email-specific tables in the unified SQLite DB."""

import sqlite3

DB_PATH = '/opt/data/whatsapp-messages/whatsapp_data.db'

db = sqlite3.connect(DB_PATH, timeout=30)
db.execute("PRAGMA journal_mode=WAL")

db.execute("""CREATE TABLE IF NOT EXISTS email_accounts (
  id TEXT PRIMARY KEY,
  address TEXT UNIQUE NOT NULL,
  label TEXT,
  last_poll TEXT,
  last_uid INTEGER DEFAULT 0,
  status TEXT DEFAULT 'active'
)""")

db.execute("""CREATE TABLE IF NOT EXISTS email_messages (
  id TEXT PRIMARY KEY,
  account_id TEXT NOT NULL,
  from_addr TEXT NOT NULL,
  from_name TEXT,
  to_addrs TEXT,
  cc_addrs TEXT,
  subject TEXT,
  body_text TEXT,
  body_html TEXT,
  has_attachments INTEGER DEFAULT 0,
  attachment_info TEXT,
  message_id TEXT UNIQUE,
  in_reply_to TEXT,
  thread_id TEXT,
  folder TEXT DEFAULT 'INBOX',
  received_at TEXT NOT NULL,
  batch_id TEXT,
  raw_headers TEXT
)""")

db.execute("CREATE INDEX IF NOT EXISTS idx_email_from ON email_messages(from_addr)")
db.execute("CREATE INDEX IF NOT EXISTS idx_email_thread ON email_messages(thread_id)")
db.execute("CREATE INDEX IF NOT EXISTS idx_email_received ON email_messages(received_at)")

db.execute("""CREATE TABLE IF NOT EXISTS email_tasks (
  id TEXT PRIMARY KEY,
  account_id TEXT,
  source_email_id TEXT,
  description TEXT,
  due_date TEXT,
  status TEXT DEFAULT 'pending',
  priority TEXT,
  created_at TEXT
)""")

db.execute("""CREATE TABLE IF NOT EXISTS email_notes (
  id TEXT PRIMARY KEY,
  account_id TEXT,
  source_email_id TEXT,
  content TEXT,
  created_at TEXT
)""")

db.execute("""CREATE TABLE IF NOT EXISTS email_digests (
  id TEXT PRIMARY KEY,
  scope TEXT,
  content TEXT,
  email_count INTEGER,
  task_count INTEGER,
  sent_at TEXT,
  channel TEXT DEFAULT 'telegram'
)""")

db.commit()

tables = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()]
print("Tables:", tables)
for t in ['email_accounts', 'email_messages', 'email_tasks', 'email_notes', 'email_digests']:
    cols = [r[1] for r in db.execute(f"PRAGMA table_info({t})").fetchall()]
    print(f"  {t}: {cols}")

db.close()
print("Email tables created successfully")
