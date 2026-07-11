"""Unit / invariant tests for the C1 principal model + C2 visibility helpers."""

from __future__ import annotations

import pytest

from hermes_cli.access import (
    Principal,
    ROLES,
    SHARED,
    can_read,
    can_read_row,
    normalize_visibility,
    parse_private_owner,
    private,
    scope_filter,
)


def _p(user_id: str, role: str) -> Principal:
    return Principal(user_id=user_id, display=user_id.title(), role=role)  # type: ignore[arg-type]


def test_role_vocabulary_is_the_locked_set() -> None:
    assert ROLES == ("owner", "admin", "member", "viewer")


def test_principal_rejects_unknown_role_and_empty_id() -> None:
    with pytest.raises(ValueError):
        Principal(user_id="u1", display="U", role="root")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        Principal(user_id="   ", display="U", role="member")


def test_only_owner_reports_is_owner() -> None:
    assert _p("u1", "owner").is_owner is True
    for role in ("admin", "member", "viewer"):
        assert _p("u1", role).is_owner is False


def test_private_and_parse_round_trip() -> None:
    assert private("bob") == "private:bob"
    assert parse_private_owner("private:bob") == "bob"
    assert parse_private_owner(SHARED) is None
    assert parse_private_owner("private:") is None
    assert normalize_visibility("shared") == "shared"
    assert normalize_visibility("private:bob") == "private:bob"
    with pytest.raises(ValueError):
        normalize_visibility("secret")


def test_shared_rows_are_readable_by_every_member() -> None:
    for role in ROLES:
        assert can_read(_p("anyone", role), SHARED) is True


def test_negative_access_private_row_is_isolated_per_user() -> None:
    """C2 negative-access invariant at the app layer.

    A member cannot read another user's ``private:<other>`` row; the owner can;
    the private owner can read their own.
    """
    member_a = _p("alice", "member")
    member_b = _p("bob", "member")
    owner = _p("root", "owner")

    bobs_private = private("bob")

    # Alice (a different member) is denied Bob's private row.
    assert can_read(member_a, bobs_private) is False
    # Bob reads his own private row.
    assert can_read(member_b, bobs_private) is True
    # The owner bypasses the filter and sees everything.
    assert can_read(owner, bobs_private) is True


def test_admin_and_viewer_do_not_bypass_private_scope() -> None:
    # Only the single owner bypasses; admin/viewer are still scoped.
    assert can_read(_p("adm", "admin"), private("bob")) is False
    assert can_read(_p("vw", "viewer"), private("bob")) is False


def test_can_read_row_fails_closed_on_missing_visibility() -> None:
    member = _p("alice", "member")
    assert can_read_row(member, {"visibility": SHARED}) is True
    assert can_read_row(member, {"visibility": private("alice")}) is True
    assert can_read_row(member, {"visibility": private("bob")}) is False
    # Missing/blank visibility is unreadable for a non-owner, readable for owner.
    assert can_read_row(member, {}) is False
    assert can_read_row(member, {"visibility": ""}) is False
    assert can_read_row(_p("root", "owner"), {}) is True


def test_scope_filter_owner_bypasses_with_no_params() -> None:
    pred = scope_filter(_p("root", "owner"))
    assert pred.sql == "TRUE"
    assert pred.params == ()


def test_scope_filter_member_is_parameterized_and_shared_plus_own() -> None:
    pred = scope_filter(_p("alice", "member"))
    assert pred.sql == "(visibility = 'shared' OR visibility = $1)"
    assert pred.params == ("private:alice",)


def test_scope_filter_honors_column_and_start_index() -> None:
    pred = scope_filter(_p("alice", "member"), column="mem.visibility", start_index=3)
    assert pred.sql == "(mem.visibility = 'shared' OR mem.visibility = $3)"
    assert pred.params == ("private:alice",)


def test_scope_filter_rejects_unsafe_column() -> None:
    with pytest.raises(ValueError):
        scope_filter(_p("alice", "member"), column="visibility; DROP TABLE x")
