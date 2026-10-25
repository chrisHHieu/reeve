"""A background job wakes its session the first time it prints the line the model is
waiting for, and its end says when that line never came."""

from __future__ import annotations

import asyncio
import sys
import threading
import time

from harness.session.sessions import SessionRecord
from harness.tools.shell import LocalExecutor, shell_tools
from harness.tools.shell_watch import LineWatch


class _Task:
    id, command, generation = "bg-9", "serve", 0

    def tail(self, chars):
        return "booting\nready on 3000\n"


def test_a_match_seen_before_anyone_listens_is_delivered_once_on_attach():
    watch = LineWatch(r"ready on \d+")
    watch.feed("booting\n", _Task())
    watch.feed("ready on 3000\n", _Task())  # the reader is faster than the watcher
    watch.feed("ready on 3001\n", _Task())  # once only
    heard: list = []
    watch.attach(heard.append)
    assert [(e["event"], e["line"], e["line_number"]) for e in heard] == [("matched", "ready on 3000", 2)]
    assert LineWatch.compile_error("(") and LineWatch.compile_error("ready") is None


def _job(tmp_path, body: str):
    script = tmp_path / "job.py"
    script.write_text(body, encoding="utf-8")
    return f'"{sys.executable}" "{script}"'


def _run(tmp_path, body: str, pattern: str) -> list[dict]:
    ex = LocalExecutor(cwd=tmp_path, default_timeout=30)
    heard: list[dict] = []
    done = threading.Event()

    def listener(info):
        heard.append(info)
        if info.get("event") != "matched":
            done.set()

    ex.set_background_listener(listener)
    try:
        run_shell = {t.__name__: t for t in shell_tools(ex)}["run_shell"]
        started = run_shell(
            command=_job(tmp_path, body),
            run_in_background=True,
            notify_when=pattern,
            shell="cmd" if sys.platform == "win32" else None,
        )
        assert "first time it prints a line matching" in started["note"]
        assert done.wait(30), "the job never reported its end"
        time.sleep(0.05)
    finally:
        ex.close()
    return heard


def test_the_line_arrives_while_the_job_runs_and_its_end_after(tmp_path):
    heard = _run(
        tmp_path,
        "import time\nprint('booting', flush=True)\nprint('Listening on :3000', flush=True)\n"
        "time.sleep(1.0)\nprint('bye', flush=True)\n",
        "listening on|ready",
    )
    matched = [e for e in heard if e.get("event") == "matched"]
    # Case matters as written: 'listening' does not match 'Listening'.
    assert matched == []
    assert heard[-1]["matched"] is False and heard[-1]["pattern"] == "listening on|ready"

    heard = _run(
        tmp_path,
        "import time\nprint('booting', flush=True)\nprint('Listening on :3000', flush=True)\n"
        "time.sleep(1.0)\nprint('bye', flush=True)\n",
        "(?i)listening on",
    )
    events = [e.get("event", "finished") for e in heard]
    assert events == ["matched", "finished"]
    assert heard[0]["line"] == "Listening on :3000" and "booting" in heard[0]["output"]
    assert heard[1]["matched"] is True and heard[1]["exit_code"] == 0


def test_an_invalid_pattern_is_refused_before_anything_runs(tmp_path):
    ex = LocalExecutor(cwd=tmp_path, default_timeout=30)
    try:
        run_shell = {t.__name__: t for t in shell_tools(ex)}["run_shell"]
        out = run_shell(command="echo hi", run_in_background=True, notify_when="(")
        assert "not a valid regular expression" in out["error"] and not ex.background_jobs()
    finally:
        ex.close()


def test_the_session_hears_the_line_and_an_end_without_it(tmp_path, monkeypatch):
    from harness.llm.providers import AssistantTurn, ModelCapabilities, ProviderClient
    from harness.interface.server.manager import SessionManager

    class _Provider(ProviderClient):
        def complete(self, *, model, messages, tools=None, **settings):
            return AssistantTurn(text="ok", finish_reason="stop")

        def capabilities(self, model):
            return ModelCapabilities()

    monkeypatch.setenv("REEVE_STATE_DIR", str(tmp_path / "state"))
    mgr = SessionManager(workspace=tmp_path, provider=_Provider())
    mgr.session_store.save(
        SessionRecord(
            session_id="s-ready", workspace=str(tmp_path), model="m", mode="interactive",
            messages=[{"role": "user", "content": "start the server"}],
        )
    )
    asyncio.run(
        mgr._deliver_shell_result(
            "s-ready",
            {"event": "matched", "task_id": "bg-1", "command": "npm run dev", "pattern": "ready",
             "line": "ready in 300ms", "line_number": 4, "output": "vite\nready in 300ms"},
        )
    )
    asyncio.run(
        mgr._deliver_shell_result(
            "s-ready",
            {"task_id": "bg-2", "command": "npm run dev", "exit_code": 1, "output": "EADDRINUSE",
             "pattern": "ready", "matched": False},
        )
    )
    blob = "\n".join(str(m.get("content")) for m in mgr.session_store.load("s-ready").messages)
    assert "bg-1 printed a line matching 'ready' (output line 4) — it is still running" in blob
    assert "ready in 300ms" in blob
    assert "bg-2 finished — exit 1" in blob
    assert "ended without ever printing a line matching 'ready'" in blob
