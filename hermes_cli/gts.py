"""Contract C9 — the unified GTS graph (Goals → Tasks → Skills), FG-18.

The **GTS Centre** is a Core tool (D14 / C7): its implementation and governing
rules are immutable to the runtime agent and to end users — only human
developers change it through the repo/PR flow. Users and the agent manage GTS
*data* within the Centre's authority rules.

This module **extends** the existing stores rather than introducing a parallel
one (the extend-don't-duplicate rule):

* Goals live in the FG-04 ``goals`` table (:mod:`hermes_cli.goal_registry`);
  this module adds ``parent_goal_id`` / ``level`` / ``score`` /
  ``evaluation_method_ref`` to it.
* Tasks live in the FG-06 ``tasks`` table (:mod:`hermes_cli.task_registry`);
  this module adds ``parent_task_id`` / ``priority`` / ``score`` /
  ``evaluation_method_ref`` to it.
* Skills are **referenced** (not copied) by a lightweight ``skills_registry``
  node pointing at existing skill content (``skill_ref``).
* Typed edges: ``task_goals`` (M:N task↔goal) and ``task_skills`` (M:N
  task↔skill); goal/task self-hierarchy via ``parent_*_id`` (cycle-safe).
* ``evaluation_methods`` — user-owned, agent-immutable rubric rows that define
  *how* a goal/task is scored. Scores are **always computed** from live
  metrics / task state, clamped to ``0–100``, and rolled up to parents by
  priority weight. A score is **never** hand-set.

Contracts consumed (never re-implemented):

* **C3** — every connection is obtained through the injected
  :class:`~hermes_cli.datastore.SupabaseAppStore` (``app_dev`` / ``app_prod``).
* **C2** — ``goals`` / ``tasks`` / ``skills_registry`` rows carry
  ``owner_user_id`` + ``visibility`` and are read through
  :func:`hermes_cli.access.scope_filter` with Postgres RLS as the DB backstop;
  edge/method rows are reached only through a scoped join to a readable node.
* **C5 / C8** — an agent attempt to create/manage a **top-level goal** or to
  set/change an **evaluation method** is refused and audited (a durable local
  audit row + an optional injected C5 recorder + a C8 ``core_denied`` trace).
* Prompt cache is sacred — GTS state is surfaced to the agent only through tool
  results / appended messages (:func:`render_gts_block`); nothing here mutates
  the byte-stable system prompt.

The metric maths reuses :class:`hermes_cli.goals.GoalMetric` /
:func:`hermes_cli.goals.verdict_for_metrics`, so achievement stays *computed*.
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import (
    TYPE_CHECKING,
    Callable,
    Dict,
    List,
    Literal,
    Mapping,
    Optional,
    Sequence,
    Tuple,
)

from hermes_cli.access import (
    GRANT_ACTIVE_STATUSES,
    GRANT_ITEM_KINDS,
    GRANT_TYPES,
    ITEM_GRANTS_SCHEMA_SQL,
    ITEM_GRANTS_TABLE,
    Principal,
    apply_item_grants_rls,
    apply_scope_rls,
    normalize_visibility,
    scope_filter,
)
from hermes_cli.consent import (
    ApprovalCallback,
    ConsentPolicy,
    evaluate_approval,
)
from hermes_cli.goal_registry import GOALS_TABLE, GoalRegistryStore
from hermes_cli.goals import (
    DEFAULT_GOAL_PRIORITY,
    GoalMetric,
    normalize_priority,
    priority_rank,
    priority_weight,
    verdict_for_metrics,
)
from hermes_cli.task_registry import (
    TASKS_TABLE,
    TaskRegistryStore,
    TaskSpec,
    _status_for_state,
    validate_progress_transition,
)

if TYPE_CHECKING:
    import asyncpg

    from hermes_cli.datastore import SupabaseAppStore

logger = logging.getLogger(__name__)

# --- vocabulary -------------------------------------------------------------

#: Who is performing a GTS mutation. ``"user"`` is the human owner path (the
#: authoritative actor); ``"agent"`` is the runtime LLM agent, which is refused
#: on top-level goals and evaluation methods (and audited).
GtsActor = Literal["user", "agent"]

#: A goal's place in the hierarchy. ``top`` goals are user-only; ``sub`` goals
#: hang off a parent goal and may be managed by the agent.
GOAL_LEVELS: Tuple[str, ...] = ("top", "sub")

#: How a goal/task's status is *observed*. Every goal is **observable**; the
#: source names where that observation comes from:
#:   * ``internal`` — Hermes/GTS state itself (metrics, task progress, …).
#:   * ``external`` — a database / API / MCP tool (carry the handle in ``ref``).
#:   * ``ask``      — feedback solicited from the user.
#: Observability is universal; *measurability* (an auto-computed 0–100 score) is
#: the narrower property layered on top (see ``measurable`` on the method).
ObservationSource = Literal["internal", "external", "ask"]
OBSERVATION_SOURCES: Tuple[str, ...] = ("internal", "external", "ask")

#: The M:N + registry + method tables this module owns (reuses ``goals`` /
#: ``tasks`` from FG-04/06 rather than duplicating them).
SKILLS_TABLE = "skills_registry"
TASK_GOALS_TABLE = "task_goals"
TASK_SKILLS_TABLE = "task_skills"
EVALUATION_METHODS_TABLE = "evaluation_methods"

_TARGET_KINDS: Tuple[str, ...] = ("goal", "task")

#: FG-19 per-item grant vocabulary (re-exported from the C2 grant primitive so
#: callers of the GTS Centre have one import site).
GRANT_ASSIGNEE = "assignee"
GRANT_WATCHER = "watcher"
#: Grant lifecycle actions audited through C5/C8.
ASSIGNMENT_ACTIONS: Tuple[str, ...] = (
    "assign",
    "reassign",
    "accept",
    "decline",
    "revoke",
    "progress",
)


class GtsError(RuntimeError):
    """Base class for GTS Centre failures."""


class GtsAuthorityError(GtsError):
    """A GTS mutation was refused by the authority model (agent over-reach)."""


class GtsCycleError(GtsError):
    """A hierarchy edit would create a cycle (a node cannot be its own ancestor)."""


class GtsAssignmentError(GtsError):
    """An assignment violated a per-item grant rule (FG-19).

    Raised for structural violations: assigning a non-assignable top-level
    goal, assigning a second active assignee (use ``reassign``), or acting on
    a grant that does not exist / is not the caller's to act on.
    """


# ---------------------------------------------------------------------------
# Schema (additive + idempotent): extend goals/tasks, add edges + registry.
# ---------------------------------------------------------------------------

_EXTEND_SQL = f"""
ALTER TABLE {GOALS_TABLE}
    ADD COLUMN IF NOT EXISTS parent_goal_id UUID
        REFERENCES {GOALS_TABLE}(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS level TEXT NOT NULL DEFAULT 'top',
    ADD COLUMN IF NOT EXISTS score DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS observed_state JSONB,
    ADD COLUMN IF NOT EXISTS evaluation_method_ref UUID,
    ADD COLUMN IF NOT EXISTS assignee_user_id TEXT;
CREATE INDEX IF NOT EXISTS {GOALS_TABLE}_parent_idx
    ON {GOALS_TABLE} (parent_goal_id);

ALTER TABLE {TASKS_TABLE}
    ADD COLUMN IF NOT EXISTS parent_task_id UUID
        REFERENCES {TASKS_TABLE}(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS priority TEXT NOT NULL DEFAULT '{DEFAULT_GOAL_PRIORITY}',
    ADD COLUMN IF NOT EXISTS score DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS observed_state JSONB,
    ADD COLUMN IF NOT EXISTS evaluation_method_ref UUID,
    ADD COLUMN IF NOT EXISTS assignee_user_id TEXT;
CREATE INDEX IF NOT EXISTS {TASKS_TABLE}_parent_idx
    ON {TASKS_TABLE} (parent_task_id);

CREATE TABLE IF NOT EXISTS {SKILLS_TABLE} (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_user_id TEXT NOT NULL,
    visibility TEXT NOT NULL,
    name TEXT NOT NULL,
    skill_ref TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (owner_user_id, name)
);
CREATE INDEX IF NOT EXISTS {SKILLS_TABLE}_visibility_idx
    ON {SKILLS_TABLE} (visibility);

CREATE TABLE IF NOT EXISTS {TASK_GOALS_TABLE} (
    task_id UUID NOT NULL REFERENCES {TASKS_TABLE}(id) ON DELETE CASCADE,
    goal_id UUID NOT NULL REFERENCES {GOALS_TABLE}(id) ON DELETE CASCADE,
    PRIMARY KEY (task_id, goal_id)
);
CREATE INDEX IF NOT EXISTS {TASK_GOALS_TABLE}_goal_idx
    ON {TASK_GOALS_TABLE} (goal_id);

CREATE TABLE IF NOT EXISTS {TASK_SKILLS_TABLE} (
    task_id UUID NOT NULL REFERENCES {TASKS_TABLE}(id) ON DELETE CASCADE,
    skill_id UUID NOT NULL REFERENCES {SKILLS_TABLE}(id) ON DELETE CASCADE,
    PRIMARY KEY (task_id, skill_id)
);
CREATE INDEX IF NOT EXISTS {TASK_SKILLS_TABLE}_skill_idx
    ON {TASK_SKILLS_TABLE} (skill_id);

CREATE TABLE IF NOT EXISTS {EVALUATION_METHODS_TABLE} (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    target_kind TEXT NOT NULL CHECK (target_kind IN ('goal', 'task')),
    target_id UUID NOT NULL,
    method_json JSONB NOT NULL,
    set_by_user_id TEXT NOT NULL,
    locked BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (target_kind, target_id)
);
"""


# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GtsGoal:
    """A goal node in the unified graph (extends the FG-04 goal row)."""

    id: str
    owner_user_id: str
    visibility: str
    title: str
    priority: str
    status: str
    level: str
    parent_goal_id: Optional[str]
    score: Optional[float]
    evaluation_method_ref: Optional[str]
    #: The single active assignee's user id (FG-19), when this sub-goal has been
    #: assigned; ``None`` when unassigned. Top-level goals are never assignable.
    assignee_user_id: Optional[str] = None

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "owner_user_id": self.owner_user_id,
            "visibility": self.visibility,
            "title": self.title,
            "priority": self.priority,
            "status": self.status,
            "level": self.level,
            "parent_goal_id": self.parent_goal_id,
            "score": self.score,
            "assignee_user_id": self.assignee_user_id,
        }


@dataclass(frozen=True)
class GtsTask:
    """A task node in the unified graph (extends the FG-06 task row)."""

    id: str
    owner_user_id: str
    visibility: str
    title: str
    priority: str
    status: str
    current_state: str
    completion_state: str
    parent_task_id: Optional[str]
    score: Optional[float]
    evaluation_method_ref: Optional[str]
    #: The single active assignee's user id (FG-19), or ``None`` when unassigned.
    assignee_user_id: Optional[str] = None

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "owner_user_id": self.owner_user_id,
            "visibility": self.visibility,
            "title": self.title,
            "priority": self.priority,
            "status": self.status,
            "current_state": self.current_state,
            "parent_task_id": self.parent_task_id,
            "score": self.score,
            "assignee_user_id": self.assignee_user_id,
        }


@dataclass(frozen=True)
class SkillNode:
    """A registry node that *references* existing skill content (never copies it)."""

    id: str
    owner_user_id: str
    visibility: str
    name: str
    skill_ref: str

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "owner_user_id": self.owner_user_id,
            "visibility": self.visibility,
            "name": self.name,
            "skill_ref": self.skill_ref,
        }


@dataclass(frozen=True)
class EvaluationMethod:
    """A user-owned, agent-immutable scoring rubric for a goal or task."""

    id: str
    target_kind: str
    target_id: str
    method: Mapping[str, object]
    set_by_user_id: str
    locked: bool


@dataclass(frozen=True)
class ItemGrant:
    """A per-item cross-user grant (FG-19): the C2 assignment primitive.

    A grant shares one specific GTS item (``item_kind`` + ``item_id``) with
    ``user_id`` as either the single ``assignee`` (may advance progress / add
    sub-tasks) or a read-only ``watcher``, without downgrading the item's
    ``visibility``. ``status`` walks the assignment lifecycle
    (``pending`` → ``accepted`` / ``declined`` / ``revoked``); only
    ``pending``/``accepted`` grants confer access.
    """

    id: str
    item_kind: str
    item_id: str
    user_id: str
    grant: str
    granted_by: str
    status: str

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "item_kind": self.item_kind,
            "item_id": self.item_id,
            "user_id": self.user_id,
            "grant": self.grant,
            "granted_by": self.granted_by,
            "status": self.status,
        }


@dataclass(frozen=True)
class ObservationSpec:
    """How a goal/task's status is observed — the user-authored *observe* half.

    ``source`` is one of :data:`OBSERVATION_SOURCES`; ``prompt`` describes, in
    the user's words, *how* to observe the status; ``ref`` carries an opaque
    handle to the backing db/api/mcp tool for ``external`` sources (never
    executed here — it is stored + validated only, a clean seam for a separate
    execution engine). Every goal is observable, so every user-authored
    evaluation method carries one of these.
    """

    source: str
    prompt: str
    ref: Optional[Mapping[str, object]] = None

    def as_dict(self) -> Dict[str, object]:
        payload: Dict[str, object] = {"source": self.source, "prompt": self.prompt}
        if self.ref is not None:
            payload["ref"] = dict(self.ref)
        return payload


@dataclass(frozen=True)
class ScoringRequest:
    """Input handed to the scoring seam for a **measurable** goal/task.

    Bundles the user-authored observation + scoring prompt with the latest
    ``observed_state`` recorded against the node. A :data:`GtsScoreEvaluator`
    turns this into a raw score (the engine clamps it to ``0–100`` and never
    lets it be hand-set). The evaluator is where a real system would *execute*
    the scoring prompt against the observed state; that execution is out of
    scope here — the default evaluator is deterministic (see
    :func:`default_score_evaluator`).
    """

    target_kind: str
    target_id: str
    observation: ObservationSpec
    scoring_prompt: str
    observed_state: Mapping[str, object]
    mode: str


#: The scoring-prompt *execution* seam. Given a :class:`ScoringRequest`, return
#: a raw score (the engine clamps to ``0–100``), or ``None`` when the node
#: cannot be scored yet (e.g. no observation recorded). Kept as a clean seam so
#: the real executor (LLM / db / api / mcp) can be wired in without touching the
#: Core engine; this task ships only the deterministic default.
GtsScoreEvaluator = Callable[["ScoringRequest"], Optional[float]]


def default_score_evaluator(request: "ScoringRequest") -> Optional[float]:
    """Minimal, deterministic scoring seam used when none is injected.

    A real executor would *run* ``request.scoring_prompt`` against the observed
    state (reading internal GTS state, querying the ``external`` ref, or using
    the user's ``ask`` feedback). We do not execute anything here; instead we
    read the programmatic result a user/agent already recorded via
    :meth:`GtsCentre.record_observation` — the numeric ``score`` key of the
    observed state. Returns ``None`` when absent so the engine leaves the score
    unset until a real observation lands. The engine clamps whatever comes back.
    """
    observed = request.observed_state or {}
    if not isinstance(observed, Mapping):
        return None
    return _as_float(observed.get("score"))


# ---------------------------------------------------------------------------
# Evaluation-method observe/measure model (parse + validate)
# ---------------------------------------------------------------------------


def method_is_measurable(method: Mapping[str, object]) -> bool:
    """Whether a node with this method is *measurable* (has an auto-score).

    Every goal is observable, but only *measurable* ones carry a
    programmatically-computed 0–100 score. When the user has not stated the
    ``measurable`` flag we default to ``True`` for backward compatibility: a
    legacy method (metric ``weights`` / task ``state_scores``, or none at all)
    keeps its computed-metrics score. A node is non-measurable only when the
    user explicitly sets ``measurable: false`` — it then has an observation +
    qualitative status but no auto-score.
    """
    flag = method.get("measurable")
    if isinstance(flag, bool):
        return flag
    return True


def parse_observation(method: Mapping[str, object]) -> Optional[ObservationSpec]:
    """Return the typed :class:`ObservationSpec` in ``method``, if present."""
    raw = method.get("observation")
    if not isinstance(raw, Mapping):
        return None
    data = {str(key): value for key, value in raw.items()}
    source = data.get("source")
    prompt = data.get("prompt")
    ref = data.get("ref")
    return ObservationSpec(
        source=str(source) if source is not None else "",
        prompt=str(prompt) if prompt is not None else "",
        ref={str(k): v for k, v in ref.items()} if isinstance(ref, Mapping) else None,
    )


def method_scoring_prompt(method: Mapping[str, object]) -> str:
    """Return the user's scoring prompt from ``method`` (``""`` when absent)."""
    scoring = method.get("scoring")
    if isinstance(scoring, Mapping):
        data = {str(key): value for key, value in scoring.items()}
        prompt = data.get("prompt")
        return str(prompt).strip() if prompt is not None else ""
    return ""


def validate_evaluation_method(method: Mapping[str, object]) -> None:
    """Validate the observe/measure shape of a user-authored method.

    Only enforced once the user opts into the new model by stating the
    ``measurable`` flag — legacy ``weights`` / ``state_scores`` methods (no
    ``measurable`` key) pass through unchanged. Rules:

    * ``measurable`` must be a bool.
    * An ``observation`` is required, with a valid :data:`OBSERVATION_SOURCES`
      source and a non-empty prompt; an ``external`` source must carry a ``ref``
      handle (the db/api/mcp target) since there is nothing internal to read.
    * ``measurable: true`` requires a non-empty ``scoring.prompt`` (the rule that
      programmatically computes the 0–100 score from the observed state).
    * ``measurable: false`` must NOT carry a scoring prompt (qualitative status
      only — no auto-score).

    Raises :class:`ValueError` on any violation. The score itself is always
    engine-computed + clamped, never taken from the method.
    """
    if "measurable" not in method:
        return
    measurable = method.get("measurable")
    if not isinstance(measurable, bool):
        raise ValueError("evaluation method 'measurable' must be a boolean")

    observation = parse_observation(method)
    if observation is None:
        raise ValueError(
            "an evaluation method must carry an 'observation' "
            "{source, prompt, ref?} (every goal is observable)"
        )
    if observation.source not in OBSERVATION_SOURCES:
        raise ValueError(
            f"observation.source must be one of {OBSERVATION_SOURCES}, "
            f"got {observation.source!r}"
        )
    if not observation.prompt.strip():
        raise ValueError("observation.prompt must be a non-empty string")
    if observation.source == "external" and observation.ref is None:
        raise ValueError(
            "an 'external' observation must carry a 'ref' handle "
            "(the db/api/mcp target to observe)"
        )

    scoring_prompt = method_scoring_prompt(method)
    if measurable and not scoring_prompt:
        raise ValueError(
            "a measurable goal/task requires a 'scoring' {prompt} that "
            "programmatically computes the 0–100 score from the observed state"
        )
    if not measurable and scoring_prompt:
        raise ValueError(
            "a non-measurable goal/task must not carry a scoring prompt "
            "(its status is qualitative / user-confirmed, with no auto-score)"
        )


@dataclass(frozen=True)
class GtsAuditEvent:
    """One audited GTS decision (surfaced to C5/C8 + a durable local log)."""

    id: str
    ts: float
    actor: GtsActor
    actor_user_id: str
    action: str
    target_kind: str
    target_ref: str
    decision: str  # "refused" | "recorded"
    reason: str
    mode: str

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "ts": self.ts,
            "actor": self.actor,
            "actor_user_id": self.actor_user_id,
            "action": self.action,
            "target_kind": self.target_kind,
            "target_ref": self.target_ref,
            "decision": self.decision,
            "reason": self.reason,
            "mode": self.mode,
            # C8 trace-row kind: a refused Core-authority mutation is
            # ``core_denied``; a recorded one is a ``change``.
            "kind": "core_denied" if self.decision == "refused" else "change",
        }


#: A C5 change-audit sink (e.g. an adapter over the FG-12 ``ChangeLog``). Given
#: the shaped audit event; must never raise into the mutation path.
GtsAuditSink = Callable[[Mapping[str, object]], None]

#: A C6 assignment-notification sink (FG-19). Given a shaped notification
#: payload (kind ``gts_assignment`` + the item/assignee/action) when a grant is
#: created/changed; a real deployment routes it to the existing human-comms
#: surface (FG-10). Must never raise into the assignment path; the default is a
#: no-op so the Core engine has no hard dependency on a live channel.
GtsNotifier = Callable[[Mapping[str, object]], None]


# ---------------------------------------------------------------------------
# Pure scoring (always computed, clamped 0–100, priority-weighted rollup)
# ---------------------------------------------------------------------------


def clamp_score(value: float) -> float:
    """Clamp any raw score onto the closed ``[0, 100]`` band."""
    return max(0.0, min(100.0, float(value)))


def score_from_metrics(
    metrics: Sequence[GoalMetric],
    *,
    weights: Optional[Mapping[str, float]] = None,
) -> float:
    """Compute a leaf goal score as a weighted mean of metric progress × 100.

    Reuses :attr:`GoalMetric.progress_fraction` (already clamped to ``[0, 1]``)
    so achievement is measured, not asserted. Unmeasured metrics (no target)
    contribute ``0``. With no measurable metric the score is ``0`` — there is
    no evidence of progress yet. The result is clamped to ``[0, 100]``.
    """
    measurable = [m for m in metrics if m.is_measurable()]
    if not measurable:
        return 0.0
    total_weight = 0.0
    accumulated = 0.0
    for metric in measurable:
        weight = float((weights or {}).get(metric.name, 1.0))
        if weight <= 0.0:
            continue
        total_weight += weight
        accumulated += weight * metric.progress_fraction * 100.0
    if total_weight <= 0.0:
        return 0.0
    return clamp_score(accumulated / total_weight)


def score_from_progress(
    progress_states: Sequence[str],
    current_state: str,
    completion_state: str,
    *,
    status: str = "",
    state_scores: Optional[Mapping[str, float]] = None,
) -> float:
    """Compute a leaf task score from its FG-06 progress state machine.

    An explicit ``state_scores`` rubric (from the user's evaluation method)
    wins when it names the current state. Otherwise the score is the fraction
    of the way from the trigger to the completion state, so ``completed`` is
    ``100`` and a cancelled task is ``0``. Always clamped to ``[0, 100]``.
    """
    if status == "cancelled":
        return 0.0
    if state_scores and current_state in state_scores:
        return clamp_score(float(state_scores[current_state]))
    states = list(progress_states)
    if current_state not in states or completion_state not in states:
        return 0.0
    completion_index = states.index(completion_state)
    if completion_index <= 0:
        return 100.0 if current_state == completion_state else 0.0
    fraction = states.index(current_state) / completion_index
    return clamp_score(fraction * 100.0)


def rollup_score(children: Sequence[Tuple[float, str]]) -> float:
    """Roll child scores up to a parent as a **priority-weighted** mean.

    ``children`` is ``(score, priority)`` pairs. Higher-priority children pull
    the parent's score harder (weights from
    :func:`hermes_cli.goals.priority_weight`). Empty → ``0``. Clamped.
    """
    if not children:
        return 0.0
    total_weight = 0
    accumulated = 0.0
    for score, priority in children:
        weight = priority_weight(priority)
        total_weight += weight
        accumulated += weight * clamp_score(score)
    if total_weight <= 0:
        return 0.0
    return clamp_score(accumulated / total_weight)


# ---------------------------------------------------------------------------
# Cache-safe surfacing (tool result / appended message — never the prompt)
# ---------------------------------------------------------------------------


def render_gts_block(
    goals: Sequence[GtsGoal],
    *,
    title: str = "GTS Centre",
) -> str:
    """Render a GTS goal slate as a labelled text block.

    Cache-safe by construction (mirrors :func:`goals.render_metrics_block`):
    the caller only ever appends this to a continuation *user* message or hands
    it to a tool result — it is never spliced into the byte-stable system
    prompt, so prompt caching is preserved.
    """
    lines = [f"[{title}]"]
    if not goals:
        lines.append("(no goals)")
        return "\n".join(lines)
    for goal in sorted(goals, key=lambda g: (priority_rank(g.priority), g.title)):
        score = "—" if goal.score is None else f"{goal.score:.0f}%"
        marker = "•" if goal.level == "top" else "  ↳"
        lines.append(
            f"{marker} {goal.title} [{goal.priority}] score={score} "
            f"({goal.status})"
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# The GTS Centre engine
# ---------------------------------------------------------------------------


class GtsCentre:
    """Async unified-graph engine over the C2-scoped, C3-routed GTS tables.

    Composes the FG-04 goal registry and FG-06 task registry (reuse, not
    duplication) and adds hierarchy, M:N edges, a skills registry, user-owned
    evaluation methods, and computed/rolled-up scores. Authority is fail-closed:
    the runtime agent is refused (and audited) on top-level goals and on any
    evaluation-method change.
    """

    def __init__(
        self,
        store: "SupabaseAppStore",
        *,
        audit_sink: Optional[GtsAuditSink] = None,
        score_evaluator: Optional[GtsScoreEvaluator] = None,
        consent_policy: Optional[ConsentPolicy] = None,
        notifier: Optional[GtsNotifier] = None,
    ) -> None:
        self._store = store
        self._goals = GoalRegistryStore(store)
        self._tasks = TaskRegistryStore(store)
        self._audit_sink = audit_sink
        # The scoring-prompt execution seam for measurable nodes. Deterministic
        # default; a real executor (LLM / db / api / mcp) can be injected here
        # without touching this Core engine.
        self._score_evaluator = score_evaluator or default_score_evaluator
        # C6 (FG-19): an agent-initiated cross-user assignment goes through the
        # existing consent/approval surface; the default policy prompts (never
        # silently auto-approves an agent's cross-user side effect).
        self._consent_policy = consent_policy or ConsentPolicy()
        # C6 assignment notification seam (FG-10); no-op default.
        self._notifier = notifier

    @property
    def mode(self) -> str:
        return self._store.mode

    @property
    def goals(self) -> GoalRegistryStore:
        return self._goals

    @property
    def tasks(self) -> TaskRegistryStore:
        return self._tasks

    async def _connect(self) -> "asyncpg.Connection":
        connection = await self._store.connect()
        await connection.execute(
            f'CREATE SCHEMA IF NOT EXISTS "{self._store.schema}"'
        )
        return connection

    async def initialize(
        self,
        *,
        connection: Optional["asyncpg.Connection"] = None,
    ) -> None:
        """Create/extend every GTS table + RLS policy (idempotent).

        The goal/task read policies are (re)installed **grant-aware** (FG-19):
        the FG-04/FG-06 stores first install the plain shared/private policy,
        then this method replaces it with one that also admits an active
        per-item grant to the bound principal. Re-applying is safe — the policy
        is dropped and recreated — and keeps the grant clause colocated with
        the GTS Centre that owns per-item assignment.
        """
        own = connection is None
        conn = connection or await self._connect()
        try:
            await self._goals.initialize(connection=conn)
            await self._tasks.initialize(connection=conn)
            await conn.execute(_EXTEND_SQL)
            await conn.execute(ITEM_GRANTS_SCHEMA_SQL)
            await apply_item_grants_rls(conn)
            await apply_scope_rls(conn, SKILLS_TABLE)
            # Replace the plain goal/task policies with grant-aware ones so an
            # assignee/watcher sees the granted item at the DB layer too.
            await apply_scope_rls(conn, GOALS_TABLE, grant_item_kind="goal")
            await apply_scope_rls(conn, TASKS_TABLE, grant_item_kind="task")
        finally:
            if own:
                await conn.close()

    # -- authority + audit -------------------------------------------------

    def _audit(
        self,
        *,
        actor: GtsActor,
        actor_user_id: str,
        action: str,
        target_kind: str,
        target_ref: str,
        decision: str,
        reason: str,
    ) -> GtsAuditEvent:
        event = GtsAuditEvent(
            id=f"gts_{uuid.uuid4().hex}",
            ts=time.time(),
            actor=actor,
            actor_user_id=actor_user_id,
            action=action,
            target_kind=target_kind,
            target_ref=target_ref,
            decision=decision,
            reason=reason,
            mode=self.mode,
        )
        # C8 — observational trace (no-op when no trace is bound; cache-safe).
        try:
            from hermes_cli.interactions import observe

            observe(
                "core_denied" if decision == "refused" else "change",
                ref=event.id,
                summary=f"GTS {action} {target_kind}:{target_ref} → {decision}",
            )
        except Exception:
            pass
        # Durable local audit — the always-on guarantee (mirrors FG-14 C7).
        _append_local_audit(event)
        # C5 — best-effort forward to an injected change recorder.
        if self._audit_sink is not None:
            try:
                self._audit_sink(event.as_dict())
            except Exception:
                pass
        return event

    def _require_user(
        self,
        actor: GtsActor,
        actor_user_id: str,
        *,
        action: str,
        target_kind: str,
        target_ref: str,
    ) -> None:
        """Fail-closed guard: only the user may perform ``action``.

        A runtime-agent attempt is audited (C5/C8 + durable log) and refused.
        """
        if actor == "user":
            return
        self._audit(
            actor=actor,
            actor_user_id=actor_user_id,
            action=action,
            target_kind=target_kind,
            target_ref=target_ref,
            decision="refused",
            reason=(
                f"{action} on a {target_kind} is user-only (Core authority C7/C9); "
                "the runtime agent is refused"
            ),
        )
        raise GtsAuthorityError(
            f"Refused: the runtime agent may not {action} (user-only under the "
            f"GTS Centre's Core authority rules). This attempt has been audited."
        )

    def _is_item_owner(self, principal: Principal, owner_user_id: str) -> bool:
        """Whether ``principal`` owns the item (its creator) or is the owner role."""
        return principal.is_owner or owner_user_id == principal.user_id

    def _require_item_owner(
        self,
        principal: Principal,
        owner_user_id: str,
        *,
        actor: GtsActor,
        action: str,
        target_kind: str,
        target_ref: str,
    ) -> None:
        """Fail-closed guard (FG-19): only the item's owner may ``action`` it.

        Content edits (priority, reparent), the user-owned evaluation method,
        and the assignment controls (assign / reassign / revoke) belong to the
        item's creator (or the owner role). A **grantee** — an assignee or
        watcher who can *read* the item via a per-item grant — is refused and
        audited here; assignees may still advance progress (a separate seam).
        """
        if self._is_item_owner(principal, owner_user_id):
            return
        self._audit(
            actor=actor,
            actor_user_id=principal.user_id,
            action=action,
            target_kind=target_kind,
            target_ref=target_ref,
            decision="refused",
            reason=(
                f"{action} is owner-only (FG-19); a grantee (assignee/watcher) "
                "may not"
            ),
        )
        raise GtsAuthorityError(
            f"Refused: only the item owner may {action}; an assignee/watcher "
            "may not (FG-19). This attempt has been audited."
        )

    # -- goals -------------------------------------------------------------

    async def create_goal(
        self,
        principal: Principal,
        title: str,
        *,
        actor: GtsActor = "user",
        parent_goal_id: Optional[str] = None,
        priority: str = DEFAULT_GOAL_PRIORITY,
        visibility: Optional[str] = None,
        connection: Optional["asyncpg.Connection"] = None,
    ) -> GtsGoal:
        """Create a goal.

        A **top-level** goal (``parent_goal_id is None``) is *user-only* — an
        agent attempt is refused and audited. A **sub-goal** may be created by
        the agent under a parent that ``principal`` can read.
        """
        clean_title = (title or "").strip()
        if not clean_title:
            raise ValueError("Cannot create a goal with an empty title")
        resolved_visibility = _resolve_visibility(principal, visibility)
        resolved_priority = normalize_priority(priority)

        own = connection is None
        conn = connection or await self._connect()
        try:
            if parent_goal_id is None:
                self._require_user(
                    actor,
                    principal.user_id,
                    action="create a top-level goal",
                    target_kind="goal",
                    target_ref="(new top-level goal)",
                )
                level = "top"
            else:
                parent = await self._get_goal_node(
                    principal, parent_goal_id, conn
                )
                if parent is None:
                    raise PermissionError(
                        f"Parent goal {parent_goal_id} not found or not visible "
                        f"to {principal.user_id}"
                    )
                level = "sub"
            row = await conn.fetchrow(
                f"""
                INSERT INTO {GOALS_TABLE}
                    (owner_user_id, visibility, title, priority,
                     parent_goal_id, level)
                VALUES ($1, $2, $3, $4, $5, $6)
                RETURNING {_GOAL_COLUMNS}
                """,
                principal.user_id,
                resolved_visibility,
                clean_title,
                resolved_priority,
                parent_goal_id,
                level,
            )
            goal = _row_to_goal_node(row)
            self._audit(
                actor=actor,
                actor_user_id=principal.user_id,
                action="create_goal",
                target_kind="goal",
                target_ref=goal.id,
                decision="recorded",
                reason=f"created {level} goal",
            )
            return goal
        finally:
            if own:
                await conn.close()

    async def get_goal(
        self,
        principal: Principal,
        goal_id: str,
        *,
        connection: Optional["asyncpg.Connection"] = None,
    ) -> Optional[GtsGoal]:
        """Return the goal node if ``principal`` may read it (C2), else None."""
        own = connection is None
        conn = connection or await self._connect()
        try:
            return await self._get_goal_node(principal, goal_id, conn)
        finally:
            if own:
                await conn.close()

    async def list_goals(
        self,
        principal: Principal,
        *,
        parent_goal_id: Optional[str] = None,
        top_level_only: bool = False,
        connection: Optional["asyncpg.Connection"] = None,
    ) -> List[GtsGoal]:
        """List readable goal nodes, priority-ordered.

        With ``parent_goal_id`` returns that goal's direct children; with
        ``top_level_only`` returns only ``level = 'top'`` goals. Grant-aware
        (FG-19): an assignee/watcher additionally sees a specifically-granted
        goal, but never the owner's *other* private goals.
        """
        clauses: List[str] = []
        params: List[object] = []
        index = 1
        if parent_goal_id is not None:
            clauses.append(f"parent_goal_id = ${index}")
            params.append(parent_goal_id)
            index += 1
        elif top_level_only:
            clauses.append("parent_goal_id IS NULL")
        predicate = scope_filter(principal, start_index=index, grant_item_kind="goal")
        clauses.append(predicate.sql)
        params.extend(predicate.params)

        own = connection is None
        conn = connection or await self._connect()
        try:
            rows = await conn.fetch(
                f"""
                SELECT {_GOAL_COLUMNS} FROM {GOALS_TABLE}
                WHERE {" AND ".join(clauses)}
                """,
                *params,
            )
            goals = [_row_to_goal_node(r) for r in rows]
            return sorted(goals, key=lambda g: (priority_rank(g.priority), g.title))
        finally:
            if own:
                await conn.close()

    async def set_goal_priority(
        self,
        principal: Principal,
        goal_id: str,
        priority: str,
        *,
        actor: GtsActor = "user",
        connection: Optional["asyncpg.Connection"] = None,
    ) -> GtsGoal:
        """Change a goal's priority (managing a top-level goal is user-only)."""
        own = connection is None
        conn = connection or await self._connect()
        try:
            goal = await self._require_goal_manageable(
                principal, goal_id, conn, actor=actor, action="set_goal_priority"
            )
            self._require_item_owner(
                principal,
                goal.owner_user_id,
                actor=actor,
                action="change a goal's priority",
                target_kind="goal",
                target_ref=goal.id,
            )
            row = await conn.fetchrow(
                f"""
                UPDATE {GOALS_TABLE}
                SET priority = $2, updated_at = NOW()
                WHERE id = $1
                RETURNING {_GOAL_COLUMNS}
                """,
                goal.id,
                normalize_priority(priority),
            )
            return _row_to_goal_node(row)
        finally:
            if own:
                await conn.close()

    async def reparent_goal(
        self,
        principal: Principal,
        goal_id: str,
        new_parent_goal_id: Optional[str],
        *,
        actor: GtsActor = "user",
        connection: Optional["asyncpg.Connection"] = None,
    ) -> GtsGoal:
        """Move a goal under a new parent (or to top-level), cycle-safe.

        Promoting a goal to top-level (``new_parent_goal_id is None``) is
        user-only. Reparenting refuses a self-parent or any parent that is a
        descendant of the goal (which would create a cycle).
        """
        own = connection is None
        conn = connection or await self._connect()
        try:
            goal = await self._get_goal_node(principal, goal_id, conn)
            if goal is None:
                raise PermissionError(
                    f"Goal {goal_id} not found or not visible to {principal.user_id}"
                )
            self._require_item_owner(
                principal,
                goal.owner_user_id,
                actor=actor,
                action="reparent a goal",
                target_kind="goal",
                target_ref=goal_id,
            )
            if new_parent_goal_id is None:
                self._require_user(
                    actor,
                    principal.user_id,
                    action="promote a goal to top-level",
                    target_kind="goal",
                    target_ref=goal_id,
                )
                level = "top"
            else:
                if new_parent_goal_id == goal_id:
                    raise GtsCycleError("A goal cannot be its own parent")
                parent = await self._get_goal_node(
                    principal, new_parent_goal_id, conn
                )
                if parent is None:
                    raise PermissionError(
                        f"Parent goal {new_parent_goal_id} not found or not "
                        f"visible to {principal.user_id}"
                    )
                if await self._is_goal_descendant(
                    conn, ancestor=goal_id, candidate=new_parent_goal_id
                ):
                    raise GtsCycleError(
                        f"Reparenting {goal_id} under {new_parent_goal_id} "
                        "would create a cycle"
                    )
                level = "sub"
            row = await conn.fetchrow(
                f"""
                UPDATE {GOALS_TABLE}
                SET parent_goal_id = $2, level = $3, updated_at = NOW()
                WHERE id = $1
                RETURNING {_GOAL_COLUMNS}
                """,
                goal_id,
                new_parent_goal_id,
                level,
            )
            return _row_to_goal_node(row)
        finally:
            if own:
                await conn.close()

    async def _require_goal_manageable(
        self,
        principal: Principal,
        goal_id: str,
        conn: "asyncpg.Connection",
        *,
        actor: GtsActor,
        action: str,
    ) -> GtsGoal:
        goal = await self._get_goal_node(principal, goal_id, conn)
        if goal is None:
            raise PermissionError(
                f"Goal {goal_id} not found or not visible to {principal.user_id}"
            )
        if goal.level == "top":
            self._require_user(
                actor,
                principal.user_id,
                action=f"{action} on a top-level goal",
                target_kind="goal",
                target_ref=goal_id,
            )
        return goal

    async def _get_goal_node(
        self,
        principal: Principal,
        goal_id: str,
        conn: "asyncpg.Connection",
    ) -> Optional[GtsGoal]:
        predicate = scope_filter(principal, start_index=2, grant_item_kind="goal")
        row = await conn.fetchrow(
            f"""
            SELECT {_GOAL_COLUMNS} FROM {GOALS_TABLE}
            WHERE id = $1 AND {predicate.sql}
            """,
            goal_id,
            *predicate.params,
        )
        return _row_to_goal_node(row) if row is not None else None

    async def _is_goal_descendant(
        self,
        conn: "asyncpg.Connection",
        *,
        ancestor: str,
        candidate: str,
    ) -> bool:
        """True if ``candidate`` is ``ancestor`` or below it in the goal tree.

        Walks up from ``candidate`` following ``parent_goal_id``; if we reach
        ``ancestor`` then ``candidate`` sits inside ``ancestor``'s subtree.
        Unscoped on purpose — cycle safety is a graph invariant, not a per-user
        visibility question.
        """
        seen: set[str] = set()
        node: Optional[str] = candidate
        while node is not None:
            if node == ancestor:
                return True
            if node in seen:
                break
            seen.add(node)
            node = await conn.fetchval(
                f"SELECT parent_goal_id FROM {GOALS_TABLE} WHERE id = $1",
                node,
            )
            node = str(node) if node is not None else None
        return False

    # -- tasks -------------------------------------------------------------

    async def create_task(
        self,
        principal: Principal,
        spec: TaskSpec,
        *,
        actor: GtsActor = "user",
        parent_task_id: Optional[str] = None,
        priority: str = DEFAULT_GOAL_PRIORITY,
        visibility: Optional[str] = None,
        goal_ids: Optional[Sequence[str]] = None,
        connection: Optional["asyncpg.Connection"] = None,
    ) -> GtsTask:
        """Create a task or sub-task (the agent is allowed under a readable parent).

        Optionally link the new task to one or more readable goals (M:N).
        """
        own = connection is None
        conn = connection or await self._connect()
        try:
            if parent_task_id is not None:
                parent = await self._get_task_node(principal, parent_task_id, conn)
                if parent is None:
                    raise PermissionError(
                        f"Parent task {parent_task_id} not found or not visible "
                        f"to {principal.user_id}"
                    )
            record = await self._tasks.create_task(
                principal,
                spec,
                visibility=visibility,
                connection=conn,
            )
            row = await conn.fetchrow(
                f"""
                UPDATE {TASKS_TABLE}
                SET parent_task_id = $2, priority = $3, updated_at = NOW()
                WHERE id = $1
                RETURNING {_TASK_COLUMNS}
                """,
                record.id,
                parent_task_id,
                normalize_priority(priority),
            )
            task = _row_to_task_node(row)
            for goal_id in goal_ids or ():
                await self._link_task_goal(principal, task.id, goal_id, conn)
            self._audit(
                actor=actor,
                actor_user_id=principal.user_id,
                action="create_task",
                target_kind="task",
                target_ref=task.id,
                decision="recorded",
                reason="created sub-task" if parent_task_id else "created task",
            )
            return task
        finally:
            if own:
                await conn.close()

    async def get_task(
        self,
        principal: Principal,
        task_id: str,
        *,
        connection: Optional["asyncpg.Connection"] = None,
    ) -> Optional[GtsTask]:
        """Return the task node if ``principal`` may read it (C2), else None."""
        own = connection is None
        conn = connection or await self._connect()
        try:
            return await self._get_task_node(principal, task_id, conn)
        finally:
            if own:
                await conn.close()

    async def reparent_task(
        self,
        principal: Principal,
        task_id: str,
        new_parent_task_id: Optional[str],
        *,
        actor: GtsActor = "user",
        connection: Optional["asyncpg.Connection"] = None,
    ) -> GtsTask:
        """Move a task under a new parent task, cycle-safe (owner-only, FG-19)."""
        own = connection is None
        conn = connection or await self._connect()
        try:
            task = await self._get_task_node(principal, task_id, conn)
            if task is None:
                raise PermissionError(
                    f"Task {task_id} not found or not visible to {principal.user_id}"
                )
            self._require_item_owner(
                principal,
                task.owner_user_id,
                actor=actor,
                action="reparent a task",
                target_kind="task",
                target_ref=task_id,
            )
            if new_parent_task_id is not None:
                if new_parent_task_id == task_id:
                    raise GtsCycleError("A task cannot be its own parent")
                parent = await self._get_task_node(
                    principal, new_parent_task_id, conn
                )
                if parent is None:
                    raise PermissionError(
                        f"Parent task {new_parent_task_id} not found or not "
                        f"visible to {principal.user_id}"
                    )
                if await self._is_task_descendant(
                    conn, ancestor=task_id, candidate=new_parent_task_id
                ):
                    raise GtsCycleError(
                        f"Reparenting {task_id} under {new_parent_task_id} "
                        "would create a cycle"
                    )
            row = await conn.fetchrow(
                f"""
                UPDATE {TASKS_TABLE}
                SET parent_task_id = $2, updated_at = NOW()
                WHERE id = $1
                RETURNING {_TASK_COLUMNS}
                """,
                task_id,
                new_parent_task_id,
            )
            return _row_to_task_node(row)
        finally:
            if own:
                await conn.close()

    async def _get_task_node(
        self,
        principal: Principal,
        task_id: str,
        conn: "asyncpg.Connection",
    ) -> Optional[GtsTask]:
        predicate = scope_filter(principal, start_index=2, grant_item_kind="task")
        row = await conn.fetchrow(
            f"""
            SELECT {_TASK_COLUMNS} FROM {TASKS_TABLE}
            WHERE id = $1 AND {predicate.sql}
            """,
            task_id,
            *predicate.params,
        )
        return _row_to_task_node(row) if row is not None else None

    async def _is_task_descendant(
        self,
        conn: "asyncpg.Connection",
        *,
        ancestor: str,
        candidate: str,
    ) -> bool:
        seen: set[str] = set()
        node: Optional[str] = candidate
        while node is not None:
            if node == ancestor:
                return True
            if node in seen:
                break
            seen.add(node)
            node = await conn.fetchval(
                f"SELECT parent_task_id FROM {TASKS_TABLE} WHERE id = $1",
                node,
            )
            node = str(node) if node is not None else None
        return False

    # -- skills registry ---------------------------------------------------

    async def register_skill(
        self,
        principal: Principal,
        name: str,
        skill_ref: str,
        *,
        visibility: Optional[str] = None,
        connection: Optional["asyncpg.Connection"] = None,
    ) -> SkillNode:
        """Register a skill *reference* node (points at existing skill content)."""
        clean_name = (name or "").strip()
        clean_ref = (skill_ref or "").strip()
        if not clean_name:
            raise ValueError("A skill node requires a name")
        if not clean_ref:
            raise ValueError("A skill node requires a skill_ref to existing content")
        resolved_visibility = _resolve_visibility(principal, visibility)
        own = connection is None
        conn = connection or await self._connect()
        try:
            row = await conn.fetchrow(
                f"""
                INSERT INTO {SKILLS_TABLE}
                    (owner_user_id, visibility, name, skill_ref)
                VALUES ($1, $2, $3, $4)
                ON CONFLICT (owner_user_id, name) DO UPDATE SET
                    skill_ref = EXCLUDED.skill_ref,
                    visibility = EXCLUDED.visibility,
                    updated_at = NOW()
                RETURNING id, owner_user_id, visibility, name, skill_ref
                """,
                principal.user_id,
                resolved_visibility,
                clean_name,
                clean_ref,
            )
            return _row_to_skill(row)
        finally:
            if own:
                await conn.close()

    async def list_skills(
        self,
        principal: Principal,
        *,
        connection: Optional["asyncpg.Connection"] = None,
    ) -> List[SkillNode]:
        """List skill nodes ``principal`` may read (C2)."""
        predicate = scope_filter(principal, start_index=1)
        own = connection is None
        conn = connection or await self._connect()
        try:
            rows = await conn.fetch(
                f"""
                SELECT id, owner_user_id, visibility, name, skill_ref
                FROM {SKILLS_TABLE}
                WHERE {predicate.sql}
                ORDER BY name
                """,
                *predicate.params,
            )
            return [_row_to_skill(r) for r in rows]
        finally:
            if own:
                await conn.close()

    # -- M:N edges ---------------------------------------------------------

    async def link_task_to_goal(
        self,
        principal: Principal,
        task_id: str,
        goal_id: str,
        *,
        connection: Optional["asyncpg.Connection"] = None,
    ) -> None:
        """Associate a task with a goal (M:N). Both must be readable by C2."""
        own = connection is None
        conn = connection or await self._connect()
        try:
            await self._link_task_goal(principal, task_id, goal_id, conn)
        finally:
            if own:
                await conn.close()

    async def _link_task_goal(
        self,
        principal: Principal,
        task_id: str,
        goal_id: str,
        conn: "asyncpg.Connection",
    ) -> None:
        if await self._get_task_node(principal, task_id, conn) is None:
            raise PermissionError(
                f"Task {task_id} not found or not visible to {principal.user_id}"
            )
        if await self._get_goal_node(principal, goal_id, conn) is None:
            raise PermissionError(
                f"Goal {goal_id} not found or not visible to {principal.user_id}"
            )
        await conn.execute(
            f"""
            INSERT INTO {TASK_GOALS_TABLE} (task_id, goal_id)
            VALUES ($1, $2)
            ON CONFLICT DO NOTHING
            """,
            task_id,
            goal_id,
        )

    async def unlink_task_from_goal(
        self,
        principal: Principal,
        task_id: str,
        goal_id: str,
        *,
        connection: Optional["asyncpg.Connection"] = None,
    ) -> None:
        """Remove a task↔goal association (both must be readable)."""
        own = connection is None
        conn = connection or await self._connect()
        try:
            if await self._get_task_node(principal, task_id, conn) is None:
                raise PermissionError(
                    f"Task {task_id} not found or not visible to {principal.user_id}"
                )
            await conn.execute(
                f"DELETE FROM {TASK_GOALS_TABLE} WHERE task_id = $1 AND goal_id = $2",
                task_id,
                goal_id,
            )
        finally:
            if own:
                await conn.close()

    async def goals_for_task(
        self,
        principal: Principal,
        task_id: str,
        *,
        connection: Optional["asyncpg.Connection"] = None,
    ) -> List[GtsGoal]:
        """Goals linked to a task, restricted to those ``principal`` may read."""
        predicate = scope_filter(
            principal,
            column="g.visibility",
            start_index=2,
            grant_item_kind="goal",
            id_column="g.id",
        )
        own = connection is None
        conn = connection or await self._connect()
        try:
            if await self._get_task_node(principal, task_id, conn) is None:
                return []
            rows = await conn.fetch(
                f"""
                SELECT {_goal_columns_prefixed("g")}
                FROM {TASK_GOALS_TABLE} tg
                JOIN {GOALS_TABLE} g ON g.id = tg.goal_id
                WHERE tg.task_id = $1 AND {predicate.sql}
                """,
                task_id,
                *predicate.params,
            )
            return [_row_to_goal_node(r) for r in rows]
        finally:
            if own:
                await conn.close()

    async def link_task_to_skill(
        self,
        principal: Principal,
        task_id: str,
        skill_id: str,
        *,
        connection: Optional["asyncpg.Connection"] = None,
    ) -> None:
        """Associate a task with a registered skill (M:N). Both readable by C2."""
        own = connection is None
        conn = connection or await self._connect()
        try:
            if await self._get_task_node(principal, task_id, conn) is None:
                raise PermissionError(
                    f"Task {task_id} not found or not visible to {principal.user_id}"
                )
            skill = await conn.fetchval(
                f"""
                SELECT id FROM {SKILLS_TABLE}
                WHERE id = $1 AND {scope_filter(principal, start_index=2).sql}
                """,
                skill_id,
                *scope_filter(principal, start_index=2).params,
            )
            if skill is None:
                raise PermissionError(
                    f"Skill {skill_id} not found or not visible to "
                    f"{principal.user_id}"
                )
            await conn.execute(
                f"""
                INSERT INTO {TASK_SKILLS_TABLE} (task_id, skill_id)
                VALUES ($1, $2)
                ON CONFLICT DO NOTHING
                """,
                task_id,
                skill_id,
            )
        finally:
            if own:
                await conn.close()

    async def skills_for_task(
        self,
        principal: Principal,
        task_id: str,
        *,
        connection: Optional["asyncpg.Connection"] = None,
    ) -> List[SkillNode]:
        """Skills linked to a task, restricted to those ``principal`` may read."""
        predicate = scope_filter(principal, column="s.visibility", start_index=2)
        own = connection is None
        conn = connection or await self._connect()
        try:
            if await self._get_task_node(principal, task_id, conn) is None:
                return []
            rows = await conn.fetch(
                f"""
                SELECT s.id, s.owner_user_id, s.visibility, s.name, s.skill_ref
                FROM {TASK_SKILLS_TABLE} ts
                JOIN {SKILLS_TABLE} s ON s.id = ts.skill_id
                WHERE ts.task_id = $1 AND {predicate.sql}
                ORDER BY s.name
                """,
                task_id,
                *predicate.params,
            )
            return [_row_to_skill(r) for r in rows]
        finally:
            if own:
                await conn.close()

    # -- evaluation methods (user-only, agent-immutable) -------------------

    async def set_evaluation_method(
        self,
        principal: Principal,
        target_kind: str,
        target_id: str,
        method: Mapping[str, object],
        *,
        actor: GtsActor = "user",
        connection: Optional["asyncpg.Connection"] = None,
    ) -> EvaluationMethod:
        """Set/replace the evaluation method for a goal or task — **user only**.

        The runtime agent is refused and audited (C5/C8): the method — the
        observation prompt, the ``measurable`` flag, and the scoring prompt —
        governs *how* a node scores and is a Core-protected, user-owned field
        (C7/C9). The agent may still record observations/measurements/progress,
        which does not touch the method.
        """
        if target_kind not in _TARGET_KINDS:
            raise ValueError(f"Unknown evaluation target_kind: {target_kind!r}")
        # Authority first (C7/C9): the agent is refused + audited before we
        # even validate the method shape — setting the observe/measure method
        # is user-only.
        self._require_user(
            actor,
            principal.user_id,
            action="set an evaluation method",
            target_kind=target_kind,
            target_ref=target_id,
        )
        validate_evaluation_method(method)
        own = connection is None
        conn = connection or await self._connect()
        try:
            node = await self._require_target_node(
                principal, target_kind, target_id, conn
            )
            # The evaluation method is user-owned (FG-18); a grantee
            # (assignee/watcher) who can read the item may NOT change it.
            self._require_item_owner(
                principal,
                node.owner_user_id,
                actor=actor,
                action="set an evaluation method",
                target_kind=target_kind,
                target_ref=target_id,
            )
            row = await conn.fetchrow(
                f"""
                INSERT INTO {EVALUATION_METHODS_TABLE}
                    (target_kind, target_id, method_json, set_by_user_id, locked)
                VALUES ($1, $2, $3::jsonb, $4, TRUE)
                ON CONFLICT (target_kind, target_id) DO UPDATE SET
                    method_json = EXCLUDED.method_json,
                    set_by_user_id = EXCLUDED.set_by_user_id,
                    updated_at = NOW()
                RETURNING id, target_kind, target_id, method_json,
                          set_by_user_id, locked
                """,
                target_kind,
                target_id,
                json.dumps(dict(method), sort_keys=True),
                principal.user_id,
            )
            table = GOALS_TABLE if target_kind == "goal" else TASKS_TABLE
            await conn.execute(
                f"UPDATE {table} SET evaluation_method_ref = $2 WHERE id = $1",
                target_id,
                row["id"],
            )
            self._audit(
                actor=actor,
                actor_user_id=principal.user_id,
                action="set_evaluation_method",
                target_kind=target_kind,
                target_ref=target_id,
                decision="recorded",
                reason="user set the evaluation method",
            )
            return _row_to_method(row)
        finally:
            if own:
                await conn.close()

    async def get_evaluation_method(
        self,
        principal: Principal,
        target_kind: str,
        target_id: str,
        *,
        connection: Optional["asyncpg.Connection"] = None,
    ) -> Optional[EvaluationMethod]:
        """Return the evaluation method for a readable goal/task, if any."""
        if target_kind not in _TARGET_KINDS:
            raise ValueError(f"Unknown evaluation target_kind: {target_kind!r}")
        own = connection is None
        conn = connection or await self._connect()
        try:
            if (
                await self._target_readable(principal, target_kind, target_id, conn)
                is False
            ):
                return None
            row = await conn.fetchrow(
                f"""
                SELECT id, target_kind, target_id, method_json,
                       set_by_user_id, locked
                FROM {EVALUATION_METHODS_TABLE}
                WHERE target_kind = $1 AND target_id = $2
                """,
                target_kind,
                target_id,
            )
            return _row_to_method(row) if row is not None else None
        finally:
            if own:
                await conn.close()

    async def _require_target_readable(
        self,
        principal: Principal,
        target_kind: str,
        target_id: str,
        conn: "asyncpg.Connection",
    ) -> None:
        if not await self._target_readable(principal, target_kind, target_id, conn):
            raise PermissionError(
                f"{target_kind} {target_id} not found or not visible to "
                f"{principal.user_id}"
            )

    async def _require_target_node(
        self,
        principal: Principal,
        target_kind: str,
        target_id: str,
        conn: "asyncpg.Connection",
    ) -> "GtsGoal | GtsTask":
        """Return the readable goal/task node, or raise ``PermissionError``."""
        node = (
            await self._get_goal_node(principal, target_id, conn)
            if target_kind == "goal"
            else await self._get_task_node(principal, target_id, conn)
        )
        if node is None:
            raise PermissionError(
                f"{target_kind} {target_id} not found or not visible to "
                f"{principal.user_id}"
            )
        return node

    async def _target_readable(
        self,
        principal: Principal,
        target_kind: str,
        target_id: str,
        conn: "asyncpg.Connection",
    ) -> bool:
        if target_kind == "goal":
            return await self._get_goal_node(principal, target_id, conn) is not None
        return await self._get_task_node(principal, target_id, conn) is not None

    # -- observation (recorded state — NOT the user-owned method) ----------

    async def record_observation(
        self,
        principal: Principal,
        target_kind: str,
        target_id: str,
        observed: Mapping[str, object],
        *,
        actor: GtsActor = "user",
        connection: Optional["asyncpg.Connection"] = None,
    ) -> Dict[str, object]:
        """Record the latest *observed state* of a goal/task and return it.

        This is **data**, not the evaluation *method*: it is the result of
        carrying out the observation prompt (reading internal state, an external
        db/api/mcp tool, or the user's answer). Like recording metrics/progress
        it is allowed for both the user and the runtime agent — the agent is
        only refused on the user-owned method (observation prompt / measurable
        flag / scoring prompt), never on recording what was observed.

        For a **measurable** node the observed state feeds the scoring seam (a
        numeric ``score`` key drives the default evaluator); for a
        **non-measurable** node it holds the qualitative ``status``. Stored on
        the additive ``observed_state`` column — never in the locked method.
        """
        if target_kind not in _TARGET_KINDS:
            raise ValueError(f"Unknown evaluation target_kind: {target_kind!r}")
        payload = {str(key): value for key, value in dict(observed).items()}
        own = connection is None
        conn = connection or await self._connect()
        try:
            node = await self._require_target_node(
                principal, target_kind, target_id, conn
            )
            # Recording observed state IS advancing progress (FG-19): allowed
            # for the item owner and its single active assignee, but not a
            # read-only watcher.
            await self._require_progress_authority(
                principal,
                target_kind,
                node,
                conn,
                actor=actor,
                action="record an observation",
            )
            table = GOALS_TABLE if target_kind == "goal" else TASKS_TABLE
            await conn.execute(
                f"UPDATE {table} SET observed_state = $2::jsonb, updated_at = NOW() "
                f"WHERE id = $1",
                target_id,
                json.dumps(payload, sort_keys=True),
            )
            self._audit(
                actor=actor,
                actor_user_id=principal.user_id,
                action="record_observation",
                target_kind=target_kind,
                target_ref=target_id,
                decision="recorded",
                reason=f"recorded observed state ({actor})",
            )
            return payload
        finally:
            if own:
                await conn.close()

    async def get_observation(
        self,
        principal: Principal,
        target_kind: str,
        target_id: str,
        *,
        connection: Optional["asyncpg.Connection"] = None,
    ) -> Dict[str, object]:
        """Return the recorded observed state for a readable goal/task ({} if none)."""
        if target_kind not in _TARGET_KINDS:
            raise ValueError(f"Unknown evaluation target_kind: {target_kind!r}")
        own = connection is None
        conn = connection or await self._connect()
        try:
            if not await self._target_readable(
                principal, target_kind, target_id, conn
            ):
                return {}
            return await self._observed_state(conn, target_kind, target_id)
        finally:
            if own:
                await conn.close()

    async def _observed_state(
        self,
        conn: "asyncpg.Connection",
        target_kind: str,
        target_id: str,
    ) -> Dict[str, object]:
        table = GOALS_TABLE if target_kind == "goal" else TASKS_TABLE
        raw = await conn.fetchval(
            f"SELECT observed_state FROM {table} WHERE id = $1",
            target_id,
        )
        return _load_method(raw)

    # -- assignment lifecycle + per-item grants (FG-19) --------------------

    async def assign(
        self,
        principal: Principal,
        item_kind: str,
        item_id: str,
        assignee_user_id: str,
        *,
        grant: str = GRANT_ASSIGNEE,
        actor: GtsActor = "user",
        require_acceptance: bool = False,
        approval_callback: Optional[ApprovalCallback] = None,
        connection: Optional["asyncpg.Connection"] = None,
    ) -> ItemGrant:
        """Grant a task or sub-goal to ``assignee_user_id`` (FG-19).

        A **per-item grant** — it shares this one item, never the owner's other
        private GTS, and does not touch the item's ``visibility``. Only the item
        owner (or the owner role) may assign; a top-level goal is not assignable.
        ``grant`` is ``assignee`` (single, may progress) or ``watcher``
        (read-only). A user-initiated assignment auto-accepts by default (still
        declinable); ``require_acceptance`` (and every agent-initiated
        assignment) leaves it ``pending`` until the grantee accepts. An
        agent-initiated assignment must clear C6 approval first. Emits a C5/C8
        audit row and a C6 notification.
        """
        if item_kind not in GRANT_ITEM_KINDS:
            raise ValueError(f"Unknown grant item_kind: {item_kind!r}")
        if grant not in GRANT_TYPES:
            raise ValueError(f"Unknown grant type: {grant!r}")
        own = connection is None
        conn = connection or await self._connect()
        try:
            node = await self._require_target_node(
                principal, item_kind, item_id, conn
            )
            await self._authorize_assignment(
                principal,
                node,
                item_kind=item_kind,
                item_id=item_id,
                assignee_user_id=assignee_user_id,
                actor=actor,
                action="assign an item",
                approval_callback=approval_callback,
            )
            if grant == GRANT_ASSIGNEE:
                existing = await self._active_assignee(conn, item_kind, item_id)
                if existing is not None and existing != assignee_user_id:
                    raise GtsAssignmentError(
                        f"{item_kind} {item_id} already has an active assignee "
                        f"({existing!r}); use reassign() to change it."
                    )
            status = "pending" if (require_acceptance or actor == "agent") else "accepted"
            record = await self._write_grant(
                conn,
                item_kind=item_kind,
                item_id=item_id,
                user_id=assignee_user_id,
                grant=grant,
                granted_by=principal.user_id,
                status=status,
            )
            if grant == GRANT_ASSIGNEE:
                await self._sync_assignee_column(conn, item_kind, item_id)
            self._audit(
                actor=actor,
                actor_user_id=principal.user_id,
                action="assign",
                target_kind=item_kind,
                target_ref=item_id,
                decision="recorded",
                reason=(
                    f"{principal.user_id} assigned {item_kind} {item_id} to "
                    f"{assignee_user_id} as {grant} (status={status}, {actor})"
                ),
            )
            self._notify_assignment(record, action="assign", by_user_id=principal.user_id)
            return record
        finally:
            if own:
                await conn.close()

    async def reassign(
        self,
        principal: Principal,
        item_kind: str,
        item_id: str,
        new_assignee_user_id: str,
        *,
        actor: GtsActor = "user",
        require_acceptance: bool = False,
        approval_callback: Optional[ApprovalCallback] = None,
        connection: Optional["asyncpg.Connection"] = None,
    ) -> ItemGrant:
        """Move the single assignee grant to ``new_assignee_user_id`` (FG-19).

        Owner-only (an assignee may not reassign). Revokes the current active
        assignee grant (audited) then assigns the new one, preserving the
        single-active-assignee invariant. Agent-initiated reassignment clears
        C6 approval first.
        """
        if item_kind not in GRANT_ITEM_KINDS:
            raise ValueError(f"Unknown grant item_kind: {item_kind!r}")
        own = connection is None
        conn = connection or await self._connect()
        try:
            async with conn.transaction():
                node = await self._require_target_node(
                    principal, item_kind, item_id, conn
                )
                await self._authorize_assignment(
                    principal,
                    node,
                    item_kind=item_kind,
                    item_id=item_id,
                    assignee_user_id=new_assignee_user_id,
                    actor=actor,
                    action="reassign an item",
                    approval_callback=approval_callback,
                )
                current = await self._active_assignee(conn, item_kind, item_id)
                if current is not None and current != new_assignee_user_id:
                    await conn.execute(
                        f"""
                        UPDATE {ITEM_GRANTS_TABLE}
                        SET status = 'revoked', updated_at = NOW()
                        WHERE item_kind = $1 AND item_id = $2
                          AND grant_type = 'assignee' AND user_id = $3
                          AND status = ANY($4::text[])
                        """,
                        item_kind,
                        item_id,
                        current,
                        list(GRANT_ACTIVE_STATUSES),
                    )
                    self._audit(
                        actor=actor,
                        actor_user_id=principal.user_id,
                        action="reassign",
                        target_kind=item_kind,
                        target_ref=item_id,
                        decision="recorded",
                        reason=(
                            f"{principal.user_id} revoked assignee {current} of "
                            f"{item_kind} {item_id} before reassigning"
                        ),
                    )
                status = (
                    "pending" if (require_acceptance or actor == "agent") else "accepted"
                )
                record = await self._write_grant(
                    conn,
                    item_kind=item_kind,
                    item_id=item_id,
                    user_id=new_assignee_user_id,
                    grant=GRANT_ASSIGNEE,
                    granted_by=principal.user_id,
                    status=status,
                )
                await self._sync_assignee_column(conn, item_kind, item_id)
                self._audit(
                    actor=actor,
                    actor_user_id=principal.user_id,
                    action="reassign",
                    target_kind=item_kind,
                    target_ref=item_id,
                    decision="recorded",
                    reason=(
                        f"{principal.user_id} reassigned {item_kind} {item_id} to "
                        f"{new_assignee_user_id} (status={status}, {actor})"
                    ),
                )
            self._notify_assignment(record, action="reassign", by_user_id=principal.user_id)
            return record
        finally:
            if own:
                await conn.close()

    async def accept_assignment(
        self,
        principal: Principal,
        grant_id: str,
        *,
        connection: Optional["asyncpg.Connection"] = None,
    ) -> ItemGrant:
        """Accept a grant addressed to ``principal`` (grantee-only, FG-19)."""
        return await self._resolve_grant(
            principal, grant_id, new_status="accepted", action="accept"
        )

    async def decline_assignment(
        self,
        principal: Principal,
        grant_id: str,
        *,
        connection: Optional["asyncpg.Connection"] = None,
    ) -> ItemGrant:
        """Decline a grant addressed to ``principal`` (grantee-only, FG-19)."""
        return await self._resolve_grant(
            principal, grant_id, new_status="declined", action="decline"
        )

    async def revoke_grant(
        self,
        principal: Principal,
        grant_id: str,
        *,
        actor: GtsActor = "user",
        connection: Optional["asyncpg.Connection"] = None,
    ) -> ItemGrant:
        """Revoke a grant — the item owner (or owner role) only (FG-19)."""
        own = connection is None
        conn = connection or await self._connect()
        try:
            grant = await self._grant_by_id(conn, grant_id)
            if grant is None:
                raise GtsAssignmentError(f"Grant {grant_id} not found")
            node = await self._require_target_node(
                principal, grant.item_kind, grant.item_id, conn
            )
            self._require_item_owner(
                principal,
                node.owner_user_id,
                actor=actor,
                action="revoke a grant",
                target_kind=grant.item_kind,
                target_ref=grant.item_id,
            )
            updated = await self._set_grant_status(conn, grant_id, "revoked")
            if updated.grant == GRANT_ASSIGNEE:
                await self._sync_assignee_column(
                    conn, updated.item_kind, updated.item_id
                )
            self._audit(
                actor=actor,
                actor_user_id=principal.user_id,
                action="revoke",
                target_kind=updated.item_kind,
                target_ref=updated.item_id,
                decision="recorded",
                reason=(
                    f"{principal.user_id} revoked {updated.grant} grant of "
                    f"{updated.item_kind} {updated.item_id} from {updated.user_id}"
                ),
            )
            self._notify_assignment(updated, action="revoke", by_user_id=principal.user_id)
            return updated
        finally:
            if own:
                await conn.close()

    async def list_grants(
        self,
        principal: Principal,
        item_kind: str,
        item_id: str,
        *,
        connection: Optional["asyncpg.Connection"] = None,
    ) -> List[ItemGrant]:
        """List grants on a readable item (owner/grantee scope via item_grants RLS)."""
        if item_kind not in GRANT_ITEM_KINDS:
            raise ValueError(f"Unknown grant item_kind: {item_kind!r}")
        own = connection is None
        conn = connection or await self._connect()
        try:
            if await self._target_node_or_none(
                principal, item_kind, item_id, conn
            ) is None:
                return []
            rows = await conn.fetch(
                f"""
                SELECT id, item_kind, item_id, user_id, grant_type,
                       granted_by, status
                FROM {ITEM_GRANTS_TABLE}
                WHERE item_kind = $1 AND item_id = $2
                ORDER BY created_at ASC
                """,
                item_kind,
                item_id,
            )
            return [_row_to_item_grant(r) for r in rows]
        finally:
            if own:
                await conn.close()

    async def advance_task(
        self,
        principal: Principal,
        task_id: str,
        to_state: str,
        *,
        actor: GtsActor = "user",
        connection: Optional["asyncpg.Connection"] = None,
    ) -> GtsTask:
        """Advance a task's progress state — owner or active assignee (FG-19).

        A grant-aware wrapper of the FG-06 progress machine: it authorizes the
        item owner or its single active assignee (a watcher is refused), then
        applies the validated transition and records it. Progress feeds the
        automatic FG-18 score rollup; it never hand-sets a score.
        """
        own = connection is None
        conn = connection or await self._connect()
        try:
            node = await self._get_task_node(principal, task_id, conn)
            if node is None:
                raise PermissionError(
                    f"Task {task_id} not found or not visible to {principal.user_id}"
                )
            await self._require_progress_authority(
                principal, "task", node, conn, actor=actor, action="advance a task"
            )
            async with conn.transaction():
                row = await conn.fetchrow(
                    f"""
                    SELECT current_state, trigger_state, completion_state
                    FROM {TASKS_TABLE} WHERE id = $1 FOR UPDATE
                    """,
                    task_id,
                )
                if row is None:
                    raise PermissionError(f"Task {task_id} not found")
                progress_rows = await conn.fetch(
                    """
                    SELECT name FROM task_progress_states
                    WHERE task_id = $1 ORDER BY ordinal
                    """,
                    task_id,
                )
                validate_progress_transition(
                    tuple(str(item["name"]) for item in progress_rows),
                    str(row["current_state"]),
                    to_state,
                )
                status = _status_for_state(
                    to_state,
                    trigger_state=str(row["trigger_state"]),
                    completion_state=str(row["completion_state"]),
                )
                updated = await conn.fetchrow(
                    f"""
                    UPDATE {TASKS_TABLE}
                    SET current_state = $2, status = $3, updated_at = NOW()
                    WHERE id = $1
                    RETURNING {_TASK_COLUMNS}
                    """,
                    task_id,
                    to_state,
                    status,
                )
                await conn.execute(
                    """
                    INSERT INTO task_transitions
                        (task_id, from_state, to_state, actor)
                    VALUES ($1, $2, $3, $4)
                    """,
                    task_id,
                    row["current_state"],
                    to_state,
                    principal.user_id,
                )
            self._audit(
                actor=actor,
                actor_user_id=principal.user_id,
                action="progress",
                target_kind="task",
                target_ref=task_id,
                decision="recorded",
                reason=(
                    f"{principal.user_id} advanced task {task_id} to {to_state} "
                    f"({actor})"
                ),
            )
            return _row_to_task_node(updated)
        finally:
            if own:
                await conn.close()

    # -- grant internals ---------------------------------------------------

    async def _authorize_assignment(
        self,
        principal: Principal,
        node: "GtsGoal | GtsTask",
        *,
        item_kind: str,
        item_id: str,
        assignee_user_id: str,
        actor: GtsActor,
        action: str,
        approval_callback: Optional[ApprovalCallback],
    ) -> None:
        """Shared assign/reassign gate: owner-only + top-level guard + C6."""
        self._require_item_owner(
            principal,
            node.owner_user_id,
            actor=actor,
            action=action,
            target_kind=item_kind,
            target_ref=item_id,
        )
        if item_kind == "goal":
            if not isinstance(node, GtsGoal):  # pragma: no cover - defensive
                raise GtsAssignmentError(f"Item {item_id} is not a goal")
            if node.level == "top":
                raise GtsAssignmentError(
                    "Top-level goals are not assignable (FG-19); only tasks and "
                    "sub-goals can be assigned."
                )
        if actor == "agent":
            self._require_assignment_approval(
                principal,
                item_kind=item_kind,
                item_id=item_id,
                assignee_user_id=assignee_user_id,
                approval_callback=approval_callback,
            )

    def _require_assignment_approval(
        self,
        principal: Principal,
        *,
        item_kind: str,
        item_id: str,
        assignee_user_id: str,
        approval_callback: Optional[ApprovalCallback],
    ) -> None:
        """C6 gate for agent-initiated assignment (reuses ``evaluate_approval``)."""
        decision = evaluate_approval(
            self._consent_policy,
            reversible=True,
            command=f"gts.assign {item_kind}:{item_id} -> {assignee_user_id}",
            description=(
                f"Agent-initiated cross-user GTS assignment of {item_kind} "
                f"{item_id} to {assignee_user_id}"
            ),
            approval_callback=approval_callback,
        )
        if not decision.approved:
            self._audit(
                actor="agent",
                actor_user_id=principal.user_id,
                action="assign",
                target_kind=item_kind,
                target_ref=item_id,
                decision="refused",
                reason=(
                    f"agent-initiated assignment to {assignee_user_id} denied by "
                    f"C6 ({decision.reason})"
                ),
            )
            raise GtsAuthorityError(
                "Refused: agent-initiated assignment requires C6 approval and the "
                "request was not approved. This attempt has been audited."
            )

    async def _resolve_grant(
        self,
        principal: Principal,
        grant_id: str,
        *,
        new_status: str,
        action: str,
        connection: Optional["asyncpg.Connection"] = None,
    ) -> ItemGrant:
        """Grantee-only accept/decline of a grant addressed to ``principal``."""
        own = connection is None
        conn = connection or await self._connect()
        try:
            grant = await self._grant_by_id(conn, grant_id)
            if grant is None:
                raise GtsAssignmentError(f"Grant {grant_id} not found")
            if not (principal.is_owner or grant.user_id == principal.user_id):
                self._audit(
                    actor="user",
                    actor_user_id=principal.user_id,
                    action=action,
                    target_kind=grant.item_kind,
                    target_ref=grant.item_id,
                    decision="refused",
                    reason=(
                        f"only the grantee ({grant.user_id}) may {action} this "
                        f"grant"
                    ),
                )
                raise GtsAuthorityError(
                    f"Refused: only the grantee may {action} this grant (FG-19). "
                    "This attempt has been audited."
                )
            if grant.status not in GRANT_ACTIVE_STATUSES:
                raise GtsAssignmentError(
                    f"Grant {grant_id} is {grant.status}; cannot {action}"
                )
            updated = await self._set_grant_status(conn, grant_id, new_status)
            if updated.grant == GRANT_ASSIGNEE:
                await self._sync_assignee_column(
                    conn, updated.item_kind, updated.item_id
                )
            self._audit(
                actor="user",
                actor_user_id=principal.user_id,
                action=action,
                target_kind=updated.item_kind,
                target_ref=updated.item_id,
                decision="recorded",
                reason=(
                    f"{principal.user_id} {action}ed {updated.grant} grant of "
                    f"{updated.item_kind} {updated.item_id} (granted by "
                    f"{updated.granted_by})"
                ),
            )
            self._notify_assignment(updated, action=action, by_user_id=principal.user_id)
            return updated
        finally:
            if own:
                await conn.close()

    async def _write_grant(
        self,
        conn: "asyncpg.Connection",
        *,
        item_kind: str,
        item_id: str,
        user_id: str,
        grant: str,
        granted_by: str,
        status: str,
    ) -> ItemGrant:
        row = await conn.fetchrow(
            f"""
            INSERT INTO {ITEM_GRANTS_TABLE}
                (item_kind, item_id, user_id, grant_type, granted_by, status)
            VALUES ($1, $2, $3, $4, $5, $6)
            ON CONFLICT (item_kind, item_id, user_id) DO UPDATE SET
                grant_type = EXCLUDED.grant_type,
                granted_by = EXCLUDED.granted_by,
                status = EXCLUDED.status,
                updated_at = NOW()
            RETURNING id, item_kind, item_id, user_id, grant_type,
                      granted_by, status
            """,
            item_kind,
            item_id,
            user_id,
            grant,
            granted_by,
            status,
        )
        return _row_to_item_grant(row)

    async def _set_grant_status(
        self,
        conn: "asyncpg.Connection",
        grant_id: str,
        status: str,
    ) -> ItemGrant:
        row = await conn.fetchrow(
            f"""
            UPDATE {ITEM_GRANTS_TABLE}
            SET status = $2, updated_at = NOW()
            WHERE id = $1
            RETURNING id, item_kind, item_id, user_id, grant_type,
                      granted_by, status
            """,
            grant_id,
            status,
        )
        if row is None:
            raise GtsAssignmentError(f"Grant {grant_id} not found")
        return _row_to_item_grant(row)

    async def _grant_by_id(
        self,
        conn: "asyncpg.Connection",
        grant_id: str,
    ) -> Optional[ItemGrant]:
        row = await conn.fetchrow(
            f"""
            SELECT id, item_kind, item_id, user_id, grant_type,
                   granted_by, status
            FROM {ITEM_GRANTS_TABLE} WHERE id = $1
            """,
            grant_id,
        )
        return _row_to_item_grant(row) if row is not None else None

    async def _active_assignee(
        self,
        conn: "asyncpg.Connection",
        item_kind: str,
        item_id: str,
    ) -> Optional[str]:
        val = await conn.fetchval(
            f"""
            SELECT user_id FROM {ITEM_GRANTS_TABLE}
            WHERE item_kind = $1 AND item_id = $2
              AND grant_type = 'assignee' AND status = ANY($3::text[])
            LIMIT 1
            """,
            item_kind,
            item_id,
            list(GRANT_ACTIVE_STATUSES),
        )
        return str(val) if val is not None else None

    async def _sync_assignee_column(
        self,
        conn: "asyncpg.Connection",
        item_kind: str,
        item_id: str,
    ) -> None:
        """Mirror the active assignee onto the row's ``assignee_user_id``."""
        table = GOALS_TABLE if item_kind == "goal" else TASKS_TABLE
        assignee = await self._active_assignee(conn, item_kind, item_id)
        await conn.execute(
            f"UPDATE {table} SET assignee_user_id = $2, updated_at = NOW() "
            f"WHERE id = $1",
            item_id,
            assignee,
        )

    async def _require_progress_authority(
        self,
        principal: Principal,
        item_kind: str,
        node: "GtsGoal | GtsTask",
        conn: "asyncpg.Connection",
        *,
        actor: GtsActor,
        action: str,
    ) -> None:
        """Allow progress by the item owner or its single active assignee.

        A read-only watcher — or any non-grantee — is refused and audited. This
        is the one authority seam an assignee is *granted* (unlike content /
        evaluation-method / assignment edits, which stay owner-only).
        """
        if self._is_item_owner(principal, node.owner_user_id):
            return
        assignee = await self._active_assignee(conn, item_kind, node.id)
        if assignee == principal.user_id:
            return
        self._audit(
            actor=actor,
            actor_user_id=principal.user_id,
            action=action,
            target_kind=item_kind,
            target_ref=node.id,
            decision="refused",
            reason=(
                f"{action} on {item_kind} {node.id} is limited to the owner or "
                "active assignee (a watcher/non-grantee may not, FG-19)"
            ),
        )
        raise GtsAuthorityError(
            f"Refused: only the item owner or its assignee may {action}; a "
            "watcher/non-grantee may not (FG-19). This attempt has been audited."
        )

    async def _target_node_or_none(
        self,
        principal: Principal,
        item_kind: str,
        item_id: str,
        conn: "asyncpg.Connection",
    ) -> "GtsGoal | GtsTask | None":
        if item_kind == "goal":
            return await self._get_goal_node(principal, item_id, conn)
        return await self._get_task_node(principal, item_id, conn)

    def _notify_assignment(
        self,
        grant: ItemGrant,
        *,
        action: str,
        by_user_id: str,
    ) -> None:
        """Best-effort C6 assignment notification (FG-19); never raises."""
        if self._notifier is None:
            return
        payload = {
            "kind": "gts_assignment",
            "action": action,
            "item_kind": grant.item_kind,
            "item_id": grant.item_id,
            "grant": grant.grant,
            "assignee_user_id": grant.user_id,
            "status": grant.status,
            "by_user_id": by_user_id,
        }
        try:
            self._notifier(payload)
        except Exception:
            logger.exception("GTS assignment notifier failed (non-fatal)")

    # -- owner cross-user browse (FG-19) -----------------------------------

    async def list_goals_for_user(
        self,
        principal: Principal,
        subject_user_id: str,
        *,
        top_level_only: bool = False,
        connection: Optional["asyncpg.Connection"] = None,
    ) -> List[GtsGoal]:
        """Owner-only cross-user browse: list ``subject_user_id``'s goals.

        The owner role sees every user's GTS (the existing C2 owner bypass);
        this narrows that view to one subject user for the owner cross-user
        browse surface (rendered by FG-17). A non-owner is refused — it may
        never enumerate another user's private GTS.
        """
        if not principal.is_owner:
            raise GtsAuthorityError(
                "Refused: cross-user GTS browse is owner-only (FG-19)."
            )
        clauses = ["owner_user_id = $1"]
        if top_level_only:
            clauses.append("parent_goal_id IS NULL")
        where = " AND ".join(clauses)
        own = connection is None
        conn = connection or await self._connect()
        try:
            rows = await conn.fetch(
                f"""
                SELECT {_GOAL_COLUMNS} FROM {GOALS_TABLE}
                WHERE {where}
                ORDER BY created_at ASC
                """,
                subject_user_id,
            )
            return [_row_to_goal_node(r) for r in rows]
        finally:
            if own:
                await conn.close()

    # -- scoring (always computed; clamped; rolled up) ---------------------

    async def recompute_goal_score(
        self,
        principal: Principal,
        goal_id: str,
        *,
        connection: Optional["asyncpg.Connection"] = None,
    ) -> Optional[float]:
        """Recompute + persist a goal's score bottom-up and return it.

        A goal with sub-goals rolls its **measurable** children's (recomputed)
        scores up by priority weight; a measurable leaf goal is scored either
        from a user-supplied scoring prompt over its observed state (the new
        observe/measure model) or, for a legacy method, from its FG-04 metrics
        under the user's evaluation-method weights. A **non-measurable** goal
        has no auto-score — it returns (and persists) ``None`` and is excluded
        from any parent rollup. Any computed score is always clamped to
        ``[0, 100]`` and never hand-set.
        """
        own = connection is None
        conn = connection or await self._connect()
        try:
            return await self._recompute_goal_score(principal, goal_id, conn)
        finally:
            if own:
                await conn.close()

    async def _recompute_goal_score(
        self,
        principal: Principal,
        goal_id: str,
        conn: "asyncpg.Connection",
    ) -> Optional[float]:
        goal = await self._get_goal_node(principal, goal_id, conn)
        if goal is None:
            raise PermissionError(
                f"Goal {goal_id} not found or not visible to {principal.user_id}"
            )
        children = await self.list_goals(
            principal, parent_goal_id=goal_id, connection=conn
        )
        if children:
            # Roll up only the measurable children (those with a real score).
            child_scores: List[Tuple[float, str]] = []
            for child in children:
                child_score = await self._recompute_goal_score(
                    principal, child.id, conn
                )
                if child_score is not None:
                    child_scores.append((child_score, child.priority))
            score = rollup_score(child_scores) if child_scores else None
        else:
            method = await self._method_json(conn, "goal", goal_id)
            if not method_is_measurable(method):
                score = None
            else:
                scoring_prompt = method_scoring_prompt(method)
                observation = parse_observation(method)
                if scoring_prompt and observation is not None:
                    # New observe/measure path: run the scoring prompt over the
                    # recorded observed state via the (clamped) evaluator seam.
                    observed = await self._observed_state(conn, "goal", goal_id)
                    raw = self._score_evaluator(
                        ScoringRequest(
                            target_kind="goal",
                            target_id=goal_id,
                            observation=observation,
                            scoring_prompt=scoring_prompt,
                            observed_state=observed,
                            mode=self.mode,
                        )
                    )
                    score = clamp_score(raw) if raw is not None else None
                else:
                    # Legacy path: compute from FG-04 metrics under weights.
                    metrics = await self._goals.list_metrics(
                        principal, goal_id, connection=conn
                    )
                    weights = _method_weights(method)
                    score = score_from_metrics(metrics, weights=weights)
        await conn.execute(
            f"UPDATE {GOALS_TABLE} SET score = $2 WHERE id = $1",
            goal_id,
            score,
        )
        return score

    async def recompute_task_score(
        self,
        principal: Principal,
        task_id: str,
        *,
        connection: Optional["asyncpg.Connection"] = None,
    ) -> Optional[float]:
        """Recompute + persist a task's score bottom-up and return it.

        A task with sub-tasks rolls its **measurable** children's scores up by
        priority weight; a measurable leaf task is scored from a user scoring
        prompt over its observed state, or (legacy) from its FG-06 progress
        state under any user-supplied ``state_scores`` rubric. A
        **non-measurable** task has no auto-score (returns ``None`` and is
        excluded from rollup). Any computed score is clamped; never hand-set.
        """
        own = connection is None
        conn = connection or await self._connect()
        try:
            return await self._recompute_task_score(principal, task_id, conn)
        finally:
            if own:
                await conn.close()

    async def _recompute_task_score(
        self,
        principal: Principal,
        task_id: str,
        conn: "asyncpg.Connection",
    ) -> Optional[float]:
        task = await self._get_task_node(principal, task_id, conn)
        if task is None:
            raise PermissionError(
                f"Task {task_id} not found or not visible to {principal.user_id}"
            )
        children = await conn.fetch(
            f"SELECT id FROM {TASKS_TABLE} WHERE parent_task_id = $1",
            task_id,
        )
        if children:
            child_scores: List[Tuple[float, str]] = []
            for row in children:
                child = await self._get_task_node(principal, str(row["id"]), conn)
                if child is None:
                    continue
                child_score = await self._recompute_task_score(
                    principal, child.id, conn
                )
                if child_score is not None:
                    child_scores.append((child_score, child.priority))
            score = rollup_score(child_scores) if child_scores else None
        else:
            method = await self._method_json(conn, "task", task_id)
            if not method_is_measurable(method):
                score = None
            else:
                scoring_prompt = method_scoring_prompt(method)
                observation = parse_observation(method)
                if scoring_prompt and observation is not None:
                    observed = await self._observed_state(conn, "task", task_id)
                    raw = self._score_evaluator(
                        ScoringRequest(
                            target_kind="task",
                            target_id=task_id,
                            observation=observation,
                            scoring_prompt=scoring_prompt,
                            observed_state=observed,
                            mode=self.mode,
                        )
                    )
                    score = clamp_score(raw) if raw is not None else None
                else:
                    progress_states = await self._tasks.progress_states(
                        principal, task_id, connection=conn
                    )
                    state_scores = _method_state_scores(method)
                    score = score_from_progress(
                        progress_states,
                        task.current_state,
                        task.completion_state,
                        status=task.status,
                        state_scores=state_scores,
                    )
        await conn.execute(
            f"UPDATE {TASKS_TABLE} SET score = $2 WHERE id = $1",
            task_id,
            score,
        )
        return score

    async def goal_verdict(
        self,
        principal: Principal,
        goal_id: str,
        *,
        connection: Optional["asyncpg.Connection"] = None,
    ) -> Tuple[str, str]:
        """Reuse FG-04 ``verdict_for_metrics`` over a goal's stored metrics."""
        own = connection is None
        conn = connection or await self._connect()
        try:
            metrics = await self._goals.list_metrics(
                principal, goal_id, connection=conn
            )
            return verdict_for_metrics(metrics)
        finally:
            if own:
                await conn.close()

    async def _method_json(
        self,
        conn: "asyncpg.Connection",
        target_kind: str,
        target_id: str,
    ) -> Mapping[str, object]:
        raw = await conn.fetchval(
            f"""
            SELECT method_json FROM {EVALUATION_METHODS_TABLE}
            WHERE target_kind = $1 AND target_id = $2
            """,
            target_kind,
            target_id,
        )
        return _load_method(raw)


# ---------------------------------------------------------------------------
# Row mappers + column lists
# ---------------------------------------------------------------------------

_GOAL_COLUMNS = (
    "id, owner_user_id, visibility, title, priority, status, "
    "level, parent_goal_id, score, evaluation_method_ref, assignee_user_id"
)

_TASK_COLUMNS = (
    "id, owner_user_id, visibility, title, priority, status, "
    "current_state, completion_state, parent_task_id, score, "
    "evaluation_method_ref, assignee_user_id"
)


def _goal_columns_prefixed(alias: str) -> str:
    return ", ".join(f"{alias}.{col.strip()}" for col in _GOAL_COLUMNS.split(","))


def _row_to_goal_node(row: "asyncpg.Record") -> GtsGoal:
    score = row["score"]
    parent = row["parent_goal_id"]
    method_ref = row["evaluation_method_ref"]
    assignee = row["assignee_user_id"]
    return GtsGoal(
        id=str(row["id"]),
        owner_user_id=str(row["owner_user_id"]),
        visibility=str(row["visibility"]),
        title=str(row["title"]),
        priority=str(row["priority"]),
        status=str(row["status"]),
        level=str(row["level"]),
        parent_goal_id=str(parent) if parent is not None else None,
        score=float(score) if score is not None else None,
        evaluation_method_ref=str(method_ref) if method_ref is not None else None,
        assignee_user_id=str(assignee) if assignee is not None else None,
    )


def _row_to_task_node(row: "asyncpg.Record") -> GtsTask:
    score = row["score"]
    parent = row["parent_task_id"]
    method_ref = row["evaluation_method_ref"]
    assignee = row["assignee_user_id"]
    return GtsTask(
        id=str(row["id"]),
        owner_user_id=str(row["owner_user_id"]),
        visibility=str(row["visibility"]),
        title=str(row["title"]),
        priority=str(row["priority"]),
        status=str(row["status"]),
        current_state=str(row["current_state"]),
        completion_state=str(row["completion_state"]),
        parent_task_id=str(parent) if parent is not None else None,
        score=float(score) if score is not None else None,
        evaluation_method_ref=str(method_ref) if method_ref is not None else None,
        assignee_user_id=str(assignee) if assignee is not None else None,
    )


def _row_to_item_grant(row: "asyncpg.Record") -> ItemGrant:
    return ItemGrant(
        id=str(row["id"]),
        item_kind=str(row["item_kind"]),
        item_id=str(row["item_id"]),
        user_id=str(row["user_id"]),
        grant=str(row["grant_type"]),
        granted_by=str(row["granted_by"]),
        status=str(row["status"]),
    )


def _row_to_skill(row: "asyncpg.Record") -> SkillNode:
    return SkillNode(
        id=str(row["id"]),
        owner_user_id=str(row["owner_user_id"]),
        visibility=str(row["visibility"]),
        name=str(row["name"]),
        skill_ref=str(row["skill_ref"]),
    )


def _row_to_method(row: "asyncpg.Record") -> EvaluationMethod:
    return EvaluationMethod(
        id=str(row["id"]),
        target_kind=str(row["target_kind"]),
        target_id=str(row["target_id"]),
        method=_load_method(row["method_json"]),
        set_by_user_id=str(row["set_by_user_id"]),
        locked=bool(row["locked"]),
    )


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _resolve_visibility(principal: Principal, visibility: Optional[str]) -> str:
    """Map a ``shared``/``private`` intent onto a concrete C2 tag (own private)."""
    if visibility is None or visibility == "private":
        return principal.private_visibility
    return normalize_visibility(visibility)


def _load_method(raw: object) -> Dict[str, object]:
    if raw is None:
        return {}
    if isinstance(raw, (str, bytes, bytearray)):
        try:
            raw = json.loads(raw)
        except (ValueError, TypeError):
            return {}
    if isinstance(raw, Mapping):
        return {str(key): value for key, value in raw.items()}
    return {}


def _as_float(value: object) -> Optional[float]:
    """Coerce a JSON scalar to ``float``; return ``None`` for anything else."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _method_weights(method: Mapping[str, object]) -> Dict[str, float]:
    weights = method.get("weights")
    if not isinstance(weights, Mapping):
        return {}
    resolved: Dict[str, float] = {}
    for name, value in weights.items():
        number = _as_float(value)
        if number is not None:
            resolved[str(name)] = number
    return resolved


def _method_state_scores(method: Mapping[str, object]) -> Dict[str, float]:
    scores = method.get("state_scores")
    if not isinstance(scores, Mapping):
        return {}
    resolved: Dict[str, float] = {}
    for state, value in scores.items():
        number = _as_float(value)
        if number is not None:
            resolved[str(state)] = number
    return resolved


def gts_audit_log_path() -> Path:
    """Path of the durable, append-only local GTS-authority audit log (JSONL)."""
    try:
        from hermes_constants import get_hermes_home

        home = Path(get_hermes_home())
    except Exception:
        home = Path(os.path.expanduser("~/.hermes"))
    return home / "audit" / "gts_authority.jsonl"


def _append_local_audit(event: GtsAuditEvent) -> None:
    """Best-effort durable audit — always written, never raises into a mutation."""
    try:
        path = gts_audit_log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(event.as_dict(), sort_keys=True) + "\n")
    except Exception:
        pass


__all__ = [
    "ASSIGNMENT_ACTIONS",
    "EVALUATION_METHODS_TABLE",
    "GOAL_LEVELS",
    "GRANT_ASSIGNEE",
    "GRANT_WATCHER",
    "OBSERVATION_SOURCES",
    "SKILLS_TABLE",
    "TASK_GOALS_TABLE",
    "TASK_SKILLS_TABLE",
    "EvaluationMethod",
    "GtsActor",
    "GtsAssignmentError",
    "GtsAuditEvent",
    "GtsAuthorityError",
    "GtsCentre",
    "GtsCycleError",
    "GtsError",
    "GtsGoal",
    "GtsNotifier",
    "GtsScoreEvaluator",
    "GtsTask",
    "ItemGrant",
    "ObservationSource",
    "ObservationSpec",
    "ScoringRequest",
    "SkillNode",
    "clamp_score",
    "default_score_evaluator",
    "gts_audit_log_path",
    "method_is_measurable",
    "method_scoring_prompt",
    "parse_observation",
    "render_gts_block",
    "rollup_score",
    "score_from_metrics",
    "score_from_progress",
    "validate_evaluation_method",
]
