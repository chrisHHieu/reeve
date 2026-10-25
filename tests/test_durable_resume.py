"""A prompt that is still waiting when the process goes away is answered later, and the turn goes on
from the saved thread, with nothing kept awaiting in memory. `_run_until_pending` stands in for the
restart, and the other modules that need one use it too.
"""

import asyncio

from harness.interface.server.manager import SessionManager
from harness.llm.providers import AssistantTurn, ModelCapabilities, ProviderClient, ToolCall


class ScriptedProvider(ProviderClient):
    """Answers each request with the next queued turn."""

    def __init__(self, turns):
        self._turns = list(turns)

    def complete(self, *, model, messages, tools=None, **settings):
        return self._turns.pop(0)

    def capabilities(self, model):
        return ModelCapabilities()


def _tool(name, args, call_id):
    return AssistantTurn(tool_calls=[ToolCall(id=call_id, name=name, arguments=args)])


def _text(text):
    return AssistantTurn(text=text, finish_reason="stop")


async def _run_until_pending(mgr, sid, engine):
    """Runs a turn until it parks a prompt in the inbox, then does what a restart does to it: the
    turn is cancelled, the engine dropped and the session marked idle. Gives back the parked item."""
    turn = asyncio.create_task(_drain(engine.run("go")))
    waiting = []
    for _ in range(100):
        await asyncio.sleep(0.02)
        waiting = mgr.inbox.pending(sid)
        if waiting:
            break
    assert waiting, "the prompt never reached the inbox"
    turn.cancel()
    try:
        await turn
    except asyncio.CancelledError:
        pass
    mgr._engines.pop(sid, None)
    mgr.mark_idle(sid)
    return waiting[0]


async def _drain(events):
    async for _ in events:
        pass


def _answers(mgr, sid):
    return [m.get("content") for m in mgr.session_store.load(sid).messages if m.get("role") == "assistant" and m.get("content")]


def _answered_after_a_restart(tmp_path, sid, turns, answer, check_parked):
    manager = SessionManager(workspace=tmp_path, provider=ScriptedProvider(turns))

    async def scenario():
        item = await _run_until_pending(manager, sid, manager.get_engine(sid, agent="reeve", workspace=str(tmp_path)))
        check_parked(item)
        await manager.resolve_inbox(item.id, answer)  # answered after the restart

    asyncio.run(scenario())
    return manager


def test_a_question_answered_after_a_restart_finishes_its_turn(tmp_path):
    def parked(item):
        assert (item.kind, item.tool_call_id) == ("question", "call_q")

    asked = _tool("ask_user", {"question": "Which region?", "options": ["us-east-1", "us-west-2"]}, "call_q")
    manager = _answered_after_a_restart(tmp_path, "dur-q", [asked, _text("You chose us-west-2.")], "us-west-2", parked)
    assert any("us-west-2" in (said or "") for said in _answers(manager, "dur-q"))
    assert manager.inbox.pending("dur-q") == []


def test_an_approval_given_after_a_restart_runs_the_call_it_approved(tmp_path):
    # the write waits for approval; "allow" after the restart has to run it, not just record it
    marker = tmp_path / "scratch_marker.txt"

    def parked(item):
        assert (item.kind, item.tool_call_id) == ("approval", "call_w")
        assert not marker.exists()  # nothing ran before the answer

    write = _tool("write_file", {"path": str(marker), "content": "ok"}, "call_w")
    manager = _answered_after_a_restart(tmp_path, "dur-a", [write, _text("Done — file written.")], "allow", parked)
    assert marker.exists() and marker.read_text() == "ok"
    assert any("Done" in (said or "") for said in _answers(manager, "dur-a"))
