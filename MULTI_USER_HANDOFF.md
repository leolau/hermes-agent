# Multi-User / Member Enablement — Handoff

Handoff for another agent (or a returning human) picking up the multi-user work
on the shared Hermes brain. Captures the agreed plan, what has shipped, what
remains, and the exact production-rollout steps that are deliberately **not**
done yet.

- **Repo:** `leolau/ai-prentice-4-all`
- **Base branch:** `develop`
- **Owner deployment (production):** host `hermes-systest`
  - agent-home: `https://home.leolau.ai-and-i.io` (Next.js, local `127.0.0.1:3100`)
  - dashboard/operator console: `https://leolau.ai-and-i.io` (Python API `127.0.0.1:9119`)
  - self-hosted Supabase stack on-box (GoTrue `supabase-auth`, `supabase-storage`,
    `supabase-rest`, `supabase-kong`, `supabase-db`) — all healthy.
- **Full original plan:** the PR-by-PR plan, decisions, and risk notes live below
  and in each merged PR description.

---

## The five requests (a–e)

From the owner:

> a) Each additional member has access right, could start with least-privilege
>    non-BYPASSRLS DB role, but can be configured by the owner;
> b) wire dashboard/agent-home login to resolve the logged-in user to their own
>    principal instead of always the owner;
> c) Configure Supabase to do password (encrypted) as login provider;
> d) switch the media bucket to private/signed URLs so that each user/member has
>    their own sub-buckets;
> e) an UI in the agent-home to create and manage users/members;

## Decisions locked in (do not re-litigate)

1. **Owner identity:** alias-map the owner's Supabase `sub` (UUID) → existing
   `leo_owner` principal. **No re-keying** of historical rows.
2. **Who manages members:** **owner + admin** (roles ordered `owner > admin >
   member > viewer`). The member-management path never grants/mutates `owner`;
   ownership only moves via `hermes owner transfer`.
3. **Onboarding:** owner sets a **temporary password** and hands it over. No SMTP
   / invite emails.
4. **Rollout:** the DB-role switch (PR-1's serving role) is done in a
   **maintenance window**, verifying background services before/after.
5. **Packaging:** **5-PR split**, reviewed/merged/deployed independently.

---

## Status at a glance

| PR | Scope | Status |
|----|-------|--------|
| **PR-1** #55 | Security foundation (a+b): non-BYPASSRLS `hermes_app` role, admin-DSN split, `bind_principal` per request, `_comms_resolve_principal` identity binding, owner alias map, ensure-RLS, real-path DB RLS tests | **merged** |
| **PR-2** #56 | Supabase (GoTrue) email/password dashboard-auth provider (c): password grant, local JWT verify, `sub`→principal, closed signup | **merged** |
| **PR-3** #57 | Member-management backend (e-backend): `/api/comms/members` API + `hermes member` CLI, GoTrue admin create → enroll principal, owner/admin guard | **merged** |
| **PR-4** #58 | agent-home Members management UI (e-frontend): owner/admin screen + BFF routes | **open → `develop`** (this branch) |
| **PR-5** | Private media bucket + signed URLs (d): private bucket, on-demand signing with ownership/grant check, migrate render path | **not started** |

Branch for PR-4: `devin/1784799589-multiuser-pr4-members-ui`.

---

## What already existed (build on it, don't rebuild)

- `DashboardAuthProvider` ABC (`hermes_cli/dashboard_auth/base.py`) — full OAuth +
  password framework. PR-2 added a Supabase provider plugin, not a new stack.
- Auth middleware attaches the verified `Session` (with `user_id` = provider
  subject) to `request.state.session` on every gated request.
- RLS engine (`hermes_cli/access.py`): `apply_scope_rls` / `apply_item_grants_rls`
  / `scope_filter` + `bind_principal()` set `hermes.principal_id` /
  `hermes.principal_role` GUCs. `PrincipalStore.enroll(user_id, role=…)` +
  `link_channel(...)`, roles `owner|admin|member|viewer`.
- agent-home login bridge (`agent-home/src/app/api/session/login`,
  `src/lib/auth/principal.ts`): `POST /auth/password-login` → capture token →
  `/api/comms/whoami` → signed cookie with the principal snapshot.
- The two things that made RLS *inert* before PR-1 (both fixed there): the app
  connected as a `BYPASSRLS` role (`postgres`), and `bind_principal()` was never
  called on a live request connection.

---

## PR-4 (this branch) — what was built

Owner/admin **Members** screen in agent-home + same-origin BFF routes. The
browser only ever calls same-origin `/api/comms/members[...]`; those Next.js
route handlers forward to the merged Python `/api/comms/members` API under the
bridged session. The service-role key never reaches the browser.

**Files added / changed (all under `agent-home/`):**

- `src/types/index.ts` — `Member`, `MembersResponse`, `MemberCreateResponse`,
  `MemberRoleResponse`, `MemberOkResponse` (mirror `members.MemberView.as_dict`
  and the PR-3 route envelopes).
- `src/lib/api/client.ts` — typed methods: `members()`, `createMember()`,
  `setMemberRole()`, `setMemberPassword()`, `deactivateMember()`,
  `activateMember()`.
- `src/lib/api/member-bff.ts` — shared `requireMemberAdmin()` (401 unauth / 403
  non-admin → owner/admin client) + `forwardMemberError()` (maps
  `HermesApiError` status, else 502).
- `src/app/api/comms/members/route.ts` — `GET` (list) + `POST` (create).
- `src/app/api/comms/members/[userId]/role/route.ts` — `PUT` role.
- `src/app/api/comms/members/[userId]/password/route.ts` — `POST` reset password.
- `src/app/api/comms/members/[userId]/deactivate/route.ts` — `POST` deactivate.
- `src/app/api/comms/members/[userId]/activate/route.ts` — `POST` reactivate.
- `src/components/members/MembersView.tsx` — interactive client screen: add-member
  form (email, display, role select `admin|member|viewer`, temp-password with a
  Generate button), member list with per-row role select, reset-password,
  deactivate/reactivate. Owner row is **read-only** (note points to
  `hermes owner transfer`). Temp passwords are surfaced exactly once from what the
  browser just set — never fetched back or persisted.
- `src/app/members/page.tsx` — server component; `requirePrincipal()` + hard
  owner/admin gate (member/viewer → not-authorized card), loads the roster into
  `MembersView` inside the existing responsive `MobileShell`.
- `src/app/page.tsx` — role-gated **Members** link on Home (owner/admin only).
- Tests: `src/lib/api/client.members.test.ts` (BFF forwarding: paths, methods,
  bodies, token replay) and `src/components/members/MembersView.test.tsx` (owner
  read-only, member controls, deactivated state, empty/not-configured).

**Authorization is enforced twice:** the BFF rejects non-admins early for clean
UX; the Python layer enforces owner/admin independently as the real boundary
(a forged/direct request still gets 403 upstream). The BFF gate is a UX
convenience, **not** the security boundary.

**data-component:** every new/modified component root carries a
`data-component="…"` attribute (per repo knowledge note).

### Backend response shapes PR-4 depends on (from PR-3, merged)

- `GET  /api/comms/members` → `{ configured, members: [{ user_id, display, role, email, active, channels, is_owner }] }`
- `POST /api/comms/members` → `{ ok, member: { user_id, display, role } }`
- `PUT  /api/comms/members/{id}/role` → `{ ok, member: { user_id, role } }`
- `POST /api/comms/members/{id}/password` → `{ ok }`
- `POST /api/comms/members/{id}/deactivate|activate` → `{ ok, active }`

`MemberView.as_dict` lives in `hermes_cli/members.py`; the routes in
`hermes_cli/web_server.py` (`@app.get/post/put("/api/comms/members…")`, ~line
3221+). `ASSIGNABLE_ROLES = ("admin", "member", "viewer")` — never `owner`.

---

## PR-5 — remaining work (NOT started)

Item (d). Do **not** begin without owner go-ahead. Plan:

1. Flip the `agent-home-media` bucket from **public → private**.
2. Replace `getPublicUrl` (`agent-home/src/lib/supabase/storage.ts`) with
   `createSignedUrl(path, ttl)` (short TTL, ~5 min).
3. Add a BFF **read/sign route** that resolves the requesting principal and
   **verifies it owns the `<user_id>/` prefix** (or holds a grant) *before*
   signing. The ownership check is the real isolation — signing without it lets
   any member sign any path.
4. "Per-member sub-buckets" = the existing `<user_id>/<session>/<uuid>-<name>`
   prefix, optionally hardened with Storage RLS on `storage.objects` keyed to the
   authenticated user.
5. Migrate the render path: history persists the object `path` on
   `ChatAttachment`, so re-sign by `path` — no object move needed; replace any
   hard-coded public URLs in history with the signing route.

---

## Production rollout — deliberately deferred (needs a maintenance window)

None of the merged PRs changed production. Members cannot log in end-to-end
until these run on the box (owner-approved):

1. Determine the owner's real Supabase subject UUID and alias it:
   `hermes owner alias <subject-uuid>` (maps `sub` → `leo_owner`).
2. Configure the Supabase auth provider server-side: set `dashboard.supabase_auth`
   (`url` / `anon_key` / `jwt_secret`) — secrets in the env file, not `config.yaml`.
3. Close signup at GoTrue: `GOTRUE_DISABLE_SIGNUP=true`.
4. Maintenance-window DB-role switch: repoint the serving DSN from `postgres`
   (BYPASSRLS) → `hermes_app` (NOBYPASSRLS). **Audit every `DATABASE_URL`
   consumer first** — any request path that misses `bind_principal` sees zero
   rows (fails safe but breaks features). Background/system paths that need broad
   reads must run as the **owner principal** (RLS `owner` branch = full
   visibility) or use the admin DSN deliberately. The messaging pipeline is
   SQLite (unaffected) but confirm service-by-service.
5. Verify dashboard, agent-home login, gateway/background services, and RLS
   (owner sees all; member sees only shared + own; cross-member read empty).
6. Create members through the new UI (PR-4) or `hermes member` CLI.
7. Do PR-5 (private media + signed URLs) before/with onboarding real members.

Env var names for the service-role key (env-only, never `config.yaml`, never
browser), in precedence order: `HERMES_DASHBOARD_SUPABASE_SERVICE_ROLE_KEY`,
`HERMES_SUPABASE_SERVICE_ROLE_KEY`, `SUPABASE_SERVICE_ROLE_KEY`,
`SUPABASE_SERVICE_KEY`.

---

## How to run / verify PR-4 locally

```bash
cd agent-home
npm run typecheck   # tsc --noEmit
npm run lint        # eslint (1 pre-existing warning in eslint.config.mjs, 0 errors)
npm run test        # vitest — 86 passing (incl. the new member tests)
npm run build       # next build — all routes incl. /members + the 5 BFF routes
```

Manual: as **owner/admin** open `/members` (or the Home Members link) → add a
member (blank password auto-generates one, shown once), change a role, reset a
password, deactivate/reactivate; each mutation re-reads the list. As a
**member/viewer**, `/members` shows the not-authorized card, the Home link is
hidden, and the API returns 403.

Python side (for PR-3 backend changes): `scripts/run_tests.sh`, plus `ruff` and
`ty` from `/home/ubuntu/.hermes/venvs/hermes-dev/bin/`. Note: this repo has **no
CI checks configured** (0 checks), so verification is local.

---

## Guardrails (from AGENTS.md + session decisions)

- Preserve prompt caching, strict role alternation, byte-stable system prompt.
- Keep the core narrow; extend at CLI/plugin/service/BFF edges.
- Secrets in env only; behavioral config in `config.yaml`. Never log passwords,
  JWT secrets, access/refresh tokens, or the service-role key. Never send the
  service-role key to the browser. Browser-side authz is never authoritative.
- Add `data-component="ComponentName"` to the root element of every new/modified
  React component (exact PascalCase; don't overwrite an existing one).
- Git: base off `develop`; stage files explicitly (never `git add .`); don't
  amend, force-push protected branches, skip hooks, or run destructive resets.
  Note the four untracked `agent-home/*_TEST_PLAN_*.md` files are intentionally
  left uncommitted.
