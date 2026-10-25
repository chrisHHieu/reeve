"""The recurrence eval's instrument, offline: one session repairs a failure, the
bug comes back, and three later sessions get exactly what their name says of the first one's
memory. Scripted providers; the product's engine, the real shell, the real pytest."""

from __future__ import annotations

import asyncio
import os
import sys

import pytest

from benchmarks import recur_eval, runs
from benchmarks.probe_eval import load_probes
from benchmarks.probe_seen_before import CATALOGUE, CAUSE, FAILING
from harness.context.memory.repairs import SIDECAR_NAME
from harness.llm.providers import AssistantTurn, ModelCapabilities, ProviderClient, ToolCall

LESSON = (
    "---\nname: lesson-table-keys\ndescription: table keys must be lower case, lookups are lower-cased\n"
    "metadata:\n  type: lesson\n---\n`tests/test_report.py` failed with `assert 100.0 == 125.0`. "
    f"`{CAUSE}` spelled a region in mixed case and `shop/data/store.py` looks keys up lower-cased.\n"
)


def _call(name, **arguments):
    return AssistantTurn(
        text="", tool_calls=[ToolCall(id=f"c-{name}", name=name, arguments=arguments)], finish_reason="tool_calls"
    )


def _pytest_here():
    quoted = f'"{sys.executable}"'
    return f"{'& ' if os.name == 'nt' else ''}{quoted} -m pytest -q -p no:cacheprovider"


class _Story(ProviderClient):
    """A scripted provider that keeps what it was sent. A turn may be a callable, made into a
    turn when its moment comes (the lesson's path exists only once the engine is built)."""

    def __init__(self, turns, look=None):
        self._turns = list(turns)
        self.requests = []
        self.looked = None
        self._look = look

    def complete(self, *, model, messages, tools=None, **settings):
        if self._look and self.looked is None:
            self.looked = self._look()  # what the workspace held when the session began
        self.requests.append([dict(message) for message in messages])
        if not self._turns:
            return AssistantTurn(text="Done.", finish_reason="stop")
        turn = self._turns.pop(0)
        return turn() if callable(turn) else turn

    def capabilities(self, model):
        return ModelCapabilities()


def _repairs_it(probe, out, lesson):
    """The first session: a long look, the fix, the suite green, and maybe the lesson."""
    fixed = probe["setup"][CAUSE].replace("'EU-North'", "'eu-north'")
    turns = [_call("run_shell", command=_pytest_here()), _call("read_file", path=CAUSE)]
    turns += [_call("read_file", path="shop/data/store.py") for _ in range(6)]
    turns += [_call("write_file", path="scratch.txt", content="what the first session left lying about\n")]
    turns += [_call("write_file", path=CAUSE, content=fixed), _call("run_shell", command=_pytest_here())]
    if lesson:
        memory = lambda: recur_eval.memory_of(out / "trial-0" / "first")  # noqa: E731
        turns.append(lambda: _call("write_file", path=str(memory() / "lesson_table_keys.md"), content=LESSON))
    return _Story(turns)


def _trial(tmp_path, monkeypatch, *, repairs=True, lesson=True, first_passes=None):
    monkeypatch.setenv("REEVE_STATE_DIR", str(tmp_path / "ledger"))
    (probe,) = load_probes(["seen-before"])
    out = tmp_path / "out"
    providers = {}
    if first_passes is not None:  # the grade of the first session, whatever it did
        graded = recur_eval.grade
        monkeypatch.setattr(
            recur_eval, "grade",
            lambda p, ws, answer: {**graded(p, ws, answer), "passed": first_passes},
        )

    def provider_for(tag):
        if tag == "first-0":
            providers[tag] = _repairs_it(probe, out, lesson) if repairs else _Story([])
        else:
            leftover = out / "trial-0" / "ws" / "scratch.txt"
            providers[tag] = _Story([_call("run_shell", command=_pytest_here())], look=leftover.exists)
        return providers[tag]

    rows = asyncio.run(asyncio.wait_for(recur_eval.trial("fake:model", probe, out, 0, provider_for), timeout=600))
    return out, rows, providers


def _told(provider):
    return [str(m.get("content")) for m in provider.requests[-1] if "repaired this failure before" in str(m.get("content"))]


def test_a_trial_gives_each_later_session_what_its_name_says(tmp_path, monkeypatch):
    out, rows, providers = _trial(tmp_path, monkeypatch)
    assert [(row["variant"], row["outcome"]) for row in rows] == [
        ("first", "passed"), ("none", "failed"), ("recall", "failed"), ("lesson", "failed"),
    ]
    first = rows[0]
    assert first["fired"] == {"fixed": 1, "recalled": 0, "asked": 1} and first["suite"] == "recur"
    # The failure was gone at the step that ran the suite green (the eleventh call of the
    # story); the lesson and the closing answer came after it.
    assert (first["to_fix"], first["steps"]) == (11, 13)
    assert [row["to_fix"] for row in rows[1:]] == [row["steps"] for row in rows[1:]]  # never repaired: all of it
    assert rows[3]["flags"] == ["lessons=1"]

    # The bug is back for every later session, and each starts with no history of the first.
    for variant in recur_eval.LATER:
        assert not (out / "trial-0" / variant / "conversations" / "first-0.events.jsonl").exists()
        assert "EU-North" in str(providers[f"{variant}-0"].requests[-1])  # the failing run's own output
        assert providers[f"{variant}-0"].looked is False  # nothing the first session left in the folder

    none, recall, lesson = (recur_eval.memory_of(out / "trial-0" / v) for v in recur_eval.LATER)
    assert not (none / SIDECAR_NAME).exists() and not list(none.glob("lesson_*.md"))
    assert (recall / SIDECAR_NAME).is_file() and not list(recall.glob("lesson_*.md"))
    assert (lesson / SIDECAR_NAME).is_file() and (lesson / "lesson_table_keys.md").is_file()

    assert _told(providers["none-0"]) == [] and rows[1]["fired"]["recalled"] == 0
    assert rows[2]["fired"]["recalled"] == 1 and rows[3]["fired"]["recalled"] == 1
    (said,) = _told(providers["recall-0"])
    # The bug is back because the repair was undone, and the note says that, not "changed".
    assert f"`{FAILING}` stopped failing" in said and f"Back as before that repair: `{CAUSE}`." in said
    assert "Changed since" not in said
    assert "+    'eu-north': 0.25," in said  # and what that repair changed
    assert "A note was saved" not in said
    (said,) = _told(providers["lesson-0"])
    assert "A note was saved with that repair: `lesson_table_keys.md` (table keys must be lower case" in said

    assert [row["variant"] for row in runs.load() if row["suite"] == "recur"] == ["first", "none", "recall", "lesson"]
    summary = "\n".join(recur_eval.summarize(rows))
    assert "none -> recall: what the harness's record is worth" in summary
    assert "recall -> lesson: what the lesson adds to it" in summary
    assert "steps: no reading (a side ran a task once)" in summary  # one trial is not a reading


def test_a_first_session_that_wrote_no_lesson_is_not_followed_by_a_lesson_session(tmp_path, monkeypatch, capsys):
    out, rows, _ = _trial(tmp_path, monkeypatch, lesson=False)
    assert [row["variant"] for row in rows] == ["first", "none", "recall"]
    assert rows[0]["fired"]["asked"] == 1  # it was asked, and wrote nothing
    assert "wrote no lesson" in capsys.readouterr().out
    assert recur_eval.lessons_in(recur_eval.memory_of(out / "trial-0" / "first")) == []


def test_a_first_session_that_repaired_nothing_has_nothing_to_carry(tmp_path, monkeypatch, capsys):
    out, rows, _ = _trial(tmp_path, monkeypatch, repairs=False)
    assert [(row["variant"], row["outcome"]) for row in rows] == [("first", "failed")]
    assert "left no repair to carry" in capsys.readouterr().out
    assert not (out / "trial-0" / "none").exists()


@pytest.mark.parametrize(
    "repairs, first_passes",
    [
        (True, False),  # a repair is on record, but the task was not done: nothing worth carrying
        (False, True),  # graded done, but no repair was witnessed: nothing to carry
    ],
)
def test_a_trial_carries_only_a_repair_from_a_first_session_that_passed(tmp_path, monkeypatch, capsys, repairs, first_passes):
    out, rows, _ = _trial(tmp_path, monkeypatch, repairs=repairs, first_passes=first_passes)
    assert [row["variant"] for row in rows] == ["first"]
    assert "left no repair to carry" in capsys.readouterr().out


def _elsewhere(tmp_path, monkeypatch, *, touch_catalogue=False):
    """A trial of the task whose later sessions meet another cause: the first session repairs
    the table (and may also have edited the catalogue), each later one repairs the catalogue."""
    monkeypatch.setenv("REEVE_STATE_DIR", str(tmp_path / "ledger"))
    (probe,) = recur_eval.load_tasks(["seen-before-new-cause"])
    out = tmp_path / "out"
    ws = out / "trial-0" / "ws"
    providers = {}

    def looked():  # the project a later session starts from
        return {
            "table repaired": "'eu-north'" in (ws / CAUSE).read_text(encoding="utf-8"),
            "catalogue": probe["later"][CATALOGUE] == (ws / CATALOGUE).read_text(encoding="utf-8"),
            "hidden tests": (ws / "tests" / "test_report_regions.py").exists(),
            "what the first left": (ws / "scratch.txt").exists(),
        }

    def provider_for(tag):
        if tag == "first-0":
            story = _repairs_it(probe, out, lesson=True)
            if touch_catalogue:
                body = probe["setup"][CATALOGUE] + "# checked\n"
                story._turns[1:1] = [_call("read_file", path=CATALOGUE), _call("write_file", path=CATALOGUE, content=body)]
            providers[tag] = story
        else:
            providers[tag] = _Story(
                [
                    _call("run_shell", command=_pytest_here()),
                    _call("read_file", path=CATALOGUE),
                    _call("write_file", path=CATALOGUE, content=probe["setup"][CATALOGUE]),
                    _call("run_shell", command=_pytest_here()),
                ],
                look=looked,
            )
        return providers[tag]

    rows = asyncio.run(asyncio.wait_for(recur_eval.trial("fake:model", probe, out, 0, provider_for), timeout=600))
    return rows, providers


def test_later_sessions_meet_another_cause_with_the_first_repair_kept(tmp_path, monkeypatch):
    rows, providers = _elsewhere(tmp_path, monkeypatch)
    assert [(row["variant"], row["outcome"]) for row in rows] == [
        ("first", "passed"), ("none", "passed"), ("recall", "passed"), ("lesson", "passed"),
    ]
    for variant in recur_eval.LATER:
        assert providers[f"{variant}-0"].looked == {
            "table repaired": True, "catalogue": True, "hidden tests": False, "what the first left": True,
        }
    # Repaired at the run after the catalogue was put right, whatever each was told.
    assert [row["to_fix"] for row in rows[1:]] == [4, 4, 4]
    assert _told(providers["none-0"]) == []
    (said,) = _told(providers["recall-0"])
    # The record is recalled on the same test's name, and its file is as that repair left it:
    # nothing is back, nothing is shown as changed.
    assert f"`{FAILING}` stopped failing" in said and f"`{CAUSE}` (10 steps then)." in said
    assert "Back as before" not in said and "Changed since" not in said and "```diff" not in said
    (said,) = _told(providers["lesson-0"])
    assert "A note was saved with that repair: `lesson_table_keys.md`" in said
    summary = "\n".join(recur_eval.summarize(rows, elsewhere=True))
    assert "none -> recall: what a record of another cause does here" in summary
    assert "recall -> lesson: what that cause's lesson adds" in summary


def test_a_first_session_that_edited_where_the_new_cause_goes_has_no_later_sessions(tmp_path, monkeypatch, capsys):
    rows, _ = _elsewhere(tmp_path, monkeypatch, touch_catalogue=True)
    assert [(row["variant"], row["outcome"]) for row in rows] == [("first", "passed")]
    assert "edited where the new cause goes" in capsys.readouterr().out


def test_each_recurrence_task_fails_untouched_and_passes_repaired(tmp_path):
    """A task that passes without work, or fails a right repair, measures nothing; the task with
    another cause fails again once it is written in, and passes when that one is repaired."""
    from benchmarks.probe_eval import grade, write_setup

    def graded(probe, files):
        ws = tmp_path / f"ws-{len(list(tmp_path.iterdir()))}"
        write_setup({"setup": files}, ws)
        return grade(probe, ws, "")["passed"]

    back, elsewhere = recur_eval.load_tasks(["seen-before-catalogue", "seen-before-new-cause"])
    assert graded(back, back["setup"]) is False
    original = elsewhere["setup"][CATALOGUE]
    assert graded(back, {**back["setup"], CATALOGUE: original}) is True
    assert back["setup"][CAUSE] != elsewhere["setup"][CAUSE]  # its table carries the first repair

    repaired = {**elsewhere["setup"], CAUSE: back["setup"][CAUSE]}
    assert graded(elsewhere, elsewhere["setup"]) is False
    assert graded(elsewhere, repaired) is True
    assert graded(elsewhere, {**repaired, **elsewhere["later"]}) is False
    assert graded(elsewhere, {**repaired, **elsewhere["later"], CATALOGUE: original}) is True


def test_tasks_are_found_in_either_suite_and_an_unknown_one_is_refused():
    assert [task["id"] for task in recur_eval.load_tasks(["seen-before-new-cause", "seen-before"])] == [
        "seen-before-new-cause", "seen-before",
    ]
    with pytest.raises(SystemExit, match="no recurrence task named nope"):
        recur_eval.load_tasks(["seen-before", "nope"])


def test_a_lesson_is_a_note_of_that_kind_that_is_still_there(tmp_path):
    from harness.context.memory import anchors

    assert recur_eval.lessons_in(None) == [] and recur_eval.memory_of(tmp_path) is None
    (tmp_path / "a.md").write_text("x", encoding="utf-8")
    (tmp_path / "b.md").write_text("x", encoding="utf-8")
    anchors.record(tmp_path, "a.md", kind="lesson", anchors=[])
    anchors.record(tmp_path, "b.md", kind="feedback", anchors=[])
    anchors.record(tmp_path, "gone.md", kind="lesson", anchors=[])
    assert recur_eval.lessons_in(tmp_path) == ["a.md"]


def _row(variant, steps, tokens, to_fix=None):
    return {"suite": "recur", "task": "p", "spec": "s", "model": "m", "variant": variant, "outcome": "passed",
            "steps": steps, "tokens": tokens, "to_fix": to_fix if to_fix is not None else steps,
            "fired": {"fixed": 1, "recalled": 0, "asked": 0}}


@pytest.mark.parametrize("measure, ratio", [("to_fix", "x0.5"), ("steps", "x0.4"), ("tokens", "x0.2")])
def test_the_summary_reads_cost_on_each_measure(measure, ratio):
    # To the fix by half, the session by more than half, tokens by more than three quarters:
    # each line reads its own.
    rows = [_row("none", s, s * 1000, to_fix=s - 2) for s in (12, 13, 12, 14)]
    rows += [_row("recall", s, s * 500, to_fix=s) for s in (5, 6, 5, 6)]
    summary = "\n".join(recur_eval.summarize(rows))
    assert f"{measure} {ratio}" in summary and "cheaper" in summary
    assert "recall -> lesson" not in summary  # no lesson sessions ran


def test_trials_stop_once_both_questions_are_answered_on_what_the_recurrence_cost():
    clear = [_row("none", 12, 1, 10), _row("none", 13, 1, 11), _row("none", 12, 1, 10), _row("none", 14, 1, 12)]
    clear += [_row("recall", 9, 1, 5), _row("recall", 9, 1, 6), _row("recall", 9, 1, 5), _row("recall", 9, 1, 6)]
    assert not recur_eval.called(clear, looks=3)  # one question answered, the lesson's still open
    lesson = [_row("lesson", 9, 1, 3), _row("lesson", 9, 1, 3), _row("lesson", 9, 1, 2), _row("lesson", 9, 1, 3)]
    assert recur_eval.called(clear + lesson, looks=3)
    # The session's own steps say nothing here: it is what the failure cost that is read.
    mixed = [{**row, "to_fix": n} for row, n in zip(clear + lesson, (5, 12, 6, 11, 11, 5, 12, 6, 4, 13, 5, 12))]
    assert not recur_eval.called(mixed, looks=3)
    # The same cost on both sides is an answer too: more trials would not move it.
    flat = [{**row, "to_fix": 7} for row in clear + lesson]
    assert recur_eval.called(flat, looks=3)
    # The same spread answers at one look and not when the bound is for ten.
    noisy = [_row("none", 9, 1, n) for n in (8, 12, 9, 13)] + [_row("recall", 9, 1, n) for n in (5, 7, 6, 8)]
    noisy += lesson
    assert recur_eval.called(noisy, looks=1) and not recur_eval.called(noisy, looks=10)
