#!/usr/bin/env python3
"""
Tests for Telegram inline keyboard merge/reject callbacks.

Tests the HTTP-based callback handler logic (merge/reject) and verifies
that send_merge_confirmation produces URL-based inline keyboard markup.
"""

import json
import os
import sqlite3
import sys
import unittest
import uuid
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock

# Add custom/shared to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'shared'))

DB_PATH = ':memory:'


class NonClosingConnection:
    """Wraps a sqlite3 connection but makes close() a no-op for testing."""
    def __init__(self, conn):
        self._conn = conn
    def __getattr__(self, name):
        return getattr(self._conn, name)
    def close(self):
        pass  # no-op so test assertions can still use the connection
    def real_close(self):
        self._conn.close()


def create_test_db():
    """Create an in-memory test DB with the required schema."""
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")

    db.execute("""CREATE TABLE unified_contacts (
        id TEXT PRIMARY KEY,
        display_name TEXT NOT NULL,
        is_family INTEGER DEFAULT 0,
        is_vip INTEGER DEFAULT 0,
        relation TEXT,
        company TEXT,
        notes TEXT,
        auto_merged_count INTEGER DEFAULT 0,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""")

    db.execute("""CREATE TABLE contact_handles (
        id TEXT PRIMARY KEY,
        contact_id TEXT NOT NULL,
        handle_type TEXT NOT NULL,
        handle_value TEXT NOT NULL,
        display_name TEXT,
        source TEXT,
        first_seen TEXT,
        last_seen TEXT,
        message_count INTEGER DEFAULT 0,
        UNIQUE(handle_type, handle_value),
        FOREIGN KEY (contact_id) REFERENCES unified_contacts(id)
    )""")

    db.execute("""CREATE TABLE contact_merge_suggestions (
        id TEXT PRIMARY KEY,
        new_handle_type TEXT NOT NULL,
        new_handle_value TEXT NOT NULL,
        new_display_name TEXT,
        new_contact_id TEXT,
        candidate_contact_id TEXT NOT NULL,
        correlation_reason TEXT,
        confidence TEXT,
        status TEXT DEFAULT 'pending',
        created_at TEXT NOT NULL,
        resolved_at TEXT,
        FOREIGN KEY (new_contact_id) REFERENCES unified_contacts(id),
        FOREIGN KEY (candidate_contact_id) REFERENCES unified_contacts(id)
    )""")

    db.execute("""CREATE TABLE escalations (
        id TEXT PRIMARY KEY,
        source_phone TEXT,
        source_msg_id TEXT,
        sender_phone TEXT,
        reason TEXT,
        summary TEXT,
        priority TEXT,
        status TEXT,
        created_at TEXT,
        delivered_at TEXT,
        resolved_at TEXT,
        channel TEXT DEFAULT 'whatsapp',
        sender_email TEXT,
        sender_name TEXT,
        contact_id TEXT
    )""")

    db.commit()
    return NonClosingConnection(db)


def seed_merge_scenario(db):
    """Seed a merge scenario: two contacts that should be merged."""
    now = datetime.now(timezone.utc).isoformat()
    target_id = str(uuid.uuid4())
    source_id = str(uuid.uuid4())
    suggestion_id = str(uuid.uuid4())

    # Target contact (existing, with phone handle)
    db.execute(
        """INSERT INTO unified_contacts (id, display_name, is_family, auto_merged_count, created_at, updated_at)
           VALUES (?, 'Heidi Lui', 1, 0, ?, ?)""",
        (target_id, now, now)
    )
    db.execute(
        """INSERT INTO contact_handles (id, contact_id, handle_type, handle_value, display_name, source, first_seen, last_seen, message_count)
           VALUES (?, ?, 'phone', '+85294066060', 'Heidi Lui', 'whatsapp', ?, ?, 42)""",
        (str(uuid.uuid4()), target_id, now, now)
    )

    # Source contact (new, with email handle)
    db.execute(
        """INSERT INTO unified_contacts (id, display_name, is_family, auto_merged_count, created_at, updated_at)
           VALUES (?, 'Heidi Lui', 0, 0, ?, ?)""",
        (source_id, now, now)
    )
    db.execute(
        """INSERT INTO contact_handles (id, contact_id, handle_type, handle_value, display_name, source, first_seen, last_seen, message_count)
           VALUES (?, ?, 'email', 'heidi@gmail.com', 'Heidi Lui', 'email', ?, ?, 3)""",
        (str(uuid.uuid4()), source_id, now, now)
    )

    # Pending merge suggestion
    db.execute(
        """INSERT INTO contact_merge_suggestions
           (id, new_handle_type, new_handle_value, new_display_name, new_contact_id, candidate_contact_id,
            correlation_reason, confidence, status, created_at)
           VALUES (?, 'email', 'heidi@gmail.com', 'Heidi Lui', ?, ?, 'Exact name match', 'medium', 'pending', ?)""",
        (suggestion_id, source_id, target_id, now)
    )

    db.commit()
    return target_id, source_id, suggestion_id


class TestCallbackMerge(unittest.TestCase):
    def setUp(self):
        self.db = create_test_db()
        self.target_id, self.source_id, self.suggestion_id = seed_merge_scenario(self.db)

    def tearDown(self):
        self.db.real_close()

    @patch('telegram_callback_handler.get_db')
    @patch('telegram_callback_handler.send_telegram')
    def test_merge_moves_handles_to_target(self, mock_send, mock_get_db):
        """Merge should move all handles from source to target contact."""
        mock_get_db.return_value = self.db
        from telegram_callback_handler import handle_merge
        success, msg = handle_merge(self.suggestion_id)
        self.assertTrue(success)

        # Source contact should be deleted
        source = self.db.execute(
            "SELECT * FROM unified_contacts WHERE id = ?", (self.source_id,)
        ).fetchone()
        self.assertIsNone(source)

        # All handles should now belong to target
        handles = self.db.execute(
            "SELECT * FROM contact_handles WHERE contact_id = ?", (self.target_id,)
        ).fetchall()
        handle_values = {h['handle_value'] for h in handles}
        self.assertIn('+85294066060', handle_values)
        self.assertIn('heidi@gmail.com', handle_values)

    @patch('telegram_callback_handler.get_db')
    @patch('telegram_callback_handler.send_telegram')
    def test_merge_increments_auto_merged_count(self, mock_send, mock_get_db):
        """Target contact's auto_merged_count should increase by 1."""
        mock_get_db.return_value = self.db
        from telegram_callback_handler import handle_merge
        handle_merge(self.suggestion_id)

        target = self.db.execute(
            "SELECT * FROM unified_contacts WHERE id = ?", (self.target_id,)
        ).fetchone()
        self.assertEqual(target['auto_merged_count'], 1)

    @patch('telegram_callback_handler.get_db')
    @patch('telegram_callback_handler.send_telegram')
    def test_merge_updates_suggestion_status(self, mock_send, mock_get_db):
        """Suggestion status should be 'approved' after merge."""
        mock_get_db.return_value = self.db
        from telegram_callback_handler import handle_merge
        handle_merge(self.suggestion_id)

        suggestion = self.db.execute(
            "SELECT * FROM contact_merge_suggestions WHERE id = ?", (self.suggestion_id,)
        ).fetchone()
        self.assertEqual(suggestion['status'], 'approved')
        self.assertIsNotNone(suggestion['resolved_at'])

    @patch('telegram_callback_handler.get_db')
    @patch('telegram_callback_handler.send_telegram')
    def test_merge_reassigns_escalations(self, mock_send, mock_get_db):
        """Escalations linked to source contact should be reassigned to target."""
        now = datetime.now(timezone.utc).isoformat()
        esc_id = str(uuid.uuid4())
        self.db.execute(
            """INSERT INTO escalations (id, source_phone, reason, summary, priority, status, created_at, contact_id, channel)
               VALUES (?, 'phone1', 'family', 'test', 'high', 'pending', ?, ?, 'email')""",
            (esc_id, now, self.source_id)
        )
        self.db.commit()

        mock_get_db.return_value = self.db
        from telegram_callback_handler import handle_merge
        handle_merge(self.suggestion_id)

        esc = self.db.execute("SELECT * FROM escalations WHERE id = ?", (esc_id,)).fetchone()
        self.assertEqual(esc['contact_id'], self.target_id)

    @patch('telegram_callback_handler.get_db')
    @patch('telegram_callback_handler.send_telegram')
    def test_merge_idempotent_already_approved(self, mock_send, mock_get_db):
        """Merging an already-approved suggestion should return failure."""
        now = datetime.now(timezone.utc).isoformat()
        self.db.execute(
            "UPDATE contact_merge_suggestions SET status = 'approved', resolved_at = ? WHERE id = ?",
            (now, self.suggestion_id)
        )
        self.db.commit()

        mock_get_db.return_value = self.db
        from telegram_callback_handler import handle_merge
        success, msg = handle_merge(self.suggestion_id)
        self.assertFalse(success)
        self.assertIn('Already approved', msg)

    @patch('telegram_callback_handler.get_db')
    @patch('telegram_callback_handler.send_telegram')
    def test_merge_nonexistent_suggestion(self, mock_send, mock_get_db):
        """Merging a nonexistent suggestion should return failure."""
        mock_get_db.return_value = self.db
        from telegram_callback_handler import handle_merge
        success, msg = handle_merge('nonexistent-id')
        self.assertFalse(success)
        self.assertIn('not found', msg)


class TestCallbackReject(unittest.TestCase):
    def setUp(self):
        self.db = create_test_db()
        self.target_id, self.source_id, self.suggestion_id = seed_merge_scenario(self.db)

    def tearDown(self):
        self.db.real_close()

    @patch('telegram_callback_handler.get_db')
    @patch('telegram_callback_handler.send_telegram')
    def test_reject_keeps_both_contacts(self, mock_send, mock_get_db):
        """Reject should keep both contacts intact."""
        mock_get_db.return_value = self.db
        from telegram_callback_handler import handle_reject
        handle_reject(self.suggestion_id)

        source = self.db.execute(
            "SELECT * FROM unified_contacts WHERE id = ?", (self.source_id,)
        ).fetchone()
        target = self.db.execute(
            "SELECT * FROM unified_contacts WHERE id = ?", (self.target_id,)
        ).fetchone()
        self.assertIsNotNone(source)
        self.assertIsNotNone(target)

    @patch('telegram_callback_handler.get_db')
    @patch('telegram_callback_handler.send_telegram')
    def test_reject_updates_suggestion_status(self, mock_send, mock_get_db):
        """Suggestion status should be 'rejected' after reject."""
        mock_get_db.return_value = self.db
        from telegram_callback_handler import handle_reject
        handle_reject(self.suggestion_id)

        suggestion = self.db.execute(
            "SELECT * FROM contact_merge_suggestions WHERE id = ?", (self.suggestion_id,)
        ).fetchone()
        self.assertEqual(suggestion['status'], 'rejected')
        self.assertIsNotNone(suggestion['resolved_at'])

    @patch('telegram_callback_handler.get_db')
    @patch('telegram_callback_handler.send_telegram')
    def test_reject_idempotent_already_rejected(self, mock_send, mock_get_db):
        """Rejecting an already-rejected suggestion should return failure."""
        now = datetime.now(timezone.utc).isoformat()
        self.db.execute(
            "UPDATE contact_merge_suggestions SET status = 'rejected', resolved_at = ? WHERE id = ?",
            (now, self.suggestion_id)
        )
        self.db.commit()

        mock_get_db.return_value = self.db
        from telegram_callback_handler import handle_reject
        success, msg = handle_reject(self.suggestion_id)
        self.assertFalse(success)
        self.assertIn('Already rejected', msg)


class TestHTTPHandler(unittest.TestCase):
    """Test the HTTP endpoint routing."""

    def setUp(self):
        self.db = create_test_db()
        self.target_id, self.source_id, self.suggestion_id = seed_merge_scenario(self.db)

    def tearDown(self):
        self.db.real_close()

    @patch('telegram_callback_handler.get_db')
    @patch('telegram_callback_handler.send_telegram')
    def test_merge_via_url_path(self, mock_send, mock_get_db):
        """Simulates the URL path parsing for merge action."""
        mock_get_db.return_value = self.db
        from telegram_callback_handler import handle_merge
        path = f'/action/merge/{self.suggestion_id}'
        parts = path.strip('/').split('/')

        self.assertEqual(len(parts), 3)
        self.assertEqual(parts[0], 'action')
        self.assertEqual(parts[1], 'merge')
        self.assertEqual(parts[2], self.suggestion_id)

        success, msg = handle_merge(self.suggestion_id)
        self.assertTrue(success)

    @patch('telegram_callback_handler.get_db')
    @patch('telegram_callback_handler.send_telegram')
    def test_reject_via_url_path(self, mock_send, mock_get_db):
        """Simulates the URL path parsing for reject action."""
        mock_get_db.return_value = self.db
        from telegram_callback_handler import handle_reject
        path = f'/action/reject/{self.suggestion_id}'
        parts = path.strip('/').split('/')

        self.assertEqual(len(parts), 3)
        self.assertEqual(parts[0], 'action')
        self.assertEqual(parts[1], 'reject')

        success, msg = handle_reject(self.suggestion_id)
        self.assertTrue(success)

    def test_html_response_success(self):
        """html_response should produce valid HTML with success styling."""
        from telegram_callback_handler import html_response
        html = html_response("Test Title", "Test message", success=True)
        self.assertIn("Test Title", html)
        self.assertIn("Test message", html)
        self.assertIn("#4CAF50", html)  # green color for success

    def test_html_response_failure(self):
        """html_response should produce valid HTML with failure styling."""
        from telegram_callback_handler import html_response
        html = html_response("Error", "Something went wrong", success=False)
        self.assertIn("Error", html)
        self.assertIn("#f44336", html)  # red color for failure


class TestInlineKeyboardMarkup(unittest.TestCase):
    def test_send_merge_confirmation_includes_url_buttons(self):
        """send_merge_confirmation should include URL-based inline keyboard buttons."""
        db = create_test_db()

        candidate_contact = {
            'id': 'c1',
            'display_name': 'Heidi Lui',
            'relation': 'Wife',
            'is_family': 1,
        }
        candidate_handles = [
            {'handle_type': 'phone', 'handle_value': '+85294066060', 'message_count': 42},
        ]

        with patch('contact_manager.send_telegram') as mock_send:
            from contact_manager import send_merge_confirmation
            send_merge_confirmation(
                db, 'suggestion-123', 'heidi@gmail.com', 'Heidi Lui',
                candidate_contact, candidate_handles, 'Exact name match'
            )

            mock_send.assert_called_once()
            call_args = mock_send.call_args
            text = call_args[0][0]
            reply_markup = call_args[1]['reply_markup']

            # Verify inline keyboard structure
            self.assertIn('inline_keyboard', reply_markup)
            buttons = reply_markup['inline_keyboard'][0]
            self.assertEqual(len(buttons), 2)

            # Verify merge button uses URL (not callback_data)
            self.assertIn('Merge', buttons[0]['text'])
            self.assertIn('url', buttons[0])
            self.assertIn('/action/merge/suggestion-123', buttons[0]['url'])
            self.assertNotIn('callback_data', buttons[0])

            # Verify reject button uses URL (not callback_data)
            self.assertIn('Keep Separate', buttons[1]['text'])
            self.assertIn('url', buttons[1])
            self.assertIn('/action/reject/suggestion-123', buttons[1]['url'])
            self.assertNotIn('callback_data', buttons[1])

            # Verify text content
            self.assertIn('Heidi Lui', text)
            self.assertIn('heidi@gmail.com', text)
            self.assertIn('Contact Merge Suggestion', text)

        db.real_close()


if __name__ == '__main__':
    unittest.main()
