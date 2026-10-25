"""The harness reviewing itself from its own logs, for the Metrics page (2026-10-14).

`scripts/harness_review.py` counts the known failure shapes and `scripts/harness_predictions.py`
judges each claim in `docs/decisions/predictions.json` on the two sides of its ship date. Both read
the session logs and spend no tokens, and both take minutes over a real store. The server never
runs them in its own process, where the work would hold the interpreter lock and stall the turns
streaming beside it: it starts this module as a child (`python -m harness.observability.self_review`,
or `reeve-server --self-review` in a frozen build), which runs them once and prints one JSON object.

A checkout finds the scripts and the ledger beside the package. A frozen build carries copies under
`reeve_review/` (packaging/reeve-server.spec). The ledger is the repository's own record and a tree
may not have it (the public one does not): the shapes are still counted, and no claim is judged.
Nothing here is a second rule: the counts are the review's, the verdicts the ledger's own `judge`.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import time
from pathlib import Path
from types import ModuleType
from typing import Any, Optional

#: The window the failure shapes are counted over.
REVIEW_DAYS = 30


def sources() -> Optional[dict[str, Optional[Path]]]:
    """Where the review script and the predictions script are, or None; the ledger beside them, or
    None when the tree has none."""
    roots = []
    bundle = getattr(sys, "_MEIPASS", None)
    if bundle:
        roots.append((Path(bundle) / "reeve_review", Path(bundle) / "reeve_review"))
    repo = Path(__file__).resolve().parents[2]
    roots.append((repo / "scripts", repo / "docs" / "decisions"))
    for scripts, docs in roots:
        review, predictions, ledger = scripts / "harness_review.py", scripts / "harness_predictions.py", docs / "predictions.json"
        if review.is_file() and predictions.is_file():
            return {"review": review, "predictions": predictions, "ledger": ledger if ledger.is_file() else None}
    return None


def ships(ledger: Optional[Path]) -> list[dict[str, Any]]:
    """The days a harness change went live, from the ledger's `shipped`, oldest first, with the
    ADRs that shipped that day. Cheap: one small file, read in the server itself."""
    if ledger is None:
        return []
    try:
        entries = json.loads(ledger.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    days: dict[str, set[str]] = {}
    for entry in entries if isinstance(entries, list) else []:
        shipped = str((entry or {}).get("shipped") or "")[:10]
        if len(shipped) == 10:
            adrs = days.setdefault(shipped, set())
            if entry.get("adr"):
                adrs.add(str(entry["adr"]))
    return [{"day": day, "adrs": sorted(days[day])} for day in sorted(days)]


def _module(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


def _share(part: int, whole: int) -> Optional[float]:
    return round(part / whole, 4) if whole else None


def run(state: Path, *, now: Optional[float] = None) -> dict[str, Any]:
    """The failure shapes of the last `REVIEW_DAYS` and every claim's verdict, counts only."""
    found = sources()
    if found is None:
        return {"error": "the review scripts are not in this build"}
    started = time.time() if now is None else now
    review = _module(found["review"], "harness_review")
    predictions = _module(found["predictions"], "harness_predictions")
    logs = review._logs([str(state)])
    counts, _refs = review.review(logs, started - REVIEW_DAYS * 86400, None)
    per = getattr(review, "PER", {})
    shapes = []
    for name in review.DETECTORS:
        count = int(counts.get(name) or 0)
        if not count:
            continue
        base = per.get(name)
        shapes.append({
            "name": name,
            "owner": review.OWNER.get(name, ""),
            "count": count,
            "per": base,
            "of": int(counts.get(base) or 0) if base else None,
            "share": _share(count, int(counts.get(base) or 0)) if base else None,
        })
    shapes.sort(key=lambda s: (s["share"] is None, -(s["share"] or 0), -s["count"]))
    rows = predictions.judge(predictions.load(found["ledger"]), logs, review.review, review.stamp) if found["ledger"] else []
    verdicts: dict[str, int] = {}
    for row in rows:
        verdicts[row["verdict"]] = verdicts.get(row["verdict"], 0) + 1
    refuted = [
        {
            "id": row["id"], "adr": row.get("adr"), "claim": row["claim"], "metric": row["metric"],
            "per": row["per"], "expect": row["expect"], "before": row.get("before"), "after": row.get("after"),
            "resolution": row.get("resolution") or "",
        }
        for row in rows
        if row["verdict"] == "refuted"
    ]
    return {
        "at": time.time(),
        "took_s": round(time.time() - started, 1),
        "days": REVIEW_DAYS,
        "sessions": int(counts.get("sessions") or 0),
        "shapes": shapes,
        "claims": {"verdicts": verdicts, "refuted": refuted},
    }


def main(argv: Optional[list[str]] = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        from harness.safety.secrets import state_dir

        args = [str(state_dir())]
    try:
        out = run(Path(args[0]))
    except Exception as exc:  # noqa: BLE001 — the parent reads the error; nothing else may print
        # The class only: a message can carry a path or a line of a session.
        out = {"error": type(exc).__name__}
    sys.stdout.write(json.dumps(out) + "\n")
    sys.stdout.flush()
    return 0 if "error" not in out else 1


if __name__ == "__main__":
    raise SystemExit(main())
