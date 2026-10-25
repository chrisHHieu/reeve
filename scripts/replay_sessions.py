"""Replay real sessions through the CURRENT context pipeline — a drift alarm.

This replays a real session database through transcript
hydration and the product projection, because the failure it looks for is invisible to
unit tests: the pipeline still runs, it just disagrees with what the session actually
recorded. Here the same shape, aimed at the two things that decide what a provider sees:

    the OUTBOUND PROJECTION — compaction, prune, revert, sidecar stripping — must not
    crash, must stay bounded, and must keep the invariant the audit enforces (everything
    model-visible is in the session log);

    the SIZE ESTIMATE — the trigger's arithmetic on a real transcript, reported so a
    change in the estimator shows up as a number, not as a surprise in production.

Usage:

    .venv\\Scripts\\python.exe scripts/replay_sessions.py --dir %APPDATA%\\Reeve\\conversations
    ... --file <session>.jsonl [--json] [--limit 20]

Exit 1 when a replay finds an invariant violation or the projection cannot be built;
0 otherwise (a session with no event log beside it is reported, not failed: it may simply
predate the log).
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:  # `python scripts/…` from the repo root
    sys.path.insert(0, str(REPO))

from harness.context import compaction as _compaction  # noqa: E402

from harness.loop import TurnEngine  # noqa: E402

from harness.session.invariants import ModelVisibilityAudit  # noqa: E402

from harness.safety.permissions import PermissionEngine  # noqa: E402

from harness.session.session_log import SessionLog  # noqa: E402

from harness.tools import ToolRegistry  # noqa: E402



@dataclass
class ReplayResult:
    session: str
    messages: int = 0
    outbound: int = 0
    estimated_tokens: int = 0
    trigger_tokens: int = 0
    compaction_active: bool = False
    pruned: int = 0
    violations: list[str] = field(default_factory=list)
    error: str = ""
    has_event_log: bool = False

    @property
    def ok(self) -> bool:
        return not self.error and not self.violations


class _NeverCalled:
    """The provider is never reached: a replay reads the pipeline, it does not run turns.
    If this is called, the replay did something it should not have — loudly."""

    def complete(self, **kwargs):  # pragma: no cover - a guard, not a path
        raise AssertionError("replay must not call the provider")

    def stream(self, **kwargs):  # pragma: no cover
        raise AssertionError("replay must not call the provider")

    def capabilities(self, model):
        raise AssertionError("replay must not call the provider")


def _read_jsonl(path: Path) -> list[dict]:
    out: list[dict] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue  # a torn final line is ordinary
        if isinstance(entry, dict):
            out.append(entry)
    return out


def replay(transcript: Path, *, log_path: Path | None = None) -> ReplayResult:
    result = ReplayResult(session=transcript.stem)
    messages = _read_jsonl(transcript)
    result.messages = len(messages)
    if not messages:
        result.error = "no messages"
        return result

    engine = TurnEngine(
        provider=_NeverCalled(),
        registry=ToolRegistry(),
        permissions=PermissionEngine(workspace_root=REPO),
        model="replay",
    )
    engine.messages = messages
    # The session's own log, when it is beside the transcript (`<id>.events.jsonl`).
    events_file = log_path or transcript.with_suffix(".events.jsonl")
    result.has_event_log = events_file.is_file()
    if result.has_event_log:
        engine.log = SessionLog(events_file)
    else:
        engine.log = SessionLog(REPO / ".replay-no-log.jsonl")

    try:
        outbound = engine._outbound_messages()
    except Exception as exc:  # the projection is the thing under test
        result.error = f"{type(exc).__name__}: {exc}"
        return result
    result.outbound = len(outbound)
    result.estimated_tokens = _compaction.estimate_tokens(outbound)
    # The same arithmetic the trigger uses, on the session's own default window: reported
    # so an estimator change is a number in a report rather than a surprise in production.
    result.trigger_tokens = _compaction.trigger_tokens(
        _compaction.DEFAULT_CONTEXT_WINDOW
    )
    state = engine.compaction_state
    result.compaction_active = state is not None and state.boundary_index > 0
    result.pruned = state.pruned_upto if state is not None else 0

    if result.has_event_log:
        audit = ModelVisibilityAudit()
        result.violations = audit.check(
            engine.log, engine.messages, compaction_active=result.compaction_active
        )
    if not result.has_event_log:
        result.error = "no event log beside the transcript (older session?)"
    return result


def discover(directory: Path, *, limit: int) -> list[Path]:
    files = sorted(
        (p for p in directory.glob("*.jsonl") if not p.name.endswith(".events.jsonl")),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return files[:limit] if limit else files


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="replay_sessions")
    parser.add_argument("--dir", default="", help="directory of <session>.jsonl transcripts")
    parser.add_argument("--file", default="", help="one transcript")
    parser.add_argument("--limit", type=int, default=10, help="how many (newest first)")
    parser.add_argument("--json", action="store_true", help="machine-readable report")
    args = parser.parse_args(argv)

    targets: list[Path] = []
    if args.file:
        targets = [Path(args.file)]
    elif args.dir:
        targets = discover(Path(args.dir), limit=args.limit)
    else:
        parser.error("pass --dir or --file")
    if not targets:
        print("no transcripts found")
        return 0

    results = [replay(path) for path in targets]
    # A session whose event log is missing is REPORTED, not failed: it may predate the
    # log, and failing on that would make the tool useless on old state.
    failed = [r for r in results if r.error and "no event log" not in r.error]
    failed += [r for r in results if r.violations]
    if args.json:
        # ONE document on stdout: a report a script reads must not be two things.
        print(
            json.dumps(
                {
                    "sessions": [r.__dict__ for r in results],
                    "failed": len(failed),
                },
                indent=2,
            )
        )
    else:
        for r in results:
            if r.ok:
                status = "ok  "
            elif "no event log" in r.error:
                status = "skip"  # not a failure: the session may predate the log
            else:
                status = "BAD "
            print(
                f"{status} {r.session}: {r.messages} messages, outbound {r.outbound}, "
                f"~{r.estimated_tokens} tokens"
                + (", compacted" if r.compaction_active else "")
                + (f", pruned to {r.pruned}" if r.pruned else "")
                + (f" — {r.error}" if r.error else "")
            )
            for violation in r.violations:
                print(f"      violation: {violation}")
    if failed:
        if not args.json:
            print(f"\n{len(failed)} session(s) need a look")
        return 1
    if not args.json:
        print(f"\n{len(results)} session(s) replayed, no violations")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
