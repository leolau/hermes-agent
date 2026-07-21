# agent-home — on-box deploy runbook (FG-20, Decision 2)

Deploys `agent-home` on the **existing prod box** behind the **existing Caddy**,
on its own subdomain, same box + same Supabase as the Python AI layer and the
`web/` dashboard. Nothing here changes `web/` or the Python service.

> **Owner-gated.** Prod deploy touches the running box. Only run this with the
> owner's go-ahead. The ECS system-test gate is separate and also owner-gated.

## Live-infra facts (see `docs/design/SESSION-HANDOFF-2026-07-prod-cutover.md`)

- **Prod box:** `hermes-systest`, Alibaba ECS `i-j6c81aisv2dd8mg17yle`, `47.83.199.25`, cn-hongkong.
- **Reach it:** `aliyun` CLI → ECS RunCommand (creds `ALIBABA_CLOUD_ACCESS_KEY_ID/SECRET` in the agent VM env; no SSH key on file). Runs as `root`.
- **agent-home checkout:** `/opt/data/agent-home-app` — a **dedicated fresh clone** of `develop` for this app. (The older `/opt/data/hermes-agent` tree is a copied snapshot, **not** a git repo, so it can't `git pull`; deploy agent-home from its own clone instead.)
- **Port:** agent-home Next runs on `127.0.0.1:3100`. **`:3000` is already taken** by the WhatsApp bridge (`scripts/whatsapp-bridge/bridge.js`) — do not reuse it.
- **Python AI layer / dashboard:** bound `0.0.0.0:9119`, fronted by Caddy at `leolau.ai-and-i.io`.
- **Caddy:** v2.11.4, config `/etc/caddy/Caddyfile`.
- **DNS/TLS:** Cloudflare zone `ai-and-i.io`; A records are **DNS-only (unproxied)**; Let's Encrypt via Caddy HTTP-01. Cloudflare token `CLOUDFLARE_DNS_API_TOKEN` (Zone·DNS·Edit) enables subdomain automation.
- **Supabase:** self-hosted Docker stack on the box (Postgres + Storage + RLS). The Python `datastore.supabase_app.dsn` resolves to env `DATABASE_URL` (role `postgres`). **Caveat:** that role has `BYPASSRLS`, so per-principal RLS is *not* enforced for it — fine while the owner is the only login (owner sees all rows), but provision a dedicated non-BYPASSRLS role before onboarding other users. agent-home is deployed with `AGENT_HOME_DATASTORE_MODE=prod` (`app_prod`).

## 1. DNS — point a subdomain at the box

Pick a subdomain, e.g. `home.leolau.ai-and-i.io`. Add a Cloudflare **A record**
→ `47.83.199.25`, **DNS-only (grey cloud)** — mirroring the existing dashboard
record. Automatable with the Cloudflare API using `CLOUDFLARE_DNS_API_TOKEN`:

```bash
ZONE=$(curl -sS -H "Authorization: Bearer $CLOUDFLARE_DNS_API_TOKEN" \
  "https://api.cloudflare.com/client/v4/zones?name=ai-and-i.io" \
  | python3 -c "import sys,json;print(json.load(sys.stdin)['result'][0]['id'])")
curl -sS -X POST -H "Authorization: Bearer $CLOUDFLARE_DNS_API_TOKEN" \
  -H "Content-Type: application/json" \
  "https://api.cloudflare.com/client/v4/zones/$ZONE/dns_records" \
  --data '{"type":"A","name":"home.leolau.ai-and-i.io","content":"47.83.199.25","ttl":1,"proxied":false}'
```

## 2. Build on the box

Deploy from a dedicated clone (the `/opt/data/hermes-agent` tree is not a git
repo). First run clones; later runs fast-forward:

```bash
APP_DIR=/opt/data/agent-home-app
if [ ! -d "$APP_DIR/.git" ]; then
  git clone --branch develop https://github.com/leolau/ai-prentice-4-all "$APP_DIR"
else
  cd "$APP_DIR" && git fetch origin develop && git checkout develop && git reset --hard origin/develop
fi
cd "$APP_DIR"
npm ci                         # root install (workspaces include agent-home)
npm run build -w agent-home    # next build -> .next production build
command -v npm                 # note the path; put it in agent-home.service PATH if not /usr/bin
```

## 3. Secrets — write the EnvironmentFile (NOT committed, chmod 600)

Use the filename `agent-home/agent-home.env` — **not** `.env.production`. Next's
built-in dotenv auto-loads `.env*` files and runs dotenv-expand on them; a DSN
resolved from `${DATABASE_URL}` self-references and blows the stack ("Maximum
call stack size exceeded"). A non-`.env` filename is read only by systemd and
left untouched by Next, which reads the values straight from `process.env`.

```dotenv
# 32+ random bytes; rotates all agent-home sessions when changed.
AGENT_HOME_SESSION_SECRET=<openssl rand -hex 32>
# Postgres DSN for the box's self-hosted Supabase — the SAME connection string
# the Python backend uses (its config's datastore.supabase_app.dsn resolves to
# env DATABASE_URL). Read the literal value from the running gateway's env:
#   tr '\0' '\n' < /proc/$(pgrep -f 'hermes.*gateway'|head -1)/environ | sed -n 's/^DATABASE_URL=//p'
# NOTE: on this box that role is `postgres`, which is BYPASSRLS (see facts above).
DATABASE_URL=postgresql://<user>:<pass>@<host>:5432/<db>
# Self-hosted Supabase gateway + anon key (browser-safe; RLS-scoped). Only used
# by the (disabled) Realtime stub in Wave A; never the service-role key.
SUPABASE_URL=http://127.0.0.1:8000
SUPABASE_ANON_KEY=<anon key>
# Server-side proxy target for the Python AI layer.
AGENT_HOME_API_URL=http://127.0.0.1:9119
# C3 datastore schema and the loopback port Caddy proxies to.
AGENT_HOME_DATASTORE_MODE=prod                 # app_prod (app_dev if 'dev')
PORT=3100                                      # :3000 is the WhatsApp bridge
```

```bash
chmod 600 /opt/data/agent-home-app/agent-home/agent-home.env
```

## 4. Service — run Next on loopback via systemd

```bash
chmod +x /opt/data/agent-home-app/agent-home/deploy/start.sh
sudo cp /opt/data/agent-home-app/agent-home/deploy/agent-home.service /etc/systemd/system/
# If `command -v npm` above was not /usr/bin, edit the unit's Environment=PATH.
sudo systemctl daemon-reload
sudo systemctl enable --now agent-home.service
systemctl status agent-home.service --no-pager
curl -sS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:3100/login   # expect 200
```

## 5. Caddy — publish the subdomain

Append `deploy/Caddyfile.agent-home` (edit the hostname; it proxies to `:3100`)
to `/etc/caddy/Caddyfile`, then:

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

## Login prerequisite — the Python dashboard needs `DATABASE_URL`

The bridge login (`/api/session/login`) forwards to the Python dashboard's
`/auth/password-login`, then resolves the C1 principal via
`/api/comms/whoami`. That whoami reads the owner principal from the Supabase
app store, so **the dashboard process must be started with `DATABASE_URL` in
its environment** (same DSN as the gateway — the gateway sources
`/opt/data/hermes-staging.env`). If the dashboard is launched without it,
password auth succeeds but whoami 500s (`invalid DSN: scheme … got ''`) and
login fails with no visible reason. Verify:

```bash
DPID=$(ss -ltnp | grep ':9119' | grep -oE 'pid=[0-9]+' | head -1 | cut -d= -f2)
tr '\0' '\n' < /proc/$DPID/environ | grep -q '^DATABASE_URL=' && echo ok || echo "MISSING DATABASE_URL"
```

Also note the datastore mode must match where the owner principal is enrolled
(here `prod`/`app_prod` — `get_owner()` returns the owner in `app_prod`, and
`None` in `app_dev`, which would surface as `no_principal`/409).

## Rollback

```bash
sudo systemctl disable --now agent-home.service     # stop serving Next
# remove the agent-home site block from /etc/caddy/Caddyfile, then:
sudo systemctl reload caddy
# optionally delete the Cloudflare A record for the subdomain
```

`web/`, the Python service, and the dashboard subdomain are untouched throughout;
removing the agent-home unit + Caddy block fully reverts this deploy.
