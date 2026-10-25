"""Owner isolation for the shell (owner-hit 2026-09-20).

A subagent's registry is a copy of its session's, so its `run_shell` closure
pointed at the PARENT's shell process. Two background children then shared one PowerShell
REPL: their commands queued behind each other (a sibling's 8s sleep delayed the other's
command) and their trailer markers crossed — one child's tool returned "(no output)" while
its command had not started yet, the other returned the neighbour's completion 8 seconds
late. The terminal tools had already been rebound per child; the shell tools now are.
Every command is its own process, so cross-talk is impossible by
construction — the rebind still matters because it is what gives the child its OWN cwd,
environment and background registry.
"""

from __future__ import annotations

import sys
import threading
import time

from harness.tools import ToolRegistry
from harness.tools.shell import LocalExecutor, replace_shell_tools, shell_tools

_WIN = sys.platform == "win32"

CWD = "Get-Location" if _WIN else "pwd"
SLOW_THEN_TAG = (
    'Start-Sleep -Milliseconds 700; Write-Output "slow-done"'
    if _WIN
    else "sleep 0.7; echo slow-done"
)
TAG = 'Write-Output "fast-done"' if _WIN else "echo fast-done"


def _executor(path):
    ex = LocalExecutor(cwd=path, default_timeout=10)
    return ex


def test_concurrent_runs_on_one_executor_do_not_swap_results(tmp_path):
    """Two calls on one executor run in their OWN processes — the property the
    old REPL could only get from a lock around the whole command.

    Before, the two commands shared one stdin/stdout pair: the first marker read satisfied
    whichever call happened to read it — the fast call could return the slow command's
    marker (claiming a 700ms command for a 0ms one) and the slow call was left with no
    output at all. The lock prevented that by serialising. Now there is nothing to
    serialise: the fast call must NOT wait for the slow one, and no line may cross.
    """
    ex = _executor(tmp_path)
    results: dict[str, dict] = {}
    timings: dict[str, float] = {}

    def run(key: str, command: str) -> None:
        started = time.monotonic()
        results[key] = ex.run(command)
        timings[key] = time.monotonic() - started

    try:
        slow = threading.Thread(target=run, args=("slow", SLOW_THEN_TAG))
        fast = threading.Thread(target=run, args=("fast", TAG))
        slow.start()
        time.sleep(0.05)
        fast.start()
        slow.join(timeout=15)
        fast.join(timeout=15)
    finally:
        ex.close()

    assert results["slow"]["exit_code"] == 0
    assert results["fast"]["exit_code"] == 0
    assert "slow-done" in results["slow"]["output"]
    assert "fast-done" in results["fast"]["output"]
    # Each result carries its OWN command's output — not the neighbour's.
    assert "fast-done" not in results["slow"]["output"]
    assert "slow-done" not in results["fast"]["output"]
    # …and the fast call did not wait for the slow one: serialised, it would have paid
    # the full 700ms sleep plus its own spawn.
    assert timings["slow"] >= 0.7
    assert timings["fast"] < 0.7, f"the fast call waited: {timings['fast']:.2f}s"


def test_replace_shell_tools_rebinds_to_the_new_owner(tmp_path):
    """The rebind a child engine needs: same three tools, the child's own executor."""
    parent_dir = tmp_path / "parent"
    child_dir = tmp_path / "child"
    parent_dir.mkdir()
    child_dir.mkdir()
    parent_exec = _executor(parent_dir)
    child_exec = _executor(child_dir)
    registry = ToolRegistry()
    registry.register_all(shell_tools(parent_exec))
    try:
        # Before the swap the inherited tool is the parent's shell...
        inherited = registry.get("run_shell")
        assert inherited is not None
        assert str(parent_dir) in inherited.func(command=CWD)["output"]

        # ...after it, the child's own — for every shell tool name.
        assert replace_shell_tools(registry, child_exec) is True
        rebound = registry.get("run_shell")
        assert rebound is not None
        assert str(child_dir) in rebound.func(command=CWD)["output"]

        # A registry without shell tools (a surface that never had one) is a no-op.
        bare = ToolRegistry()
        assert replace_shell_tools(bare, child_exec) is False
    finally:
        parent_exec.close()
        child_exec.close()
