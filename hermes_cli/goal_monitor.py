"""Proactive measurement solicitation for the goal registry (FG-04 / 4.1).

When a registry goal has no measurable success criterion — or a metric has gone
stale — this monitor **asks the user for the missing measurement info** so
"achieved?" stays computed rather than guessed. Two hard rules shape it:

* **Prompt caching is sacred.** The monitor never mutates the system prompt or
  splices a tool in mid-conversation. It surfaces the ask through an injected
  ``ask_fn`` that appends a normal message (the same cache-safe pattern the
  Ralph loop uses for continuation prompts).
* **Contract C6 gates every ask.** Before asking, the monitor consults a
  consent / quiet-hours / rate-limit policy. Consent rides the merged C6
  surface (:func:`tools.approval.prompt_dangerous_approval`) via an injectable
  ``consent_fn``; quiet-hours and rate-limit thresholds come from
  ``config.yaml`` (never a new ``HERMES_*`` env var).

The fuller org-wide C6 policy object is owned by FG-10/FG-12 (not yet merged);
:class:`ProactiveAskPolicy` is a self-contained, config-driven implementation
of the same contract that a caller can swap for the shared policy once it
lands — the monitor only depends on the ``decide`` protocol.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Callable, List, Mapping, Optional, Protocol

from hermes_cli.access import Principal

if TYPE_CHECKING:
    from hermes_cli.goal_registry import GoalRegistryStore, MeasurementGap


#: A consent callback mirrors ``prompt_dangerous_approval``'s return contract:
#: ``"once"`` / ``"session"`` grant consent, anything else (``"deny"``) refuses.
ConsentFn = Callable[..., str]

#: An ask delivery callback appends a cache-safe message to the conversation.
AskFn = Callable[[Principal, str], None]


def _config_get(config: Mapping[str, object], *keys: str, default: object) -> object:
    node: object = config
    for key in keys:
        if not isinstance(node, dict):
            return default
        for candidate_key, candidate_value in node.items():
            if candidate_key == key:
                node = candidate_value
                break
        else:
            return default
    return node


def _as_dict(value: object) -> dict:
    return value if isinstance(value, dict) else {}


@dataclass(frozen=True)
class AskDecision:
    """Whether an ask may be sent now, and why (contract C6)."""

    allowed: bool
    reason: str  # "ok" | "disabled" | "quiet_hours" | "rate_limited" | "no_consent"


@dataclass(frozen=True)
class ProactiveAskConfig:
    """Config-driven C6 thresholds for proactive asks (from ``config.yaml``)."""

    enabled: bool = True
    quiet_start_hour: Optional[int] = None
    quiet_end_hour: Optional[int] = None
    min_interval_minutes: int = 0
    require_consent: bool = False

    @classmethod
    def from_config(cls, config: Optional[Mapping[str, object]] = None) -> "ProactiveAskConfig":
        if config is None:
            from hermes_cli.config import load_config_readonly

            config = load_config_readonly()
        block = _as_dict(_config_get(config, "goals", "proactive", default={}))
        quiet = _as_dict(block.get("quiet_hours"))
        return cls(
            enabled=bool(block.get("enabled", True)),
            quiet_start_hour=_maybe_hour(quiet.get("start")),
            quiet_end_hour=_maybe_hour(quiet.get("end")),
            min_interval_minutes=int(block.get("min_interval_minutes", 0) or 0),
            require_consent=bool(block.get("require_consent", False)),
        )


def _maybe_hour(value: object) -> Optional[int]:
    if not isinstance(value, (int, float, str)):
        return None
    try:
        hour = int(value)
    except (TypeError, ValueError):
        return None
    return hour % 24


class SupportsDecide(Protocol):
    """The one method the monitor needs from any C6 policy implementation."""

    def decide(
        self,
        *,
        now: datetime,
        last_ask_at: Optional[datetime],
        consent_fn: Optional[ConsentFn] = None,
        command: str = "",
        description: str = "",
    ) -> AskDecision: ...


class ProactiveAskPolicy:
    """Self-contained C6 gate: enabled → quiet-hours → rate-limit → consent."""

    def __init__(self, config: Optional[ProactiveAskConfig] = None) -> None:
        self.config = config or ProactiveAskConfig()

    def _in_quiet_hours(self, now: datetime) -> bool:
        start = self.config.quiet_start_hour
        end = self.config.quiet_end_hour
        if start is None or end is None or start == end:
            return False
        hour = now.hour
        if start < end:
            return start <= hour < end
        # Wraparound window, e.g. 22:00–08:00.
        return hour >= start or hour < end

    def decide(
        self,
        *,
        now: datetime,
        last_ask_at: Optional[datetime],
        consent_fn: Optional[ConsentFn] = None,
        command: str = "",
        description: str = "",
    ) -> AskDecision:
        if not self.config.enabled:
            return AskDecision(False, "disabled")
        if self._in_quiet_hours(now):
            return AskDecision(False, "quiet_hours")
        interval = self.config.min_interval_minutes
        if interval > 0 and last_ask_at is not None:
            if now - last_ask_at < timedelta(minutes=interval):
                return AskDecision(False, "rate_limited")
        if self.config.require_consent:
            if consent_fn is None:
                return AskDecision(False, "no_consent")
            choice = consent_fn(
                command or "hermes goal proactive-ask",
                description or "send a proactive measurement question to the user",
                allow_permanent=False,
            )
            if choice not in ("once", "session"):
                return AskDecision(False, "no_consent")
        return AskDecision(True, "ok")


def build_question(gap: "MeasurementGap") -> str:
    """Compose the user-facing measurement question for a gap."""
    title = gap.goal.title
    if gap.reason == "no_metric":
        return (
            f"Your goal “{title}” has no measurable success criterion yet. "
            "How should we measure whether it's achieved — what's the metric, "
            "its target value, and unit?"
        )
    if gap.reason == "unmeasured_target":
        return (
            f"Your goal “{title}” tracks “{gap.metric_name}” but has no target. "
            "What value would mean this metric is achieved?"
        )
    if gap.reason == "stale":
        return (
            f"It's been a while since we measured “{gap.metric_name}” for the "
            f"goal “{title}”. What's its current value?"
        )
    return (
        f"Could you share the latest measurement for the goal “{title}”?"
    )


@dataclass(frozen=True)
class AskOutcome:
    """Result of considering one measurement gap during a monitor run."""

    gap: "MeasurementGap"
    asked: bool
    reason: str
    question: Optional[str] = None


class ProactiveMeasurementMonitor:
    """Finds measurement gaps and asks the user — cache-safe, C6-gated."""

    def __init__(
        self,
        store: "GoalRegistryStore",
        *,
        policy: Optional[SupportsDecide] = None,
        ask_fn: Optional[AskFn] = None,
        consent_fn: Optional[ConsentFn] = None,
    ) -> None:
        self._store = store
        self._policy: SupportsDecide = policy or ProactiveAskPolicy(
            ProactiveAskConfig.from_config()
        )
        self._ask_fn = ask_fn
        self._consent_fn = consent_fn

    async def run_once(
        self,
        principal: Principal,
        *,
        now: Optional[datetime] = None,
        connection=None,
    ) -> List[AskOutcome]:
        """Consider every current measurement gap for ``principal`` once.

        For each gap the C6 policy decides whether an ask may go out now; when
        allowed, the question is delivered via ``ask_fn`` (an appended,
        cache-safe message) and logged for rate-limiting/audit. Subsequent gaps
        in the same run see the just-sent ask as ``last_ask_at`` so the
        configured rate limit is honoured within a run too.
        """
        now = now or datetime.now(timezone.utc)
        gaps = await self._store.measurement_gaps(
            principal, now=now, connection=connection
        )
        last_ask_at = await self._store.last_ask_at(
            principal.user_id, connection=connection
        )
        outcomes: List[AskOutcome] = []
        for gap in gaps:
            decision = self._policy.decide(
                now=now,
                last_ask_at=last_ask_at,
                consent_fn=self._consent_fn,
            )
            if not decision.allowed:
                outcomes.append(AskOutcome(gap, False, decision.reason))
                continue
            question = build_question(gap)
            if self._ask_fn is not None:
                self._ask_fn(principal, question)
            await self._store.log_ask(
                principal,
                gap.goal.id,
                question,
                metric_name=gap.metric_name,
                connection=connection,
            )
            outcomes.append(AskOutcome(gap, True, "asked", question))
            last_ask_at = now
        return outcomes


__all__ = [
    "AskDecision",
    "AskOutcome",
    "ProactiveAskConfig",
    "ProactiveAskPolicy",
    "ProactiveMeasurementMonitor",
    "build_question",
]
