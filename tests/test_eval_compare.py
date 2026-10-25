"""The eval gate: a difference between two harnesses is called only when the runs
can carry it, a run that was not a run is on neither side, and stopping early does not
make a result."""

from __future__ import annotations

import json

import pytest

from benchmarks import compare, runs
from benchmarks.compare import BETTER, CHEAPER, DEARER, NO_READING, NO_RUNS, SAME, UNDECIDED, WORSE, verdict


def _runs(outcomes, task="t", tokens=None, version="v1", variant="baseline", spec="s", signs=None):
    tokens = tokens or [100] * len(outcomes)
    return [
        {"suite": "probe", "task": task, "spec": spec, "model": "m", "version": version,
         "variant": variant, "outcome": {True: "passed", False: "failed", None: "void"}[o],
         "tokens": t, "signs": dict(signs or {}), "t": 1.0}
        for o, t in zip(outcomes, tokens)
    ]


def _suite(per_task, **keys):
    return [row for index, outcomes in enumerate(per_task) for row in _runs(outcomes, task=f"t{index}", **keys)]


def test_on_one_task_three_runs_call_only_a_clean_sweep():
    assert verdict(_runs([True] * 3), _runs([False] * 3))["quality"] == WORSE
    assert verdict(_runs([False] * 3), _runs([True] * 3))["quality"] == BETTER
    # 3 of 3 against 1 of 3 was "worse" under the old rule (moved by half the runs); the
    # unchanged long session itself passed 7 of 9, so that much is the harness's own noise.
    found = verdict(_runs([True] * 3), _runs([True, False, False]))
    assert found["quality"] == UNDECIDED and found["more"] >= 1
    assert found["low"] < 0 < found["high"]


def test_three_identical_runs_do_not_show_two_harnesses_are_the_same():
    assert verdict(_runs([True] * 3), _runs([True] * 3))["quality"] == UNDECIDED
    assert verdict(_runs([True] * 60), _runs([True] * 60))["quality"] == SAME


def test_a_void_run_is_on_neither_side():
    found = verdict(_runs([True, True, True]), _runs([None, None, None]))
    assert found["quality"] == NO_RUNS and found["void"] == (0, 3) and found["tasks"] == 0
    # Three failures would have been a clean sweep; three runs that never happened are not.
    mixed = verdict(_runs([True] * 3), _runs([True, None, None, True, True]))
    assert mixed["runs"] == (3, 3) and mixed["void"] == (0, 2) and mixed["quality"] == UNDECIDED


def test_only_tasks_both_sides_ran_are_compared():
    base = _runs([True] * 3, task="a") + _runs([True] * 3, task="b")
    head = _runs([False] * 3, task="a") + _runs([False] * 3, task="c")
    found = verdict(base, head)
    assert (found["tasks"], found["unpaired"]) == (1, 2)
    # The same task id with other contents is another task.
    assert verdict(_runs([True] * 3), _runs([False] * 3, spec="changed"))["quality"] == NO_RUNS


def test_with_many_tasks_one_run_a_task_is_already_a_reading():
    base = _suite([[True]] * 12)
    assert verdict(base, _suite([[False]] * 6 + [[True]] * 6))["quality"] == WORSE
    found = verdict(base, _suite([[False]] * 2 + [[True]] * 10))
    assert found["quality"] == UNDECIDED and found["difference"] == pytest.approx(-2 / 12)
    assert (found["everywhere"], found["nowhere"]) == (10, 0)


def test_no_task_differing_is_not_proof_until_there_are_enough_tasks():
    assert verdict(_suite([[True]] * 12), _suite([[True]] * 12))["quality"] == UNDECIDED
    assert verdict(_suite([[True]] * 35), _suite([[True]] * 35))["quality"] == SAME


def test_a_result_looked_at_round_after_round_is_held_to_a_wider_interval():
    base, head = _runs([True] * 3), _runs([False] * 3)
    assert verdict(base, head, looks=1)["quality"] == WORSE
    assert verdict(base, head, looks=5)["quality"] == UNDECIDED
    assert verdict(_runs([True] * 4), _runs([False] * 4), looks=5)["quality"] == WORSE


def test_the_critical_value_widens_with_looks_and_with_a_spread_from_few_values():
    assert compare.critical(1) == pytest.approx(1.96)
    assert compare.critical(5) > compare.critical(2) > compare.critical(1)
    assert compare.critical(1, 4) == pytest.approx(2.776, rel=0.02)   # Student's t, 4 df
    assert compare.critical(1, 30) == pytest.approx(2.042, rel=0.01)
    assert compare.critical(1, 0) == float("inf")


def test_cost_is_called_only_past_the_spread_of_the_runs():
    base = _runs([True] * 3, tokens=[100, 130, 120])
    assert verdict(base, _runs([True] * 3, tokens=[150, 160, 170]))["cost"] == DEARER
    assert verdict(base, _runs([True] * 3, tokens=[60, 70, 65]))["cost"] == CHEAPER
    assert verdict(base, _runs([True] * 3, tokens=[90, 125, 140]))["cost"] == UNDECIDED
    assert verdict(base, _runs([True], tokens=[500]))["cost"] == NO_READING  # one run has no spread
    steady = _runs([True] * 8, tokens=[100, 101, 99, 100, 102, 98, 100, 100])
    assert verdict(steady, _runs([True] * 8, tokens=[101, 100, 99, 101, 100, 102, 99, 100]))["cost"] == SAME


def test_the_gate_says_what_to_do_with_the_change():
    assert compare.call(verdict(_runs([True] * 3), _runs([False] * 3))).startswith("reopen: worse")
    assert compare.call(verdict(_runs([False] * 3), _runs([True] * 3))).startswith("keep: better")
    assert compare.call(verdict(_runs([True] * 3), _runs([None] * 3))) == "no task was run on both sides"
    undecided = compare.call(verdict(_runs([True] * 3), _runs([True, True, False])))
    assert undecided.startswith("undecided: about ") and "more run(s)" in undecided

    same = _suite([[True, True]] * 35, tokens=[100, 102])
    assert compare.call(verdict(same, _suite([[True, True]] * 35, tokens=[101, 100]))) == "keep: no difference past 10 points"
    # No gain and a bigger bill is not a change worth keeping.
    dear = verdict(same, _suite([[True, True]] * 35, tokens=[150, 152]))
    assert (dear["quality"], dear["cost"]) == (SAME, DEARER)
    assert compare.call(dear) == "reopen: no gain past 10 points, and dearer"
    cheap = verdict(same, _suite([[True, True]] * 35, tokens=[50, 52]))
    assert compare.call(cheap) == "keep: no difference past 10 points, and cheaper"


def test_a_suite_that_cannot_tell_two_harnesses_apart_says_so():
    found = verdict(_runs([True, False] * 3), _runs([False, True] * 3), tolerance=0.01)
    assert found["quality"] == UNDECIDED and found["more"] > 50
    assert "cannot tell the two apart" in compare.call(found)


def test_what_failed_runs_show_that_passed_runs_do_not():
    rows = (
        _runs([False] * 3, signs={"edit-misses": 2, "poll-steps": 1})
        + _runs([False], signs={"poll-steps": 4})
        + _runs([True] * 4, signs={"poll-steps": 1})
        + _runs([True], signs={})
        + _runs([None], signs={"edit-misses": 9})  # not a run: not evidence
    )
    owners = {"edit-misses": "tools/edit surface", "poll-steps": "tools/shell background"}
    # edit-misses: 3 of 4 failed runs, no passed run. poll-steps: 4 of 4 failed, 4 of 5 passed.
    assert compare.separates(rows, owners) == [("edit-misses", "tools/edit surface", 3, 4, 0, 5)]
    assert compare.separates(_runs([True] * 3, signs={"edit-misses": 1}), owners) == []
    # One failed run is an anecdote, however wide its gap to the passed ones.
    once = _runs([False], signs={"edit-misses": 1}) + _runs([True] * 3)
    assert compare.separates(once, owners) == []


def test_a_side_is_picked_by_version_prefix_and_variant(monkeypatch):
    rows = (
        _runs([True], version="abc123") + _runs([False], version="abd999")
        + _runs([True], version="abc123", variant="todo_list")
    )
    label, picked = compare.side(rows, "abc")
    assert label == "abc123:baseline" and len(picked) == 1
    assert compare.side(rows, "abc:todo_list")[0] == "abc123:todo_list"
    assert compare.side(rows, "zzz") == ("zzz:baseline", [])
    with pytest.raises(SystemExit, match="more than one version"):
        compare.side(rows, "ab")
    monkeypatch.setattr(runs, "harness_version", lambda: "abd999")
    assert compare.side(rows, "HEAD")[0] == "abd999:baseline"


def test_the_command_reads_a_ledger_and_its_exit_code_is_the_gate(tmp_path, capsys):
    ledger = tmp_path / "runs.jsonl"
    assert compare.main(["--ledger", str(ledger)]) == 0
    assert "holds no runs" in capsys.readouterr().out

    runs.record(_suite([[True]] * 12, version="aaa111") + _suite([[False]] * 12, version="bbb222"), ledger)
    assert compare.main(["--ledger", str(ledger)]) == 0
    listing = capsys.readouterr().out
    assert "aaa111" in listing and "bbb222" in listing

    assert compare.main(["--ledger", str(ledger), "--base", "aaa", "--head", "bbb"]) == 1
    out = capsys.readouterr().out
    assert "aaa111:baseline  ->  bbb222:baseline" in out and "reopen: worse" in out
    assert compare.main(["--ledger", str(ledger), "--base", "bbb", "--head", "aaa"]) == 0
    assert compare.main(["--ledger", str(ledger), "--base", "aaa", "--head", "aaa"]) == 2  # undecided
    assert compare.main(["--ledger", str(ledger), "--base", "aaa", "--head", "bbb", "--model", "other"]) == 2


def _fake_ablation(monkeypatch, tmp_path, passes):
    """`ablation.main` with the model taken out: `passes(variant)` says how a run ends."""
    from benchmarks import ablation

    ran = []

    async def run(model, suite, window, state, tag, ablate, enable, knobs):
        variant = next(iter(ablate), "baseline")
        ran.append((suite, variant))
        return {"passed": passes(variant), "spec": "s", "steps": 1, "tokens": 100, "fresh": 0,
                "split": {"fresh": 0, "cached": 0, "output": 0}, "compactions": {},
                "schema_chars": 0, "wall": 0.0, "result": {}}

    def row(**keys):
        return {"outcome": "passed" if keys["passed"] else "failed", "why": "", **{
            k: keys[k] for k in ("suite", "task", "variant", "model", "tokens")}}

    monkeypatch.setattr(ablation, "_run", run)
    monkeypatch.setattr(ablation.runs, "row", row)
    monkeypatch.setattr(ablation.runs, "record", lambda rows: None)
    monkeypatch.setattr(ablation, "fired", lambda state, name: 1)
    return ablation, ran


def test_a_variant_already_called_buys_no_more_runs_and_an_open_one_runs_to_the_ceiling(monkeypatch, tmp_path, capsys):
    ablation, ran = _fake_ablation(monkeypatch, tmp_path, lambda variant: variant != "verify_nudge")
    mechanisms = ablation.suites(["verify_nudge", "check_loop"])
    assert ablation.main(["--model", "m", "--only", "verify_nudge", "check_loop", "--runs", "8",
                          "--out", str(tmp_path)]) == 0
    count = {pair: ran.count(pair) for pair in set(ran)}
    lost, kept = mechanisms["verify_nudge"], mechanisms["check_loop"]
    # Four clean sweeps settle it at this ceiling; the variant and its baseline stop there.
    assert count[(lost, "verify_nudge")] == 4 and count[(lost, "baseline")] == 4
    # Passing like its baseline is not "the same" on one task: it runs to the ceiling.
    assert count[(kept, "check_loop")] == 8 and count[(kept, "baseline")] == 8
    out = capsys.readouterr().out
    assert "quality: worse without it" in out and "within noise" in out


def test_one_run_a_side_never_stops_an_ablation(monkeypatch, tmp_path):
    ablation, ran = _fake_ablation(monkeypatch, tmp_path, lambda variant: variant == "baseline")
    ablation.main(["--model", "m", "--only", "verify_nudge", "--runs", "2", "--out", str(tmp_path)])
    assert len(ran) == 4


def test_a_probe_run_that_was_not_a_run_does_not_fail_the_probe_eval(monkeypatch, tmp_path, capsys):
    from benchmarks import probe_eval

    outcomes = iter(["passed", "void", "failed"])

    async def run_probe(model, probe, state, tag):
        return {"passed": False, "check": True, "touched": [], "missing": [], "wall": 0.0}

    def row(**keys):
        outcome = next(outcomes)
        return {"outcome": outcome, "why": "no_quota" if outcome == "void" else "", "suite": "probe",
                "task": keys["task"], "variant": "baseline", "model": "m", "tokens": 0}

    monkeypatch.setattr(probe_eval, "run_probe", run_probe)
    monkeypatch.setattr(probe_eval, "_kpis", lambda: type("K", (), {"compute": staticmethod(lambda state: {})}))
    monkeypatch.setattr(probe_eval, "fresh_tokens", lambda state: 0)
    monkeypatch.setattr(probe_eval.runs, "row", row)
    monkeypatch.setattr(probe_eval.runs, "record", lambda rows: None)
    args = ["--model", "m", "--only", "rename-everywhere", "--out", str(tmp_path)]
    assert probe_eval.main([*args, "--runs", "2"]) == 0  # passed, then a run the account ended
    assert "[VOID]" in capsys.readouterr().out
    assert probe_eval.main(args) == 1  # a failed run is still a failed probe


def test_the_long_session_passes_on_its_tasks_and_its_recall_and_is_paired_by_its_window():
    from benchmarks.harness_eval import session_passed, session_spec

    done = {"api-migration": True, "bugfix-chain": True, "settings-audit": True}
    assert session_passed({"checks": done, "recall": True})
    assert not session_passed({"checks": done, "recall": False})
    assert not session_passed({"checks": {**done, "bugfix-chain": "1 failed"}, "recall": True})
    assert not session_passed({"checks": {}, "recall": True})  # a session that never ran its tasks
    assert session_spec(60_000) == session_spec(60_000) != session_spec(0)
    assert session_spec(60_000, {"fold_hint_tokens": 1}) != session_spec(60_000)


def test_a_history_comparison_already_called_buys_no_second_round(monkeypatch, tmp_path, capsys):
    from benchmarks import history_eval

    tasks = [{"id": f"c{i}", "commit": f"c{i}", "tests": ["tests/test_x.py"]} for i in range(10)]
    tasks_file = tmp_path / "tasks.json"
    tasks_file.write_text(json.dumps(tasks), encoding="utf-8")
    ran = []

    async def run_task(task, model, state, tag, ablate, window=0):
        ran.append(tag)
        return {"passed": False, "edited_tests": False, "peeked": False, "capped": True, "wall": 1.0}

    def row(**keys):
        return {"outcome": "failed", "why": "", "fresh": 0, **{
            k: keys[k] for k in ("suite", "task", "variant", "model", "tokens", "flags")},
            "spec": keys["task_spec"]}

    earlier = [
        {"suite": "history", "task": t["id"], "spec": runs.spec(t["commit"], t["tests"]), "model": "m",
         "version": "old111", "variant": "baseline", "outcome": "passed", "tokens": 100, "t": 1.0}
        for t in tasks
    ]
    monkeypatch.setattr(history_eval, "LINK_TARGET", None)  # `main` sets it: put it back after
    monkeypatch.setattr(history_eval, "TASKS_FILE", tasks_file)
    monkeypatch.setattr(history_eval, "eval_venv", lambda: tmp_path)
    monkeypatch.setattr(history_eval, "run_task", run_task)
    monkeypatch.setattr(history_eval, "_kpis", lambda: type("K", (), {"compute": staticmethod(lambda state: {})}))
    monkeypatch.setattr(history_eval, "fresh_tokens", lambda state: 0)
    monkeypatch.setattr(history_eval, "fired", lambda state, name: 0)
    monkeypatch.setattr(history_eval.runs, "row", row)
    monkeypatch.setattr(history_eval.runs, "record", lambda rows: None)
    monkeypatch.setattr(history_eval.runs, "load", lambda: earlier)

    assert history_eval.main(["--model", "m", "--runs", "3", "--against", "old", "--out", str(tmp_path / "o")]) == 0
    assert len(ran) == 10  # every task failed where the earlier version passed: one round says it
    out = capsys.readouterr().out
    assert "old -> this checkout" in out and "reopen: worse" in out

    with pytest.raises(SystemExit):  # nothing to compare with is said before a token is spent
        history_eval.main(["--model", "m", "--against", "nothere", "--out", str(tmp_path / "p")])
    assert len(ran) == 10


def _on(model, rows):
    return [{**row, "model": model} for row in rows]


def test_a_change_worse_on_one_model_is_reopened_whatever_the_models_together_show(tmp_path, capsys):
    """Self-Harness refuses a proposal that improves one split and degrades the other; a
    harness edit's gain depends on the model running it (HarnessDev). Pooled, the two models
    below cancel out."""
    base = _on("a", _suite([[False]] * 12, version="aaa111")) + _on("b", _suite([[True]] * 12, version="aaa111"))
    head = _on("a", _suite([[True]] * 12, version="bbb222")) + _on("b", _suite([[False]] * 12, version="bbb222"))
    models = compare.per_model(base, head)
    assert {m: v["quality"] for m, v in models.items()} == {"a": BETTER, "b": WORSE}
    assert compare.worse_on(models) == ["b"]
    assert verdict(base, head)["quality"] != WORSE  # what the pooled rate alone would say

    ledger = tmp_path / "runs.jsonl"
    runs.record(base + head, ledger)
    assert compare.main(["--ledger", str(ledger), "--base", "aaa", "--head", "bbb"]) == 1
    out = capsys.readouterr().out
    assert "reopen: worse on b" in out and "\na\n" in out and "\nb\n" in out
    # One model alone is read as before.
    assert compare.main(["--ledger", str(ledger), "--base", "aaa", "--head", "bbb", "--model", "a"]) == 0
    assert "worse on" not in capsys.readouterr().out
