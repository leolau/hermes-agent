#!/usr/bin/env python3
"""
Email Batcher for Hermes Agent

Groups incoming emails by sender+account with a 30-second debounce window.
Thread-aware: emails in the same thread within the window are batched together.
Outputs batch JSON files to /opt/data/email-messages/batches/
"""

import json
import sqlite3
import os
import time
import uuid
from datetime import datetime, timezone
from collections import defaultdict

DB_PATH = '/opt/data/whatsapp-messages/whatsapp_data.db'
CONFIG_PATH = '/opt/data/email-messages/config.json'
BATCH_DIR = '/opt/data/email-messages/batches'
PROCESSED_DIR = '/opt/data/email-messages/batches/processed'

def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn

def load_config():
    with open(CONFIG_PATH) as f:
        return json.load(f)

def get_unbatched_emails(db):
    """Get emails that haven't been assigned to a batch yet."""
    rows = db.execute(
        "SELECT * FROM email_messages WHERE batch_id IS NULL ORDER BY received_at ASC"
    ).fetchall()
    return [dict(r) for r in rows]

def group_emails(emails):
    """Group emails by sender+account, with thread awareness."""
    groups = defaultdict(list)
    
    for email_row in emails:
        # Primary grouping key: sender + account
        key = f"{email_row['from_addr']}|{email_row['account_id']}"
        
        # Check if this email belongs to an existing thread in another group
        thread_id = email_row.get('thread_id')
        if thread_id:
            # Check if any existing group has emails from this thread
            found_thread_group = None
            for gkey, gemails in groups.items():
                for ge in gemails:
                    if ge.get('thread_id') == thread_id:
                        found_thread_group = gkey
                        break
                if found_thread_group:
                    break
            
            if found_thread_group and found_thread_group != key:
                # Add to existing thread group instead
                key = found_thread_group
        
        groups[key].append(email_row)
    
    return groups

def write_batch(group_key, emails, batch_id):
    """Write a batch JSON file."""
    sender_addr = emails[0]['from_addr']
    sender_name = emails[0].get('from_name', '')
    account_id = emails[0]['account_id']
    
    batch = {
        'batch_id': batch_id,
        'channel': 'email',
        'account_id': account_id,
        'sender': sender_addr,
        'sender_name': sender_name,
        'email_count': len(emails),
        'created_at': datetime.now(timezone.utc).isoformat(),
        'emails': []
    }
    
    for em in emails:
        batch['emails'].append({
            'id': em['id'],
            'from_addr': em['from_addr'],
            'from_name': em.get('from_name', ''),
            'to_addrs': em.get('to_addrs', '[]'),
            'cc_addrs': em.get('cc_addrs', '[]'),
            'subject': em.get('subject', ''),
            'body_text': em.get('body_text', ''),
            'has_attachments': em.get('has_attachments', 0),
            'attachment_info': em.get('attachment_info'),
            'message_id': em.get('message_id', ''),
            'in_reply_to': em.get('in_reply_to'),
            'thread_id': em.get('thread_id'),
            'folder': em.get('folder', 'INBOX'),
            'received_at': em.get('received_at', ''),
        })
    
    batch_file = os.path.join(BATCH_DIR, f"batch_{batch_id}.json")
    with open(batch_file, 'w') as f:
        json.dump(batch, f, indent=2)
    
    return batch_file

def main():
    print("[email_batcher] Starting email batcher...")
    print(f"[email_batcher] DB: {DB_PATH}")
    print(f"[email_batcher] Batch dir: {BATCH_DIR}")
    
    config = load_config()
    window_seconds = config.get('batching', {}).get('window_seconds', 30)
    print(f"[email_batcher] Batching window: {window_seconds}s")
    
    os.makedirs(BATCH_DIR, exist_ok=True)
    os.makedirs(PROCESSED_DIR, exist_ok=True)
    
    # Track pending groups with their first-seen time
    pending_groups = {}  # key -> {'first_seen': timestamp, 'email_ids': set()}
    
    while True:
        try:
            db = get_db()
            unbatched = get_unbatched_emails(db)
            
            if unbatched:
                groups = group_emails(unbatched)
                now = time.time()
                
                for key, emails in groups.items():
                    email_ids = {em['id'] for em in emails}
                    
                    if key not in pending_groups:
                        pending_groups[key] = {
                            'first_seen': now,
                            'email_ids': email_ids,
                            'emails': emails
                        }
                    else:
                        # Add new emails to existing pending group
                        pending_groups[key]['email_ids'].update(email_ids)
                        # Update emails list (avoid duplicates)
                        existing_ids = {em['id'] for em in pending_groups[key]['emails']}
                        for em in emails:
                            if em['id'] not in existing_ids:
                                pending_groups[key]['emails'].append(em)
                
                # Check which pending groups have exceeded the window
                keys_to_flush = []
                for key, group in pending_groups.items():
                    elapsed = now - group['first_seen']
                    if elapsed >= window_seconds:
                        keys_to_flush.append(key)
                
                # Flush expired groups
                for key in keys_to_flush:
                    group = pending_groups.pop(key)
                    batch_id = str(uuid.uuid4())[:8]
                    
                    batch_file = write_batch(key, group['emails'], batch_id)
                    
                    # Mark emails as batched in DB
                    for email_id in group['email_ids']:
                        db.execute(
                            "UPDATE email_messages SET batch_id = ? WHERE id = ?",
                            (batch_id, email_id)
                        )
                    
                    db.commit()
                    sender = group['emails'][0]['from_addr']
                    print(f"[email_batcher] Flushed batch {batch_id}: {len(group['emails'])} emails from {sender}")
            
            db.close()
        except Exception as e:
            print(f"[email_batcher] Error: {e}")
            import traceback
            traceback.print_exc()
        
        # Check every 5 seconds
        time.sleep(5)


if __name__ == '__main__':
    main()
