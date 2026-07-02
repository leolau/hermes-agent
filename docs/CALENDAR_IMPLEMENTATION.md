# Google Calendar Integration — Implementation Plan

## Overview

Integrates Google Calendar across 3 accounts into the Hermes Agent pipeline.
Polls for events, stores them locally, links attendees to unified contacts,
provides LLM-classified importance and prep context, and sends Telegram reminders.

## Architecture

```
Google Calendar API (3 accounts, OAuth2)
       │
       ▼
┌─────────────────────┐
│  Calendar Poller     │  Polls every 60s using syncToken (incremental)
│  calendar_poller.py  │  Stores events in calendar_events table
│  Health: port 7903   │  Handles creates, updates, cancellations
└────────┬────────────┘
         │
         ▼
┌─────────────────────┐
│  Calendar Triage     │  DeepSeek classifies events:
│  calendar_triage.py  │  - Importance (critical/normal/low)
│                      │  - Attendee → contact linking
│                      │  - Context extraction (prep notes)
└────────┬────────────┘
         │
    ┌────┴────┐
    ▼         ▼
┌──────────┐ ┌──────────────────┐
│ Reminder │ │ Calendar MCP     │
│ Daemon   │ │ Server :8652     │
│          │ │ 9 tools          │
└──────────┘ └──────────────────┘
    │              │
    ▼              ▼
 Telegram      Hermes Agent
```

## Accounts

| ID    | Email                    | Label        |
|-------|--------------------------|--------------|
| gcal1 | leo11lau@gmail.com       | Personal     |
| gcal2 | leolau@joyaether.com     | Joyaether    |
| gcal3 | leolau@snappopapp.com    | SnappoPop    |

## Authentication

Google Calendar API requires OAuth2 (not App Passwords).

- **OAuth Client**: Desktop app type, created in Google Cloud Console
- **Scopes**: `https://www.googleapis.com/auth/calendar` (full read/write)
- **Token storage**: Refresh tokens saved as Devin secrets (`GCAL_REFRESH_TOKEN_1/2/3`)
- **Auth flow**: One-time script opens browser, user approves, script saves refresh token
- **Token refresh**: Access tokens auto-refreshed using refresh token (1-hour expiry)

## Database Schema

All tables in the unified SQLite DB (`whatsapp_data.db`).

### `calendar_accounts`

| Column     | Type    | Notes                          |
|------------|---------|--------------------------------|
| id         | TEXT PK | "gcal1", "gcal2", "gcal3"      |
| email      | TEXT    | Google account email           |
| label      | TEXT    | Human-friendly label           |
| sync_token | TEXT    | Google incremental sync token   |
| last_synced| TEXT    | ISO timestamp of last sync     |
| enabled    | INT    | 1/0                            |

### `calendar_events`

| Column             | Type    | Notes                                    |
|--------------------|---------|------------------------------------------|
| id                 | TEXT PK | UUID                                     |
| google_event_id    | TEXT    | Google's event ID (UNIQUE with account)  |
| account_id         | TEXT FK | → calendar_accounts.id                   |
| calendar_id        | TEXT    | "primary" or specific calendar ID        |
| summary            | TEXT    | Event title                              |
| description        | TEXT    | Event description/notes                  |
| location           | TEXT    | Physical or virtual location             |
| start_time         | TEXT    | ISO timestamp                            |
| end_time           | TEXT    | ISO timestamp                            |
| all_day            | INT    | 1 if all-day event                       |
| timezone           | TEXT    | IANA timezone (e.g. Asia/Hong_Kong)      |
| status             | TEXT    | confirmed/tentative/cancelled            |
| organizer_email    | TEXT    | Who created it                           |
| organizer_name     | TEXT    |                                          |
| recurring_event_id | TEXT    | Parent recurring event ID                |
| html_link          | TEXT    | Link to open in Google Calendar          |
| conference_link    | TEXT    | Google Meet / Zoom link extracted        |
| raw_json           | TEXT    | Full API response for future use         |
| importance         | TEXT    | LLM-classified: critical/normal/low      |
| triage_notes       | TEXT    | LLM-extracted prep context               |
| triaged            | INT    | 0/1 — has triage agent processed this    |
| contact_id         | TEXT FK | → unified_contacts.id (organizer)        |
| created_at         | TEXT    |                                          |
| updated_at         | TEXT    |                                          |

Unique constraint: `(google_event_id, account_id)`

### `calendar_attendees`

| Column          | Type    | Notes                              |
|-----------------|---------|-------------------------------------|
| id              | TEXT PK | UUID                               |
| event_id        | TEXT FK | → calendar_events.id               |
| email           | TEXT    | Attendee email                     |
| display_name    | TEXT    |                                    |
| response_status | TEXT    | accepted/declined/tentative/needsAction |
| organizer       | INT    | 1 if this attendee is the organizer|
| self            | INT    | 1 if this is your account          |
| contact_id      | TEXT FK | → unified_contacts.id (linked)     |

### `calendar_reminders`

| Column       | Type    | Notes                              |
|--------------|---------|-------------------------------------|
| id           | TEXT PK | UUID                               |
| event_id     | TEXT FK | → calendar_events.id               |
| remind_at    | TEXT    | When to send the Telegram reminder |
| lead_minutes | INT    | Minutes before event               |
| sent         | INT    | 0/1                                |
| sent_at      | TEXT    |                                    |

## Phases

### Phase 1: OAuth2 Setup + DB Schema (8 tests) — COMPLETE
- [x] Create calendar tables migration script (`custom/migrations/create_calendar_tables.py`)
- [x] Build OAuth2 auth flow script (`custom/calendar/calendar_auth.py`)
- [x] Add calendar section to config.example.json and .env.example
- [x] Create calendar triage skill files (`custom/skills/calendar-triage/`)
- [x] 8/8 tests passing (schema validation, account seeding, token refresh, caching)
- BLOCKED: Awaiting OAuth2 Client ID + Secret from user to obtain refresh tokens

### Phase 2: Calendar Poller (12 tests) — COMPLETE
- [x] Build `calendar_poller.py` with incremental sync via syncToken
- [x] Handle event creates, updates, cancellations
- [x] Extract conference links (Google Meet, Zoom, Teams)
- [x] Multi-account polling with per-account refresh tokens
- [x] Health check endpoint (port 7903)
- [x] Attendee sync with response status tracking
- [x] All-day event handling
- [x] Expired sync token recovery (full re-sync on HTTP 410)
- [x] 12/12 tests passing (sync, dedup, update, cancel, conference extraction, time parsing)
- BLOCKED: Cannot deploy until OAuth2 refresh tokens are obtained

### Phase 3: Attendee → Contact Linking (8 tests)
- [ ] Map attendee emails to unified_contacts
- [ ] Auto-create contacts for unknown attendees
- [ ] Track meeting frequency per contact
- [ ] Merge suggestions for attendees matching existing contacts
- Tests: linking, auto-create, frequency stats, merge triggers

### Phase 4: Calendar Triage Agent (8 tests)
- [ ] DeepSeek classifies importance
- [ ] Extracts prep context from title/description/attendees
- [ ] Detects scheduling conflicts
- [ ] Identifies meeting patterns
- Tests: classification, prep extraction, conflict detection

### Phase 5: Reminder Daemon (8 tests)
- [ ] Configurable lead times per importance level
- [ ] Telegram notifications with event details
- [ ] Skip declined/cancelled events
- [ ] Dedup reminders (don't re-send)
- Tests: scheduling, sending, skip logic, dedup

### Phase 6: Calendar MCP Server — port 8652 (12 tests)
Tools:
- `calendar_today` — today's events across all accounts
- `calendar_week` — this week's events
- `calendar_search` — search by title/attendee/date range
- `calendar_event_detail` — full event with attendees + triage notes
- `calendar_attendees` — who's in a specific event
- `calendar_free_slots` — find available time windows
- `calendar_create_event` — create event via API
- `calendar_contacts_meetings` — meeting frequency per contact
- `calendar_upcoming_with_contact` — next meetings with a specific person

### Phase 7: Integration + Self-Learning (10 tests)
- [ ] Add calendar events to merged hourly digest
- [ ] Calendar triage skill files
- [ ] Hermes memory integration
- [ ] End-to-end test: poll → triage → remind → query

## Environment Variables

| Variable              | Description                        |
|-----------------------|------------------------------------|
| GCAL_CLIENT_ID        | OAuth2 client ID                   |
| GCAL_CLIENT_SECRET    | OAuth2 client secret               |
| GCAL_REFRESH_TOKEN_1  | Refresh token for leo11lau@gmail.com |
| GCAL_REFRESH_TOKEN_2  | Refresh token for leolau@joyaether.com |
| GCAL_REFRESH_TOKEN_3  | Refresh token for leolau@snappopapp.com |

## Config Section (in config.json)

```json
{
  "calendar": {
    "accounts": [
      {
        "id": "gcal1",
        "email": "leo11lau@gmail.com",
        "label": "Personal",
        "refresh_token_env": "GCAL_REFRESH_TOKEN_1",
        "calendars": ["primary"],
        "poll_interval_seconds": 60,
        "enabled": true
      }
    ],
    "reminders": {
      "critical": [1440, 60, 15],
      "normal": [60, 15],
      "low": [15]
    },
    "working_hours": {
      "start": "09:00",
      "end": "19:00",
      "timezone": "Asia/Hong_Kong"
    },
    "triage": {
      "model": "deepseek-chat",
      "provider": "deepseek"
    }
  }
}
```

## Recovery Instructions

If the calendar pipeline stops:

1. Check processes: `docker exec hermes-agent ps aux | grep calendar`
2. Check logs: `docker exec hermes-agent cat /opt/data/calendar/poller.log | tail -20`
3. Restart poller: `docker exec -d hermes-agent bash -c 'cd /opt/data/whatsapp-messages && python3 calendar_poller.py > /opt/data/calendar/poller.log 2>&1'`
4. If OAuth token expired: re-run auth flow script to get new refresh token

## Progress Tracking

| Phase | Status      | Tests   | Date       |
|-------|-------------|---------|------------|
| 1     | Complete    | 8/8     | 2026-07-02 |
| 2     | Complete    | 12/12   | 2026-07-02 |
| 3     | Pending     | 0/8     |            |
| 4     | Pending     | 0/8     |            |
| 5     | Pending     | 0/8     |            |
| 6     | Pending     | 0/12    |            |
| 7     | Pending     | 0/10    |            |
