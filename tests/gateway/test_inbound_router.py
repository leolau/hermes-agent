"""FG-03 Shape 1 — in-process inbound queue + bounded worker pool (contract C4).

Real-path behaviour tests: submit normalised events and assert the router (a)
isolates two accounts' conversations under the one shared brain, (b) keeps a
single session's turns strictly serial while (c) running distinct sessions in
parallel, and (d) forces channel origins to prod (D5/C3).
"""

import asyncio

import pytest

from gateway.config import Platform
from gateway.inbound import InboundRouter, guard_channel_prod
from gateway.producers import normalize_whatsapp
from gateway.session import SessionSource


@pytest.mark.asyncio
async def test_two_accounts_isolate_conversations_under_one_brain():
    seen: dict[str, list[str]] = {}

    async def handler(event, session_key):
        seen.setdefault(session_key, []).append(event.payload)

    router = InboundRouter(handler)
    key_a = await router.submit(normalize_whatsapp("num_A", "sender1", "for A"))
    key_b = await router.submit(normalize_whatsapp("num_B", "sender1", "for B"))
    await router.drain_idle()

    assert key_a != key_b, "same sender on two accounts must not share a session"
    assert seen[key_a] == ["for A"]
    assert seen[key_b] == ["for B"]
    # One shared router (brain) served both accounts.
    assert len(seen) == 2


@pytest.mark.asyncio
async def test_same_session_turns_are_serial():
    # A handler that fails if two turns for the same session overlap.
    active: set[str] = set()
    overlaps: list[str] = []
    order: list[str] = []

    async def handler(event, session_key):
        if session_key in active:
            overlaps.append(session_key)
        active.add(session_key)
        await asyncio.sleep(0.01)  # window for an overlap to be observed
        order.append(event.payload)
        active.discard(session_key)

    router = InboundRouter(handler, max_concurrent_sessions=4)
    for i in range(5):
        await router.submit(normalize_whatsapp("num_A", "sender1", f"m{i}"))
    await router.drain_idle()

    assert overlaps == [], "turns for one session must never overlap"
    assert order == [f"m{i}" for i in range(5)], "serial, in arrival order"


@pytest.mark.asyncio
async def test_distinct_sessions_run_in_parallel():
    # Two sessions each block on the same barrier; if they were serialized the
    # barrier (needing 2 parties) would deadlock and the test would time out.
    barrier = asyncio.Barrier(2)

    async def handler(event, session_key):
        await barrier.wait()

    router = InboundRouter(handler, max_concurrent_sessions=2)
    await router.submit(normalize_whatsapp("num_A", "s", "a"))
    await router.submit(normalize_whatsapp("num_B", "s", "b"))
    await asyncio.wait_for(router.drain_idle(), timeout=2.0)


@pytest.mark.asyncio
async def test_concurrency_is_bounded():
    peak = 0
    current = 0
    lock = asyncio.Lock()

    async def handler(event, session_key):
        nonlocal peak, current
        async with lock:
            current += 1
            peak = max(peak, current)
        await asyncio.sleep(0.01)
        async with lock:
            current -= 1

    router = InboundRouter(handler, max_concurrent_sessions=2)
    for i in range(6):
        await router.submit(normalize_whatsapp(f"acct{i}", "s", "x"))
    await router.drain_idle()
    assert peak <= 2, "never more than max_concurrent_sessions in flight"


@pytest.mark.asyncio
async def test_handler_exception_does_not_wedge_router():
    handled: list[str] = []

    async def handler(event, session_key):
        if event.payload == "boom":
            raise RuntimeError("handler blew up")
        handled.append(event.payload)

    router = InboundRouter(handler)
    await router.submit(normalize_whatsapp("num_A", "s1", "boom"))
    await router.submit(normalize_whatsapp("num_B", "s2", "ok"))
    await router.drain_idle()
    assert "ok" in handled


def test_channel_prod_only_guard_forces_prod():
    channel = SessionSource(
        platform=Platform.TELEGRAM, chat_id="c", chat_type="dm"
    )
    # Even with config asking for dev, a channel origin resolves to prod.
    assert guard_channel_prod(channel, config={"datastore": {"mode": "dev"}}) == "prod"


def test_local_origin_is_not_forced_to_prod():
    local = SessionSource(platform=Platform.LOCAL, chat_id="c", chat_type="dm")
    assert guard_channel_prod(local, config={"datastore": {"mode": "dev"}}) == "dev"
