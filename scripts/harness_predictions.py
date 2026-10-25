"""Harness predictions: a change says what it will move in the session store, and the store judges it.

An ADR's "Verification" section proves the code does what was written; it does not
prove the change did anything for the sessions people run. Agentic Harness Engineering
(arXiv 2604.25850) closes that gap with what it calls decision observability: every harness
edit ships with a self-declared prediction, the next round's outcomes confirm or refute it,
and a refuted edit is reverted. This is that contract for a harness whose "next round" is the
real session store instead of a benchmark run.

`docs/decisions/predictions.json` is the ledger — one entry per claim:

    id              a stable name
    adr             the decision it belongs to (or null, with `change` naming the commit)
    shipped         when the change reached the running app (ISO date/time), or null while
                    it is committed but not live
    baseline_since  where the "before" window starts; it ends at `shipped`
    metric / per    a `harness_review` count and the count it is a share of
    expect          zero | nonzero | down | up
    min_n           how many `per` the "after" window needs before the store may answer
    at_risk         counts the change could make WORSE: [{metric, per}]; judged as "not up"
    claim, evidence one sentence each: what will move, and the numbers it was decided on
    resolution      for a refuted claim: what was done about it

Verdicts: `confirmed`, `refuted`, `too early` (not enough sessions since), `not live`.
A refuted claim is the useful one. Counts only — nothing from a session is printed.

A verdict is `confounded_by` the other live claims on the same metric that shipped inside its
"after" window (or within a day before it): the store moved once for all of them and cannot say
which change moved it. The verdict is still given; separating them is an ablation's job
(`benchmarks.ablation`), not the store's. Taken from Harness² (arXiv 2610.14224), whose edit rows
call an effect `confounded` when no run isolates one change.

    .venv\\Scripts\\python.exe scripts\\harness_predictions.py [<state_dir>] [--json] [--strict]

`--strict` exits 1 when a claim is refuted and has no `resolution`.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any, Optional

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

LEDGER = REPO / "docs" / "decisions" / "predictions.json"
#: A share has to move by this much, relative to where it was, to count as moved. Real use
#: is not a controlled run: a claim that 40% became 38% has not been shown.
MARGIN = 0.2
EXPECTATIONS = ("zero", "nonzero", "down", "up")


def _review_module():
    spec = importlib.util.spec_from_file_location("harness_review", REPO / "scripts" / "harness_review.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


def _share(part: int, whole: int) -> Optional[float]:
    return part / whole if whole else None


def verdict(
    expect: str, before: tuple[int, int], after: tuple[int, int], min_n: int, margin: float = MARGIN
) -> str:
    """What the store says about one claim. `before`/`after` are `(metric, per)` counts."""
    seen, sample = after
    if expect == "zero":
        if seen:
            return "refuted"  # one is enough: the claim was "never"
        return "confirmed" if sample >= min_n else "too early"
    if expect == "nonzero":
        if seen:
            return "confirmed"
        return "refuted" if sample >= min_n else "too early"
    if sample < min_n:
        return "too early"
    was, now = _share(*before), _share(*after)
    if was is None or now is None:
        return "too early"  # no baseline to move from
    if expect == "down":
        return "confirmed" if now <= was * (1 - margin) else "refuted"
    return "confirmed" if now >= was * (1 + margin) else "refuted"


def regressed(before: tuple[int, int], after: tuple[int, int], min_n: int, margin: float = MARGIN) -> str:
    """An at-risk count: `worse` when its share rose past the margin, else `held`."""
    if after[1] < min_n:
        return "too early"
    was, now = _share(*before), _share(*after)
    if now is None:
        return "too early"
    if was is None or was == 0:
        return "worse" if after[0] else "held"
    return "worse" if now >= was * (1 + margin) else "held"


def load(path: Path = LEDGER) -> list[dict[str, Any]]:
    entries = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(entries, list):
        raise ValueError("the ledger is a list of claims")
    seen: set[str] = set()
    for entry in entries:
        for key in ("id", "claim", "evidence", "metric", "per", "expect", "baseline_since"):
            if not entry.get(key):
                raise ValueError(f"claim {entry.get('id') or '?'} has no `{key}`")
        if entry["expect"] not in EXPECTATIONS:
            raise ValueError(f"claim {entry['id']}: `expect` is one of {', '.join(EXPECTATIONS)}")
        if entry["id"] in seen:
            raise ValueError(f"claim {entry['id']} appears twice")
        seen.add(entry["id"])
    return entries


def judge(entries: list[dict[str, Any]], logs: list[Path], review, stamp) -> list[dict[str, Any]]:
    """One row per claim: the counts on each side of `shipped` and the verdict."""
    rows = []
    windows: dict[tuple, Any] = {}

    def counts(since: Optional[float], until: Optional[float]):
        key = (since, until)
        if key not in windows:
            windows[key] = review(logs, since, until)[0]
        return windows[key]

    for entry in entries:
        min_n = int(entry.get("min_n") or 10)
        row = {
            "id": entry["id"], "adr": entry.get("adr"), "claim": entry["claim"],
            "expect": entry["expect"], "metric": entry["metric"], "per": entry["per"],
            "resolution": entry.get("resolution", ""),
        }
        if not entry.get("shipped"):
            rows.append({**row, "verdict": "not live"})
            continue
        shipped = stamp(entry["shipped"])
        was = counts(stamp(entry["baseline_since"]), shipped)
        now = counts(shipped, stamp(entry.get("until") or ""))
        before = (was[entry["metric"]], was[entry["per"]])
        after = (now[entry["metric"]], now[entry["per"]])
        risks = []
        for risk in entry.get("at_risk") or []:
            risk_before = (was[risk["metric"]], was[risk["per"]])
            risk_after = (now[risk["metric"]], now[risk["per"]])
            risks.append({
                "metric": risk["metric"], "per": risk["per"],
                "before": risk_before, "after": risk_after,
                "verdict": regressed(risk_before, risk_after, min_n),
            })
        rows.append({
            **row, "before": before, "after": after,
            "verdict": verdict(entry["expect"], before, after, min_n),
            "at_risk": risks,
            "confounded_by": confounders(entry, entries, stamp),
        })
    return rows


#: A change shipped this close before another is inside its window too: both reach the same
#: sessions from the same restart, more or less.
_SAME_RESTART_S = 86_400


def confounders(entry: dict[str, Any], entries: list[dict[str, Any]], stamp) -> list[str]:
    """The other live claims on `entry`'s metric that shipped inside its "after" window."""
    start = stamp(entry["shipped"])
    end = stamp(entry.get("until") or "")
    found = []
    for other in entries:
        if other is entry or not other.get("shipped") or other.get("metric") != entry["metric"]:
            continue
        at = stamp(other["shipped"])
        if at is None or start is None:
            continue
        if at >= start - _SAME_RESTART_S and (end is None or at < end):
            found.append(other["id"])
    return found


def _fraction(pair: tuple[int, int]) -> str:
    share = _share(*pair)
    return f"{pair[0]}/{pair[1]}" + (f" ({share:.0%})" if share is not None else "")


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("paths", nargs="*")
    parser.add_argument("--ledger", default=str(LEDGER))
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args(argv)
    module = _review_module()
    if not args.paths:
        from harness.safety.secrets import state_dir

        args.paths = [str(state_dir())]
    rows = judge(load(Path(args.ledger)), module._logs(args.paths), module.review, module.stamp)
    if args.json:
        print(json.dumps(rows, indent=1))
    else:
        for row in rows:
            where = f"ADR {row['adr']}" if row.get("adr") else "no ADR"
            print(f"{row['verdict'].upper():10} {row['id']}  ({where})")
            print(f"           {row['claim']}")
            if "before" in row:
                print(
                    f"           {row['metric']} per {row['per']}, expected {row['expect']}: "
                    f"{_fraction(row['before'])} before -> {_fraction(row['after'])} after"
                )
            if row.get("confounded_by"):
                print(
                    "           confounded by: " + ", ".join(row["confounded_by"])
                    + " (same metric, same window: an ablation separates them)"
                )
            for risk in row.get("at_risk") or []:
                print(
                    f"           at risk, {risk['metric']} per {risk['per']}: "
                    f"{_fraction(risk['before'])} -> {_fraction(risk['after'])}: {risk['verdict']}"
                )
            if row["verdict"] == "refuted" and row["resolution"]:
                print(f"           resolution: {row['resolution']}")
    open_refutals = [r for r in rows if r["verdict"] == "refuted" and not r["resolution"]]
    return 1 if args.strict and open_refutals else 0


if __name__ == "__main__":
    raise SystemExit(main())
