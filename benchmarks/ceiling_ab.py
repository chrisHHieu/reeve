"""A/B: does the 400k accuracy ceiling help, or should a 1M model use it all?

The auto window's accuracy ceiling rests on a correlation from the real store — the tool-error rate doubled past
400k — and a long session is also usually a harder one. This measures it directly: the
same six tasks with hidden checks, run inside a context that is ALREADY past 400k, under

    arm A  `max_context_tokens = "auto"` — the ceiling: compaction at ~380k, so the tasks
           run in a compacted context
    arm B  `max_context_tokens = 0`      — the model's own window: the tasks run on top of
           ~450k tokens of earlier work

The earlier work is real `read_file` output (rendered by the real tool contract) of an
unrelated `archive/` folder — the shape a long session's stale reads have — prefilled
into the session before the first task. Then six tasks run one per turn, each in its own
folder: the three `benchmarks/tasks-long` generators at two seeds.

    .venv\\Scripts\\python.exe -m benchmarks.ceiling_ab --model opencode-go:deepseek-v4.1-flash --arm A
    ... --arm B        (run the arms in parallel processes; each writes <out>/<tag>.json)
    ... --report <out> (the side-by-side table from the rows written so far)

Spends real tokens: the first request of each session sends the whole prefill (~450k).
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import random
import shutil
import statistics
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from benchmarks import make_long_tasks as gen  # noqa: E402
from harness.compose import build_engine  # noqa: E402

from harness.modes.agents.registry import get_agent  # noqa: E402

from harness.observability.bench import Task, _run_checker  # noqa: E402

from harness.loop import ApprovalOutcome  # noqa: E402

from harness.session.session_log import SessionLog  # noqa: E402

from harness.tools.contract import safe_render  # noqa: E402


ARMS = {"A": "auto", "B": 0}
TASK_SEEDS = (11, 23)
PREFILL_TOKENS = 450_000
#: Files per prefilled "earlier" turn.
READS_PER_TURN = 5


def _kpis() -> Any:
    spec = importlib.util.spec_from_file_location("harness_kpis", REPO / "scripts" / "harness_kpis.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


def make_tasks() -> list[Task]:
    tasks: list[Task] = []
    for seed in TASK_SEEDS:
        rng = random.Random(seed)
        for raw in (gen.bugfix_chain(rng), gen.settings_audit(rng), gen.api_migration(rng)):
            tasks.append(
                Task(id=f"{raw['id']}-{seed}", prompt=raw["prompt"], setup=raw["setup"], check=raw["check"])
            )
    return tasks


def _archive_file(root: Path, rng: random.Random, i: int) -> str:
    noun = gen._NOUNS[i % len(gen._NOUNS)]
    rel = f"archive/{noun}_{i:03d}.py"
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    body = f'"""Archived {noun} helpers, batch {i}."""\n' + gen._filler(rng, noun, 50)
    path.write_text(body, encoding="utf-8")
    return rel


def prefill(engine: Any, root: Path, target_tokens: int) -> int:
    """Earlier turns in which the agent read an unrelated `archive/`, appended after the
    system prompt exactly as the engine records them (the real tool, the real render),
    until the engine's own estimator puts the history at `target_tokens`."""
    from harness.context import compaction as _compaction

    contract = engine.registry.contract("read_file")
    rng = random.Random(7)
    call_no = files = 0
    while _compaction.estimate_tokens(engine.messages) < target_tokens:
        chunk = [_archive_file(root, rng, files + k) for k in range(READS_PER_TURN)]
        files += len(chunk)
        engine.messages.append(
            {"role": "user", "content": f"Skim {', '.join(chunk)} and tell me what they are for."}
        )
        calls, results = [], []
        for rel in chunk:
            call_no += 1
            args = {"path": rel}
            value = engine.registry.execute("read_file", args)
            text = safe_render(contract, "read_file", args, value)
            call_id = f"prefill_{call_no}"
            calls.append(
                {"id": call_id, "type": "function", "function": {"name": "read_file", "arguments": json.dumps(args)}}
            )
            results.append({"role": "tool", "tool_call_id": call_id, "content": text if text is not None else json.dumps(value)})
        engine.messages.append({"role": "assistant", "content": "", "tool_calls": calls})
        engine.messages.extend(results)
        engine.messages.append(
            {"role": "assistant", "content": "These are archived helper modules (normalizers, bucketing, windows, descriptions); nothing to change."}
        )
    return files


async def _approve(_request: Any) -> ApprovalOutcome:
    return ApprovalOutcome.ONCE


async def run_arm(model: str, arm: str, out: Path, tag: str) -> dict[str, Any]:
    from harness.llm.providers.router import ProviderRouter
    from harness.safety.secrets import SecretStore

    tasks = make_tasks()
    workspace = Path(tempfile.mkdtemp(prefix=f"ceiling-{arm}-"))
    for index, task in enumerate(tasks, 1):
        for rel, body in task.setup.items():
            path = workspace / f"t{index}" / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(body, encoding="utf-8")

    engine = build_engine(
        agent=get_agent("reeve"), workspace=workspace, model=model,
        provider=ProviderRouter(SecretStore()), approver=_approve, session_id=None,
        max_iterations=80,
    )
    state = out / tag
    engine.log = SessionLog(state / "conversations" / f"{tag}.events.jsonl")
    engine.compaction_settings = lambda: {"max_context_tokens": ARMS[arm]}
    prefill(engine, workspace, PREFILL_TOKENS)

    started = time.time()
    per_task: list[dict[str, Any]] = []
    compactions = 0
    try:
        for index, task in enumerate(tasks, 1):
            folder = f"t{index}"
            prompt = (
                f"New task. Work ONLY inside the folder `{folder}/`: every path below is relative "
                f"to it, and run pytest from inside it (`cd {folder}` first). " + task.prompt.replace("the workspace root", f"the root of `{folder}/`")
            )
            t0 = time.time()
            peak = 0
            async for event in engine.run(prompt):
                kind = event.type.value
                if kind == "compacted":
                    compactions += 1
                if kind == "assistant_message":
                    usage = event.data.get("usage") or {}
                    peak = max(peak, sum(int(usage.get(k) or 0) for k in ("input", "cache_read", "cache_write")))
            ok, detail = _run_checker(task.check, workspace / folder)
            per_task.append(
                {"task": task.id, "pass": ok, "peak_context": peak, "seconds": round(time.time() - t0, 1),
                 "detail": "" if ok else detail[-300:]}
            )
            print(f"  [{tag}] {task.id}: {'PASS' if ok else 'FAIL'} peak={peak:,} ({per_task[-1]['seconds']}s)", flush=True)
    finally:
        try:
            engine.executor.close()
        except Exception:
            pass
        shutil.rmtree(workspace, ignore_errors=True)

    report = _kpis().compute(state)
    row = {
        "tag": tag, "model": model, "arm": arm, "tasks": per_task,
        "passed": sum(1 for t in per_task if t["pass"]),
        "compactions": compactions,
        "wall": round(time.time() - started, 1),
        "tokens": report["tokens"],
        "steps": report["steps"],
        "tool_error_pct": _error_pct(report),
        "ttft_p50": report["latency"]["ttft_p50"],
        "stalls": report["latency"]["stalls"],
        "unattributed_pct": report["spend"]["unattributed_pct"],
    }
    (out / f"{tag}.json").write_text(json.dumps(row, indent=1), encoding="utf-8")
    return row


def _error_pct(report: dict[str, Any]) -> float:
    calls = sum(r["calls"] for r in report["tools"].values())
    errors = sum(r["calls"] * r["error_pct"] / 100 for r in report["tools"].values())
    return round(100 * errors / calls, 1) if calls else 0.0


def print_report(out: Path) -> None:
    rows = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(out.glob("*.json"))]
    by: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in rows:
        by.setdefault((row["model"], row["arm"]), []).append(row)
    print(f"{'model':36s} arm  runs  passed      tool-err%  steps  tokens(M)  wall(s)  compactions")
    for (model, arm), group in sorted(by.items()):
        total = sum(len(r["tasks"]) for r in group)
        print(
            f"{model:36s} {arm:3s}  {len(group):4d}  {sum(r['passed'] for r in group):2d}/{total:<2d}"
            f"       {statistics.mean(r['tool_error_pct'] for r in group):8.1f}"
            f"  {statistics.mean(r['steps'] for r in group):5.0f}"
            f"  {statistics.mean(r['tokens'] for r in group) / 1e6:9.1f}"
            f"  {statistics.mean(r['wall'] for r in group):7.0f}"
            f"  {statistics.mean(r['compactions'] for r in group):5.1f}"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--model")
    parser.add_argument("--arm", choices=sorted(ARMS))
    parser.add_argument("--run", type=int, default=0)
    parser.add_argument("--out", required=True)
    parser.add_argument("--report", action="store_true")
    args = parser.parse_args(argv)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    if args.report:
        print_report(out)
        return 0
    if not (args.model and args.arm):
        parser.error("--model and --arm are required unless --report")
    tag = f"{args.model.replace(':', '_').replace('/', '_')}-{args.arm}-{args.run}"
    row = asyncio.run(run_arm(args.model, args.arm, out, tag))
    print(json.dumps({k: row[k] for k in ("tag", "passed", "compactions", "tool_error_pct", "steps", "tokens", "wall")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
