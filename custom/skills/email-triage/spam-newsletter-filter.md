---
name: spam-newsletter-filter
description: Identify and filter marketing emails and newsletters
---
# Newsletter & Spam Filter

## Auto-Ignore Signals
- Sender contains "noreply@", "newsletter@", "marketing@", "notifications@"
- Subject contains "unsubscribe", "weekly digest", "monthly update"
- Body contains unsubscribe links
- Bulk mail headers (X-Mailer, List-Unsubscribe)
- Known marketing platforms (mailchimp, sendgrid, constant contact)

## Action
- Classification: "newsletter" or "spam"
- Do NOT extract tasks from newsletters
- Do NOT escalate
- Store silently for digest summary count only
