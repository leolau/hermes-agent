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
