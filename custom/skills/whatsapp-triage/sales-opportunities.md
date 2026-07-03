---
name: sales-opportunities
description: Detection rules for time-sensitive sales opportunities.
---

# Sales Opportunity Detection

## Indicators (escalate if 2+ present)
- Inquiry about pricing, quotes, or availability
- Mention of budget, purchase order, or procurement
- Request for proposal (RFP) or bidding
- Competitor comparison ("we're also looking at X")
- Timeline pressure ("need by next week", "budget expires")
- Volume indicators ("100 units", "for the whole team", "enterprise")
- Decision-maker language ("I'm the CTO", "we've decided to go with")

## False positives (do NOT escalate)
- General questions about a product/service with no urgency
- Spam or automated marketing messages
- Existing customers with routine support queries
- Messages from unknown numbers with no clear business context

## Output
- Escalate with reason="sales_opportunity"
- Summary should include: what they want, quantity/scope, timeline, contact info
