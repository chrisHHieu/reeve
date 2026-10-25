"""Lifecycle hooks — the contract, the trust model, and the engine's wiring.

The three properties that matter, each with its own reason:
- a hook may DENY or add CONTEXT and never grant (an `allow` still passes through the
  normal guard stage and presets);
- a hook cannot break a turn (timeout, crash, bad JSON, missing binary: all logged, none
  fatal);
- a workspace hook needs trust by DECLARATION DIGEST, so `git pull` cannot change what
  runs and a fresh clone runs nothing.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import aisuite as ai

from harness.loop import TurnEngine
from harness.session.events import EventType
from harness.loop.hooks import HookSpec, HookTrustStore, declaration_digest, load_hooks
from harness.loop.hooks_runner import HookRunner
from harness.safety.permissions import PermissionEngine
from harness.llm.providers import AssistantTurn, ModelCapabilities, ProviderClient
from harness.llm.providers.base import ToolCall
from harness.session.session_log import SessionLog
from harness.tools import ToolRegistry


def _script(body: str) -> tuple[str, ...]:
    """A hook command that runs a Python one-liner — argv, not a shell string, so the
    same hook works on Windows and POSIX."""
    return (sys.executable, "-c", body)


def _emit(payload: dict) -> str:
    return f"import json,sys; print(json.dumps({payload!r}))"


# -- the runner --------------------------------------------------------------------


def test_a_hook_denies_with_its_reason():
    spec = HookSpec(
        event="pre_tool_use",
        name="no-rm",
        command=_script(_emit({"decision": "deny", "reason": "use make clean"})),
    )
    outcome = asyncio.run(HookRunner([spec]).run("pre_tool_use", {}, tool="run_shell"))
    assert outcome.denied and outcome.reason == "use make clean"


def test_plain_text_output_is_context_not_an_error():
    """`echo "remember the branch"` is the shortest useful hook; requiring JSON would
    teach people to wrap everything in a serializer."""
    spec = HookSpec(event="session_start", name="note", command=_script("print('on main')"))
    outcome = asyncio.run(HookRunner([spec]).run("session_start", {}))
    assert not outcome.denied and outcome.context == "on main" and not outcome.errors


def test_a_broken_hook_never_raises(tmp_path):
    cases = [
        HookSpec(event="stop", name="crash", command=_script("raise SystemExit(3)")),
        HookSpec(event="stop", name="garbage", command=_script("print('{oops')")),
        HookSpec(event="stop", name="missing", command=("definitely-not-a-binary-xyz",)),
    ]
    for spec in cases:
        outcome = asyncio.run(HookRunner([spec]).run("stop", {}))
        assert not outcome.denied and outcome.errors, spec.name
        # A broken hook never contributes context it did not produce.
        assert outcome.context == ""


def test_a_hanging_hook_is_killed_by_its_timeout():
    spec = HookSpec(
        event="stop",
        name="slow",
        command=_script("import time; time.sleep(30)"),
        timeout_seconds=0.4,
    )
    outcome = asyncio.run(HookRunner([spec]).run("stop", {}))
    assert outcome.errors and "timed out" in outcome.errors[0]


def test_the_matcher_selects_tools_and_a_broken_pattern_matches_nothing():
    deny = _script(_emit({"decision": "deny", "reason": "no shell"}))
    spec = HookSpec(event="pre_tool_use", name="shells", command=deny, matcher=r"^run_shell$")
    runner = HookRunner([spec])
    assert asyncio.run(runner.run("pre_tool_use", {}, tool="run_shell")).denied
    assert not asyncio.run(runner.run("pre_tool_use", {}, tool="read_file")).denied

    broken = HookSpec(event="pre_tool_use", name="bad", command=deny, matcher="(")
    assert not asyncio.run(HookRunner([broken]).run("pre_tool_use", {}, tool="run_shell")).denied


def test_the_payload_reaches_the_hook_on_stdin():
    spec = HookSpec(
        event="pre_tool_use",
        name="echo",
        command=_script(
            "import json,sys; d=json.load(sys.stdin); "
            "print(json.dumps({'context': d['tool'] + ':' + d['arguments']['command']}))"
        ),
    )
    outcome = asyncio.run(
        HookRunner([spec]).run(
            "pre_tool_use", {"tool": "run_shell", "arguments": {"command": "ls"}}, tool="run_shell"
        )
    )
    assert outcome.context == "run_shell:ls"


def test_hook_runs_are_logged_with_their_verdict(tmp_path):
    log = SessionLog(tmp_path / "events.jsonl")
    spec = HookSpec(
        event="stop", name="gate", command=_script(_emit({"decision": "deny", "reason": "tests"}))
    )
    asyncio.run(HookRunner([spec], log=log).run("stop", {}))
    row = log.read()[0]
    assert row["type"] == "hook/run"
    assert row["decision"] == "deny" and row["reason"] == "tests" and row["name"] == "gate"


# -- loading and trust -------------------------------------------------------------


def _write_hooks(path: Path, command: tuple[str, ...]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "[hooks.pre_tool_use]\n"
        f"name = 'guard'\ncommand = {json.dumps(list(command))}\n",
        encoding="utf-8",
    )


def test_workspace_hooks_need_trust_and_the_digest_tracks_the_declaration(tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    hooks_file = workspace / ".reeve" / "hooks.toml"
    _write_hooks(hooks_file, _script(_emit({"decision": "deny", "reason": "no"})))
    store = HookTrustStore(tmp_path / "trust.json")

    specs, note = load_hooks(workspace, trust=store)
    assert specs == [] and "NOT running" in note

    # Trust the declaration itself, then it runs.
    from harness.loop.hooks import _read_hook_tables  # the loader's own reader


    declared = _read_hook_tables(hooks_file, source="workspace", warnings=[])
    store.trust(workspace, declaration_digest(declared))
    specs, note = load_hooks(workspace, trust=store)
    assert len(specs) == 1 and note == ""
    assert store.is_trusted(workspace, declaration_digest(declared))

    # Changing the COMMAND invalidates trust; a comment does not.
    _write_hooks(hooks_file, _script(_emit({"decision": "deny", "reason": "changed"})))
    specs, note = load_hooks(workspace, trust=store)
    assert specs == [] and "NOT running" in note
    hooks_file.write_text(
        "# a comment is not a change\n" + hooks_file.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    assert load_hooks(workspace, trust=store)[0] == []


def test_global_hooks_are_trusted_by_construction(tmp_path):
    config = tmp_path / "config.toml"
    command = json.dumps(list(_script("print('hi')")))
    config.write_text(
        f"[hooks.session_start]\ncommand = {command}\n",
        encoding="utf-8",
    )
    specs, note = load_hooks(tmp_path, global_path=config, trust=HookTrustStore(tmp_path / "t.json"))
    assert [s.source for s in specs] == ["global"] and note == ""


def test_an_unknown_event_is_reported_not_silently_kept(tmp_path):
    config = tmp_path / "config.toml"
    config.write_text(
        "[hooks.before_everything]\ncommand = ['echo']\n", encoding="utf-8"
    )
    specs, note = load_hooks(tmp_path, global_path=config, trust=HookTrustStore(tmp_path / "t.json"))
    assert specs == [] and "unknown hook event" in note


# -- the engine's wiring -----------------------------------------------------------


class _OneCall(ProviderClient):
    """Streams one scripted tool call, then a plain answer."""

    def __init__(self, name="read_file", arguments=None) -> None:
        self.name = name
        self.arguments = arguments or {"path": "notes.txt"}
        self.calls = 0

    def stream(self, *, model, messages, tools=None, **settings):
        from harness.llm.providers import StreamChunk

        self.calls += 1
        if self.calls == 1:
            yield StreamChunk(
                turn=AssistantTurn(
                    text="",
                    finish_reason="tool_calls",
                    tool_calls=[ToolCall(id="c1", name=self.name, arguments=self.arguments)],
                )
            )
        else:
            yield StreamChunk(turn=AssistantTurn(text="done", finish_reason="stop"))

    def complete(self, *, model, messages, tools=None, **settings):
        raise AssertionError("the engine must stream")

    def capabilities(self, model):
        return ModelCapabilities()


def _engine(tmp_path, provider, hooks) -> TurnEngine:
    registry = ToolRegistry()
    registry.register_all(ai.toolkits.files(roots=[{"path": str(tmp_path), "writable": True}]))
    engine = TurnEngine(
        provider=provider,
        registry=registry,
        permissions=PermissionEngine(workspace_root=tmp_path),
        model="gpt-5.5",
        hooks=hooks,
    )
    engine.log = SessionLog(tmp_path / "events.jsonl", trace="t1")
    return engine


def _run(engine) -> list:
    async def go():
        return [ev async for ev in engine.run("read the notes")]

    return asyncio.run(go())


def test_a_pre_tool_use_deny_stops_the_call_before_any_card(tmp_path):
    (tmp_path / "notes.txt").write_text("secret\n", encoding="utf-8")
    hook = HookSpec(
        event="pre_tool_use",
        name="no-reads",
        command=_script(_emit({"decision": "deny", "reason": "reads are frozen"})),
    )
    engine = _engine(tmp_path, _OneCall(), HookRunner([hook], workspace=tmp_path))
    events = _run(engine)

    finished = [e for e in events if e.type is EventType.TOOL_FINISHED]
    assert finished and finished[0].data["status"] == "denied"
    assert "reads are frozen" in finished[0].data["reason"]
    assert not [e for e in events if e.type is EventType.PERMISSION_REQUIRED], "no card was raised"
    # The hook's own verdict is in the log.
    assert any(
        row["type"] == "hook/run" and row["decision"] == "deny" for row in engine.log.read()
    )


def test_a_hook_allow_is_not_a_permission(tmp_path):
    """A hook that says `allow` must not make a consequential call run: the normal path
    still asks. This is the monotonic rule applied to the newest extension."""
    hook = HookSpec(
        event="pre_tool_use",
        name="permissive",
        command=_script(_emit({"decision": "allow"})),
    )
    engine = _engine(
        tmp_path,
        _OneCall(name="write_file", arguments={"path": "x.txt", "content": "y"}),
        HookRunner([hook], workspace=tmp_path),
    )
    events = _run(engine)
    asked = [e for e in events if e.type is EventType.PERMISSION_REQUIRED]
    assert asked, "the write still needed approval despite the hook's allow"


def test_a_post_tool_use_context_enters_the_turn_and_the_log(tmp_path):
    (tmp_path / "notes.txt").write_text("hello\n", encoding="utf-8")
    hook = HookSpec(
        event="post_tool_use",
        name="formatter",
        command=_script("print('the file is formatted')"),
    )
    engine = _engine(tmp_path, _OneCall(), HookRunner([hook], workspace=tmp_path))
    _run(engine)

    contents = [m.get("content") for m in engine.messages if m.get("role") == "user"]
    assert any("the file is formatted" in str(c) for c in contents)
    sources = [
        row.get("source") for row in engine.log.read() if row["type"] == "steering/message"
    ]
    assert "hook:post_tool_use" in sources


def test_a_stop_hook_objects_once_then_lets_the_turn_finish(tmp_path):
    """The canonical stop hook: refuse the completion attempt. The guard is the
    verification gate's shape — consulted ONCE per turn, so a hook that keeps objecting
    cannot loop the turn; the model gets the reason and decides what to do about it."""
    script = (
        "import json,os,pathlib;"
        "p=pathlib.Path(os.environ['REEVE_HOOK_WORKSPACE'])/'stop-count';"
        "n=int(p.read_text()) if p.exists() else 0;"
        "p.write_text(str(n+1));"
        "print(json.dumps({'decision':'deny','reason':'run the tests'}))"
    )
    hook = HookSpec(event="stop", name="tests-first", command=_script(script))
    engine = _engine(tmp_path, _OneCall(), HookRunner([hook], workspace=tmp_path))
    events = _run(engine)

    assert (tmp_path / "stop-count").read_text(encoding="utf-8") == "1"
    end = [e for e in events if e.type is EventType.TURN_END][-1]
    assert end.data["status"] == "completed"
    nudges = [
        row
        for row in engine.log.read()
        if row["type"] == "steering/message" and row.get("source") == "hook:stop"
    ]
    assert nudges and "run the tests" in nudges[0]["text"]


def test_an_untrusted_workspace_hook_is_skipped_and_explained(tmp_path, monkeypatch):
    monkeypatch.setenv("REEVE_STATE_DIR", str(tmp_path / "state"))
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "notes.txt").write_text("hello\n", encoding="utf-8")
    _write_hooks(
        workspace / ".reeve" / "hooks.toml",
        _script(_emit({"decision": "deny", "reason": "never runs"})),
    )
    from harness.compose.session_helpers import build_hooks as _build_hooks

    runner = _build_hooks(workspace)
    assert runner is not None and runner.specs == [] and "NOT running" in runner.note

    registry = ToolRegistry()
    registry.register_all(ai.toolkits.files(roots=[{"path": str(workspace), "writable": True}]))
    engine = TurnEngine(
        provider=_OneCall(),
        registry=registry,
        permissions=PermissionEngine(workspace_root=workspace),
        model="gpt-5.5",
        hooks=runner,
    )
    engine.log = SessionLog(workspace / "events.jsonl")
    events = _run(engine)
    finished = [e for e in events if e.type is EventType.TOOL_FINISHED]
    assert finished and finished[0].data["status"] == "ok", "the hook did not deny anything"
    notices = [m for m in engine.messages if m.get("role") == "notice"]
    assert notices and "NOT running" in str(notices[0].get("text"))


def test_a_remote_session_gets_no_hooks(tmp_path):
    from harness.compose.session_helpers import build_hooks as _build_hooks

    assert _build_hooks(tmp_path, remote=True) is None
