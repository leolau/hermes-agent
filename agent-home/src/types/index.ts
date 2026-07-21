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

/** A tool-registry listing entry (FG-07). Minimal; extend as Wave B3 lands. */
export interface Tool {
  ref: string;
  name: string;
  enabled: boolean;
  mode: StoreMode;
}

/** A comms/notification item (FG-10). Minimal; extend as Wave C3 lands. */
export interface Notification {
  id: string;
  kind: string;
  summary: string;
  created_at: string;
  answered: boolean;
}
