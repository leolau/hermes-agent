---
name: testing-datastore-promotion
description: Test datastore routing and artifact promotion end to end.
---

# Testing Datastore Promotion

Use this skill to validate C3 mode routing and approval-gated dev-to-prod artifact promotion without contacting production services.

## When to Use

- Changes touch `hermes_cli/datastore.py`, `hermes_cli/promote.py`, or `gateway/session.py`.
- Promotion approval, audit links, schema DDL, or dev/prod isolation changes.

## Devin Secrets Needed

None.

## Prerequisites

- Repository development dependencies installed.
- A running Docker daemon.
- Use `scripts/run_tests.sh`; never invoke pytest directly.
- Use a temporary `HERMES_HOME` and a digest-pinned disposable Postgres container.

## How to Run

1. Run the real installed `hermes promote` CLI against the disposable Postgres DSN.
2. Probe the published host DSN with the Postgres client before testing; container-local `pg_isready` can succeed before Docker's published port accepts connections.
3. Run the focused suite:

```bash
scripts/run_tests.sh tests/hermes_cli/test_datastore.py tests/hermes_cli/test_datastore_promotion_e2e.py tests/hermes_cli/test_promote.py tests/hermes_cli/test_startup_plugin_gating.py tests/skills/test_promote_artifact_skill.py
```

4. Run `scripts/run_tests.sh tests/plan_baseline/`, Ruff on changed files, and ty on the datastore/promotion modules.

## Assertions

- Omitted mode is `prod`; explicit local/API authoring can use `dev`.
- Every channel-originated session remains `prod` when `dev` is requested.
- SQLite resolves to distinct `state.db` and `state.dev.db` files.
- Approved promotion exits zero and links one C6 approval, C5 change, and promotion row.
- Schema promotion creates the prod table but copies no dev rows.
- Denial exits nonzero and leaves prod definitions and audit counts unchanged.
- `promote` remains in `_BUILTIN_SUBCOMMANDS` so startup skips plugin discovery.

## Cleanup

Force-remove the disposable Postgres container in a `finally` path. Do not contact Supabase, ECS, staging, or production.