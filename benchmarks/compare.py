"""Compare — whether one harness is better than another, past its own noise.

The unchanged harness does not score the same twice: on the rows the evals had written by
2026-10-06 the same harness, task and model both passed and failed in 4 of 10 baseline
groups, and the long session passed 7 of 9. So a difference is called only when the runs
can carry it, and what they cannot carry is said in runs:

    better / worse   the interval of the difference in pass rate leaves zero out
    same             the whole interval lies inside the tolerance (default 10 points)
    undecided        neither, with how many more runs a side would settle it

Two sides are compared on the tasks both ran (same suite, task, task digest and model);
`void` runs (`benchmarks/runs.py`) are on neither side. Where there are many tasks the
tasks are the sample: the interval is over each task's difference, so one run a task is
already a reading. Where there are few, the runs are the sample (Agresti-Caffo, which
does not call 3 of 3 a certainty). A side run round after round and looked at each time is
held to a wider interval (Pocock), so stopping early does not manufacture a result.

Cost is read the same way, on the tokens one run of the suite takes: `cheaper`, `dearer`,
`same` (inside the tolerance, whichever side of it), or no reading when a side ran a task
once.

What the gate asks of a harness change: keep it when it is better, or the same and not
dearer; reopen it when it is worse; run more when it is undecided. Where the rows hold more
than one model, each model is called on its own too, and a change worse on any one is
reopened whatever the models together show (`per_model`): a harness edit's gain depends on
the model running it (HarnessDev, arXiv 2609.01437), and a pooled rate hides the one it hurt.

    .venv\\Scripts\\python.exe -m benchmarks.compare                       what the ledger holds
    .venv\\Scripts\\python.exe -m benchmarks.compare --base <version> [--head <version>]
        [--suite history] [--model <provider:model>] [--tolerance 0.1]

A side is `<version>` or `<version>:<variant>`; a version may be a prefix, and `HEAD` is
this checkout as it stands. Reads the ledger only: spends nothing.
"""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path
from typing import Any, Optional

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from benchmarks import runs  # noqa: E402

BETTER, WORSE, SAME, UNDECIDED, NO_RUNS = "better", "worse", "same", "undecided", "no runs"
CHEAPER, DEARER, NO_READING = "cheaper", "dearer", "no reading"
TOLERANCE = 0.10
#: Fewer paired tasks than this and the runs, not the tasks, are the sample.
MANY_TASKS = 8
#: Two-sided 5% critical values for a result looked at up to N times (Pocock, 1977).
_POCOCK = (1.960, 2.178, 2.289, 2.361, 2.413, 2.453, 2.485, 2.512, 2.535, 2.555)


def critical(looks: int, freedom: Optional[int] = None) -> float:
    """The half-width multiplier: normal for a single look, wider for a result looked at
    `looks` times, and Student's when the spread itself came from `freedom` + 1 values."""
    z = _POCOCK[min(max(looks, 1), len(_POCOCK)) - 1]
    if freedom is None:
        return z
    if freedom < 1:
        return math.inf
    # Cornish-Fisher expansion of the t quantile: within 2% from four degrees of freedom up.
    return z + (z**3 + z) / (4 * freedom) + (5 * z**5 + 16 * z**3 + 3 * z) / (96 * freedom**2)


def _outcome(row: dict[str, Any]) -> str:
    return row.get("outcome") or (runs.PASSED if row.get("passed") else runs.FAILED)


def _key(row: dict[str, Any]) -> tuple:
    return (row.get("suite"), row.get("task"), row.get("spec"), row.get("model"))


def _by_task(rows: list[dict[str, Any]]) -> dict[tuple, list[dict[str, Any]]]:
    tasks: dict[tuple, list[dict[str, Any]]] = {}
    for row in rows:
        if _outcome(row) != runs.VOID:
            tasks.setdefault(_key(row), []).append(row)
    return tasks


def _rate(rows: list[dict[str, Any]]) -> float:
    return sum(1 for row in rows if _outcome(row) == runs.PASSED) / len(rows)


def _quality(pairs: list[tuple[list, list]], looks: int) -> tuple[float, float, float]:
    """(difference, low, high) of head minus base in pass rate, over the paired tasks."""
    count = len(pairs)
    raw = sum(_rate(head) - _rate(base) for base, head in pairs) / count
    if count >= MANY_TASKS:
        each = [_rate(head) - _rate(base) for base, head in pairs]
        spread = math.sqrt(sum((d - raw) ** 2 for d in each) / (count - 1))
        half = critical(looks, count - 1) * spread / math.sqrt(count)
        if spread == 0.0:
            # Every task moved alike (usually: none moved). No spread seen is not no spread:
            # by the rule of three, tasks that would differ can still be 3 in `count`.
            half = 3.0 / count
        return raw, raw - half, raw + half
    centre = variance = 0.0
    for base, head in pairs:
        shares = []
        for side in (base, head):
            share = (sum(1 for row in side if _outcome(row) == runs.PASSED) + 1) / (len(side) + 2)
            shares.append(share)
            variance += share * (1 - share) / (len(side) + 2)
        centre += shares[1] - shares[0]
    centre /= count
    half = critical(looks) * math.sqrt(variance) / count
    return raw, centre - half, centre + half


def _cost(
    pairs: list[tuple[list, list]], looks: int, measure: str = "tokens"
) -> Optional[tuple[float, float, float]]:
    """(ratio, low, high) of head over base in `measure` (tokens, or steps) for one run of
    the paired tasks, or None when a side ran a task once: one run has no spread to read."""
    totals = [0.0, 0.0]
    variances = [0.0, 0.0]
    freedom = 0
    for pair in pairs:
        for side, group in enumerate(pair):
            tokens = [float(row.get(measure) or 0) for row in group]
            if len(tokens) < 2:
                return None
            mean = sum(tokens) / len(tokens)
            totals[side] += mean
            variances[side] += sum((t - mean) ** 2 for t in tokens) / (len(tokens) - 1) / len(tokens)
            freedom += len(tokens) - 1
    if min(totals) <= 0:
        return None
    ratio = totals[1] / totals[0]
    spread = math.sqrt(variances[0] / totals[0] ** 2 + variances[1] / totals[1] ** 2)
    half = critical(looks, freedom) * spread
    return ratio, ratio * math.exp(-half), ratio * math.exp(half)


def verdict(
    base: list[dict[str, Any]],
    head: list[dict[str, Any]],
    tolerance: float = TOLERANCE,
    looks: int = 1,
    measure: str = "tokens",
) -> dict[str, Any]:
    """What `head`'s runs show against `base`'s, on the tasks both ran. Cost is read on
    `measure`: the tokens a run took, or its `steps`."""
    base_tasks, head_tasks = _by_task(base), _by_task(head)
    shared = sorted(set(base_tasks) & set(head_tasks), key=str)
    pairs = [(base_tasks[key], head_tasks[key]) for key in shared]
    out: dict[str, Any] = {
        "quality": NO_RUNS,
        "cost": NO_READING,
        "tasks": len(pairs),
        "unpaired": len(set(base_tasks) ^ set(head_tasks)),
        "runs": (sum(len(b) for b, _ in pairs), sum(len(h) for _, h in pairs)),
        "void": (
            sum(1 for row in base if _outcome(row) == runs.VOID),
            sum(1 for row in head if _outcome(row) == runs.VOID),
        ),
        "tolerance": tolerance,
        "measure": measure,
    }
    if not pairs:
        return out
    rates = [(_rate(b), _rate(h)) for b, h in pairs]
    out["passed"] = (sum(r for r, _ in rates) / len(pairs), sum(r for _, r in rates) / len(pairs))
    out["everywhere"] = sum(1 for pair in rates if pair == (1.0, 1.0))
    out["nowhere"] = sum(1 for pair in rates if pair == (0.0, 0.0))
    difference, low, high = _quality(pairs, looks)
    out.update(difference=difference, low=low, high=high)
    if low > 0:
        out["quality"] = BETTER
    elif high < 0:
        out["quality"] = WORSE
    elif low >= -tolerance and high <= tolerance:
        out["quality"] = SAME
    else:
        out["quality"] = UNDECIDED
        # The half-width shrinks with the square root of the runs: how many more a side
        # would bring it to what there is to see (the difference, else the tolerance).
        half = (high - low) / 2
        target = abs(difference) if abs(difference) > tolerance else max(tolerance - abs(difference), tolerance / 2)
        per_task = sum(out["runs"]) / (2 * len(pairs))
        out["more"] = max(1, math.ceil(per_task * (half / target) ** 2 - per_task))
    cost = _cost(pairs, looks, measure)
    if cost is not None:
        ratio, cost_low, cost_high = cost
        out.update(ratio=ratio, ratio_low=cost_low, ratio_high=cost_high)
        # A bill inside the tolerance is the same bill even where steady runs can show which
        # side of it lies: a regression in quality matters at any size, a 2% one in cost does not.
        if cost_low >= 1 - tolerance and cost_high <= 1 + tolerance:
            out["cost"] = SAME
        elif cost_high < 1:
            out["cost"] = CHEAPER
        elif cost_low > 1:
            out["cost"] = DEARER
        else:
            out["cost"] = UNDECIDED
    return out


def per_model(
    base: list[dict[str, Any]],
    head: list[dict[str, Any]],
    tolerance: float = TOLERANCE,
    looks: int = 1,
    measure: str = "tokens",
) -> dict[str, dict[str, Any]]:
    """`verdict` for each model both sides ran. A harness change can help one model and hurt
    another (HarnessDev, arXiv 2609.01437: gains "depend strongly on the model executing the
    harness"), and a pooled pass rate averages the two into "same"."""
    models = sorted({str(r.get("model")) for r in base} & {str(r.get("model")) for r in head})
    return {
        model: verdict(
            [r for r in base if str(r.get("model")) == model],
            [r for r in head if str(r.get("model")) == model],
            tolerance,
            looks,
            measure,
        )
        for model in models
    }


def worse_on(models: dict[str, dict[str, Any]]) -> list[str]:
    """The models a change made worse: the gate reopens it for any one of them, as a
    proposal that improved one split and degraded the other is refused (Self-Harness, arXiv
    2606.09498)."""
    return [model for model, found in models.items() if found["quality"] == WORSE]


def settled(found: dict[str, Any]) -> bool:
    """Whether more runs could still change the call on quality."""
    return found["quality"] in (BETTER, WORSE, SAME)


def call(found: dict[str, Any]) -> str:
    """What the gate says of the head side, as one sentence."""
    quality, cost = found["quality"], found["cost"]
    if quality == NO_RUNS:
        return "no task was run on both sides"
    if quality == WORSE:
        return "reopen: worse on these tasks"
    if quality == BETTER:
        return "keep: better on these tasks" + (", and dearer" if cost == DEARER else "")
    points = round(found["tolerance"] * 100)
    if quality == SAME:
        if cost == DEARER:
            return f"reopen: no gain past {points} points, and dearer"
        return f"keep: no difference past {points} points" + (", and cheaper" if cost == CHEAPER else "")
    more = found.get("more", 0)
    if more > 50:
        return "undecided: these tasks cannot tell the two apart at any size worth running"
    return f"undecided: about {more} more run(s) a task on each side"


def separates(rows: list[dict[str, Any]], owners: dict[str, str]) -> list[tuple[str, str, int, int, int, int]]:
    """Failure shapes seen in more failed runs than passed ones, widest gap first:
    `(detector, owner, failed runs with it, failed runs, passed runs with it, passed runs)`.
    Where a failure points, by contrast with the runs that did not fail."""
    failed = [row for row in rows if _outcome(row) == runs.FAILED]
    passed = [row for row in rows if _outcome(row) == runs.PASSED]
    if not failed:
        return []
    found = []
    for name in sorted({name for row in failed for name in row.get("signs") or {}}):
        in_failed = sum(1 for row in failed if (row.get("signs") or {}).get(name))
        in_passed = sum(1 for row in passed if (row.get("signs") or {}).get(name))
        gap = in_failed / len(failed) - (in_passed / len(passed) if passed else 0.0)
        if in_failed >= 2 and gap >= 0.25:
            found.append((gap, name, owners.get(name, "?"), in_failed, len(failed), in_passed, len(passed)))
    return [entry[1:] for entry in sorted(found, reverse=True)]


def render(found: dict[str, Any]) -> list[str]:
    lines = [
        f"tasks on both sides {found['tasks']} (on one side only {found['unpaired']}) | "
        f"runs {found['runs'][0]} vs {found['runs'][1]} | void {found['void'][0]} vs {found['void'][1]}"
    ]
    if found["quality"] == NO_RUNS:
        return lines + [f"-> {call(found)}"]
    told = found["tasks"] - found["everywhere"] - found["nowhere"]
    lines.append(
        f"pass rate {found['passed'][0]:.0%} -> {found['passed'][1]:.0%}: {found['difference']:+.0%} "
        f"[{found['low']:+.0%}, {found['high']:+.0%}] {found['quality']} | "
        f"passed on both sides every time {found['everywhere']}, never {found['nowhere']}, telling {told}"
    )
    measure = found.get("measure", "tokens")
    if "ratio" in found:
        lines.append(
            f"{measure} x{found['ratio']:.2f} [{found['ratio_low']:.2f}, {found['ratio_high']:.2f}] {found['cost']}"
        )
    else:
        lines.append(f"{measure}: no reading (a side ran a task once)")
    return lines + [f"-> {call(found)}"]


def side(rows: list[dict[str, Any]], selector: str) -> tuple[str, list[dict[str, Any]]]:
    """`<version>[:<variant>]` -> its label and its rows. The version may be a prefix."""
    version, _, variant = selector.partition(":")
    variant = variant or "baseline"
    if version.upper() == "HEAD":
        version = runs.harness_version()
    matched = sorted({str(r.get("version")) for r in rows if str(r.get("version", "")).startswith(version)})
    # A whole version names itself even when it is the commit an uncommitted one (`<commit>+<digest>`)
    # starts with: the base of an A/B and its head share that prefix.
    if version in matched:
        matched = [version]
    if len(matched) > 1:
        raise SystemExit(f"{version!r} names more than one version: {', '.join(matched)}")
    picked = [r for r in rows if r.get("version") in matched and r.get("variant") == variant]
    return f"{matched[0] if matched else version}:{variant}", picked


def holdings(rows: list[dict[str, Any]]) -> list[str]:
    groups: dict[tuple, list[dict[str, Any]]] = {}
    for row in rows:
        groups.setdefault(
            (row.get("version"), row.get("suite"), row.get("variant"), row.get("model")), []
        ).append(row)
    lines = [f"{'version':22} {'suite':8} {'variant':22} {'model':34} tasks  pass  fail  void"]
    for (version, suite, variant, model), group in sorted(groups.items(), key=lambda kv: min(r["t"] for r in kv[1])):
        tally = {name: sum(1 for r in group if _outcome(r) == name) for name in (runs.PASSED, runs.FAILED, runs.VOID)}
        lines.append(
            f"{str(version)[:22]:22} {str(suite)[:8]:8} {str(variant)[:22]:22} {str(model)[:34]:34} "
            f"{len({r.get('task') for r in group}):>5} {tally[runs.PASSED]:>5} {tally[runs.FAILED]:>5} {tally[runs.VOID]:>5}"
        )
    return lines


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--base", default="")
    parser.add_argument("--head", default="HEAD")
    parser.add_argument("--suite", default="")
    parser.add_argument("--model", default="")
    parser.add_argument("--tolerance", type=float, default=TOLERANCE)
    parser.add_argument("--ledger", default="", help="another ledger file than the state dir's")
    args = parser.parse_args(argv)

    rows = runs.load(Path(args.ledger) if args.ledger else None)
    rows = [
        r for r in rows
        if (not args.suite or r.get("suite") == args.suite) and (not args.model or r.get("model") == args.model)
    ]
    if not args.base:
        print("\n".join(holdings(rows)) if rows else "the ledger holds no runs yet")
        return 0
    base_label, base = side(rows, args.base)
    head_label, head = side(rows, args.head)
    found = verdict(base, head, args.tolerance)
    models = per_model(base, head, args.tolerance)
    print(f"{base_label}  ->  {head_label}")
    if len(models) > 1:
        for model, alone in models.items():
            print(f"\n{model}")
            print("\n".join(render(alone)))
        print("\nall models")
    print("\n".join(render(found)))
    worse = worse_on(models) if len(models) > 1 else []
    if worse:
        print(f"-> reopen: worse on {', '.join(worse)}, whatever the models together show")
    owners = getattr(runs._review(), "OWNER", {})
    for label, group in ((base_label, base), (head_label, head)):
        for name, owner, in_failed, failed, in_passed, passed in separates(group, owners)[:5]:
            print(f"  {label}: {name} ({owner}) in {in_failed}/{failed} failed runs, {in_passed}/{passed} passed")
    if worse:
        return 1
    return 0 if found["quality"] in (BETTER, SAME) else 1 if found["quality"] == WORSE else 2


if __name__ == "__main__":
    raise SystemExit(main())
