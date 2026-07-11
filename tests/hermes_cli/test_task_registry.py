from datetime import datetime, timezone

import pytest

from hermes_cli.access import Principal
from hermes_cli.consent import ConsentPolicy
from hermes_cli.task_registry import (
    TaskDiscoveryEngine,
    TaskRecord,
    normalize_intent,
    synthesize_task_spec,
    validate_progress_transition,
)


MEMBER = Principal(user_id="member-a", display="Member A", role="member")


class FakeSignals:
    def __init__(self) -> None:
        self.count = 0
        self.recorded: list[tuple[str, str | None]] = []

    async def record(
        self,
        principal: Principal,
        normalized_intent: str,
        *,
        source_session: str | None = None,
    ) -> int:
        assert principal == MEMBER
        self.recorded.append((normalized_intent, source_session))
        self.count += 1
        return self.count


class FakeRegistry:
    mode = "prod"

    def __init__(self) -> None:
        self.task: TaskRecord | None = None
        self.proposals = []

    async def find_discovered_task(
        self, principal: Principal, normalized_intent: str
    ) -> TaskRecord | None:
        return self.task

    async def last_proposed_at(
        self, principal: Principal, normalized_intent: str
    ) -> datetime | None:
        return None

    async def recent_auto_approvals(
        self, principal: Principal, *, since: datetime
    ) -> int:
        return 0

    async def record_proposal(
        self, principal, normalized_intent, signal_count, decision
    ) -> None:
        self.proposals.append((normalized_intent, signal_count, decision))

    async def create_task(
        self,
        principal,
        spec,
        *,
        origin,
        normalized_intent,
    ) -> TaskRecord:
        self.task = TaskRecord(
            id="task-1",
            owner_user_id=principal.user_id,
            visibility=principal.private_visibility,
            title=spec.title,
            description=spec.description,
            trigger_state=spec.trigger_state,
            completion_state=spec.completion_state,
            current_state=spec.trigger_state,
            status="pending",
            origin=origin,
            normalized_intent=normalized_intent,
            created_at=None,
            updated_at=None,
        )
        return self.task


class FakeChangeRecorder:
    def __init__(self) -> None:
        self.calls = []

    async def record(self, **kwargs) -> object:
        self.calls.append(kwargs)
        return object()


def test_intent_normalization_and_spec_synthesis() -> None:
    normalized = normalize_intent(
        "Please, can you   SEND the weekly status report?!"
    )
    assert normalized == "send the weekly status report"

    spec = synthesize_task_spec(normalized, signal_count=3)
    assert spec.title == "Send the weekly status report"
    assert spec.trigger_state == "pending"
    assert spec.completion_state == "completed"
    assert spec.progress_states == ("pending", "in_progress", "completed")


@pytest.mark.asyncio
async def test_repetition_threshold_proposes_then_persists_with_c5_record() -> None:
    registry = FakeRegistry()
    signals = FakeSignals()
    changes = FakeChangeRecorder()
    proposals: list[str] = []
    engine = TaskDiscoveryEngine(
        registry,
        signals,
        threshold=3,
        policy=ConsentPolicy(auto_approve_reversible=True),
        proposal_sink=proposals.append,
        change_recorder=changes,
    )

    first = await engine.observe_prompt(MEMBER, "Please send the report")
    second = await engine.observe_prompt(MEMBER, "Can you send the report?")
    third = await engine.observe_prompt(MEMBER, "send the report")

    assert first.action == second.action == "below_threshold"
    assert third.action == "task_accepted"
    assert third.task is registry.task
    assert len(proposals) == 1
    assert len(registry.proposals) == 1
    assert changes.calls[0]["target_ref"] == "task-1"
    assert changes.calls[0]["approved"] is True


@pytest.mark.asyncio
async def test_discovered_task_activity_does_not_feed_discovery() -> None:
    signals = FakeSignals()
    engine = TaskDiscoveryEngine(FakeRegistry(), signals)

    outcome = await engine.observe_prompt(
        MEMBER,
        "send the report",
        origin="discovered_task",
    )

    assert outcome.action == "ignored_origin"
    assert signals.recorded == []


def test_progress_transitions_are_strictly_ordered() -> None:
    states = ("queued", "drafted", "reviewed", "sent")
    validate_progress_transition(states, "queued", "drafted")

    with pytest.raises(ValueError, match="Invalid progress transition"):
        validate_progress_transition(states, "queued", "reviewed")
    with pytest.raises(ValueError, match="Unknown target progress state"):
        validate_progress_transition(states, "drafted", "missing")


@pytest.mark.asyncio
async def test_rate_limit_uses_c6_prompt_path() -> None:
    class RateLimitedRegistry(FakeRegistry):
        async def recent_auto_approvals(
            self, principal: Principal, *, since: datetime
        ) -> int:
            assert since.tzinfo == timezone.utc
            return 1

    prompts = []

    def approve(command: str, description: str, **_: object) -> str:
        prompts.append((command, description))
        return "once"

    engine = TaskDiscoveryEngine(
        RateLimitedRegistry(),
        FakeSignals(),
        threshold=2,
        policy=ConsentPolicy(
            auto_approve_reversible=True,
            rate_limit_max=1,
        ),
        approval_callback=approve,
    )

    await engine.observe_prompt(MEMBER, "send the report")
    outcome = await engine.observe_prompt(MEMBER, "send the report")

    assert outcome.action == "task_accepted"
    assert outcome.decision is not None
    assert outcome.decision.mode == "prompted"
    assert outcome.decision.reason == "rate_limited"
    assert len(prompts) == 1


@pytest.mark.asyncio
async def test_quiet_hours_require_explicit_c6_approval() -> None:
    prompts = []

    def deny(command: str, description: str, **_: object) -> str:
        prompts.append((command, description))
        return "deny"

    engine = TaskDiscoveryEngine(
        FakeRegistry(),
        FakeSignals(),
        threshold=2,
        policy=ConsentPolicy(
            auto_approve_reversible=True,
            quiet_hours_start=22,
            quiet_hours_end=7,
        ),
        approval_callback=deny,
    )

    await engine.observe_prompt(MEMBER, "send the report")
    outcome = await engine.observe_prompt(
        MEMBER,
        "send the report",
        now=datetime(2026, 7, 11, 23, tzinfo=timezone.utc),
    )

    assert outcome.action == "proposal_denied"
    assert outcome.decision is not None
    assert outcome.decision.reason == "quiet_hours"
    assert len(prompts) == 1
