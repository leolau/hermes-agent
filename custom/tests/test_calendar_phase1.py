#!/usr/bin/env python3
"""
Phase 1 Tests: Calendar DB schema + OAuth2 token refresh.
"""

import os
import sqlite3
import sys
import unittest
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'migrations'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'calendar'))

os.environ['DB_PATH'] = ':memory:'


class TestCalendarSchema(unittest.TestCase):
    """Test calendar table creation and schema."""

    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        # Create the unified_contacts table (foreign key target)
        self.db.execute("""CREATE TABLE unified_contacts (
            id TEXT PRIMARY KEY,
            display_name TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )""")
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def test_create_calendar_accounts_table(self):
        """calendar_accounts table should be created with correct columns."""
        from create_calendar_tables import create_tables
        create_tables(self.db)

        cols = self.db.execute("PRAGMA table_info(calendar_accounts)").fetchall()
        col_names = {c['name'] for c in cols}
        self.assertIn('id', col_names)
        self.assertIn('email', col_names)
        self.assertIn('label', col_names)
        self.assertIn('sync_token', col_names)
        self.assertIn('last_synced', col_names)
        self.assertIn('enabled', col_names)

    def test_create_calendar_events_table(self):
        """calendar_events table should have all required columns."""
        from create_calendar_tables import create_tables
        create_tables(self.db)

        cols = self.db.execute("PRAGMA table_info(calendar_events)").fetchall()
        col_names = {c['name'] for c in cols}
        expected = {
            'id', 'google_event_id', 'account_id', 'calendar_id',
            'summary', 'description', 'location',
            'start_time', 'end_time', 'all_day', 'timezone',
            'status', 'organizer_email', 'organizer_name',
            'recurring_event_id', 'html_link', 'conference_link',
            'raw_json', 'importance', 'triage_notes', 'triaged',
            'contact_id', 'created_at', 'updated_at'
        }
        self.assertTrue(expected.issubset(col_names))

    def test_create_calendar_attendees_table(self):
        """calendar_attendees table should have all required columns."""
        from create_calendar_tables import create_tables
        create_tables(self.db)

        cols = self.db.execute("PRAGMA table_info(calendar_attendees)").fetchall()
        col_names = {c['name'] for c in cols}
        expected = {
            'id', 'event_id', 'email', 'display_name',
            'response_status', 'organizer', 'self', 'contact_id'
        }
        self.assertTrue(expected.issubset(col_names))

    def test_create_calendar_reminders_table(self):
        """calendar_reminders table should have all required columns."""
        from create_calendar_tables import create_tables
        create_tables(self.db)

        cols = self.db.execute("PRAGMA table_info(calendar_reminders)").fetchall()
        col_names = {c['name'] for c in cols}
        expected = {'id', 'event_id', 'remind_at', 'lead_minutes', 'sent', 'sent_at'}
        self.assertTrue(expected.issubset(col_names))

    def test_seed_accounts(self):
        """Should seed 3 Google Calendar accounts."""
        from create_calendar_tables import create_tables, seed_accounts
        create_tables(self.db)
        seed_accounts(self.db)

        accounts = self.db.execute("SELECT * FROM calendar_accounts ORDER BY id").fetchall()
        self.assertEqual(len(accounts), 3)
        self.assertEqual(accounts[0]['id'], 'gcal1')
        self.assertEqual(accounts[0]['email'], 'leo11lau@gmail.com')
        self.assertEqual(accounts[1]['id'], 'gcal2')
        self.assertEqual(accounts[1]['email'], 'leolau@joyaether.com')
        self.assertEqual(accounts[2]['id'], 'gcal3')
        self.assertEqual(accounts[2]['email'], 'leolau@snappopapp.com')

    def test_idempotent_creation(self):
        """Running create_tables twice should not fail."""
        from create_calendar_tables import create_tables, seed_accounts
        create_tables(self.db)
        seed_accounts(self.db)
        create_tables(self.db)
        seed_accounts(self.db)

        accounts = self.db.execute("SELECT * FROM calendar_accounts").fetchall()
        self.assertEqual(len(accounts), 3)


class TestOAuthTokenRefresh(unittest.TestCase):
    """Test OAuth2 token refresh logic."""

    @patch('calendar_poller.urlopen')
    def test_refresh_access_token(self, mock_urlopen):
        """Should exchange refresh token for access token."""
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps({
            'access_token': 'ya29.test-access-token',
            'expires_in': 3600,
            'token_type': 'Bearer'
        }).encode()
        mock_response.__enter__ = lambda s: s
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_response

        import calendar_poller
        calendar_poller.GCAL_CLIENT_ID = 'test-client-id'
        calendar_poller.GCAL_CLIENT_SECRET = 'test-client-secret'
        calendar_poller._token_cache = {}

        token = calendar_poller.get_access_token('gcal1', 'test-refresh-token')
        self.assertEqual(token, 'ya29.test-access-token')
        mock_urlopen.assert_called_once()

    @patch('calendar_poller.urlopen')
    def test_token_caching(self, mock_urlopen):
        """Should cache access tokens and not re-request until expired."""
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps({
            'access_token': 'ya29.cached-token',
            'expires_in': 3600,
        }).encode()
        mock_response.__enter__ = lambda s: s
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_response

        import calendar_poller
        calendar_poller.GCAL_CLIENT_ID = 'test-client-id'
        calendar_poller.GCAL_CLIENT_SECRET = 'test-client-secret'
        calendar_poller._token_cache = {}

        token1 = calendar_poller.get_access_token('gcal_cache_test', 'test-refresh')
        token2 = calendar_poller.get_access_token('gcal_cache_test', 'test-refresh')

        self.assertEqual(token1, token2)
        self.assertEqual(mock_urlopen.call_count, 1)  # Only one API call


import json

if __name__ == '__main__':
    unittest.main()
