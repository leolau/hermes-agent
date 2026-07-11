"""Contract C6 — the approval / consent policy surface.

A single, reusable policy object that wraps the existing dangerous-command
approval backbone (``tools/approval.py`` +
``tools/write_approval.py``) with three consent knobs configured in
``config.yaml`` (never via ``HERMES_*`` env vars):

* **quiet-hours** — a window during which silent auto-approval is suppressed
  and the user is prompted instead;
* **rate-limit** — a ceiling on how many changes may be auto-approved within a
  rolling window before the user is prompted again;
* **consent** — whether the user has granted standing consent to
  auto-approve *reversible* changes at all.

This is published as contract **C6** (FG-12, jointly with FG-10): change
approvals (FG-12), proactive messaging (4.1/6.1), and action gating all route
their "may I do this?" decision through :func:`evaluate_approval` so the
consent knobs live in exactly one place.

The one hard invariant: an **irreversible** action is *never* auto-approved —
it always requires explicit approval through ``prompt_dangerous_approval``,
regardless of consent / quiet-hours / rate-limit (D6).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

# Config section (config.yaml) — NOT an env var.
CONFIG_SECTION = "change_management"

# Approval choices returned by ``prompt_dangerous_approval`` that count as a
# grant. ``deny`` (and anything else) is a refusal.
_GRANTED_CHOICES = frozenset({"once", "session", "always"})

ApprovalCallback = Callable[..., str]


@dataclass(frozen=True)
class ConsentPolicy:
    """The C6 consent knobs, resolved from ``config.yaml``.

    All fields default to the safe, backwards-compatible values: consent is
    OFF (every change is prompted), no quiet-hours window, and no rate-limit.
    """

    auto_approve_reversible: bool = False
    quiet_hours_start: int | None = None
    quiet_hours_end: int | None = None
    rate_limit_max: int | None = None
    rate_limit_window_seconds: int = 3600

    def within_quiet_hours(self, now: datetime) -> bool:
        """Return whether ``now`` falls inside the configured quiet-hours.

        Supports windows that wrap past midnight (e.g. 22:00-07:00). Returns
        ``False`` when no window is configured.
        """
        start, end = self.quiet_hours_start, self.quiet_hours_end
        if start is None or end is None or start == end:
            return False
        hour = now.hour
        if start < end:
            return start <= hour < end
        # Wraps midnight: [start, 24) ∪ [0, end)
        return hour >= start or hour < end

    def is_rate_limited(self, recent_count: int) -> bool:
        """Return whether ``recent_count`` auto-approvals hits the ceiling."""
        if self.rate_limit_max is None:
            return False
        return recent_count >= self.rate_limit_max


@dataclass(frozen=True)
class ConsentDecision:
    """Outcome of a C6 approval evaluation."""

    approved: bool
    # How the decision was reached: "auto" (consent granted, silent),
    # "prompted" (the user was asked), or "denied" (the user refused).
    mode: str
    reason: str = ""


def load_consent_policy(config: dict[str, Any] | None = None) -> ConsentPolicy:
    """Resolve the :class:`ConsentPolicy` from ``config.yaml``.

    Accepts an explicit ``config`` mapping (tests / callers that already hold a
    config) or loads ``~/.hermes/config.yaml`` when omitted. Unknown / malformed
    values fall back to the safe defaults rather than raising.
    """
    from hermes_cli.config import cfg_get, load_config

    cfg = load_config() if config is None else config

    auto = bool(cfg_get(cfg, CONFIG_SECTION, "auto_approve_reversible", default=False))
    qh_start = _coerce_hour(cfg_get(cfg, CONFIG_SECTION, "quiet_hours", "start"))
    qh_end = _coerce_hour(cfg_get(cfg, CONFIG_SECTION, "quiet_hours", "end"))
    rl_max = _coerce_positive_int(cfg_get(cfg, CONFIG_SECTION, "rate_limit", "max"))
    rl_window = _coerce_positive_int(
        cfg_get(cfg, CONFIG_SECTION, "rate_limit", "window_seconds")
    )
    return ConsentPolicy(
        auto_approve_reversible=auto,
        quiet_hours_start=qh_start,
        quiet_hours_end=qh_end,
        rate_limit_max=rl_max,
        rate_limit_window_seconds=rl_window if rl_window is not None else 3600,
    )


def _coerce_hour(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    if 0 <= value <= 23:
        return value
    return None


def _coerce_positive_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if value > 0 else None


def evaluate_approval(
    policy: ConsentPolicy,
    *,
    reversible: bool,
    command: str,
    description: str,
    now: datetime | None = None,
    recent_auto_approvals: int = 0,
    approval_callback: ApprovalCallback | None = None,
) -> ConsentDecision:
    """Decide whether an action may proceed, per contract C6.

    * **Irreversible** actions (``reversible=False``) always require explicit
      approval via ``prompt_dangerous_approval`` — consent / quiet-hours /
      rate-limit can never silence the prompt (D6).
    * **Reversible** actions are auto-approved *only* when the user granted
      standing consent (``auto_approve_reversible``) AND the request is outside
      quiet-hours AND under the rate-limit. Otherwise the user is prompted.

    The actual prompt reuses the existing dangerous-command approval flow so
    secret redaction, per-session state, and fail-closed behaviour are shared.
    """
    now = now or datetime.now()

    if not reversible:
        return _prompt(command, description, approval_callback, base_reason="irreversible")

    if not policy.auto_approve_reversible:
        return _prompt(command, description, approval_callback, base_reason="consent_off")

    if policy.within_quiet_hours(now):
        return _prompt(command, description, approval_callback, base_reason="quiet_hours")

    if policy.is_rate_limited(recent_auto_approvals):
        return _prompt(command, description, approval_callback, base_reason="rate_limited")

    return ConsentDecision(approved=True, mode="auto", reason="consent")


def _prompt(
    command: str,
    description: str,
    approval_callback: ApprovalCallback | None,
    *,
    base_reason: str,
) -> ConsentDecision:
    from tools.approval import prompt_dangerous_approval

    choice = prompt_dangerous_approval(
        command,
        description,
        allow_permanent=False,
        approval_callback=approval_callback,
    )
    approved = choice in _GRANTED_CHOICES
    return ConsentDecision(
        approved=approved,
        mode="prompted" if approved else "denied",
        reason=base_reason,
    )
