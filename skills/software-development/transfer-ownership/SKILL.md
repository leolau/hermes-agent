---
name: transfer-ownership
description: Transfer the single shared-brain owner role to another enrolled user, approval-gated.
version: 1.0.0
author: Leo Lau (@leolau), Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [access-control, multi-user, owner, approval]
    category: software-development
    related_skills: []
---

# Transfer Ownership Skill

Move the single **owner** role of the shared Hermes brain from the current
owner to another already-enrolled principal. Exactly one principal is the owner
at any time; the transfer demotes the outgoing owner and promotes the target
atomically, records a C6 approval, and emits a C5 change-event.

## When to Use

- The current owner wants to hand the shared brain over to another user.
- The target user is already enrolled as a principal (member/admin/viewer).
- The handoff needs a durable approval and audit trail.

Do not use this skill to grant elevated access without transferring ownership,
to create new users, or to change a running conversation's prompt/toolset.

## Prerequisites

- The Supabase/Postgres DSN is configured at `datastore.supabase_app.dsn` in
  `config.yaml`, preferably as a `${DATABASE_URL}` reference.
- A current owner exists (`hermes owner show`).
- The target principal is enrolled (resolve them via pairing first if not).

## How to Run

Use `clarify` to obtain the current owner's approval, then use `terminal`:

```bash
hermes owner show                      # confirm the current owner
hermes owner transfer <TARGET_USER_ID> --approve --actor "ACTOR"
```

When a human runs the command directly, omit `--approve` to use the interactive
approval prompt. Add `--demote-to member` (or `viewer`) to change the outgoing
owner's role; the default keeps them as `admin`.

## Procedure

1. Run `hermes owner show` and confirm who currently owns the brain.
2. Confirm the target `user_id` is enrolled.
3. Use `clarify` to ask the **current owner** for explicit approval of the exact
   transfer (from → to).
4. Stop on silence, ambiguity, or denial.
5. After approval, run `hermes owner transfer <TARGET_USER_ID> --approve`.
6. Report the returned change reference.

## Pitfalls

- Never transfer ownership without the current owner's explicit approval.
- The transfer is only reversible by a second transfer back — treat it as
  high-consequence.
- Never substitute a similar `user_id` for the approved target.
- Do not run this against a dev store; ownership lives only in prod.
- Never change a running conversation's system prompt or toolset.

## Verification

- Confirm the command returns a change reference.
- Confirm `hermes owner show` now reports the new owner.
- Confirm exactly one owner exists (the command fails otherwise).
- Confirm an approval and change-event were recorded in `app_prod`.
