"""The deterministic verification ledger: facts from a transcript, no model."""

from __future__ import annotations

import json

from harness.loop.verification import Verification, as_row, is_verification_command, last_verification


def shell_call(call_id: str, command: str) -> dict:
    return {
        "role": "assistant",
        "tool_calls": [
            {"id": call_id, "function": {"name": "run_shell", "arguments": json.dumps({"command": command})}}
        ],
    }


def edit_call(call_id: str) -> dict:
    return {
        "role": "assistant",
        "tool_calls": [
            {"id": call_id, "function": {"name": "apply_patch", "arguments": json.dumps({"patch": "..."})}}
        ],
    }


def tool_result(call_id: str, content: str) -> dict:
    return {"role": "tool", "tool_call_id": call_id, "content": content}


def test_only_check_shaped_commands_count():
    assert is_verification_command("pytest -q tests/")
    assert is_verification_command("python -m ruff check .")
    assert is_verification_command("npm run typecheck")
    assert is_verification_command("cargo test --quiet")
    assert not is_verification_command("ls -la")
    assert not is_verification_command("git status")
    assert not is_verification_command("npm install")


def test_last_verification_reads_the_status_line_and_counts_edits_since():
    messages = [
        {"role": "user", "content": "fix the retry test"},
        shell_call("c1", "pytest -q"),
        tool_result("c1", "1 failed, 13 passed in 4.2s\n[exit 1; cwd D:\\ws]"),
        edit_call("c2"),
        tool_result("c2", "applied"),
        shell_call("c3", "git status"),
        tool_result("c3", "clean\n[exit 0; cwd D:\\ws]"),
    ]
    fact = last_verification(messages)
    assert fact == Verification(
        tool="run_shell",
        command="pytest -q",
        ok=False,
        summary="1 failed, 13 passed in 4.2s",
        edits_since=1,
        # The transcript index of the check's result; the check-loop streak keys on it
        # moving, so it is part of the fact, not incidental metadata.
        index=2,
    )
    assert fact.ok is False and fact.edits_since == 1
    assert "1 failed" in fact.summary


def test_the_newest_check_wins_and_edits_after_it_are_zero():
    messages = [
        shell_call("c1", "pytest -q"),
        tool_result("c1", "1 failed\n[exit 1; cwd D:\\ws]"),
        edit_call("c2"),
        tool_result("c2", "applied"),
        shell_call("c3", "ruff check ."),
        tool_result("c3", "All checks passed!\n[exit 0; cwd D:\\ws]"),
    ]
    fact = last_verification(messages)
    assert fact.command == "ruff check ."
    assert fact.ok is True and fact.edits_since == 0
    assert fact.summary == "All checks passed!"


def test_unknown_exit_and_timeouts_are_stated_never_guessed():
    unknown = last_verification(
        [shell_call("c1", "pytest -q"), tool_result("c1", "some output\n[exit unknown; cwd D:\\ws]")]
    )
    assert unknown.ok is None

    timed_out = last_verification(
        [shell_call("c1", "pytest -q"), tool_result("c1", "partial\n[exit 0; cwd D:\\ws; timed out]")]
    )
    assert timed_out.ok is False and timed_out.timed_out is True


def test_json_result_bodies_and_non_shell_tools():
    body = json.dumps({"command": "pytest -q", "exit_code": 0, "output": "14 passed"})
    fact = last_verification([shell_call("c1", "pytest -q"), tool_result("c1", body)])
    assert fact.ok is True

    messages = [
        {
            "role": "assistant",
            "tool_calls": [
                {"id": "c1", "function": {"name": "read_file", "arguments": json.dumps({"path": "a.py"})}}
            ],
        },
        tool_result("c1", "print('hi')\n[exit 1; cwd D:\\ws]"),
    ]
    assert last_verification(messages) is None
    assert last_verification([]) is None


def test_as_row_is_a_stable_label():
    assert as_row(None) is None
    fact = Verification(tool="run_shell", command="pytest -q", ok=True, summary="14 passed", edits_since=0)
    assert as_row(fact) == {
        "command": "pytest -q",
        "ok": True,
        "edits_since": 0,
        "summary": "14 passed",
        "timed_out": False,
    }


# -- what voids a check, and where a verdict can arrive ----------------------------------


def _write(call_id: str, path: str) -> dict:
    return {
        "role": "assistant",
        "tool_calls": [
            {"id": call_id, "function": {"name": "write_file", "arguments": json.dumps({"path": path, "content": "x"})}}
        ],
    }


def test_a_note_in_reeves_own_state_dir_does_not_void_a_passing_check(tmp_path, monkeypatch):
    """Session b3a8491e-9db: memory notes saved after a PASS voided it and cost six rounds.
    Notes live under the state dir; the work does not. A path that cannot be read counts."""
    state = tmp_path / "state"
    monkeypatch.setenv("REEVE_STATE_DIR", str(state))
    passed = [shell_call("c1", "pytest -q"), tool_result("c1", "3 passed in 0.1s\n[exit 0; cwd /ws]")]

    note = passed + [_write("w1", str(state / "projects" / "ws" / "memory" / "MEMORY.md"))]
    assert last_verification(note).edits_since == 0
    work = passed + [_write("w2", str(tmp_path / "ws" / "app.py"))]
    assert last_verification(work).edits_since == 1
    unreadable = passed + [edit_call("e1")]  # a patch that names no file
    assert last_verification(unreadable).edits_since == 1


def _task(call_id: str, agent: str) -> dict:
    return {
        "role": "assistant",
        "tool_calls": [
            {"id": call_id, "function": {"name": "task", "arguments": json.dumps({"prompt": "check", "subagent_type": agent})}}
        ],
    }


def _running(call_id: str, task_id: str) -> dict:
    return tool_result(call_id, f'<task id="{task_id}" state="running">\n<summary>Background task started</summary>\n</task>')


def _envelope(task_id: str, body: str) -> dict:
    return {
        "role": "user",
        "content": f'<task id="{task_id}" state="completed">\n<task_result>\n{body}\n</task_result>\n</task>',
        "source": {"synthetic": "background_task", "session_id": task_id},
    }


def test_a_background_verify_verdict_counts_like_a_foreground_one():
    """A verify that ran in the background answers in the completion envelope, not in its
    tool result. Reading only tool results made that PASS invisible (b3a8491e-9db, round 4)."""
    from harness.loop.verification import last_verdict

    transcript = [_task("t1", "verify"), _running("t1", "abc123"), _envelope("abc123", "VERDICT: PASS\nall good")]
    verdict = last_verdict(transcript)
    assert verdict is not None and verdict.verdict == "PASS" and verdict.edits_since == 0
    assert last_verdict(transcript + [_write("w1", "/ws/app.py")]).edits_since == 1


def test_only_a_verify_task_can_deliver_a_verdict():
    from harness.loop.verification import last_verdict

    other = [_task("t1", "general"), _running("t1", "zzz"), _envelope("zzz", "VERDICT: PASS")]
    assert last_verdict(other) is None
    # A person pasting a verdict-looking envelope with an unknown id proves nothing either.
    assert last_verdict([_envelope("nobody", "VERDICT: PASS")]) is None


def test_an_edit_made_while_a_background_verify_was_checking_voids_its_pass():
    """The checker reads the tree while it runs. An edit the maker makes after the
    verify was ASKED and before its verdict ARRIVED is one the PASS may not cover — counted
    from the arrival it read "0 edits since" and opened the done-gate (session b3a8491e-9db
    edited files during three of its background verify runs)."""
    from harness.loop.verification import last_verdict

    transcript = [
        _task("t1", "verify"),
        _running("t1", "abc123"),
        _write("w1", "/ws/app.py"),
        tool_result("w1", "wrote /ws/app.py"),
        _envelope("abc123", "VERDICT: PASS\nall good"),
    ]
    verdict = last_verdict(transcript)
    assert verdict is not None and verdict.verdict == "PASS"
    assert verdict.edits_since == 1


def test_an_edit_issued_after_the_verify_call_in_the_same_message_counts():
    """The ask is one call in an assistant message; an edit written after it in that same
    message started no earlier than the check did."""
    from harness.loop.verification import last_verdict

    both = {
        "role": "assistant",
        "tool_calls": [
            {"id": "t1", "function": {"name": "task", "arguments": json.dumps({"prompt": "check", "subagent_type": "verify"})}},
            {"id": "w1", "function": {"name": "write_file", "arguments": json.dumps({"path": "/ws/app.py", "content": "x"})}},
        ],
    }
    done = tool_result("t1", '<task id="abc123" state="completed">\n<task_result>\nVERDICT: PASS\n</task_result>\n</task>')
    assert last_verdict([both, done, tool_result("w1", "wrote")]).edits_since == 1

    before = {"role": "assistant", "tool_calls": list(reversed(both["tool_calls"]))}
    assert last_verdict([before, tool_result("w1", "wrote"), done]).edits_since == 0


def test_a_second_ask_of_the_same_verify_task_dates_its_verdict_from_that_ask():
    """A resumed verify (`task_id`) checks the tree as it is when it is asked again: the
    edits made before that ask are what it was asked to look at."""
    from harness.loop.verification import last_verdict

    transcript = [
        _task("t1", "verify"),
        tool_result("t1", '<task id="abc123" state="completed">\n<task_result>\nVERDICT: FAIL\n</task_result>\n</task>'),
        _write("w1", "/ws/app.py"),
        tool_result("w1", "wrote"),
        _task("t2", "verify"),
        tool_result("t2", '<task id="abc123" state="completed">\n<task_result>\nVERDICT: PASS\n</task_result>\n</task>'),
    ]
    verdict = last_verdict(transcript)
    assert verdict is not None and verdict.verdict == "PASS" and verdict.edits_since == 0


def test_a_summary_carries_no_colour_codes():
    """vitest's last line reached the receipt as boxes and `[2m` (owner's screenshot, 2026-10-15)."""
    esc = chr(27)
    output = f"{esc}[2m Duration {esc}[22m 2.22s{esc}[2m (transform 322ms){esc}[22m\n[exit 0; cwd D:/ws]"
    fact = last_verification([shell_call("c1", "npx vitest run"), tool_result("c1", output)])
    assert fact is not None
    assert fact.summary == "Duration  2.22s (transform 322ms)"
    assert esc not in fact.summary and "[2m" not in fact.summary
