"""Approve-with-edit: the person fixes the call, the engine re-checks it.

What is pinned here:
- the edited arguments RUN, history carries them (not the model's original), and the
  session log keeps both facts (`tool/call` = what the model asked, `tool/edited` = what
  actually ran);
- an edit is a NEW call: it is re-checked, so an edit that walks into a floor or a
  read-only preset is refused — with the reason named — and NOTHING runs;
- an edit that still needs a decision asks again, with the edited arguments on the card
  (the original consent never carries over);
- an empty/absent edit is a refusal, and the mapping layer treats a malformed decision
  as one too.
"""

from __future__ import annotations

import asyncio
import json

import aisuite as ai

from harness.loop import ApprovalOutcome, PermissionRequest, TurnEngine
from harness.session.events import EventType
from harness.safety.permissions import Mode, PermissionEngine
from harness.llm.providers import AssistantTurn, ModelCapabilities, ProviderClient
from harness.llm.providers.base import ToolCall
from harness.session.session_log import SessionLog
from harness.tools import ToolRegistry


class _Asking(ProviderClient):
    """One scripted tool call, then a plain answer."""

    def __init__(self, arguments: dict) -> None:
        self.arguments = arguments
        self.messages_seen: list[list[dict]] = []

    def stream(self, *, model, messages, tools=None, **settings):
        from harness.llm.providers import StreamChunk

        self.messages_seen.append([dict(m) for m in messages])
        if len(self.messages_seen) == 1:
            yield StreamChunk(
                turn=AssistantTurn(
                    text="",
                    finish_reason="tool_calls",
                    tool_calls=[
                        ToolCall(id="c1", name="write_file", arguments=self.arguments)
                    ],
                )
            )
        else:
            yield StreamChunk(turn=AssistantTurn(text="done", finish_reason="stop"))

    def complete(self, *, model, messages, tools=None, **settings):
        raise AssertionError("the engine must stream")

    def capabilities(self, model):
        return ModelCapabilities()


class _Approver:
    """Answers the first card with `edit` (or a scripted outcome) and records requests."""

    def __init__(self, *, edit=None, outcome=ApprovalOutcome.MODIFY, then=None) -> None:
        self.edit = edit
        self.outcome = outcome
        self.then = then
        self.requests: list[PermissionRequest] = []

    async def __call__(self, request: PermissionRequest) -> ApprovalOutcome:
        self.requests.append(request)
        if len(self.requests) == 1:
            if self.edit is not None:
                request.edited_arguments = self.edit
            return self.outcome
        return self.then or ApprovalOutcome.DENY


def _engine(tmp_path, provider, approver, *, mode=Mode.WORKSPACE_WRITE):
    registry = ToolRegistry()
    registry.register_all(
        ai.toolkits.files(roots=[{"path": str(tmp_path), "writable": True}])
    )
    engine = TurnEngine(
        provider=provider,
        registry=registry,
        permissions=PermissionEngine(workspace_root=tmp_path, mode=mode),
        model="gpt-5.5",
        approver=approver,
    )
    engine.log = SessionLog(tmp_path / "events.jsonl", trace="t1")
    return engine


def _run(engine):
    async def go():
        return [ev async for ev in engine.run("write the file")]

    return asyncio.run(go())


def _assistant_tool_calls(engine) -> list[dict]:
    for message in engine.messages:
        if message.get("role") == "assistant" and message.get("tool_calls"):
            return message["tool_calls"]
    return []


# -- the edit runs, and every record says so -----------------------------------------


def test_an_edited_call_runs_with_the_persons_arguments(tmp_path):
    provider = _Asking({"path": "notes.txt", "content": "from the model"})
    approver = _Approver(edit={"path": "notes.txt", "content": "edited by the person"})
    engine = _engine(tmp_path, provider, approver)
    events = _run(engine)

    assert (tmp_path / "notes.txt").read_text(encoding="utf-8") == "edited by the person"
    finished = [e for e in events if e.type is EventType.TOOL_FINISHED]
    assert finished and finished[-1].data.get("status") == "ok"

    # History carries the arguments that RAN — a model that read back its original call
    # would believe it asked for the other content.
    calls = _assistant_tool_calls(engine)
    assert json.loads(calls[0]["function"]["arguments"]) == {
        "path": "notes.txt",
        "content": "edited by the person",
    }

    # ...and the log keeps both sides: the model's original arguments, and the ones the
    # dispatch actually carried.
    logged = engine.log.read()
    edited = [e for e in logged if e["type"] == "tool/edited"]
    assert edited and edited[0]["original"]["content"] == "from the model"
    assert edited[0]["arguments"]["content"] == "edited by the person"
    assert edited[0]["call_id"] == "c1"
    dispatched = [e for e in logged if e["type"] == "tool/call"]
    assert json.loads(dispatched[-1]["arguments"])["content"] == "edited by the person"

    # The very next request the provider saw contains the edited arguments.
    assert "edited by the person" in json.dumps(provider.messages_seen[-1])


def test_the_request_the_approver_saw_is_the_model_call(tmp_path):
    provider = _Asking({"path": "a.txt", "content": "x"})
    approver = _Approver(edit={"path": "a.txt", "content": "y"})
    _run(_engine(tmp_path, provider, approver))
    assert approver.requests[0].tool_name == "write_file"
    assert approver.requests[0].arguments["content"] == "x"


# -- an edit is re-checked, and a refusal runs nothing --------------------------------


def test_an_edit_into_a_floor_is_refused(tmp_path):
    """The edit is re-checked, and a FLOOR survives it: a file that runs later still needs a
    person to SEE it, so rewriting the call cannot clear that consent."""
    provider = _Asking({"path": "a.txt", "content": "x"})
    approver = _Approver(edit={"path": ".git/hooks/pre-commit", "content": "echo edited"})
    engine = _engine(tmp_path, provider, approver)
    events = _run(engine)

    assert not (tmp_path / ".git" / "hooks" / "pre-commit").exists()
    finished = [e for e in events if e.type is EventType.TOOL_FINISHED]
    assert finished and finished[-1].data.get("status") == "denied"
    assert "not run" in finished[-1].data.get("reason", "")


def test_read_only_never_offers_a_card_to_edit(tmp_path):
    """Nothing to edit when the preset refuses outright: the person's Run is consent, and
    consent is exactly what read-only does not take for consequential calls."""
    provider = _Asking({"path": "a.txt", "content": "x"})
    approver = _Approver(edit={"path": "a.txt", "content": "y"})
    engine = _engine(tmp_path, provider, approver, mode=Mode.READ_ONLY)
    events = _run(engine)

    assert approver.requests == [], "read-only denies before any card"
    assert not (tmp_path / "a.txt").exists()
    finished = [e for e in events if e.type is EventType.TOOL_FINISHED]
    assert finished and finished[-1].data.get("status") == "denied"


def test_an_empty_edit_is_refused(tmp_path):
    provider = _Asking({"path": "a.txt", "content": "x"})
    approver = _Approver(edit=None)  # MODIFY with nothing attached
    engine = _engine(tmp_path, provider, approver)
    events = _run(engine)

    assert not (tmp_path / "a.txt").exists()
    finished = [e for e in events if e.type is EventType.TOOL_FINISHED]
    assert finished and finished[-1].data.get("status") == "denied"
    assert "no arguments" in finished[-1].data.get("reason", "")


# -- the mapping layer (any surface: WS card, Inbox REST, a channel) ------------------


def _manager(tmp_path, monkeypatch):
    from harness.interface.server.manager import SessionManager

    monkeypatch.setenv("REEVE_STATE_DIR", str(tmp_path / "state"))
    return SessionManager(workspace=tmp_path, provider=_Asking({}))


def _request():
    return PermissionRequest(
        tool_name="write_file",
        arguments={"path": "a.txt", "content": "x"},
        metadata=None,
        reason="requires approval",
    )


def test_a_modify_resolution_carries_the_arguments_and_anything_malformed_denies(
    tmp_path, monkeypatch
):
    mgr = _manager(tmp_path, monkeypatch)
    request = _request()
    outcome = mgr.approval_outcome(
        json.dumps({"decision": "modify", "arguments": {"path": "b.txt", "content": "y"}}),
        request,
        "s1",
    )
    assert outcome is ApprovalOutcome.MODIFY
    assert request.edited_arguments == {"path": "b.txt", "content": "y"}

    for bad in (
        "{not json",
        json.dumps({"decision": "modify"}),
        json.dumps({"decision": "modify", "arguments": {}}),
        json.dumps({"decision": "modify", "arguments": "nope"}),
        json.dumps({"decision": "once"}),
    ):
        other = _request()
        assert mgr.approval_outcome(bad, other, "s1") is ApprovalOutcome.DENY, bad
        assert other.edited_arguments is None


# -- the parked card is the live card (GUI UX review, U2) -------------------------------


def test_the_request_carries_the_live_card_so_a_parked_prompt_draws_the_same_one(tmp_path):
    """Back in a session waiting on an approval, the card lost Always allow and Edit: the
    parked Inbox item held only the tool, its arguments and a sentence, while the live card
    reads `axis`, `provenance`, `standing_target`. The engine now hands the
    approver the exact payload it sent live, and the parked item keeps it."""
    import types

    from harness.interface.server.manager import SessionManager

    approver = _Approver(outcome=ApprovalOutcome.DENY)
    engine = _engine(tmp_path, _Asking({"path": str(tmp_path / "a.txt"), "content": "x"}), approver)
    events = _run(engine)

    [live] = [e.data for e in events if e.type == EventType.PERMISSION_REQUIRED]
    [request] = approver.requests
    assert request.card == live
    assert {"axis", "standing_target", "provenance"} <= set(request.card)

    data = SessionManager.approval_prompt_data(
        types.SimpleNamespace(task_store=types.SimpleNamespace(task_for_run_session=lambda _s: None)),
        "s1",
        request,
    )
    # What the parked card needs beyond tool + arguments, stored once (not the arguments twice).
    assert data["card"] == {k: v for k, v in live.items() if k not in ("name", "arguments", "presentation", "category")}
    assert data["arguments"] == live["arguments"]


# -- an ask no mode can clear says so (the card's `human_only`) --------------------------


def test_the_card_says_when_no_grant_can_clear_the_ask(tmp_path):
    """Below full access a file that runs later (a git hook) puts a person in front of the
    call whatever grant the session holds, and the card has to say so."""
    hook = tmp_path / ".git" / "hooks" / "pre-commit"
    approver = _Approver(outcome=ApprovalOutcome.DENY)
    engine = _engine(
        tmp_path,
        _Asking({"path": str(hook), "content": "#!/bin/sh\n"}),
        approver,
        mode=Mode.WORKSPACE_WRITE,
    )
    engine.permissions.allow_tool_for_session("write_file")
    events = _run(engine)

    [live] = [e.data for e in events if e.type == EventType.PERMISSION_REQUIRED]
    assert live["human_only"] is True
    assert "runs automatically later" in live["reason"]


def test_full_access_asks_nobody_even_for_a_file_that_runs_later(tmp_path):
    """Full access is the person's answer given in advance. The same git hook
    write runs there with no card, and the approver is never called."""
    hook = tmp_path / ".git" / "hooks" / "pre-commit"
    approver = _Approver(outcome=ApprovalOutcome.DENY)
    engine = _engine(
        tmp_path,
        _Asking({"path": str(hook), "content": "#!/bin/sh\n"}),
        approver,
        mode=Mode.DANGER_FULL_ACCESS,
    )
    events = _run(engine)

    assert not [e for e in events if e.type == EventType.PERMISSION_REQUIRED]
    assert hook.read_text() == "#!/bin/sh\n"


def test_an_ordinary_ask_is_not_marked_as_one_no_mode_can_clear(tmp_path):
    """The same card for a plain write in the default preset: a person's OK, but a grant or a
    mode could have answered it, so it must not claim to be a floor."""
    approver = _Approver(outcome=ApprovalOutcome.DENY)
    engine = _engine(
        tmp_path, _Asking({"path": str(tmp_path / "a.txt"), "content": "x"}), approver
    )
    events = _run(engine)

    [live] = [e.data for e in events if e.type == EventType.PERMISSION_REQUIRED]
    assert live["human_only"] is False


def test_full_access_runs_an_ordinary_write_without_a_card(tmp_path):
    """The other half of the promise the picker makes."""
    approver = _Approver(outcome=ApprovalOutcome.DENY)
    target = tmp_path / "a.txt"
    engine = _engine(
        tmp_path,
        _Asking({"path": str(target), "content": "x"}),
        approver,
        mode=Mode.DANGER_FULL_ACCESS,
    )
    events = _run(engine)

    assert [e for e in events if e.type == EventType.PERMISSION_REQUIRED] == []
    assert approver.requests == []
    assert target.read_text() == "x"
