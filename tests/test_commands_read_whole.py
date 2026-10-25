"""A command is read whole and said short.

The ledger once kept the first 120 characters of every shell command and asked of those whether
it ran a check. A check after a long first step (`cd "<a long folder>"; python -m pytest -q`,
`python -c "…"; python -m pytest`) was no check: 114 of 618 checks in the session store, 2026-10-23.
The repairs those runs proved went unrecorded, a failing one recalled nothing, and two commands
alike for 120 characters were one. What a command is, is now read on all of it; only where it is
said (a note, the receipt, a record) is it shortened, by one rule, keeping both its ends.
"""

from __future__ import annotations

import json

import pytest

from harness.context.memory.repairs import Repairs
from harness.loop.goal_proof import completion_gaps
from harness.loop.goals import Goal
from harness.loop.repair_facts import repair_of
from harness.loop.verification import as_row, last_run, last_variant, last_verification
from harness.tools.checks import SHOWN_CHARS, is_check_command, shown_command

#: First steps longer than what used to be read, each before the check itself.
FAR = 'cd "C:/Users/someone/AppData/Local/Temp/' + "nested-folder/" * 9 + 'ws"; '
PROBE = (
    'python -c "from shop.data.catalogue import INDEX; from collections import Counter; '
    "c=Counter(INDEX.values()); print([ (k,v) for k,v in c.items() if v>1 ]); print(len(INDEX), 'entries')\"; "
)
FIRST_STEPS = [FAR, PROBE]


def _shell(n, command, output, code):
    call = {"id": f"c{n}", "function": {"name": "run_shell", "arguments": json.dumps({"command": command})}}
    return [
        {"role": "assistant", "content": "", "tool_calls": [call]},
        {"role": "tool", "tool_call_id": call["id"], "content": f"{output}\n[exit {code}; cwd /ws]", "ts": 1_800_000_000 + n},
    ]


def _edit(n, path="src/a.py"):
    call = {"id": f"c{n}", "function": {"name": "write_file", "arguments": json.dumps({"path": path, "content": "x"})}}
    return [
        {"role": "assistant", "content": "", "tool_calls": [call]},
        {"role": "tool", "tool_call_id": call["id"], "content": "wrote it", "ts": 1_800_000_000 + n},
    ]


RED = "FAILED tests/test_x.py::test_a - AssertionError\n1 failed in 0.1s"
GREEN = "3 passed in 0.1s"


@pytest.mark.parametrize("first", FIRST_STEPS)
def test_a_check_after_a_long_first_step_is_a_check(first):
    assert len(first) > SHOWN_CHARS
    messages = [{"role": "user", "content": "fix it"}, *_shell(1, first + "python -m pytest -q", RED, 1)]
    fact = last_verification(messages)
    assert fact is not None and fact.ok is False
    assert fact.command == first + "python -m pytest -q"  # the whole of it, not its first characters


@pytest.mark.parametrize("first", FIRST_STEPS)
def test_a_repair_proved_by_such_a_run_is_a_repair(first):
    messages = [
        {"role": "user", "content": "fix it"},
        *_shell(1, first + "python -m pytest -q tests/test_x.py", RED, 1),
        *_edit(2),
        *_shell(3, first + "python -m pytest -q", GREEN, 0),
    ]
    repair = repair_of(messages)
    assert repair is not None and repair.marks == {"test:tests/test_x.py::test_a"}
    assert repair.files == ("src/a.py",)


#: A first step that leaves `uv run pytest` inside the first 120 characters and what follows it
#: outside: cut there, every such run read as the bare `pytest`, the whole suite.
NEAR = 'cd D:/Project/app/backend; $env:UV_PROJECT_ENVIRONMENT="C:/Users/someone/env/' + "x" * 26 + '"; '


@pytest.mark.parametrize("narrower", ["tests/test_other.py", "-k test_b tests/test_x.py"])
def test_a_run_that_did_not_look_at_the_failure_proves_no_repair(narrower):
    """Proofs once, in the session store: false repairs of tests those runs never ran."""
    assert len(NEAR + "uv run pytest") < SHOWN_CHARS < len(NEAR + "uv run pytest " + narrower)
    messages = [
        {"role": "user", "content": "fix it"},
        *_shell(1, NEAR + "uv run pytest tests/test_x.py", RED, 1),
        *_edit(2),
        *_shell(3, NEAR + "uv run pytest " + narrower, GREEN, 0),
    ]
    assert repair_of(messages) is None
    # The run that does look at it still proves it.
    messages[-2:] = _shell(3, NEAR + "uv run pytest tests/test_x.py", GREEN, 0)
    assert repair_of(messages).marks == {"test:tests/test_x.py::test_a"}


def test_two_commands_alike_for_their_first_characters_are_two_commands():
    one, other = FAR + "python -m pytest -q tests/test_a.py", FAR + "python -m pytest -q tests/test_b.py"
    messages = [{"role": "user", "content": "x"}, *_shell(1, one, GREEN, 0)]
    assert last_run(messages, one) is not None
    assert last_run(messages, other) is None
    # And a pipe added after a long command is still seen as that command's variant.
    piped = [{"role": "user", "content": "x"}, *_shell(1, one + " | tail -5", GREEN, 0)]
    assert last_variant(piped, one) == one + " | tail -5"


def test_a_command_is_said_short_with_both_its_ends():
    long = FAR + "python -m pytest -q"
    said = shown_command(long)
    assert len(said) == SHOWN_CHARS and "…" in said
    assert said.startswith('cd "C:/Users/someone') and said.endswith("python -m pytest -q")
    assert shown_command("pytest   -q\n  tests/") == "pytest -q tests/"  # a short one is only tidied
    assert shown_command("") == ""
    assert is_check_command(long) and not is_check_command(shown_command(FAR + "ls"))


def test_where_a_command_is_said_it_is_said_short():
    long = FAR + "python -m pytest -q"
    messages = [{"role": "user", "content": "x"}, *_shell(1, long, RED, 1)]
    assert as_row(last_verification(messages))["command"] == shown_command(long)


def test_the_model_is_told_the_command_short():
    from harness.loop.goal_ledger import standing_of
    from harness.loop.witnessed import witnessed_record

    long = FAR + "python -m pytest -q"
    messages = [{"role": "user", "content": "x"}, *_shell(1, long, RED, 1)]
    assert f"- Last check: `{shown_command(long)}` FAILED" in witnessed_record(messages)
    assert standing_of(Goal(objective="ship"), messages) == f"`{shown_command(long)}` failed"


def test_a_compactions_working_state_says_what_a_long_command_ran():
    from harness.context.compaction.extraction import extract_working_state

    long = FAR + "python -m pytest -q"
    state = extract_working_state([{"role": "user", "content": "x"}, *_shell(1, long, RED, 1)])
    assert shown_command(long, 160) in state and "python -m pytest -q" in state


def test_a_repairs_record_keeps_the_command_as_it_is_said(tmp_path):
    store = Repairs(tmp_path, resolve=lambda raw: tmp_path / raw, stamp=lambda: ("s", 1))
    long = FAR + "python -m pytest -q"
    entry = store.record(["test:tests/test_x.py::test_a"], long, [], 3)
    assert entry["command"] == shown_command(long)


def test_a_declared_check_that_failed_is_said_once_however_long():
    long = FAR + "python -m pytest -q"
    goal = Goal(objective="ship", checks=[long])
    messages = [{"role": "user", "content": "x"}, *_shell(1, long, RED, 1)]
    gaps = completion_gaps(goal, messages)
    assert gaps == [f"`{long}` failed on its last run"]  # declared: said as declared, and not twice
    other = completion_gaps(Goal(objective="ship"), messages)
    assert other == [f"the last check, `{shown_command(long)}`, failed and nothing changed since"]
