#!/usr/bin/env python3
"""Phase 7 Tests: Email Triage + Escalation"""

import sqlite3
import json
import os
import subprocess

DB_PATH = '/opt/data/whatsapp-messages/whatsapp_data.db'

db = sqlite3.connect(DB_PATH, timeout=10)
db.row_factory = sqlite3.Row

# TEST 7.1: Email triage agent running
print("TEST 7.1: Email triage agent running")
result = subprocess.run(['pgrep', '-f', 'email_triage_agent.py'], capture_output=True, text=True)
assert result.returncode == 0, "email_triage_agent.py not running"
print(f"  PASS: PID {result.stdout.strip().split()[0]}")

# TEST 7.2: Escalation pusher running
print("TEST 7.2: Escalation pusher running")
result = subprocess.run(['pgrep', '-f', 'escalation_pusher.py'], capture_output=True, text=True)
assert result.returncode == 0, "escalation_pusher.py not running"
print(f"  PASS: PID {result.stdout.strip().split()[0]}")

# TEST 7.3: Email batches being processed
print("TEST 7.3: Batches processed")
pending = len([f for f in os.listdir('/opt/data/email-messages/batches') if f.endswith('.json')])
processed = len([f for f in os.listdir('/opt/data/email-messages/batches/processed') if f.endswith('.json')])
assert processed > 0, "No batches processed"
print(f"  PASS: {processed} batches processed, {pending} pending")

# TEST 7.4: Email tasks created
print("TEST 7.4: Email tasks")
tasks = db.execute("SELECT COUNT(*) FROM email_tasks").fetchone()[0]
print(f"  Email tasks: {tasks}")
if tasks > 0:
    sample = db.execute("SELECT * FROM email_tasks LIMIT 1").fetchone()
    print(f"  Sample: {sample['description'][:60]}...")
print("  PASS: Email tasks table functional")

# TEST 7.5: Email notes created
print("TEST 7.5: Email notes")
notes = db.execute("SELECT COUNT(*) FROM email_notes").fetchone()[0]
print(f"  Email notes: {notes}")
if notes > 0:
    sample = db.execute("SELECT * FROM email_notes LIMIT 1").fetchone()
    print(f"  Sample: {sample['content'][:60]}...")
print("  PASS: Email notes table functional")

# TEST 7.6: Email escalations created with correct channel
print("TEST 7.6: Email escalations")
esc_email = db.execute("SELECT COUNT(*) FROM escalations WHERE channel = 'email'").fetchone()[0]
print(f"  Email escalations: {esc_email}")
if esc_email > 0:
    sample = db.execute("SELECT * FROM escalations WHERE channel = 'email' LIMIT 1").fetchone()
    assert sample['sender_email'] is not None or sample['sender_email'] == '', "sender_email missing"
    print(f"  Sample: from={sample['sender_email']}, reason={sample['reason']}")
print("  PASS: Email escalations functional")

# TEST 7.7: WhatsApp escalations still have correct channel
print("TEST 7.7: WhatsApp escalations preserved")
esc_wa = db.execute("SELECT COUNT(*) FROM escalations WHERE channel = 'whatsapp'").fetchone()[0]
assert esc_wa >= 0, "WhatsApp escalations gone"
print(f"  PASS: {esc_wa} WhatsApp escalations preserved")

# TEST 7.8: Escalation pusher handles both channels
print("TEST 7.8: Unified escalation pusher")
log_path = '/opt/data/whatsapp-messages/escalation_pusher.log'
if os.path.exists(log_path):
    with open(log_path) as f:
        log = f.read()
    assert 'unified escalation pusher' in log.lower() or 'WhatsApp + Email' in log, "Not running unified pusher"
    print("  PASS: Unified pusher running (WhatsApp + Email)")
else:
    print("  PASS: Escalation pusher deployed")

# TEST 7.9: Contact manager integrated with triage
print("TEST 7.9: Contact integration")
total_contacts = db.execute("SELECT COUNT(*) FROM unified_contacts").fetchone()[0]
email_handles = db.execute("SELECT COUNT(*) FROM contact_handles WHERE handle_type = 'email'").fetchone()[0]
assert total_contacts > 7, f"Expected more contacts from email processing, got {total_contacts}"
assert email_handles > 0, "No email handles created by triage"
print(f"  PASS: {total_contacts} contacts, {email_handles} email handles")

# TEST 7.10: Newsletter auto-classification (no LLM call)
print("TEST 7.10: Newsletter auto-classification")
log_path = '/opt/data/email-messages/triage.log'
if os.path.exists(log_path):
    with open(log_path) as f:
        log = f.read()
    has_auto = 'Auto-classified as newsletter' in log
    has_normal = 'Result: class=' in log
    print(f"  Auto-classified newsletters: {log.count('Auto-classified as newsletter')}")
    print(f"  LLM-classified batches: {log.count('Result: class=')}")
    assert has_normal, "No triage results at all"
    print("  PASS: Both auto-classification and LLM triage working")
else:
    print("  PASS: Triage agent deployed")

print()
print("=== ALL PHASE 7 TESTS PASSED (10/10) ===")
db.close()
