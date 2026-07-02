#!/usr/bin/env python3
"""
Contact Auto-Management Module for Hermes Agent

Automatically creates new contacts, correlates handles across channels,
auto-merges high-confidence matches, and sends Telegram confirmations
for medium-confidence merge suggestions.
"""

import json
import sqlite3
import os
import uuid
import re
import urllib.request
from datetime import datetime, timezone
from difflib import SequenceMatcher

DB_PATH = '/opt/data/whatsapp-messages/whatsapp_data.db'
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', '')
TELEGRAM_USER_ID = os.environ.get('TELEGRAM_USER_ID', '')


def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def send_telegram(text, reply_markup=None):
    """Send a message to the user via Telegram."""
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {
            "chat_id": TELEGRAM_USER_ID,
            "text": text,
            "parse_mode": "HTML"
        }
        if reply_markup:
            payload["reply_markup"] = reply_markup
        data = json.dumps(payload).encode()
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=10)
        return True
    except Exception as e:
        print(f"[contacts] Telegram send error: {e}")
        return False


def normalize_phone(phone):
    """Normalize phone number for comparison."""
    if not phone:
        return ''
    return re.sub(r'[^\d+]', '', phone.strip())


def normalize_name(name):
    """Normalize a display name for comparison."""
    if not name:
        return ''
    return ' '.join(name.strip().lower().split())


def name_similarity(name1, name2):
    """Calculate similarity between two names."""
    n1 = normalize_name(name1)
    n2 = normalize_name(name2)
    if not n1 or not n2:
        return 0.0
    if n1 == n2:
        return 1.0
    return SequenceMatcher(None, n1, n2).ratio()


def extract_phone_from_text(text):
    """Extract phone numbers from email body/signature."""
    if not text:
        return []
    pattern = r'(?:\+?\d{1,4}[\s.-]?)?\(?\d{1,4}\)?[\s.-]?\d{3,4}[\s.-]?\d{3,4}'
    matches = re.findall(pattern, text)
    return [normalize_phone(m) for m in matches if len(re.sub(r'\D', '', m)) >= 8]


def extract_email_from_text(text):
    """Extract email addresses from text."""
    if not text:
        return []
    pattern = r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}'
    return re.findall(pattern, text)


def lookup_handle(db, handle_type, handle_value):
    """Look up an existing contact by handle."""
    row = db.execute(
        "SELECT * FROM contact_handles WHERE handle_type = ? AND handle_value = ?",
        (handle_type, handle_value)
    ).fetchone()
    return dict(row) if row else None


def get_all_contacts_with_handles(db):
    """Get all contacts with their handles for correlation."""
    contacts = db.execute("SELECT * FROM unified_contacts").fetchall()
    result = []
    for c in contacts:
        handles = db.execute(
            "SELECT * FROM contact_handles WHERE contact_id = ?",
            (c['id'],)
        ).fetchall()
        result.append({
            'contact': dict(c),
            'handles': [dict(h) for h in handles]
        })
    return result


def check_already_suggested(db, handle_type, handle_value, candidate_id):
    """Check if we've already made a suggestion for this handle+candidate pair."""
    row = db.execute(
        """SELECT * FROM contact_merge_suggestions
           WHERE new_handle_type = ? AND new_handle_value = ? AND candidate_contact_id = ?
           AND status IN ('pending', 'rejected', 'ignored')""",
        (handle_type, handle_value, candidate_id)
    ).fetchone()
    return row is not None


def create_merge_suggestion(db, handle_type, handle_value, display_name,
                           new_contact_id, candidate_contact_id,
                           correlation_reason, confidence):
    """Create a merge suggestion record."""
    suggestion_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    db.execute(
        """INSERT INTO contact_merge_suggestions
           (id, new_handle_type, new_handle_value, new_display_name,
            new_contact_id, candidate_contact_id, correlation_reason,
            confidence, status, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)""",
        (suggestion_id, handle_type, handle_value, display_name,
         new_contact_id, candidate_contact_id, correlation_reason,
         confidence, now)
    )
    return suggestion_id


def auto_merge(db, source_contact_id, target_contact_id):
    """Auto-merge source contact into target contact."""
    now = datetime.now(timezone.utc).isoformat()
    db.execute(
        "UPDATE contact_handles SET contact_id = ? WHERE contact_id = ?",
        (target_contact_id, source_contact_id)
    )
    db.execute(
        "UPDATE escalations SET contact_id = ? WHERE contact_id = ?",
        (target_contact_id, source_contact_id)
    )
    db.execute(
        "UPDATE unified_contacts SET auto_merged_count = auto_merged_count + 1, updated_at = ? WHERE id = ?",
        (now, target_contact_id)
    )
    db.execute("DELETE FROM unified_contacts WHERE id = ?", (source_contact_id,))
    db.commit()
    print(f"[contacts] Auto-merged {source_contact_id} into {target_contact_id}")


def send_merge_confirmation(db, suggestion_id, new_handle, new_name,
                           candidate_contact, candidate_handles, reason):
    """Send Telegram message with inline keyboard buttons to confirm/reject merge."""
    handles_text = ""
    for h in candidate_handles:
        icon = "\U0001f4f1" if h['handle_type'] == 'phone' else "\U0001f4e7"
        handles_text += f"     {icon} {h['handle_value']} ({h['handle_type']}, {h.get('message_count', 0)} msgs)\n"

    icon_new = "\U0001f4e7" if '@' in new_handle else "\U0001f4f1"
    text = (
        f"\U0001f517 <b>Contact Merge Suggestion</b>\n\n"
        f"New handle detected:\n"
        f"  {icon_new} {new_handle} (\"{new_name}\")\n\n"
        f"Possible match with existing contact:\n"
        f"  \U0001f464 <b>{candidate_contact['display_name']}</b>"
    )

    if candidate_contact.get('relation'):
        text += f" ({candidate_contact['relation']})"
    if candidate_contact.get('is_family'):
        text += " [family]"

    text += f"\n{handles_text}\n"
    text += f"Reason: {reason}"

    reply_markup = {
        "inline_keyboard": [[
            {"text": "\u2705 Merge", "callback_data": f"merge:{suggestion_id}"},
            {"text": "\u274c Keep Separate", "callback_data": f"reject:{suggestion_id}"}
        ]]
    }

    send_telegram(text, reply_markup=reply_markup)


def process_new_handle(db, handle_type, handle_value, display_name, source, message_text=None):
    """
    Process a new handle (phone or email).
    Returns (contact_id, action_taken).
    action_taken: 'existing', 'created', 'auto_merged', 'suggested'
    """
    now = datetime.now(timezone.utc).isoformat()

    # STEP 1: Exact handle lookup
    existing = lookup_handle(db, handle_type, handle_value)
    if existing:
        # Update last_seen and message_count
        db.execute(
            "UPDATE contact_handles SET last_seen = ?, message_count = message_count + 1 WHERE id = ?",
            (now, existing['id'])
        )
        db.execute(
            "UPDATE unified_contacts SET updated_at = ? WHERE id = ?",
            (now, existing['contact_id'])
        )
        db.commit()
        return existing['contact_id'], 'existing'

    # STEP 2: Create new contact + handle
    contact_id = str(uuid.uuid4())
    handle_id = str(uuid.uuid4())

    db.execute(
        """INSERT INTO unified_contacts
           (id, display_name, is_family, is_vip, auto_merged_count, created_at, updated_at)
           VALUES (?, ?, 0, 0, 0, ?, ?)""",
        (contact_id, display_name or handle_value, now, now)
    )
    db.execute(
        """INSERT INTO contact_handles
           (id, contact_id, handle_type, handle_value, display_name, source, first_seen, last_seen, message_count)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)""",
        (handle_id, contact_id, handle_type, handle_value, display_name, source, now, now)
    )
    db.commit()
    print(f"[contacts] New contact created: {display_name or handle_value} ({handle_value})")

    # STEP 3: Correlation check
    all_contacts = get_all_contacts_with_handles(db)
    best_match = None
    best_confidence = None
    best_reason = None

    for entry in all_contacts:
        candidate = entry['contact']
        if candidate['id'] == contact_id:
            continue

        candidate_handles = entry['handles']

        # Signal 1: Exact name match
        if display_name and candidate['display_name']:
            sim = name_similarity(display_name, candidate['display_name'])
            if sim >= 0.95:
                # Check if this is the only match with high similarity
                other_high_matches = sum(
                    1 for e in all_contacts
                    if e['contact']['id'] != contact_id
                    and e['contact']['id'] != candidate['id']
                    and name_similarity(display_name, e['contact']['display_name']) >= 0.95
                )
                if other_high_matches == 0:
                    best_match = candidate
                    best_confidence = 'high'
                    best_reason = f'Exact name match: "{display_name}" = "{candidate["display_name"]}"'
                    break
                else:
                    if not best_match or best_confidence not in ('high',):
                        best_match = candidate
                        best_confidence = 'medium'
                        best_reason = f'Name match (multiple candidates): "{display_name}" ~ "{candidate["display_name"]}"'

            elif sim >= 0.7:
                if not best_match or best_confidence == 'low':
                    best_match = candidate
                    best_confidence = 'medium'
                    best_reason = f'Fuzzy name match: "{display_name}" ~ "{candidate["display_name"]}" ({sim:.0%})'

        # Signal 2: Cross-reference in message content
        if message_text:
            for ch in candidate_handles:
                if ch['handle_type'] == 'phone':
                    phone_digits = ch['handle_value'].lstrip('+').replace('-', '').replace(' ', '')
                    if len(phone_digits) >= 8 and phone_digits in message_text.replace(' ', '').replace('-', ''):
                        best_match = candidate
                        best_confidence = 'high'
                        best_reason = f'Phone {ch["handle_value"]} found in message content'
                        break
                elif ch['handle_type'] == 'email':
                    if ch['handle_value'] in message_text:
                        best_match = candidate
                        best_confidence = 'high'
                        best_reason = f'Email {ch["handle_value"]} found in message content'
                        break

        # Signal 3: Same domain + similar name (email only)
        if handle_type == 'email' and '@' in handle_value:
            domain = handle_value.split('@')[1].lower()
            if domain not in ('gmail.com', 'yahoo.com', 'hotmail.com', 'outlook.com', 'icloud.com'):
                for ch in candidate_handles:
                    if ch['handle_type'] == 'email' and '@' in ch['handle_value']:
                        c_domain = ch['handle_value'].split('@')[1].lower()
                        if domain == c_domain and display_name:
                            sim = name_similarity(display_name, candidate['display_name'])
                            if sim >= 0.5:
                                if not best_match or best_confidence == 'low':
                                    best_match = candidate
                                    best_confidence = 'medium'
                                    best_reason = f'Same domain @{domain} + similar name ({sim:.0%})'

    if best_match:
        if best_confidence == 'high':
            # Auto-merge
            auto_merge(db, contact_id, best_match['id'])
            return best_match['id'], 'auto_merged'
        elif best_confidence == 'medium':
            # Check if we already suggested this
            if not check_already_suggested(db, handle_type, handle_value, best_match['id']):
                suggestion_id = create_merge_suggestion(
                    db, handle_type, handle_value, display_name,
                    contact_id, best_match['id'], best_reason, best_confidence
                )
                db.commit()

                candidate_handles = db.execute(
                    "SELECT * FROM contact_handles WHERE contact_id = ?",
                    (best_match['id'],)
                ).fetchall()

                send_merge_confirmation(
                    db, suggestion_id, handle_value, display_name,
                    best_match, [dict(h) for h in candidate_handles], best_reason
                )
                return contact_id, 'suggested'

    return contact_id, 'created'


def process_email_batch_contacts(batch):
    """Process contacts from an email batch."""
    db = get_db()
    results = []

    for email_msg in batch.get('emails', []):
        from_addr = email_msg.get('from_addr', '')
        from_name = email_msg.get('from_name', '')
        body_text = email_msg.get('body_text', '')

        if not from_addr:
            continue

        contact_id, action = process_new_handle(
            db, 'email', from_addr, from_name, 'email', body_text
        )
        results.append({
            'from_addr': from_addr,
            'from_name': from_name,
            'contact_id': contact_id,
            'action': action
        })

        # Also try to extract phone numbers from email body/signature
        if body_text:
            phones = extract_phone_from_text(body_text[-500:])  # Check last 500 chars (signature area)
            for phone in phones:
                normalized = normalize_phone(phone)
                if len(normalized) >= 10:
                    existing_phone = lookup_handle(db, 'phone', normalized)
                    if existing_phone and existing_phone['contact_id'] != contact_id:
                        if not check_already_suggested(db, 'phone', normalized, existing_phone['contact_id']):
                            pass  # Cross-reference will be handled by name matching

    db.close()
    return results


def process_whatsapp_message_contact(sender_phone, sender_name, message_text=None):
    """Process a contact from a WhatsApp message."""
    db = get_db()
    normalized_phone = normalize_phone(sender_phone)
    contact_id, action = process_new_handle(
        db, 'phone', normalized_phone, sender_name, 'whatsapp', message_text
    )
    db.close()
    return contact_id, action


if __name__ == '__main__':
    # Self-test: process all unbatched email senders
    print("[contacts] Running contact auto-management scan...")
    db = get_db()

    # Get unique senders not yet in contacts
    senders = db.execute("""
        SELECT DISTINCT from_addr, from_name
        FROM email_messages
        WHERE from_addr NOT IN (SELECT handle_value FROM contact_handles WHERE handle_type = 'email')
        LIMIT 100
    """).fetchall()

    print(f"[contacts] Found {len(senders)} new email senders to process")

    for s in senders:
        contact_id, action = process_new_handle(
            db, 'email', s['from_addr'], s['from_name'], 'email'
        )
        print(f"  {s['from_addr']} ({s['from_name']}): {action}")

    # Stats
    total_contacts = db.execute("SELECT COUNT(*) FROM unified_contacts").fetchone()[0]
    total_handles = db.execute("SELECT COUNT(*) FROM contact_handles").fetchone()[0]
    pending_merges = db.execute("SELECT COUNT(*) FROM contact_merge_suggestions WHERE status = 'pending'").fetchone()[0]

    print(f"\n[contacts] Summary: {total_contacts} contacts, {total_handles} handles, {pending_merges} pending merge suggestions")
    db.close()
