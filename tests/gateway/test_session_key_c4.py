"""Contract C4 (FG-03): account_id / internal user / task session-key dimensions.

Behaviour/invariant tests (not value snapshots). These complement
``tests/plan_baseline/test_session_key_baseline.py`` (which locks the pre-C4
byte-stable behaviour): here we assert what the *new* dimensions must do —
isolate accounts/users/tasks, stay path-safe, and never perturb a key when
unset — plus that ``SessionSource`` round-trips the new fields only when set.
"""

from gateway.session import Platform, SessionSource, build_session_key


def _src(**kw) -> SessionSource:
    kw.setdefault("platform", Platform.TELEGRAM)
    kw.setdefault("chat_type", "dm")
    return SessionSource(**kw)


# --- byte-stability: no C4 dimension set == pre-C4 key -----------------------


def test_unset_c4_dimensions_are_byte_identical_to_base_key():
    src = _src(chat_id="123")
    plain = build_session_key(src)
    # Explicit Nones and unset source fields must both yield the base key.
    assert build_session_key(
        src, account_id=None, internal_user_id=None, task=None
    ) == plain
    assert ":acct:" not in plain
    assert ":usr:" not in plain
    assert ":task:" not in plain


def test_blank_c4_dimension_does_not_change_key():
    src = _src(chat_id="123")
    assert build_session_key(src, account_id="   ") == build_session_key(src)


# --- account_id isolates receiving inboxes -----------------------------------


def test_account_id_isolates_same_sender_across_two_accounts():
    a = build_session_key(_src(chat_id="sender1", account_id="inbox_A"))
    b = build_session_key(_src(chat_id="sender1", account_id="inbox_B"))
    assert a != b, "two accounts receiving the same sender must not share a session"


def test_account_id_field_and_kwarg_agree():
    from_field = build_session_key(_src(chat_id="s", account_id="inbox_A"))
    from_kwarg = build_session_key(_src(chat_id="s"), account_id="inbox_A")
    assert from_field == from_kwarg


def test_explicit_kwarg_overrides_source_field():
    src = _src(chat_id="s", account_id="inbox_A")
    assert build_session_key(src, account_id="inbox_B") == build_session_key(
        _src(chat_id="s", account_id="inbox_B")
    )


# --- internal user + task isolate the (user, task) core ----------------------


def test_internal_user_isolates_sessions():
    a = build_session_key(_src(chat_id="c", internal_user_id="U1"))
    b = build_session_key(_src(chat_id="c", internal_user_id="U2"))
    assert a != b


def test_task_isolates_sessions_per_user():
    billing = build_session_key(
        _src(chat_id="c", internal_user_id="U1", task="billing")
    )
    support = build_session_key(
        _src(chat_id="c", internal_user_id="U1", task="support")
    )
    assert billing != support, "different tasks must not share a cached prefix"


def test_same_user_task_is_stable_long_lived_key():
    a = build_session_key(_src(chat_id="c", internal_user_id="U1", task="billing"))
    b = build_session_key(_src(chat_id="c", internal_user_id="U1", task="billing"))
    assert a == b, "one (user, task) must map to one long-lived session"


def test_dimensions_compose_in_fixed_order():
    key = build_session_key(
        _src(chat_id="c", account_id="acc", internal_user_id="U1", task="t")
    )
    # acct, then usr, then task — a fixed order so the key is deterministic.
    assert key.index(":acct:") < key.index(":usr:") < key.index(":task:")


# --- path safety: channel-supplied dimensions can't escape the sessions dir --


def test_dimension_values_are_path_safe():
    from gateway.session import _is_path_unsafe

    key = build_session_key(
        _src(chat_id="c", account_id="../../etc", task="a/b\\c")
    )
    assert not _is_path_unsafe(key)
    assert ".." not in key and "/" not in key and "\\" not in key


# --- SessionSource serialization round-trips the new fields (only when set) ---


def test_to_dict_omits_unset_c4_fields():
    d = _src(chat_id="c").to_dict()
    assert "account_id" not in d
    assert "internal_user_id" not in d
    assert "task" not in d


def test_to_dict_from_dict_round_trip_with_c4_fields():
    src = _src(chat_id="c", account_id="acc", internal_user_id="U1", task="t")
    restored = SessionSource.from_dict(src.to_dict())
    assert restored.account_id == "acc"
    assert restored.internal_user_id == "U1"
    assert restored.task == "t"
    assert build_session_key(restored) == build_session_key(src)
