"""The fixes from the first session-log audit (session a5a5ce3d): the run_code child's
stderr is kept as a backstop, a PowerShell parse failure is not reported as success, the
run_code schema names its environment, and every turn's final step closes.
"""

from __future__ import annotations

import sys

from harness.tools.code_mode import CodeProgram as CodeRun
from harness.tools.shell import LocalExecutor, _parse_error_hint


# -- Fix 1: the run_code child's stderr survives a native death ---------------------


def test_a_child_that_dies_without_a_frame_reports_its_stderr():
    """A program that hard-exits (os._exit skips every frame): the no-frame error now
    carries the process's real stderr — the only evidence a native death leaves, since
    the program's own print goes to the captured buffer and dies with it."""
    code = (
        "import os, sys\n"
        "sys.stderr.write('the child died here, hard')\n"
        "os._exit(9)\n"
    )
    run = CodeRun(code, names=frozenset(), timeout=30)
    run.start()
    # Drive the same broker the engine's executor drives, without a model: no request
    # ever arrives, the child dies, the run ends.
    while not run.finished:
        if run.next_request(timeout=0.2) is None:
            continue
        break
    result = run.result()

    assert "error" in result
    assert "the program ended without returning a result" in result["error"]
    assert "the child died here, hard" in result["error"], "stderr backstop discarded"
    assert "[child stderr tail]" in result["error"]


def test_a_healthy_run_gains_no_stderr_noise():
    run = CodeRun("print('fine')\nreturn 7", names=frozenset(), timeout=30)
    run.start()
    while not run.finished:
        if run.next_request(timeout=0.2) is None:
            continue
        break
    result = run.result()
    assert result.get("returned") == 7
    assert "error" not in result
    assert "child stderr" not in result["output"]


# -- Fix 2: a PowerShell parse failure is a failure ---------------------------------


def test_the_parse_error_hint_names_the_separator_fix():
    transcript = (
        "The token '&&' is not a valid statement separator in this version.\n"
        "    + CategoryInfo: ParserError\n"
    )
    hint = _parse_error_hint(transcript)
    assert hint and "`;`" in hint and "cd path ; python script.py" in hint


def test_a_generic_parser_error_hints_syntax_without_inventing_the_fix():
    transcript = "+ CategoryInfo : ParserError: (:) [], missing closing brace\n"
    hint = _parse_error_hint(transcript)
    assert hint and "parse" in hint
    assert _parse_error_hint("Chart 1 done\n") is None


def _run_once(executor: LocalExecutor, command: str) -> dict:
    result = executor.run(command, timeout=30)
    executor.close()
    return result


def test_local_executor_reports_a_parse_error_as_an_error(tmp_path):
    """The exact command from session a5a5ce3d: `&&` on Windows PowerShell 5.1 used to
    come back exit_code 0, status ok, with the ParserError sitting in the output."""
    if sys.platform != "win32":
        return  # the signature check is what non-Windows runs pin; this pins the shell
    executor = LocalExecutor(cwd=tmp_path)
    result = _run_once(executor, f'cd "{tmp_path}" && echo hello')
    assert result.get("error"), "a parse failure was reported as success"
    assert "`;`" in result["error"]
    # exit_code stays whatever the trailer said — the verdict is carried by `error`.


# -- Fix 4: the final step of a turn closes -----------------------------------------


def test_step_end_exists_for_every_step_start(tmp_path):
    import asyncio
    import json

    from harness.loop import TurnEngine
    from harness.session.invariants import _empty_index  # noqa: F401  (import sanity)

    from harness.safety.permissions import PermissionEngine
    from harness.llm.providers import AssistantTurn, ModelCapabilities, ProviderClient, StreamChunk
    from harness.session.session_log import SessionLog
    from harness.tools import ToolRegistry

    class Scripted(ProviderClient):
        def stream(self, *, model, messages, tools=None, **settings):
            yield StreamChunk(turn=AssistantTurn(text="the answer", finish_reason="stop"))

        def complete(self, *, model, messages, tools=None, **settings):
            raise AssertionError

        def capabilities(self, model):
            return ModelCapabilities()

    engine = TurnEngine(
        provider=Scripted(),
        registry=ToolRegistry(),
        permissions=PermissionEngine(workspace_root=tmp_path),
        model="m",
    )
    engine.log = SessionLog(tmp_path / "events.jsonl")

    async def go():
        async for _ in engine.run("hello"):
            pass

    asyncio.run(go())
    events = engine.log.read()
    starts = sum(1 for e in events if e["type"] == "step/start")
    ends = sum(1 for e in events if e["type"] == "step/end")
    assert starts == ends == 1, f"step boundaries dangle: {starts} starts, {ends} ends"
    order = [e["type"] for e in events]
    assert order.index("step/end") < order.index("turn/end")
    json.dumps(events)  # the log stays machine-readable
