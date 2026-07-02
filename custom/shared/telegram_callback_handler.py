#!/usr/bin/env python3
"""
Telegram Callback Handler for Hermes Agent

Long-polling daemon that listens for inline keyboard button clicks
(callback_query events) from Telegram. Handles merge/reject actions
for contact merge suggestions.
"""

import json
import os
import sqlite3
import time
from datetime import datetime, timezone
from urllib.request import Request, urlopen
from urllib.error import URLError

DB_PATH = '/opt/data/whatsapp-messages/whatsapp_data.db'
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', '')
TELEGRAM_USER_ID = os.environ.get('TELEGRAM_USER_ID', '')
POLL_TIMEOUT = 30
POLL_INTERVAL = 1


def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def telegram_api(method, payload=None):
    """Call Telegram Bot API."""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/{method}"
    if payload:
        data = json.dumps(payload).encode()
        req = Request(url, data=data, headers={"Content-Type": "application/json"})
    else:
        req = Request(url)
    try:
        resp = urlopen(req, timeout=POLL_TIMEOUT + 10)
        return json.loads(resp.read().decode())
    except URLError as e:
        if 'timed out' not in str(e):
            print(f"[callback] Telegram API error ({method}): {e}")
        return None
    except Exception as e:
        print(f"[callback] Telegram API error ({method}): {e}")
        return None


def answer_callback_query(callback_query_id, text):
    """Acknowledge a button click with a toast notification."""
    telegram_api("answerCallbackQuery", {
        "callback_query_id": callback_query_id,
        "text": text,
        "show_alert": False,
    })


def edit_message_text(chat_id, message_id, new_text):
    """Edit the original merge suggestion message to show the result."""
    telegram_api("editMessageText", {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": new_text,
        "parse_mode": "HTML",
    })


def handle_merge(db, suggestion_id, chat_id, message_id, callback_query_id):
    """Execute a merge action."""
    now = datetime.now(timezone.utc).isoformat()

    suggestion = db.execute(
        "SELECT * FROM contact_merge_suggestions WHERE id = ?",
        (suggestion_id,)
    ).fetchone()

    if not suggestion:
        answer_callback_query(callback_query_id, "Suggestion not found")
        return

    if suggestion['status'] != 'pending':
        answer_callback_query(callback_query_id, f"Already {suggestion['status']}")
        return

    source_contact_id = suggestion['new_contact_id']
    target_contact_id = suggestion['candidate_contact_id']

    # Verify both contacts still exist
    source = db.execute("SELECT * FROM unified_contacts WHERE id = ?", (source_contact_id,)).fetchone()
    target = db.execute("SELECT * FROM unified_contacts WHERE id = ?", (target_contact_id,)).fetchone()

    if not target:
        answer_callback_query(callback_query_id, "Target contact no longer exists")
        db.execute(
            "UPDATE contact_merge_suggestions SET status = 'expired', resolved_at = ? WHERE id = ?",
            (now, suggestion_id)
        )
        db.commit()
        return

    if not source:
        # Source may have been auto-merged already; just mark as resolved
        answer_callback_query(callback_query_id, "Already merged")
        db.execute(
            "UPDATE contact_merge_suggestions SET status = 'approved', resolved_at = ? WHERE id = ?",
            (now, suggestion_id)
        )
        db.commit()
        return

    # Execute merge: move handles from source to target
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

    # Update suggestion status
    db.execute(
        "UPDATE contact_merge_suggestions SET status = 'approved', resolved_at = ? WHERE id = ?",
        (now, suggestion_id)
    )
    db.commit()

    # Get updated handle list for confirmation message
    handles = db.execute(
        "SELECT handle_type, handle_value FROM contact_handles WHERE contact_id = ?",
        (target_contact_id,)
    ).fetchall()
    handles_text = ", ".join(f"{h['handle_value']}" for h in handles)

    answer_callback_query(callback_query_id, "Contacts merged!")

    edit_message_text(chat_id, message_id, (
        f"\u2705 <b>Merged</b>\n\n"
        f"\U0001f464 <b>{target['display_name']}</b>\n"
        f"Handles: {handles_text}"
    ))

    print(f"[callback] Merged contact {source_contact_id} into {target_contact_id} "
          f"({suggestion['new_handle_value']} -> {target['display_name']})")


def handle_reject(db, suggestion_id, chat_id, message_id, callback_query_id):
    """Reject a merge suggestion."""
    now = datetime.now(timezone.utc).isoformat()

    suggestion = db.execute(
        "SELECT * FROM contact_merge_suggestions WHERE id = ?",
        (suggestion_id,)
    ).fetchone()

    if not suggestion:
        answer_callback_query(callback_query_id, "Suggestion not found")
        return

    if suggestion['status'] != 'pending':
        answer_callback_query(callback_query_id, f"Already {suggestion['status']}")
        return

    db.execute(
        "UPDATE contact_merge_suggestions SET status = 'rejected', resolved_at = ? WHERE id = ?",
        (now, suggestion_id)
    )
    db.commit()

    answer_callback_query(callback_query_id, "Kept separate")

    # Get contact names for the edited message
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

    edit_message_text(chat_id, message_id, (
        f"\u274c <b>Kept Separate</b>\n\n"
        f"\"{new_name}\" and \"{candidate_name}\" will remain as separate contacts."
    ))

    print(f"[callback] Rejected merge suggestion {suggestion_id} "
          f"({suggestion['new_handle_value']} != {candidate_name})")


def process_callback_query(update):
    """Process a single callback_query from Telegram."""
    callback = update.get('callback_query')
    if not callback:
        return

    callback_query_id = callback['id']
    callback_data = callback.get('data', '')
    chat_id = callback['message']['chat']['id']
    message_id = callback['message']['message_id']
    user_id = str(callback['from']['id'])

    # Security: only accept callbacks from the configured user
    if user_id != TELEGRAM_USER_ID:
        answer_callback_query(callback_query_id, "Unauthorized")
        return

    if ':' not in callback_data:
        answer_callback_query(callback_query_id, "Invalid action")
        return

    action, suggestion_id = callback_data.split(':', 1)

    db = get_db()
    try:
        if action == 'merge':
            handle_merge(db, suggestion_id, chat_id, message_id, callback_query_id)
        elif action == 'reject':
            handle_reject(db, suggestion_id, chat_id, message_id, callback_query_id)
        else:
            answer_callback_query(callback_query_id, f"Unknown action: {action}")
    except Exception as e:
        print(f"[callback] Error processing callback: {e}")
        answer_callback_query(callback_query_id, "Error processing request")
    finally:
        db.close()


def main():
    if not TELEGRAM_BOT_TOKEN:
        print("[callback] ERROR: TELEGRAM_BOT_TOKEN not set")
        return
    if not TELEGRAM_USER_ID:
        print("[callback] ERROR: TELEGRAM_USER_ID not set")
        return

    print(f"[callback] Starting Telegram callback handler (long-polling)")
    print(f"[callback] Authorized user: {TELEGRAM_USER_ID}")
    print(f"[callback] Poll timeout: {POLL_TIMEOUT}s")

    offset = None

    while True:
        try:
            params = {"timeout": POLL_TIMEOUT, "allowed_updates": ["callback_query"]}
            if offset is not None:
                params["offset"] = offset

            result = telegram_api("getUpdates", params)

            if result and result.get('ok') and result.get('result'):
                for update in result['result']:
                    offset = update['update_id'] + 1
                    process_callback_query(update)

        except Exception as e:
            print(f"[callback] Polling error: {e}")
            time.sleep(POLL_INTERVAL)


if __name__ == '__main__':
    main()
