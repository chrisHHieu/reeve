"""`reeve exec`: the product's engine, run for one prompt with no interface.

What has to hold for a script, a CI job or an outside benchmark to rely on it:

  - the answer is on stdout and nothing else is, so `reeve exec "…" > answer.md` is the answer;
  - `--json` is one event per line and ends with a `result` line that carries the exit code;
  - the exit status says how the turn ended in the turn's own vocabulary: done, failed, stopped by a
    rail, or "said done but nothing proved it" (a CI job must not read that as green);
  - nobody answers: a call that needs approval is denied unless its tool was named, and the model is
    told, so a headless run never hangs on an Inbox no one watches;
  - the session is saved like any other, so `--resume` continues it.
"""

from __future__ import annotations

import io
import json

import pytest

from harness.interface import exec_cli
from harness.llm.providers.base import AssistantTurn, ModelCapabilities, ProviderClient, ToolCall


def _text(text):
    return AssistantTurn(text=text, finish_reason="stop")


def _call(name, args, call_id="c1"):
    return AssistantTurn(tool_calls=[ToolCall(id=call_id, name=name, arguments=args)], finish_reason="tool_calls")


class Scripted(ProviderClient):
    def __init__(self, turns):
        self.turns, self.seen = list(turns), []

    def complete(self, *, model, messages, tools=None, **settings):
        self.seen.append(messages)
        return self.turns.pop(0)

    def capabilities(self, model):
        return ModelCapabilities()


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("REEVE_STATE_DIR", str(tmp_path / "state"))
    work = tmp_path / "work"
    work.mkdir()
    return tmp_path, work


def _run(env, argv, turns, stdin=None):
    tmp, work = env
    out, err = io.StringIO(), io.StringIO()
    provider = Scripted(turns)
    code = exec_cli.main(
        ["--cwd", str(work), "--model", "gpt-5.5", "--no-mcp", *argv],
        stdin=stdin or io.StringIO(""), provider=provider, out=out, err=err, state_dir=tmp / "state",
    )
    return code, out.getvalue(), err.getvalue(), provider


def test_the_answer_is_the_only_thing_on_stdout(env):
    code, out, err, _ = _run(env, ["say hi"], [_text("Hello from Reeve.")])
    assert code == exec_cli.EXIT_OK
    assert out == "Hello from Reeve.\n"
    assert "session " in err  # progress and the session id go to stderr


def test_json_is_one_event_per_line_and_ends_with_the_result(env):
    code, out, err, _ = _run(env, ["--json", "say hi"], [_text("Hello.")])
    lines = [json.loads(line) for line in out.splitlines()]
    kinds = [line["type"] for line in lines]
    assert "turn_start" in kinds and "turn_end" in kinds and kinds[-1] == "result"
    assert not [k for k in kinds if k.endswith("_delta")]  # display-only events are left out
    result = lines[-1]
    assert result["exit_code"] == 0 and result["status"] == "completed" and result["text"] == "Hello."
    assert err == ""  # a machine reader gets nothing else


def test_a_tool_call_runs_inside_the_workspace_and_is_reported_on_stderr(env):
    tmp, work = env
    code, out, err, _ = _run(
        env, ["--allow-tool", "write_file", "write a file"],
        [_call("write_file", {"path": "note.txt", "content": "hi"}), _text("Wrote it.")],
    )
    assert code == 0 and (work / "note.txt").read_text() == "hi"
    assert "• write_file note.txt" in err


def test_full_access_runs_what_the_floors_allow_without_naming_tools(env):
    tmp, work = env
    code, out, err, _ = _run(
        env, ["--mode", "danger-full-access", "write a file"],
        [_call("write_file", {"path": "auto.txt", "content": "yes"}), _text("Done.")],
    )
    assert code == 0 and (work / "auto.txt").read_text() == "yes"
    assert "denied" not in err


def test_a_call_that_needs_approval_is_denied_and_the_model_is_told(env):
    code, out, err, provider = _run(
        env, ["--mode", "read-only", "write a file"],
        [_call("write_file", {"path": "x.txt", "content": "no"}), _text("Could not.")],
    )
    tool_message = [m for m in provider.seen[-1] if m.get("role") == "tool"][0]
    assert not (env[1] / "x.txt").exists()
    assert tool_message["content"]  # the refusal reached the model
    assert "denied" in err  # read-only refuses outright: no approval was even asked for


def test_the_default_mode_also_denies_a_write_nobody_approved(env):
    code, out, err, _ = _run(
        env, ["write a file"],
        [_call("write_file", {"path": "y.txt", "content": "no"}), _text("Could not.")],
    )
    assert code == 0 and not (env[1] / "y.txt").exists()
    assert "--allow-tool write_file" in err  # the person reading the log is told how to allow it


def test_the_task_can_come_from_stdin(env):
    code, out, _err, provider = _run(env, ["-"], [_text("ok")], stdin=io.StringIO("do the thing\n"))
    assert code == 0
    first_user = [m for m in provider.seen[0] if m.get("role") == "user"][-1]["content"]
    assert "do the thing" in str(first_user)


def test_no_task_is_a_usage_error_not_a_failed_run(env):
    code, out, err, _ = _run(env, [], [])
    assert code == exec_cli.EXIT_USAGE and "no task given" in err and out == ""


def test_a_bad_option_is_a_usage_error(env):
    with pytest.raises(SystemExit) as stop:
        exec_cli.build_parser().parse_args(["--mode", "yolo", "x"])
    assert stop.value.code == exec_cli.EXIT_USAGE  # not 2: that is "stopped by a rail"


def test_exit_codes_follow_how_the_turn_ended():
    ok = {"status": "completed", "iterations": 2}
    assert exec_cli.exit_code(ok, failed=False) == exec_cli.EXIT_OK
    assert exec_cli.exit_code({**ok, "unverified": True}, failed=False) == exec_cli.EXIT_UNPROVEN
    assert exec_cli.exit_code({**ok, "check_failed": True}, failed=False) == exec_cli.EXIT_UNPROVEN
    assert exec_cli.exit_code({**ok, "truncated": True}, failed=False) == exec_cli.EXIT_UNPROVEN
    assert exec_cli.exit_code({"status": "max_iterations_exceeded"}, failed=False) == exec_cli.EXIT_STOPPED
    assert exec_cli.exit_code({"status": "repetition_stopped"}, failed=False) == exec_cli.EXIT_STOPPED
    assert exec_cli.exit_code(None, failed=False) == exec_cli.EXIT_FAILED
    assert exec_cli.exit_code(ok, failed=True) == exec_cli.EXIT_FAILED


def test_a_step_limit_ends_the_run_with_the_stopped_code(env):
    # the model keeps calling a tool; --max-steps ends the turn, and the code says a rail did it
    calls = [_call("list_files", {}, f"c{i}") for i in range(10)]
    code, out, err, _ = _run(env, ["--max-steps", "2", "--json", "loop"], calls + [_text("wrapped up")])
    result = json.loads(out.splitlines()[-1])
    assert code == exec_cli.EXIT_STOPPED and result["status"] != "completed"


def test_a_model_failure_is_the_failed_code_and_still_saves_the_session(env):
    class Down(Scripted):
        def complete(self, *, model, messages, tools=None, **settings):
            raise RuntimeError("provider is down")

    tmp, work = env
    out, err = io.StringIO(), io.StringIO()
    code = exec_cli.main(
        ["--cwd", str(work), "--model", "gpt-5.5", "--no-mcp", "--json", "hello"],
        stdin=io.StringIO(""), provider=Down([]), out=out, err=err, state_dir=tmp / "state",
    )
    assert code == exec_cli.EXIT_FAILED
    result = json.loads(out.getvalue().splitlines()[-1])
    assert result["type"] == "result" and result["exit_code"] == 1


def test_resume_continues_the_saved_session(env):
    tmp, work = env
    code, out, err, _ = _run(env, ["--json", "remember: the code is 7"], [_text("Noted.")])
    session = json.loads(out.splitlines()[-1])["session_id"]
    code2, out2, _e, provider = _run(env, ["--resume", session, "what was the code?"], [_text("It was 7.")])
    assert code2 == 0 and out2 == "It was 7.\n"
    history = " ".join(str(m.get("content")) for m in provider.seen[0])
    assert "the code is 7" in history  # the first turn is in the second request


def test_the_last_message_can_be_written_to_a_file(env):
    tmp, _w = env
    target = tmp / "last.txt"
    _run(env, ["--output-last-message", str(target), "x"], [_text("final words")])
    assert target.read_text(encoding="utf-8") == "final words"


def test_the_answer_survives_a_console_that_cannot_print_it(env):
    """A Windows console is cp1252. The first real run printed the answer after the work was done and
    saved, and died on `đạt` (UnicodeEncodeError, exit 1): a task that succeeded reported as a failure."""
    tmp, work = env
    raw = io.BytesIO()
    out = io.TextIOWrapper(raw, encoding="cp1252", write_through=True, newline="\n")
    err = io.StringIO()
    code = exec_cli.main(
        ["--cwd", str(work), "--model", "gpt-5.5", "--no-mcp", "hello"],
        stdin=io.StringIO(""), provider=Scripted([_text("Báo cáo đạt: Nguyễn Văn Ánh")]), out=out, err=err,
        state_dir=tmp / "state",
    )
    assert code == 0
    assert raw.getvalue().decode("utf-8") == "Báo cáo đạt: Nguyễn Văn Ánh\n"
