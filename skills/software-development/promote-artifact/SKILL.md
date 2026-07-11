---
name: promote-artifact
description: Move approved definitions from dev to prod.
version: 1.0.0
author: Leo Lau (@leolau), Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [datastore, dev-prod, promotion, approval]
    category: software-development
    related_skills: []
---

# Promote Artifact Skill

Move a validated tool, skill, config, or schema definition from `app_dev` to
`app_prod`. This procedure never moves raw application or user data.

## When to Use

- A definition has been authored and validated in dev.
- The user wants that exact version made available in prod.
- The promotion needs a durable approval, change-event, and audit row.

Do not use this skill for copying records from application data tables,
backfilling production data, or changing a running conversation's prompt.

## Prerequisites

- The Supabase/Postgres DSN is configured at
  `datastore.supabase_app.dsn` in `config.yaml`, preferably as a
  `${DATABASE_URL}` reference.
- The target definition exists in `app_dev.artifact_definitions`.
- The artifact has already passed its dev-mode checks.

## How to Run

Use `clarify` for approval, then use `terminal` to run:

```bash
hermes promote KIND:REF --approve --actor "ACTOR"
```

When a human runs the command directly, omit `--approve` to use the interactive
approval prompt.

## Quick Reference

| Value | Meaning |
|---|---|
| `tool:REF` | Tool definition |
| `skill:REF` | Skill definition |
| `config:REF` | Configuration definition |
| `schema:REF` | Schema definition |
| `--actor NAME` | Identity recorded in audit rows |
| `--approve` | Current operator explicitly approved this invocation |

## Procedure

1. Identify the exact artifact as `KIND:REF`.
2. Read or inspect the dev definition and summarize the production change.
3. Use `clarify` to ask the user for explicit approval of that exact artifact.
4. Stop on silence, ambiguity, or denial.
5. After approval, use `terminal` to run the command with `--approve`.
6. Report the returned promotion reference.

The command atomically writes the production definition, C6 approval, C5
change-event, and promotion audit row.

## Pitfalls

- Never promote raw application or user data.
- Store schema definitions as `{"sql": "<migration SQL>"}` and validate that
  SQL in `app_dev` before promotion.
- Schema migration SQL runs with `app_prod` as its search path; use
  unqualified relation names.
- Never copy tables outside `artifact_definitions`.
- Never substitute a similar artifact reference for the approved one.
- Never change a running conversation's system prompt or toolset.
- Never retry a denied promotion without a new explicit approval.

## Verification

- Confirm the command returns a promotion reference.
- Confirm the target definition is readable through the prod store.
- Confirm the approval and change references are attached to the promotion.
- Confirm dev-only definitions remain unavailable through prod reads.
- Confirm a failure or denial left production unchanged.
