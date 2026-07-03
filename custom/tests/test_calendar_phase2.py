#!/usr/bin/env python3
"""
Phase 2 Tests: Calendar poller — event sync, dedup, update, cancel, conference extraction.
"""

import json
import os
import sqlite3
import sys
import unittest
import uuid
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'migrations'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'calendar'))

os.environ['DB_PATH'] = ':memory:'


def create_test_db():
    """Create an in-memory test DB with calendar + contacts schema."""
    db = sqlite3.connect(':memory:')
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")

    db.execute("""CREATE TABLE unified_contacts (
        id TEXT PRIMARY KEY, display_name TEXT NOT NULL,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL
    )""")

    from create_calendar_tables import create_tables, seed_accounts
    create_tables(db)
    seed_accounts(db)
    return db


def make_google_event(event_id='evt1', summary='Team Standup',
                      start_hours_from_now=1, duration_hours=1,
                      status='confirmed', attendees=None,
                      description='', location='',
                      conference_link=None, organizer_email='boss@company.com'):
    """Create a mock Google Calendar API event response."""
    now = datetime.now(timezone.utc)
    start = now + timedelta(hours=start_hours_from_now)
    end = start + timedelta(hours=duration_hours)

    event = {
        'id': event_id,
        'status': status,
        'summary': summary,
        'description': description,
        'location': location,
        'start': {'dateTime': start.isoformat(), 'timeZone': 'Asia/Hong_Kong'},
        'end': {'dateTime': end.isoformat(), 'timeZone': 'Asia/Hong_Kong'},
        'organizer': {'email': organizer_email, 'displayName': 'Boss'},
        'htmlLink': f'https://calendar.google.com/event?eid={event_id}',
        'attendees': attendees or [],
    }

    if conference_link:
        event['conferenceData'] = {
            'entryPoints': [{'entryPointType': 'video', 'uri': conference_link}]
        }

    return event


def make_allday_event(event_id='allday1', summary='Holiday', date='2026-07-04'):
    """Create a mock all-day event."""
    return {
        'id': event_id,
        'status': 'confirmed',
        'summary': summary,
        'start': {'date': date},
        'end': {'date': date},
        'organizer': {'email': 'me@gmail.com'},
        'htmlLink': f'https://calendar.google.com/event?eid={event_id}',
    }


class TestEventSync(unittest.TestCase):
    """Test event sync from API to DB."""

    def setUp(self):
        self.db = create_test_db()

    def tearDown(self):
        self.db.close()

    @patch('calendar_poller.gcal_api')
    @patch('calendar_poller.get_access_token')
    def test_sync_creates_new_events(self, mock_token, mock_api):
        """New events from API should be inserted into DB."""
        mock_token.return_value = 'fake-token'
        mock_api.return_value = {
            'items': [
                make_google_event('evt1', 'Meeting 1'),
                make_google_event('evt2', 'Meeting 2'),
            ],
            'nextSyncToken': 'sync-token-1'
        }

        from calendar_poller import sync_events
        created, updated, cancelled = sync_events(self.db, 'gcal1', 'refresh-token')

        self.assertEqual(created, 2)
        self.assertEqual(updated, 0)
        self.assertEqual(cancelled, 0)

        events = self.db.execute("SELECT * FROM calendar_events").fetchall()
        self.assertEqual(len(events), 2)
        summaries = {e['summary'] for e in events}
        self.assertIn('Meeting 1', summaries)
        self.assertIn('Meeting 2', summaries)

    @patch('calendar_poller.gcal_api')
    @patch('calendar_poller.get_access_token')
    def test_sync_updates_existing_events(self, mock_token, mock_api):
        """Updated events should be modified in DB, not duplicated."""
        mock_token.return_value = 'fake-token'

        # First sync
        mock_api.return_value = {
            'items': [make_google_event('evt1', 'Original Title')],
            'nextSyncToken': 'sync-1'
        }
        from calendar_poller import sync_events
        sync_events(self.db, 'gcal1', 'refresh-token')

        # Second sync with updated title
        mock_api.return_value = {
            'items': [make_google_event('evt1', 'Updated Title')],
            'nextSyncToken': 'sync-2'
        }
        created, updated, cancelled = sync_events(self.db, 'gcal1', 'refresh-token')

        self.assertEqual(created, 0)
        self.assertEqual(updated, 1)

        events = self.db.execute("SELECT * FROM calendar_events").fetchall()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]['summary'], 'Updated Title')

    @patch('calendar_poller.gcal_api')
    @patch('calendar_poller.get_access_token')
    def test_sync_handles_cancelled_events(self, mock_token, mock_api):
        """Cancelled events should be marked as cancelled in DB."""
        mock_token.return_value = 'fake-token'

        # First sync: create event
        mock_api.return_value = {
            'items': [make_google_event('evt1', 'Meeting')],
            'nextSyncToken': 'sync-1'
        }
        from calendar_poller import sync_events
        sync_events(self.db, 'gcal1', 'refresh-token')

        # Second sync: event cancelled
        mock_api.return_value = {
            'items': [{'id': 'evt1', 'status': 'cancelled'}],
            'nextSyncToken': 'sync-2'
        }
        created, updated, cancelled = sync_events(self.db, 'gcal1', 'refresh-token')

        self.assertEqual(cancelled, 1)
        event = self.db.execute("SELECT * FROM calendar_events WHERE google_event_id = 'evt1'").fetchone()
        self.assertEqual(event['status'], 'cancelled')

    @patch('calendar_poller.gcal_api')
    @patch('calendar_poller.get_access_token')
    def test_sync_saves_sync_token(self, mock_token, mock_api):
        """Sync token should be saved for incremental sync."""
        mock_token.return_value = 'fake-token'
        mock_api.return_value = {
            'items': [make_google_event('evt1', 'Meeting')],
            'nextSyncToken': 'new-sync-token-xyz'
        }

        from calendar_poller import sync_events
        sync_events(self.db, 'gcal1', 'refresh-token')

        account = self.db.execute(
            "SELECT sync_token FROM calendar_accounts WHERE id = 'gcal1'"
        ).fetchone()
        self.assertEqual(account['sync_token'], 'new-sync-token-xyz')

    @patch('calendar_poller.gcal_api')
    @patch('calendar_poller.get_access_token')
    def test_sync_all_day_event(self, mock_token, mock_api):
        """All-day events should be stored with all_day=1."""
        mock_token.return_value = 'fake-token'
        mock_api.return_value = {
            'items': [make_allday_event('allday1', 'Company Holiday', '2026-07-04')],
            'nextSyncToken': 'sync-1'
        }

        from calendar_poller import sync_events
        sync_events(self.db, 'gcal1', 'refresh-token')

        event = self.db.execute("SELECT * FROM calendar_events WHERE google_event_id = 'allday1'").fetchone()
        self.assertEqual(event['all_day'], 1)
        self.assertEqual(event['start_time'], '2026-07-04')
        self.assertEqual(event['summary'], 'Company Holiday')

    @patch('calendar_poller.gcal_api')
    @patch('calendar_poller.get_access_token')
    def test_sync_stores_attendees(self, mock_token, mock_api):
        """Attendees should be stored in calendar_attendees table."""
        mock_token.return_value = 'fake-token'
        mock_api.return_value = {
            'items': [make_google_event('evt1', 'Team Meeting', attendees=[
                {'email': 'alice@company.com', 'displayName': 'Alice', 'responseStatus': 'accepted'},
                {'email': 'bob@company.com', 'displayName': 'Bob', 'responseStatus': 'tentative'},
                {'email': 'leo11lau@gmail.com', 'self': True, 'responseStatus': 'accepted'},
            ])],
            'nextSyncToken': 'sync-1'
        }

        from calendar_poller import sync_events
        sync_events(self.db, 'gcal1', 'refresh-token')

        attendees = self.db.execute("SELECT * FROM calendar_attendees ORDER BY email").fetchall()
        self.assertEqual(len(attendees), 3)
        emails = [a['email'] for a in attendees]
        self.assertIn('alice@company.com', emails)
        self.assertIn('bob@company.com', emails)

        # Check self flag
        me = [a for a in attendees if a['email'] == 'leo11lau@gmail.com'][0]
        self.assertEqual(me['self'], 1)


class TestConferenceExtraction(unittest.TestCase):
    """Test extraction of video conference links."""

    def test_extract_google_meet_from_conference_data(self):
        """Should extract Google Meet link from conferenceData."""
        from calendar_poller import extract_conference_link
        event = make_google_event(conference_link='https://meet.google.com/abc-defg-hij')
        self.assertEqual(extract_conference_link(event), 'https://meet.google.com/abc-defg-hij')

    def test_extract_hangout_link(self):
        """Should extract hangoutLink field."""
        from calendar_poller import extract_conference_link
        event = {'hangoutLink': 'https://meet.google.com/xyz'}
        self.assertEqual(extract_conference_link(event), 'https://meet.google.com/xyz')

    def test_extract_zoom_from_description(self):
        """Should extract Zoom link from description text."""
        from calendar_poller import extract_conference_link
        event = {
            'description': 'Join Zoom: https://us02web.zoom.us/j/1234567890?pwd=abc',
            'location': ''
        }
        link = extract_conference_link(event)
        self.assertIn('zoom.us', link)

    def test_no_conference_link(self):
        """Should return empty string when no conference link found."""
        from calendar_poller import extract_conference_link
        event = {'description': 'In-person meeting', 'location': 'Office'}
        self.assertEqual(extract_conference_link(event), '')


class TestEventTimeParsing(unittest.TestCase):
    """Test event time parsing."""

    def test_parse_datetime_event(self):
        """Should parse dateTime events correctly."""
        from calendar_poller import parse_event_time
        event = {
            'start': {'dateTime': '2026-07-02T10:00:00+08:00', 'timeZone': 'Asia/Hong_Kong'},
            'end': {'dateTime': '2026-07-02T11:00:00+08:00', 'timeZone': 'Asia/Hong_Kong'},
        }
        start, end, all_day, tz = parse_event_time(event)
        self.assertEqual(start, '2026-07-02T10:00:00+08:00')
        self.assertEqual(end, '2026-07-02T11:00:00+08:00')
        self.assertFalse(all_day)
        self.assertEqual(tz, 'Asia/Hong_Kong')

    def test_parse_allday_event(self):
        """Should parse all-day events correctly."""
        from calendar_poller import parse_event_time
        event = {
            'start': {'date': '2026-07-04'},
            'end': {'date': '2026-07-05'},
        }
        start, end, all_day, tz = parse_event_time(event)
        self.assertEqual(start, '2026-07-04')
        self.assertTrue(all_day)


if __name__ == '__main__':
    unittest.main()
