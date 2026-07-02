---
name: extract-deadlines
description: Extract deadlines and due dates from email body
---
# Deadline Extraction

Scan email body and subject for deadlines:

## Patterns to Match
- Explicit dates: "by June 30", "deadline: July 15", "due 2024-07-01"
- Relative dates: "by end of week", "by tomorrow", "within 3 days", "ASAP"
- Meeting times: "meeting at 3pm today", "call scheduled for Monday"
- Payment terms: "net 30", "payment due in 7 days"

## Output
For each deadline found:
- `description`: What needs to be done
- `due_date`: ISO date (YYYY-MM-DD) or null if unclear
- `priority`: high (today/tomorrow), medium (this week), low (later)

## Escalation Trigger
If deadline is within 24 hours → mark for escalation
