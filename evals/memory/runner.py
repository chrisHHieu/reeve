"""Runner for the memory eval harness (Phase 0 of docs/memory-redesign.md).

Standalone:  ``python -m evals.memory.runner [--only ID] [--json]``
Via pytest:  ``tests/test_memory_eval.py`` runs the same checks.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from evals.memory.scenarios import SCENARIOS, Scenario


@dataclass
class Result:
    scenario: Scenario
    status: str  # PASS | FAIL | PENDING | SKIP
    detail: str = ""


def provider_available() -> bool:
    """A real embedding endpoint needs both the opt-in and a model name; without them a
    provider scenario skips instead of failing on a setup the caller never built."""
    return bool(
        os.environ.get("REEVE_EVAL_PROVIDER") == "1"
        and os.environ.get("REEVE_EVAL_EMBED_MODEL")
    )


def run_one(scenario: Scenario, workdir: Path, *, has_provider: bool | None = None) -> Result:
    """Run one scenario in ``workdir``.

    ``pending`` scenarios report instead of failing (the owning phase has not
    shipped); ``needs_provider`` ones skip unless the caller opts in.
    """
    if has_provider is None:
        has_provider = provider_available()
    if scenario.needs_provider and not has_provider:
        return Result(
            scenario,
            "SKIP",
            "needs REEVE_EVAL_PROVIDER=1 and REEVE_EVAL_EMBED_MODEL",
        )
    try:
        scenario.check(workdir)
    except Exception as exc:
        detail = f"{type(exc).__name__}: {exc}"
        if scenario.pending:
            return Result(scenario, "PENDING", detail)
        return Result(scenario, "FAIL", detail)
    if scenario.pending:
        return Result(scenario, "PENDING", "check passed — flip the pending flag")
    return Result(scenario, "PASS")


def run_all(*, only: str | None = None) -> list[Result]:
    results: list[Result] = []
    for scenario in SCENARIOS:
        if only and scenario.id != only:
            continue
        with tempfile.TemporaryDirectory(
            prefix="memory-eval-", ignore_cleanup_errors=True
        ) as tmp:
            results.append(run_one(scenario, Path(tmp)))
    return results


def format_report(results: list[Result]) -> str:
    lines = []
    for result in results:
        scenario = result.scenario
        line = f"{result.status:<7} {scenario.id:<30} phase {scenario.phase}  {scenario.title}"
        if result.detail and result.status != "PASS":
            line += f"\n        {result.detail}"
        lines.append(line)
    passed = sum(r.status == "PASS" for r in results)
    failed = sum(r.status == "FAIL" for r in results)
    pending = sum(r.status == "PENDING" for r in results)
    skipped = sum(r.status == "SKIP" for r in results)
    lines.append(f"\n{passed} passed · {failed} failed · {pending} pending · {skipped} skipped")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Memory eval harness")
    parser.add_argument("--only", help="run a single scenario id")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args(argv)
    results = run_all(only=args.only)
    if args.json:
        payload = [
            {
                "id": r.scenario.id,
                "status": r.status,
                "phase": r.scenario.phase,
                "modes": r.scenario.modes,
                "detail": r.detail,
            }
            for r in results
        ]
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(format_report(results))
    return 1 if any(r.status == "FAIL" for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
