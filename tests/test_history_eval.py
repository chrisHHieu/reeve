"""History eval's worktree: the venv link comes and goes, the real venv never does.

Every task checks out a worktree with a `.venv` junction to this checkout's venv, so a
removal that followed the link would delete the developer's environment. Pinned against
a stand-in venv, never the real one.
"""

from __future__ import annotations

import json

import pytest

from benchmarks import history_eval


@pytest.fixture
def fake_venv(tmp_path, monkeypatch):
    venv = tmp_path / "venv"
    (venv / "marker").mkdir(parents=True)
    monkeypatch.setattr(history_eval, "VENV", venv)
    return venv


def test_the_snapshot_links_the_venv_and_leaves_it_behind(fake_venv):
    # HEAD, not a commit before it: a fresh or shallow clone (CI's checkout) has no parent to snapshot.
    with history_eval.Worktree("HEAD") as tree:
        path = tree.path
        assert (path / ".venv" / "marker").is_dir()  # the link resolves
        assert (path / "pyproject.toml").is_file()  # a real checkout
        # No road to the fix: the snapshot's history is one commit, whatever the checkout's is.
        assert history_eval.git("rev-list", "--all", "--count", cwd=path).strip() == "1"
    assert not path.exists()
    assert (fake_venv / "marker").is_dir()  # the target survived the removal


def test_an_edited_test_file_is_noticed_and_restored(fake_venv):
    test = "tests/test_todo_tool.py"
    with history_eval.Worktree("HEAD") as tree:
        tree.bring_tests("HEAD", [test])
        assert not tree.edited("HEAD", [test])
        (tree.path / test).write_text("def test_x():\n    pass\n", encoding="utf-8")
        assert tree.edited("HEAD", [test])
        tree.bring_tests("HEAD", [test])
        assert not tree.edited("HEAD", [test])


def test_the_prompt_names_the_tests_and_forbids_editing_them():
    task = {"tests": ["tests/test_a.py"], "failing": "E   AssertionError\n1 failed in 0.1s"}
    prompt = history_eval.prompt_for(task)
    assert "tests/test_a.py" in prompt and "1 failed" in prompt
    assert "do not edit them" in prompt


def test_a_run_that_reached_for_the_fix_is_marked():
    # The first pilot copied `git show <fix>` from a worktree that shared refs.
    def call(arguments):
        return {"role": "assistant", "tool_calls": [{"function": {"name": "run_shell", "arguments": arguments}}]}

    sha = "1b08d797deadbeef"
    assert history_eval._peeked([call('{"command": "git show 1b08d79 -- a.py"}')], sha)
    source = str(history_eval.REPO).replace("\\", "\\\\")
    assert history_eval._peeked([call('{"command": "cd ' + source + '; git log"}')], sha)
    assert not history_eval._peeked([call('{"command": "git status --short"}')], sha)


def test_importing_the_harness_in_run_code_reads_the_fix():
    """The code child runs in this checkout, whose code is the fix: one fix imported
    `harness.tools.search` there in the 2026-09-29 baseline."""
    def run_code(code):
        return {"role": "assistant", "tool_calls": [{"function": {
            "name": "run_code", "arguments": json.dumps({"code": code})}}]}

    sha = "1b08d797deadbeef"
    assert history_eval._peeked([run_code("from harness.tools.search import search_tool\n")], sha)
    assert history_eval._peeked([run_code("x = 1\nimport harness.loop as e\n")], sha)
    assert not history_eval._peeked([run_code("r = tools.read_file(path='harness/a.py')\n")], sha)
    assert not history_eval._peeked([run_code("import json\nprint('harness')\n")], sha)


def test_the_runs_own_scratch_folder_is_not_a_peek():
    call = {"role": "assistant", "tool_calls": [{"function": {"name": "run_shell", "arguments":
            '{"command": "python C:\\Users\\x\\Reeve\\1b08d797-baseline-0\\check.py"}'}}]}
    assert not history_eval._peeked([call], "1b08d797deadbeef", ignore="1b08d797-baseline-0")


def test_the_prompt_never_names_this_checkout():
    # 2026-09-29: two tasks' failing output held this checkout's venv path (a test prints
    # the argv of the Python it spawns); the agent used it and a solved task was graded
    # `peeked`. Every spelling a traceback or a repr prints is taken out.
    root = str(history_eval.REPO)
    failing = "\n".join([
        f"CompletedProcess(args=['{root}\\.venv\\Scripts\\python.exe', 'x'])",
        "args=['" + root.replace("\\", "\\\\") + "\\\\.venv']",
        root.replace("\\", "/").upper() + "/harness/integrations/cua_server.py",
        "E   assert 1 == 0",
    ])
    prompt = history_eval.prompt_for({"tests": ["tests/test_a.py"], "failing": failing})
    low = prompt.lower()
    for spelling in (root, root.replace("\\", "/"), root.replace("\\", "\\\\")):
        assert spelling.lower() not in low
    assert "assert 1 == 0" in prompt and ".venv" in prompt


def test_a_task_installs_its_snapshot_into_the_runs_venv_never_the_real_one(tmp_path, monkeypatch):
    calls = []
    real_run = history_eval.subprocess.run

    def fake_run(argv, *args, **kwargs):
        if argv and "pip" in argv:
            calls.append(argv)
            return None
        return real_run(argv, *args, **kwargs)

    monkeypatch.setattr(history_eval.subprocess, "run", fake_run)
    venv = tmp_path / "run-venv"
    venv.mkdir()
    monkeypatch.setattr(history_eval, "LINK_TARGET", venv)
    with history_eval.Worktree("HEAD") as tree:
        path = tree.path
    assert len(calls) == 1 and calls[0][-2:] == ["-e", str(path)]
    assert str(venv) in " ".join(calls[0]) and "--no-deps" in calls[0]
    # With no run venv (LINK_TARGET None) the real venv is linked and nothing is installed into it.
    calls.clear()
    monkeypatch.setattr(history_eval, "LINK_TARGET", None)
    monkeypatch.setattr(history_eval, "VENV", venv)
    with history_eval.Worktree("HEAD"):
        pass
    assert calls == []


def test_a_run_the_step_ceiling_ended_is_reported_capped(tmp_path):
    log = tmp_path / "conversations" / "t-baseline-0.events.jsonl"
    log.parent.mkdir()
    log.write_text(
        '{"type": "turn/start"}\n{"type": "turn/end", "reason": "max_iterations_exceeded"}\n',
        encoding="utf-8",
    )
    assert history_eval.end_reason(tmp_path, "t-baseline-0") == "max_iterations_exceeded"
    assert history_eval.end_reason(tmp_path, "missing") == ""
    # The ceiling sits well above the longest real run (80 steps in the baseline).
    assert history_eval.STEP_CEILING > 80


def test_a_task_is_graded_by_the_interpreter_its_agent_checks_with(tmp_path, monkeypatch):
    """A task of 2026-10-09: its test starts `python -m <package>` in a child. The grader
    ran this checkout's interpreter, where the snapshot's package is not installed, so the
    commit's own fix graded 1 failed, 32 passed, while the snapshot's `.venv` passed all 33."""
    ran = []
    real_run = history_eval.subprocess.run

    def fake_run(argv, *args, **kwargs):
        if argv and "pip" in argv:
            return None
        if argv and "pytest" in argv:
            ran.append(argv[0])
            return history_eval.subprocess.CompletedProcess(argv, 0, "1 passed", "")
        return real_run(argv, *args, **kwargs)

    monkeypatch.setattr(history_eval.subprocess, "run", fake_run)
    venv = tmp_path / "run-venv"
    venv.mkdir()
    monkeypatch.setattr(history_eval, "LINK_TARGET", venv)
    with history_eval.Worktree("HEAD") as tree:
        assert tree.run_tests(["tests/test_a.py"])[0] is True
    own = venv / ("Scripts/python.exe" if history_eval.os.name == "nt" else "bin/python")
    assert ran == [str(own)] and str(own) != str(history_eval.PYTHON)
    # With no run venv the checkout's interpreter is all there is.
    ran.clear()
    monkeypatch.setattr(history_eval, "LINK_TARGET", None)
    monkeypatch.setattr(history_eval, "VENV", venv)
    with history_eval.Worktree("HEAD") as tree:
        tree.run_tests(["tests/test_a.py"])
    assert ran == [str(history_eval.PYTHON)]
