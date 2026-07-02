#!/usr/bin/env python3
"""
Unified Escalation Pusher for Hermes Agent

Monitors the escalations table for pending items from BOTH WhatsApp and Email,
formats them channel-appropriately, and pushes to Telegram.
"""

import json
import os
import sqlite3
import time
from datetime import datetime, timezone
from urllib.request import Request, urlopen

# Config
WA_CONFIG_PATH = '/opt/data/whatsapp-messages/config.json'
DB_PATH = '/opt/data/whatsapp-messages/whatsapp_data.db'
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', '')
TELEGRAM_USER_ID = os.environ.get('TELEGRAM_ALLOWED_USERS', '').split(',')[0]
CHECK_INTERVAL = 5


def load_config():
    try:
        with open(WA_CONFIG_PATH) as f:
            return json.load(f)
    except Exception:
        return {}


config = load_config()
FAMILY_CONTACTS = {
    c['phone']: c for c in config.get('escalation', {}).get('criteria', {}).get('family_contacts', [])
}


def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def send_telegram(text, parse_mode='HTML'):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_USER_ID:
        print(f"[escalation] No Telegram creds, would send: {text[:100]}")
        return False

    url = f'https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage'
    payload = {
        'chat_id': TELEGRAM_USER_ID,
        'text': text,
        'parse_mode': parse_mode,
    }

    req = Request(
        url,
        data=json.dumps(payload).encode(),
        headers={'Content-Type': 'application/json'}
    )

    try:
        resp = urlopen(req, timeout=10)
        data = json.loads(resp.read().decode())
        if data.get('ok'):
            print(f"[escalation] Telegram message sent")
            return True
        else:
            print(f"[escalation] Telegram API error: {data}")
            return False
    except Exception as e:
        print(f"[escalation] Telegram send error: {e}")
        return False


def get_contact_info(db, contact_id):
    """Get contact details and handles."""
    if not contact_id:
        return None
    contact = db.execute("SELECT * FROM unified_contacts WHERE id = ?", (contact_id,)).fetchone()
    if not contact:
        return None
    handles = db.execute("SELECT * FROM contact_handles WHERE contact_id = ?", (contact_id,)).fetchall()
    return {'contact': dict(contact), 'handles': [dict(h) for h in handles]}


def format_whatsapp_escalation(row, db):
    """Format a WhatsApp escalation for Telegram."""
    sender = row['sender_phone'] or ''
    reason = row['reason'] or 'unknown'
    summary = row['summary'] or ''
    priority = row['priority'] or 'medium'
    source = row['source_phone'] or ''

    sender_name = ''
    if sender in FAMILY_CONTACTS:
        fc = FAMILY_CONTACTS[sender]
        sender_name = f"{fc['name']} ({fc.get('relation', '')})"

    # Try unified contacts
    if not sender_name and row.get('contact_id'):
        info = get_contact_info(db, row['contact_id'])
        if info:
            sender_name = info['contact']['display_name']
            if info['contact'].get('relation'):
                sender_name += f" ({info['contact']['relation']})"

    emoji_map = {
        'family': '\u2764\ufe0f',
        'urgent_business': '\u26a0\ufe0f',
        'sales_opportunity': '\U0001f4b0',
    }
    emoji = emoji_map.get(reason, '\U0001f4e8')

    phone_label = 'Phone 1' if source == 'phone1' else 'Phone 2'

    text = (
        f"{emoji} <b>WhatsApp Escalation</b> [{priority.upper()}]\n\n"
        f"<b>From:</b> {sender_name or sender}\n"
        f"<b>Reason:</b> {reason.replace('_', ' ').title()}\n"
        f"<b>Phone:</b> {phone_label}\n\n"
        f"{summary}"
    )
    return text


def format_email_escalation(row, db):
    """Format an email escalation for Telegram."""
    sender_email = row.get('sender_email') or ''
    sender_name = row.get('sender_name') or ''
    reason = row['reason'] or 'unknown'
    summary = row['summary'] or ''
    priority = row['priority'] or 'medium'
    account_id = row['source_phone'] or ''  # source_phone stores account_id for email

    # Try unified contacts for more info
    if row.get('contact_id'):
        info = get_contact_info(db, row['contact_id'])
        if info:
            if not sender_name:
                sender_name = info['contact']['display_name']
            if info['contact'].get('relation'):
                sender_name += f" ({info['contact']['relation']})"
            if info['contact'].get('is_family'):
                sender_name += " [family]"

    # Get email subject if possible
    subject = ''
    if row.get('source_msg_id'):
        email_row = db.execute(
            "SELECT subject FROM email_messages WHERE id = ?",
            (row['source_msg_id'],)
        ).fetchone()
        if email_row:
            subject = email_row['subject'] or ''

    # Account label
    account_label = account_id
    try:
        acc = db.execute("SELECT label FROM email_accounts WHERE id = ?", (account_id,)).fetchone()
        if acc:
            account_label = acc['label']
    except Exception:
        pass

    emoji_map = {
        'family': '\u2764\ufe0f',
        'urgent_business': '\u26a0\ufe0f',
        'sales_opportunity': '\U0001f4b0',
        'invoice': '\U0001f4b3',
        'client_email': '\U0001f465',
        'vip_sender': '\u2b50',
    }
    emoji = emoji_map.get(reason, '\U0001f4e7')

    text = (
        f"{emoji} <b>Email Escalation</b> [{priority.upper()}]\n\n"
        f"<b>From:</b> {sender_name or sender_email}\n"
        f"<b>Email:</b> {sender_email}\n"
        f"<b>Account:</b> {account_label}\n"
        f"<b>Reason:</b> {reason.replace('_', ' ').title()}\n"
    )
    if subject:
        text += f"<b>Subject:</b> {subject}\n"
    text += f"\n{summary}"

    return text


def process_pending_escalations():
    """Check for and push pending escalations from both channels."""
    db = get_db()
    pending = db.execute(
        "SELECT * FROM escalations WHERE status = 'pending' ORDER BY created_at ASC"
    ).fetchall()

    for row in pending:
        channel = row.get('channel') or 'whatsapp'

        if channel == 'email':
            msg_text = format_email_escalation(row, db)
        else:
            msg_text = format_whatsapp_escalation(row, db)

        success = send_telegram(msg_text, parse_mode='HTML')

        if success:
            now = datetime.now(timezone.utc).isoformat()
            db.execute(
                "UPDATE escalations SET status = 'delivered', delivered_at = ? WHERE id = ?",
                (now, row['id'])
            )
            db.commit()
        else:
            break

    db.close()
    return len(pending)


def main():
    print(f"[escalation] Starting unified escalation pusher (WhatsApp + Email)")
    print(f"[escalation] Telegram user: {TELEGRAM_USER_ID}")
    print(f"[escalation] Check interval: {CHECK_INTERVAL}s")

    while True:
        try:
            count = process_pending_escalations()
            if count > 0:
                print(f"[escalation] Processed {count} pending escalation(s)")
        except Exception as e:
            print(f"[escalation] Error: {e}")

        time.sleep(CHECK_INTERVAL)


if __name__ == '__main__':
    main()
