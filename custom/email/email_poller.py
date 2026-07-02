#!/usr/bin/env python3
"""
Email IMAP Poller for Hermes Agent

Polls multiple Gmail accounts via IMAP, stores new emails in SQLite.
Supports incremental fetch (only new emails since last poll).
"""

import imaplib
import email
import email.header
import email.utils
import json
import sqlite3
import os
import sys
import time
import uuid
import threading
import traceback
from datetime import datetime, timezone, timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler
from email.utils import parsedate_to_datetime

DB_PATH = '/opt/data/whatsapp-messages/whatsapp_data.db'
CONFIG_PATH = '/opt/data/email-messages/config.json'
HEALTH_PORT = 7901

def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn

def load_config():
    with open(CONFIG_PATH) as f:
        return json.load(f)

def decode_header_value(raw):
    """Decode an email header value (handles encoded words like =?UTF-8?B?...?=)."""
    if not raw:
        return ''
    parts = email.header.decode_header(raw)
    decoded = []
    for part, charset in parts:
        if isinstance(part, bytes):
            decoded.append(part.decode(charset or 'utf-8', errors='replace'))
        else:
            decoded.append(part)
    return ' '.join(decoded)

def extract_email_body(msg):
    """Extract plain text and HTML body from an email message."""
    text_body = ''
    html_body = ''

    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            content_disp = str(part.get('Content-Disposition', ''))
            if 'attachment' in content_disp:
                continue
            try:
                payload = part.get_payload(decode=True)
                if payload is None:
                    continue
                charset = part.get_content_charset() or 'utf-8'
                text = payload.decode(charset, errors='replace')
                if content_type == 'text/plain' and not text_body:
                    text_body = text
                elif content_type == 'text/html' and not html_body:
                    html_body = text
            except Exception:
                pass
    else:
        content_type = msg.get_content_type()
        try:
            payload = msg.get_payload(decode=True)
            if payload:
                charset = msg.get_content_charset() or 'utf-8'
                text = payload.decode(charset, errors='replace')
                if content_type == 'text/plain':
                    text_body = text
                elif content_type == 'text/html':
                    html_body = text
        except Exception:
            pass

    return text_body, html_body

def extract_attachments(msg):
    """Extract attachment metadata (name, mimetype, size) without downloading."""
    attachments = []
    if not msg.is_multipart():
        return attachments

    for part in msg.walk():
        content_disp = str(part.get('Content-Disposition', ''))
        if 'attachment' in content_disp or 'inline' in content_disp:
            filename = part.get_filename()
            if filename:
                filename = decode_header_value(filename)
                mimetype = part.get_content_type()
                payload = part.get_payload(decode=True)
                size = len(payload) if payload else 0
                attachments.append({
                    'name': filename,
                    'mimetype': mimetype,
                    'size': size
                })
    return attachments

def extract_thread_id(msg):
    """Extract thread ID from References or In-Reply-To headers."""
    references = msg.get('References', '')
    in_reply_to = msg.get('In-Reply-To', '')

    if references:
        # First message-id in References is typically the thread root
        refs = references.strip().split()
        if refs:
            return refs[0].strip('<>').strip()

    if in_reply_to:
        return in_reply_to.strip('<>').strip()

    # Use own Message-ID as thread root
    msg_id = msg.get('Message-ID', '')
    if msg_id:
        return msg_id.strip('<>').strip()

    return None

def parse_address_list(raw):
    """Parse a comma-separated address list into JSON array."""
    if not raw:
        return '[]'
    addrs = []
    decoded = decode_header_value(raw)
    for name, addr in email.utils.getaddresses([decoded]):
        if addr:
            addrs.append({'name': name, 'address': addr})
    return json.dumps(addrs)

def poll_account(account, db):
    """Poll a single IMAP account for new emails."""
    account_id = account['id']
    address = account['address']
    imap_host = account['imap']['host']
    imap_port = account['imap']['port']
    cred_env = account['credentials_env']
    folders = account.get('folders', ['INBOX'])

    password = os.environ.get(cred_env, '').replace('\xa0', ' ').strip()
    if not password:
        print(f"[poller] WARNING: No password for {address} (env: {cred_env})")
        return 0

    # Ensure account exists in DB
    existing = db.execute("SELECT last_uid FROM email_accounts WHERE id = ?", (account_id,)).fetchone()
    if not existing:
        db.execute(
            "INSERT INTO email_accounts (id, address, label, last_poll, last_uid, status) VALUES (?, ?, ?, NULL, 0, 'active')",
            (account_id, address, account.get('label', address))
        )
        db.commit()
        last_uid = 0
    else:
        last_uid = existing['last_uid'] or 0

    total_new = 0
    try:
        if account['imap'].get('tls', True):
            mail = imaplib.IMAP4_SSL(imap_host, imap_port)
        else:
            mail = imaplib.IMAP4(imap_host, imap_port)

        mail.login(address, password)

        for folder in folders:
            try:
                status, data = mail.select(folder, readonly=True)
                if status != 'OK':
                    print(f"[poller] {address}: Cannot select {folder}")
                    continue

                # Fetch emails with UID > last_uid
                if last_uid > 0:
                    search_criteria = f'UID {last_uid + 1}:*'
                else:
                    # First run: get only recent emails (last 24h)
                    since_date = (datetime.now() - timedelta(days=1)).strftime('%d-%b-%Y')
                    search_criteria = f'SINCE {since_date}'

                status, msg_nums = mail.uid('SEARCH', None, search_criteria)
                if status != 'OK' or not msg_nums[0]:
                    continue

                uids = msg_nums[0].split()
                # Limit to 50 per poll to avoid overload
                uids = uids[:50]

                for uid_bytes in uids:
                    uid = int(uid_bytes)
                    if uid <= last_uid:
                        continue

                    try:
                        status, msg_data = mail.uid('FETCH', uid_bytes, '(RFC822)')
                        if status != 'OK' or not msg_data[0]:
                            continue

                        raw_email = msg_data[0][1]
                        msg = email.message_from_bytes(raw_email)

                        # Extract fields
                        message_id = msg.get('Message-ID', '').strip('<>').strip()
                        if not message_id:
                            message_id = str(uuid.uuid4())

                        from_header = decode_header_value(msg.get('From', ''))
                        from_name, from_addr = email.utils.parseaddr(from_header)
                        if not from_addr:
                            from_addr = from_header

                        subject = decode_header_value(msg.get('Subject', ''))
                        to_addrs = parse_address_list(msg.get('To', ''))
                        cc_addrs = parse_address_list(msg.get('Cc', ''))
                        in_reply_to = msg.get('In-Reply-To', '').strip('<>').strip() or None
                        thread_id = extract_thread_id(msg)

                        text_body, html_body = extract_email_body(msg)
                        attachments = extract_attachments(msg)
                        has_attachments = 1 if attachments else 0
                        attachment_info = json.dumps(attachments) if attachments else None

                        # Parse received date
                        date_header = msg.get('Date', '')
                        try:
                            received_dt = parsedate_to_datetime(date_header)
                            received_at = received_dt.isoformat()
                        except Exception:
                            received_at = datetime.now(timezone.utc).isoformat()

                        # Extract key headers for debugging
                        raw_headers = ''
                        for key in ['From', 'To', 'Cc', 'Subject', 'Date', 'Message-ID',
                                    'In-Reply-To', 'References', 'List-Unsubscribe', 'X-Mailer']:
                            val = msg.get(key, '')
                            if val:
                                raw_headers += f'{key}: {val}\n'

                        # Insert into DB (dedup by message_id)
                        try:
                            db.execute(
                                """INSERT OR IGNORE INTO email_messages
                                   (id, account_id, from_addr, from_name, to_addrs, cc_addrs,
                                    subject, body_text, body_html, has_attachments, attachment_info,
                                    message_id, in_reply_to, thread_id, folder, received_at, batch_id, raw_headers)
                                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?)""",
                                (str(uuid.uuid4()), account_id, from_addr, from_name, to_addrs, cc_addrs,
                                 subject, text_body, html_body, has_attachments, attachment_info,
                                 message_id, in_reply_to, thread_id, folder, received_at, raw_headers)
                            )
                            total_new += 1
                        except sqlite3.IntegrityError:
                            pass  # Duplicate message_id — already stored

                        if uid > last_uid:
                            last_uid = uid

                    except Exception as e:
                        print(f"[poller] {address}: Error fetching UID {uid}: {e}")
                        continue

            except Exception as e:
                print(f"[poller] {address}: Error with folder {folder}: {e}")
                continue

        mail.logout()

    except Exception as e:
        print(f"[poller] {address}: Connection error: {e}")
        traceback.print_exc()
        db.execute("UPDATE email_accounts SET status = 'error' WHERE id = ?", (account_id,))
        db.commit()
        return 0

    # Update last_poll and last_uid
    now = datetime.now(timezone.utc).isoformat()
    db.execute(
        "UPDATE email_accounts SET last_poll = ?, last_uid = ?, status = 'active' WHERE id = ?",
        (now, last_uid, account_id)
    )
    db.commit()

    if total_new > 0:
        print(f"[poller] {address}: {total_new} new emails (last_uid={last_uid})")

    return total_new


# Health check server
poll_status = {'last_poll': None, 'accounts': {}, 'running': True}

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps(poll_status, default=str).encode())

    def log_message(self, format, *args):
        pass

def start_health_server():
    server = HTTPServer(('0.0.0.0', HEALTH_PORT), HealthHandler)
    server.serve_forever()


def main():
    print(f"[poller] Email IMAP Poller starting...")
    print(f"[poller] DB: {DB_PATH}")
    print(f"[poller] Config: {CONFIG_PATH}")

    # Start health server
    health_thread = threading.Thread(target=start_health_server, daemon=True)
    health_thread.start()
    print(f"[poller] Health server on port {HEALTH_PORT}")

    config = load_config()
    accounts = [a for a in config['accounts'] if a.get('enabled', True)]
    print(f"[poller] Active accounts: {len(accounts)}")
    for a in accounts:
        print(f"[poller]   {a['id']}: {a['address']} (poll every {a.get('poll_interval_seconds', 60)}s)")

    if not accounts:
        print("[poller] No enabled accounts. Waiting for config update...")
        while True:
            time.sleep(60)
            config = load_config()
            accounts = [a for a in config['accounts'] if a.get('enabled', True)]
            if accounts:
                break

    poll_status['accounts'] = {a['id']: {'address': a['address'], 'last_poll': None, 'new_count': 0} for a in accounts}

    while True:
        config = load_config()
        accounts = [a for a in config['accounts'] if a.get('enabled', True)]

        db = get_db()
        for account in accounts:
            try:
                new_count = poll_account(account, db)
                now = datetime.now(timezone.utc).isoformat()
                poll_status['accounts'][account['id']] = {
                    'address': account['address'],
                    'last_poll': now,
                    'new_count': new_count
                }
                poll_status['last_poll'] = now
            except Exception as e:
                print(f"[poller] Error polling {account['address']}: {e}")
                traceback.print_exc()
        db.close()

        # Sleep for the shortest poll interval
        min_interval = min(a.get('poll_interval_seconds', 60) for a in accounts)
        time.sleep(min_interval)


if __name__ == '__main__':
    main()
