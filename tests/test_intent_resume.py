"""Item 2.2 — a restart must not silently repeat a call that was already under way.

`resume()` re-runs the trailing tool calls with no result in the persisted thread. For a
call that never ran that is exactly right, and it is how a suspended approval finishes
after a restart. For a call that DID run and whose result never reached disk it is a
second live send — and from the engine's side the two are indistinguishable, because the
only evidence either leaves is a missing result.

The property under test is NOT "the engine knows whether the email went out". It cannot
know. It is that the engine knows it does not know, and asks.

These tests model the persistence boundary the way the real consumers do: what reaches
"disk" is `engine.messages` at each CHECKPOINT_EVENTS event, and a crash is the moment
consumption stops WITHOUT that write.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from harness.loop import USER_DENY_MESSAGE, ApprovalOutcome, TurnEngine
from harness.session.events import CHECKPOINT_EVENTS, EventType
from harness.safety.permissions import Mode, PermissionEngine
from harness.llm.providers import AssistantTurn, ModelCapabilities, ProviderClient, ToolCall
from harness.safety.risk import classify
from harness.tools import ToolRegistry
from harness.tools.intents import IntentStore, intent_listener


@dataclass
class _Meta:
    category: str = "connector"
    risk_level: str = "high"
    requires_approval: bool = False  # True => RiskClass.EXTERNAL


class _Scripted(ProviderClient):
    def __init__(self, turns: list[AssistantTurn]) -> None:
        self._turns = list(turns)

    def complete(self, *, model, messages, tools=None, **settings):
        if self._turns:
            return self._turns.pop(0)
        return AssistantTurn(text="done", finish_reason="stop")

    def capabilities(self, model):
        return ModelCapabilities()


class _World:
    """The far end of the outbound call — the thing a repeat would hit twice."""

    def __init__(self) -> None:
        self.sent: list[str] = []


def _registry(world: _World) -> ToolRegistry:
    def send_message(target: str, text: str) -> str:
        """Send a message.

        Args:
            target: where
            text: what
        """
        world.sent.append(f"{target}:{text}")
        return "sent"

    def read_note(path: str) -> str:
        """Read a note.

        Args:
            path: which
        """
        return "note body"

    registry = ToolRegistry()
    registry.register(send_message, metadata=_Meta(requires_approval=True))
    registry.register(read_note, metadata=_Meta(category="", risk_level="low"))
    return registry


def _engine(
    tmp: Path,
    world: _World,
    turns: list[AssistantTurn],
    *,
    messages: Optional[list[dict]] = None,
    mode: Mode = Mode.DANGER_FULL_ACCESS,
    approver: Any = None,
    store: Any = "default",
) -> TurnEngine:
    registry = _registry(world)
    engine = TurnEngine(
        provider=_Scripted(turns),
        registry=registry,
        permissions=PermissionEngine(workspace_root=tmp, mode=mode),
        model="m",
        approver=approver,
        messages=messages,
    )
    if store == "default":
        store = IntentStore(tmp / ".reeve" / "intents")
    if store is not None:
        engine.intents = store
        engine.pipeline.on_pre_execute(
            intent_listener(
                store,
                lambda name: classify(
                    name,
                    (registry.get(name).metadata if registry.get(name) else None),
                    None,
                ),
            )
        )
    return engine


def _calls(*specs: tuple[str, str, dict]) -> AssistantTurn:
    return AssistantTurn(
        tool_calls=[ToolCall(id=cid, name=name, arguments=args) for cid, name, args in specs],
        finish_reason="tool_calls",
    )


async def _crash(
    engine: TurnEngine, at: EventType, tool: str = "", *, after: bool = False
) -> list[dict]:
    """Consume the turn, checkpointing as the server does, then die at `at`.

    `after=False` dies BEFORE that event's checkpoint is written — the crash that loses a
    result. `after=True` dies just after it — which is how you land inside the approval
    await, with the card already on disk and the tool not yet run. Returns what reached
    disk, which is what a rebuilt engine would be handed.
    """
    disk: list[dict] = []
    async for event in engine.run("go"):
        hit = event.type is at and (not tool or event.data.get("name") == tool)
        if hit and not after:
            return disk
        if event.type in CHECKPOINT_EVENTS:
            disk = [dict(m) for m in engine.messages]
        if hit:
            return disk
    return disk


async def _collect(events: Any) -> list[Any]:
    return [event async for event in events]


def _cards(events: list[Any]) -> list[Any]:
    return [e for e in events if e.type == EventType.PERMISSION_REQUIRED]


async def _approve(_request: Any) -> ApprovalOutcome:
    return ApprovalOutcome.ONCE


async def _decline(_request: Any) -> ApprovalOutcome:
    return ApprovalOutcome.DENY


SEND = ("c2", "send_message", {"target": "slack:T1/C1", "text": "the invoice is attached"})
READ = ("c1", "read_note", {"path": "a.md"})


# -- the bug, so the harness is known to be able to see it ------------------------


def test_without_the_store_a_restart_sends_twice(tmp_path):
    """The reproduction this item was built from. Not a hypothetical: the checkpoint
    written by the FIRST tool in the batch persists the assistant message, so the second
    call is on disk as unanswered while its side effect has already happened."""
    world = _World()

    async def go():
        crashed = _engine(tmp_path, world, [_calls(READ, SEND)], store=None)
        disk = await _crash(crashed, EventType.TOOL_FINISHED, "send_message")
        assert world.sent == ["slack:T1/C1:the invoice is attached"]

        resumed = _engine(tmp_path, world, [], messages=disk, store=None)
        await _collect(resumed.resume())

    asyncio.run(go())
    assert len(world.sent) == 2  # sent again, nobody asked, nobody told


# -- with the store: it asks -------------------------------------------------------


def test_a_started_call_is_not_repeated_on_its_own(tmp_path):
    world = _World()

    async def go():
        crashed = _engine(tmp_path, world, [_calls(READ, SEND)])
        disk = await _crash(crashed, EventType.TOOL_FINISHED, "send_message")
        assert world.sent == ["slack:T1/C1:the invoice is attached"]

        resumed = _engine(
            tmp_path, world, [], messages=disk, mode=Mode.WORKSPACE_WRITE, approver=_decline
        )
        events = await _collect(resumed.resume())
        # It reached a person rather than the far end.
        cards = _cards(events)
        assert len(cards) == 1 and cards[0].data["name"] == "send_message"
        assert "may already have happened" in cards[0].data["reason"]
        return resumed

    resumed = asyncio.run(go())
    assert len(world.sent) == 1  # declined: not sent twice

    # And the model is told the truth about it — NOT that the user refused the action.
    refusals = [
        str(m.get("content", ""))
        for m in resumed.messages
        if m.get("role") == "tool" and m.get("tool_call_id") == "c2"
    ]
    # Matched on fragments, not the whole constant: the tool result is JSON, so the
    # message's em-dash arrives as an escape and a literal comparison would fail for a
    # reason that has nothing to do with the behaviour.
    assert refusals, "the declined call left the model with no answer at all"
    assert "may have taken effect" in refusals[0]
    assert "Do not simply try again" in refusals[0]
    assert USER_DENY_MESSAGE not in refusals[0]


def test_the_person_may_still_say_send_it_again(tmp_path):
    """Asking is the point; refusing is not. Someone who checked the channel and saw
    nothing must be able to say go ahead."""
    world = _World()

    async def go():
        crashed = _engine(tmp_path, world, [_calls(READ, SEND)])
        disk = await _crash(crashed, EventType.TOOL_FINISHED, "send_message")
        resumed = _engine(
            tmp_path, world, [], messages=disk, mode=Mode.WORKSPACE_WRITE, approver=_approve
        )
        await _collect(resumed.resume())

    asyncio.run(go())
    assert len(world.sent) == 2  # a person decided this, which is the whole difference


def test_full_access_hands_a_started_call_back_to_the_model(tmp_path):
    """Full access asks nobody, and must not send twice either. The call is not
    re-run and no card is raised; the model is told it may have happened and decides."""
    world = _World()

    async def go():
        crashed = _engine(tmp_path, world, [_calls(READ, SEND)])
        disk = await _crash(crashed, EventType.TOOL_FINISHED, "send_message")
        resumed = _engine(tmp_path, world, [], messages=disk, approver=_approve)
        events = await _collect(resumed.resume())
        assert _cards(events) == []
        return resumed

    resumed = asyncio.run(go())
    assert len(world.sent) == 1
    told = [
        str(m.get("content", ""))
        for m in resumed.messages
        if m.get("role") == "tool" and m.get("tool_call_id") == "c2"
    ]
    assert told and "call it again only if it did not" in told[0]


def test_the_approval_path_window_closes_too(tmp_path):
    """The other shape: no batch, but the approval card itself checkpointed the assistant
    message before the tool ran."""
    world = _World()

    async def go():
        crashed = _engine(
            tmp_path, world, [_calls(SEND)], mode=Mode.WORKSPACE_WRITE, approver=_approve
        )
        disk = await _crash(crashed, EventType.TOOL_FINISHED, "send_message")
        assert len(world.sent) == 1

        resumed = _engine(
            tmp_path, world, [], messages=disk, mode=Mode.WORKSPACE_WRITE, approver=_decline
        )
        events = await _collect(resumed.resume())
        assert len(_cards(events)) == 1

    asyncio.run(go())
    assert len(world.sent) == 1


# -- and it must not break durable resume, which is the same code path -------------


def test_a_call_that_never_started_still_resumes_silently(tmp_path):
    """The control that matters most. Durable resume exists so a turn suspended at an
    approval finishes after a restart — if every resumed call now asked again, this
    change would have broken the feature it lives inside. Crashing AT the card means the
    tool never ran, so no note exists, so nothing is uncertain."""
    world = _World()

    async def go():
        crashed = _engine(
            tmp_path, world, [_calls(SEND)], mode=Mode.WORKSPACE_WRITE, approver=_approve
        )
        disk = await _crash(crashed, EventType.PERMISSION_REQUIRED, after=True)
        assert world.sent == []  # died before it ran

        resumed = _engine(
            tmp_path, world, [], messages=disk, mode=Mode.WORKSPACE_WRITE, approver=_approve
        )
        events = await _collect(resumed.resume())
        return events

    events = asyncio.run(go())
    assert len(world.sent) == 1  # it ran, once
    # One card: the ordinary approval for this action, not an "already happened?" card.
    cards = _cards(events)
    assert len(cards) == 1
    assert "may already have happened" not in cards[0].data["reason"]


def test_reads_are_never_noted(tmp_path):
    """Scope. A read has no effect to repeat, so noting it would cost a card on every
    restart for nothing."""
    world = _World()
    store = IntentStore(tmp_path / ".reeve" / "intents")

    async def go():
        engine = _engine(tmp_path, world, [_calls(READ)], store=store)
        await _collect(engine.run("go"))

    asyncio.run(go())
    assert store.pending("c1") is None


# -- the failure policy, which is the opposite of undo's ---------------------------


def test_a_note_that_cannot_be_written_blocks_the_call(tmp_path):
    """Fail CLOSED, deliberately unlike `snapshot_listener`. A backup that failed is no
    reason to refuse a write; a missing intent note IS a reason to refuse a send, because
    the note is the only thing standing between a crash and a second one."""
    world = _World()

    class _Broken(IntentStore):
        def record(self, call_id, tool, arguments=None):
            raise OSError("no space left on device")

    async def go():
        engine = _engine(
            tmp_path, world, [_calls(SEND)], store=_Broken(tmp_path / "x")
        )
        await _collect(engine.run("go"))
        return engine

    engine = asyncio.run(go())
    assert world.sent == []  # refused rather than sent blind
    blocked = [
        str(m.get("content", ""))
        for m in engine.messages
        if m.get("role") == "tool" and m.get("tool_call_id") == "c2"
    ]
    assert blocked and "blocked" in blocked[0]
