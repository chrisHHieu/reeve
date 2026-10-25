"""Refuse to work on a stale checkout.

This check exists because a local clone was once 140
commits behind and an agent spent a day implementing against an architecture that no
longer existed. The cheap guard is the same here: fetch, then compare HEAD with its
upstream. A branch behind its OWN upstream is always an error (someone else's commits are
missing); being far behind `origin/main` on a feature branch is a warning with a number,
because a long-lived branch is a legitimate state.

    .venv\\Scripts\\python.exe scripts/check_workspace_freshness.py
    ... --no-fetch          # offline: compare against the refs already fetched
    ... --max-behind-main 20

Exit codes: 0 fresh, 1 behind upstream (or too far behind main), 2 the check could not run
(no git, no upstream) — a check that cannot answer must not read as "fine".
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _git(*args: str) -> tuple[int, str]:
    proc = subprocess.run(
        ["git", *args],
        cwd=str(REPO),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return proc.returncode, (proc.stdout or proc.stderr or "").strip()


def _counts(upstream: str) -> tuple[int, int] | None:
    code, out = _git("rev-list", "--left-right", "--count", f"{upstream}...HEAD")
    if code != 0:
        return None
    try:
        behind, ahead = (int(part) for part in out.split())
    except ValueError:
        return None
    return behind, ahead


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="check_workspace_freshness")
    parser.add_argument(
        "--no-fetch",
        action="store_true",
        help="do not reach the remote; compare against the refs already fetched",
    )
    parser.add_argument(
        "--max-behind-main",
        type=int,
        default=50,
        help="how far behind origin/main a branch may sit before it is an error (default 50)",
    )
    args = parser.parse_args(argv)

    code, _ = _git("rev-parse", "--is-inside-work-tree")
    if code != 0:
        print("not a git work tree — freshness cannot be checked", file=sys.stderr)
        return 2

    if not args.no_fetch:
        fetch_code, fetch_out = _git("fetch", "origin", "--prune")
        if fetch_code != 0:
            print(f"git fetch failed:\n{fetch_out}", file=sys.stderr)
            print("re-run with --no-fetch to compare against what is already here")
            return 2

    code, upstream = _git("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
    if code != 0 or not upstream or upstream == "HEAD":
        print(
            "no upstream branch configured (git branch --set-upstream-to=origin/<branch>)",
            file=sys.stderr,
        )
        return 2

    counts = _counts(upstream)
    if counts is None:
        print(f"could not compare against {upstream}", file=sys.stderr)
        return 2
    behind, ahead = counts
    print(f"HEAD is {ahead} ahead / {behind} behind {upstream}")

    failed = False
    if behind:
        print(
            f"STALE: {behind} commit(s) on {upstream} are not in this checkout. "
            "Pull before working — code you read now may already be gone.",
            file=sys.stderr,
        )
        failed = True

    main_counts = _counts("origin/main")
    if main_counts is not None:
        main_behind, main_ahead = main_counts
        if main_behind > args.max_behind_main and not main_ahead:
            # Nothing of its own against main: this is a stale checkout, not a branch.
            print(
                f"STALE: this branch is {main_behind} behind origin/main (limit "
                f"{args.max_behind_main}) with no commits of its own. Rebase before "
                "working — code you read now may already be gone.",
                file=sys.stderr,
            )
            failed = True
        elif main_behind > args.max_behind_main:
            print(
                f"warning: {main_behind} behind origin/main (limit {args.max_behind_main}) "
                "— rebase soon; conflicts grow with distance"
            )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
