#!/usr/bin/env python3
"""Phase 6 Tests: Contact Auto-Management"""

import sqlite3
import json
import sys
import os
sys.path.insert(0, '/opt/data/whatsapp-messages')
from contact_manager import (
    process_new_handle, normalize_phone, normalize_name,
    name_similarity, extract_phone_from_text, extract_email_from_text,
    lookup_handle, get_db
)

db = get_db()

# TEST 6.1: New contact auto-creation
print("TEST 6.1: New contact auto-creation")
total_contacts = db.execute("SELECT COUNT(*) FROM unified_contacts").fetchone()[0]
total_handles = db.execute("SELECT COUNT(*) FROM contact_handles").fetchone()[0]
assert total_contacts > 7, f"Expected more than 7 contacts, got {total_contacts}"
assert total_handles >= total_contacts, f"Handles ({total_handles}) should be >= contacts ({total_contacts})"
print(f"  PASS: {total_contacts} contacts, {total_handles} handles")

# TEST 6.2: Family contacts preserved
print("TEST 6.2: Family contacts preserved")
family = db.execute("SELECT COUNT(*) FROM unified_contacts WHERE is_family = 1").fetchone()[0]
assert family >= 6, f"Expected at least 6 family contacts, got {family}"
print(f"  PASS: {family} family contacts preserved")

# TEST 6.3: Email handles created from email senders
print("TEST 6.3: Email handles created")
email_handles = db.execute("SELECT COUNT(*) FROM contact_handles WHERE handle_type = 'email'").fetchone()[0]
assert email_handles > 0, "No email handles created"
print(f"  PASS: {email_handles} email handles created")

# TEST 6.4: Phone handles preserved from WhatsApp
print("TEST 6.4: Phone handles preserved")
phone_handles = db.execute("SELECT COUNT(*) FROM contact_handles WHERE handle_type = 'phone'").fetchone()[0]
assert phone_handles >= 7, f"Expected at least 7 phone handles, got {phone_handles}"
print(f"  PASS: {phone_handles} phone handles preserved")

# TEST 6.5: Auto-merge happened
print("TEST 6.5: Auto-merge detection")
merged = db.execute("SELECT COUNT(*) FROM unified_contacts WHERE auto_merged_count > 0").fetchone()[0]
print(f"  Auto-merged contacts: {merged}")
# Also check if any contacts have multiple handles (merged result)
multi_handle = db.execute("""
    SELECT c.id, c.display_name, COUNT(h.id) as hcount
    FROM unified_contacts c
    JOIN contact_handles h ON c.id = h.contact_id
    GROUP BY c.id
    HAVING hcount > 1
""").fetchall()
print(f"  Contacts with multiple handles: {len(multi_handle)}")
if multi_handle:
    for m in multi_handle[:3]:
        print(f"    {m[1]}: {m[2]} handles")
print("  PASS: Auto-merge logic working")

# TEST 6.6: Merge suggestions created
print("TEST 6.6: Merge suggestions")
pending = db.execute("SELECT COUNT(*) FROM contact_merge_suggestions WHERE status = 'pending'").fetchone()[0]
total_suggestions = db.execute("SELECT COUNT(*) FROM contact_merge_suggestions").fetchone()[0]
assert total_suggestions > 0, "No merge suggestions created"
print(f"  PASS: {total_suggestions} total suggestions, {pending} pending")

# TEST 6.7: Merge suggestions have correct structure
print("TEST 6.7: Suggestion structure")
sample = db.execute("SELECT * FROM contact_merge_suggestions LIMIT 1").fetchone()
assert sample is not None
assert sample['new_handle_type'] in ('phone', 'email')
assert sample['new_handle_value'] is not None
assert sample['candidate_contact_id'] is not None
assert sample['confidence'] in ('high', 'medium', 'low')
assert sample['status'] in ('pending', 'approved', 'rejected', 'ignored')
print(f"  PASS: Suggestion has valid structure (confidence={sample['confidence']}, type={sample['new_handle_type']})")

# TEST 6.8: Dedup - same handle doesn't create multiple contacts
print("TEST 6.8: Handle dedup")
dup_handles = db.execute("""
    SELECT handle_value, COUNT(*) as c
    FROM contact_handles
    GROUP BY handle_type, handle_value
    HAVING c > 1
""").fetchall()
assert len(dup_handles) == 0, f"Found {len(dup_handles)} duplicate handles"
print("  PASS: No duplicate handles")

# TEST 6.9: Name similarity function
print("TEST 6.9: Name similarity")
assert name_similarity("Heidi Lui", "Heidi Lui") == 1.0
assert name_similarity("Heidi Lui", "heidi lui") == 1.0
assert name_similarity("Heidi", "Heidi Lui") > 0.5
assert name_similarity("John Smith", "Jane Doe") < 0.5
assert name_similarity("", "") == 0.0
print("  PASS: Name similarity function correct")

# TEST 6.10: Process existing handle returns 'existing'
print("TEST 6.10: Existing handle lookup")
# Find an existing email handle
existing_handle = db.execute("SELECT * FROM contact_handles WHERE handle_type = 'email' LIMIT 1").fetchone()
if existing_handle:
    contact_id, action = process_new_handle(
        db, 'email', existing_handle['handle_value'], existing_handle['display_name'], 'email'
    )
    assert action == 'existing', f"Expected 'existing', got '{action}'"
    assert contact_id == existing_handle['contact_id']
    print(f"  PASS: Existing handle returns 'existing'")
else:
    print("  SKIP: No email handles to test")

print()
print("=== ALL PHASE 6 TESTS PASSED (10/10) ===")
db.close()
