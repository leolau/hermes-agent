"""FG-10 unit tests (no datastore): reply routing (C1+C4) and the C6
auto-approve decision. The DB-backed dedupe / negative-access / delivery
behaviour lives in ``test_human_comms_e2e.py`` (real Postgres).
"""

from __future__ import annotations

from datetime import datetime

from gateway.config import Platform
from gateway.session import SessionSource
from hermes_cli.access import Principal, private
from hermes_cli.consent import ConsentPolicy
from hermes_cli.datastore import get_store
from hermes_cli.human_comms import (
    Notification,
    NotificationStore,
    notification_target_user,
    resolve_reply_target,
)


def _member(user_id: str) -> Principal:
    return Principal(user_id=user_id, display=user_id, role="member")


def _source(**kwargs) -> SessionSource:
    base = dict(platform=Platform.TELEGRAM, chat_id="chat-1")
    base.update(kwargs)
    return SessionSource(**base)


# -- reply routing (C1 + C4) -------------------------------------------------


def test_reply_target_uses_channel_account_id() -> None:
    """A reply leaves via the SAME account the message arrived on (C4)."""
    source = _source(account_id="wa:+15550001", thread_id="topic-9")
    target = resolve_reply_target(_member("alice"), source)
    assert target.platform == "telegram"
    assert target.account_id == "wa:+15550001"
    assert target.chat_id == "chat-1"
    assert target.thread_id == "topic-9"
    # The resolved principal is authoritative for internal attribution (C1).
    assert target.internal_user_id == "alice"


def test_reply_target_without_principal_keeps_channel_identity() -> None:
    """Unenrolled sender: routing still works, internal-user attribution drops."""
    source = _source(account_id="acct-2", internal_user_id="prebound")
    target = resolve_reply_target(None, source)
    assert target.account_id == "acct-2"
    # No principal => fall back to whatever the inbound binder stamped.
    assert target.internal_user_id == "prebound"


def test_reply_target_principal_overrides_stale_internal_id() -> None:
    source = _source(account_id="acct-3", internal_user_id="stale")
    target = resolve_reply_target(_member("bob"), source)
    assert target.internal_user_id == "bob"


# -- C6 auto-approve decision (quiet-hours / rate-limit / consent) -----------


def _store():
    # A store object is enough for the pure decision path — no connection.
    return get_store(
        "supabase-app",
        "prod",
        config={"datastore": {"supabase_app": {"dsn": "postgresql://unused"}}},
    )


def _ntf(policy: ConsentPolicy) -> NotificationStore:
    return NotificationStore(_store(), policy=policy)


NOON = datetime(2026, 7, 11, 12, 0, 0)
NIGHT = datetime(2026, 7, 11, 23, 0, 0)


def test_c6_no_standing_consent_never_auto_approves() -> None:
    ntf = _ntf(ConsentPolicy(auto_approve_reversible=False))
    assert not ntf._may_auto_approve(
        reversible=True, now=NOON, recent_auto_approvals=0
    )


def test_c6_irreversible_never_auto_approves_even_with_consent() -> None:
    ntf = _ntf(ConsentPolicy(auto_approve_reversible=True))
    assert not ntf._may_auto_approve(
        reversible=False, now=NOON, recent_auto_approvals=0
    )


def test_c6_reversible_auto_approves_under_standing_consent() -> None:
    ntf = _ntf(ConsentPolicy(auto_approve_reversible=True))
    assert ntf._may_auto_approve(reversible=True, now=NOON, recent_auto_approvals=0)


def test_c6_quiet_hours_block_auto_approve() -> None:
    ntf = _ntf(
        ConsentPolicy(
            auto_approve_reversible=True, quiet_hours_start=22, quiet_hours_end=7
        )
    )
    assert not ntf._may_auto_approve(
        reversible=True, now=NIGHT, recent_auto_approvals=0
    )
    # Outside the window the same policy auto-approves.
    assert ntf._may_auto_approve(reversible=True, now=NOON, recent_auto_approvals=0)


def test_c6_rate_limit_blocks_auto_approve() -> None:
    ntf = _ntf(ConsentPolicy(auto_approve_reversible=True, rate_limit_max=3))
    assert ntf._may_auto_approve(reversible=True, now=NOON, recent_auto_approvals=2)
    assert not ntf._may_auto_approve(
        reversible=True, now=NOON, recent_auto_approvals=3
    )


# -- target resolution -------------------------------------------------------


def test_notification_target_user_prefers_private_owner() -> None:
    item = Notification(
        id="ntf_1",
        kind="approval",
        owner_user_id="root",
        visibility=private("carol"),
        title="t",
        body="",
        command="",
        reversible=True,
        status="pending",
        answer=None,
        answered_by=None,
        answered_via=None,
        dedupe_key=None,
        delivered=True,
        created_at=None,
        answered_at=None,
    )
    assert notification_target_user(item) == "carol"
