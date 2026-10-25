"""Ablation — what each harness mechanism is worth, by switching it off (live).

Ablation: keep the model fixed, remove one harness
component at a time, and see which removal hurts. The one that hurts most is carrying
the most weight today; one whose removal changes nothing is a candidate to simplify
away — "a constraint essential today may be unnecessary overhead in three months".

Each mechanism is measured on the suite that makes it act:

    probe   the `benchmarks/tasks-probe` task built for it (one single-turn session,
            graded on what the mechanism protects) — the engine nudges
    long    the `benchmarks.harness_eval` session (three long tasks + a recall after
            compaction) — the context budget line, the model fold

Each row also carries the input tokens priced in full (`fresh`): a mechanism
that rewrites the middle of the prompt can lower the token count while raising the bill.

Both run on the product's engine (`harness_eval.product_engine`). Every variant and its
baseline run up to `--runs` times (default 3), round after round: the first ablation read
a ±26% token swing between two IDENTICAL configurations at n=1, so one run is not a
reading. The table gives each side's pass rate, how many baseline runs the mechanism acted
in, and steps and tokens as mean [min-max]; the reading calls a difference only when the
runs can carry it (`benchmarks/compare.py`):

    never acted       the mechanism did not act in any baseline run: no reading at all
    quality           the interval of the difference in pass rate leaves zero out
    cost              the interval of the token ratio leaves one out
    within noise      neither

A variant whose quality is already called stops being run, and so does a baseline nothing
still needs, so a larger `--runs` is spent only where the answer is still open. A run the
account or the network ended is on neither side (`void`, `benchmarks/runs.py`); every run
is a row in the eval ledger, with the harness version that ran it.

A mechanism that is OFF in the product (`harness_eval.OPT_IN`) is measured the other way
round with `--enable`: the baseline is the product as it ships, the variant switches the
mechanism ON, and its row also splits the tokens into priced-in-full, read from cache and
written, because a mechanism that shrinks the context moves tokens between the first two.
`--window 0` runs the long session at the product's own window instead of a small one.

A feature of the session is a mechanism too: `--only feature:web` runs the variant
with `[features] remove = ["web"]` in the workspace config, the way a person leaves it out, on
the long session; it acted in a baseline when the model called one of its tools.

Spends real tokens:

    .venv\\Scripts\\python.exe -m benchmarks.ablation --model opencode-go:deepseek-v4.1-flash [--only todo_nudge context_budget_line] [--enable model_fold] [--runs 3] [--window 60000]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
from pathlib import Path
from typing import Any, Optional

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from benchmarks import runs  # noqa: E402
from benchmarks.compare import BETTER, CHEAPER, DEARER, WORSE, settled, verdict  # noqa: E402
from benchmarks.harness_eval import (  # noqa: E402
    ABLATABLE,
    FEATURE_ABLATABLE,
    OPT_IN,
    _kpis,
    compaction_ends,
    fired,
    fresh_tokens,
    run_session,
    session_passed,
    session_spec,
    token_split,
)
from benchmarks.probe_eval import load_probes, run_probe  # noqa: E402

LONG = "long"


#: A mechanism measured on another mechanism's probe: the task list is taken away on the
#: task the todo nudge was built for (many files, one rename, several steps).
SHARED_PROBES = {"todo_list": "todo_nudge"}


def suites(mechanisms: list[str]) -> dict[str, str]:
    """mechanism -> the suite that measures it: its probe's id, else the long session."""
    probe_for = {probe["mechanism"]: probe["id"] for probe in load_probes()}
    for name, owner in SHARED_PROBES.items():
        if owner in probe_for:
            probe_for.setdefault(name, probe_for[owner])
    return {name: probe_for.get(name, LONG) for name in mechanisms}


async def _run(
    model: str,
    suite: str,
    window: int,
    state: Path,
    tag: str,
    ablate: frozenset[str],
    enable: frozenset[str] = frozenset(),
    compaction: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """One session of `suite`, reduced to the row the table reads."""
    if suite == LONG:
        result = await run_session(
            model, window, state, tag, ablate=ablate, enable=enable, compaction=compaction
        )
        passed = session_passed(result)
        task_spec = session_spec(window, compaction)
    else:
        (probe,) = load_probes([suite])
        result = await run_probe(model, probe, state, tag, ablate=ablate)
        passed = bool(result["passed"])
        task_spec = runs.spec(probe)
    report = _kpis().compute(state)
    return {
        "passed": passed,
        "spec": task_spec,
        "steps": int(report.get("steps") or 0),
        "tokens": int(report.get("tokens") or 0),
        "fresh": fresh_tokens(state),
        "split": token_split(state),
        "compactions": compaction_ends(state),
        "schema_chars": int((report.get("request") or {}).get("schema_chars_median") or 0),
        "wall": float(result.get("wall") or 0.0),
        "result": result,
    }


def _spread(values: list[int]) -> tuple[float, int, int]:
    return (sum(values) / len(values), min(values), max(values)) if values else (0.0, 0, 0)


def _ran(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The rows that were runs: a void one is on neither side of a reading."""
    return [row for row in rows if row.get("outcome") != runs.VOID]


def reading(
    mechanism: str, base: list[dict[str, Any]], off: list[dict[str, Any]], looks: int = 1
) -> str:
    """What the runs can and cannot say about switching `mechanism` off."""
    base, off = _ran(base), _ran(off)
    if not base or not off:
        return "no runs"
    acted = sum(1 for row in base if row["fired"].get(mechanism, 0) > 0)
    if acted == 0:
        return "never acted in the baseline: no reading"
    found = verdict(base, off, looks=looks)
    notes = []
    if found["quality"] in (BETTER, WORSE):
        notes.append(f"quality: {found['quality']} without it")
    if found["cost"] in (CHEAPER, DEARER):
        notes.append(f"cost: {found['cost']} without it")
    return "; ".join(notes) or "within noise"


def _side(rows: list[dict[str, Any]]) -> str:
    void = len(rows) - len(_ran(rows))
    rows = _ran(rows)
    mean_steps, lo_steps, hi_steps = _spread([row["steps"] for row in rows])
    mean_tokens, lo_tokens, hi_tokens = _spread([row["tokens"] for row in rows])
    return (
        f"pass {sum(row['passed'] for row in rows)}/{len(rows)}  "
        f"steps {mean_steps:.0f} [{lo_steps}-{hi_steps}]  "
        f"tokens {mean_tokens:,.0f} [{lo_tokens:,}-{hi_tokens:,}]"
        + (f"  ({void} void: not runs)" if void else "")
    )


def summarize(rows: list[dict[str, Any]], mechanisms: dict[str, str], looks: int = 1) -> list[str]:
    lines = []
    for mechanism, suite in mechanisms.items():
        base = [row for row in rows if row["suite"] == suite and row["variant"] == "baseline"]
        off = [row for row in rows if row["suite"] == suite and row["variant"] == mechanism]
        acted = sum(1 for row in _ran(base) if row["fired"].get(mechanism, 0) > 0)
        still = sum(row["fired"].get(mechanism, 0) for row in off)
        lines.append(f"{mechanism}  ({suite}; acted in {acted}/{len(base)} baseline runs)")
        lines.append(f"    on   {_side(base)}")
        lines.append(f"    off  {_side(off)}" + (f"  !! still acted {still}x - the switch did not take" if still else ""))
        lines.append(f"    -> {reading(mechanism, base, off, looks)}")
    return lines


def _mean(rows: list[dict[str, Any]], key: str) -> float:
    return sum(row["split"][key] for row in rows) / len(rows) if rows else 0.0


def enabled_reading(off: list[dict[str, Any]], on: list[dict[str, Any]], mechanism: str) -> str:
    """What the runs can say about switching an opt-in mechanism ON."""
    off, on = _ran(off), _ran(on)
    if not off or not on:
        return "no runs"
    acted = sum(1 for row in on if row["fired"].get(mechanism, 0) > 0)
    if acted == 0:
        return "never acted when enabled: no reading (the model did not use it)"
    notes = [f"acted in {acted}/{len(on)} enabled runs"]
    quality = verdict(off, on)["quality"]
    if quality in (BETTER, WORSE):
        notes.append(f"quality: {quality} with it")
    for key in ("fresh", "cached"):
        off_values = [row["split"][key] for row in off]
        on_values = [row["split"][key] for row in on]
        if min(on_values) > max(off_values):
            notes.append(f"{key}: more with it")
        elif max(on_values) < min(off_values):
            notes.append(f"{key}: less with it")
    return "; ".join(notes) if len(notes) > 1 else notes[0] + "; within noise"


def _split_side(rows: list[dict[str, Any]]) -> str:
    rows = _ran(rows)
    ends: dict[str, int] = {}
    for row in rows:
        for reason, count in (row.get("compactions") or {}).items():
            ends[reason] = ends.get(reason, 0) + count
    return (
        f"{_side(rows)}  |  fresh {_mean(rows, 'fresh'):,.0f}  cached {_mean(rows, 'cached'):,.0f}  "
        f"output {_mean(rows, 'output'):,.0f}  |  compactions {ends or 'none'}"
    )


def summarize_enabled(rows: list[dict[str, Any]], mechanisms: list[str]) -> list[str]:
    lines = []
    for mechanism in mechanisms:
        off = [row for row in rows if row["suite"] == LONG and row["variant"] == "baseline"]
        on = [row for row in rows if row["suite"] == LONG and row["variant"] == f"+{mechanism}"]
        lines.append(f"{mechanism}  (opt-in; {LONG})")
        lines.append(f"    off  {_split_side(off)}")
        lines.append(f"    on   {_split_side(on)}")
        lines.append(f"    -> {enabled_reading(off, on, mechanism)}")
    return lines


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--model", required=True)
    parser.add_argument("--only", nargs="*", default=[], choices=[*ABLATABLE, *FEATURE_ABLATABLE],
                        help="the mechanisms to switch off, one per variant (default: every mechanism; a feature, `feature:<id>`, only when named)")
    parser.add_argument("--enable", nargs="*", default=[], choices=list(OPT_IN),
                        help="opt-in mechanisms to switch ON, one per variant, on the long session")
    parser.add_argument("--fold-hint-tokens", type=int, default=0,
                        help="the foldable tokens a fold hint needs, for every run that has model_fold on (default: the product's 12000)")
    parser.add_argument("--no-baseline", action="store_true",
                        help="run only the variants (their baselines already run elsewhere)")
    parser.add_argument("--runs", type=int, default=3,
                        help="the most runs a side gets; a variant already called stops sooner")
    parser.add_argument("--window", type=int, default=60_000)
    parser.add_argument("--out", default="")
    args = parser.parse_args(argv)

    out = Path(args.out) if args.out else Path(tempfile.mkdtemp(prefix="ablation-"))
    # `--enable` alone measures only the opt-in mechanisms; with `--only` it adds to them.
    mechanisms = suites(list(args.only or ([] if args.enable else ABLATABLE)))
    suites_used = list(dict.fromkeys([*mechanisms.values(), *([LONG] if args.enable else [])]))
    plan = [(suite, "baseline") for suite in suites_used]
    plan += [(suite, mechanism) for mechanism, suite in mechanisms.items()]
    plan += [(LONG, f"+{mechanism}") for mechanism in args.enable]
    if args.no_baseline:
        plan = [(suite, variant) for suite, variant in plan if variant != "baseline"]
    rows: list[dict[str, Any]] = []
    entries: list[dict[str, Any]] = []
    ceiling = max(1, args.runs)
    called: set[tuple[str, str]] = set()
    for run in range(ceiling):
        open_ = [pair for pair in plan if pair[1] != "baseline" and pair not in called]
        if run and not open_:
            break
        for suite, variant in plan:
            # A baseline is run again only while a variant measured against it is open.
            if (suite, variant) in called or (
                run and variant == "baseline" and not any(s == suite for s, _ in open_)
            ):
                continue
            opt_in = variant.startswith("+")
            ablate = frozenset() if variant == "baseline" or opt_in else frozenset({variant})
            enable = frozenset({variant[1:]}) if opt_in else frozenset()
            name = f"{variant[1:]}-on" if opt_in else variant
            tag = f"{suite}-{name}-{run}"
            state = out / tag
            knobs = {"fold_hint_tokens": args.fold_hint_tokens} if args.fold_hint_tokens else None
            row = asyncio.run(_run(args.model, suite, args.window, state, tag, ablate, enable, knobs))
            entry = runs.row(
                suite=LONG if suite == LONG else "probe", task="session" if suite == LONG else suite,
                variant=variant, model=args.model, passed=row["passed"], state=state, tag=tag,
                steps=row["steps"], tokens=row["tokens"], fresh=row["fresh"], wall=row["wall"],
                task_spec=row["spec"],
            )
            runs.record([entry])
            entries.append(entry)
            # In a baseline the count is how often each mechanism acted; in a variant,
            # how often ITS mechanism still acted (0 proves the switch took). An opt-in
            # variant counts how often the mechanism it switched on acted.
            features = tuple(name for name in mechanisms if name in FEATURE_ABLATABLE)
            names = (*ABLATABLE, *OPT_IN, *features) if variant == "baseline" else (variant.lstrip("+"),)
            row.update(suite=suite, variant=variant, run=run, outcome=entry["outcome"],
                       fired={name: fired(state, name) for name in names})
            rows.append(row)
            why = f" ({entry['why']})" if entry["why"] else ""
            print(f"  done: {tag} {entry['outcome']}{why} steps={row['steps']} tokens={row['tokens']:,}", flush=True)
        # One run a side on one task never clears the interval, so this cannot stop a
        # comparison at its first round; nothing here needs to say so twice.
        for suite, variant in open_:
            base = [r for r in rows if r["suite"] == suite and r["variant"] == "baseline"]
            side = [r for r in rows if r["suite"] == suite and r["variant"] == variant]
            if settled(verdict(base, side, looks=ceiling)):
                called.add((suite, variant))
    (out / "ablation.json").write_text(json.dumps(rows, indent=1, default=str), encoding="utf-8")
    print("\n".join([*summarize(rows, mechanisms, ceiling), *summarize_enabled(rows, list(args.enable))]))
    print(f"logs and results: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
