---
name: classify-emails
description: Email classification taxonomy
---
# Email Classification

Classify each email into one of these categories:

| Category | Description | Example |
|----------|-------------|---------|
| urgent_business | Needs immediate response/action | Client deadline, payment issue |
| meeting | Calendar invite, meeting request, scheduling | Meeting at 3pm, schedule change |
| task | Contains an action item or request | "Please review", "Can you send" |
| invoice | Payment request, invoice, receipt | Invoice #1234, payment due |
| sales_opportunity | Potential deal, RFP, lead | "Interested in your services" |
| informational | FYI, no action needed | Status update, announcement |
| newsletter | Marketing, subscription, automated | Weekly digest, promotional |
| notification | System-generated alerts | Password reset, login alert |
| personal | From friends/family, non-business | Social, personal matters |
| spam | Unwanted, suspicious | Phishing, unsolicited |

## Priority Mapping
- urgent_business, invoice (due soon) → HIGH
- meeting (today/tomorrow), sales_opportunity → MEDIUM
- task, informational, personal → LOW
- newsletter, notification, spam → IGNORE
