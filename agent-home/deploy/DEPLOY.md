# agent-home — on-box deploy runbook (FG-20, Decision 2)

Deploys `agent-home` on the **existing prod box** behind the **existing Caddy**,
on its own subdomain, same box + same Supabase as the Python AI layer and the
`web/` dashboard. Nothing here changes `web/` or the Python service.

> **Owner-gated.** Prod deploy touches the running box. Only run this with the
> owner's go-ahead. The ECS system-test gate is separate and also owner-gated.

## Live-infra facts (see `docs/design/SESSION-HANDOFF-2026-07-prod-cutover.md`)

- **Prod box:** `hermes-systest`, Alibaba ECS `i-j6c81aisv2dd8mg17yle`, `47.83.199.25`, cn-hongkong.
- **Reach it:** `aliyun` CLI → ECS RunCommand (creds `ALIBABA_CLOUD_ACCESS_KEY_ID/SECRET` in the agent VM env; no SSH key on file).
- **Checkout on box:** `/opt/data/hermes-agent` (tracks `develop`).
- **Python AI layer / dashboard:** bound `0.0.0.0:9119`, fronted by Caddy at `leolau.ai-and-i.io`.
- **Caddy:** v2.11.4, config `/etc/caddy/Caddyfile`.
- **DNS/TLS:** Cloudflare zone `ai-and-i.io`; A records are **DNS-only (unproxied)**, TTL 300; Let's Encrypt via Caddy HTTP-01. Cloudflare token `CLOUDFLARE_DNS_API_TOKEN` (Zone·DNS·Edit) enables subdomain automation.
- **Supabase:** self-hosted Docker stack on the box (Postgres + Storage + RLS). `datastore.mode` on the box = `dev` for dashboard/CLI (`app_dev`); channel traffic is prod-routed (`app_prod`).

## 1. DNS — point a subdomain at the box

Pick a subdomain, e.g. `home.leolau.ai-and-i.io`. Add a Cloudflare **A record**
→ `47.83.199.25`, **DNS-only (grey cloud)**, TTL 300 — mirroring the existing
dashboard record. (Automatable with the Cloudflare API using
`CLOUDFLARE_DNS_API_TOKEN`, or add it by hand in the Cloudflare dashboard.)

## 2. Build on the box

```bash
cd /opt/data/hermes-agent
git fetch --all && git checkout develop && git pull --ff-only
npm ci                         # root install (workspaces include agent-home)
npm run build -w agent-home    # next build -> .next production build
command -v npm                 # note the path; put it in agent-home.service PATH if not /usr/bin
```

## 3. Secrets — write `agent-home/.env.production` (NOT committed, chmod 600)

```dotenv
# 32+ random bytes; rotates all agent-home sessions when changed.
AGENT_HOME_SESSION_SECRET=<openssl rand -hex 32>
# Postgres DSN for the box's self-hosted Supabase. Reuse the SAME role the
# Python backend uses so FORCE'd RLS applies identically (a BYPASSRLS/superuser
# role would defeat C2). Get it from the box's Python config/.env.
DATABASE_URL=postgresql://<user>:<pass>@127.0.0.1:5432/<db>
# Self-hosted Supabase gateway + anon key (browser-safe; RLS-scoped). Storage
# and future browser-direct Realtime use these; never the service-role key.
SUPABASE_URL=https://home.leolau.ai-and-i.io   # or the box's Supabase URL
SUPABASE_ANON_KEY=<anon key>
# Server-side proxy target for the Python AI layer and datastore schema.
AGENT_HOME_API_URL=http://127.0.0.1:9119
AGENT_HOME_DATASTORE_MODE=dev                  # app_dev; set prod for app_prod
```

```bash
chmod 600 /opt/data/hermes-agent/agent-home/.env.production
chown ubuntu:ubuntu /opt/data/hermes-agent/agent-home/.env.production
```

## 4. Service — run Next on loopback via systemd

```bash
chmod +x /opt/data/hermes-agent/agent-home/deploy/start.sh
sudo cp /opt/data/hermes-agent/agent-home/deploy/agent-home.service /etc/systemd/system/
# If `command -v npm` above was not /usr/bin, edit the unit's Environment=PATH.
sudo systemctl daemon-reload
sudo systemctl enable --now agent-home.service
systemctl status agent-home.service --no-pager
curl -sS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:3000/login   # expect 200
```

## 5. Caddy — publish the subdomain

Append `deploy/Caddyfile.agent-home` (edit the hostname) to `/etc/caddy/Caddyfile`, then:

```bash
sudo caddy validate --config /etc/caddy/Caddyfile
sudo systemctl reload caddy
```

## 6. Verify

```bash
curl -sSI https://home.leolau.ai-and-i.io/login | head        # 200, valid TLS
curl -sSI https://home.leolau.ai-and-i.io/manifest.webmanifest # 200, correct type
curl -sSI https://home.leolau.ai-and-i.io/sw.js                # 200
```

Then on a phone over HTTPS: **iOS Safari** → Share → Add to Home Screen;
**Android Chrome** → Install app. Confirm standalone launch + offline shell
(airplane mode → navigate → `offline.html`).

## Rollback

```bash
sudo systemctl disable --now agent-home.service     # stop serving Next
# remove the agent-home site block from /etc/caddy/Caddyfile, then:
sudo systemctl reload caddy
# optionally delete the Cloudflare A record for the subdomain
```

`web/`, the Python service, and the dashboard subdomain are untouched throughout;
removing the agent-home unit + Caddy block fully reverts this deploy.
