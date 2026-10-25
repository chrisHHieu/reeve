"""A tool-call id is unique only within one response; the ledger reads results by position (call_pairs.py).

Found from a goal that could not be closed: the Gemini client names its calls `call_<position in the response>`,
so every step restarted at `call_0` (65 of them in one real session, b3a8491e-9db). The verification ledger kept a
table `id -> call` over the whole transcript, which holds the LAST call of each id. At the moment the model called
`goal(done)` the newest `call_0` was that very call, so every shell result before it read as "goal" and every
acceptance check read as "has not been run", however often it was run, whatever shell or spelling was used.

What has to hold:

  - a result is read with the NEWEST call before it that has its id, on every reader of a transcript;
  - the goal gate sees checks that ran, with ids repeated, and judges them like any other run;
  - Gemini makes ids unique across the session, so a new transcript does not have the shape at all;
  - the session's scratch folder (when the work is elsewhere) is not an edit the checks ran over, unless the check
    names the file; a session that works IN its scratch folder exempts nothing.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

from harness.loop.call_pairs import answered_calls, call_name
from harness.context.compaction.extraction import iter_tool_calls as _iter_tool_calls
from harness.loop.goal_proof import completion_gaps, proven
from harness.loop.goals import Goal, GoalStore, goal_tools
from harness.llm.providers import GeminiProvider
from harness.loop.verification import last_run, last_verdict, last_verification
from harness.loop.work_edits import is_work_edit, work_edits_after


def _call(call_id, name, **arguments):
    return {"id": call_id, "function": {"name": name, "arguments": json.dumps(arguments)}}


def _asks(*calls):
    return {"role": "assistant", "content": "", "tool_calls": list(calls)}


def _answer(call_id, content):
    return {"role": "tool", "tool_call_id": call_id, "content": content}


def _shell(command, *, status="[exit 0; cwd D:\\ws]", output="ok"):
    # Every call is `call_0`, as the Gemini client named them.
    return [_asks(_call("call_0", "run_shell", command=command)), _answer("call_0", f"{output}\n{status}")]


def _transcript_ending_in_done():
    """Three checks that ran, then the `done` call, all with the id `call_0`."""
    messages = [{"role": "user", "content": "close the goal"}]
    for command in ("pnpm exec tsc --noEmit", "pnpm test", "pnpm check:api"):
        messages += _shell(command)
    messages.append(_asks(_call("call_0", "goal", action="done")))
    return messages


CHECKS = ["pnpm exec tsc --noEmit", "pnpm test", "pnpm check:api"]


# -- the pairing rule ---------------------------------------------------------------------


def test_a_result_is_paired_with_the_newest_call_before_it_with_its_id():
    messages = [
        _asks(_call("call_0", "first")),
        _answer("call_0", "r1"),
        _asks(_call("call_0", "second")),
        _answer("call_0", "r2"),
        _asks(_call("call_0", "third")),  # the newest call_0, with no result yet
    ]
    paired = [(m["content"], call_name(call)) for _i, m, call in answered_calls(messages) if m["role"] == "tool"]
    assert paired == [("r1", "first"), ("r2", "second")]


def test_a_result_with_no_call_before_it_has_none():
    messages = [_answer("call_7", "orphan"), _asks(_call("call_7", "late"))]
    assert [call for _i, m, call in answered_calls(messages) if m["role"] == "tool"] == [None]


# -- the ledger ---------------------------------------------------------------------------


def test_the_ledger_sees_runs_whose_ids_repeat_and_whose_last_call_is_something_else():
    messages = _transcript_ending_in_done()
    for command in CHECKS:
        run = last_run(messages, command)
        assert run is not None and run.ok is True and run.command == command
    assert last_verification(messages).command == "pnpm check:api"


def test_the_goal_gate_is_not_blind_to_checks_that_ran():
    goal = Goal(objective="ship", checks=list(CHECKS))
    messages = _transcript_ending_in_done()
    assert completion_gaps(goal, messages) == []
    assert proven(goal, messages)


def test_the_gate_still_refuses_what_did_not_run_or_failed():
    goal = Goal(objective="ship", checks=[*CHECKS, "pnpm check:read"])
    messages = _transcript_ending_in_done()
    assert completion_gaps(goal, messages) == ["`pnpm check:read` has not been run in this session"]

    failing = _shell("pnpm test", status="[exit 1; cwd D:\\ws]", output="1 failed")
    gaps = completion_gaps(Goal(objective="ship", checks=["pnpm test"]), [*_shell("pnpm test"), *failing])
    assert gaps == ["`pnpm test` failed on its last run"]  # the newest run of the command is the one read


def test_an_edit_after_a_check_still_makes_it_stale_with_repeated_ids():
    goal = Goal(objective="ship", checks=["pnpm test"])
    messages = [*_shell("pnpm test"), _asks(_call("call_0", "write_file", path="D:\\ws\\app.py", content="x"))]
    assert completion_gaps(goal, messages) == ["`pnpm test` passed, but 1 file edit(s) came after it"]


def test_a_verify_verdict_is_found_when_the_task_call_shares_its_id():
    messages = [
        _asks(_call("call_0", "task", subagent_type="verify", prompt="check it")),
        _answer("call_0", "VERDICT: PASS"),
        _asks(_call("call_0", "read_file", path="a.py")),  # a later call reusing the id
        _answer("call_0", "contents"),
    ]
    verdict = last_verdict(messages)
    assert verdict is not None and verdict.verdict == "PASS"


def test_compaction_extracts_each_call_with_its_own_result():
    span = [
        _asks(_call("call_0", "write_file", path="a.py")),
        _answer("call_0", "wrote a.py"),
        _asks(_call("call_0", "write_file", path="b.py")),
        _answer("call_0", "wrote b.py"),
    ]
    assert [(args["path"], result) for _name, args, result in _iter_tool_calls(span)] == [
        ("a.py", "wrote a.py"),
        ("b.py", "wrote b.py"),
    ]


# -- Gemini makes its ids unique ----------------------------------------------------------


def _gemini_response(*names):
    parts = [SimpleNamespace(text=None, function_call=SimpleNamespace(name=n, args={})) for n in names]
    return SimpleNamespace(
        candidates=[SimpleNamespace(content=SimpleNamespace(parts=parts), finish_reason="STOP")]
    )


def _gemini_client(response):
    return SimpleNamespace(
        models=SimpleNamespace(
            generate_content=lambda **kwargs: response,
            generate_content_stream=lambda **kwargs: iter([response]),
        )
    )


def test_gemini_call_ids_are_unique_across_responses():
    ids = []
    for _ in range(3):
        turn = GeminiProvider(client=_gemini_client(_gemini_response("a", "b"))).complete(
            model="gemini-3.7-flash", messages=[{"role": "user", "content": "go"}]
        )
        ids += [c.id for c in turn.tool_calls]
    assert len(ids) == 6 and len(set(ids)) == 6
    assert not any(i in ("call_0", "call_1") for i in ids)


def test_gemini_streamed_call_ids_are_unique_too():
    ids = []
    for _ in range(2):
        provider = GeminiProvider(client=_gemini_client(_gemini_response("a")))
        for chunk in provider.stream(model="gemini-3.7-flash", messages=[{"role": "user", "content": "go"}]):
            if getattr(chunk, "turn", None) is not None:
                ids += [c.id for c in chunk.turn.tool_calls]
    assert len(ids) == 2 and len(set(ids)) == 2


# -- the session's scratch folder is not the work -------------------------------------------


SCRATCH = "D:\\scratch\\s1"


def test_an_edit_in_the_scratch_folder_is_not_work_unless_the_check_names_the_file():
    args = {"path": SCRATCH + "\\notes.md", "content": "x"}
    assert is_work_edit("write_file", args) is True  # nothing exempt: as before
    assert is_work_edit("write_file", args, exempt=[SCRATCH]) is False
    # The check runs that very file: it is the work.
    guard = {"path": SCRATCH + "\\guard.py", "content": "x"}
    assert is_work_edit("write_file", guard, command=f"python {SCRATCH}\\guard.py", exempt=[SCRATCH]) is True
    # An edit anywhere else, or one that cannot be read, still counts.
    assert is_work_edit("write_file", {"path": "D:\\ws\\app.py", "content": "x"}, exempt=[SCRATCH]) is True
    assert is_work_edit("write_file", {"content": "x"}, exempt=[SCRATCH]) is True
    # One path outside the folder is enough for the whole call to count.
    patch = "*** Update File: " + SCRATCH + "\\a.md\n*** Update File: D:\\ws\\app.py\n"
    assert is_work_edit("apply_patch", {"patch": patch}, exempt=[SCRATCH]) is True


def test_the_gate_ignores_scratch_edits_only_when_told_the_scratch_folder():
    goal = Goal(objective="ship", checks=["pnpm test"])
    messages = [*_shell("pnpm test"), _asks(_call("call_1", "write_file", path=SCRATCH + "\\handoff.md", content="x"))]
    assert completion_gaps(goal, messages) == ["`pnpm test` passed, but 1 file edit(s) came after it"]
    assert completion_gaps(goal, messages, exempt=[SCRATCH]) == []
    assert work_edits_after(messages, 1, exempt=[SCRATCH]) == 0


def test_done_reads_the_exempt_folders_the_session_gives_it(tmp_path, monkeypatch):
    monkeypatch.setenv("REEVE_STATE_DIR", str(tmp_path / "state"))
    messages = [*_shell("pnpm test"), _asks(_call("call_1", "write_file", path=SCRATCH + "\\handoff.md", content="x"))]

    def attempt(exempt):
        store = GoalStore.for_session("s1")
        store.save(Goal(objective="ship", checks=["pnpm test"]))
        tool = goal_tools(store, messages_of=lambda: messages, exempt_of=exempt)[0]
        return tool(action="done", reason="shipped")

    refused = attempt(None)
    assert "done refused" in refused["error"] and "1 file edit(s) came after it" in refused["error"]
    assert attempt(lambda: [SCRATCH])["goal"]["phase"] == "complete"
