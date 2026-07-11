"""FG-03 thin-producer normalisers -> InboundEvent (contract C4)."""

import pytest

from gateway.config import Platform
from gateway.inbound import InboundEvent
from gateway.producers import (
    calendar_event_to_inbound,
    normalize_email,
    normalize_message,
    normalize_whatsapp,
)


def test_normalize_message_tags_receiving_account():
    ev = normalize_message(
        Platform.TELEGRAM, "my_bot", "sender7", "hi", user_id="sender7"
    )
    assert isinstance(ev, InboundEvent)
    assert ev.account_id == "my_bot"
    assert ev.sender_chat_id == "sender7"
    src = ev.to_source()
    assert src.account_id == "my_bot"
    assert src.chat_id == "sender7"


def test_whatsapp_producer_defaults_user_to_sender():
    ev = normalize_whatsapp("num_A", "1555000", "hello")
    assert ev.platform == Platform.WHATSAPP
    assert ev.account_id == "num_A"
    assert ev.user_id == "1555000"


def test_email_producer_carries_sender_and_subject():
    ev = normalize_email(
        "me@inbox.test", "them@x.test", "Invoice #9", "body text"
    )
    assert ev.platform == Platform.EMAIL
    assert ev.account_id == "me@inbox.test"
    assert ev.user_id == "them@x.test"
    assert ev.chat_name == "Invoice #9"
    assert ev.payload == "body text"


def test_calendar_producer_keys_on_event_id():
    ev = calendar_event_to_inbound(
        "cal_primary", {"id": "evt123", "summary": "Standup"}
    )
    assert ev.account_id == "cal_primary"
    assert ev.sender_chat_id == "evt123"
    assert ev.chat_name == "Standup"
    assert ev.metadata["source"] == "calendar"


def test_event_requires_account_id():
    with pytest.raises(ValueError):
        InboundEvent(platform=Platform.WHATSAPP, account_id="", sender_chat_id="s", payload="x")
