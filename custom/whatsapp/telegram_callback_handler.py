#!/usr/bin/env python3
"""
Telegram Callback Handler for Hermes Agent

HTTP server that processes merge/reject actions triggered by
Telegram inline keyboard URL buttons. Runs on port 7902.

When a user taps a button in Telegram, their browser opens a URL
on this server. The server processes the action, updates the
Telegram message, and returns a confirmation page.
"""

import json
import os
import sqlite3
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.request import Request, urlopen

DB_PATH = '/opt/data/whatsapp-messages/whatsapp_data.db'
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', '')
TELEGRAM_USER_ID = os.environ.get('TELEGRAM_USER_ID', '')
PORT = int(os.environ.get('CALLBACK_PORT', '7902'))


def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def telegram_api(method, payload):
    """Call Telegram Bot API."""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/{method}"
    data = json.dumps(payload).encode()
    req = Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        resp = urlopen(req, timeout=10)
        return json.loads(resp.read().decode())
    except Exception as e:
        print(f"[callback] Telegram API error ({method}): {e}")
        return None


def send_telegram(text):
    """Send a notification message to Telegram."""
    telegram_api("sendMessage", {
        "chat_id": TELEGRAM_USER_ID,
        "text": text,
        "parse_mode": "HTML",
    })


def handle_merge(suggestion_id):
    """Execute a merge action. Returns (success, message)."""
    now = datetime.now(timezone.utc).isoformat()
    db = get_db()
    try:
        suggestion = db.execute(
            "SELECT * FROM contact_merge_suggestions WHERE id = ?",
            (suggestion_id,)
        ).fetchone()

        if not suggestion:
            return False, "Suggestion not found"

        if suggestion['status'] != 'pending':
            return False, f"Already {suggestion['status']}"

        source_contact_id = suggestion['new_contact_id']
        target_contact_id = suggestion['candidate_contact_id']

        target = db.execute(
            "SELECT * FROM unified_contacts WHERE id = ?",
            (target_contact_id,)
        ).fetchone()

        if not target:
            db.execute(
                "UPDATE contact_merge_suggestions SET status = 'expired', resolved_at = ? WHERE id = ?",
                (now, suggestion_id)
            )
            db.commit()
            return False, "Target contact no longer exists"

        source = db.execute(
            "SELECT * FROM unified_contacts WHERE id = ?",
            (source_contact_id,)
        ).fetchone()

        if not source:
            db.execute(
                "UPDATE contact_merge_suggestions SET status = 'approved', resolved_at = ? WHERE id = ?",
                (now, suggestion_id)
            )
            db.commit()
            return True, f"Already merged into {target['display_name']}"

        # Execute merge
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
        db.execute(
            "UPDATE contact_merge_suggestions SET status = 'approved', resolved_at = ? WHERE id = ?",
            (now, suggestion_id)
        )
        db.commit()

        handles = db.execute(
            "SELECT handle_type, handle_value FROM contact_handles WHERE contact_id = ?",
            (target_contact_id,)
        ).fetchall()
        handles_text = ", ".join(h['handle_value'] for h in handles)

        send_telegram(
            f"\u2705 <b>Merged</b>\n\n"
            f"\U0001f464 <b>{target['display_name']}</b>\n"
            f"Handles: {handles_text}"
        )

        print(f"[callback] Merged {source_contact_id} into {target_contact_id} "
              f"({suggestion['new_handle_value']} -> {target['display_name']})")
        return True, f"Merged into {target['display_name']} ({handles_text})"
    finally:
        db.close()


def handle_reject(suggestion_id):
    """Reject a merge suggestion. Returns (success, message)."""
    now = datetime.now(timezone.utc).isoformat()
    db = get_db()
    try:
        suggestion = db.execute(
            "SELECT * FROM contact_merge_suggestions WHERE id = ?",
            (suggestion_id,)
        ).fetchone()

        if not suggestion:
            return False, "Suggestion not found"

        if suggestion['status'] != 'pending':
            return False, f"Already {suggestion['status']}"

        db.execute(
            "UPDATE contact_merge_suggestions SET status = 'rejected', resolved_at = ? WHERE id = ?",
            (now, suggestion_id)
        )
        db.commit()

        new_contact = db.execute(
            "SELECT display_name FROM unified_contacts WHERE id = ?",
            (suggestion['new_contact_id'],)
        ).fetchone()
        candidate = db.execute(
            "SELECT display_name FROM unified_contacts WHERE id = ?",
            (suggestion['candidate_contact_id'],)
        ).fetchone()

        new_name = new_contact['display_name'] if new_contact else suggestion['new_display_name']
        candidate_name = candidate['display_name'] if candidate else 'Unknown'

        send_telegram(
            f"\u274c <b>Kept Separate</b>\n\n"
            f"\"{new_name}\" and \"{candidate_name}\" will remain as separate contacts."
        )

        print(f"[callback] Rejected merge {suggestion_id} "
              f"({suggestion['new_handle_value']} != {candidate_name})")
        return True, f"Kept \"{new_name}\" and \"{candidate_name}\" as separate contacts"
    finally:
        db.close()


def html_response(title, message, success=True):
    """Generate a simple HTML confirmation page."""
    color = "#4CAF50" if success else "#f44336"
    icon = "\u2705" if success else "\u274c"
    return f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>{title}</title>
    <style>
        body {{ font-family: -apple-system, sans-serif; display: flex;
               justify-content: center; align-items: center; min-height: 100vh;
               margin: 0; background: #f5f5f5; }}
        .card {{ background: white; border-radius: 12px; padding: 40px;
                 text-align: center; box-shadow: 0 2px 8px rgba(0,0,0,0.1);
                 max-width: 400px; }}
        .icon {{ font-size: 48px; margin-bottom: 16px; }}
        h1 {{ color: {color}; margin: 0 0 12px; font-size: 24px; }}
        p {{ color: #666; margin: 0; font-size: 16px; line-height: 1.5; }}
    </style>
</head>
<body>
    <div class="card">
        <div class="icon">{icon}</div>
        <h1>{title}</h1>
        <p>{message}</p>
        <p style="margin-top: 16px; color: #999; font-size: 13px;">
            You can close this tab and return to Telegram.
        </p>
    </div>
</body>
</html>"""


class CallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        parts = self.path.strip('/').split('/')

        if len(parts) == 3 and parts[0] == 'action':
            action = parts[1]
            suggestion_id = parts[2]

            if action == 'merge':
                success, message = handle_merge(suggestion_id)
                title = "Contacts Merged" if success else "Merge Failed"
            elif action == 'reject':
                success, message = handle_reject(suggestion_id)
                title = "Kept Separate" if success else "Reject Failed"
            else:
                success, message = False, f"Unknown action: {action}"
                title = "Error"

            html = html_response(title, message, success)
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.end_headers()
            self.wfile.write(html.encode())

        elif self.path == '/health':
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({"status": "ok"}).encode())

        else:
            self.send_response(404)
            self.send_header('Content-Type', 'text/plain')
            self.end_headers()
            self.wfile.write(b"Not found")

    def log_message(self, format, *args):
        print(f"[callback] {args[0]}")


def main():
    if not TELEGRAM_BOT_TOKEN:
        print("[callback] ERROR: TELEGRAM_BOT_TOKEN not set")
        return
    if not TELEGRAM_USER_ID:
        print("[callback] ERROR: TELEGRAM_USER_ID not set")
        return

    server = HTTPServer(('0.0.0.0', PORT), CallbackHandler)
    print(f"[callback] Starting HTTP callback server on port {PORT}")
    print(f"[callback] Health check: http://localhost:{PORT}/health")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("[callback] Shutting down")
        server.shutdown()


if __name__ == '__main__':
    main()
