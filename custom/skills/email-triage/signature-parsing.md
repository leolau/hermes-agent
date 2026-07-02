---
name: signature-parsing
description: Extract contact info from email signatures for contact correlation
---
# Signature Parsing

## Purpose
Extract phone numbers and alternative email addresses from email signatures to help correlate contacts across WhatsApp and Email channels.

## What to Extract
- Phone numbers (mobile, office, fax) → can link to WhatsApp contacts
- Alternative email addresses → link to same contact identity
- Company name → populate contact.company field
- Job title → useful context for triage priority

## Patterns
- "Tel:", "Phone:", "Mobile:", "Cell:", "M:", "T:", followed by number
- Numbers in formats: +852-XXXX-XXXX, (416) 723-9963, +1 XXX-XXX-XXXX
- "Email:", "E:" followed by email address
- Company name often appears after the person's name

## Output
Include in triage response:
```json
{
  "extracted_contacts": [
    {"type": "phone", "value": "+85212345678"},
    {"type": "email", "value": "alternate@company.com"},
    {"type": "company", "value": "DiamondBox Ltd"}
  ]
}
```
