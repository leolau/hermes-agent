---
name: extract-contacts
description: Contact recognition and extraction rules.
---

# Contact Extraction

## When to create/update a contact record
- A new phone number messages for the first time
- Someone mentions another person by name with their phone number
- A group message introduces a new participant

## What to extract
- Phone number (always available from sender)
- Display name / push name (from WhatsApp profile)
- Any additional context mentioned (company, role, relationship)

## Family detection
Family contacts are pre-configured in config.json. When a message arrives from a number in the family_contacts list, it is automatically marked is_family=1.

## Do NOT
- Create duplicate contact records for the same phone number
- Override user-set names with WhatsApp push names if user has already set a custom name
