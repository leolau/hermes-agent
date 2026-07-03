# Classify Event Importance

Given a calendar event with title, description, attendees, and time,
classify its importance as critical/normal/low.

## Signals

High importance signals:
- External attendees (different domain from your accounts)
- Large attendee count (5+)
- Keywords: board, investor, client, deadline, review, demo, launch
- First-time meeting with new contacts
- Events within 2 hours

Low importance signals:
- Recurring weekly/daily with no description changes
- All-day events that are reminders
- Events you've declined
- "Focus time", "lunch", "blocked" in title
- Auto-generated events (birthdays, holidays)

## Output Format

```json
{
  "importance": "critical|normal|low",
  "reason": "Brief explanation of classification"
}
```
