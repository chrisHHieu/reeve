"""The long-session eval's artifact probe: after the compactions, the files the session changed in task
two must be named, graded against what really changed on disk (Factory's artifact probe; Slipstream:
compaction fails mostly by omission)."""

from __future__ import annotations

from benchmarks.harness_eval import changed_files, session_passed


def test_the_changed_files_are_what_differs_from_the_setup(tmp_path):
    setup = {"shop/rounding.py": "a = 1\n", "shop/cart.py": "b = 2\n", "tests/test_cart.py": "x\n"}
    for rel, body in setup.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(body, encoding="utf-8")
    (tmp_path / "shop/rounding.py").write_text("a = 2\n", encoding="utf-8")
    (tmp_path / "shop/helpers.py").write_text("c = 3\n", encoding="utf-8")
    (tmp_path / "shop/__pycache__").mkdir()
    (tmp_path / "shop/__pycache__/cart.cpython-311.pyc").write_text("cache", encoding="utf-8")
    assert changed_files(tmp_path, setup) == ["shop/helpers.py", "shop/rounding.py"]


def test_a_session_passes_only_when_it_still_names_what_it_changed():
    done = {"checks": {"a": True, "b": True}, "recall": True}
    assert session_passed({**done, "artifact": True}) is True
    assert session_passed({**done, "artifact": False}) is False
    assert session_passed(done) is True  # a row written before the probe existed


def test_the_detail_probe_asks_for_what_only_the_work_shows():
    """The answer is the number of tests task one's suite holds, which the person's
    request never says, so only a summary of the work can keep it."""
    import json
    import re

    from benchmarks.harness_eval import DETAIL_ANSWER, REPO, TASK_ORDER

    task = json.loads((REPO / "benchmarks" / "tasks-long" / TASK_ORDER[0] / "task.json").read_text(encoding="utf-8"))
    tests = task["setup"]["tests/test_store.py"]
    assert len(re.findall(r"(?m)^def test_", tests)) == int(DETAIL_ANSWER)
    assert DETAIL_ANSWER not in task["prompt"]
    done = {"checks": {"a": True}, "recall": True, "artifact": True}
    assert session_passed({**done, "detail": True}) is True
    assert session_passed({**done, "detail": False}) is False
