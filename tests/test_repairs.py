"""A repair is witnessed, kept, and recalled when its failure is back.

No model writes any of this: the transcript proves the repair, the harness keeps it, and a
later failing check that names the same failure is told where the repair was."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from harness.loop import TurnEngine
from harness.tools.checks import MAX_MARKS, failure_marks
from harness.session.invariants import _empty_index, _index_events, audit_messages
from harness.context.memory import anchors
from harness.context.memory import repairs as repairs_module
from harness.context.memory.repairs import COSTLY_STEPS, SIDECAR_NAME, Repairs, lesson_request, note
from harness.safety.permissions import PermissionEngine
from harness.loop.repair_facts import covers, last_check, repair_of, what_it_checks
from harness.session.session_log import SessionLog
from harness.tools import ToolRegistry
from harness.tools.undo import UndoStore
from harness.session.turns import ATTACHED_BY

import scripts.harness_review as harness_review

TEST_A = "test:tests/test_x.py::test_a"
TEST_B = "test:tests/test_x.py::test_b"


# --- which failure a check named ---------------------------------------------------------


@pytest.mark.parametrize(
    "output, marks",
    [
        (
            "FAILED tests/test_x.py::test_a - AssertionError: assert 1 == 2\n"
            "FAILED tests\\test_x.py::TestC::test_d[one] - ValueError\n"
            "ERROR tests/test_z.py - ImportError: no module named q\n"
            "2 failed, 1 error in 0.12s",
            {TEST_A, "test:tests/test_x.py::TestC::test_d[one]", "test:tests/test_z.py"},
        ),
        ("src/a.ts(12,5): error TS2345: Argument of type 'x'\nFound 1 error.", {"type:src/a.ts:TS2345"}),
        ("pkg/a.py:12: error: Argument 1 has incompatible type  [arg-type]", {"type:pkg/a.py:arg-type"}),
        (
            "  d:\\ws\\pkg\\a.py:12:5 - error: Argument of type \"int\" (reportArgumentType)",
            {"type:d:/ws/pkg/a.py:reportArgumentType"},
        ),
        ("./pkg/a.py:12:5: F401 `os` imported but unused\nFound 1 error.", {"lint:pkg/a.py:F401"}),
        ("F401 [*] `os` imported but unused\n --> pkg\\a.py:1:8\n  |\nFound 1 error.", {"lint:pkg/a.py:F401"}),
        (" FAIL  src/a.test.ts > math > adds\nTests:  1 failed", {"test:src/a.test.ts"}),
        ("--- FAIL: TestParse (0.00s)\nFAIL", {"test:TestParse"}),
        ("test parse::tests::roundtrip ... FAILED", {"test:parse::tests::roundtrip"}),
    ],
)
def test_a_failure_is_named_the_way_its_runner_names_it(output, marks):
    assert failure_marks(output) == marks


def test_a_sentence_about_failing_is_not_a_failure_with_a_name():
    for text in ("ERROR in ./src", "FAILED to connect", "1 failed login attempt", "Error: something broke", ""):
        assert failure_marks(text) == frozenset(), text


def test_the_same_failure_on_another_line_is_the_same_failure():
    assert failure_marks("src/a.ts(12,5): error TS2345: x") == failure_marks("src/a.ts(90,1): error TS2345: y")
    assert failure_marks("pkg/a.py:1:1: F401 a") == failure_marks("pkg\\a.py:77:9: F401 b")


def test_a_suite_of_failures_is_capped():
    output = "\n".join(f"FAILED tests/test_x.py::test_{n} - boom" for n in range(MAX_MARKS + 25))
    assert len(failure_marks(output)) == MAX_MARKS


# --- a repair, read from the transcript --------------------------------------------------


class _Script:
    """A transcript, built call by call."""

    def __init__(self):
        self.messages = [{"role": "user", "content": "fix it"}]
        self._n = 0

    def _call(self, name, arguments, result):
        self._n += 1
        call = {"id": f"c{self._n}", "function": {"name": name, "arguments": json.dumps(arguments)}}
        self.messages += [
            {"role": "assistant", "content": "", "tool_calls": [call]},
            {"role": "tool", "tool_call_id": call["id"], "content": result, "ts": 1_800_000_000 + self._n},
        ]
        return self

    def check(self, failing=(), command="pytest -q tests/test_x.py", exit_code=None):
        lines = [f"FAILED tests/test_x.py::{name} - AssertionError" for name in failing]
        lines.append(f"{len(failing)} failed in 0.1s" if failing else "3 passed in 0.1s")
        code = (1 if failing else 0) if exit_code is None else exit_code
        return self._call("run_shell", {"command": command}, "\n".join(lines) + f"\n[exit {code}; cwd /ws]")

    def edit(self, path="src/a.py"):
        return self._call("write_file", {"path": path, "content": "x"}, "wrote it")

    def read(self, path="src/a.py"):
        return self._call("read_file", {"path": path}, "the file")


@pytest.fixture(autouse=True)
def _state_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("REEVE_STATE_DIR", str(tmp_path / "state"))


def test_a_failure_one_run_named_and_the_next_did_not_is_a_repair():
    script = _Script().check(["test_a", "test_b"]).read().edit("src/a.py").edit("src/b.py").edit("src/a.py").check(["test_b"])
    repair = repair_of(script.messages)
    assert repair.marks == {TEST_A}  # test_b is still failing: not repaired
    assert repair.files == ("src/a.py", "src/b.py")
    assert repair.steps == 5  # the read, the three edits, and the step that ran the check again
    assert repair.command == "pytest -q tests/test_x.py"

    script.edit("src/c.py").check([])
    done = repair_of(script.messages)
    # test_b has been open since the FIRST run named it: the cost and the files count from there.
    assert (done.marks, done.files, done.steps) == ({TEST_B}, ("src/a.py", "src/b.py", "src/c.py"), 7)


@pytest.mark.parametrize(
    "script",
    [
        _Script().check(["test_a"]).check([]),                               # passed with no edit: a flake
        _Script().check(["test_a"]).edit().check([], command="ruff check ."),  # another check passed
        _Script().check(["test_a"]).edit().check(["test_a"]),                # still failing
        _Script().check([]).edit().check([]),                                # was never red
        _Script().check(["test_a"]).edit().check([], exit_code=1),           # failed, naming nothing
        _Script().check(["test_a"]).edit(),                                  # not run again
        _Script().edit(),                                                    # no check at all
    ],
)
def test_what_is_not_proof_of_a_repair_is_not_one(script):
    assert repair_of(script.messages) is None


def test_an_edit_to_reeves_own_notes_repairs_nothing(tmp_path):
    notes = str(tmp_path / "state" / "projects" / "p" / "memory" / "note.md")
    assert repair_of(_Script().check(["test_a"]).edit(notes).check([]).messages) is None


def test_the_same_command_spaced_differently_is_the_same_check():
    script = _Script().check(["test_a"]).edit().check([], command="pytest  -q   tests/test_x.py")
    assert repair_of(script.messages).marks == {TEST_A}


def test_how_a_checks_output_is_shown_is_not_what_it_checks():
    plain = what_it_checks("pytest tests/test_x.py")
    for shown in (
        "pytest -q tests/test_x.py",
        "cd repo && pytest tests/test_x.py -v --tb=short",
        "pytest tests/test_x.py 2>&1 | tail -20",
        "pytest -p no:cacheprovider -rA tests/test_x.py 2>&1",
    ):
        assert what_it_checks(shown) == plain, shown
    assert what_it_checks("pytest tests/test_y.py") != plain
    assert what_it_checks("pytest -k slow tests/test_x.py") != plain  # what it selects is what it checks


@pytest.mark.parametrize(
    "later, looked",
    [
        ("pytest", True),                                  # the whole suite
        ("python -m pytest -q", True),
        ("pytest tests", True),                            # a folder above the test
        ("pytest tests/", True),
        ("pytest tests\\test_x.py", True),                 # its file, however the slashes lean
        ("pytest tests/test_x.py::test_a", True),          # the test itself
        ("pytest tests/test_y.py tests/test_x.py", True),
        ("pytest tests/test_y.py", False),                 # another file
        ("pytest tests/test_x.py::test_b", False),         # another test
        ("pytest test", False),                            # not a folder above it: a prefix of its name
        ("pytest -k test_b", False),                       # narrowed
        ("pytest --lf", False),
        ("pytest -x", False),                              # may stop before it gets there
        ("ruff check .", False),                           # another check altogether
        ("npm test", False),
    ],
)
def test_a_later_run_is_a_second_look_only_if_it_covered_the_failure(later, looked):
    assert covers(later, "pytest tests/test_x.py::test_a -q", TEST_A) is looked


def test_a_run_of_the_test_or_of_its_class_covers_it_after_a_wider_run_named_it():
    method = "test:tests/test_x.py::TestC::test_d"
    assert covers("pytest tests/test_x.py::test_a", "pytest", TEST_A) is True
    assert covers("pytest tests/test_x.py::TestC", "pytest", method) is True
    assert covers("pytest tests/test_x.py::TestC::test_d", "pytest", method) is True
    assert covers("pytest tests/test_x.py::TestOther", "pytest", method) is False
    assert covers("pytest tests/test_x.py::Test", "pytest", method) is False  # a prefix of the class's name


def test_only_a_test_can_be_covered_by_a_wider_run():
    lint = "lint:pkg/a.py:F401"
    assert covers("ruff check . --quiet", "ruff check .", lint) is True  # the same check
    assert covers("pytest", "ruff check .", lint) is False
    assert covers("ruff check pkg", "ruff check .", lint) is False  # no scope is read for a linter


def test_a_wider_run_that_no_longer_names_the_failure_proves_the_repair():
    script = _Script().check(["test_a"], command="pytest tests/test_x.py").edit().check([], command="python -m pytest -q")
    assert repair_of(script.messages).marks == {TEST_A}
    narrowed = _Script().check(["test_a"]).edit().check([], command="pytest -k test_b tests/test_x.py")
    assert repair_of(narrowed.messages) is None


def test_a_failure_that_went_away_with_no_edit_since_it_appeared_is_not_part_of_the_repair():
    script = (
        _Script().check(["test_a"]).edit("src/a.py").check(["test_a", "test_b"])  # test_b appears
        .check([])  # and both are gone, with no edit since test_b was first named
    )
    assert repair_of(script.messages).marks == {TEST_A}


def test_a_failure_that_comes_back_is_counted_from_when_it_came_back():
    script = _Script().check(["test_a"]).edit("src/a.py").check([]).edit("src/b.py").check(["test_a"]).edit("src/c.py").check([])
    repair = repair_of(script.messages)
    assert (repair.marks, repair.files, repair.steps) == ({TEST_A}, ("src/c.py",), 2)


def test_a_check_is_known_by_what_it_ran_and_printed_not_by_where_it_sits():
    script = _Script().check(["test_a"])
    first = last_check(script.messages)
    shifted = last_check([{"role": "user", "content": "a block that replaced older turns"}, *script.messages])
    assert (first.ok, first.marks) == (False, {TEST_A})
    assert shifted.index == first.index + 1 and shifted.key == first.key
    assert last_check(script.edit().check(["test_a", "test_b"]).messages).key != first.key
    # The same check printing the same thing later is another run of it.
    again = _Script().check(["test_a"]).check(["test_a"])
    assert last_check(again.messages).key != last_check(again.messages[:3]).key
    untimed = [{k: v for k, v in m.items() if k != "ts"} for m in again.messages]
    assert last_check(untimed).key == last_check(untimed[:3]).key  # with no time, only the text can tell
    assert last_check([{"role": "user", "content": "hi"}]) is None


# --- the project's repairs ---------------------------------------------------------------


class _Clock:
    def __init__(self):
        self.session, self.turn = "s1", 1

    def __call__(self):
        return self.session, self.turn


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "project"
    (root / "src").mkdir(parents=True)
    (root / "src" / "a.py").write_text("one\n", encoding="utf-8")
    (root / "src" / "b.py").write_text("one\n", encoding="utf-8")
    memory = tmp_path / "memory"
    memory.mkdir()

    def resolve(raw):
        path = root / raw
        return path if path.exists() else None

    clock = _Clock()
    return root, Repairs(memory, resolve=resolve, stamp=clock), clock


def test_a_repair_is_recalled_in_a_later_session_with_what_became_of_its_files(project):
    root, store, clock = project
    store.record({TEST_A, TEST_B}, "pytest -q", ["src/a.py", "src/b.py", "gone/never.py"], steps=7)
    clock.session = "s2"

    (found,) = store.recall({TEST_A, "test:tests/test_other.py::test_z"})
    assert found["shared"] == [TEST_A] and found["steps"] == 7
    assert (found["changed"], found["gone"]) == ([], [])  # nothing moved, and nothing is claimed

    (root / "src" / "a.py").write_text("two\n", encoding="utf-8")
    (root / "src" / "b.py").unlink()
    (found,) = store.recall({TEST_A})
    assert (found["changed"], found["gone"]) == (["src/a.py"], ["src/b.py"])
    # A file the edit named that the project never held has no digest: never called changed.
    assert store.recall({"test:tests/test_other.py::test_z"}) == []


def test_a_repair_is_not_recalled_to_the_turn_that_made_it(project):
    _, store, clock = project
    store.record({TEST_A}, "pytest -q", ["src/a.py"], steps=3)
    assert store.recall({TEST_A}) == []
    clock.turn = 2
    assert len(store.recall({TEST_A})) == 1  # a later turn of the same session may have lost it


def test_the_same_failures_repaired_again_in_one_turn_are_one_repair(project):
    _, store, clock = project
    store.record({TEST_A}, "pytest -q", ["src/a.py"], steps=3)
    store.record({TEST_A}, "pytest -q", ["src/a.py", "src/b.py"], steps=9)
    clock.turn = 2
    store.record({TEST_A}, "pytest -q", ["src/b.py"], steps=1)
    clock.session = "s2"
    assert [row["steps"] for row in store.recall({TEST_A})] == [1, 9]  # newest first
    kept = json.loads((store.root / SIDECAR_NAME).read_text(encoding="utf-8"))["repairs"]
    assert [row["steps"] for row in kept] == [9, 1]  # the turn's first record was replaced, not kept beside


def test_only_the_newest_repairs_are_told_and_kept(project, monkeypatch):
    _, store, clock = project
    monkeypatch.setattr(repairs_module, "MAX_REPAIRS", 4)
    for turn in range(1, 8):
        clock.turn = turn
        store.record({TEST_A}, "pytest -q", [], steps=turn)
    clock.session = "s2"
    assert [row["steps"] for row in store.recall({TEST_A})] == [7, 6]
    assert len(json.loads((store.root / SIDECAR_NAME).read_text(encoding="utf-8"))["repairs"]) == 4


def test_a_store_that_cannot_be_read_recalls_nothing_and_can_still_be_written(project):
    _, store, clock = project
    (store.root / SIDECAR_NAME).write_text("{not json", encoding="utf-8")
    assert store.recall({TEST_A}) == []
    store.record({TEST_A}, "pytest -q", [], steps=2)
    clock.session = "s2"
    assert len(store.recall({TEST_A})) == 1
    assert store.record(set(), "pytest -q", ["src/a.py"], steps=2) is None  # nothing named: nothing kept


def test_being_told_is_counted(project):
    _, store, clock = project
    store.record({TEST_A}, "pytest -q", [], steps=2)
    clock.session = "s2"
    store.told(store.recall({TEST_A}))
    store.told(store.recall({TEST_A}))
    assert store.recall({TEST_A})[0]["recalled"] == 2


def test_the_note_says_what_happened_and_not_what_to_do(project):
    root, store, clock = project
    store.record({TEST_A}, "pytest -q", ["src/a.py", "src/b.py"], steps=7)
    clock.session = "s2"
    (root / "src" / "a.py").write_text("two\n", encoding="utf-8")
    said = note(store.recall({TEST_A}))
    assert "`tests/test_x.py::test_a` stopped failing on 20" in said  # the name, not the filing kind
    assert "after edits to `src/a.py`, `src/b.py` (7 steps then)" in said
    assert "Changed since: `src/a.py`." in said and "No longer there" not in said
    assert said.endswith("not what is wrong now.")

    clock.session = "s3"
    store.record({TEST_B}, "pytest -q", [], steps=0)
    clock.session = "s4"
    assert "after an edit this record cannot name." in note(store.recall({TEST_B}))


def test_a_file_back_as_it_was_before_the_repair_is_said_as_that(project):
    root, store, clock = project
    before = {"src/a.py": anchors.digest(root / "src" / "a.py"), "src/new.py": repairs_module.ABSENT}
    (root / "src" / "a.py").write_text("repaired\n", encoding="utf-8")
    (root / "src" / "new.py").write_text("made by the repair\n", encoding="utf-8")
    (root / "src" / "b.py").write_text("repaired\n", encoding="utf-8")
    store.record({TEST_A}, "pytest -q", ["src/a.py", "src/new.py", "src/b.py"], steps=7, before=before)
    clock.session = "s2"

    (found,) = store.recall({TEST_A})
    assert (found["undone"], found["changed"], found["gone"]) == ([], [], [])  # as the repair left them

    # The repair is undone: a.py is as it was, the file it made is gone, and b.py, whose before
    # is not known, changed some other way.
    (root / "src" / "a.py").write_text("one\n", encoding="utf-8")
    (root / "src" / "new.py").unlink()
    (root / "src" / "b.py").write_text("three\n", encoding="utf-8")
    (found,) = store.recall({TEST_A})
    assert (found["undone"], found["changed"], found["gone"]) == (["src/a.py", "src/new.py"], ["src/b.py"], [])
    said = note([found])
    assert "Back as before that repair: `src/a.py`, `src/new.py`. Changed since: `src/b.py`." in said

    # Changed again, now neither as the repair left it nor as before: that is "changed".
    (root / "src" / "a.py").write_text("four\n", encoding="utf-8")
    (found,) = store.recall({TEST_A})
    assert found["undone"] == ["src/new.py"] and found["changed"] == ["src/a.py", "src/b.py"]


def test_what_a_repair_changed_is_a_short_diff_with_credentials_masked():
    before = b"import os\n\nRATES = {\n    'EU-North': 0.25,\n    'eu-south': 0.21,\n}\n"
    after = b"import os\n\nRATES = {\n    'eu-north': 0.25,\n    'eu-south': 0.21,\n}\n"
    change = repairs_module.change_between(before, after)
    assert change.splitlines() == [
        "@@ -3,3 +3,3 @@", " RATES = {", "-    'EU-North': 0.25,", "+    'eu-north': 0.25,", "     'eu-south': 0.21,",
    ]
    assert repairs_module.change_between(None, b"x = 1\n") == "@@ -0,0 +1 @@\n+x = 1"  # a file it made
    assert repairs_module.change_between(before, before) == ""
    assert repairs_module.change_between(b"\x00\x01", b"\x00\x02") == ""  # not text
    many = "".join(f"line {n}\n" for n in range(40)).encode()
    assert repairs_module.change_between(b"", many) == ""  # a rewrite is not one repair's point
    shaped = "sk-" + "abcdefghijklmnopqrstuvwxyz123456"
    keyed = repairs_module.change_between(b"KEY = ''\n", f"api_key = '{shaped}'\n".encode())
    assert shaped not in keyed and "[REDACTED]" in keyed


def test_the_note_shows_what_the_repair_changed_where_it_no_longer_holds(project):
    root, store, clock = project
    before = {"src/a.py": anchors.digest(root / "src" / "a.py")}
    (root / "src" / "a.py").write_text("repaired\n", encoding="utf-8")
    changes = {"src/a.py": "@@ -1 +1 @@\n-one\n+repaired", "src/b.py": "@@ -1 +1 @@\n-zero\n+one"}
    store.record({TEST_A}, "pytest -q", ["src/a.py", "src/b.py"], steps=7, before=before, changes=changes)
    clock.session = "s2"

    said = note(store.recall({TEST_A}))
    assert "```diff" not in said  # both files as the repair left them: no change is why it is back

    (root / "src" / "a.py").write_text("one\n", encoding="utf-8")
    said = note(store.recall({TEST_A}))
    assert "Back as before that repair: `src/a.py`." in said
    assert "What that repair changed in `src/a.py`:\n```diff\n@@ -1 +1 @@\n-one\n+repaired\n```" in said
    assert "src/b.py`:\n```diff" not in said  # b.py still holds the repair
    assert said.endswith("not what is wrong now.")


def test_a_note_shows_changes_only_within_its_room(project, monkeypatch):
    root, store, clock = project
    monkeypatch.setattr(repairs_module, "MAX_SHOWN_CHANGE_CHARS", 30)
    (root / "src" / "a.py").write_text("x\n", encoding="utf-8")
    (root / "src" / "b.py").write_text("x\n", encoding="utf-8")
    store.record({TEST_A}, "pytest -q", ["src/a.py", "src/b.py"], steps=2,
                 changes={"src/a.py": "@@ -1 +1 @@\n-y\n+x", "src/b.py": "@@ -1 +1 @@\n-y\n+x"})
    clock.session = "s2"
    for name in ("a.py", "b.py"):
        (root / "src" / name).write_text("y\n", encoding="utf-8")
    assert note(store.recall({TEST_A})).count("```diff") == 1


def test_a_before_is_kept_only_for_a_file_the_record_can_find(project):
    root, store, _ = project
    entry = store.record({TEST_A}, "pytest -q", ["src/a.py", "gone/never.py"], steps=2,
                         before={"src/a.py": "d1", "gone/never.py": "d2"})
    assert [item.get("before") for item in entry["files"]] == ["d1", None]


# --- the engine: record at the boundary, recall at the boundary ---------------------------


def _engine(tmp_path, clock=None, resolve=None):
    engine = TurnEngine(
        provider=object(),
        registry=ToolRegistry(),
        permissions=PermissionEngine(workspace_root=tmp_path),
        model="m",
        messages=[{"role": "user", "content": "fix it"}],
    )
    engine.log = SessionLog(tmp_path / f"{id(engine)}.events.jsonl")
    (tmp_path / "memory").mkdir(exist_ok=True)
    engine.repairs = Repairs(tmp_path / "memory", resolve=resolve, stamp=clock or (lambda: ("s", engine._turn_no)))
    engine._turn_no = 1
    return engine


def _rows(engine, kind):
    return [{k: v for k, v in e.items() if k not in ("seq", "t", "type", "trace")} for e in engine.log.read() if e.get("type") == kind]


def _notes(engine, tag="repair_recall"):
    return [m["content"] for m in engine.messages if m.get(ATTACHED_BY) == tag]


def test_the_engine_writes_a_repair_down_when_the_check_proves_it(tmp_path):
    engine = _engine(tmp_path)
    engine._track_repairs()  # the turn's first boundary: nothing has run yet
    script = _Script().check(["test_a"])
    engine.messages = script.messages
    engine._track_repairs()
    assert _rows(engine, "repair/fixed") == []  # red is not a repair

    script.edit("src/a.py")
    engine._track_repairs()
    script.check([])
    engine._track_repairs()
    engine._track_repairs()  # the same check, looked at again: not a second repair
    assert _rows(engine, "repair/fixed") == [
        {"turn": 1, "step": engine._step_no, "marks": 1, "files": 1, "before": 0, "diffs": 0, "steps": 2}  # no undo store
    ]
    kept = json.loads((tmp_path / "memory" / SIDECAR_NAME).read_text(encoding="utf-8"))["repairs"]
    assert [(row["marks"], [f["path"] for f in row["files"]]) for row in kept] == [([TEST_A], ["src/a.py"])]


def test_the_engine_keeps_what_each_file_was_before_the_repair_from_the_undo_store(tmp_path):
    root = tmp_path / "ws"
    (root / "src").mkdir(parents=True)
    a = root / "src" / "a.py"
    a.write_text("broken\n", encoding="utf-8")
    broken = anchors.digest(a)
    engine = _engine(tmp_path, resolve=lambda raw: root / raw)
    engine.undo = UndoStore(tmp_path / "undo", base=root)
    engine._track_repairs()
    script = _Script().check(["test_a"])
    engine.messages = script.messages
    engine._track_repairs()

    # Two edits of the file, each with the before-image the pre-execute listener takes.
    engine.undo.snapshot("c2", ["src/a.py"])
    script.edit("src/a.py")
    a.write_text("half fixed\n", encoding="utf-8")
    engine.undo.snapshot("c3", ["src/a.py"])
    script.edit("src/a.py")
    a.write_text("fixed\n", encoding="utf-8")
    script.check([])
    engine._track_repairs()

    assert (_rows(engine, "repair/fixed")[0]["before"], _rows(engine, "repair/fixed")[0]["diffs"]) == (1, 1)
    (kept,) = json.loads((tmp_path / "memory" / SIDECAR_NAME).read_text(encoding="utf-8"))["repairs"]
    assert kept["files"][0]["before"] == broken  # the first edit's image, not the second's
    # The whole repair, from what it started from to what the green check ran over.
    assert kept["files"][0]["change"] == "@@ -1 +1 @@\n-broken\n+fixed"

    a.write_text("broken\n", encoding="utf-8")  # the repair is undone
    later = Repairs(tmp_path / "memory", resolve=lambda raw: root / raw, stamp=lambda: ("s2", 1))
    assert later.recall({TEST_A})[0]["undone"] == ["src/a.py"]


def test_each_file_takes_its_own_image_and_a_file_the_repair_made_was_absent(tmp_path):
    root = tmp_path / "ws"
    (root / "src").mkdir(parents=True)
    (root / "src" / "a.py").write_text("a before\n", encoding="utf-8")
    (root / "src" / "b.py").write_text("b before\n", encoding="utf-8")
    a_before = anchors.digest(root / "src" / "a.py")
    engine = _engine(tmp_path, resolve=lambda raw: root / raw)
    engine.undo = UndoStore(tmp_path / "undo", base=root)
    engine._track_repairs()
    script = _Script().check(["test_a"])
    engine.messages = script.messages
    engine._track_repairs()

    engine.undo.snapshot("c2", ["src/a.py", "src/b.py"])  # one call's images hold two files
    script.edit("src/a.py")
    engine.undo.snapshot("c3", ["src/new.py"])  # this edit creates the file
    script.edit("src/new.py")
    (root / "src" / "a.py").write_text("a fixed\n", encoding="utf-8")
    (root / "src" / "new.py").write_text("made\n", encoding="utf-8")
    script.check([])
    engine._track_repairs()

    (kept,) = json.loads((tmp_path / "memory" / SIDECAR_NAME).read_text(encoding="utf-8"))["repairs"]
    assert {item["path"]: item.get("before") for item in kept["files"]} == {
        "src/a.py": a_before, "src/new.py": repairs_module.ABSENT
    }


def test_an_image_whose_copy_is_gone_is_no_image(tmp_path):
    (tmp_path / "a.py").write_text("x\n", encoding="utf-8")
    undo = UndoStore(tmp_path / "undo", base=tmp_path)
    (shot,) = undo.snapshot("c1", ["a.py", "made.py"])[:1]
    assert [s.existed for s in undo.images("c1")] == [True, False]
    Path(shot.saved_to).unlink()  # pruned, or cleaned by hand
    assert [s.existed for s in undo.images("c1")] == [False]  # a file that did not exist needs no copy
    assert undo.images("never") == []


def test_a_file_the_repair_deleted_is_back_as_before_when_it_returns(tmp_path):
    root = tmp_path / "ws"
    (root / "src").mkdir(parents=True)
    old = root / "src" / "old.py"
    old.write_text("the cause\n", encoding="utf-8")
    (tmp_path / "memory").mkdir()
    clock = _Clock()
    store = Repairs(tmp_path / "memory", resolve=lambda raw: root / raw, stamp=clock)
    before = anchors.digest(old)
    old.unlink()  # the repair deleted it
    store.record({TEST_A}, "pytest -q", ["src/old.py"], steps=3, before={"src/old.py": before})
    clock.session = "s2"
    assert store.recall({TEST_A})[0]["undone"] == []  # still gone: as the repair left it
    old.write_text("the cause\n", encoding="utf-8")
    assert store.recall({TEST_A})[0]["undone"] == ["src/old.py"]


def test_a_repair_names_the_call_that_first_wrote_each_file():
    script = _Script().check(["test_a"]).edit("src/a.py").edit("src/b.py").edit("src/a.py").check([])
    assert repair_of(script.messages).first_edits == (("src/a.py", "c2"), ("src/b.py", "c3"))


def test_a_failure_this_project_repaired_is_recalled_once_a_turn_and_its_second_cost_is_logged(tmp_path):
    clock = _Clock()
    Repairs(tmp_path / "memory" if (tmp_path / "memory").mkdir() is None else None, stamp=clock).record(
        {TEST_A}, "pytest -q", ["src/a.py"], steps=9
    )
    engine = _engine(tmp_path)  # another session of the same project
    engine._track_repairs()
    script = _Script().check(["test_a", "test_b"])
    engine.messages = script.messages
    engine._track_repairs()

    (said,) = _notes(engine)
    assert "`tests/test_x.py::test_a` stopped failing" in said and "`src/a.py`" in said and "test_b" not in said
    assert engine.messages[-1][ATTACHED_BY] == "repair_recall" and engine.messages[-1]["role"] == "user"
    assert _rows(engine, "repair/recalled") == [
        {"turn": 1, "step": engine._step_no, "marks": 1, "repairs": 1, "undone": 0, "diffs": 0, "changed": 0}
    ]
    # The request that carries the note is one the log can account for.
    index = _empty_index()
    _index_events(index, [{"type": "request/header"}, *engine.log.read(), *({"type": "tool/result", "call_id": f"c{n}"} for n in range(1, 9))])
    assert not [v for v in audit_messages([{"role": "system"}, *engine.messages], index) if "repair_recall" in v]

    script.messages = engine.messages
    script.check(["test_a", "test_b"], command="pytest -q")  # failing again in the same turn
    engine._track_repairs()
    assert len(_notes(engine)) == 1  # told once

    script.edit("src/a.py").edit("src/a.py").check(["test_b"], command="pytest -q")
    engine._track_repairs()
    assert _rows(engine, "repair/fixed") == [
        {"turn": 1, "step": engine._step_no, "marks": 1, "files": 1, "before": 0, "diffs": 0, "steps": 4, "recalled": True,
         "earlier_steps": 9}
    ]
    assert json.loads((tmp_path / "memory" / SIDECAR_NAME).read_text(encoding="utf-8"))["repairs"][0]["recalled"] == 1


def test_a_new_turn_may_be_told_again_and_the_switch_silences_it(tmp_path):
    clock = _Clock()
    (tmp_path / "memory").mkdir()
    Repairs(tmp_path / "memory", stamp=clock).record({TEST_A}, "pytest -q", [], steps=4)
    engine = _engine(tmp_path)
    engine._track_repairs()
    script = _Script().check(["test_a"])
    engine.messages = script.messages
    engine._track_repairs()
    assert len(_notes(engine)) == 1

    engine._begin_loop()  # the next turn starts
    engine._turn_no = 2
    script.messages = engine.messages
    script.check(["test_a"], command="pytest -q tests")
    engine._track_repairs()
    assert len(_notes(engine)) == 2

    quiet = _engine(tmp_path)
    quiet.ablated = frozenset({"repair_recall"})
    quiet._track_repairs()
    quiet.messages = _Script().check(["test_a"]).messages
    quiet._track_repairs()
    assert _notes(quiet) == [] and _rows(quiet, "repair/recalled") == []


def test_a_resumed_session_does_not_answer_for_a_check_it_was_built_holding(tmp_path):
    clock = _Clock()
    (tmp_path / "memory").mkdir()
    Repairs(tmp_path / "memory", stamp=clock).record({TEST_A}, "pytest -q", [], steps=4)
    engine = _engine(tmp_path)
    engine.messages = _Script().check(["test_b"]).edit().check([]).messages  # restored from disk
    engine._track_repairs()
    assert _rows(engine, "repair/fixed") == []  # the process that ran it wrote it down

    script = _Script()
    script.messages = engine.messages
    script.check(["test_a"])
    engine._track_repairs()
    assert len(_notes(engine)) == 1  # what lands from here on is this engine's


def test_a_session_with_no_store_and_a_store_that_breaks_cost_nothing(tmp_path):
    engine = _engine(tmp_path)
    engine.repairs = None
    engine.messages = _Script().check(["test_a"]).edit().check([]).messages
    engine._track_repairs()

    class Broken:
        def record(self, *a, **k):
            raise OSError("disk full")

        recall = told = record

    engine.repairs = Broken()
    engine._repair_primed = True
    engine._repair_seen = None
    engine._track_repairs()  # raises nothing
    assert _rows(engine, "repair/fixed") == []


def test_the_review_counts_repairs_apart_from_failure_shapes(tmp_path):
    folder = tmp_path / "conversations"
    folder.mkdir()
    events = [
        {"type": "turn/start", "turn": 1},
        {"type": "repair/fixed", "turn": 1, "step": 3, "marks": 1, "files": 1, "steps": 6},
        {"type": "repair/recalled", "turn": 1, "step": 5, "marks": 1, "repairs": 1, "changed": 0},
        {"type": "repair/fixed", "turn": 1, "step": 7, "marks": 1, "files": 1, "steps": 2, "recalled": True, "earlier_steps": 6},
        {"type": "turn/end", "turn": 1, "reason": "completed"},
    ]
    (folder / "s.events.jsonl").write_text(
        "\n".join(json.dumps({"seq": i, "t": 1_800_000_000 + i, **e}) for i, e in enumerate(events)), encoding="utf-8"
    )
    counts, _ = harness_review.review(harness_review._logs([str(tmp_path)]))
    assert counts["repairs recorded"] == 2 and counts["repairs recalled"] == 1
    assert counts["repairs made after a recall"] == 1
    assert (counts["steps of repairs made after a recall"], counts["steps those repairs took the first time"]) == (2, 6)
    assert not any(name.startswith("repair") for name in harness_review.DETECTORS)


# --- a lesson: asked for at a costly repair, recalled with it ------------------------------


def _lesson(store, root, name, *, session, turn, files=("src/a.py",), kind="lesson", description="The cache key must include the locale."):
    """A memory file as the door would have left it: the file, and what the harness recorded."""
    (store.root / name).write_text(
        f"---\nname: {name[:-3]}\ndescription: {description}\nmetadata:\n  type: {kind}\n---\nWhat was wrong, what fixed it.\n",
        encoding="utf-8",
    )
    anchors.record(
        store.root, name, kind=kind, anchors=[], named=[(raw, root / raw) for raw in files], session=session, turn=turn
    )


def test_a_lesson_saved_in_the_turn_of_a_repair_is_recalled_with_it(project):
    root, store, clock = project
    store.record({TEST_A}, "pytest -q", ["src/a.py"], steps=12)
    _lesson(store, root, "lesson_cache_key.md", session="s1", turn=1)
    _lesson(store, root, "lesson_other_turn.md", session="s1", turn=2)             # another turn
    _lesson(store, root, "lesson_other_file.md", session="s1", turn=1, files=("src/b.py",))  # not about what was edited
    _lesson(store, root, "feedback_style.md", session="s1", turn=1, kind="feedback")         # not a lesson
    clock.session = "s2"

    (found,) = store.recall({TEST_A})
    assert found["lessons"] == [{"name": "lesson_cache_key.md", "description": "The cache key must include the locale."}]
    said = note([found])
    assert "A note was saved with that repair: `lesson_cache_key.md` (The cache key must include the locale.)." in said
    assert store.has_lesson({TEST_A}) is True and store.has_lesson({TEST_B}) is False

    (store.root / "lesson_cache_key.md").unlink()  # the person deleted it
    assert store.recall({TEST_A})[0]["lessons"] == [] and store.has_lesson({TEST_A}) is False
    assert "A note was saved" not in note(store.recall({TEST_A}))


def test_a_repair_that_named_no_file_takes_any_lesson_of_its_turn_and_a_long_description_is_cut(project):
    root, store, clock = project
    store.record({TEST_A}, "pytest -q", [], steps=12)
    _lesson(store, root, "lesson_long.md", session="s1", turn=1, files=("src/b.py",), description="x " * 400)
    clock.session = "s2"
    (lesson,) = store.recall({TEST_A})[0]["lessons"]
    assert lesson["name"] == "lesson_long.md" and len(lesson["description"]) <= 160


def test_the_request_names_the_failure_and_its_cost_and_leaves_nothing_open():
    said = lesson_request({TEST_A, TEST_B}, 14)
    assert "`tests/test_x.py::test_a`, `tests/test_x.py::test_b` took 14 steps to repair" in said
    assert "save it now as a `lesson` memory" in said and "save nothing and go on" in said


def _costly(script, steps=COSTLY_STEPS):
    """A red check, then enough steps to make the repair a costly one, then green."""
    script.check(["test_a"])
    for _ in range(steps - 2):
        script.read()
    return script.edit("src/a.py").check([])


def test_a_costly_repair_is_asked_about_once_a_turn_and_a_cheap_one_is_not(tmp_path):
    engine = _engine(tmp_path)
    engine._track_repairs()
    script = _Script()
    engine.messages = script.messages
    _costly(script, COSTLY_STEPS - 1)
    engine._track_repairs()
    assert _notes(engine, "repair_lesson") == []  # one step short of costly
    assert "asked" not in _rows(engine, "repair/fixed")[0]

    script.check(["test_b"])
    engine._track_repairs()
    for _ in range(COSTLY_STEPS):
        script.read()
    script.edit("src/b.py").check([])
    engine._track_repairs()
    (said,) = _notes(engine, "repair_lesson")
    assert "`tests/test_x.py::test_b` took" in said and engine.messages[-1][ATTACHED_BY] == "repair_lesson"
    assert _rows(engine, "repair/fixed")[-1]["asked"] is True
    index = _empty_index()
    _index_events(index, [{"type": "request/header"}, *engine.log.read(), *({"type": "tool/result", "call_id": f"c{n}"} for n in range(1, 40))])
    assert not [v for v in audit_messages([{"role": "system"}, *engine.messages], index) if "repair_lesson" in v]

    script.messages = engine.messages
    _costly(script)  # test_a again, costly, in the same turn
    engine._track_repairs()
    assert len(_notes(engine, "repair_lesson")) == 1  # asked once a turn
    engine._begin_loop()
    script.messages = engine.messages
    _costly(script)
    engine._track_repairs()
    assert len(_notes(engine, "repair_lesson")) == 2  # a new turn may be asked


def test_no_lesson_is_asked_for_where_memory_is_off_or_one_is_already_saved(tmp_path):
    engine = _engine(tmp_path)
    engine.repairs = Repairs(tmp_path / "memory", stamp=lambda: ("s", engine._turn_no), asks=lambda: False)
    engine._track_repairs()
    engine.messages = _costly(_Script()).messages
    engine._track_repairs()
    assert _notes(engine, "repair_lesson") == [] and "asked" not in _rows(engine, "repair/fixed")[0]

    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("x", encoding="utf-8")
    clock = _Clock()
    earlier = Repairs(tmp_path / "memory", resolve=lambda raw: tmp_path / raw, stamp=clock)
    earlier.record({TEST_A}, "pytest -q", ["src/a.py"], steps=20)
    _lesson(earlier, tmp_path, "lesson_a.md", session="s1", turn=1)
    again = _engine(tmp_path, resolve=lambda raw: tmp_path / raw)
    again._track_repairs()
    again.messages = _costly(_Script()).messages
    again._track_repairs()
    assert _notes(again, "repair_lesson") == []  # the cause is already written down


def test_the_review_counts_what_was_asked_and_what_was_saved_after(tmp_path):
    folder = tmp_path / "conversations"
    folder.mkdir()
    memory = "C:/state/projects/p/memory/lesson_a.md"
    events = [
        {"type": "turn/start", "turn": 1},
        {"type": "repair/fixed", "turn": 1, "step": 9, "marks": 1, "files": 1, "steps": 9, "asked": True},
        {"type": "tool/call", "turn": 1, "step": 10, "call_id": "w1", "name": "write_file", "arguments": {"path": memory, "content": "x"}},
        {"type": "tool/result", "turn": 1, "step": 10, "call_id": "w1", "content": "wrote it", "is_error": False},
        {"type": "tool/call", "turn": 1, "step": 11, "call_id": "w2", "name": "write_file", "arguments": {"path": memory, "content": "y"}},
        {"type": "tool/result", "turn": 1, "step": 11, "call_id": "w2", "content": "wrote it", "is_error": False},
        {"type": "turn/end", "turn": 1, "reason": "completed"},
        {"type": "turn/start", "turn": 2},
        {"type": "repair/fixed", "turn": 2, "step": 3, "marks": 1, "files": 1, "steps": 12, "asked": True},
        {"type": "turn/end", "turn": 2, "reason": "completed"},
        {"type": "turn/start", "turn": 3},
        {"type": "tool/call", "turn": 3, "step": 1, "call_id": "w3", "name": "write_file", "arguments": {"path": memory, "content": "z"}},
        {"type": "tool/result", "turn": 3, "step": 1, "call_id": "w3", "content": "wrote it", "is_error": False},
    ]
    (folder / "s.events.jsonl").write_text(
        "\n".join(json.dumps({"seq": i, "t": 1_800_000_000 + i, **e}) for i, e in enumerate(events)), encoding="utf-8"
    )
    counts, _ = harness_review.review(harness_review._logs([str(tmp_path)]))
    assert counts["repairs asked for a lesson"] == 2
    # One asked repair was followed by a memory write in its turn (counted once); the other
    # was not, and a write in a later turn answers nothing.
    assert counts["memory writes after a lesson was asked for"] == 1
