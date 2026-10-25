"""Reading a check: what it is, whether its output says it failed, and the names a repair
can be kept under, in two tiers. The outputs are invented in the shapes real runners print."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from harness.context.memory.repairs import Repairs, note
from harness.loop.repair_facts import covers, repair_of
from harness.tools.checks import (
    cut_by_command,
    failure_marks,
    is_check_command,
    is_setup,
    reports_failure,
    runner_of,
    spoken,
)
from harness.tools.shell_present import _shell_facts
from harness.tools.shell_status import _cut_check_note

import scripts.harness_review as harness_review

#: Node's own runner, default reporter: suites above the section, the failure inside it.
NODE_SPEC = """\
▶ widgets
  ✔ renders a label (0.51ms)
  ✖ renders the debit tone (2.44ms)
✖ widgets (5.67ms)
ℹ tests 3
ℹ pass 2
ℹ fail 1
ℹ duration_ms 812.4

✖ failing tests:

test at tests\\widgets.test.mjs:41:3
✖ renders the debit tone (2.44ms)
  AssertionError [ERR_ASSERTION]: The input did not match the regular expression /debit/.
"""

NODE_TAP = """\
TAP version 13
# Subtest: parses a date
ok 1 - parses a date
not ok 2 - rounds half up
not ok 3 - handles leap years # TODO not written yet
not ok 4 - reads the locale # SKIP no locale here
1..4
# pass 1
# fail 1
"""


# --- the first tier: what failed -------------------------------------------------------


def test_node_names_the_test_inside_its_failing_section_with_its_file():
    assert failure_marks(NODE_SPEC) == {"test:tests/widgets.test.mjs::renders the debit tone"}
    # the suite line above the section is not a failure of its own, and durations are left out
    assert failure_marks(NODE_SPEC.replace("2.44ms", "9.1ms")) == failure_marks(NODE_SPEC)


def test_node_without_a_location_line_still_names_the_test():
    text = "✖ failing tests:\n\n✖ keeps the order (1.2ms)\n  AssertionError\n"
    assert failure_marks(text) == {"test:keeps the order"}


def test_tap_names_a_failure_and_not_a_todo_or_a_skip():
    assert failure_marks(NODE_TAP) == {"test:rounds half up"}


@pytest.mark.parametrize(
    "text, failed",
    [("ℹ tests 3\nℹ pass 2\nℹ fail 1\n", True), ("ℹ tests 3\nℹ pass 3\nℹ fail 0\n", False), (NODE_TAP, True)],
)
def test_node_and_tap_summaries_say_whether_the_run_failed(text, failed):
    assert reports_failure(text) is failed


# --- the second tier: what kept the check from running ---------------------------------


@pytest.mark.parametrize(
    "text, mark",
    [
        ("E   ModuleNotFoundError: No module named 'app.models'\n1 error in 0.2s", "setup:module:app.models"),
        ("ImportError: cannot import name 'Ledger' from 'app.books'", "setup:import:app.books.Ledger"),
        ("Error: Cannot find module 'lightningcss'\nRequire stack:", "setup:module:lightningcss"),
        ('TypeError [ERR_UNKNOWN_FILE_EXTENSION]: Unknown file extension ".tsx" for /w/a.tsx', "setup:extension:.tsx"),
        ("/usr/bin/bash: line 1: vitest: command not found", "setup:command:vitest"),
        ("The term 'pnpm' is not recognized as a name of a cmdlet, function, script file", "setup:command:pnpm"),
        ("'tsc' is not recognized as an internal or external command,", "setup:command:tsc"),
        ("ERROR: file or directory not found: tests/test_gone.py", "setup:path:tests/test_gone.py"),
    ],
)
def test_what_kept_a_check_from_running_is_named_too(text, mark):
    assert failure_marks(text) == {mark}
    assert is_setup(mark)


def test_a_setup_name_is_used_only_when_the_output_names_no_failure():
    text = (
        "E   ModuleNotFoundError: No module named 'app.models'\n"
        "FAILED tests/test_x.py::test_a - ModuleNotFoundError\n1 failed in 0.1s"
    )
    assert failure_marks(text) == {"test:tests/test_x.py::test_a"}


def test_a_setup_mark_is_said_as_what_happened():
    assert spoken("setup:module:app.models") == "module app.models not found"
    assert spoken("setup:command:pnpm") == "command pnpm not found"
    assert spoken("test:tests/x.py::test_a") == "tests/x.py::test_a"


# --- the command ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "command, runner",
    [
        ("pytest -q tests", "pytest"),
        ('cd backend; $env:UV_X="1"; uv run pytest -q', "pytest"),
        ('node --experimental-strip-types --import ./tests/loader.mjs --test "tests/**/*.test.mjs"', "node-test"),
        ("cd frontend && pnpm test", "package-script"),
        ("npx tsc --noEmit && pnpm test", "tsc"),
        ("git status; ls tests", None),
        ("node build.mjs", None),
    ],
)
def test_a_check_is_known_by_its_runner(command, runner):
    assert runner_of(command) == runner
    assert is_check_command(command) is (runner is not None)


@pytest.mark.parametrize(
    "command, cut",
    [
        ("pnpm test 2>&1 | Select-Object -Last 10", True),
        ("uv run pytest -q 2>&1 | tail -n 20", True),
        ("pytest -q | grep FAILED", True),
        ("git log --oneline | head -5; pytest -q", False),  # the cut is before the check
        ("pytest -q > out.txt", False),
        ("pytest -q", False),
        ("ls | head", False),  # not a check
    ],
)
def test_a_pipe_that_keeps_part_of_a_checks_output_is_a_cut(command, cut):
    assert cut_by_command(command) is cut


# --- a setup failure repaired by how the check is run ----------------------------------


class _Script:
    def __init__(self):
        self.messages = [{"role": "user", "content": "run the tests"}]
        self._n = 0

    def run(self, command, output, code):
        self._n += 1
        call = {"id": f"c{self._n}", "function": {"name": "run_shell", "arguments": json.dumps({"command": command})}}
        self.messages += [
            {"role": "assistant", "content": "", "tool_calls": [call]},
            {"role": "tool", "tool_call_id": call["id"], "content": f"{output}\n[exit {code}; cwd /ws]", "ts": 1_800_000_000 + self._n},
        ]
        return self

    def edit(self, path="src/a.py"):
        self._n += 1
        call = {"id": f"c{self._n}", "function": {"name": "write_file", "arguments": json.dumps({"path": path, "content": "x"})}}
        self.messages += [
            {"role": "assistant", "content": "", "tool_calls": [call]},
            {"role": "tool", "tool_call_id": call["id"], "content": "wrote it", "ts": 1_800_000_000 + self._n},
        ]
        return self


MISSING = "E   ModuleNotFoundError: No module named 'app.models'\n1 error in 0.2s"
GREEN = "3 passed in 0.1s"


def test_a_setup_failure_fixed_by_running_the_check_another_way_is_a_repair():
    script = _Script().run("pytest -q", MISSING, 1).run('$env:UV_X="1"; uv run pytest -q', GREEN, 0)
    repair = repair_of(script.messages)
    assert repair.marks == {"setup:module:app.models"}
    assert repair.files == ()
    assert repair.command == '$env:UV_X="1"; uv run pytest -q'


def test_a_setup_failure_that_went_away_by_itself_repaired_nothing():
    assert repair_of(_Script().run("pytest -q", MISSING, 1).run("pytest -q", GREEN, 0).messages) is None


def test_a_setup_failure_is_looked_at_again_only_by_the_same_runner():
    assert covers("uv run pytest -q", "pytest -q", "setup:module:app.models")
    assert not covers("ruff check .", "pytest -q", "setup:module:app.models")
    script = _Script().run("pytest -q", MISSING, 1).edit().run("ruff check .", "All checks passed!", 0)
    assert repair_of(script.messages) is None


def test_a_setup_failure_fixed_by_an_edit_keeps_the_files(tmp_path):
    script = _Script().run("pytest -q", MISSING, 1).edit("app/models.py").run("pytest -q", GREEN, 0)
    repair = repair_of(script.messages)
    assert (repair.marks, repair.files) == ({"setup:module:app.models"}, ("app/models.py",))


def test_a_setup_repair_is_recalled_as_how_the_check_ran_with_credentials_masked(tmp_path):
    store = Repairs(tmp_path, stamp=lambda: ("s1", 1))
    store.record({"setup:module:app.models"}, 'API_KEY=sk-ant-' + 'api03-abcdefghijklmnopqrstuvwxyz0123 uv run pytest -q', [], steps=4)
    found = Repairs(tmp_path, stamp=lambda: ("s2", 1)).recall({"setup:module:app.models"})
    said = note(found)
    assert "The check could not run (`module app.models not found`) until" in said
    assert "uv run pytest -q" in said
    assert "sk-ant-api03" not in said and "sk-ant-api03" not in (tmp_path / ".repairs.json").read_text(encoding="utf-8")


# --- what the shell's result says ------------------------------------------------------


def test_a_failed_checks_facts_count_the_names_its_output_gives():
    facts = _shell_facts({"command": "pnpm test"}, {"exit_code": 1, "output": NODE_SPEC})
    assert (facts["check"], facts["named"], facts.get("setup"), facts.get("cut_by_command")) == ("package-script", 1, None, None)
    cut = _shell_facts({"command": "pnpm test | Select-Object -Last 3"}, {"exit_code": 1, "output": "ℹ fail 1\n"})
    assert (cut["named"], cut["cut_by_command"]) == (0, True)
    setup = _shell_facts({"command": "pytest -q"}, {"exit_code": 1, "output": MISSING})
    assert setup["setup"] is True
    assert "named" not in _shell_facts({"command": "pytest -q"}, {"exit_code": 0, "output": GREEN})
    assert "check" not in _shell_facts({"command": "ls"}, {"exit_code": 0, "output": "a"})


def test_a_failed_check_cut_by_its_pipe_into_naming_nothing_is_told_so():
    said = _cut_check_note("pnpm test | Select-Object -Last 3", "ℹ fail 1\n", failed=True)
    assert said and "without the pipe" in said
    assert _cut_check_note("pnpm test | Select-Object -Last 30", NODE_SPEC, failed=True) is None  # still named
    assert _cut_check_note("pnpm test", "ℹ fail 1\n", failed=True) is None  # not cut
    assert _cut_check_note("pnpm test | tail -3", "ℹ pass 3\n", failed=False) is None  # passed


# --- the count ------------------------------------------------------------------------


def _log(tmp_path: Path, events: list[dict]) -> Path:
    path = tmp_path / "s.events.jsonl"
    path.write_text("\n".join(json.dumps({"seq": i, **e}) for i, e in enumerate(events)), encoding="utf-8")
    return path


def test_the_review_counts_failed_checks_that_name_nothing_and_why(tmp_path):
    def run(cid, command, content, facts=None):
        result = {"type": "tool/result", "call_id": cid, "name": "run_shell", "content": content}
        if facts is not None:
            result["facts"] = facts
        return [{"type": "tool/call", "call_id": cid, "name": "run_shell", "arguments": json.dumps({"command": command})}, result]

    log = _log(tmp_path, [
        *run("a", "pnpm test", "...", {"exit_code": 1, "check": "package-script", "named": 0}),         # whole, unnamed
        *run("b", "pnpm test | tail -3", "ℹ fail 1\n[exit 1; cwd /w]"),                                   # cut, read from text
        *run("c", "pytest -q", "FAILED tests/t.py::test_a\n[exit 1; cwd /w]"),                            # older row, whole: not guessed
        *run("d", "pytest -q", "3 passed in 0.1s\n[exit 0; cwd /w]"),                                     # passed
        *run("e", "pytest -q", "", {"exit_code": 1, "check": "pytest", "named": 2}),                      # named
    ])
    counts, refs = harness_review.review([log])
    assert counts["failed-checks"] == 4
    assert counts["unnamed-failures"] == 2
    assert counts["unnamed-failures in a whole output"] == 1 and counts["unnamed-failures cut by its pipe"] == 1
    assert counts["failed-checks read before facts"] == 1
    assert {why for *_, why in refs["unnamed-failures"]} == {"in a whole output", "cut by its pipe"}


def test_a_test_file_that_failed_whole_is_named_once_whichever_way_its_slashes_lean():
    text = "✖ failing tests:\n\ntest at tests\\a.test.mjs:1:1\n✖ tests\\a.test.mjs (31ms)\n"
    assert failure_marks(text) == {"test:tests/a.test.mjs"}
    assert failure_marks("not ok 1 - tests\\a.test.mjs\n# fail 1\n") == {"test:tests/a.test.mjs"}
    # a runner that escapes its backslashes in a message (`tests\\a.test.mjs`) names the same file
    assert failure_marks(r"not ok 1 - tests\\a.test.mjs" + "\n# fail 1\n") == {"test:tests/a.test.mjs"}


# The five "failed checks that name no failure" that refuted `a-whole-failed-check-names-its-failure`
# (2026-10-19) were none of them a test that failed unnamed: a package install read as a pytest
# run, a check that found no tests, and a check behind a `cd` that went nowhere. Invented in the
# shapes the store held.


@pytest.mark.parametrize(
    "command",
    [
        "uv pip install --python C:/py/python.exe ipykernel numpy pytest",
        "pip install -U pytest pytest-cov",
        "npm i -D vitest @vitest/ui",
        "pnpm add -D eslint",
        "poetry add --group dev mypy",
    ],
)
def test_installing_a_runner_is_not_running_it(command):
    assert runner_of(command) is None and not is_check_command(command)


@pytest.mark.parametrize(
    "command, runner",
    [
        ("pip install pytest && pytest -q", "pytest"),
        ("npm install; npm test", "package-script"),
        ("uv pip install ruff | tail -1 ; ruff check .", "ruff"),
    ],
)
def test_a_check_after_an_install_is_still_a_check(command, runner):
    assert runner_of(command) == runner


@pytest.mark.parametrize(
    "text, mark",
    [
        ("\nno tests ran in 0.01s\n", "setup:tests:none"),
        ("============ no tests ran in 0.12s ============", "setup:tests:none"),
        ("No test files found, exiting with code 1", "setup:tests:none"),
        ("/usr/bin/bash: line 5: cd: web/app: No such file or directory", "setup:path:web/app"),
        ("Set-Location: Cannot find path 'D:\\work\\app' because it does not exist.", "setup:path:D:/work/app"),
    ],
)
def test_a_check_that_never_ran_says_why(text, mark):
    assert failure_marks(text) == frozenset({mark})


def test_finding_no_tests_is_said_as_what_happened():
    assert spoken("setup:tests:none") == "no tests were found to run"



# -- a runner nobody listed, and a second look from any runner -------------------


@pytest.mark.parametrize(
    "command,runner",
    [
        ("deno test", "deno"),
        ("swift test --parallel", "swift"),
        ("mix test test/app_test.exs", "mix"),
        ("just check", "just"),
        ("cd api && task lint", "task"),
    ],
)
def test_a_runner_called_by_its_verb_is_a_check_named_by_itself(command, runner):
    assert is_check_command(command)
    assert runner_of(command) == runner


@pytest.mark.parametrize("command", ["echo test", "cat check", "cd test", "git check-ignore x", "docker build ."])
def test_a_word_in_front_of_a_file_is_not_a_check(command):
    assert not is_check_command(command)


def test_the_listed_runners_keep_their_names():
    assert runner_of("npm test") == "package-script"
    assert runner_of("cargo test") == "cargo"
    assert runner_of("python -m pytest -q") == "pytest"


@pytest.mark.parametrize(
    "earlier,later,mark",
    [
        ("npx vitest run src/a.test.ts", "npx vitest run", "test:src/a.test.ts"),
        ("npx vitest run src/a.test.ts", "npx vitest run src", "test:src/a.test.ts"),
        ("cargo test parser::reads", "cargo test", "test:parser::reads"),
        ("go test ./pkg/... -run TestA", "go test ./...", "test:TestA"),
    ],
)
def test_a_whole_suite_run_of_the_same_runner_looks_again(earlier, later, mark):
    # The go case: `./...` names no test, so only a run with nothing after it covers TestA.
    expected = not later.startswith("go ")
    assert covers(later, earlier, mark) is expected


def test_a_narrowed_or_another_runner_does_not_look_again():
    assert not covers("npx vitest run -t other", "npx vitest run src/a.test.ts", "test:src/a.test.ts")
    assert not covers("npx jest", "npx vitest run src/a.test.ts", "test:src/a.test.ts")
    assert covers("go test", "go test ./pkg/... -run TestA", "test:TestA")
