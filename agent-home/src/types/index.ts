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

/** A GTS goal node (mirror of `gts.GtsGoal.as_dict`). */
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
}

/** A GTS task node (mirror of `gts.GtsTask.as_dict`). */
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
}

/** Either kind of GTS graph node. */
export type GtsNode =
  | ({ kind: "goal" } & GtsGoal)
  | ({ kind: "task" } & GtsTask);

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
