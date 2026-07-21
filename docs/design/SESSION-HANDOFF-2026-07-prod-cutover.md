# Session Hand-off — ai-prentice-4-all production cutover (2026-07)

> **Purpose:** let the next agent (or human) pick up development without re-deriving
> the live-infra state. This note is **operational** — the design source of truth
> remains [`master-plan/README.md`](./master-plan/README.md) (decisions D1–D15,
> contracts C1–C9, FG index, gates, append-only §9 changelog). This file records
> **what is deployed right now**, how to reach it, and the known follow-ups.
>
> Author: devin:3c64bcf2 (for Leo). Product name: **ai-prentice-4-all** (built on Hermes).

---

## 0. TL;DR of current live state

- **Public product URL:** <https://leolau.ai-and-i.io> — HTTPS (Let's Encrypt via
  Caddy), password-gated dashboard, serving **current `develop`** with the FG-17
  Next.js panels.
- **It runs on the STRONG box** (`hermes-systest`, 4 vCPU / 16 GB, `47.83.199.25`),
  which is now the de-facto production host. The old 2/4 box (`8.217.86.90`) is
  **stopped but intact** for rollback.
- **Telegram bot** `@ai_prentice_systest_01_bot` runs as an always-on
  `hermes-gateway.service` on the strong box only (old box's poller stopped to end
  the dual-poll conflict).
- **All 10 targeted FGs promoted** to `app_prod` on the strong box (FG-03, 04, 05,
  08, 11, 12, 16, 18, 15, 17) — schema initialized + RLS + functional smoke.
- **Open security follow-up:** the dashboard owner password was exposed in chat and
  **must be rotated** (not yet authorized/done).

---

## 1. Hosts / infrastructure

| Role | Name | Instance ID | IP | Spec | State |
|------|------|-------------|----|------|-------|
| **Production (current)** | `hermes-systest` | `i-j6c81aisv2dd8mg17yle` | `47.83.199.25` | `ecs.e-c1m4.xlarge`, 4 vCPU / 16 GB, Ubuntu 24.04, 100 GB ESSD at `/opt/data` | **live** — current `develop`, FG-17 dashboard, `app_prod`, gateway + Caddy active |
| Old public (rollback) | `ai-prentice` | `i-j6camnt3ocwlmzajthil` | `8.217.86.90` | `ecs.e-c1m2.large`, 2 vCPU / 4 GB | **stopped** — stale `hermes-agent:local` (v0.17.0), old dashboard, container + Caddy stopped, kept for rollback |

Both are Alibaba Cloud ECS in **`cn-hongkong`**.

### Reaching a box (no SSH key on file)
Use the `aliyun` CLI → ECS RunCommand (Cloud Assistant). Creds are in the agent VM
env (`ALIBABA_CLOUD_ACCESS_KEY_ID` / `ALIBABA_CLOUD_ACCESS_KEY_SECRET`). Helper on
the agent VM:

```bash
bash /home/ubuntu/runbox2.sh <instance-id> <script-file>
# base64-encodes the script, RunCommand --Type RunShellScript --ContentEncoding Base64,
# then polls DescribeInvocationResults and prints decoded Output.
```

The `alibabacloud` MCP server currently fails to init (`connection closed`) — use
the `aliyun` CLI path instead.

---

## 2. Networking / DNS / TLS

- **Registrar:** Namecheap (unchanged — NOT transferred).
- **DNS:** now managed by **Cloudflare**. Namecheap points at Cloudflare
  nameservers `patrick.ns.cloudflare.com` / `zoe.ns.cloudflare.com`.
- **Zone:** `ai-and-i.io` (Cloudflare Free plan, zone Active).
- **A record:** `leolau.ai-and-i.io → 47.83.199.25`, **DNS-only (not proxied)**, TTL 300.
  (Was `8.217.86.90` before cutover.)
- **Cloudflare API token** `CLOUDFLARE_DNS_API_TOKEN` is saved (scoped Zone·DNS·Edit,
  `ai-and-i.io` only) → future `*.ai-and-i.io` per-user subdomains can be automated.
- **TLS/reverse proxy:** Caddy v2.11.4 on the strong box. `/etc/caddy/Caddyfile`:

  ```caddy
  leolau.ai-and-i.io {
      encode zstd gzip
      reverse_proxy 127.0.0.1:9119
  }
  ```

  Auto HTTP→HTTPS redirect, Let's Encrypt cert (HTTP-01), expiry ~Oct 18 2026,
  auto-renews. Raw dashboard port 9119 is NOT exposed publicly (security group).

---

## 3. Dashboard (FG-17) — how it's served + auth

- Next.js build lives in `hermes_cli/web_dist` (built from `web/`), served by the
  Python dashboard bound to `0.0.0.0:9119`, fronted by Caddy.
- Rebuild after web changes: `npm run build -w web` (runs `next build` +
  `scripts/copy-dist.mjs`, which syncs `web/out` → `hermes_cli/web_dist`).
- Start command used: `hermes dashboard --skip-build --host 0.0.0.0 --port 9119 --no-open --insecure`.
  **`--insecure` no longer bypasses auth** for non-loopback binds (see
  `hermes_cli/web_server.py::should_require_auth`).
- **Auth** = bundled provider (`hermes_cli/dashboard_auth`), configured in the box
  config under `dashboard.basic_auth` (`username`, `password_hash`, `secret`).
  Plaintext password is NOT persisted. Behavior verified:
  `GET /` → 302, `GET /login` → 200, `GET /api/config` → 401,
  `POST /auth/password-login` → 200.
- **Panels/routes live** (verified in browser): `/onboarding` (Getting started),
  `/core` (Core area), `/gts` (GTS Centre), `/telegram`, `/webview` (Agent webview).

> ⚠️ **Owner login was exposed in chat earlier — rotate it.** Do NOT reuse the old
> password. When rotating, present the standard secret options and set a new
> `password_hash` in `dashboard.basic_auth`; do not weaken auth to work around it.

---

## 4. Telegram gateway

- Bot: `@ai_prentice_systest_01_bot`. Runs as `hermes-gateway.service` (systemd,
  `Restart=always`, reboot-durable) on the **strong box only**.
- Owner Telegram ID allowlisted: `telegram.allow_from: ['8756039695']`. Everyone
  else denied by default. It runs the full native agent loop (model
  `deepseek-v4-pro`), not the old one-shot demo script.
- Verified: `getMe ok=True`, no webhook, `pending=0`, no polling conflict.
- The old box's container (which also polled the same token) was **stopped** to end
  the dual-poll conflict.
- **Known onboarding gap:** onboarding readiness still reports Telegram
  "bot token set, no home channel bound". Bot works via the allowlist; binding a
  "home channel" would clear the readiness flag (not yet done).

---

## 5. Data plane / promotion

- `datastore.mode` on the box = **`dev`** for local/dashboard/CLI (uses `app_dev`),
  so the testing dashboard view is untouched. **Channel traffic (Telegram) is
  always prod-routed** → uses `app_prod`.
- All 10 FGs promoted: `app_prod` went **9 → 25 tables** (idempotent
  `CREATE … IF NOT EXISTS`, no drops), RLS enabled on app tables, per-FG audit rows
  (approval + change + promotion) recorded. Backups saved to
  `/opt/data/backups/*-20260720-001050*` before promotion.
- Prod functional smoke passed (write→read→cleanup, no rows left behind) for goals+GTS
  (FG-04/18), memory pgvector (FG-05), trace join (FG-16), changes audit (FG-12);
  presence + E2E green for FG-03/08/11/15/17.
- Supabase runs as its own Docker stack (10 healthy containers) on the box —
  untouched by Hermes source redeploys.

---

## 6. Code changes landed this session (merged to `develop`)

- **PR #38** — `test(FG-04)`: de-flaked the time-brittle stale-metric goal registry
  E2E (pinned the measurement clock deterministically instead of the real wall
  clock). This unblocked a legitimately-green FG-04 gate.
- **PR #36 / #37** — docs: refreshed the stale `AGENT-HANDOFF.md` to point at the
  master plan; corrected FG-09 "Not started" status header.
- FG-17b / FG-19 work (dashboard panels + per-user GTS isolation/assignment) landed
  in prior sessions (PRs #34/#35) and is what the deployed dashboard renders.

The production cutover itself (DNS, Caddy, gateway service, promotion) was an
**infrastructure operation on the box** — no repo code change was required for it.

---

## 7. Redeploying / rollback runbook

**Redeploy current `develop` to the strong box** (what was done): back up tree +
`config.yaml`/`.env` + DB dump → snapshot `develop` in place at
`/opt/data/hermes-agent` (preserve `config.yaml`, `.env`, WhatsApp sessions) →
`pip install -e .` → `npm run build -w web` → restart `hermes-gateway.service` +
dashboard → verify gateway (0 pending) + dashboard (HTTP 200) + new FG API endpoints.

**Rollback to old box** (if needed):
```bash
# on old box 8.217.86.90:
docker start hermes-agent
systemctl start caddy
# then repoint Cloudflare A record leolau.ai-and-i.io -> 8.217.86.90
```

Operational helper scripts from this session live on the agent VM under
`/home/ubuntu/` (e.g. `runbox2.sh`, `prod_caddy_install.sh`, `prod_setpw.sh`,
`prod_verify_gw.sh`, `prod_stop_old_container.sh`, `deploy_*.sh`, `promote_pg.sh`).
These are NOT in the repo — recreate/commit them if you need them in future
sessions.

---

## 8. Open follow-ups (owner-gated)

1. **Rotate the dashboard owner password** (exposed in chat) — recommended first.
2. Optionally **bind a Telegram home channel** so onboarding stops reporting
   "no home channel bound".
3. Decide whether to **retain or decommission the stopped old box** (`8.217.86.90`).
   Not authorized to delete — kept for rollback.
4. Optionally verify a **fresh live Telegram round-trip** from the owner's phone.
5. **FG-03 live WhatsApp/email round-trip** + auto-reply/SMTP — pending channel
   creds (Gmail IMAP app-passwords; WhatsApp QR bind).
6. **Promote the merged-but-not-yet-promoted feature groups.** All FGs are
   implemented + merged to `develop` except **FG-02** (blockchain, on hold) —
   there is no un-written FG. Only 10 were promoted to `app_prod` in this cutover
   (FG-03/04/05/08/11/12/15/16/17/18); still `develop`-only for prod:
   **FG-01, 06, 07, 09, 10, 13, 14, 19** (each has a merged PR — #12/#18/#20/#22/#19/#9/#27/#35).
   Their remaining work is the owner-gated ECS system-test + prod promotion, not
   new code. FG-19 is complete (lifecycle + RLS + authority + audit E2E green).
7. **FG-02 blockchain** stays ON HOLD unless the owner resumes it.
8. Optionally flip `datastore.mode` to `prod` (or a dedicated prod host) so the
   dashboard reads prod data — currently intentionally on `dev`.

---

## 9. Constraints to respect (from `AGENTS.md` / master plan)

- Prompt-cache safety: system prompt byte-stable within a conversation; strict role
  alternation; never inject a synthetic user message mid-loop.
- Core is immutable to the runtime agent (C7, fail-closed); every interaction traced
  end-to-end (C8 `trace_id`).
- `.env` = secrets only; behavioral config in `config.yaml` or the datastore.
- Never weaken access control / RLS or Core write-protection to make something work.
- Real-path E2E for security/datastore/network/I/O changes; assert invariants, not
  snapshots. Preserve contributor authorship.
- Request GitHub user `leolau` as reviewer on every PR.
