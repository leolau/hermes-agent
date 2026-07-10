# Hermes Agent — Session Hand-off

_Last updated: 2026-07-05 by Devin (session: agent switch requested by Leo)._

This note captures the current state so the next agent can pick up without
re-discovering context. It documents **deployment status** and **repo state**
only — no code changes were made this session.

## Repo state

- Branch: `main` @ `10499e75a` — _fix(whatsapp): align batcher field extraction with bridge normalized format_.
- Working tree clean at start of session; this hand-off note is the only addition.
- Recent history of note:
  - `10499e75a` fix(whatsapp): align batcher field extraction with bridge normalized format
  - `a1ccd112f` feat(calendar): add Google Calendar integration — Phases 1-2 (PR #4)
  - `949cb5a8f` fix(contacts): switch callback handler from getUpdates polling to HTTP server

## Live deployment status (verified this session)

The production hermes-agent is **running and healthy** on Alibaba Cloud ECS.

- **Host:** ECS instance `ai-prentice-agentdoc` (`i-j6camnt3ocwlmzajthil`)
  - Public IP `8.217.86.90`, private IP `172.29.18.230`
  - Region `cn-hongkong` (zone `cn-hongkong-b`), `ecs.e-c1m2.large` (2 vCPU / 4 GB), Ubuntu 24.04
  - Instance description: "Hosts ai-prentice (OpenClaw) and AgentDoc MCP server"
- **Container:** `hermes-agent` — Up ~4 days, image `hermes-agent:local` (run via docker; s6-supervise manages `main-hermes`).

Processes observed inside the container:

| Process | Notes |
|---|---|
| `hermes gateway run --replace` | Main gateway daemon (up since Jul 02) |
| `hermes dashboard --host 0.0.0.0 --port 9119 --no-open` | Web dashboard (up since Jun 30) |
| WhatsApp bridge (phone1) | `node bridge.js --port 3000`, session `session-phone1` (up since Jul 01) |
| WhatsApp bridge (phone2) | `node bridge.js --port 3001`, session `session-phone2` (up since Jul 01) |
| `ui-tui/dist/entry.js` | Ink TUI (node `--expose-gc`) |
| 2× `tui_gateway.slash_worker` | model `deepseek-reasoner` |

Deploy layout on host: code under `/opt/hermes`, venv at `/opt/hermes/.venv`,
data under `/opt/data` (WhatsApp sessions in `/opt/data/platforms/whatsapp/`,
bridge logs in `/opt/data/whatsapp-messages/`).

### How this was verified

Via the Alibaba Cloud MCP integration (`alibaba-cloud` server):
`ECS_DescribeInstances` (region `cn-hongkong`) to locate the host, then
`OOS_RunCommand` (`docker ps` + `ps aux`) to inspect running processes. No SSH
key needed — OOS Cloud Assistant runs commands on the instance directly.

## Environment / access notes

- Alibaba Cloud MCP (`alibaba-cloud`) is working (ECS, OOS, VPC, RDS, CMS, account APIs).
  - The second endpoint `openapi-mcp-core` **timed out** on tool listing — needs config/network fix.
  - OSS returns `UserDisable` (403) — OSS service not activated on the account.
  - CloudMonitor (CMS) CPU/mem/disk queries returned empty — CloudMonitor agent not installed on the ECS instances.
- Only ECS instances on the account are in `cn-hongkong` (this host + `launch-advisor-20260401` @ `47.238.99.110`); `cn-hangzhou`, `cn-shanghai`, `ap-southeast-1` have none.

## Suggested next steps

- No pending code changes from this session. Pick up feature/bug work from `main`.
- If you need to inspect or restart the live agent, use `alibaba-cloud` MCP → `OOS_RunCommand` against `i-j6camnt3ocwlmzajthil` (cn-hongkong) rather than direct SSH.
