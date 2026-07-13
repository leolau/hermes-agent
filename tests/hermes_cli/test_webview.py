"""Unit tests for the FG-17b agent-webview consent policy + session registry.

These are the pure, I/O-free heart of the highest-risk dashboard surface
(``hermes_cli/webview.py``): the Option-B consent decision (``decide``) and the
per-user session registry (C2 isolation + per-user browser-profile paths). The
endpoint wiring (default-deny, escalation queue, C8 trace) is covered by the
real-Postgres E2E in ``test_fg17b_dashboard_e2e.py``.
"""

from __future__ import annotations

from hermes_cli.webview import (
    WebviewAction,
    WebviewRegistry,
    WebviewScope,
    _domain_in_scope,
    decide,
)


def _scope(*, domains=(), mode="read_only") -> WebviewScope:
    return WebviewScope(allowed_domains=tuple(domains), mode=mode)


# ---------------------------------------------------------------------------
# _domain_in_scope — host/subdomain matching
# ---------------------------------------------------------------------------


def test_domain_scope_matches_exact_and_subdomains_only() -> None:
    allowed = ("example.com", ".wiki.internal")
    assert _domain_in_scope("example.com", allowed) is True
    assert _domain_in_scope("docs.example.com", allowed) is True  # subdomain
    assert _domain_in_scope("wiki.internal", allowed) is True  # leading dot stripped
    assert _domain_in_scope("a.wiki.internal", allowed) is True
    # A suffix that is NOT a dot-boundary subdomain must not match.
    assert _domain_in_scope("notexample.com", allowed) is False
    assert _domain_in_scope("example.com.evil.com", allowed) is False
    assert _domain_in_scope(None, allowed) is False
    assert _domain_in_scope("example.com", ()) is False


# ---------------------------------------------------------------------------
# decide — the Option-B consent matrix
# ---------------------------------------------------------------------------


def test_in_scope_navigation_and_reads_are_allowed() -> None:
    scope = _scope(domains=("example.com",))
    assert decide(scope, WebviewAction("navigate", url="https://example.com/x")).decision == "allow"
    assert decide(scope, WebviewAction("read", url="https://docs.example.com")).decision == "allow"
    assert decide(scope, WebviewAction("screenshot")).decision == "allow"
    # A read acting on the current page (no url) is allowed.
    assert decide(scope, WebviewAction("scroll")).decision == "allow"


def test_off_scope_navigation_escalates() -> None:
    scope = _scope(domains=("example.com",))
    verdict = decide(scope, WebviewAction("navigate", url="https://evil.test/x"))
    assert verdict.decision == "escalate"
    # A non-navigation action pointed at an off-scope host also escalates.
    assert decide(scope, WebviewAction("read", url="https://evil.test")).decision == "escalate"


def test_interactive_actions_require_an_interactive_grant() -> None:
    ro = _scope(domains=("example.com",), mode="read_only")
    rw = _scope(domains=("example.com",), mode="interactive")
    click = WebviewAction("click", url="https://example.com")
    assert decide(ro, click).decision == "escalate"  # read-only grant
    assert decide(rw, click).decision == "allow"  # interactive grant
    # type/select behave the same as click.
    assert decide(ro, WebviewAction("type", url="https://example.com")).decision == "escalate"
    assert decide(rw, WebviewAction("select", url="https://example.com")).decision == "allow"


def test_credentialed_and_destructive_actions_always_escalate() -> None:
    # Even with a full interactive grant on an in-scope domain, credentialed or
    # destructive actions never run autonomously.
    rw = _scope(domains=("example.com",), mode="interactive")
    cred = WebviewAction("type", url="https://example.com", credentialed=True)
    assert decide(rw, cred).decision == "escalate"
    dest = WebviewAction("click", url="https://example.com", destructive=True)
    assert decide(rw, dest).decision == "escalate"
    # submit/download are destructive by kind, regardless of the flag.
    assert decide(rw, WebviewAction("submit", url="https://example.com")).decision == "escalate"
    assert decide(rw, WebviewAction("download", url="https://example.com")).decision == "escalate"


def test_decide_never_denies_within_a_session() -> None:
    """A session already implies opt-in: worst case inside it is escalate."""
    scope = _scope(domains=(), mode="read_only")
    for action in (
        WebviewAction("navigate", url="https://anything.test"),
        WebviewAction("click", url="https://anything.test", credentialed=True),
        WebviewAction("submit"),
    ):
        assert decide(scope, action).decision in ("allow", "escalate")


# ---------------------------------------------------------------------------
# WebviewRegistry — per-user isolation (C2) + per-user profile dirs
# ---------------------------------------------------------------------------


def test_registry_isolates_sessions_per_user() -> None:
    reg = WebviewRegistry("/tmp/hermes-webview-test")
    alice = reg.open("alice", _scope(domains=("example.com",)))
    # Alice's own session is reachable; Bob has none (default-deny by absence).
    assert reg.get("alice") is alice
    assert reg.get("bob") is None
    assert alice.owner_user_id == "alice"

    # Bob opening his own does not touch Alice's.
    bob = reg.open("bob", _scope(mode="interactive"))
    assert reg.get("bob") is bob
    assert reg.get("alice") is alice
    assert bob.id != alice.id

    # Closing is per-user.
    assert reg.close("alice") is True
    assert reg.get("alice") is None
    assert reg.get("bob") is bob
    assert reg.close("alice") is False  # already gone


def test_registry_profile_dirs_are_deterministic_and_per_user() -> None:
    reg = WebviewRegistry("/data/webview/profiles/")
    a1 = reg.profile_dir_for("alice")
    a2 = reg.profile_dir_for("alice")
    b1 = reg.profile_dir_for("bob")
    assert a1 == a2  # deterministic
    assert a1 != b1  # never shared across users (no cookie/local-state bleed)
    # The raw user id is not leaked into the path (opaque digest under the root).
    assert a1.startswith("/data/webview/profiles/")
    assert "alice" not in a1
    # An opened session adopts its user's profile dir.
    assert reg.open("alice", _scope()).profile_dir == a1
