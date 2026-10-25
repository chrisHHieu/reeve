"""Recurrence eval — what a project's own repair is worth when the failure comes back (live).

Every other eval here is one session. What recalled repairs do acts across two: one session
repairs a failure, the harness keeps the repair (and, if the repair was costly and the
agent chose to, a lesson), and a LATER session meets the same failure. One trial is that
story on a probe (`seen-before` by default: a cause eight imports from its test):

    first    a fresh project. The agent repairs the failing test. Nothing is seeded: the
             repairs store and any lesson are what this session left behind.
    then     the bug is back (the workspace is put back as the task began), and a new
             session with no history of the first is given the same task, three ways:

    none     an empty memory directory: what the failure costs with nothing kept
    recall   the first session's repairs store and nothing else: told where the repair was
    lesson   everything the first session left: the store, and the lesson if it wrote one

`none` against `recall` is what the harness's own record is worth. `recall` against
`lesson` is what the model's words add to it. Both are read by `benchmarks/compare.py` on
what the recurrence cost (`to_fix`: the step at which the failure was gone, since a session
also spends steps on memory after the repair), and beside it on the session's steps and
tokens; every run is a row in the eval ledger (suite `recur`). Trials go on until both
questions are answered on `to_fix` or `--runs` is reached, each reading held to the bound
for that many looks.

The later sessions get a state directory of their own, so they open with no recap of the
first: this measures a failure that comes back after other work, not the very next turn.
A trial whose first session did not repair the failure has nothing to carry and stops
there; one that left no lesson runs `none` and `recall` only.

A task may instead say what the later sessions meet (`later`: files written over the project
as the first session left it). Then the same test fails for another cause, with the first
repair still in place, and the same comparisons read what a kept repair and a lesson do when
they are recalled where they do not apply: the harm a record can do, which a failure that is
merely back cannot show. A trial whose first session edited a file the new cause is written
into has no two causes apart, and stops after it. The tasks are `seen-before` in
`benchmarks/tasks-probe` and those in `benchmarks/tasks-recur`
(`benchmarks/probe_seen_before.py`), each read on its own. Spends real tokens (four sessions
a trial, about 1.2M tokens on deepseek-v4.1-flash):

    .venv\\Scripts\\python.exe -m benchmarks.recur_eval --model opencode-go:deepseek-v4.1-flash [--runs 3] [--only seen-before seen-before-new-cause]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable, Optional

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from benchmarks import compare, runs  # noqa: E402
from benchmarks.harness_eval import _kpis, fresh_tokens  # noqa: E402
from benchmarks.probe_eval import _unchanged_bytes, grade, load_probes, run_in, setup_bytes, write_setup  # noqa: E402
from harness.context.memory import anchors  # noqa: E402

from harness.context.memory.repairs import SIDECAR_NAME  # noqa: E402


SUITE = "recur"
FIRST = "first"
LATER = ("none", "recall", "lesson")
#: What a recurrence costs: the steps until the failure was gone (`_to_fix`). Steps and
#: tokens of the whole session are read beside it.
TO_FIX = "to_fix"
MEASURES = (TO_FIX, "steps", "tokens")
#: The two questions a trial answers: what the harness's own record is worth, and what the
#: model's words add to it.
QUESTIONS = (
    ("none", "recall", "what the harness's record is worth"),
    ("recall", "lesson", "what the lesson adds to it"),
)
#: The same comparisons where the later sessions meet another cause (`later`).
ELSEWHERE = (
    ("none", "recall", "what a record of another cause does here"),
    ("recall", "lesson", "what that cause's lesson adds"),
)
#: The recurrence eval's own tasks, beside `seen-before` in the probe suite.
TASKS = REPO / "benchmarks" / "tasks-recur"


def load_tasks(ids: list[str]) -> list[dict[str, Any]]:
    """The tasks named, from `benchmarks/tasks-recur` or the probe suite."""
    found = {probe["id"]: probe for probe in load_probes(ids)}
    for path in sorted(TASKS.glob("*/task.json")):
        task = json.loads(path.read_text(encoding="utf-8"))
        if task["id"] in ids:
            found[task["id"]] = task
    missing = [name for name in ids if name not in found]
    if missing:
        raise SystemExit(f"no recurrence task named {', '.join(missing)}")
    return [found[name] for name in ids]


def as_left(probe: dict[str, Any], workspace: Path, into: Path) -> Optional[Path]:
    """The project as the first session left it, with the task's `later` cause written in, at
    `into`: what every later session starts from. None when the first session's own work
    edited a file the new cause goes in, so the two causes would not be apart. What grading
    added (the hidden tests) and caches are left out."""
    later = probe.get("later") or {}
    for rel in later:
        at = workspace / rel
        if not at.is_file() or _unchanged_bytes(at.read_bytes()) != _unchanged_bytes(setup_bytes(probe["setup"][rel])):
            return None
    shutil.rmtree(into, ignore_errors=True)
    shutil.copytree(workspace, into, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
    for rel in probe.get("hidden") or {}:
        (into / rel).unlink(missing_ok=True)
    for rel, body in later.items():
        (into / rel).write_bytes(setup_bytes(body))
    return into


def memory_of(state: Path) -> Optional[Path]:
    """The project's memory directory under a state directory a session ran in."""
    found = sorted(path for path in (state / "projects").glob("*/memory") if path.is_dir())
    return found[0] if found else None


def lessons_in(memory: Optional[Path]) -> list[str]:
    """The `lesson` memories a session left, by file name."""
    if memory is None:
        return []
    return sorted(
        name for name, entry in anchors.recorded(memory).items()
        if entry.get("kind") == "lesson" and (memory / name).is_file()
    )


def carry(left: Path, into: Path, variant: str) -> None:
    """Give a later session what `variant` says it has of the first one's memory directory
    (`left`), at the same place under its own state directory (`into`)."""
    if variant == "recall":
        into.mkdir(parents=True, exist_ok=True)
        shutil.copy2(left / SIDECAR_NAME, into / SIDECAR_NAME)
    elif variant == "lesson":
        shutil.copytree(left, into)


async def trial(
    model: str,
    probe: dict[str, Any],
    out: Path,
    number: int,
    provider_for: Callable[[str], Any] = lambda tag: None,
) -> list[dict[str, Any]]:
    """One trial: the first session, then each later session that has something to carry.
    Returns their ledger rows."""
    workspace = out / f"trial-{number}" / "ws"
    kpis = _kpis()
    rows: list[dict[str, Any]] = []

    async def session(variant: str, state: Path, flags: list[str], project: Optional[Path] = None) -> dict[str, Any]:
        _empty(workspace)
        if project is None:
            write_setup(probe, workspace)  # the bug is (back) where the task began
        else:
            shutil.copytree(project, workspace, dirs_exist_ok=True)  # the other cause, the first repair kept
        tag = f"{variant}-{number}"
        ran = await run_in(model, probe, workspace, state, tag, provider=provider_for(tag), seed=False)
        verdict = grade(probe, workspace, ran["answer"])
        report = kpis.compute(state)
        entry = runs.row(
            suite=SUITE, task=probe["id"], variant=variant, model=model, passed=verdict["passed"],
            state=state, tag=tag, steps=int(report.get("steps") or 0), tokens=int(report.get("tokens") or 0),
            fresh=fresh_tokens(state), wall=ran["wall"], task_spec=runs.spec(probe), flags=flags,
        )
        events = _repair_events(state, tag)
        entry["fired"] = _fired(events)
        entry[TO_FIX] = _to_fix(events, entry["steps"])
        runs.record([entry])
        rows.append(entry)
        why = f" ({entry['why']})" if entry["why"] else ""
        print(
            f"  done: {tag} {entry['outcome']}{why} to_fix={entry[TO_FIX]} steps={entry['steps']} "
            f"tokens={entry['tokens']:,} {entry['fired']}",
            flush=True,
        )
        return entry

    first_state = out / f"trial-{number}" / FIRST
    first = await session(FIRST, first_state, [])
    left = memory_of(first_state)
    if first["outcome"] != runs.PASSED or left is None or not (left / SIDECAR_NAME).is_file():
        print(f"  trial {number}: the first session left no repair to carry; no later sessions", flush=True)
        return rows
    project = None
    if probe.get("later"):
        project = as_left(probe, workspace, out / f"trial-{number}" / "project")
        if project is None:
            print(f"  trial {number}: the first session edited where the new cause goes; no later sessions", flush=True)
            return rows
    lessons = lessons_in(left)
    for variant in LATER:
        if variant == "lesson" and not lessons:
            print(f"  trial {number}: the first session wrote no lesson; `lesson` not run", flush=True)
            continue
        state = out / f"trial-{number}" / variant
        carry(left, state / left.relative_to(first_state), variant)
        await session(variant, state, [f"lessons={len(lessons)}"] if variant == "lesson" else [], project)
    return rows


def _empty(folder: Path) -> None:
    """Clear a workspace and keep the folder: every session of a trial works at one path
    (the project's memory is keyed by it), and Windows will not remove a folder a process
    that just ended still has open."""
    folder.mkdir(parents=True, exist_ok=True)
    for child in folder.iterdir():
        if child.is_dir():
            shutil.rmtree(child, ignore_errors=True)
        else:
            child.unlink(missing_ok=True)


def _repair_events(state: Path, tag: str) -> list[dict[str, Any]]:
    """The repair mechanism's rows in one session's own log."""
    try:
        lines = (state / "conversations" / f"{tag}.events.jsonl").read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    events = []
    for line in lines:
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if str(event.get("type", "")).startswith("repair/"):
            events.append(event)
    return events


def _fired(events: list[dict[str, Any]]) -> dict[str, int]:
    """What the repair mechanism did in one session."""
    seen = {"fixed": 0, "recalled": 0, "asked": 0}
    for event in events:
        if event.get("type") == "repair/fixed":
            seen["fixed"] += 1
            seen["asked"] += bool(event.get("asked"))
        elif event.get("type") == "repair/recalled":
            seen["recalled"] += 1
    return seen


def _to_fix(events: list[dict[str, Any]], steps: int) -> int:
    """The step at which the failure was gone: the first repair the harness witnessed. A
    session's own step count also holds what it did after (a lesson written, a note
    rewritten: up to five steps in the first run of this eval), which is no part of what the
    failure cost. A session that never repaired it paid all of its steps."""
    at = next((int(e.get("step") or 0) for e in events if e.get("type") == "repair/fixed"), 0)
    return at or steps


def _by_variant(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    return {variant: [row for row in rows if row["variant"] == variant] for variant in (FIRST, *LATER)}


def called(rows: list[dict[str, Any]], looks: int) -> bool:
    """Whether both questions have an answer on what a recurrence costs (`to_fix`): more
    trials could no longer move either. Every session here passes, so the pass rate is no
    reading to stop on; a question with no sessions on a side is open. "The same" is an
    answer: it is the one a record recalled where it does not apply should give."""
    by = _by_variant(rows)
    return all(
        compare.verdict(by[base], by[head], looks=looks, measure=TO_FIX)["cost"]
        in (compare.CHEAPER, compare.DEARER, compare.SAME)
        for base, head, _ in QUESTIONS
    )


def summarize(rows: list[dict[str, Any]], looks: int = 1, elsewhere: bool = False) -> list[str]:
    """What one task's trials show; `elsewhere` for a task whose later sessions met another
    cause."""
    by = _by_variant(rows)
    lines = []
    for variant, group in by.items():
        ran = [row for row in group if row["outcome"] != runs.VOID]
        if not ran:
            continue
        lines.append(
            f"{variant:7} pass {sum(1 for r in ran if r['outcome'] == runs.PASSED)}/{len(ran)}  "
            f"to_fix {sorted(r.get(TO_FIX) or r['steps'] for r in ran)}  "
            f"steps {sorted(r['steps'] for r in ran)}  tokens {sorted(round(r['tokens'] / 1000) for r in ran)}k  "
            f"asked {sum(r['fired']['asked'] for r in ran)}  recalled {sum(r['fired']['recalled'] for r in ran)}"
        )
    for base, head, question in ELSEWHERE if elsewhere else QUESTIONS:
        if not by[base] or not by[head]:
            continue
        lines.append(f"\n{base} -> {head}: {question}")
        rendered = compare.render(compare.verdict(by[base], by[head], looks=looks))
        if len(rendered) < 4:  # no task ran on both sides
            lines.append(f"  {rendered[-1]}")
            continue
        lines.append(f"  {rendered[1]}")  # the pass rates
        for measure in MEASURES:
            found = compare.verdict(by[base], by[head], looks=looks, measure=measure)
            lines.append(f"  {compare.render(found)[2]}")
    return lines


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--model", required=True)
    parser.add_argument("--only", nargs="+", default=["seen-before"], help="the tasks, each read on its own")
    parser.add_argument(
        "--runs", type=int, default=3,
        help="the most trials a task (four sessions each); stops sooner once both questions are answered",
    )
    parser.add_argument("--out", default="")
    args = parser.parse_args(argv)

    tasks = load_tasks(args.only)
    out = Path(args.out) if args.out else Path(tempfile.mkdtemp(prefix="recur-eval-"))
    ceiling = max(1, args.runs)
    every: list[dict[str, Any]] = []
    lines: list[str] = []
    for probe in tasks:
        print(f"== {probe['id']}", flush=True)
        rows: list[dict[str, Any]] = []
        for number in range(ceiling):
            rows += asyncio.run(trial(args.model, probe, out / probe["id"], number))
            # Read at every trial, so each reading is held to the bound of `ceiling` looks.
            if called(rows, ceiling):
                print(f"  both questions answered after {number + 1} trials", flush=True)
                break
        every += rows
        lines += [f"\n== {probe['id']}", *summarize(rows, ceiling, elsewhere=bool(probe.get("later")))]
    out.mkdir(parents=True, exist_ok=True)
    (out / "recur_eval.json").write_text(json.dumps(every, indent=1), encoding="utf-8")
    print("\n".join(lines))
    print(f"logs and results: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
