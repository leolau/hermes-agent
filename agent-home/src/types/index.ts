/**
 * Shared TypeScript types for the `agent-home` seam (FG-20 Wave A2).
 *
 * These are the minimal, extendable shapes the Wave-B/C feature panels
 * consume. They intentionally mirror the Python-side records
 * (`hermes_cli/access.py`, `gts.py`, `interactions.py`) so the BFF and the
 * feature panels speak the same vocabulary as the AI layer + Supabase.
 *
 * They are deliberately *minimal*: only the fields Wave A needs to prove the
 * seam plus the core identifiers later waves will extend. Add fields as each
 * feature panel lands rather than front-loading a speculative surface.
 */

/** The four C1 roles (mirror of `access.Role`). */
export type Role = "owner" | "admin" | "member" | "viewer";

/**
 * The resolved C1 principal a request acts under (mirror of
 * `access.Principal`). This is what the auth bridge establishes and what the
 * server-side Supabase context binds into the `hermes.principal_*` GUCs.
 */
export interface Principal {
  user_id: string;
  display: string;
  role: Role;
  channels: string[];
  is_owner: boolean;
}

/** C3 datastore mode — selects the `app_dev` / `app_prod` schema. */
export type StoreMode = "dev" | "prod";

/** Visibility tag on every scoped row: `shared` or `private:<user_id>`. */
export type Visibility = "shared" | `private:${string}`;

/**
 * How a node's progress is observed/scored (mirror of
 * `gts` evaluation-method dict). The score itself is always engine-computed;
 * this is the user-owned observe/measure definition, never the score.
 */
export interface GtsObservation {
  source: string;
  prompt: string;
  ref?: Record<string, unknown>;
}

export interface GtsEvaluationMethod {
  set_by_user_id: string | null;
  locked: boolean;
  measurable: boolean;
  observation: GtsObservation | null;
  scoring_prompt: string;
}

/**
 * A FG-19 per-item grant attached to a node: the single `assignee` plus any
 * read-only `watcher`s. A grant only confers access while `pending`/`accepted`.
 */
export interface GtsItemGrant {
  id: string;
  item_kind: string;
  item_id: string;
  user_id: string;
  grant: "assignee" | "watcher" | string;
  granted_by: string;
  status: string;
}

/** A GTS goal node (mirror of `gts.GtsGoal.as_dict` + graph enrichment). */
export interface GtsGoal {
  id: string;
  owner_user_id: string;
  visibility: string;
  title: string;
  priority: string;
  status: string;
  level: string;
  parent_goal_id: string | null;
  score: number | null;
  assignee_user_id: string | null;
  evaluation_method: GtsEvaluationMethod;
  grants: GtsItemGrant[];
}

/** A GTS task node (mirror of `gts.GtsTask.as_dict` + graph enrichment). */
export interface GtsTask {
  id: string;
  owner_user_id: string;
  visibility: string;
  title: string;
  priority: string;
  status: string;
  current_state: string;
  parent_task_id: string | null;
  score: number | null;
  assignee_user_id: string | null;
  evaluation_method: GtsEvaluationMethod;
  grants: GtsItemGrant[];
}

/** A GTS skill node (mirror of `gts` skill dict). */
export interface GtsSkill {
  id: string;
  owner_user_id: string;
  visibility: string;
  name: string;
  skill_ref: string;
}

/** Either kind of GTS graph node. */
export type GtsNode =
  | ({ kind: "goal" } & GtsGoal)
  | ({ kind: "task" } & GtsTask);

/**
 * The full C2-scoped GTS graph the Python API returns from `/api/gts/graph`:
 * goal→task→skill hierarchy with the M:N edges, engine-computed scores, and
 * FG-19 assignment. `configured: false` when the app datastore is unset.
 */
export interface GtsGraphResponse {
  configured: boolean;
  principal?: string | null;
  mode?: string;
  goals: GtsGoal[];
  tasks: GtsTask[];
  skills: GtsSkill[];
  task_goals: { task_id: string; goal_id: string }[];
  task_skills: { task_id: string; skill_id: string }[];
  assignment: { enabled: boolean; scheme: string };
}

/** The C8 interaction/trace kinds (mirror of `interactions.InteractionKind`). */
export type InteractionKind =
  | "inbound"
  | "turn"
  | "tool_call"
  | "tool_result"
  | "outbound"
  | "approval"
  | "change"
  | "cost"
  | "error"
  | "core_denied";

/** A single C8 interaction-trace row (mirror of `interactions.Interaction`). */
export interface TraceRow {
  id: string;
  trace_id: string;
  parent_id: string | null;
  ts: string;
  actor_user_id: string;
  session_key: string;
  platform: string;
  kind: InteractionKind;
  ref: string;
  summary: string;
  payload_ref: string | null;
  mode: string;
}

/**
 * A C8 trace summary row (mirror of `interactions.TraceSummary.as_dict`): one
 * conversation/trace rolled up to its span, event count, and per-kind counts.
 */
export interface TraceSummary {
  trace_id: string;
  first_ts: string;
  last_ts: string;
  actor_user_id: string | null;
  session_key: string | null;
  platform: string | null;
  mode: string;
  event_count: number;
  kind_counts: Record<string, number>;
  rolled_up: boolean;
}

/** The C2-scoped list of C8 traces from `/api/comms/traces`. */
export interface TracesResponse {
  configured: boolean;
  principal?: string | null;
  traces: TraceSummary[];
}

/**
 * One trace's timeline projection from `/api/comms/traces/{id}`: the ordered
 * interaction events plus the rolled-up summary (null while still live).
 */
export interface TraceDetailResponse {
  configured: boolean;
  principal?: string | null;
  trace_id: string;
  interactions: TraceRow[];
  rollup: TraceSummary | null;
}

/**
 * A FG-12 change-log row (mirror of the `/api/comms/changes` payload): an
 * agent/human mutation the principal may review, and whether it's reversible.
 */
export interface Change {
  id: string;
  actor_user_id: string | null;
  mode: string;
  target_kind: string;
  reversible: boolean;
  visibility: string;
  undone: boolean;
}

/** The C2-scoped FG-12 change log from `/api/comms/changes`. */
export interface ChangesResponse {
  configured: boolean;
  principal?: string | null;
  changes: Change[];
}

/**
 * A durable Core-write denial (mirror of a `core_audit_log` line): an agent
 * write refused at the C7 boundary. Surfaced by the Core-area view.
 */
export interface CoreDenial {
  id: string;
  ts: number;
  actor_user_id: string;
  mode: string;
  summary: string;
  op?: { kind?: string; op?: string; path?: string; matched_glob?: string };
}

/**
 * The FG-14 C7 Core-boundary projection from `/api/core/manifest` (read-only):
 * the active `core_manifest.yaml` globs, boundary health (`fallback_active`
 * means it's running on the baked-in fail-closed set), and a tail of the
 * durable Core-denial audit log. Core is immutable to the runtime agent.
 */
export interface CoreManifestResponse {
  core_root: string;
  manifest_path: string;
  manifest_present: boolean;
  manifest_parseable: boolean;
  fallback_active: boolean;
  self_protected: boolean;
  globs: string[];
  glob_count: number;
  audit_log_path: string;
  denials: CoreDenial[];
}

/**
 * A single FG-15 onboarding-readiness check (mirror of the CLI setup schema):
 * whether a required/optional prerequisite is `met`, why it matters, and the
 * `hermes …` fix command. Reports secret *presence* only, never values.
 */
export interface OnboardingItem {
  key: string;
  label: string;
  required: boolean;
  rationale: string;
  fix_command: string;
  contract: string;
  met: boolean;
  detail: string;
}

/**
 * The FG-15 onboarding readiness from `/api/onboarding/readiness`: the overall
 * score + `ready_for_prod` gate the CLI computes, plus the per-item checks.
 */
export interface OnboardingReadinessResponse {
  score: number;
  score_pct: number;
  ready_for_prod: boolean;
  required_total: number;
  required_met: number;
  optional_total: number;
  optional_met: number;
  optional_coverage: number;
  missing_required: string[];
  items: OnboardingItem[];
}

/** A registry tool's provenance kind (mirror of the Python tools registry). */
export type ToolKind = "in_house" | "remote" | "builtin";
/** A registry tool's enable status. */
export type ToolStatus = "enabled" | "disabled";

/**
 * A FG-07 tool-registry entry (mirror of `tools_registry.Tool.as_dict`): a
 * tool the operator may enable/promote. This surface is read-only in
 * `agent-home` — enable/config/promote stay on the operator authority paths.
 */
export interface Tool {
  id: string;
  name: string;
  kind: ToolKind;
  stack: string;
  owner_user_id: string;
  visibility: string;
  mode: StoreMode;
  status: ToolStatus;
  enabled: boolean;
  mcp_endpoint_ref: string | null;
  web_url: string | null;
  config_json: Record<string, unknown>;
}

/** The C2-scoped tool registry from `/api/tools` for a datastore mode. */
export interface ToolsResponse {
  configured: boolean;
  mode: StoreMode;
  tools: Tool[];
  detail?: string;
}

/** A chat message role in a one-brain conversation (mirror of the store). */
export type ChatRole = "user" | "assistant" | "system" | "tool";

/**
 * A single persisted conversation message (mirror of the `messages` row the
 * Python `SessionDB` returns). Only the fields the mobile chat pane renders are
 * typed; `tool` rows are kept out of the visible thread.
 */
export interface ChatMessage {
  id?: number;
  role: ChatRole;
  content: string;
  timestamp?: number | string | null;
}

/**
 * A conversation summary row (mirror of `SessionDB.list_sessions_rich`): the
 * id, its human title/preview, message count, and last-active timestamp used to
 * order the mobile conversation list.
 */
export interface SessionSummary {
  id: string;
  source: string;
  title: string | null;
  preview: string | null;
  message_count: number;
  started_at: number | null;
  last_active: number | null;
  ended_at: number | null;
  is_active?: boolean;
}

/** The list of conversations from `GET /api/sessions`. */
export interface SessionsResponse {
  sessions: SessionSummary[];
  total: number;
  limit: number;
  offset: number;
}

/** One conversation's persisted transcript from `GET /api/sessions/{id}/messages`. */
export interface ChatMessagesResponse {
  session_id: string;
  messages: ChatMessage[];
}

/** Token accounting a one-brain turn reports (mirror of the agent usage dict). */
export interface ChatUsage {
  input_tokens?: number;
  output_tokens?: number;
  total_tokens?: number;
}

/**
 * The reply from `POST /api/sessions/{id}/chat`: the (possibly resumed) session
 * id the turn landed on, the assistant message, and optional usage.
 */
export interface ChatSendResponse {
  session_id: string;
  message: ChatMessage;
  usage?: ChatUsage;
}

/** The result of creating a conversation via `POST /api/sessions`. */
export interface SessionCreateResponse {
  session_id: string;
  source: string;
}

/**
 * A media attachment uploaded server-side to principal-scoped Supabase Storage
 * (browser never holds the storage key). `url` is the reference the composer
 * appends to the outgoing message and the thread renders inline.
 */
export interface ChatAttachment {
  path: string;
  url: string;
  name: string;
  content_type: string;
  size: number;
}

/** A comms/notification item (FG-10). Minimal; extend as Wave C3 lands. */
export interface Notification {
  id: string;
  kind: string;
  summary: string;
  created_at: string;
  answered: boolean;
}

/**
 * FG-17b agent-webview consent grant (mirror of `webview.WebviewScope`): the
 * domains the agent may act on and whether interactive actions are allowed.
 */
export type WebviewMode = "read_only" | "interactive";

export interface WebviewScope {
  allowed_domains: string[];
  mode: WebviewMode;
}

/**
 * An agent action verb requested against the live page (mirror of
 * `webview.ActionKind`). Read-only kinds run autonomously in scope; interactive
 * kinds need an `interactive` grant; `submit`/`download` always escalate.
 */
export type WebviewActionKind =
  | "navigate"
  | "read"
  | "screenshot"
  | "scroll"
  | "click"
  | "type"
  | "select"
  | "submit"
  | "download";

/** The Option-B policy decision for one webview action (mirror of `webview.Decision`). */
export type WebviewDecision = "allow" | "escalate" | "deny";

/**
 * A queued per-action C6 approval (mirror of `webview.PendingApproval.as_dict`):
 * an escalated action awaiting the user's grant/deny.
 */
export interface WebviewPendingApproval {
  id: string;
  kind: WebviewActionKind;
  url: string | null;
  credentialed: boolean;
  destructive: boolean;
  reason: string;
  created_at: number;
  resolved: boolean | null;
}

/**
 * One user's opted-in webview session (mirror of `webview.WebviewSession.as_dict`):
 * the consent scope, the C8 trace id grouping its actions, and any pending
 * approvals. Ephemeral + per-principal (the C2 isolation boundary).
 */
export interface WebviewSession {
  id: string;
  owner_user_id: string;
  scope: WebviewScope;
  profile_dir: string;
  created_at: number;
  trace_id: string;
  pending: WebviewPendingApproval[];
}

/**
 * `GET/POST /api/webview/session`: the caller's open session (or `null` for the
 * default-deny empty state). `configured: false` when the datastore is unset.
 */
export interface WebviewSessionResponse {
  configured: boolean;
  principal?: string | null;
  session: WebviewSession | null;
}

/**
 * The result of requesting/resolving a webview action
 * (`POST /api/webview/action` + `/approval/{id}`): the policy decision, its
 * reason, the CDP execution detail (when it ran), and the escalated approval
 * (when it queued).
 */
export interface WebviewActionResponse {
  decision: WebviewDecision;
  reason: string;
  executed?: boolean;
  detail?: string;
  granted?: boolean;
  approval?: WebviewPendingApproval;
}
