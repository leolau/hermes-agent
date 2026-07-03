---
name: extract-tasks
description: Patterns for extracting actionable tasks from messages.
---

# Task Extraction

## What counts as a task
- Explicit requests: "please do X", "can you X", "need you to X"
- Deadlines: any message mentioning a date/time as a due date
- Action items: "remember to", "don't forget", "make sure to"
- Appointments: meetings, calls, visits with specific times

## Output format
For each task extracted:
- description: clear, actionable description
- due_date: ISO date if mentioned (null if no date given)
- priority: high (within 24h or explicit urgency), medium (within a week), low (no deadline)
- source: quote the relevant part of the message

## Date parsing
- "tomorrow" -> next day from message timestamp
- "next Monday" -> upcoming Monday
- "by Friday" -> that Friday
- "ASAP" -> today, priority=high
- "end of month" -> last day of current month
- No date mentioned -> due_date=null, priority=low
