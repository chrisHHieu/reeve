"""One eval run as a row: which harness ran which task, and whether it was a run at all.

Every eval here (`ablation`, `probe_eval`, `harness_eval`, `history_eval`) ends in the same
question: is this harness better or worse than that one? The answers could not be
compared, for three reasons measured on the 160 rows the evals had written by 2026-10-06:

    no version   a row named its suite and variant, never the harness that ran: last
                 week's baseline and today's were one group
    no outcome   24 runs that never reached a model (no balance, no key: 0 tokens) were
                 stored as failed tasks, in the same column as a wrong answer
    no reason    a failed run said only that it failed

A row here carries the three. `version` is the commit of `harness/` plus a digest of
whatever is uncommitted in it. `outcome` is `passed`, `failed`, or `void`: a run the
account or the network ended, one no model answered, or one that answered on another
model, measures nothing and is kept out of every rate. `signs` are the failure shapes
`scripts/harness_review.py` counts in the run's own session log, each owned by a part of
the harness, so failed runs can be set against passed ones (`benchmarks/compare.py`).

Rows are appended to `<state>/evals/runs.jsonl`. Writing one is best-effort: a row that
cannot be written never costs the run that produced it.
"""

from __future__ import annotations

import functools
import hashlib
import importlib.util
import json
import subprocess
import time
from pathlib import Path
from typing import Any, Iterable, Optional

REPO = Path(__file__).resolve().parents[1]
#: The engine's code, which a version digests.
HARNESS = ("harness",)
#: `turn/end {error}` classes that say the account or the network ended the turn
#: (`providers/turn_errors.py`), and the ones that say this harness did: the request it
#: built was refused, or its own code raised.
PROVIDER_ENDED = ("no_key", "no_quota", "no_access", "rate_limit", "overloaded", "transport")
HARNESS_ENDED = ("bad_request", "code", "crashed")
PASSED, FAILED, VOID = "passed", "failed", "void"


def _git(*args: str, repo: Path) -> str:
    try:
        return subprocess.run(
            ["git", *args], cwd=repo, capture_output=True, text=True, encoding="utf-8",
            errors="replace", check=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        return ""


def version_of(repo: Path) -> str:
    """The harness in `repo`: its commit, and `+<digest>` when `harness/` holds changes the
    commit does not. Two uncommitted states are two versions; `unknown` outside a checkout."""
    commit = _git("rev-parse", "--short=12", "HEAD", repo=repo).strip()
    if not commit:
        return "unknown"
    digest = hashlib.sha1()
    changed = _git("diff", "HEAD", "--", *HARNESS, repo=repo)
    digest.update(changed.encode("utf-8", "replace"))
    new = _git("ls-files", "--others", "--exclude-standard", "--", *HARNESS, repo=repo).split("\n")
    for name in sorted(filter(None, new)):
        digest.update(name.encode("utf-8", "replace"))
        try:
            digest.update((repo / name).read_bytes())
        except OSError:
            pass
    if not changed and not any(new):
        return commit
    return f"{commit}+{digest.hexdigest()[:8]}"


@functools.lru_cache(maxsize=1)
def harness_version() -> str:
    """This checkout's version, read once: a run in progress must not change sides because
    a file was saved under it."""
    return version_of(REPO)


def spec(*parts: Any) -> str:
    """A digest of what a task IS (its files, its prompt, the window it runs under), so two
    rows are paired only when they ran the same work. A directory stands for its files."""
    digest = hashlib.sha1()
    for part in parts:
        if isinstance(part, Path):
            for path in sorted(p for p in part.rglob("*") if p.is_file()):
                digest.update(path.relative_to(part).as_posix().encode("utf-8"))
                digest.update(path.read_bytes())
        else:
            digest.update(json.dumps(part, sort_keys=True, default=str).encode("utf-8"))
    return digest.hexdigest()[:8]


def how_it_ran(state: Path, tag: str) -> dict[str, Any]:
    """What the run's own session log says happened: how many model answers came back, how
    each turn ended, and whether an answer came from a model other than the one asked."""
    answers = 0
    reasons: list[str] = []
    errors: list[str] = []
    fallback = False
    try:
        lines = (state / "conversations" / f"{tag}.events.jsonl").read_text(
            encoding="utf-8", errors="replace"
        ).splitlines()
    except OSError:
        lines = []
    for line in lines:
        try:
            event = json.loads(line)
        except ValueError:
            continue
        kind = event.get("type")
        if kind == "assistant/message" and not event.get("partial"):
            answers += 1  # with or without `usage`: not every provider reports it
        elif kind == "turn/end":
            reasons.append(str(event.get("reason") or ""))
            if event.get("error"):
                errors.append(str(event["error"]))
        elif kind == "llm/fallback":
            fallback = True
    return {"answers": answers, "reasons": reasons, "errors": errors, "fallback": fallback}


def outcome(passed: bool, ran: dict[str, Any]) -> tuple[str, str]:
    """`(outcome, why)` for one run. Void comes first where the row would name the wrong
    model; a passed check is otherwise the truth, whatever ended the turn after it."""
    if ran["fallback"]:
        return VOID, "answered on another model"
    if passed:
        return PASSED, ""
    for error in ran["errors"]:
        if error in PROVIDER_ENDED:
            return VOID, error
    # No answer at all is not a run, unless this harness is why: a first request the
    # provider refused as malformed is the failure an eval exists to catch.
    if not ran["answers"] and not any(error in HARNESS_ENDED for error in ran["errors"]):
        return VOID, "no model answer"
    return FAILED, ""


def _review() -> Any:
    spec_ = importlib.util.spec_from_file_location(
        "harness_review", REPO / "scripts" / "harness_review.py"
    )
    module = importlib.util.module_from_spec(spec_)
    spec_.loader.exec_module(module)  # type: ignore[union-attr]
    return module


def signs(state: Path) -> dict[str, int]:
    """The failure shapes the harness review counts in this run's logs (children included),
    by detector. Empty when the review cannot read them: a sign is evidence, not a gate."""
    try:
        review = _review()
        counts, _ = review.review(review._logs([str(state)]))
        return {name: int(counts[name]) for name in review.DETECTORS if counts[name]}
    except Exception:  # noqa: BLE001 — a row is worth more than its signs
        return {}


def row(
    *,
    suite: str,
    task: str,
    variant: str,
    model: str,
    passed: bool,
    state: Path,
    tag: str,
    steps: int = 0,
    tokens: int = 0,
    fresh: int = 0,
    wall: float = 0.0,
    task_spec: str = "",
    flags: Iterable[str] = (),
) -> dict[str, Any]:
    """The ledger row of one finished run, from its result and its own session log."""
    ran = how_it_ran(state, tag)
    result, why = outcome(bool(passed), ran)
    return {
        "t": round(time.time(), 3),
        "version": harness_version(),
        "suite": suite,
        "task": task,
        "spec": task_spec,
        "variant": variant,
        "model": model,
        "outcome": result,
        "why": why,
        "ended": ran["reasons"][-1] if ran["reasons"] else "",
        "steps": int(steps),
        "tokens": int(tokens),
        "fresh": int(fresh),
        "wall": float(wall),
        "flags": sorted(flags),
        "signs": signs(state) if result != VOID else {},
    }


def ledger_path() -> Path:
    from harness.safety.secrets import state_dir

    return state_dir() / "evals" / "runs.jsonl"


def record(rows: Iterable[dict[str, Any]], path: Optional[Path] = None) -> None:
    """Append `rows` to the ledger. Best-effort, and one line per row so a run that dies
    keeps the rows before it."""
    try:
        target = path or ledger_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as handle:
            for entry in rows:
                handle.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")
    except Exception as exc:  # noqa: BLE001 — a result must not fail the run
        print(f"note: eval rows not recorded ({type(exc).__name__})")


def load(path: Optional[Path] = None) -> list[dict[str, Any]]:
    try:
        lines = (path or ledger_path()).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    rows = []
    for line in lines:
        try:
            entry = json.loads(line)
        except ValueError:
            continue  # a torn last line: the file is appended to while it is read
        if isinstance(entry, dict):
            rows.append(entry)
    return rows
