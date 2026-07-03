# Detect Scheduling Conflicts

Check for overlapping events across all calendar accounts and flag
double-bookings or tight transitions.

## Conflict Types

1. **Hard conflict**: Two confirmed events overlap in time
2. **Soft conflict**: Less than 15 minutes between back-to-back events
3. **Location conflict**: Back-to-back events in different physical locations
4. **Cross-account duplicate**: Same event appears on multiple accounts

## Output Format

```json
{
  "conflicts": [
    {
      "type": "hard",
      "event_a": "Board Meeting 10:00-11:30",
      "event_b": "Client Call 11:00-12:00",
      "overlap_minutes": 30
    }
  ]
}
```
