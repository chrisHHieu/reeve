"""A turn and its background commands survive the end of the process that ran them.

Each test is one restart: manager A leaves a session on disk the way a dead process does (its
thread as of the last checkpoint, its turn claim, its running jobs), and manager B, a fresh one
on the same state folder, is started. The scripted provider is B's, so what it is asked is what
the model would be asked.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from harness.interface.server.manager import SessionManager
from harness.interface.server.manager.continuity import CONTINUE_AFTER_RESTART
from harness.llm.providers import AssistantTurn, ModelCapabilities, ProviderClient, ToolCall
from harness.safety.permissions import Mode
from harness.platform import process_owner
from harness.session.continuity import MAX_RESUMES, ContinuityStore
from harness.tools.intents import IntentStore


class Scripted(ProviderClient):
    """Answers in order and keeps every request's messages."""

    def __init__(self, turns=()):
        self.turns = list(turns)
        self.requests: list[list[dict]] = []

    def complete(self, *, model, messages, tools=None, **settings):
        self.requests.append([dict(m) for m in messages])
        if not self.turns:
            return AssistantTurn(text="(nothing scripted)", finish_reason="stop")
        return self.turns.pop(0)

    def capabilities(self, model):
        return ModelCapabilities()


#: A process that has ended: what a claim left by the last process names. No live process has
#: this pid, so `process_owner.running` reads it as gone, on every OS.
GONE = {"pid": 2_147_480_000, "started": 1}


def _manager(tmp: Path, provider: ProviderClient) -> SessionManager:
    return SessionManager(workspace=tmp / "work", data_dir=tmp / "state", provider=provider)


def _leave(tmp: Path, sid: str, tail: list[dict], *, mode: Mode = Mode.WORKSPACE_WRITE, claim: bool = True):
    """What a dead process leaves: the thread as saved, and the claim it never released."""
    (tmp / "work").mkdir(exist_ok=True)
    first = _manager(tmp, Scripted())
    first.continuity.owner = GONE  # it writes as the process that has since ended
    engine = first.get_engine(sid, agent="reeve", workspace=str(tmp / "work"))
    engine.permissions.mode = mode
    engine.messages.extend(tail)
    first.save(sid, engine)
    if claim:
        first.continuity.claim_turn(sid)
    return first, engine


def _restart(tmp: Path, provider: Scripted) -> tuple[SessionManager, dict]:
    second = _manager(tmp, provider)
    counts = asyncio.run(second.resume_interrupted_work())
    return second, counts


def _user_texts(messages: list[dict]) -> list[str]:
    return [m["content"] for m in messages if m.get("role") == "user" and isinstance(m.get("content"), str)]


def _log_rows(tmp: Path, sid: str, kind: str) -> list[dict]:
    rows = []
    for path in (tmp / "state").rglob(f"{sid}*.events.jsonl"):
        for line in path.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            if row.get("type") == kind:
                rows.append(row)
    return rows


def _call(call_id: str, name: str, arguments: dict) -> dict:
    return {"role": "assistant", "content": "", "tool_calls": [
        {"id": call_id, "type": "function", "function": {"name": name, "arguments": json.dumps(arguments)}}
    ]}


# -- the store ------------------------------------------------------------------------------


def test_the_store_keeps_claims_attempts_and_jobs_across_instances(tmp_path):
    path = tmp_path / "continuity.json"
    store = ContinuityStore(path, owner=GONE)
    store.claim_turn("a")
    store.claim_turn("child", parent="a")
    assert store.count_resume("a") == 1
    store.claim_turn("a")  # claiming again while being picked up keeps the count
    store.set_jobs("a", [{"id": "bg-1", "command": "npm run dev", "started_at": 5}])
    store.keep_tails({"a": {"bg-1": "ready on :5173"}})
    store.set_jobs("a", [{"id": "bg-1", "command": "npm run dev", "started_at": 5}])  # the tail survives a sync

    again = ContinuityStore(path)
    assert again.held_turns()["a"]["attempts"] == 1
    assert again.held_turns()["child"]["parent"] == "a"
    assert again.take_jobs() == {"a": {"bg-1": {"command": "npm run dev", "started": 5, "tail": "ready on :5173"}}}
    assert ContinuityStore(path).take_jobs() == {}
    again.release_turn("a")
    assert "a" not in ContinuityStore(path).held_turns()


def test_an_unreadable_store_reads_as_nothing_running(tmp_path):
    path = tmp_path / "continuity.json"
    path.write_text("{not json", encoding="utf-8")
    store = ContinuityStore(path)
    assert store.held_turns() == {} and store.take_jobs() == {}
    store.claim_turn("a")
    assert "a" in ContinuityStore(path).held_turns()


def test_a_turn_ended_in_this_process_releases_its_claim_but_a_shutdown_keeps_it(tmp_path):
    manager = _manager(tmp_path, Scripted())
    assert manager.try_mark_running("s")
    assert "s" in manager.continuity.held_turns()
    manager.mark_idle("s")
    assert "s" not in manager.continuity.held_turns()
    assert manager.try_mark_running("s")
    manager.shutting_down = True
    manager.mark_idle("s")  # cut off by the shutdown: the next process picks it up
    assert "s" in manager.continuity.held_turns()


# -- picking a turn up -----------------------------------------------------------------------


def test_a_turn_stopped_between_steps_continues_where_it_stopped(tmp_path):
    (tmp_path / "work").mkdir()
    (tmp_path / "work" / "notes.txt").write_text("the export drops the last row", encoding="utf-8")
    _leave(tmp_path, "s1", [
        {"role": "user", "content": "Fix the export"},
        _call("c1", "read_file", {"path": "notes.txt"}),
        {"role": "tool", "tool_call_id": "c1", "content": "the export drops the last row"},
    ])
    provider = Scripted([AssistantTurn(text="Fixed after the restart.", finish_reason="stop")])
    second, counts = _restart(tmp_path, provider)

    assert counts["picked_up"] == 1
    # One turn request; a request after it (with no tools) is the session's title, not a turn.
    turns = [r for r in provider.requests if any(t.startswith(CONTINUE_AFTER_RESTART) for t in _user_texts(r))]
    assert len(turns) == 1
    asked = _user_texts(turns[0])
    assert asked[-1].startswith(CONTINUE_AFTER_RESTART)
    assert sum(t.startswith("Fix the export") for t in asked) == 1  # the person's message is not sent again
    saved = second.session_store.load("s1").messages
    restart = [m for m in saved if m.get("role") == "user" and (m.get("source") or {}).get("synthetic") == "restart"]
    assert restart and restart[0]["source"]["steps"] == 1
    assert any(m.get("role") == "assistant" and m.get("content") == "Fixed after the restart." for m in saved)
    assert "s1" not in second.continuity.held_turns()
    resumed = _log_rows(tmp_path, "s1", "turn/resumed")
    assert resumed and resumed[0]["path"] == "continue" and resumed[0]["attempt"] == 1


def test_a_message_nobody_answered_is_run_again_as_itself(tmp_path):
    _leave(tmp_path, "s2", [{"role": "user", "content": "What changed in v2?"}])
    provider = Scripted([AssistantTurn(text="Here is what changed.", finish_reason="stop")])
    second, _ = _restart(tmp_path, provider)

    asked = _user_texts(provider.requests[0])
    # Sent as itself (with this turn's live context after it, as every turn's message is).
    assert asked[-1].startswith("What changed in v2?")
    assert sum(t.startswith("What changed in v2?") for t in asked) == 1
    assert not any(text.startswith(CONTINUE_AFTER_RESTART) for text in asked)
    saved = second.session_store.load("s2").messages
    assert _user_texts(saved).count("What changed in v2?") == 1
    assert any(m.get("role") == "notice" and m.get("kind") == "restart" for m in saved)


def test_a_turn_that_ended_in_an_answer_runs_nothing(tmp_path):
    _leave(tmp_path, "s3", [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "Hello."},
    ])
    provider = Scripted()
    second, counts = _restart(tmp_path, provider)
    assert provider.requests == []
    assert counts["finished"] == 1
    assert "s3" not in second.continuity.held_turns()


def test_a_turn_that_keeps_ending_the_process_is_given_up_on(tmp_path):
    first, _ = _leave(tmp_path, "s4", [{"role": "user", "content": "loop forever"}])
    for _ in range(MAX_RESUMES):
        first.continuity.count_resume("s4")
    provider = Scripted()
    second, counts = _restart(tmp_path, provider)
    assert provider.requests == []
    assert counts["given_up"] == 1
    notices = [m for m in second.session_store.load("s4").messages if m.get("kind") == "restart"]
    assert notices and notices[-1].get("given_up") is True
    assert "s4" not in second.continuity.held_turns()


def test_a_command_that_had_started_is_not_run_again_in_full_access(tmp_path):
    marker = tmp_path / "work" / "ran.txt"
    first, engine = _leave(tmp_path, "s5", [
        {"role": "user", "content": "make the marker"},
        _call("c9", "run_shell", {"command": f"echo ran > \"{marker}\""}),
    ], mode=Mode.DANGER_FULL_ACCESS)
    # The intent note the dead process wrote before it ran the command.
    IntentStore.under(engine.roots).record("c9", "run_shell", {"command": "echo ran"})
    provider = Scripted([AssistantTurn(text="I checked: it had not run.", finish_reason="stop")])
    second, counts = _restart(tmp_path, provider)

    assert counts["picked_up"] == 1
    assert not marker.exists()
    results = [m for m in second.session_store.load("s5").messages if m.get("role") == "tool" and m.get("tool_call_id") == "c9"]
    assert results and "not re-run" in str(results[0]["content"])


def test_a_background_child_is_picked_up_and_reports_to_its_parent(tmp_path):
    _leave(tmp_path, "parent", [{"role": "user", "content": "review it"}, {"role": "assistant", "content": "Started a reviewer."}], claim=False)
    first, _ = _leave(tmp_path, "child", [{"role": "user", "content": "Review the export tests"}], claim=False)
    first.continuity.claim_turn("child", parent="parent")
    provider = Scripted([AssistantTurn(text="Two tests are missing.", finish_reason="stop")])
    second = _manager(tmp_path, provider)
    delivered = []

    async def inject(**kwargs):
        delivered.append(kwargs)

    second._inject_background_result = inject
    asyncio.run(second.resume_interrupted_work())
    assert delivered and delivered[0]["parent_id"] == "parent" and delivered[0]["child_id"] == "child"
    assert delivered[0]["result"]["report"] == "Two tests are missing."


def test_a_foreground_child_is_left_to_its_parent(tmp_path):
    _leave(tmp_path, "fg", [{"role": "user", "content": "Explore the repo"}])
    provider = Scripted()
    second = _manager(tmp_path, provider)
    real_load = second.session_store.load

    def as_child(sid):
        record = real_load(sid)
        if record is not None and sid == "fg":
            record.kind = "subagent"
        return record

    second.session_store.load = as_child
    counts = asyncio.run(second.resume_interrupted_work())
    assert provider.requests == [] and counts["finished"] == 1
    assert "fg" not in second.continuity.held_turns()


# -- background commands ---------------------------------------------------------------------


def test_an_idle_session_is_shown_its_stopped_command_and_nothing_runs(tmp_path):
    first, _ = _leave(tmp_path, "s6", [
        {"role": "user", "content": "start the dev server"},
        {"role": "assistant", "content": "It is running in the background."},
    ], claim=False)
    first.continuity.set_jobs("s6", [{"id": "bg-1", "command": "npm run dev", "status": "running", "started_at": 1}])
    first.continuity.keep_tails({"s6": {"bg-1": "ready on :5173"}})
    provider = Scripted()
    second, counts = _restart(tmp_path, provider)

    assert provider.requests == []
    assert counts["jobs_told"] == 1
    stopped = [m for m in second.session_store.load("s6").messages if m.get("kind") == "job_stopped"]
    assert stopped and stopped[0]["command"] == "npm run dev" and "5173" in stopped[0]["tail"]


def test_a_picked_up_turn_is_told_what_the_restart_stopped(tmp_path):
    first, _ = _leave(tmp_path, "s7", [
        {"role": "user", "content": "check the page"},
        _call("c1", "list_files", {"path": "."}),
        {"role": "tool", "tool_call_id": "c1", "content": "index.html"},
    ])
    first.continuity.set_jobs("s7", [{"id": "bg-2", "command": "npm run dev", "status": "running", "started_at": 1}])
    provider = Scripted([AssistantTurn(text="Restarting the server first.", finish_reason="stop")])
    second, _ = _restart(tmp_path, provider)

    told = next(t for t in reversed(_user_texts(provider.requests[0])) if t.startswith(CONTINUE_AFTER_RESTART))
    assert told.startswith(CONTINUE_AFTER_RESTART) and "`npm run dev` was stopped by the restart" in told
    assert not [m for m in second.session_store.load("s7").messages if m.get("kind") == "job_stopped"]
    source = [m["source"] for m in second.session_store.load("s7").messages if (m.get("source") or {}).get("synthetic") == "restart"]
    assert source and source[0]["jobs"] == ["npm run dev"]


def test_the_roster_keeps_the_store_in_step(tmp_path):
    manager = _manager(tmp_path, Scripted())
    rows = [{"id": "bg-1", "command": "pytest -q", "status": "running", "started_at": 3},
            {"id": "bg-0", "command": "ls", "status": "finished", "started_at": 1}]
    manager.background_jobs = lambda sid: {"jobs": rows}
    manager._jobs_changed = lambda sid, printing=False: None
    manager._roster_changed("s8")
    on_disk = lambda: json.loads(manager.continuity.path.read_text(encoding="utf-8")).get("jobs", {})  # noqa: E731
    assert list(on_disk()["s8"]) == ["bg-1"]
    # This process is running, so its jobs are its own and no starting process takes them.
    assert manager.continuity.take_jobs() == {}
    rows[0]["status"] = "finished"
    manager._roster_changed("s8")
    assert on_disk() == {}


# -- a neighbour's work is not a leftover (2026-10-15) ---------------------------------------


def test_a_turn_a_running_process_holds_is_not_picked_up(tmp_path):
    """The desktop app and a dev server share the state folder, and an agent working on Reeve
    started a second server beside the one it ran in. The second picked up the first's turn and two
    engines wrote one session's log (242 `invariant/violation` rows)."""
    first, _ = _leave(tmp_path, "live", [{"role": "user", "content": "keep going"}], claim=False)
    first.continuity.owner = process_owner.current()  # this process: alive
    first.continuity.claim_turn("live")
    _leave(tmp_path, "left", [{"role": "user", "content": "finish it"}])
    provider = Scripted([AssistantTurn(text="Done.", finish_reason="stop")])
    second, counts = _restart(tmp_path, provider)

    assert counts["picked_up"] == 1
    asked = [text for request in provider.requests for text in _user_texts(request)]
    assert "finish it" in asked
    assert "keep going" not in asked  # the live process's turn was never run here
    assert "live" in second.continuity.held_turns()  # still the live process's


def test_a_live_process_keeps_its_jobs_and_a_dead_ones_are_taken(tmp_path):
    path = tmp_path / "continuity.json"
    ContinuityStore(path).set_jobs("mine", [{"id": "bg-1", "command": "npm run dev", "started_at": 1}])
    ContinuityStore(path, owner=GONE).set_jobs("left", [{"id": "bg-2", "command": "pytest", "started_at": 2}])
    assert ContinuityStore(path).take_jobs() == {"left": {"bg-2": {"command": "pytest", "started": 2}}}
    assert "mine" in json.loads(path.read_text(encoding="utf-8"))["jobs"]


def test_two_processes_writing_the_store_keep_each_others_claims(tmp_path):
    """Each kept a copy of the whole file and wrote it back, so one erased what the other had just
    claimed. Every write now starts from the file."""
    path = tmp_path / "continuity.json"
    app, server = ContinuityStore(path, owner=GONE), ContinuityStore(path)
    app.claim_turn("a")
    server.claim_turn("b")
    app.release_turn("x")  # an unrelated write from the first store's side
    assert set(ContinuityStore(path).held_turns()) == {"a", "b"}
    server.release_turn("b")
    assert set(ContinuityStore(path).held_turns()) == {"a"}


def test_an_owner_is_the_pid_and_when_it_started():
    me = process_owner.current()
    assert process_owner.running(me)
    assert not process_owner.running(GONE)
    assert not process_owner.running(None)  # a claim from before owners were recorded
    if "started" in me:
        # The same pid with another start time is a reused pid: another process.
        assert not process_owner.running({**me, "started": me["started"] + 1})
