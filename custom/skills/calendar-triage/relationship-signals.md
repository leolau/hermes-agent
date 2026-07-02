# Relationship Signals from Calendar

Infer contact relationships from meeting patterns.

## Patterns to Detect

- **Weekly 1:1** → direct report, manager, or close collaborator
- **Same recurring meetings** → same team or project
- **Always appears with X** → assistant, co-lead, or partner
- **Client domain + monthly cadence** → account management
- **Board meeting attendee** → board member or advisor
- **Only in large meetings** → executive or stakeholder

## Output Format

```json
{
  "relationship_signals": [
    {
      "contact_email": "alice@company.com",
      "pattern": "Weekly 1:1 every Monday",
      "inferred_relationship": "direct_report",
      "confidence": "high",
      "evidence": "12 consecutive Monday meetings in past 3 months"
    }
  ]
}
```
