"""The Metrics page's harness numbers by model, and the same numbers for the span before.

A harness change is judged on one model at a time (the owner's rule): a page that adds
every model together shows a switch to a weaker model as the harness getting worse. The token
rollup (`model_usage`) knows each call's model but not turns or tools, so these come from the
session rows, by the session's model, with the definitions the headline uses (`metrics_store.report`).
Split from `metrics_store.py` (size ceiling).
"""

from __future__ import annotations

import sqlite3
from typing import Any, Optional

_KEYS = ("turns_started", "turns_completed", "turns_failed", "tool_calls", "tool_errors", "tool_timeouts",
         "blocked", "steps", "tokens_in", "tokens_out", "cache_read", "cache_write")


def _ratio(part: float, whole: float, digits: int = 4) -> Optional[float]:
    return round(part / whole, digits) if whole else None


def _numbers(rows: list[sqlite3.Row]) -> dict[str, Any]:
    total = {key: sum(int(row[key] or 0) for row in rows) for key in _KEYS}
    ok_tools = max(0, total["tool_calls"] - total["tool_errors"] - total["tool_timeouts"] - total["blocked"])
    tokens = total["tokens_in"] + total["tokens_out"] + total["cache_read"] + total["cache_write"]
    p95 = [row["ttft_p95_ms"] for row in rows if row["ttft_p95_ms"] is not None]
    return {
        "turns": total["turns_started"],
        "turns_failed": total["turns_failed"],
        "completion": _ratio(total["turns_completed"], total["turns_started"]),
        "tool_success": _ratio(ok_tools, total["tool_calls"]),
        "steps_per_turn": _ratio(total["steps"], total["turns_started"], 2),
        "tokens_per_turn": _ratio(tokens, total["turns_completed"], 1),
        "ttft_p95_ms": max(p95) if p95 else None,
    }


def with_harness(by_model: list[dict[str, Any]], sessions: list[sqlite3.Row]) -> list[dict[str, Any]]:
    """`by_model` (the token rollup's rows) with each model's turns, completion, tool success,
    steps and tokens per turn from the sessions that ran on it. A model with no session of its own
    (one a fallback reached) keeps its tokens and has no harness numbers."""
    grouped: dict[str, list[sqlite3.Row]] = {}
    for row in sessions:
        grouped.setdefault(str(row["model"] or ""), []).append(row)
    out = []
    for row in by_model:
        mine = grouped.get(str(row.get("name") or ""))
        out.append({**row, **({"harness": _numbers(mine)} if mine else {})})
    return out


def harness_between(conn: sqlite3.Connection, lock: Any, since: str, until: str) -> dict[str, Any]:
    """The headline harness numbers for sessions whose day is in `[since, until)`: what each tile on
    the page is compared with."""
    with lock:
        rows = list(conn.execute("SELECT * FROM session_metrics WHERE day >= ? AND day < ?", (since, until)).fetchall())
    return _numbers(rows)


__all__ = ["harness_between", "with_harness"]
