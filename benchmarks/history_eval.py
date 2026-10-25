"""History eval — real bugs from this repository's own history, fixed by the product (live).

The first ablation could not tell one mechanism from another: its synthetic tasks passed
on every variant, so no difference was left to measure. The harder, honest source of tasks is the repository's own past — every
`fix` commit that changed code AND a test file is a bug a person actually hit, with a
specification (the test) already written:

    build   for each candidate commit, check out its PARENT in a git worktree, bring in
            the commit's test files, and keep the task only if those tests FAIL there and
            PASS at the commit itself — so every task is solvable and currently unsolved
    run     the product engine (`harness_eval.product_engine`: the SessionManager-built
            agent, every tool, the real prompt) is asked to make the failing tests
            pass without editing them; afterwards the test files are restored from the
            commit — an edited test cannot pass the grade — and run again

Tasks are frozen in `benchmarks/tasks-history.json` so two runs grade the same work. The
snapshot gets a `.venv` junction to a venv built for the run, with the SNAPSHOT installed
editable in it, so the repository's commands work and `harness` is the snapshot's from any
directory — as in a real checkout. Nothing the agent reads names this checkout: the failing
output recorded at build time is scrubbed of its path. Spends real tokens:

    .venv\\Scripts\\python.exe -m benchmarks.history_eval --build --limit 30
    .venv\\Scripts\\python.exe -m benchmarks.history_eval --model opencode-go:deepseek-v4.1-flash [--only ID ...] [--runs 3] [--ablate MECHANISM] [--against VERSION]

Every run is a row in the eval ledger (`benchmarks/runs.py`), with the harness
version that ran it. `--against` compares this checkout's runs with an earlier version's
rows there; `--runs` is then a ceiling: rounds stop once every comparison is called
(`benchmarks/compare.py`).
"""

from __future__ import annotations

import argparse
import asyncio
import atexit
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Optional

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from benchmarks import compare, runs  # noqa: E402
from benchmarks.harness_eval import (  # noqa: E402
    ABLATABLE,
    _kpis,
    fired,
    fresh_tokens,
    product_engine,
    release,
)

TASKS_FILE = REPO / "benchmarks" / "tasks-history.json"
#: This suite's name in the eval ledger (`benchmarks/runs.py`).
SUITE = "history"
VENV = REPO / ".venv"
PYTHON = VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
#: What a task's `.venv` junction points at: a venv built for this run (`eval_venv`), so
#: the agent under test can `uv sync` / `pip install` against an OLD commit's pyproject
#: without touching the checkout's own venv, which the grader and the product still use.
LINK_TARGET: Optional[Path] = None


def eval_venv() -> Path:
    """A throwaway venv for the tasks' `.venv` junctions, built for this run alone.

    The junction used to point at this checkout's `.venv`: a task's agent that ran
    `uv sync` in its worktree would have re-synced the real venv to a months-old lockfile.
    Same Python minor, the project installed editable with the extras the suite imports.

    One venv per run, never a shared path: each task re-points the editable install at its
    own snapshot, so two runs sharing a venv graded each other's code, and the second one to
    start deleted the venv under the first (2026-10-10: three parallel runs, the venv left
    with only `Scripts/python.exe`, every row from the overlap void)."""
    uv = shutil.which("uv")
    if uv is None:
        raise SystemExit("history_eval needs `uv` to build the task venv (it never links the real one)")
    target = Path(tempfile.mkdtemp(prefix="reeve-history-eval-venv-"))
    shutil.rmtree(target)  # `uv venv` makes the directory itself
    atexit.register(shutil.rmtree, target, ignore_errors=True)
    version = f"{sys.version_info.major}.{sys.version_info.minor}"
    subprocess.run([uv, "venv", "--quiet", "--python", version, str(target)], check=True)
    python = target / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    subprocess.run(
        [uv, "pip", "install", "--quiet", "--python", str(python), "-e", f"{REPO}[dev,messaging]"],
        check=True,
    )
    return target


#: The eval's step ceiling. The product has none; this only stops a runaway, and is set
#: well above the longest real run (80 in the 2026-09-29 baseline, where the old cap of 80
#: ended a run that was still making progress). A run that reaches it is reported `capped`.
STEP_CEILING = 200


def scrub(text: str) -> str:
    """`text` with this checkout's path taken out (as `.`), in every spelling a tool or a
    traceback prints it.

    The 2026-09-29 baseline: two tasks' failing output named `D:\\Project\\reeve\\.venv
    \\Scripts\\python.exe` (a test that spawns Python prints its argv). The agent used that
    path, which made the run `peeked` and a solved task a failure — and running the real
    venv's Python outside the snapshot imported this checkout's (fixed) code."""
    roots = {str(REPO), str(REPO).replace("\\", "/"), str(REPO).replace("\\", "\\\\")}
    out = text
    for root in sorted(roots, key=len, reverse=True):
        lower = out.lower()
        needle = root.lower()
        start = lower.find(needle)
        while start != -1:
            out = out[:start] + "." + out[start + len(root):]
            lower = out.lower()
            start = lower.find(needle, start + 1)
    return out


#: A candidate whose tests take longer than this is not a quick-feedback task.
TEST_TIMEOUT_S = 180
OUTPUT_TAIL = 3_000


def git(*args: str, cwd: Path = REPO) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8",
        errors="replace", check=True,
    ).stdout


def candidates(limit: int) -> list[dict[str, Any]]:
    """Newest `fix` commits touching both package code and a test file (added or modified)."""
    rows = git("log", "--no-merges", "--format=%H%x09%s", "--grep=^fix", f"-n{limit}")
    found: list[dict[str, Any]] = []
    for line in rows.splitlines():
        sha, _, subject = line.partition("\t")
        changed = git("show", "--name-only", "--diff-filter=AM", "--format=", sha).split()
        tests = [p for p in changed if p.startswith("tests/") and Path(p).name.startswith("test_")]
        code = [p for p in changed if p.startswith("harness/") and p.endswith(".py")]
        if tests and code:
            found.append({"commit": sha, "subject": subject, "tests": tests, "code": code})
    return found


class Worktree:
    """`commit`'s files in a fresh one-commit repository, with a `.venv` junction.

    Not `git worktree add`: a worktree shares every ref with this checkout, so the agent
    could `git log --all` its way to the very fix it is asked to write. An archive of the
    commit, committed once in a new repository, keeps `git status`/`git diff` working and
    holds no history past the task.
    """

    def __init__(self, commit: str) -> None:
        import io
        import tarfile

        self.path = Path(tempfile.mkdtemp(prefix="history-eval-"))
        archive = subprocess.run(
            ["git", "archive", "--format=tar", commit], cwd=REPO, capture_output=True, check=True
        ).stdout
        with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
            tar.extractall(self.path)
        for step in (("init", "-q"), ("add", "-A"),
                     ("-c", "user.name=eval", "-c", "user.email=eval@local",
                      "commit", "-qm", "task snapshot")):
            git(*step, cwd=self.path)
        link = self.path / ".venv"
        if os.name == "nt":
            subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(LINK_TARGET or VENV)],
                           capture_output=True, check=True)
        else:
            link.symlink_to(LINK_TARGET or VENV)
        # The interpreter the snapshot's own `.venv` runs: the one the agent checks its work
        # with, and so the one its work is graded by.
        self.python = PYTHON
        if LINK_TARGET is not None:
            # The run's venv had THIS checkout installed editable, so Python started outside
            # the snapshot imported this checkout's code — the fix itself. Point the install
            # at the snapshot, as a real checkout's own venv points at the checkout. Never
            # the real venv (LINK_TARGET is None only when no run venv was built).
            python = LINK_TARGET / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
            subprocess.run(
                [shutil.which("uv") or "uv", "pip", "install", "--quiet", "--no-deps",
                 "--python", str(python), "-e", str(self.path)],
                capture_output=True, check=True,
            )
            self.python = python

    def __enter__(self) -> "Worktree":
        return self

    def __exit__(self, *_exc: Any) -> None:
        link = self.path / ".venv"
        try:
            # The link itself, never what it points at: rmdir on a junction (or unlink on
            # a symlink) removes the reparse point and leaves the real venv alone.
            if os.name == "nt":
                os.rmdir(link)
            else:
                link.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            print(f"  left {self.path} in place: its venv link would not come off", flush=True)
            return  # never delete a tree that may still reach the real venv

        def _writable(func: Any, target: str, _exc: Any) -> None:
            # Git leaves its object files read-only, which Windows will not delete.
            os.chmod(target, 0o700)
            func(target)

        # A process that just died may still hold a handle for a moment; a result is worth
        # more than a tidy temp dir, so a stubborn one is left and named, never raised.
        for _ in range(5):
            try:
                shutil.rmtree(self.path, onerror=_writable)
                return
            except OSError:
                time.sleep(2)
        print(f"  left {self.path} in place: still in use", flush=True)

    def bring_tests(self, commit: str, tests: list[str]) -> None:
        """Write `commit`'s version of each test file into the snapshot."""
        for test in tests:
            body = subprocess.run(
                ["git", "show", f"{commit}:{test}"], cwd=REPO, capture_output=True, check=True
            ).stdout
            target = self.path / test
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(body)

    def edited(self, commit: str, tests: list[str]) -> bool:
        """Whether any test file differs from `commit`'s version."""
        for test in tests:
            body = subprocess.run(
                ["git", "show", f"{commit}:{test}"], cwd=REPO, capture_output=True, check=True
            ).stdout
            target = self.path / test
            if not target.is_file() or target.read_bytes() != body:
                return True
        return False

    def run_tests(self, tests: list[str]) -> tuple[Optional[bool], str]:
        """(passed, output tail); passed is None when the run did not finish.

        Run by the snapshot's own interpreter. It used to be this checkout's, where the
        snapshot's package is not installed: a test that starts `python -m <package>` in a
        child failed there whatever the agent did, while the agent's own run of the same
        test passed (a task of 2026-10-09: the commit's own fix graded 1 failed, 32
        passed; by the snapshot's interpreter 33 passed). Once the package was renamed,
        this checkout's venv could not import the old name at all."""
        try:
            out = subprocess.run(
                [str(self.python), "-m", "pytest", "-q", "-p", "no:cacheprovider", *tests],
                cwd=self.path, capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=TEST_TIMEOUT_S,
                env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
            )
        except subprocess.TimeoutExpired:
            return None, "timed out"
        text = (out.stdout or "") + (out.stderr or "")
        return out.returncode == 0, text[-OUTPUT_TAIL:]


def build(limit: int) -> list[dict[str, Any]]:
    """Keep the candidates that are red at the parent and green at the commit."""
    tasks: list[dict[str, Any]] = []
    for cand in candidates(limit):
        sha = cand["commit"]
        with Worktree(f"{sha}~1") as before:
            before.bring_tests(sha, cand["tests"])
            red, output = before.run_tests(cand["tests"])
        if red is not False:
            print(f"  skip {sha[:8]}: {'slow' if red is None else 'already green'} at parent", flush=True)
            continue
        with Worktree(sha) as after:
            green, _ = after.run_tests(cand["tests"])
        if green is not True:
            print(f"  skip {sha[:8]}: not green at the commit", flush=True)
            continue
        tasks.append({"id": sha[:8], **cand, "failing": scrub(output)})
        print(f"  keep {sha[:8]} {cand['subject'][:70]}", flush=True)
    return tasks


def prompt_for(task: dict[str, Any]) -> str:
    files = " ".join(task["tests"])
    return (
        f"These tests fail in this repository: {files}\n\n"
        f"The end of their output:\n```\n{scrub(task['failing'])[-1500:]}\n```\n\n"
        "Make them pass by fixing the code they test. The test files are the "
        "specification: do not edit them. Check your work with "
        f"`.venv\\Scripts\\python.exe -m pytest {files} -q` and the test files of the "
        "modules you change; the whole suite takes about fifteen minutes and is not "
        "needed here."
    )


def suite_for(window: int) -> str:
    """The ledger suite: `history`, or `history@64k` for runs at a working window small enough that
    the context is managed (compacted, folded, pruned) on the way to the fix. The same tasks at two
    windows are two suites, so a comparison never pairs one with the other."""
    return SUITE if not window else f"{SUITE}@{window // 1000}k"


async def run_task(
    task: dict[str, Any], model: str, state: Path, tag: str, ablate: frozenset[str], window: int = 0
) -> dict[str, Any]:
    started = time.time()
    with Worktree(f"{task['commit']}~1") as tree:
        tree.bring_tests(task["commit"], task["tests"])
        engine = product_engine(model, tree.path, state, tag, ablate, max_iterations=STEP_CEILING)
        if window:
            # The product's machinery at a window the task's own context crosses: what a long
            # session meets at 400k, met here on a real fix (`harness_eval` does the same).
            settings = {"max_context_tokens": window}
            engine.compaction_settings = lambda: settings
        try:
            async for _ in engine.run(prompt_for(task)):
                pass
        finally:
            release(engine)
        edited = tree.edited(task["commit"], task["tests"])
        peeked = _peeked(engine.messages, task["commit"], ignore=tag)
        tree.bring_tests(task["commit"], task["tests"])  # an edited test cannot pass the grade
        passed, _ = tree.run_tests(task["tests"])
    return {
        "passed": bool(passed) and not peeked,
        "edited_tests": edited,
        "peeked": peeked,
        # The eval's step ceiling ended the turn: the product would have gone on.
        "capped": end_reason(state, tag) == "max_iterations_exceeded",
        # How often the context was cut on the way (compactions and model folds), so a run at a
        # small window shows the machinery it exercised.
        "cuts": cuts(state, tag),
        "wall": round(time.time() - started, 1),
    }


def cuts(state: Path, tag: str) -> int:
    """The context cuts a run's log records: compactions, folds and prunes that ended with a new
    view."""
    log = state / "conversations" / f"{tag}.events.jsonl"
    count = 0
    try:
        for line in log.read_text(encoding="utf-8").splitlines():
            if '"compaction/end"' in line and any(f'"{reason}"' in line for reason in ("summarized", "folded", "pruned")):
                count += 1
    except OSError:
        return count
    return count


def end_reason(state: Path, tag: str) -> str:
    """How the run's last turn ended, from its session event log ("" when unknown)."""
    log = state / "conversations" / f"{tag}.events.jsonl"
    reason = ""
    try:
        for line in log.read_text(encoding="utf-8").splitlines():
            if '"turn/end"' in line:
                reason = str(json.loads(line).get("reason") or "")
    except (OSError, ValueError):
        return reason
    return reason


#: An import of the engine's package.
_IMPORTS_HARNESS = re.compile(r"^\s*(?:from|import)\s+harness\b", re.MULTILINE)


def _imports_the_checkout(function: dict[str, Any]) -> bool:
    """A `run_code` program that imports the engine's package (`harness`).

    The code child is the product's own interpreter, started with this process's working
    directory: THIS checkout, whose code is the fix. So an import of the package inside
    `run_code` reads the answer, not the snapshot. The child cannot be pointed at the
    snapshot: it must be the product's own package to speak its protocol. The 2026-09-29
    baseline had one: a fix imported the package's search tool there.
    """
    if function.get("name") != "run_code":
        return False
    arguments = function.get("arguments")
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except ValueError:
            return bool(_IMPORTS_HARNESS.search(arguments.replace("\\n", "\n")))
    code = arguments.get("code") if isinstance(arguments, dict) else None
    return isinstance(code, str) and bool(_IMPORTS_HARNESS.search(code))


def _peeked(messages: list[dict[str, Any]], commit: str, ignore: str = "") -> bool:
    """Whether a tool call reached for the answer: the fix's sha or the source checkout.

    The first pilot (git worktrees, which share refs) found the agent running
    `git log --all` and `git show <fix>` and copying the upstream change. The snapshot
    closes that road; `run_shell` is not sandboxed, so a call naming this checkout or the
    fix commit still marks the run, and a marked run does not count as solved.
    """
    needles = {commit[:7].lower(), str(REPO).lower(), str(REPO).replace("\\", "/").lower()}
    for message in messages:
        for call in message.get("tool_calls") or []:
            if _imports_the_checkout(call.get("function") or {}):
                return True
            text = str((call.get("function") or {}).get("arguments") or "").lower()
            text = text.replace("\\\\", "\\")
            if ignore:
                # The run's own tag names its session scratch folder, and the tag starts
                # with the task id — the fix's short sha (a false "peeked" in the pilot).
                text = text.replace(ignore.lower(), "")
            if any(needle in text for needle in needles):
                return True
    return False


def _print(rows: list[dict[str, Any]]) -> None:
    print(f"{'task':10} {'variant':20} {'pass':>5} {'steps':>6} {'tokens':>10} {'wall':>6}  flags")
    for row in rows:
        flags = " ".join(f for f in ("edited-tests", "peeked", "capped") if row.get(f.replace("-", "_")))
        print(f"{row['task']:10} {row['variant']:20} {('yes' if row['passed'] else 'NO'):>5} "
              f"{row['steps']:>6} {row['tokens']:>10,} {row['wall']:>6.0f}  {flags}")
    by_variant: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_variant.setdefault(row["variant"], []).append(row)
    for variant, group in by_variant.items():
        void = sum(1 for r in group if r.get("outcome") == runs.VOID)
        group = [r for r in group if r.get("outcome") != runs.VOID]
        solved = sum(r["passed"] for r in group)
        tokens = sum(r["tokens"] for r in group) / max(1, len(group))
        print(f"{variant}: {solved}/{len(group)} solved, {tokens:,.0f} tokens per run"
              + (f" ({void} void: not runs)" if void else ""))


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--build", action="store_true", help="rebuild tasks-history.json")
    parser.add_argument("--limit", type=int, default=40, help="fix commits to consider when building")
    parser.add_argument("--model", default="")
    parser.add_argument("--only", nargs="*", default=[])
    parser.add_argument("--runs", type=int, default=1,
                        help="the most rounds over the tasks; fewer once every comparison is called")
    parser.add_argument("--against", default="",
                        help="a harness version in the eval ledger (prefix, or version:variant) to "
                             "compare this checkout's baseline runs with")
    parser.add_argument("--no-baseline", action="store_true",
                        help="run only the variants (their baseline runs in another process)")
    parser.add_argument("--ablate", nargs="*", default=[], choices=list(ABLATABLE),
                        help="also run each task with these mechanisms off, one per variant")
    parser.add_argument("--out", default="")
    parser.add_argument("--window", type=int, default=0,
                        help="a working window in tokens (0: the product's own); its runs are the suite history@<N>k")
    args = parser.parse_args(argv)
    suite = suite_for(args.window)

    global LINK_TARGET
    if args.build:
        # A task is kept when its tests are green at the commit, graded as a run grades
        # them: by the snapshot's own interpreter.
        LINK_TARGET = eval_venv()
        tasks = build(args.limit)
        TASKS_FILE.write_text(json.dumps(tasks, indent=1), encoding="utf-8")
        print(f"{len(tasks)} tasks -> {TASKS_FILE}")
        return 0
    if not args.model:
        parser.error("--model is required to run")
    # The tasks are this checkout's own fix commits, so they are built where that history is.
    if not TASKS_FILE.exists():
        parser.error(f"no {TASKS_FILE.name} yet: build it from this checkout's history with --build")
    LINK_TARGET = eval_venv()
    tasks = json.loads(TASKS_FILE.read_text(encoding="utf-8"))
    if args.only:
        tasks = [t for t in tasks if t["id"] in args.only]
    out = Path(args.out) if args.out else Path(tempfile.mkdtemp(prefix="history-eval-out-"))
    kpis = _kpis()
    rows: list[dict[str, Any]] = []
    entries: list[dict[str, Any]] = []
    variants = [] if args.no_baseline else ["baseline"]
    variants += args.ablate
    earlier: list[dict[str, Any]] = []
    if args.against:
        label, earlier = compare.side(
            [r for r in runs.load() if r.get("suite") == suite and r.get("model") == args.model],
            args.against,
        )
        if not earlier:
            parser.error(f"the ledger holds no {suite} runs of {label} on {args.model}")
    ceiling = max(1, args.runs)

    def readings() -> list[tuple[str, dict[str, Any]]]:
        """Each comparison this invocation exists to make, as it stands now."""
        mine = [e for e in entries if e["variant"] == "baseline"]
        found = []
        if earlier:
            found.append((f"{args.against} -> this checkout", compare.verdict(earlier, mine, looks=ceiling)))
        for variant in args.ablate:
            off = [e for e in entries if e["variant"] == variant]
            found.append((f"baseline -> without {variant}", compare.verdict(mine, off, looks=ceiling)))
        return found

    # Round after round, every variant on every task, so a provider's bad hour falls on
    # both sides alike; and no further than the round where every comparison is called.
    for run in range(ceiling):
        if run and (found := readings()) and all(compare.settled(v) for _, v in found):
            break
        for variant in variants:
            ablate = frozenset() if variant == "baseline" else frozenset({variant})
            for task in tasks:
                tag = f"{task['id']}-{variant}-{run}"
                state = out / tag
                result = asyncio.run(run_task(task, args.model, state, tag, ablate, args.window))
                report = kpis.compute(state)
                steps, tokens = int(report.get("steps") or 0), int(report.get("tokens") or 0)
                entry = runs.row(
                    suite=suite, task=task["id"], variant=variant, model=args.model,
                    passed=result["passed"], state=state, tag=tag, steps=steps, tokens=tokens,
                    fresh=fresh_tokens(state), wall=result["wall"],
                    task_spec=runs.spec(task["commit"], task["tests"]),
                    flags=[f for f in ("edited_tests", "peeked", "capped") if result.get(f)],
                )
                runs.record([entry])
                entries.append(entry)
                rows.append({
                    "task": task["id"], "variant": variant, "run": run, **result,
                    "outcome": entry["outcome"], "steps": steps, "tokens": tokens,
                    "fresh": entry["fresh"],
                    "fired": {name: fired(state, name) for name in ABLATABLE},
                })
                why = f" ({entry['why']})" if entry["why"] else ""
                print(f"  done {tag}: {entry['outcome']}{why}", flush=True)
    out.mkdir(parents=True, exist_ok=True)
    (out / "history_eval.json").write_text(json.dumps(rows, indent=1), encoding="utf-8")
    _print(rows)
    for label, found in readings():
        print(f"\n{label}")
        print("\n".join(compare.render(found)))
    print(f"logs and results: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
