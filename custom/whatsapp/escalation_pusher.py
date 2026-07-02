#!/usr/bin/env python3
"""
WhatsApp Escalation Pusher

Monitors the escalations table for pending items and pushes them to Telegram.
Runs as a daemon alongside the triage agent.
"""

import json
import os
import sqlite3
import time
from datetime import datetime, timezone
from urllib.request import Request, urlopen

# Config
CONFIG_PATH = '/opt/data/whatsapp-messages/config.json'
DB_PATH = '/opt/data/whatsapp-messages/whatsapp_data.db'
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', '')
TELEGRAM_USER_ID = os.environ.get('TELEGRAM_ALLOWED_USERS', '').split(',')[0]
CHECK_INTERVAL = 5  # seconds

def load_config():
    with open(CONFIG_PATH) as f:
        return json.load(f)

config = load_config()
FAMILY_CONTACTS = {
    c['phone']: c for c in config.get('escalation', {}).get('criteria', {}).get('family_contacts', [])
}


def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def send_telegram(text, parse_mode='Markdown'):
    """Send a message to Telegram via Bot API."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_USER_ID:
        print(f"[escalation] No Telegram credentials, would send: {text[:100]}")
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
            print(f"[escalation] Telegram message sent successfully")
            return True
        else:
            print(f"[escalation] Telegram API error: {data}")
            return False
    except Exception as e:
        print(f"[escalation] Telegram send error: {e}")
        return False


def format_escalation(row):
    """Format an escalation as a Telegram message."""
    sender = row['sender_phone']
    reason = row['reason']
    summary = row['summary']
    priority = row['priority']
    source = row['source_phone']
    
    # Get sender name
    sender_name = ''
    if sender in FAMILY_CONTACTS:
        fc = FAMILY_CONTACTS[sender]
        sender_name = f"{fc['name']} ({fc.get('relation', '')})"
    
    # Build message
    emoji_map = {
        'family': '\u2764\ufe0f',
        'urgent_business': '\u26a0\ufe0f',
        'sales_opportunity': '\U0001f4b0',
    }
    emoji = emoji_map.get(reason, '\U0001f4e8')
    
    phone_label = 'Phone 1' if source == 'phone1' else 'Phone 2'
    
    lines = [
        f"{emoji} *WhatsApp Escalation* [{priority.upper()}]",
        f"",
        f"*From:* {sender_name or sender}",
        f"*Reason:* {reason.replace('_', ' ').title()}",
        f"*Phone:* {phone_label}",
        f"",
        f"{summary}",
    ]
    
    return '\n'.join(lines)


def process_pending_escalations():
    """Check for and push pending escalations."""
    db = get_db()
    pending = db.execute(
        "SELECT * FROM escalations WHERE status = 'pending' ORDER BY created_at ASC"
    ).fetchall()
    
    for row in pending:
        msg_text = format_escalation(row)
        success = send_telegram(msg_text)
        
        if success:
            now = datetime.now(timezone.utc).isoformat()
            db.execute(
                "UPDATE escalations SET status = 'delivered', delivered_at = ? WHERE id = ?",
                (now, row['id'])
            )
            db.commit()
        else:
            # Don't retry immediately, will pick up next cycle
            break
    
    db.close()
    return len(pending)


def main():
    print(f"[escalation] Starting escalation pusher")
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
