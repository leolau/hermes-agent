"""Baseline invariants for gateway session-key construction.

Locks the behaviour FG-03 (multi-channel redesign, contract C4) must preserve
while it adds ``account_id`` to ``SessionSource``: single-account callers must
keep producing byte-identical keys, and per-chat / per-user isolation must hold.

Invariant contracts, not value snapshots.
"""

from gateway.session import Platform, SessionSource, build_session_key


def _src(**kw):
    kw.setdefault("platform", Platform.TELEGRAM)
    kw.setdefault("chat_type", "dm")
    return SessionSource(**kw)


def test_build_session_key_is_deterministic():
    src = _src(chat_id="123")
    assert build_session_key(src) == build_session_key(src)


def test_distinct_dm_chats_get_distinct_keys():
    a = build_session_key(_src(chat_type="dm", chat_id="chatA"))
    b = build_session_key(_src(chat_type="dm", chat_id="chatB"))
    assert a != b, "different DM chats must not share a session (history bleed)"


def test_group_isolates_per_user_by_default():
    u1 = build_session_key(_src(chat_type="group", chat_id="g1", user_id="u1"))
    u2 = build_session_key(_src(chat_type="group", chat_id="g1", user_id="u2"))
    assert u1 != u2, "group participants must be isolated when group_sessions_per_user"


def test_group_shared_when_isolation_disabled():
    u1 = build_session_key(
        _src(chat_type="group", chat_id="g1", user_id="u1"),
        group_sessions_per_user=False,
    )
    u2 = build_session_key(
        _src(chat_type="group", chat_id="g1", user_id="u2"),
        group_sessions_per_user=False,
    )
    assert u1 == u2, "with isolation disabled, a group shares one session"


def test_default_namespace_prefix_is_stable():
    # Single-account callers use the default (profile=None) namespace. FG-03 must
    # not change this prefix for existing callers, or every cached session churns.
    key = build_session_key(_src(chat_type="dm", chat_id="123"))
    assert key.startswith("agent:main:"), key
    assert ":telegram:" in key


def test_thread_differentiates_dm_sessions():
    base = build_session_key(_src(chat_type="dm", chat_id="c1"))
    threaded = build_session_key(_src(chat_type="dm", chat_id="c1", thread_id="t9"))
    assert base != threaded
