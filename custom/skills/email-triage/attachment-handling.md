---
name: attachment-handling
description: Handle emails with attachments
---
# Attachment Handling

## Important Attachment Types
- PDF: Could be invoice, contract, proposal → check subject/body for context
- DOCX/XLSX: Document review request → likely a task
- Images: Could be receipts, screenshots → check context
- ZIP: Could contain multiple files → note but don't extract

## Escalation Triggers
- Invoice/contract attachments → escalate if urgent
- Signed document requiring counter-signature → escalate
- Attachment from VIP sender → escalate

## Notes
- Store attachment metadata (name, type, size) but don't download
- Mention attachments in task descriptions: "Review attached proposal (proposal.pdf)"
