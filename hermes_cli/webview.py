"""FG-17b agent-webview consent policy + per-user session registry.

The dashboard "agent webview" lets the agent drive real webpages on the
user's behalf through the existing CDP browser toolset. It is the
highest-risk dashboard surface, so it is governed by an explicit consent
model (Leo's decision: **Option B — session-scoped consent + escalation**):

* **Default-deny / opt-in.** No webview session exists until the user
  explicitly opens one. With no session, every action is denied — the
  registry simply has nothing to act on.
* **Session scope.** Opening a session grants a scope: a set of
  ``allowed_domains`` and a mode (``read_only`` vs ``interactive``). Routine
  in-scope work proceeds autonomously.
* **Escalation (C6).** Anything outside the granted scope — an off-scope
  domain, an interactive action under a read-only grant, or any
  credentialed/destructive action even in scope — does not run. It escalates
  to a per-action C6 approval the user must grant.
* **Per-user isolation (C2).** Sessions and their CDP browser profiles are
  keyed by the resolved principal; one user can never see or drive another
  user's webview session or profile.
* **Traced (C8).** Every decision (allow / escalate / deny) is surfaced for
  the FG-16 interaction trace.

This module holds the *pure* policy decision (``decide``) plus the in-process
session registry. The decision function has no I/O and is unit-tested
directly; the registry holds ephemeral per-process browser-session state
(browser sessions are inherently process-lived, not durable domain data).
The actual CDP page-driving is delegated to the existing browser toolset by
the web server behind an availability check — this module never imports it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Optional
from urllib.parse import urlparse
import time
import uuid

# Action verbs the agent can request against a live page.
ActionKind = Literal[
    "navigate",
    "read",
    "screenshot",
    "click",
    "type",
    "select",
    "scroll",
    "submit",
    "download",
]

# Read-only actions: safe to run autonomously when in-scope.
READ_ONLY_KINDS: frozenset[str] = frozenset({"navigate", "read", "screenshot", "scroll"})
# Interactive actions: require an ``interactive`` grant to run autonomously.
INTERACTIVE_KINDS: frozenset[str] = frozenset({"click", "type", "select"})
# Inherently high-risk actions: always escalate, even in-scope + interactive.
DESTRUCTIVE_KINDS: frozenset[str] = frozenset({"submit", "download"})

Mode = Literal["read_only", "interactive"]
Decision = Literal["allow", "escalate", "deny"]


@dataclass(frozen=True)
class WebviewScope:
    """The consent granted when a webview session is opened."""

    allowed_domains: tuple[str, ...] = ()
    mode: Mode = "read_only"

    def as_dict(self) -> dict[str, object]:
        return {"allowed_domains": list(self.allowed_domains), "mode": self.mode}


@dataclass(frozen=True)
class WebviewAction:
    """A single agent-requested action against the live page."""

    kind: ActionKind
    url: Optional[str] = None
    # ``credentialed`` marks login / secret-entry; ``destructive`` marks
    # purchases, deletes, form submits, downloads. Both always escalate.
    credentialed: bool = False
    destructive: bool = False


@dataclass(frozen=True)
class PolicyDecision:
    decision: Decision
    reason: str

    def as_dict(self) -> dict[str, object]:
        return {"decision": self.decision, "reason": self.reason}


def _host_of(url: Optional[str]) -> Optional[str]:
    if not url:
        return None
    parsed = urlparse(url if "://" in url else f"https://{url}")
    host = (parsed.hostname or "").lower()
    return host or None


def _domain_in_scope(host: Optional[str], allowed: tuple[str, ...]) -> bool:
    """A host is in scope if it equals or is a subdomain of an allowed entry."""
    if host is None:
        return False
    for entry in allowed:
        e = entry.strip().lower().lstrip(".")
        if not e:
            continue
        if host == e or host.endswith("." + e):
            return True
    return False


def decide(scope: WebviewScope, action: WebviewAction) -> PolicyDecision:
    """Pure Option-B consent decision for one action within a live session.

    Never returns ``deny`` on its own — a session already implies the user
    opted in, so the worst case inside a session is ``escalate`` (a per-action
    C6 approval). ``deny`` is reserved for the endpoint layer, which denies
    everything when no session/consent exists at all (default-deny).
    """
    # Credentialed or destructive actions always escalate, regardless of scope.
    if action.credentialed:
        return PolicyDecision("escalate", "credentialed action requires approval")
    if action.destructive or action.kind in DESTRUCTIVE_KINDS:
        return PolicyDecision("escalate", "destructive action requires approval")

    host = _host_of(action.url)
    if action.kind == "navigate":
        if not _domain_in_scope(host, scope.allowed_domains):
            return PolicyDecision(
                "escalate", "navigation target is outside the granted scope"
            )
        return PolicyDecision("allow", "in-scope navigation")

    # Non-navigation actions act on the current page; if a URL/host is given it
    # must still be in scope.
    if host is not None and not _domain_in_scope(host, scope.allowed_domains):
        return PolicyDecision("escalate", "action target is outside the granted scope")

    if action.kind in READ_ONLY_KINDS:
        return PolicyDecision("allow", "in-scope read-only action")

    if action.kind in INTERACTIVE_KINDS:
        if scope.mode != "interactive":
            return PolicyDecision(
                "escalate", "interactive action under a read-only grant"
            )
        return PolicyDecision("allow", "in-scope interactive action")

    return PolicyDecision("escalate", "unrecognized action requires approval")


@dataclass
class PendingApproval:
    id: str
    action: WebviewAction
    reason: str
    created_at: float
    resolved: Optional[bool] = None

    def as_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "kind": self.action.kind,
            "url": self.action.url,
            "credentialed": self.action.credentialed,
            "destructive": self.action.destructive,
            "reason": self.reason,
            "created_at": self.created_at,
            "resolved": self.resolved,
        }


@dataclass
class WebviewSession:
    """One user's opted-in webview session (ephemeral, process-lived)."""

    id: str
    owner_user_id: str
    scope: WebviewScope
    profile_dir: str
    created_at: float
    # One FG-16 trace id groups every action taken in this session (C8).
    trace_id: str
    pending: dict[str, PendingApproval] = field(default_factory=dict)

    def as_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "owner_user_id": self.owner_user_id,
            "scope": self.scope.as_dict(),
            "profile_dir": self.profile_dir,
            "created_at": self.created_at,
            "trace_id": self.trace_id,
            "pending": [p.as_dict() for p in self.pending.values()],
        }


class WebviewRegistry:
    """In-process, per-user registry of open webview sessions.

    Keyed by the resolved principal's ``user_id`` so a caller can only ever
    reach their own session — the C2 isolation boundary for this surface.
    Browser profile dirs are derived per user under ``profiles_root`` so no
    two users share cookies/local-state.
    """

    def __init__(self, profiles_root: str) -> None:
        self._profiles_root = profiles_root
        self._by_user: dict[str, WebviewSession] = {}

    def profile_dir_for(self, user_id: str) -> str:
        # Deterministic, collision-free per-user path (opaque, not the raw id).
        digest = uuid.uuid5(uuid.NAMESPACE_URL, f"hermes-webview:{user_id}").hex
        return f"{self._profiles_root.rstrip('/')}/{digest}"

    def get(self, user_id: str) -> Optional[WebviewSession]:
        return self._by_user.get(user_id)

    def open(self, user_id: str, scope: WebviewScope) -> WebviewSession:
        session = WebviewSession(
            id=f"wv_{uuid.uuid4().hex}",
            owner_user_id=user_id,
            scope=scope,
            profile_dir=self.profile_dir_for(user_id),
            created_at=time.time(),
            trace_id=f"trace_{uuid.uuid4().hex}",
        )
        self._by_user[user_id] = session
        return session

    def close(self, user_id: str) -> bool:
        return self._by_user.pop(user_id, None) is not None
