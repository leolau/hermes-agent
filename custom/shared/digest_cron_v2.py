#!/usr/bin/env python3
"""
Unified Hourly Digest for Hermes Agent

Generates a merged summary of WhatsApp + Email activity every hour
and sends to Telegram. Uses DeepSeek for summary generation.
"""

import json
import os
import sqlite3
import time
import uuid
from datetime import datetime, timezone, timedelta
from urllib.request import Request, urlopen

# Config
WA_CONFIG_PATH = '/opt/data/whatsapp-messages/config.json'
DB_PATH = '/opt/data/whatsapp-messages/whatsapp_data.db'
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', '')
TELEGRAM_USER_ID = os.environ.get('TELEGRAM_ALLOWED_USERS', '').split(',')[0]
DEEPSEEK_API_KEY = os.environ.get('DEEPSEEK_API_KEY', '')

def load_config():
    try:
        with open(WA_CONFIG_PATH) as f:
            return json.load(f)
    except Exception:
        return {}

config = load_config()
DIGEST_INTERVAL_MIN = config.get('digest', {}).get('frequency_minutes', 60)


def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def send_telegram(text, parse_mode='HTML'):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_USER_ID:
        print(f"[digest] No Telegram creds, would send: {text[:100]}")
        return False

    url = f'https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage'
    payload = {
        'chat_id': TELEGRAM_USER_ID,
        'text': text,
        'parse_mode': parse_mode,
    }

    req = Request(url, data=json.dumps(payload).encode(),
                  headers={'Content-Type': 'application/json'})
    try:
        resp = urlopen(req, timeout=10)
        data = json.loads(resp.read().decode())
        return data.get('ok', False)
    except Exception as e:
        print(f"[digest] Telegram error: {e}")
        return False


def call_deepseek(prompt):
    url = 'https://api.deepseek.com/v1/chat/completions'
    payload = {
        'model': 'deepseek-chat',
        'messages': [
            {'role': 'system', 'content': 'You are a concise personal assistant summarizing communication activity across WhatsApp and Email. Be brief but informative. Use bullet points. Group by channel when relevant.'},
            {'role': 'user', 'content': prompt}
        ],
        'temperature': 0.3,
        'max_tokens': 1200,
    }

    req = Request(url, data=json.dumps(payload).encode(),
                  headers={'Content-Type': 'application/json',
                          'Authorization': f'Bearer {DEEPSEEK_API_KEY}'})
    try:
        resp = urlopen(req, timeout=30)
        data = json.loads(resp.read().decode())
        return data['choices'][0]['message']['content']
    except Exception as e:
        print(f"[digest] DeepSeek error: {e}")
        return None


def generate_digest():
    """Generate a merged hourly digest of WhatsApp + Email activity."""
    db = get_db()
    now = datetime.now(timezone.utc)
    since = (now - timedelta(minutes=DIGEST_INTERVAL_MIN)).isoformat()

    # === WhatsApp Stats ===
    wa_msg_count = 0
    wa_top_senders = []
    wa_sample_msgs = []
    try:
        wa_msg_count = db.execute(
            "SELECT COUNT(*) as c FROM messages WHERE received_at >= ?", (since,)
        ).fetchone()['c']

        wa_top_senders = db.execute(
            """SELECT sender_phone, sender_name, COUNT(*) as cnt
               FROM messages WHERE received_at >= ? AND sender_phone != ''
               GROUP BY sender_phone ORDER BY cnt DESC LIMIT 5""",
            (since,)
        ).fetchall()

        wa_sample_msgs = db.execute(
            """SELECT sender_name, text FROM messages
               WHERE received_at >= ? AND text IS NOT NULL AND text != ''
               ORDER BY timestamp DESC LIMIT 10""",
            (since,)
        ).fetchall()
    except Exception:
        pass

    # === Email Stats ===
    email_count = db.execute(
        "SELECT COUNT(*) as c FROM email_messages WHERE received_at >= ?", (since,)
    ).fetchone()['c']

    email_top_senders = db.execute(
        """SELECT from_addr, from_name, COUNT(*) as cnt
           FROM email_messages WHERE received_at >= ?
           GROUP BY from_addr ORDER BY cnt DESC LIMIT 5""",
        (since,)
    ).fetchall()

    email_sample = db.execute(
        """SELECT from_name, subject FROM email_messages
           WHERE received_at >= ? AND subject IS NOT NULL
           ORDER BY received_at DESC LIMIT 10""",
        (since,)
    ).fetchall()

    # === Shared Stats ===
    wa_pending_tasks = db.execute(
        "SELECT description, priority, due_date FROM wa_tasks WHERE status = 'pending' ORDER BY created_at DESC LIMIT 5"
    ).fetchall()

    email_pending_tasks = db.execute(
        "SELECT description, priority, due_date FROM email_tasks WHERE status = 'pending' ORDER BY created_at DESC LIMIT 5"
    ).fetchall()

    wa_escalations = db.execute(
        "SELECT sender_phone, reason, summary, status FROM escalations WHERE channel = 'whatsapp' AND created_at >= ? ORDER BY created_at DESC",
        (since,)
    ).fetchall()

    email_escalations = db.execute(
        "SELECT sender_email, reason, summary, status FROM escalations WHERE channel = 'email' AND created_at >= ? ORDER BY created_at DESC",
        (since,)
    ).fetchall()

    # Contact stats
    total_contacts = db.execute("SELECT COUNT(*) FROM unified_contacts").fetchone()[0]
    pending_merges = db.execute("SELECT COUNT(*) FROM contact_merge_suggestions WHERE status = 'pending'").fetchone()[0]

    total_msgs = wa_msg_count + email_count

    if total_msgs == 0 and not wa_pending_tasks and not email_pending_tasks:
        print(f"[digest] No activity in last {DIGEST_INTERVAL_MIN}min, skipping digest")
        db.close()
        return

    # Build context for LLM
    context_parts = [
        f"Time period: Last {DIGEST_INTERVAL_MIN} minutes",
        f"Total messages: {total_msgs} (WhatsApp: {wa_msg_count}, Email: {email_count})",
    ]

    if wa_msg_count > 0:
        context_parts.append("\n--- WHATSAPP ---")
        context_parts.append("Top senders:")
        for s in wa_top_senders:
            context_parts.append(f"  - {s['sender_name'] or s['sender_phone']}: {s['cnt']} msgs")
        if wa_sample_msgs:
            context_parts.append("Recent messages:")
            for m in wa_sample_msgs[:5]:
                text = (m['text'] or '')[:80]
                context_parts.append(f"  - {m['sender_name'] or 'Unknown'}: {text}")

    if email_count > 0:
        context_parts.append("\n--- EMAIL ---")
        context_parts.append("Top senders:")
        for s in email_top_senders:
            context_parts.append(f"  - {s['from_name'] or s['from_addr']}: {s['cnt']} emails")
        if email_sample:
            context_parts.append("Recent emails:")
            for e in email_sample[:5]:
                context_parts.append(f"  - {e['from_name'] or 'Unknown'}: {e['subject']}")

    all_tasks = list(wa_pending_tasks) + list(email_pending_tasks)
    if all_tasks:
        context_parts.append(f"\n--- PENDING TASKS ({len(all_tasks)}) ---")
        for t in all_tasks[:5]:
            context_parts.append(f"  - [{t['priority']}] {t['description']} (due: {t['due_date'] or 'none'})")

    all_escalations = list(wa_escalations) + list(email_escalations)
    if all_escalations:
        context_parts.append(f"\n--- ESCALATIONS ({len(all_escalations)}) ---")
        for e in wa_escalations:
            context_parts.append(f"  - [WA] {e['reason']}: {e['summary']} [{e['status']}]")
        for e in email_escalations:
            context_parts.append(f"  - [Email] {e['reason']}: {e['summary'][:80]} [{e['status']}]")

    prompt = "\n".join(context_parts)
    prompt += "\n\nGenerate a brief hourly digest summary (4-6 bullet points max). Group by channel if both have activity. Focus on what needs attention."

    summary = call_deepseek(prompt)
    if not summary:
        # Fallback
        parts = []
        if wa_msg_count > 0:
            parts.append(f"WhatsApp: {wa_msg_count} messages")
        if email_count > 0:
            parts.append(f"Email: {email_count} emails")
        if all_tasks:
            parts.append(f"{len(all_tasks)} pending task(s)")
        summary = " | ".join(parts) if parts else "No significant activity"

    # Format final message
    digest_msg = (
        f"\U0001f4cb <b>Hourly Digest</b>\n"
        f"<i>{now.strftime('%H:%M UTC')} | WA: {wa_msg_count} msgs, Email: {email_count} emails</i>\n\n"
        f"{summary}"
    )

    if pending_merges > 0:
        digest_msg += f"\n\n\U0001f517 {pending_merges} pending contact merge suggestion(s)"

    success = send_telegram(digest_msg, parse_mode='HTML')

    # Record in wa_digests (WhatsApp portion)
    digest_id = str(uuid.uuid4())
    try:
        db.execute(
            """INSERT INTO wa_digests (id, scope, content, message_count, task_count, sent_at, channel)
               VALUES (?, 'all', ?, ?, ?, ?, 'telegram')""",
            (digest_id, summary, wa_msg_count, len(wa_pending_tasks), now.isoformat())
        )
    except Exception:
        pass

    # Record in email_digests
    digest_id2 = str(uuid.uuid4())
    try:
        db.execute(
            """INSERT INTO email_digests (id, scope, content, email_count, task_count, sent_at, channel)
               VALUES (?, 'all', ?, ?, ?, ?, 'telegram')""",
            (digest_id2, summary, email_count, len(email_pending_tasks), now.isoformat())
        )
    except Exception:
        pass

    db.commit()
    db.close()

    print(f"[digest] Sent merged digest: WA={wa_msg_count} msgs, Email={email_count}, "
          f"tasks={len(all_tasks)}, escalations={len(all_escalations)}")


def main():
    print(f"[digest] Starting unified digest (WhatsApp + Email, interval: {DIGEST_INTERVAL_MIN}min)")

    while True:
        try:
            generate_digest()
        except Exception as e:
            print(f"[digest] Error: {e}")
            import traceback
            traceback.print_exc()

        time.sleep(DIGEST_INTERVAL_MIN * 60)


if __name__ == '__main__':
    main()
