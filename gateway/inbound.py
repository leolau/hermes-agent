"""FG-03 Shape 1 — one brain, all channels (contract C4).

Every incoming channel (each WhatsApp number, each email inbox, each calendar,
…) is just a different *input* into the **one shared agent brain**. This module
is the Shape-1 transport that realises that principle without a per-channel or
per-account silo:

* **Producers are thin normalisers.** A poller/bridge/cron converts a raw
  channel payload into a channel-neutral :class:`InboundEvent` tagged with the
  *receiving* ``account_id`` (which of my inboxes took it in) — it does NOT run
  its own bespoke triage brain. See :mod:`gateway.producers`.
* **One in-process queue + a bounded worker pool.** Events are submitted to an
  :class:`InboundRouter` which routes each to the single shared ``AIAgent``
  core keyed by the C4 ``session_key``. Turns for one session run **serially**
  (prompt-cache safety: a byte-stable prefix + strict role alternation needs
  serial turns), while **different sessions run in parallel** up to a bounded
  concurrency — matching how ``gateway/run.py`` already caches one ``AIAgent``
  per ``session_key``.
* **Identity binds through the C1 seam.** :func:`bind_channel_principal` resolves the
  channel identity to a :class:`~hermes_cli.access.Principal` via
  ``resolve_principal`` and stamps the internal user onto the session source so
  the key isolates per internal user (never per raw channel handle).
* **Channels are prod-only (D5/C3).** :func:`guard_channel_prod` routes every
  channel-originated event through the C3 mode guard, which forces ``prod``.

This is Shape 1 (in-process, reuse the working pollers). Shape 2 (N adapter
instances per account via ``PlatformConfig.accounts``) is the durable target
and is documented in the FG-03 doc; it can be migrated to once Shape 1 proves
out, without changing the C4 contract this module publishes.
"""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from dataclasses import dataclass, field
from typing import (
    TYPE_CHECKING,
    Any,
    Awaitable,
    Callable,
    Deque,
    Dict,
    Literal,
    Mapping,
    Optional,
)

from gateway.config import Platform
from gateway.session import SessionSource, build_session_key

if TYPE_CHECKING:
    from hermes_cli.access import Principal, PrincipalStore

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# InboundEvent — the channel-neutral event a thin producer emits
# ---------------------------------------------------------------------------


@dataclass
class InboundEvent:
    """A normalised inbound message/event from any channel (contract C4).

    A thin producer emits ``(platform, account_id, sender_chat_id, payload)``
    plus whatever identity/threading it already knows; the router turns it into
    a :class:`SessionSource` and a C4 ``session_key``. ``account_id`` is the
    *receiving inbox* (my WhatsApp number / email address / calendar id), NOT
    the sender — it keeps two accounts' conversations isolated and lets egress
    replies leave via the correct account.
    """

    platform: Platform
    account_id: str
    sender_chat_id: Optional[str]
    payload: Any
    chat_type: str = "dm"
    user_id: Optional[str] = None
    user_name: Optional[str] = None
    chat_name: Optional[str] = None
    thread_id: Optional[str] = None
    task: Optional[str] = None
    # Populated by :func:`bind_channel_principal` once the C1 seam resolves the channel
    # identity to an internal system user; folded into the session key.
    internal_user_id: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.account_id or not str(self.account_id).strip():
            raise ValueError(
                "InboundEvent.account_id is required (the receiving-inbox "
                "identity); a channel event with no account cannot be routed "
                "to the correct egress account."
            )

    def to_source(self) -> SessionSource:
        """Build the :class:`SessionSource` for this event (C4 dimensions set)."""
        return SessionSource(
            platform=self.platform,
            chat_id=self.sender_chat_id or "",
            chat_name=self.chat_name,
            chat_type=self.chat_type,
            user_id=self.user_id,
            user_name=self.user_name,
            thread_id=self.thread_id,
            account_id=str(self.account_id),
            internal_user_id=self.internal_user_id,
            task=self.task,
        )


# ---------------------------------------------------------------------------
# Identity binding (C1 seam) + channel prod-only guard (D5/C3)
# ---------------------------------------------------------------------------


async def bind_channel_principal(
    source: SessionSource,
    *,
    store: "PrincipalStore",
    auto_enroll_if_paired: bool = True,
    is_paired: Optional[Callable[[str, str], bool]] = None,
) -> Optional["Principal"]:
    """Resolve ``source`` to an internal principal and stamp it on the source.

    Binds channel identity → :class:`~hermes_cli.access.Principal` through the
    C1 ``resolve_principal`` seam (which owns pairing/enrolment via
    ``gateway/pairing.py``). On success ``source.internal_user_id`` is set to
    the resolved system ``user_id`` so :func:`build_session_key` isolates the
    session per *internal* user — several channel handles for one person share
    one brain-scoped core. Returns the principal, or ``None`` when the identity
    is unenrolled/unauthorised (the source is left with ``internal_user_id``
    unset, so keying falls back to channel identity only).
    """
    from hermes_cli.access import resolve_principal

    principal = await resolve_principal(
        source,
        store=store,
        auto_enroll_if_paired=auto_enroll_if_paired,
        is_paired=is_paired,
    )
    if principal is not None:
        source.internal_user_id = principal.user_id
    return principal


def guard_channel_prod(
    source: SessionSource,
    *,
    config: Optional[Mapping[str, object]] = None,
) -> Literal["dev", "prod"]:
    """Return the datastore mode for a channel-originated event — always ``prod``.

    Channels are PROD-ONLY (decision D5): there are no dev/staging channels, so
    every channel-origin session forces ``mode=prod`` through the C3 router
    (:func:`hermes_cli.datastore.resolve_mode`). Local/api_server origins are
    not channels and keep their requested mode. Producers call this so a channel
    event can never resolve its shared coordination state to a dev schema.
    """
    from hermes_cli.datastore import resolve_mode

    return resolve_mode(None, source=source, config=config)


# ---------------------------------------------------------------------------
# InboundRouter — in-process queue + bounded, per-session-serial worker pool
# ---------------------------------------------------------------------------


EventHandler = Callable[[InboundEvent, str], Awaitable[Any]]
SessionKeyFn = Callable[[SessionSource], str]


class InboundRouter:
    """Route inbound events to the one shared brain, keyed by C4 ``session_key``.

    The router is the Shape-1 "in-process inbound queue + bounded worker pool":

    * **Per-session serial.** All events for a given ``session_key`` are drained
      by exactly one task, one at a time, in arrival order — so a live
      conversation keeps a byte-stable prompt prefix and strict role
      alternation (prompt-cache safety).
    * **Cross-session parallel.** Distinct ``session_key``s drain concurrently,
      bounded by ``max_concurrent_sessions`` (mirrors the gateway's one cached
      ``AIAgent`` per session). Backpressure: a new session waits for a free
      slot rather than oversubscribing the box.

    ``handler(event, session_key)`` is the injected sink — in the gateway it
    dispatches to the cached per-session ``AIAgent``; in tests it records
    routing. The router never mutates the system prompt or splices tools; new
    knowledge reaches the agent via appended messages / tool queries, not here.
    """

    def __init__(
        self,
        handler: EventHandler,
        *,
        max_concurrent_sessions: int = 8,
        principal_store: Optional["PrincipalStore"] = None,
        session_key_fn: SessionKeyFn = build_session_key,
    ) -> None:
        if max_concurrent_sessions < 1:
            raise ValueError("max_concurrent_sessions must be >= 1")
        self._handler = handler
        self._principal_store = principal_store
        self._session_key_fn = session_key_fn
        self._sem = asyncio.Semaphore(max_concurrent_sessions)
        self._lock = asyncio.Lock()
        self._queues: Dict[str, Deque[InboundEvent]] = {}
        self._tasks: Dict[str, "asyncio.Task[None]"] = {}

    async def submit(self, event: InboundEvent) -> str:
        """Enqueue ``event`` for its session and return the resolved key.

        Binds the principal (when a store is configured), forces the channel
        prod-only mode guard, computes the C4 ``session_key``, appends the event
        to that session's serial queue, and starts a drain task if none runs.
        """
        source = event.to_source()
        if self._principal_store is not None:
            await bind_channel_principal(source, store=self._principal_store)
            event.internal_user_id = source.internal_user_id
        # D5/C3: channel origin is always prod. Calling the guard here makes the
        # invariant explicit (and would surface a misconfigured router).
        guard_channel_prod(source)

        session_key = self._session_key_fn(source)

        async with self._lock:
            queue = self._queues.setdefault(session_key, deque())
            queue.append(event)
            if session_key not in self._tasks:
                self._tasks[session_key] = asyncio.create_task(
                    self._drain(session_key)
                )
        return session_key

    async def _drain(self, session_key: str) -> None:
        # Holding a semaphore slot for the life of this session's burst bounds
        # cross-session concurrency; the single-task-per-key invariant (guarded
        # by _tasks under _lock) bounds per-session concurrency to serial.
        async with self._sem:
            while True:
                async with self._lock:
                    queue = self._queues.get(session_key)
                    if not queue:
                        # Drop the empty session bookkeeping under the same lock
                        # a producer would take, so no event is lost in the gap.
                        self._tasks.pop(session_key, None)
                        self._queues.pop(session_key, None)
                        return
                    event = queue.popleft()
                try:
                    await self._handler(event, session_key)
                except Exception:
                    logger.exception(
                        "inbound handler failed for session %s", session_key
                    )

    async def drain_idle(self) -> None:
        """Await all currently-running session drains (test/shutdown helper)."""
        while True:
            async with self._lock:
                tasks = list(self._tasks.values())
            if not tasks:
                return
            await asyncio.gather(*tasks, return_exceptions=True)
