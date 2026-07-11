---
name: acquire-oss-capability
description: Acquire a capability from open source — either a remote system (clone + host an OSS project on a DIFFERENT machine and wrap it as MCP) or an in-house rebuild (Next.js + MCP via the FG-07 scaffolder). Approval-gated, license-vetted, provenance-tracked.
version: 1.0.0
author: Leo Lau (@leolau), Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [oss, mcp, remote, in-house, provenance, approval, dev-prod, registry]
    category: software-development
    related_skills: [create-in-house-tool, promote-artifact, fastmcp, native-mcp]
---

# Acquire OSS Capability Skill

Bring a new capability into Hermes from open source **without vendoring anyone
else's project into the core tree**. Two modes (decision D3):

- **Remote system** — study an OSS project, clone + host it on a **different**
  machine with minimal/ideally no changes, and reach it through a generated
  `fastmcp` wrapper. Follows architecture §4.3 (propose → vet → adapt → run →
  expose-MCP → retire) with all hard rails.
- **In-house system** — rebuild the capability in-house by **reusing the FG-07
  scaffolder** (Next.js web UI + thin MCP server in its own Node process).

Both register a `remote` / `in_house` tool in the FG-07 registry, an MCP
endpoint in the FG-11 registry (for a **future** session — never spliced into a
live conversation), and a provenance row. New systems land **disabled** in
`dev` and reach `prod` only through the FG-07 approval-gated promotion.

## When to Use

- The user needs a capability that a well-licensed OSS project already provides
  (prefer remote adapt-and-wrap) or that should be built fresh (in-house).

Do NOT use this to add a new *core* model tool, to vendor a third-party project
under the repo tree, or to splice a tool into a running conversation.

## Prerequisites

- `datastore.supabase_app.dsn` is set in `config.yaml` (prefer `${DATABASE_URL}`).
- For a **remote** acquisition: an SSH-reachable host (the different machine),
  and the exact upstream commit you intend to pin.
- The operator is the enrolled owner, or pass `--as <user_id>`.

## How to Run

```bash
# 1. Propose: find candidates for a stated goal (ranked by fit; flag licenses).
hermes oss discover "convert office documents to markdown" --allowed-only

# 2a. Remote: adapt-and-wrap an OSS project on a DIFFERENT machine (§4.3).
hermes oss acquire markitdown \
  --repo https://github.com/microsoft/markitdown \
  --license MIT --commit <full-sha> \
  --host ai-prentice-2 --ssh-user hermes \
  --start-cmd "python -m markitdown.server" \
  --health-url http://127.0.0.1:8080/health
#   -> approval #1 (evaluate) then approval #2 (apply): 2 human approvals.

# 2b. In-house: rebuild via the FG-07 scaffolder instead.
hermes oss acquire invoicer --in-house

# 3. Review provenance, then enable + promote through the FG-07 path.
hermes oss list
hermes tool enable markitdown --mode dev
hermes tool promote markitdown            # C6-approval-gated dev -> prod

# 4. Retire (stage 6): disable + stop the hosted service.
hermes oss retire markitdown
```

Add `--shared` to register a system shared instead of private to the operator.
`--mode {dev,prod}` selects the datastore mode (default: config / prod).

## Procedure (remote, §4.3)

1. **Propose.** `hermes oss discover <goal>` and pick a candidate. Only
   permissive licenses (MIT/BSD/Apache-2.0/ISC …) pass; copyleft (GPL/AGPL/…)
   is rejected — escalate to the owner if a copyleft project is essential.
2. **Approve evaluation (#1)** when prompted.
3. **Vet.** The pipeline enforces the license allowlist and runs the
   supply-chain / secret / sandbox-smoke checks; a failure blocks the pipeline.
4. **Adapt.** The clone is **commit-pinned** and lands on the `--host` under
   `--workdir` (default `/opt/data/internal-solutions/<name>`) — never in the
   Hermes core tree.
5. **Run.** The service runs **non-root**, **network-restricted**, bound to
   localhost on that host.
6. **Expose as MCP + approve application (#2).** A thin `fastmcp` wrapper is
   generated OUTSIDE the core tree; the FG-11 endpoint + FG-07 `remote` tool +
   provenance row are registered only after the second approval.

## Pitfalls

- **Prompt-cache safety is sacred.** A newly acquired system's MCP endpoint
  only surfaces in a *fresh* session; nothing mutates a running conversation.
- **Never vendor the OSS project into the repo tree** — it is cloned onto a
  different host; only the thin wrapper is ours.
- **Two approvals minimum** (evaluate, apply). A denied gate registers nothing.
- **Pin the exact commit** — no silent upstream updates.
- No new `HERMES_*` env vars for non-secret config; secrets go in the hosted
  service's own `.env`.

## Verification

- `hermes oss list` shows the acquired system with its provenance (repo,
  pinned commit, license, host) for `remote`, or "in-house rebuild".
- `hermes tool list` shows the `remote` / `in_house` tool (disabled in dev).
- The generated `solution_mcp.py` (remote) or `mcp/server.mjs` (in-house)
  completes an MCP handshake, and a non-owner cannot see another member's
  private system.
