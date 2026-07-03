# Extract Preparation Context

Analyze the event title, description, attendees, and related contact
history to suggest what the user should prepare before the meeting.

## What to extract

- Action items mentioned in the description
- Documents or materials referenced
- Previous meeting outcomes with same attendees
- Relevant recent emails or messages with attendees
- Agenda items

## Output Format

```json
{
  "prep_notes": "Review Q2 report before board meeting",
  "related_topics": ["quarterly-review", "financials"],
  "attendee_context": "3 board members + CFO, last met 2026-06-01"
}
```
