#!/usr/bin/env python3
"""
WhatsApp Triage Subagent

Watches for new batch files, classifies messages using DeepSeek,
extracts tasks/notes, and creates escalations for urgent items.
Loads dynamic skills from /opt/data/skills/whatsapp-triage/.
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
from urllib.error import URLError

# Credit tracking
sys.path.insert(0, '/opt/data')
from track_credit_helper import track_inference

# Paths
CONFIG_PATH = '/opt/data/whatsapp-messages/config.json'
DB_PATH = '/opt/data/whatsapp-messages/whatsapp_data.db'
BATCH_DIR = '/opt/data/whatsapp-messages/batches'
SKILLS_DIR = '/opt/data/skills/whatsapp-triage'
PROCESSED_DIR = '/opt/data/whatsapp-messages/batches/processed'

# Ensure dirs
os.makedirs(PROCESSED_DIR, exist_ok=True)

# Load config
def load_config():
    with open(CONFIG_PATH) as f:
        return json.load(f)

config = load_config()
DEEPSEEK_API_KEY = os.environ.get('DEEPSEEK_API_KEY', '')
DEEPSEEK_MODEL = config.get('triage', {}).get('model', 'deepseek-chat')
DEEPSEEK_BASE_URL = 'https://api.deepseek.com/v1/chat/completions'

# Family phones for quick matching
FAMILY_PHONES = {
    c['phone']: c for c in config.get('escalation', {}).get('criteria', {}).get('family_contacts', [])
}


def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def load_skills():
    """Load all skill .md files from the skills directory."""
    skills_content = []
    skill_dirs = [SKILLS_DIR, os.path.join(SKILLS_DIR, 'custom')]
    
    for sdir in skill_dirs:
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
        def _do_api_call():
            resp = urlopen(req, timeout=30)
            data = json.loads(resp.read().decode())
            return data['choices'][0]['message']['content']
        return track_inference("WhatsApp processing", _do_api_call)
    except Exception as e:
        print(f"[triage] DeepSeek API error: {e}")
        return None


def triage_batch(batch):
    """Run triage on a message batch."""
    skills_text = load_skills()
    sender_phone = batch['sender_phone']
    source_phone = batch['source_phone']
    is_family = batch.get('is_family', sender_phone in FAMILY_PHONES)
    
    # Build message content for the LLM
    messages_text = ""
    for m in batch['messages']:
        text = m.get('text', '') or '[media]'
        messages_text += f"- [{m.get('timestamp', '')}] {text}\n"
    
    family_info = ""
    if is_family and sender_phone in FAMILY_PHONES:
        fc = FAMILY_PHONES[sender_phone]
        family_info = f"\nSender is a FAMILY MEMBER: {fc['name']} ({fc.get('relation', '')}). ALL messages from family must be escalated.\n"
    
    system_prompt = f"""You are a WhatsApp message triage agent. Your job is to analyze incoming messages and:
1. Classify the overall batch
2. Extract any tasks or action items
3. Extract any notes worth remembering
4. Determine if this batch should be escalated (pushed immediately to the user)

{skills_text}

ESCALATION RULES:
- ANY message from a family contact -> ESCALATE (reason: "family")
- Business requests needing immediate attention -> ESCALATE (reason: "urgent_business")
- Sales opportunities needing immediate attention -> ESCALATE (reason: "sales_opportunity")
- Everything else -> DO NOT escalate (will surface in hourly digest)
{family_info}

Respond in JSON format:
{{
  "classification": "task|reminder|note|urgent_business|sales_opportunity|informational|ignorable",
  "summary": "brief 1-2 sentence summary of the batch",
  "escalate": true/false,
  "escalation_reason": "family|urgent_business|sales_opportunity|null",
  "escalation_priority": "high|medium|low",
  "tasks": [
    {{"description": "...", "due_date": "YYYY-MM-DD or null", "priority": "high|medium|low"}}
  ],
  "notes": [
    {{"content": "..."}}
  ]
}}
"""
    
    user_prompt = f"""Triage this message batch:

Sender: {sender_phone} (Name: {batch['messages'][0].get('sender_name', 'Unknown')})
Source Phone: {source_phone}
Message Count: {batch['message_count']}
Is Family: {is_family}

Messages:
{messages_text}
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
        print(f"[triage] Failed to parse response: {response[:200]}")
        return None


def process_triage_result(batch, result):
    """Write triage results to SQLite."""
    db = get_db()
    now = datetime.now(timezone.utc).isoformat()
    
    # Write tasks
    for task in result.get('tasks', []):
        task_id = str(uuid.uuid4())
        try:
            db.execute(
                """INSERT INTO wa_tasks (id, source_phone, source_msg_id, description, due_date, status, priority, created_at)
                   VALUES (?, ?, ?, ?, ?, 'pending', ?, ?)""",
                (task_id, batch['source_phone'], batch['messages'][0]['msg_id'],
                 task['description'], task.get('due_date'), task.get('priority', 'medium'), now)
            )
        except Exception as e:
            print(f"[triage] Error inserting task: {e}")
    
    # Write notes
    for note in result.get('notes', []):
        note_id = str(uuid.uuid4())
        try:
            db.execute(
                """INSERT INTO wa_notes (id, source_phone, source_msg_id, content, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (note_id, batch['source_phone'], batch['messages'][0]['msg_id'],
                 note['content'], now)
            )
        except Exception as e:
            print(f"[triage] Error inserting note: {e}")
    
    # Write escalation if needed
    if result.get('escalate'):
        esc_id = str(uuid.uuid4())
        try:
            db.execute(
                """INSERT INTO escalations (id, source_phone, source_msg_id, sender_phone, reason, summary, priority, status, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?)""",
                (esc_id, batch['source_phone'], batch['messages'][0]['msg_id'],
                 batch['sender_phone'], result.get('escalation_reason', 'unknown'),
                 result.get('summary', ''), result.get('escalation_priority', 'medium'), now)
            )
        except Exception as e:
            print(f"[triage] Error inserting escalation: {e}")
    
    db.commit()
    db.close()
    
    print(f"[triage] Result: class={result.get('classification')}, escalate={result.get('escalate')}, "
          f"tasks={len(result.get('tasks', []))}, notes={len(result.get('notes', []))}")


def process_batch_file(batch_path):
    """Process a single batch file."""
    with open(batch_path) as f:
        batch = json.load(f)
    
    print(f"[triage] Processing batch: {batch['sender_phone']} on {batch['source_phone']} ({batch['message_count']} msgs)")
    
    # Check if sender is family (immediate escalation without LLM for pure routing)
    sender_phone = batch['sender_phone']
    is_family = sender_phone in FAMILY_PHONES
    
    # Skip LLM for empty/media-only messages from non-family
    has_text = any(m.get('text') for m in batch['messages'])
    
    if is_family and not has_text:
        # Family media-only: escalate directly without LLM
        result = {
            'classification': 'informational',
            'summary': f"Media message from {FAMILY_PHONES[sender_phone]['name']}",
            'escalate': True,
            'escalation_reason': 'family',
            'escalation_priority': 'medium',
            'tasks': [],
            'notes': [],
        }
    elif not has_text and not is_family:
        # Non-family media with no text: skip triage
        result = {
            'classification': 'ignorable',
            'summary': 'Media message without text',
            'escalate': False,
            'escalation_reason': None,
            'escalation_priority': 'low',
            'tasks': [],
            'notes': [],
        }
    else:
        # Call LLM for classification
        result = triage_batch(batch)
    
    if result:
        process_triage_result(batch, result)
        # Move batch to processed
        processed_path = os.path.join(PROCESSED_DIR, os.path.basename(batch_path))
        os.rename(batch_path, processed_path)
    else:
        print(f"[triage] Failed to triage batch, will retry later")


def main():
    print(f"[triage] Starting triage agent (model: {DEEPSEEK_MODEL})")
    print(f"[triage] Watching: {BATCH_DIR}")
    print(f"[triage] Skills dir: {SKILLS_DIR}")
    print(f"[triage] Family contacts: {len(FAMILY_PHONES)}")
    
    while True:
        # Find unprocessed batch files
        batch_files = glob.glob(os.path.join(BATCH_DIR, '*.json'))
        
        for bf in sorted(batch_files):
            if '/processed/' in bf:
                continue
            try:
                process_batch_file(bf)
            except Exception as e:
                print(f"[triage] Error processing {bf}: {e}")
            time.sleep(1)  # Rate limit between batches
        
        # Sleep before next scan
        time.sleep(3)


if __name__ == '__main__':
    main()
