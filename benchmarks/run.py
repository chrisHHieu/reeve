"""Minimal-mode benchmark runner (D6) — real model runs, driven from the command line.

    python -m benchmarks.run                          # every task, the app's configured provider
    python -m benchmarks.run --model anthropic:claude-sonnet-4 --tasks hello-file
    python -m benchmarks.run --max-iterations 10 --out bench-results

The provider is the SAME wiring the app uses (ProviderRouter over the local secret
store), so a run measures what a user would get — and needs whatever keys the app
already has. Nothing here is invoked by tests.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from harness.observability.bench import load_tasks, run_bench
from harness.config.config import Config
from harness.llm.providers.router import ProviderRouter
from harness.safety.secrets import SecretStore

HERE = Path(__file__).parent


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the Minimal-mode benchmark tasks.")
    parser.add_argument(
        "--model",
        default=Config().model,
        help="Model string, provider-prefixed like the app (default: config.toml's model)",
    )
    parser.add_argument(
        "--tasks", default="all", help="Comma-separated task ids, or 'all' (default all)"
    )
    parser.add_argument("--max-iterations", type=int, default=25)
    parser.add_argument(
        "--out",
        default="",
        help="Directory for results.json (default: print only)",
    )
    parser.add_argument(
        "--tasks-dir",
        default="tasks",
        help="Task directory under benchmarks/ ('tasks', or 'tasks-long' for the long "
        "context tasks)",
    )
    parser.add_argument(
        "--max-context-tokens",
        type=int,
        default=0,
        help="Cap the working window (the `[compaction] max_context_tokens` setting); "
        "0 = the model's own window",
    )
    args = parser.parse_args(argv)

    tasks = load_tasks(HERE / args.tasks_dir)
    if args.tasks != "all":
        wanted = {t.strip() for t in args.tasks.split(",") if t.strip()}
        tasks = [t for t in tasks if t.id in wanted]
        missing = wanted - {t.id for t in tasks}
        if missing:
            parser.error(f"unknown task ids: {sorted(missing)}")
    if not tasks:
        parser.error("no tasks matched")

    provider = ProviderRouter(SecretStore())
    print(f"bench: {len(tasks)} task(s) on {args.model}")
    compaction = (
        {"max_context_tokens": args.max_context_tokens} if args.max_context_tokens else None
    )
    summary = run_bench(
        tasks, provider, args.model, max_iterations=args.max_iterations, compaction=compaction
    )

    for row in summary["tasks"]:
        mark = "PASS" if row["passed"] else "FAIL"
        print(
            f"  [{mark}] {row['task_id']}: {row['iterations']} iters, "
            f"{row['wall_seconds']}s, in={row['input_tokens']} "
            f"cr={row['cache_read_tokens']} out={row['output_tokens']} "
            f"peak={row['peak_context']} compactions={row['compactions']} "
            f"summarizer={row['summarizer_tokens']} (${row['cost_usd']:.5f})"
            + (f" — {row['detail'][:200]}" if not row["passed"] else "")
        )
    print(
        f"{summary['passed']}/{summary['total']} passed · "
        f"{summary['wall_seconds']}s · in={summary['input_tokens']} "
        f"cr={summary['cache_read_tokens']} out={summary['output_tokens']} · "
        f"${summary['cost_usd']:.5f}"
    )

    if args.out:
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        dest = out / "results.json"
        dest.write_text(json.dumps(summary, indent=2), encoding="utf-8")
        print(f"wrote {dest}")
    return 0 if summary["passed"] == summary["total"] else 1


if __name__ == "__main__":
    sys.exit(main())
