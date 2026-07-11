"""FG-03 thin producers — normalise raw channel input into :class:`InboundEvent`.

Under the one-brain design a producer's ONLY job is to convert a raw channel
payload into a channel-neutral :class:`~gateway.inbound.InboundEvent` tagged
with the *receiving* ``account_id`` and push it onto the shared inbound queue.
It does **not** run its own DeepSeek/LLM triage (the "siloed cognition" the
redesign removes) — reasoning happens once, in the shared ``AIAgent`` core the
router dispatches to.

These helpers are deliberately tiny and pure (input → ``InboundEvent``) so the
existing pollers/bridges (``custom/whatsapp``, ``custom/email``,
``custom/calendar``) can be reduced to producers by swapping their bespoke
triage step for one of these calls, with no adapter rewrite.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional

from gateway.config import Platform
from gateway.inbound import InboundEvent


def normalize_message(
    platform: Platform,
    account_id: str,
    sender_chat_id: Optional[str],
    payload: Any,
    *,
    chat_type: str = "dm",
    user_id: Optional[str] = None,
    user_name: Optional[str] = None,
    chat_name: Optional[str] = None,
    thread_id: Optional[str] = None,
    task: Optional[str] = None,
    metadata: Optional[Mapping[str, Any]] = None,
) -> InboundEvent:
    """Normalise any channel message into an :class:`InboundEvent`.

    The generic path every messaging producer (WhatsApp number, email inbox,
    SMS, …) funnels through. ``account_id`` is the receiving inbox identity, so
    two of my accounts never collide on a shared sender ``chat_id``.
    """
    return InboundEvent(
        platform=platform,
        account_id=str(account_id),
        sender_chat_id=sender_chat_id,
        payload=payload,
        chat_type=chat_type,
        user_id=user_id,
        user_name=user_name,
        chat_name=chat_name,
        thread_id=thread_id,
        task=task,
        metadata=dict(metadata or {}),
    )


def normalize_whatsapp(
    account_id: str,
    sender_chat_id: str,
    text: str,
    *,
    user_id: Optional[str] = None,
    user_name: Optional[str] = None,
    task: Optional[str] = None,
    metadata: Optional[Mapping[str, Any]] = None,
) -> InboundEvent:
    """Thin WhatsApp producer: one of my numbers received ``text`` from a sender.

    ``account_id`` is my receiving WhatsApp number/phone-number-id; the reply
    egresses via that same number.
    """
    return normalize_message(
        Platform.WHATSAPP,
        account_id,
        sender_chat_id,
        text,
        chat_type="dm",
        user_id=user_id or sender_chat_id,
        user_name=user_name,
        task=task,
        metadata=metadata,
    )


def normalize_email(
    account_id: str,
    from_address: str,
    subject: str,
    body: str,
    *,
    thread_id: Optional[str] = None,
    task: Optional[str] = None,
    metadata: Optional[Mapping[str, Any]] = None,
) -> InboundEvent:
    """Thin email producer: one of my inboxes received a message.

    ``account_id`` is my receiving mailbox address; ``from_address`` is the
    sender. The subject is carried as the chat name and the body as the payload.
    """
    return normalize_message(
        Platform.EMAIL,
        account_id,
        from_address,
        body,
        chat_type="dm",
        user_id=from_address,
        chat_name=subject,
        thread_id=thread_id,
        task=task,
        metadata=metadata,
    )


# Calendar is not a gateway platform (it has no inbound webhook); it is polled.
# The calendar producer is therefore a cron/heartbeat producer that emits an
# event into the SAME inbound queue, so a calendar item flows through the
# identical shared agent loop as a message. It rides the LOCAL platform (a
# heartbeat, not a chat channel) with the calendar id as ``account_id``.
CALENDAR_PLATFORM = Platform.LOCAL


def calendar_event_to_inbound(
    calendar_id: str,
    event: Mapping[str, Any],
    *,
    task: Optional[str] = None,
) -> InboundEvent:
    """Normalise a single calendar event into an :class:`InboundEvent`.

    ``calendar_id`` is the receiving calendar (the ``account_id``); ``event`` is
    a provider event mapping (``id``, ``summary``, ``start``, …). The event id
    keys the sender/chat so repeated syncs of the same event route to the same
    session rather than spawning duplicates.
    """
    event_id = str(event.get("id") or event.get("iCalUID") or "")
    summary = str(event.get("summary") or event.get("title") or "(untitled event)")
    return InboundEvent(
        platform=CALENDAR_PLATFORM,
        account_id=str(calendar_id),
        sender_chat_id=event_id or None,
        payload=dict(event),
        chat_type="dm",
        chat_name=summary,
        task=task,
        metadata={"source": "calendar", "calendar_id": str(calendar_id)},
    )


async def run_calendar_sync(
    calendar_id: str,
    events,
    *,
    router,
    task: Optional[str] = None,
) -> list[str]:
    """Cron/heartbeat producer: submit each polled calendar event to ``router``.

    ``events`` is the iterable a poller fetched for one calendar since its last
    sync token. Returns the resolved ``session_key`` per event (order preserved)
    so a caller/test can assert routing. Kept dependency-free of any live
    Google client so it composes with the existing poller shape.
    """
    keys: list[str] = []
    for event in events:
        inbound = calendar_event_to_inbound(calendar_id, event, task=task)
        keys.append(await router.submit(inbound))
    return keys
