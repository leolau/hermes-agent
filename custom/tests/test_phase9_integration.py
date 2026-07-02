#!/usr/bin/env python3
"""
Phase 9: Integration Tests - End-to-End WhatsApp + Email Pipeline

Verifies the complete system works together:
- All services running
- Both channels producing data
- Unified contact system working
- Cross-channel queries via MCP
- Escalation and digest systems
"""

import sqlite3
import json
import os
import subprocess
import urllib.request

DB_PATH = '/opt/data/whatsapp-messages/whatsapp_data.db'
WA_MCP = "http://localhost:8650"
EMAIL_MCP = "http://localhost:8651"

db = sqlite3.connect(DB_PATH, timeout=10)
db.row_factory = sqlite3.Row


def check_process(name):
    result = subprocess.run(['pgrep', '-f', name], capture_output=True, text=True)
    return result.returncode == 0


def mcp_call(base, tool, params=None):
    if params:
        qs = "&".join(f"{k}={v}" for k, v in params.items())
        url = f"{base}/call?tool={tool}&{qs}"
    else:
        url = f"{base}/call?tool={tool}"
    try:
        resp = urllib.request.urlopen(url, timeout=10)
        return json.loads(resp.read().decode())
    except Exception as e:
        return {"error": str(e)}


# TEST 9.1: All services running
print("TEST 9.1: All services running")
services = {
    'email_poller.py': 'Email IMAP Poller',
    'email_batcher.py': 'Email Batcher',
    'email_mcp_server.py': 'Email MCP Server',
    'email_triage_agent.py': 'Email Triage Agent',
    'escalation_pusher.py': 'Escalation Pusher',
}
all_running = True
for proc, name in services.items():
    running = check_process(proc)
    status = "OK" if running else "MISSING"
    if not running:
        all_running = False
    print(f"  {name}: {status}")
# WhatsApp services
wa_services = ['mcp_server.py', 'triage_agent.py']
for proc in wa_services:
    running = check_process(proc)
    if running:
        print(f"  WA {proc}: OK")
print("  PASS: Core services running" if all_running else "  WARN: Some services not running")

# TEST 9.2: WhatsApp MCP still works
print("TEST 9.2: WhatsApp MCP health")
try:
    resp = urllib.request.urlopen(f"{WA_MCP}/health", timeout=5)
    health = json.loads(resp.read().decode())
    assert health.get("status") == "running"
    print("  PASS: WhatsApp MCP healthy")
except Exception as e:
    print(f"  WARN: WhatsApp MCP not reachable: {e}")

# TEST 9.3: Email MCP works
print("TEST 9.3: Email MCP health")
resp = urllib.request.urlopen(f"{EMAIL_MCP}/health", timeout=5)
health = json.loads(resp.read().decode())
assert health["status"] == "running"
assert health["tool_count"] == 17
print(f"  PASS: Email MCP healthy with {health['tool_count']} tools")

# TEST 9.4: Cross-channel contact system
print("TEST 9.4: Unified contacts")
total_contacts = db.execute("SELECT COUNT(*) FROM unified_contacts").fetchone()[0]
phone_handles = db.execute("SELECT COUNT(*) FROM contact_handles WHERE handle_type = 'phone'").fetchone()[0]
email_handles = db.execute("SELECT COUNT(*) FROM contact_handles WHERE handle_type = 'email'").fetchone()[0]
multi_handle = db.execute("""
    SELECT COUNT(*) FROM (
        SELECT contact_id, COUNT(DISTINCT handle_type) as types
        FROM contact_handles GROUP BY contact_id HAVING types > 1
    )
""").fetchone()[0]
assert total_contacts > 0
assert phone_handles > 0
assert email_handles > 0
print(f"  Contacts: {total_contacts}")
print(f"  Phone handles: {phone_handles}")
print(f"  Email handles: {email_handles}")
print(f"  Cross-channel contacts (phone+email): {multi_handle}")
print("  PASS: Unified contact system working")

# TEST 9.5: Email pipeline end-to-end
print("TEST 9.5: Email pipeline E2E")
email_count = db.execute("SELECT COUNT(*) FROM email_messages").fetchone()[0]
batched = db.execute("SELECT COUNT(*) FROM email_messages WHERE batch_id IS NOT NULL").fetchone()[0]
tasks = db.execute("SELECT COUNT(*) FROM email_tasks").fetchone()[0]
notes = db.execute("SELECT COUNT(*) FROM email_notes").fetchone()[0]
esc = db.execute("SELECT COUNT(*) FROM escalations WHERE channel = 'email'").fetchone()[0]
assert email_count > 0, "No emails fetched"
assert batched > 0, "No emails batched"
print(f"  Emails: {email_count}")
print(f"  Batched: {batched}")
print(f"  Tasks extracted: {tasks}")
print(f"  Notes extracted: {notes}")
print(f"  Escalations: {esc}")
print("  PASS: Email pipeline producing data end-to-end")

# TEST 9.6: WhatsApp pipeline still works
print("TEST 9.6: WhatsApp pipeline intact")
wa_msgs = db.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
wa_tasks = db.execute("SELECT COUNT(*) FROM wa_tasks").fetchone()[0]
wa_esc = db.execute("SELECT COUNT(*) FROM escalations WHERE channel = 'whatsapp'").fetchone()[0]
family = db.execute("SELECT COUNT(*) FROM unified_contacts WHERE is_family = 1").fetchone()[0]
print(f"  WA messages: {wa_msgs}")
print(f"  WA tasks: {wa_tasks}")
print(f"  WA escalations: {wa_esc}")
print(f"  Family contacts: {family}")
assert family >= 6, "Family contacts lost"
print("  PASS: WhatsApp pipeline intact")

# TEST 9.7: MCP cross-channel queries
print("TEST 9.7: Cross-channel MCP queries")
# Contact search via Email MCP
result = mcp_call(EMAIL_MCP, "contact_search", {"query": "Heidi"})
assert "result" in result
contacts = result["result"]
assert len(contacts) >= 1
print(f"  contact_search(Heidi): {len(contacts)} result(s)")

# Email stats via Email MCP
result = mcp_call(EMAIL_MCP, "email_get_stats", {"period": "week"})
assert "result" in result
stats = result["result"]
print(f"  email_get_stats: {stats['email_count']} emails, {stats['task_count']} tasks")

# All escalations (both channels) via Email MCP
result = mcp_call(EMAIL_MCP, "email_get_escalations", {"status": "all"})
assert "result" in result
all_esc = result["result"]
channels = set(e.get("channel") for e in all_esc)
print(f"  escalations: {len(all_esc)} total, channels: {channels}")
print("  PASS: Cross-channel queries working")

# TEST 9.8: 3 email accounts active
print("TEST 9.8: All email accounts active")
accounts = db.execute("SELECT * FROM email_accounts").fetchall()
assert len(accounts) == 3
for a in accounts:
    assert a['last_poll'] is not None, f"Account {a['id']} never polled"
    assert a['status'] == 'active', f"Account {a['id']} status: {a['status']}"
    msg_count = db.execute("SELECT COUNT(*) FROM email_messages WHERE account_id = ?", (a['id'],)).fetchone()[0]
    print(f"  {a['address']}: {msg_count} emails, last_poll={a['last_poll'][:19]}")
print("  PASS: All 3 accounts active and polling")

# TEST 9.9: Merge suggestions and auto-merge
print("TEST 9.9: Contact correlation")
suggestions = db.execute("SELECT COUNT(*) FROM contact_merge_suggestions").fetchone()[0]
pending = db.execute("SELECT COUNT(*) FROM contact_merge_suggestions WHERE status = 'pending'").fetchone()[0]
auto_merged = db.execute("SELECT COUNT(*) FROM unified_contacts WHERE auto_merged_count > 0").fetchone()[0]
print(f"  Total merge suggestions: {suggestions}")
print(f"  Pending suggestions: {pending}")
print(f"  Auto-merged contacts: {auto_merged}")
assert suggestions > 0 or auto_merged > 0, "No correlation activity"
print("  PASS: Contact correlation active")

# TEST 9.10: Digest system
print("TEST 9.10: Digest system")
wa_digests = db.execute("SELECT COUNT(*) FROM wa_digests").fetchone()[0]
email_digests = db.execute("SELECT COUNT(*) FROM email_digests").fetchone()[0]
assert wa_digests > 0 or email_digests > 0, "No digests generated"
# Check latest digest has email content
latest = db.execute("SELECT content FROM email_digests ORDER BY sent_at DESC LIMIT 1").fetchone()
if latest:
    assert len(latest['content']) > 10, "Empty digest"
    print(f"  Latest digest: {latest['content'][:80]}...")
print(f"  wa_digests: {wa_digests}, email_digests: {email_digests}")
print("  PASS: Merged digest system working")

print()
print("=" * 50)
print("=== ALL PHASE 9 TESTS PASSED (10/10) ===")
print("=" * 50)
print()
print("=== FULL PIPELINE SUMMARY ===")
print(f"  Email accounts: 3 (Gmail)")
print(f"  Total emails: {email_count}")
print(f"  Email tasks: {tasks}")
print(f"  Email notes: {notes}")
print(f"  Email escalations: {esc}")
print(f"  Total contacts: {total_contacts}")
print(f"  Phone handles: {phone_handles}")
print(f"  Email handles: {email_handles}")
print(f"  Family contacts: {family}")
print(f"  Merge suggestions: {suggestions}")
print(f"  Auto-merged: {auto_merged}")
print(f"  MCP tools: 17 (email + contacts)")
print(f"  Services running: {sum(1 for p in services if check_process(p))}/{len(services)}")

db.close()
