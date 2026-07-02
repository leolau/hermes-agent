#!/usr/bin/env python3
"""
Email Triage Agent for Hermes Agent

Watches email batch files, classifies emails using DeepSeek,
extracts tasks/notes, creates escalations. Uses contact_manager
for auto-contact creation and correlation.
"""

import json
import os
import sqlite3
import time
import uuid
import glob
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

# Paths
EMAIL_CONFIG_PATH = '/opt/data/email-messages/config.json'
DB_PATH = '/opt/data/whatsapp-messages/whatsapp_data.db'
EMAIL_BATCH_DIR = '/opt/data/email-messages/batches'
WA_SKILLS_DIR = '/opt/data/skills/whatsapp-triage'
EMAIL_SKILLS_DIR = '/opt/data/skills/email-triage'
EMAIL_PROCESSED_DIR = '/opt/data/email-messages/batches/processed'

os.makedirs(EMAIL_PROCESSED_DIR, exist_ok=True)

# Add contact_manager to path
sys.path.insert(0, '/opt/data/whatsapp-messages')

DEEPSEEK_API_KEY = os.environ.get('DEEPSEEK_API_KEY', '')
DEEPSEEK_MODEL = 'deepseek-chat'
DEEPSEEK_BASE_URL = 'https://api.deepseek.com/v1/chat/completions'


def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def load_email_config():
    with open(EMAIL_CONFIG_PATH) as f:
        return json.load(f)


def load_skills(channel='email'):
    """Load skill .md files from both shared and email-specific directories."""
    skills_content = []
    dirs = [WA_SKILLS_DIR, EMAIL_SKILLS_DIR]
    if channel == 'email':
        dirs.append(os.path.join(EMAIL_SKILLS_DIR, 'custom'))

    for sdir in dirs:
        if not os.path.isdir(sdir):
            continue
        for f in sorted(glob.glob(os.path.join(sdir, '*.md'))):
            if f.endswith('.disabled'):
                continue
            with open(f) as fh:
                content = fh.read()
            skills_content.append(f"## Skill: {os.path.basename(f)}\n{content}")

    return "\n\n---\n\n".join(skills_content)


def call_deepseek(messages, temperature=0.3, max_tokens=2000):
    """Call DeepSeek Chat API."""
    payload = {
        'model': DEEPSEEK_MODEL,
        'messages': messages,
        'temperature': temperature,
        'max_tokens': max_tokens,
        'response_format': {'type': 'json_object'},
    }

    req = Request(
        DEEPSEEK_BASE_URL,
        data=json.dumps(payload).encode(),
        headers={
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {DEEPSEEK_API_KEY}',
        }
    )

    try:
        resp = urlopen(req, timeout=60)
        data = json.loads(resp.read().decode())
        return data['choices'][0]['message']['content']
    except Exception as e:
        print(f"[email-triage] DeepSeek API error: {e}")
        return None


def triage_email_batch(batch):
    """Run triage on an email batch."""
    skills_text = load_skills('email')
    sender = batch['sender']
    sender_name = batch.get('sender_name', '')
    account_id = batch['account_id']

    # Check if sender is VIP/family via contact system
    db = get_db()
    handle = db.execute(
        "SELECT ch.contact_id FROM contact_handles ch WHERE ch.handle_type = 'email' AND ch.handle_value = ?",
        (sender,)
    ).fetchone()

    is_family = False
    is_vip = False
    contact_name = ''
    if handle:
        contact = db.execute(
            "SELECT * FROM unified_contacts WHERE id = ?",
            (handle['contact_id'],)
        ).fetchone()
        if contact:
            is_family = contact['is_family'] == 1
            is_vip = contact['is_vip'] == 1
            contact_name = contact['display_name']
    db.close()

    emails_text = ""
    for em in batch['emails']:
        subject = em.get('subject', '(no subject)')
        body = (em.get('body_text', '') or '')[:1000]  # Truncate long bodies
        attach = ''
        if em.get('has_attachments'):
            attach = ' [HAS ATTACHMENTS]'
        emails_text += f"- Subject: {subject}{attach}\n  Body: {body[:500]}\n\n"

    family_info = ""
    if is_family:
        family_info = f"\nSender is a FAMILY MEMBER: {contact_name}. ALL emails from family must be escalated.\n"
    if is_vip:
        family_info += f"\nSender is a VIP CONTACT: {contact_name}. Escalate important emails.\n"

    system_prompt = f"""You are an email triage agent. Your job is to analyze incoming emails and:
1. Classify the overall batch
2. Extract any tasks or action items
3. Extract any notes worth remembering
4. Determine if this batch should be escalated (pushed immediately to the user)

{skills_text}

ESCALATION RULES:
- ANY email from a family contact -> ESCALATE (reason: "family")
- ANY email from a VIP contact with important content -> ESCALATE (reason: "vip_sender")
- Business requests needing immediate attention -> ESCALATE (reason: "urgent_business")
- Sales/RFP/proposal deadlines within 24h -> ESCALATE (reason: "sales_opportunity")
- Invoices and payment requests -> ESCALATE (reason: "invoice")
- Client emails needing response -> ESCALATE (reason: "client_email")
- Newsletters, marketing, auto-generated notifications -> DO NOT escalate
- Automated alerts and system notifications -> DO NOT escalate
{family_info}

Respond in JSON format:
{{
  "classification": "urgent_business|meeting|task|invoice|sales_opportunity|informational|newsletter|notification|personal|spam",
  "summary": "brief 1-2 sentence summary of the batch",
  "escalate": true/false,
  "escalation_reason": "family|vip_sender|urgent_business|sales_opportunity|invoice|client_email|null",
  "escalation_priority": "high|medium|low",
  "tasks": [
    {{"description": "...", "due_date": "YYYY-MM-DD or null", "priority": "high|medium|low"}}
  ],
  "notes": [
    {{"content": "..."}}
  ]
}}
"""

    user_prompt = f"""Triage this email batch:

Sender: {sender} (Name: {sender_name or 'Unknown'})
Account: {account_id}
Email Count: {batch['email_count']}
Is Family: {is_family}
Is VIP: {is_vip}

Emails:
{emails_text}
"""

    response = call_deepseek([
        {'role': 'system', 'content': system_prompt},
        {'role': 'user', 'content': user_prompt},
    ])

    if not response:
        return None

    try:
        return json.loads(response)
    except json.JSONDecodeError:
        print(f"[email-triage] Failed to parse response: {response[:200]}")
        return None


def process_email_triage_result(batch, result):
    """Write email triage results to SQLite."""
    db = get_db()
    now = datetime.now(timezone.utc).isoformat()
    account_id = batch['account_id']
    first_email_id = batch['emails'][0]['id'] if batch['emails'] else None

    # Write tasks to email_tasks
    for task in result.get('tasks', []):
        task_id = str(uuid.uuid4())
        try:
            db.execute(
                """INSERT INTO email_tasks (id, account_id, source_email_id, description, due_date, status, priority, created_at)
                   VALUES (?, ?, ?, ?, ?, 'pending', ?, ?)""",
                (task_id, account_id, first_email_id,
                 task['description'], task.get('due_date'), task.get('priority', 'medium'), now)
            )
        except Exception as e:
            print(f"[email-triage] Error inserting task: {e}")

    # Write notes to email_notes
    for note in result.get('notes', []):
        note_id = str(uuid.uuid4())
        try:
            db.execute(
                """INSERT INTO email_notes (id, account_id, source_email_id, content, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (note_id, account_id, first_email_id,
                 note['content'], now)
            )
        except Exception as e:
            print(f"[email-triage] Error inserting note: {e}")

    # Write escalation if needed
    if result.get('escalate'):
        esc_id = str(uuid.uuid4())

        # Look up contact_id
        contact_id = None
        handle = db.execute(
            "SELECT contact_id FROM contact_handles WHERE handle_type = 'email' AND handle_value = ?",
            (batch['sender'],)
        ).fetchone()
        if handle:
            contact_id = handle['contact_id']

        try:
            db.execute(
                """INSERT INTO escalations (id, source_phone, source_msg_id, sender_phone,
                   reason, summary, priority, status, created_at,
                   channel, sender_email, sender_name, contact_id)
                   VALUES (?, ?, ?, NULL, ?, ?, ?, 'pending', ?, 'email', ?, ?, ?)""",
                (esc_id, account_id, first_email_id,
                 result.get('escalation_reason', 'unknown'),
                 result.get('summary', ''), result.get('escalation_priority', 'medium'), now,
                 batch['sender'], batch.get('sender_name', ''), contact_id)
            )
        except Exception as e:
            print(f"[email-triage] Error inserting escalation: {e}")

    db.commit()
    db.close()

    print(f"[email-triage] Result: class={result.get('classification')}, escalate={result.get('escalate')}, "
          f"tasks={len(result.get('tasks', []))}, notes={len(result.get('notes', []))}")


def process_email_batch_file(batch_path):
    """Process a single email batch file."""
    with open(batch_path) as f:
        batch = json.load(f)

    print(f"[email-triage] Processing: {batch['sender']} ({batch['email_count']} emails, account={batch['account_id']})")

    # Process contacts first
    try:
        from contact_manager import process_new_handle, get_db as cm_get_db
        cm_db = cm_get_db()
        for email_msg in batch.get('emails', []):
            from_addr = email_msg.get('from_addr', '')
            from_name = email_msg.get('from_name', '')
            body_text = email_msg.get('body_text', '')
            if from_addr:
                process_new_handle(cm_db, 'email', from_addr, from_name, 'email', body_text)
        cm_db.close()
    except Exception as e:
        print(f"[email-triage] Contact processing error: {e}")

    # Check if this is likely spam/newsletter (skip LLM for obvious cases)
    sender = batch['sender'].lower()
    skip_indicators = ['noreply@', 'no-reply@', 'newsletter@', 'marketing@',
                       'notifications@', 'notification@', 'mailer-daemon@',
                       'postmaster@']
    is_auto = any(sender.startswith(s) for s in skip_indicators)

    # Check email content for unsubscribe links
    has_unsubscribe = False
    for em in batch.get('emails', []):
        raw = em.get('body_text', '') or ''
        if 'unsubscribe' in raw.lower():
            has_unsubscribe = True
            break

    if is_auto and has_unsubscribe:
        # Auto-classify as newsletter, no escalation
        result = {
            'classification': 'newsletter',
            'summary': f"Auto-generated/newsletter email from {batch['sender']}",
            'escalate': False,
            'escalation_reason': None,
            'escalation_priority': 'low',
            'tasks': [],
            'notes': [],
        }
        print(f"[email-triage] Auto-classified as newsletter (skipped LLM)")
    else:
        # Call LLM for classification
        result = triage_email_batch(batch)

    if result:
        process_email_triage_result(batch, result)
        processed_path = os.path.join(EMAIL_PROCESSED_DIR, os.path.basename(batch_path))
        os.rename(batch_path, processed_path)
    else:
        print(f"[email-triage] Failed to triage batch, will retry later")


def main():
    print(f"[email-triage] Starting email triage agent (model: {DEEPSEEK_MODEL})")
    print(f"[email-triage] Watching: {EMAIL_BATCH_DIR}")
    print(f"[email-triage] Email skills dir: {EMAIL_SKILLS_DIR}")

    while True:
        batch_files = glob.glob(os.path.join(EMAIL_BATCH_DIR, '*.json'))

        for bf in sorted(batch_files):
            if '/processed/' in bf:
                continue
            try:
                process_email_batch_file(bf)
            except Exception as e:
                print(f"[email-triage] Error processing {bf}: {e}")
                import traceback
                traceback.print_exc()
            time.sleep(1)

        time.sleep(5)


if __name__ == '__main__':
    main()
