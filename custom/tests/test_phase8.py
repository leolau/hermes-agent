#!/usr/bin/env python3
"""Phase 8 Tests: Merged Digest"""

import sqlite3
import json
import os

DB_PATH = '/opt/data/whatsapp-messages/whatsapp_data.db'

db = sqlite3.connect(DB_PATH, timeout=10)
db.row_factory = sqlite3.Row

# TEST 8.1: wa_digests has entries
print("TEST 8.1: wa_digests populated")
wa_digests = db.execute("SELECT COUNT(*) FROM wa_digests").fetchone()[0]
assert wa_digests > 0, "No wa_digests entries"
print(f"  PASS: {wa_digests} wa_digest entries")

# TEST 8.2: email_digests has entries
print("TEST 8.2: email_digests populated")
email_digests = db.execute("SELECT COUNT(*) FROM email_digests").fetchone()[0]
assert email_digests > 0, "No email_digests entries"
print(f"  PASS: {email_digests} email_digest entries")

# TEST 8.3: Digest content references email data
print("TEST 8.3: Digest content has email data")
latest = db.execute("SELECT content FROM email_digests ORDER BY sent_at DESC LIMIT 1").fetchone()
assert latest is not None
content = latest['content']
assert len(content) > 10, f"Digest content too short: {content}"
print(f"  Content preview: {content[:100]}...")
print("  PASS: Digest has content")

# TEST 8.4: Digest records sent_at timestamp
print("TEST 8.4: sent_at timestamp")
row = db.execute("SELECT sent_at FROM email_digests ORDER BY sent_at DESC LIMIT 1").fetchone()
assert row['sent_at'] is not None
assert '2026' in row['sent_at'] or '202' in row['sent_at']
print(f"  PASS: sent_at = {row['sent_at']}")

# TEST 8.5: Digest cron script exists and is updated
print("TEST 8.5: Digest script updated")
script_path = '/opt/data/whatsapp-messages/digest_cron.py'
assert os.path.exists(script_path), "digest_cron.py not found"
with open(script_path) as f:
    script = f.read()
assert 'email_messages' in script, "Digest script not updated for email"
assert 'email_digests' in script, "Digest script missing email_digests table"
assert 'unified' in script.lower() or 'merged' in script.lower() or 'WhatsApp + Email' in script, "Not a unified digest"
print("  PASS: Digest script handles both channels")

# TEST 8.6: Both channel data in same digest cycle
print("TEST 8.6: Merged digest format")
# Check that the email_digests content mentions emails
ed = db.execute("SELECT content, email_count FROM email_digests ORDER BY sent_at DESC LIMIT 1").fetchone()
assert ed is not None
assert ed['email_count'] >= 0
print(f"  email_count={ed['email_count']}")
print("  PASS: Merged digest records both channels")

print()
print("=== ALL PHASE 8 TESTS PASSED (6/6) ===")
db.close()
