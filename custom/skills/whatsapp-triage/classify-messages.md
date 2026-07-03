---
name: classify-messages
description: Message classification taxonomy for WhatsApp triage.
---

# Message Classification Taxonomy

## Categories

### task
A message that contains an actionable item someone needs to do.
Examples: "Can you send me the report by Friday?", "Please call the plumber", "Reminder: dentist at 3pm tomorrow"

### reminder
A message that reminds about an existing commitment or deadline.
Examples: "Don't forget the meeting at 2", "Tomorrow is the deadline", "Pickup at 4pm"

### note
A message containing information worth saving but not actionable.
Examples: "New address is 123 Main St", "The wifi password is abc123", "John's birthday is March 5"

### urgent_business
A business message requiring immediate attention (within hours, not days).
Examples: "Client threatening to cancel", "Server is down", "Need approval before 5pm", "Payment overdue, action required"

### sales_opportunity
A message indicating a potential sale or business opportunity that is time-sensitive.
Examples: "Interested in buying 100 units", "Can you quote for this project?", "We have budget to spend before month end"

### informational
General information, news, or updates that don't require action.
Examples: "FYI the office is closed Monday", "Here's the link you asked for", "Good morning!"

### ignorable
Messages with no useful content to extract or store.
Examples: "ok", "lol", "thumbs up emoji", "seen", forwarded memes/jokes with no context
