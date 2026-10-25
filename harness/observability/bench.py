"""The Minimal-mode benchmark harness (D6) — measure a MODEL over a fixed harness.

A minimal runtime mode exists for exactly this ("for benchmarking models in a
minimal environment", BENCHMARK.md + a Python SDK): only bash and an editor, no
permission theater, deterministic outcome checks — so what varies between runs is the
model, not the harness. reeve's version is this module: the TurnEngine with the
minimal toolset (read/write/patch files + run_shell, composed through the session's
World — D5's seam is what makes "the same minimal world" a one-liner), approvals
bypassed the way the danger-full-access preset is, and PASS/FAIL decided by
checkers that never consult a model.

The harness is library-first on purpose: `run_task`/`run_bench` take any ProviderClient,
so tests drive them with a scripted provider (tests/test_bench_harness.py) and a human
drives real runs through benchmarks/run.py with the app's own configured providers.
No test ever calls a model API.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from harness.loop import TurnEngine
from harness.safety.permissions import Mode, PermissionEngine
from harness.llm.providers.base import ProviderClient
from harness.llm.providers.catalog import estimate_cost_usd
from harness.tools import ToolRegistry
from harness.tools.files import file_tools
from harness.tools.shell import shell_tools
from harness.orchestration.worlds import LocalWorld
from harness.platform.spawn import run as spawn_run


@dataclass
class Task:
    """One benchmark task: a brief, the files it starts with, and a deterministic check.
    `setup` maps workspace-relative paths to contents, written fresh per run."""

    id: str
    prompt: str
    setup: dict[str, str] = field(default_factory=dict)
    check: dict[str, Any] = field(default_factory=dict)


@dataclass
class TaskResult:
    task_id: str
    passed: bool
    iterations: int = 0
    output_tokens: int = 0
    # The prompt side, split exactly like TokenUsage: fresh input, cache reads, cache
    # writes. A bench that reports only output tokens cannot see what a harness change
    # did to the prompt — which is where the money is.
    input_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    # USD from the models.dev price sheet on disk (0.0 when the sheet does not price the
    # model — "unknown", never "free").
    cost_usd: float = 0.0
    wall_seconds: float = 0.0
    detail: str = ""
    # The context side of a run (what a context-window policy changes): the largest
    # prompt any round-trip carried, how many compactions ran, and what the summarizer
    # itself spent — without that last one a capped run looks cheaper than it was.
    peak_context: int = 0
    compactions: int = 0
    summarizer_tokens: int = 0


# -- tasks --------------------------------------------------------------------------


def load_tasks(directory: str | Path) -> list[Task]:
    """Every `tasks/<id>/task.json` under `directory`, sorted by id. A task file missing
    `prompt` or `check` is a broken harness, not a skipped row — raise, loudly."""
    tasks: list[Task] = []
    for path in sorted(Path(directory).glob("*/task.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        if not data.get("prompt") or not data.get("check"):
            raise ValueError(f"{path}: a task needs at least 'prompt' and 'check'")
        tasks.append(
            Task(
                id=data.get("id") or path.parent.name,
                prompt=str(data["prompt"]),
                setup=data.get("setup") or {},
                check=data["check"],
            )
        )
    return tasks


def build_minimal_registry(workspace: Path) -> tuple[ToolRegistry, LocalWorld]:
    """The Minimal toolset: an editor (read + the version-guarded write/patch tools) and
    a shell — all from the session's world, the 'bash + editor' environment. Returns
    the world too: its executor is a live process holding the workspace open, and the
    caller must close it before the throwaway directory can be removed."""
    world = LocalWorld(root=workspace)
    registry = ToolRegistry()
    registry.register_all([*file_tools(str(workspace)), *world.fs_tools()])
    registry.register_all(shell_tools(world.executor))
    return registry, world


# -- checkers -----------------------------------------------------------------------
#
# A checker never asks a model. Every kind reads the workspace the run left behind and
# answers yes/no — the whole difference between "the model says it is done" and "it is
# done" (the L2 line the roadmap draws).


def _run_checker(check: dict[str, Any], workspace: Path) -> tuple[bool, str]:
    kind = check.get("type")
    if kind == "pytest":
        proc = spawn_run(
            # `args` narrows the graded selection (`-k "not …"`) where a red test is
            # part of the task's setup rather than the agent's to fix.
            [sys.executable, "-m", "pytest", "-q", *map(str, check.get("args") or []), str(workspace)],
            # From the workspace, not this process's cwd: run from the repository, pytest took
            # the REPO as rootdir and its pyproject as config for a workspace outside it, and
            # the probe suite flaked with "error during collection" on a Chromium profile dir
            # beside the workspace while Playwright ran alongside (2026-09-26).
            cwd=str(workspace),
            capture_output=True,
            text=True,
            timeout=300,
        )
        return proc.returncode == 0, (proc.stdout or "").strip()[-2000:]
    if kind == "file_equals":
        path = workspace / str(check["path"])
        if not path.exists():
            return False, f"{check['path']} was never written"
        expected = str(check["expected"])
        actual = path.read_text(encoding="utf-8", errors="replace")
        return (
            actual == expected,
            "ok" if actual == expected else f"expected {expected!r}, got {actual!r}",
        )
    if kind == "file_contains":
        path = workspace / str(check["path"])
        if not path.exists():
            return False, f"{check['path']} was never written"
        needle = str(check["contains"])
        actual = path.read_text(encoding="utf-8", errors="replace")
        return needle in actual, f"{needle!r} not found in {check['path']}"
    if kind == "json_matches":
        path = workspace / str(check["path"])
        if not path.exists():
            return False, f"{check['path']} was never written"
        try:
            actual = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        except json.JSONDecodeError as exc:
            return False, f"{check['path']} is not valid JSON: {exc}"
        expected = check["expected"]
        missing = _subset_diff(expected, actual)
        return (
            not missing,
            "missing or mismatched: " + json.dumps(missing)[:2000] if missing else "ok",
        )
    raise ValueError(f"unknown checker type: {kind!r}")


def _subset_diff(expected: Any, actual: Any) -> Any:
    """What `expected` requires that `actual` does not deliver (dict/list recursion;
    scalars compared by equality). The readable half of a JSON check."""
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            return expected
        out = {}
        for key, want in expected.items():
            if key not in actual:
                out[key] = want
            else:
                diff = _subset_diff(want, actual[key])
                if diff is not None:
                    out[key] = diff
        return out or None
    if isinstance(expected, list):
        if not isinstance(actual, list):
            return expected
        missing = [item for item in expected if not _contained(item, actual)]
        return missing or None
    return None if expected == actual else expected


def _contained(item: Any, actual: list[Any]) -> bool:
    if item in actual:
        return True
    if isinstance(item, dict):
        return any(
            isinstance(other, dict) and _subset_diff(item, other) is None
            for other in actual
        )
    return False


# -- the run ------------------------------------------------------------------------


async def _drive(engine: TurnEngine, prompt: str) -> dict[str, Any]:
    """Run one turn to completion, tallying what the benchmark reports on: iterations
    (ITERATION_END count) and the token split (every assistant message's usage).

    `input`, `output`, `cache_read` and `cache_write` are the sidecar's actual keys
    (`TokenUsage.as_dict`). Each round-trip's whole prompt is accounted — the fresh
    tokens AND both cache buckets — so a cost regression (a broken prefix, a schema
    that stopped caching) shows up in the report instead of hiding behind output-only
    numbers. This read `output_tokens` for as long as the field has existed, so every
    benchmark report printed a zero — and nothing asserted the number, which is why
    nothing caught it.
    """
    iterations = 0
    input_tokens = 0
    output_tokens = 0
    cache_read = 0
    cache_write = 0
    peak_context = 0
    compactions = 0
    summarizer_tokens = 0
    async for event in engine.run(prompt):
        if event.type.value == "iteration_end":
            iterations += 1
        if event.type.value == "compacted":
            compactions += 1
            summarizer_tokens += _int_field(event.data, "summarizer_tokens")
        usage = event.data.get("usage")
        if not isinstance(usage, dict):
            continue
        fresh = _int_field(usage, "input")
        read = _int_field(usage, "cache_read")
        write = _int_field(usage, "cache_write")
        input_tokens += fresh
        output_tokens += _int_field(usage, "output")
        cache_read += read
        cache_write += write
        peak_context = max(peak_context, fresh + read + write)
    return {
        "iterations": iterations,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_read": cache_read,
        "cache_write": cache_write,
        "peak_context": peak_context,
        "compactions": compactions,
        "summarizer_tokens": summarizer_tokens,
    }


def _int_field(payload: dict[str, Any], key: str) -> int:
    value = payload.get(key)
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def run_task(
    task: Task,
    provider: ProviderClient,
    model: str,
    *,
    max_iterations: int = 25,
    workspace: Optional[Path] = None,
    keep_workspace: bool = False,
    compaction: Optional[dict[str, Any]] = None,
) -> tuple[TaskResult, Path]:
    """One task, fresh workspace, one verdict. The engine runs with approvals bypassed
    (Minimal mode: the harness is the sandbox — tasks run in a throwaway directory) and
    the cap IS the report: a model that churns without finishing fails on `detail`."""
    root = Path(workspace or Path(tempfile.mkdtemp(prefix=f"bench-{task.id}-")))
    for rel, content in task.setup.items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    registry, world = build_minimal_registry(root)
    engine = TurnEngine(
        provider=provider,
        registry=registry,
        permissions=PermissionEngine(workspace_root=root, mode=Mode.DANGER_FULL_ACCESS),
        model=model,
        max_iterations=max_iterations,
    )
    if compaction:
        # The same live getter the app wires (`[compaction]` settings), so a bench run
        # exercises the real trigger/prepare/prune/summarize path at the chosen window.
        settings = dict(compaction)
        engine.compaction_settings = lambda: dict(settings)

    started = time.monotonic()
    try:
        tallies = asyncio.run(_drive(engine, task.prompt))
    finally:
        # The world's shell is a live process whose cwd IS the workspace — on Windows
        # that locks the directory, and the cleanup below would silently no-op.
        executor = world.executor
        if executor is not None:
            executor.close()
    elapsed = time.monotonic() - started

    ok, detail = _run_checker(task.check, root)
    if not tallies["iterations"] and not ok:
        detail = "the turn produced no steps; " + detail
    result = TaskResult(
        task_id=task.id,
        passed=ok,
        iterations=tallies["iterations"],
        output_tokens=tallies["output_tokens"],
        input_tokens=tallies["input_tokens"],
        cache_read_tokens=tallies["cache_read"],
        cache_write_tokens=tallies["cache_write"],
        cost_usd=estimate_cost_usd(
            model,
            input_tokens=tallies["input_tokens"],
            output_tokens=tallies["output_tokens"],
            cache_read_tokens=tallies["cache_read"],
            cache_write_tokens=tallies["cache_write"],
        )
        or 0.0,
        wall_seconds=round(elapsed, 2),
        detail=detail[:2000],
        peak_context=tallies["peak_context"],
        compactions=tallies["compactions"],
        summarizer_tokens=tallies["summarizer_tokens"],
    )
    if not keep_workspace and ok:
        _rmtree_quiet(root)
    return result, root


def _rmtree_quiet(root: Path) -> None:
    import shutil

    shutil.rmtree(root, ignore_errors=True)


def run_bench(
    tasks: list[Task],
    provider: ProviderClient,
    model: str,
    *,
    max_iterations: int = 25,
    compaction: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Every task, sequentially (one model, one harness, N verdicts). Returns the
    summary the CLI prints and writes: per-task rows plus the pass rate and the token
    split with its USD estimate, so one run answers BOTH questions the harness is
    judged on — did it work, and what did it cost."""
    rows = [
        run_task(t, provider, model, max_iterations=max_iterations, compaction=compaction)[0]
        for t in tasks
    ]
    passed = sum(1 for r in rows if r.passed)
    return {
        "model": model,
        "tasks": [r.__dict__ for r in rows],
        "passed": passed,
        "total": len(rows),
        "wall_seconds": round(sum(r.wall_seconds for r in rows), 2),
        "input_tokens": sum(r.input_tokens for r in rows),
        "output_tokens": sum(r.output_tokens for r in rows),
        "cache_read_tokens": sum(r.cache_read_tokens for r in rows),
        "cache_write_tokens": sum(r.cache_write_tokens for r in rows),
        "cost_usd": round(sum(r.cost_usd for r in rows), 6),
        "summarizer_tokens": sum(r.summarizer_tokens for r in rows),
        "compaction": dict(compaction or {}),
    }
