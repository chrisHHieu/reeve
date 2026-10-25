"""Harness KPIs from the session store — the before/after numbers for any harness change.

The harness audit of 2026-09-24 found its three worst problems (a turn budget that blinded
long turns, context left to grow toward a 1M window, every MCP schema on every request)
by mining the event logs by hand. None of them was visible to the benchmark or the test
suite, because each only shows up across many steps of real sessions. This script is that
mining, kept: run it before a change for the baseline and after it (`--since`) to see
whether the change moved the number it was meant to move.

    .venv\\Scripts\\python.exe scripts/harness_kpis.py [--state-dir <dir>] [--since 2026-09-24] [--json]

Counts only — no message, argument or result content is printed, so the output is safe to
paste into an issue. Two measurement traps it accounts for:

- the event log clips every field at `session_log.MAX_FIELD_CHARS` (4,000), so result
  SIZES are not measured here — only counts, statuses and budget events;
- `metrics.db`'s `model_usage.tokens_in` excludes `cache_read`, so the hit rate is
  `cache_read / (tokens_in + cache_read)`.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import statistics
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Optional

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:  # `python scripts/…` from the repo root
    sys.path.insert(0, str(REPO))

# The harness folds are shared with the Metrics tab (harness/observability/metrics_harness.py): the
# page and this script must never disagree on a number.
from harness.observability.metrics_harness import STALL_SECONDS, band_order, latency_fold, quantile, spend_fold, spend_report
from harness.session.turn_numbers import monotonic_turns  # noqa: E402


#: Context sizes (tokens per step) whose step and token shares are reported.
CONTEXT_BANDS = (100_000, 200_000, 400_000)
#: The tools whose failures are edit failures.
EDIT_TOOLS = ("replace_in_file", "apply_patch", "apply_unified_diff", "write_file")
#: Tool names that are MCP-backed for the schema-share figure.
MCP_PREFIX = "mcp__"


def _events(path: Path) -> Iterable[dict[str, Any]]:
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if isinstance(event, dict):
                yield event


def _is_error(content: Any) -> bool:
    text = content if isinstance(content, str) else json.dumps(content, default=str)
    return text.startswith("Error") or '"error":' in text[:40]


def _pct(part: float, whole: float) -> float:
    return round(100.0 * part / whole, 1) if whole else 0.0


def _quantile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(len(ordered) * q))]


def _tool_name(schema: Any) -> str:
    if not isinstance(schema, dict):
        return ""
    return str((schema.get("function") or schema).get("name") or "")


def compute(state_dir: Path, since: Optional[float] = None) -> dict[str, Any]:
    """Every KPI, from `<state_dir>/conversations/*.events.jsonl` and `metrics.db`.

    `since` (unix seconds) keeps only TURNS that started at or after it. It used to keep
    whole sessions by their first event, but sessions are resumed for days: the first
    after-change run (2026-09-24) saw 1 of the 3 sessions actually used that afternoon.
    A turn is the unit that runs on one build, so a straddling session contributes exactly
    its new turns — plus the tool header in force when they started.
    """
    conversations = state_dir / "conversations"
    sessions = 0
    turns: list[tuple[int, int]] = []  # (steps, tokens)
    headers: list[tuple[int, int, int]] = []  # (tools, schema chars, mcp chars)
    calls: Counter[str] = Counter()
    errors: Counter[str] = Counter()
    calls_per_step: Counter[int] = Counter()
    budget_by_tool: Counter[str] = Counter()
    compactions: Counter[str] = Counter()
    nudges: Counter[str] = Counter()
    reads = duplicate_reads = 0
    shrunk_reads = rereads_after_shrink = reshrunk_reads = 0
    budget_crossed_turns = 0
    # Where `turn/end`'s token total came from. Every model call the engine charges to a
    # turn is supposed to be logged with its tokens; the residue is spend no event
    # explains. The 2026-09-24 compaction regression hid there (17% of one session's
    # tokens were an unlogged prepare) until someone subtracted by hand.
    spend: Counter[str] = Counter()
    # Latency (Phase 5 of the 2026-09-24 audit): how long the model takes to start
    # answering, by prompt size, and every wait before a step the log cannot explain —
    # the regression's nine 30-93s pre-step stalls had no event of their own.
    ttft: dict[str, list[float]] = {}
    stalls: Counter[str] = Counter()
    # Outcomes (2026-09-29): the harness is judged by how often it finishes correctly at
    # a reasonable cost, and none of the numbers above says whether a turn FINISHED. Only
    # hard signals: how turns end (the engine's own reasons), and what a failed tool call
    # costs — the next step re-reads the whole context to recover from it.
    end_reasons: Counter[str] = Counter()
    recovery_tokens = 0
    # A step whose only call is `todo_write` re-reads the whole context to update a list
    # (2026-09-29: 3.5% of steps, 4.2% of tokens); the list belongs beside a working call.
    todo_only_tokens = 0
    todo_only_steps = 0
    for log_path in sorted(conversations.glob("*.events.jsonl")):
        # Renumbered first: every fold below keys on `turn`, and a log an engine
        # rebuild wrote holds the same number twice — `--since` then dated the new turn by
        # the old one's first row, and `(turn, step)` merged the calls of different steps.
        events = list(monotonic_turns(_events(log_path)))
        if not events:
            continue
        keep = _turn_filter(events, since)
        kept = [event for event, ok in zip(events, keep) if ok]
        if not kept:
            continue
        if since is not None and not any(e.get("type") == "request/header" for e in kept):
            # The toolset those turns ran with was announced before them.
            header = next(
                (e for e, ok in zip(reversed(events), reversed(keep))
                 if not ok and e.get("type") == "request/header"),
                None,
            )
            if header is not None:
                kept.insert(0, header)
        events = kept
        sessions += 1
        latency_fold(events, ttft, stalls)
        spend.update(spend_fold(events))
        read_args: dict[str, tuple[Any, Any]] = {}  # call_id -> (turn, path)
        shrunk: set[tuple[Any, Any]] = set()
        seen_windows: dict[tuple[Any, Any, Any], int] = {}
        writes: Counter[Any] = Counter()
        step_calls: Counter[tuple[Any, Any]] = Counter()
        crossed: set[Any] = set()
        step_tokens: dict[tuple[Any, Any], int] = {}
        failed_steps: set[tuple[Any, Any]] = set()
        step_names: dict[tuple[Any, Any], list[str]] = defaultdict(list)
        for event in events:
            kind = event.get("type")
            if kind == "assistant/message" and isinstance(event.get("usage"), dict):
                usage = event["usage"]
                step_tokens[(event.get("turn"), event.get("step"))] = int(
                    usage.get("input") or 0
                ) + int(usage.get("cache_read") or 0) + int(usage.get("cache_write") or 0)
            if kind == "turn/end":
                end_reasons[str(event.get("reason") or "?")] += 1
                steps, tokens = event.get("steps"), event.get("tokens")
                if isinstance(steps, int) and steps > 0 and isinstance(tokens, int) and tokens > 0:
                    turns.append((steps, tokens))
            elif kind == "request/header":
                tools = event.get("tools")
                if isinstance(tools, list):
                    total = sum(len(json.dumps(t)) for t in tools)
                    mcp = sum(
                        len(json.dumps(t))
                        for t in tools
                        if _tool_name(t).startswith(MCP_PREFIX)
                    )
                    headers.append((len(tools), total, mcp))
            elif kind == "tool/call":
                name = str(event.get("name") or "")
                calls[name] += 1
                step_calls[(event.get("turn"), event.get("step"))] += 1
                step_names[(event.get("turn"), event.get("step"))].append(name)
                try:
                    args = json.loads(event.get("arguments") or "{}")
                except (TypeError, ValueError):
                    args = {}
                if not isinstance(args, dict):
                    args = {}
                path = args.get("path")
                if name == "read_file":
                    reads += 1
                    turn = event.get("turn")
                    key = (turn, path)
                    if key in shrunk:
                        rereads_after_shrink += 1
                    read_args[str(event.get("call_id"))] = key
                    window = (path, args.get("start_line") or 1, args.get("max_lines"))
                    if window in seen_windows and seen_windows[window] == writes[path]:
                        duplicate_reads += 1
                    seen_windows[window] = writes[path]
                elif name in EDIT_TOOLS and path:
                    writes[path] += 1
            elif kind == "tool/result":
                # The row says so itself since 2026-10-06; older rows are read by their text.
                if event.get("is_error") or _is_error(event.get("content")):
                    errors[str(event.get("name") or "")] += 1
                    failed_steps.add((event.get("turn"), event.get("step")))
            elif kind == "tool/result/budget":
                name = str(event.get("name") or "")
                budget_by_tool[name] += 1
                if event.get("limit") == 4_000:
                    crossed.add(event.get("turn"))
                if name == "read_file":
                    key = read_args.get(str(event.get("call_id")))
                    if key is not None:
                        shrunk_reads += 1
                        if key in shrunk:
                            reshrunk_reads += 1
                        shrunk.add(key)
            elif kind == "compaction/end":
                compactions[str(event.get("reason") or "?")] += 1
            elif kind == "compaction/prune":
                compactions["prune:" + str(event.get("reason") or "?")] += 1
            elif kind in ("todo/nudge", "verify/nudge"):
                nudges[kind] += 1
        for count in step_calls.values():
            calls_per_step[min(count, 5)] += 1
        for key, names in step_names.items():
            if names == ["todo_write"]:
                todo_only_steps += 1
                todo_only_tokens += step_tokens.get(key, 0)
        for turn, step in failed_steps:
            try:
                recovery_tokens += step_tokens.get((turn, int(step) + 1), 0)
            except (TypeError, ValueError):
                pass
        budget_crossed_turns += len(crossed)

    total_steps = sum(steps for steps, _ in turns)
    total_tokens = sum(tokens for _, tokens in turns)
    bands = {}
    for band in CONTEXT_BANDS:
        in_steps = sum(s for s, t in turns if t / s > band)
        in_tokens = sum(t for s, t in turns if t / s > band)
        bands[f">{band // 1000}k"] = {
            "steps_pct": _pct(in_steps, total_steps),
            "tokens_pct": _pct(in_tokens, total_tokens),
        }
    tool_rows = {
        name: {"calls": n, "error_pct": _pct(errors[name], n)}
        for name, n in calls.most_common()
    }
    edit_calls = sum(calls[name] for name in EDIT_TOOLS)
    edit_errors = sum(errors[name] for name in EDIT_TOOLS)
    step_total = sum(calls_per_step.values())
    return {
        "sessions": sessions,
        "turns": len(turns),
        "steps": total_steps,
        "tokens": total_tokens,
        "tokens_per_step": round(total_tokens / total_steps) if total_steps else 0,
        "context_bands": bands,
        "request": {
            "headers": len(headers),
            "tools_median": statistics.median([h[0] for h in headers]) if headers else 0,
            "schema_chars_median": statistics.median([h[1] for h in headers]) if headers else 0,
            "mcp_share_median": (
                round(statistics.median([h[2] / h[1] for h in headers if h[1]]), 2)
                if headers
                else 0.0
            ),
        },
        "tools": tool_rows,
        "edits": {"calls": edit_calls, "error_pct": _pct(edit_errors, edit_calls)},
        "reads": {
            "calls": reads,
            "duplicate_windows": duplicate_reads,
            "shrunk_by_budget": shrunk_reads,
            "rereads_after_shrink": rereads_after_shrink,
            "reshrunk": reshrunk_reads,
        },
        "budget": {
            "events_by_tool": dict(budget_by_tool.most_common()),
            "turns_past_aggregate": budget_crossed_turns,
        },
        "parallelism": {
            "calls_per_step_pct": {
                (f"{k}+" if k == 5 else str(k)): _pct(v, step_total)
                for k, v in sorted(calls_per_step.items())
            }
        },
        "spend": _spend(spend),
        "latency": {
            "ttft_p50": {band: quantile(v, 0.5) for band, v in ttft.items()},
            "ttft_p90": {band: quantile(v, 0.9) for band, v in ttft.items()},
            "stalls": dict(stalls),
        },
        "compaction": dict(compactions),
        "nudges": dict(nudges),
        "outcomes": {
            **_outcomes(end_reasons, recovery_tokens, total_tokens, state_dir, since),
            "todo_only_steps": todo_only_steps,
            "todo_only_pct": _pct(todo_only_tokens, total_tokens),
        },
        "cache": _cache_hits(state_dir / "metrics.db", since),
        "off_turn": _off_turn(state_dir / "metrics.db", since),
    }


#: Turn ends that are not a verified finish: the engine's own words for "not evidence".
NOT_EVIDENCE = ("completed_unverified", "completed_check_failed")
#: Turn ends where a rail or a failure stopped the work before the model did.
STOPPED = (
    "max_iterations_exceeded", "repetition_stopped", "no_progress_stopped",
    "token_budget_exceeded", "error",
)


def _outcomes(
    reasons: Counter[str],
    recovery_tokens: int,
    total_tokens: int,
    state_dir: Path,
    since: Optional[float],
) -> dict[str, Any]:
    """How turns and goals ended, and what failed tool calls cost."""
    ended = sum(reasons.values())
    goals: Counter[str] = Counter()
    goal_rounds: list[int] = []
    for path in (state_dir / "goals").glob("*.json"):
        try:
            if since is not None and path.stat().st_mtime < since:
                continue
            goal = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        phase = str(goal.get("phase") or goal.get("status") or "?")
        blocker = (goal.get("blocker") or {}).get("code") if isinstance(goal.get("blocker"), dict) else None
        goals[f"{phase}:{blocker}" if blocker else phase] += 1
        goal_rounds.append(int(goal.get("rounds_started") or 0))
    return {
        "turns_ended": ended,
        "end_reasons": dict(reasons.most_common()),
        "not_evidence_pct": _pct(sum(reasons[r] for r in NOT_EVIDENCE), ended),
        "stopped_pct": _pct(sum(reasons[r] for r in STOPPED), ended),
        "interrupted_pct": _pct(reasons["interrupted"], ended),
        "recovery_tokens": recovery_tokens,
        "recovery_pct": _pct(recovery_tokens, total_tokens),
        "goals": dict(goals),
        "goal_rounds_median": statistics.median(goal_rounds) if goal_rounds else 0,
    }


def _spend(spend: Counter[str]) -> dict[str, Any]:
    """The turn total split by the logged call that paid for it, and what is left over."""
    report = spend_report(spend)
    total = report["turn_total"]
    return {
        "turn_total": total,
        **{f"{k}_pct": _pct(report[k], total) for k in ("main", "summarizer", "prepare", "delegated")},
        "unattributed": report["unattributed"],
        "unattributed_pct": _pct(report["unattributed"], total),
    }


def _turn_filter(events: list[dict[str, Any]], since: Optional[float]) -> list[bool]:
    """Per event: does it belong to a turn that started at or after `since`?

    A turn's start is the earliest `t` among its events. An event with no turn of its own
    (a header) is judged by its own `t`; one with neither inherits the verdict of the
    event before it, since the log is written in order.
    """
    if since is None:
        return [True] * len(events)
    starts: dict[Any, float] = {}
    for event in events:
        turn, stamp = event.get("turn"), event.get("t")
        if turn is not None and isinstance(stamp, (int, float)):
            starts[turn] = min(starts.get(turn, stamp), stamp)
    verdicts: list[bool] = []
    current = False
    for event in events:
        turn, stamp = event.get("turn"), event.get("t")
        if turn is not None and turn in starts:
            current = starts[turn] >= since
        elif isinstance(stamp, (int, float)):
            current = stamp >= since
        verdicts.append(current)
    return verdicts


def _cache_hits(db_path: Path, since: Optional[float] = None) -> dict[str, Any]:
    """Per-model cache hit rate from `model_usage` (by `day` from `since`'s date); empty
    when the store has no metrics."""
    if not db_path.is_file():
        return {}
    day = datetime.fromtimestamp(since).strftime("%Y-%m-%d") if since is not None else ""
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            rows = conn.execute(
                "SELECT model, SUM(calls), SUM(tokens_in), SUM(cache_read) "
                "FROM model_usage WHERE day >= ? GROUP BY model "
                "ORDER BY SUM(tokens_in + cache_read) DESC",
                (day,),
            ).fetchall()
        finally:
            conn.close()
    except sqlite3.Error:
        return {}
    out: dict[str, Any] = {}
    for model, n, fresh, cached in rows:
        fresh, cached = int(fresh or 0), int(cached or 0)
        out[str(model)] = {
            "calls": int(n or 0),
            "hit_pct": _pct(cached, fresh + cached),
            "input_per_call": round((fresh + cached) / n) if n else 0,
        }
    return out


def _off_turn(db_path: Path, since: Optional[float] = None) -> dict[str, int]:
    """Tokens spent by model calls outside any turn (titles, memory passes, decision
    slots), from the rollup's sentinel sessions (`__<purpose>__`)."""
    if not db_path.is_file():
        return {}
    day = datetime.fromtimestamp(since).strftime("%Y-%m-%d") if since is not None else ""
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            rows = conn.execute(
                "SELECT session_id, SUM(tokens_in + tokens_out + cache_read + cache_write) "
                "FROM model_usage WHERE day >= ? AND session_id GLOB '__*__' "
                "GROUP BY session_id ORDER BY 2 DESC",
                (day,),
            ).fetchall()
        finally:
            conn.close()
    except sqlite3.Error:
        return {}
    return {str(sid).strip("_"): int(n or 0) for sid, n in rows}


def _print(report: dict[str, Any]) -> None:
    print(
        f"sessions {report['sessions']} | turns {report['turns']} | steps {report['steps']} | "
        f"tokens {report['tokens']:,} | tokens/step {report['tokens_per_step']:,}"
    )
    for band, row in report["context_bands"].items():
        print(f"  steps {band}: {row['steps_pct']}% of steps, {row['tokens_pct']}% of tokens")
    req = report["request"]
    print(
        f"request: {req['tools_median']} tools median, {req['schema_chars_median']:,.0f} "
        f"schema chars median, MCP share {req['mcp_share_median']}"
    )
    reads = report["reads"]
    print(
        f"reads {reads['calls']}: duplicate windows {reads['duplicate_windows']}, shrunk by "
        f"budget {reads['shrunk_by_budget']}, re-read after shrink "
        f"{reads['rereads_after_shrink']}, re-shrunk {reads['reshrunk']}"
    )
    print(
        f"edits {report['edits']['calls']}: {report['edits']['error_pct']}% errors | turns "
        f"past the aggregate budget {report['budget']['turns_past_aggregate']}"
    )
    print(f"calls per step: {report['parallelism']['calls_per_step_pct']}")
    lat = report["latency"]
    bands = ", ".join(
        f"{band} {lat['ttft_p50'][band]}s/{lat['ttft_p90'][band]}s"
        for band in sorted(lat["ttft_p50"], key=band_order)
    )
    print(f"time to first token p50/p90 by prompt size: {bands}")
    print(
        f"pre-step stalls >{STALL_SECONDS:.0f}s: compaction {lat['stalls'].get('compaction', 0)}, "
        f"provider retry {lat['stalls'].get('provider retry', 0)}, "
        f"UNEXPLAINED {lat['stalls'].get('unexplained', 0)}"
    )
    spend = report["spend"]
    print(
        f"spend: main {spend['main_pct']}% | summarizer {spend['summarizer_pct']}% | prepare "
        f"{spend['prepare_pct']}% | delegated {spend['delegated_pct']}% | UNATTRIBUTED "
        f"{spend['unattributed_pct']}% ({spend['unattributed']:,} tokens no event explains)"
    )
    if report.get("off_turn"):
        parts = ", ".join(f"{k} {v:,}" for k, v in report["off_turn"].items())
        print(f"off-turn spend (tokens, outside any turn): {parts}")
    print(f"compaction: {report['compaction']} | nudges: {report['nudges']}")
    out = report["outcomes"]
    print(
        f"outcomes: {out['turns_ended']} turns | not evidence (unverified / check failed) "
        f"{out['not_evidence_pct']}% | stopped by a rail or error {out['stopped_pct']}% | "
        f"interrupted {out['interrupted_pct']}%"
    )
    print(f"  end reasons: {out['end_reasons']}")
    print(
        f"  tokens re-reading context after a failed tool call: {out['recovery_tokens']:,} "
        f"({out['recovery_pct']}% of turn tokens)"
    )
    print(
        f"  steps whose only call is todo_write: {out['todo_only_steps']} "
        f"({out['todo_only_pct']}% of turn tokens)"
    )
    if out["goals"]:
        print(f"  goals: {out['goals']} | rounds median {out['goal_rounds_median']}")
    print("tools (calls, error %):")
    for name, row in list(report["tools"].items())[:25]:
        print(f"  {name:40s} {row['calls']:6d} {row['error_pct']:6.1f}")
    if report["cache"]:
        print("cache hit by model:")
        for model, row in report["cache"].items():
            print(
                f"  {model:45s} {row['hit_pct']:5.1f}% over {row['calls']} calls, "
                f"~{row['input_per_call']:,} input tokens/call"
            )


def main(argv: Optional[list[str]] = None) -> int:
    from harness.safety.secrets import state_dir

    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--state-dir", default="", help="default: the app's state dir")
    parser.add_argument("--since", default="", help="YYYY-MM-DD: only turns started on/after")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    root = Path(args.state_dir).expanduser() if args.state_dir else state_dir()
    since = datetime.strptime(args.since, "%Y-%m-%d").timestamp() if args.since else None
    report = compute(root, since=since)
    if args.json:
        print(json.dumps(report, indent=1))
    else:
        _print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
