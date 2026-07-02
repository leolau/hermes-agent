# Calendar Triage Skill

Classify calendar events by importance, extract preparation context,
and detect scheduling conflicts.

## Classification Rules

### Critical
- Client meetings (external attendees from client domains)
- Board meetings, investor meetings
- Job interviews (yours or as interviewer)
- Events with "deadline", "due", "final", "urgent" in title
- Events within next 2 hours with 5+ attendees
- Meetings where you are the organizer with 3+ external attendees

### Normal
- Internal team meetings
- Regular 1:1s
- Recurring standups
- Training sessions
- Events with 2-4 attendees

### Low
- Optional events (tentative, FYI)
- All-day reminders
- Blocked calendar time (focus time, lunch)
- Events you declined
- Recurring events with no recent changes
