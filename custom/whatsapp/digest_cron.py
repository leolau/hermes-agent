#!/usr/bin/env python3
"""
WhatsApp Hourly Digest

Generates a summary of WhatsApp activity every hour and sends to Telegram.
Uses DeepSeek for summary generation.
"""

import json
import os
import sqlite3
import time
import uuid
from datetime import datetime, timezone, timedelta
from urllib.request import Request, urlopen

# Config
CONFIG_PATH = '/opt/data/whatsapp-messages/config.json'
DB_PATH = '/opt/data/whatsapp-messages/whatsapp_data.db'
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', '')
TELEGRAM_USER_ID = os.environ.get('TELEGRAM_ALLOWED_USERS', '').split(',')[0]
DEEPSEEK_API_KEY = os.environ.get('DEEPSEEK_API_KEY', '')

def load_config():
    with open(CONFIG_PATH) as f:
        return json.load(f)

config = load_config()
DIGEST_INTERVAL_MIN = config.get('digest', {}).get('frequency_minutes', 60)


def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def send_telegram(text, parse_mode='Markdown'):
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
            {'role': 'system', 'content': 'You are a concise personal assistant summarizing WhatsApp activity. Be brief but informative. Use bullet points.'},
            {'role': 'user', 'content': prompt}
        ],
        'temperature': 0.3,
        'max_tokens': 1000,
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
    """Generate an hourly digest of WhatsApp activity."""
    db = get_db()
    now = datetime.now(timezone.utc)
    since = (now - timedelta(minutes=DIGEST_INTERVAL_MIN)).isoformat()
    
    # Get message stats
    msg_count = db.execute(
        "SELECT COUNT(*) as c FROM messages WHERE received_at >= ?", (since,)
    ).fetchone()['c']
    
    if msg_count == 0:
        print(f"[digest] No messages in last {DIGEST_INTERVAL_MIN}min, skipping digest")
        db.close()
        return
    
    # Get top senders
    top_senders = db.execute(
        """SELECT sender_phone, sender_name, COUNT(*) as cnt 
           FROM messages WHERE received_at >= ? AND sender_phone != ''
           GROUP BY sender_phone ORDER BY cnt DESC LIMIT 5""",
        (since,)
    ).fetchall()
    
    # Get pending tasks
    pending_tasks = db.execute(
        "SELECT description, priority, due_date FROM tasks WHERE status = 'pending' ORDER BY created_at DESC LIMIT 5"
    ).fetchall()
    
    # Get recent escalations
    escalations = db.execute(
        "SELECT sender_phone, reason, summary, status FROM escalations WHERE created_at >= ? ORDER BY created_at DESC",
        (since,)
    ).fetchall()
    
    # Get sample messages for LLM summary
    sample_msgs = db.execute(
        """SELECT sender_name, text FROM messages 
           WHERE received_at >= ? AND text IS NOT NULL AND text != '' 
           ORDER BY timestamp DESC LIMIT 15""",
        (since,)
    ).fetchall()
    
    # Build context for LLM
    context_parts = [
        f"Time period: Last {DIGEST_INTERVAL_MIN} minutes",
        f"Total messages received: {msg_count}",
        f"\nTop senders:",
    ]
    for s in top_senders:
        context_parts.append(f"  - {s['sender_name'] or s['sender_phone']}: {s['cnt']} msgs")
    
    if pending_tasks:
        context_parts.append(f"\nPending tasks ({len(pending_tasks)}):")
        for t in pending_tasks:
            context_parts.append(f"  - [{t['priority']}] {t['description']} (due: {t['due_date'] or 'none'})")
    
    if escalations:
        context_parts.append(f"\nEscalations ({len(escalations)}):")
        for e in escalations:
            context_parts.append(f"  - {e['reason']}: {e['summary']} [{e['status']}]")
    
    if sample_msgs:
        context_parts.append(f"\nRecent message samples:")
        for m in sample_msgs[:10]:
            text = (m['text'] or '')[:80]
            context_parts.append(f"  - {m['sender_name'] or 'Unknown'}: {text}")
    
    prompt = "\n".join(context_parts)
    prompt += "\n\nGenerate a brief hourly digest summary (3-5 bullet points max). Focus on what needs attention."
    
    # Generate summary
    summary = call_deepseek(prompt)
    if not summary:
        # Fallback: structured summary without LLM
        summary = f"*{msg_count} messages* in the last hour"
        if top_senders:
            summary += f"\nTop: {', '.join(s['sender_name'] or s['sender_phone'] for s in top_senders[:3])}"
        if pending_tasks:
            summary += f"\n{len(pending_tasks)} pending task(s)"
    
    # Format final digest message
    digest_msg = f"\U0001f4cb *Hourly WhatsApp Digest*\n_{now.strftime('%H:%M UTC')} | {msg_count} messages_\n\n{summary}"
    
    # Send to Telegram
    success = send_telegram(digest_msg)
    
    # Record digest in DB
    digest_id = str(uuid.uuid4())
    db.execute(
        """INSERT INTO digests (id, scope, content, message_count, task_count, sent_at, channel)
           VALUES (?, 'all', ?, ?, ?, ?, 'telegram')""",
        (digest_id, summary, msg_count, len(pending_tasks), now.isoformat())
    )
    db.commit()
    db.close()
    
    print(f"[digest] Sent digest: {msg_count} msgs, {len(pending_tasks)} tasks, {len(escalations)} escalations")


def main():
    print(f"[digest] Starting hourly digest (interval: {DIGEST_INTERVAL_MIN}min)")
    
    while True:
        try:
            generate_digest()
        except Exception as e:
            print(f"[digest] Error: {e}")
        
        # Sleep until next digest
        time.sleep(DIGEST_INTERVAL_MIN * 60)


if __name__ == '__main__':
    main()
