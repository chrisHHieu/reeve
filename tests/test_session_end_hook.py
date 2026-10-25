"""`session_end` (Claude Code's `SessionEnd`): the hook a session's lifetime closes with. It runs
once, only for an engine whose `session_start` ran, when the session is deleted or the server shuts
down; it steers nothing, and neither a failing nor a hanging hook may cost the deletion or the quit.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time

from harness.extensions.plugins.executable import hooks_from_claude
from harness.interface.server.manager.session_end import end_all, end_before_delete
from harness.loop.hook_calls import HookCallsMixin
from harness.loop.hooks import EVENTS, HookSpec
from harness.loop.hooks_runner import HookRunner


class _Log:
    def __init__(self):
        self.rows = []

    def append(self, kind, **fields):
        self.rows.append((kind, fields))


class _Engine(HookCallsMixin):
    def __init__(self, hooks, started=True):
        self.hooks = hooks
        self.log = _Log()
        self._session_started = started


def _writer(path) -> HookSpec:
    body = f"import sys; open({str(path)!r}, 'a', encoding='utf-8').write(sys.stdin.read() + '\\n')"
    return HookSpec(event="session_end", name="note", command=(sys.executable, "-c", body))


def test_it_runs_once_for_an_engine_that_started_and_says_why(tmp_path):
    out = tmp_path / "ended.jsonl"
    engine = _Engine(HookRunner([_writer(out)]))
    asyncio.run(engine.end_session("deleted"))
    asyncio.run(engine.end_session("shutdown"))
    lines = out.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["reason"] == "deleted"


def test_an_engine_that_never_started_announces_no_end(tmp_path):
    out = tmp_path / "ended.jsonl"
    asyncio.run(_Engine(HookRunner([_writer(out)]), started=False).end_session("deleted"))
    assert not out.exists()


def test_a_failing_hook_costs_nothing(tmp_path):
    spec = HookSpec(event="session_end", name="boom", command=(sys.executable, "-c", "raise SystemExit(3)"))
    asyncio.run(_Engine(HookRunner([spec])).end_session("deleted"))


def test_a_claude_code_session_end_hook_is_kept():
    skipped: list[str] = []
    hooks = hooks_from_claude(
        {"hooks": {"SessionEnd": [{"hooks": [{"command": "report", "args": []}]}]}}, plugin="p", skipped=skipped
    )
    assert [h["event"] for h in hooks] == ["session_end"] and skipped == []
    assert "session_end" in EVENTS


class _Recorder:
    def __init__(self, delay=0.0):
        self.reasons, self.delay = [], delay

    async def end_session(self, reason):
        await asyncio.sleep(self.delay)
        self.reasons.append(reason)


def test_a_deletion_waits_for_the_end_hooks_from_a_thread_and_schedules_them_on_the_loop():
    engine = _Recorder()
    end_before_delete(engine, None)  # the route's worker thread with no server loop
    assert engine.reasons == ["deleted"]

    async def on_the_loop():
        scheduled = _Recorder()
        end_before_delete(scheduled, asyncio.get_running_loop())  # must not block the loop
        await asyncio.sleep(0.05)
        return scheduled.reasons

    assert asyncio.run(on_the_loop()) == ["deleted"]


def test_a_shutdown_ends_every_session_and_does_not_wait_past_its_bound():
    quick, slow = _Recorder(), _Recorder(delay=5.0)
    started = time.monotonic()
    asyncio.run(end_all([quick, slow], timeout=0.2))
    assert time.monotonic() - started < 2.0
    assert quick.reasons == ["shutdown"] and slow.reasons == []
