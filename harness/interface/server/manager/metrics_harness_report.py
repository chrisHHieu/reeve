"""The Metrics tab's harness and usage blocks, aggregated across sessions.

Split from `metrics_store.py` (size ceiling). Each session row carries its own
`harness_json` fold (`harness.observability.metrics_harness.harness_fold`); this sums them over a
range, and reads the durable `model_usage` rollup for the per-day, per-provider token
series and for the background spend (titles and review assists, the sentinel sessions
`__<purpose>__`; rows from the removed memory passes and from evals stay as
history). Deleted sessions are included: their numbers stay.
"""

from __future__ import annotations

import json
import sqlite3
from collections import Counter
from typing import Any, Optional

from harness.observability.metrics_harness import band_order, quantile


def _pct(part: float, whole: float) -> Optional[float]:
    return round(part / whole, 4) if whole else None


def harness_blocks(
    conn: sqlite3.Connection, lock: Any, sessions: list[sqlite3.Row], since_day: Optional[str]
) -> dict[str, Any]:
    spend: Counter[str] = Counter()
    stalls: Counter[str] = Counter()
    compaction: Counter[str] = Counter()
    prepares: Counter[str] = Counter()
    ttft: dict[str, list[float]] = {}
    partial: Counter[str] = Counter()
    days: dict[str, dict[str, Any]] = {}
    for row in sessions:
        try:
            fold = json.loads(row["harness_json"] or "{}")
        except (TypeError, ValueError, IndexError):
            fold = {}
        if not isinstance(fold, dict):
            continue
        spend.update({k: int(v) for k, v in (fold.get("spend") or {}).items() if isinstance(v, int)})
        stalls.update(fold.get("stalls") or {})
        compaction.update(fold.get("compaction") or {})
        prepares.update(fold.get("prepares") or {})
        for name, values in (fold.get("ttft") or {}).items():
            ttft.setdefault(name, []).extend(float(v) for v in values if isinstance(v, (int, float)))
        for model in fold.get("usage_partial") or []:
            partial[str(model)] += 1
        for day, values in (fold.get("days") or {}).items():
            if not isinstance(values, dict) or (since_day and str(day) < since_day):
                continue
            bucket = days.setdefault(
                str(day), {"turn_total": 0, "unattributed": 0, "unexplained": 0, "compaction_failed": 0, "routes": set()}
            )
            for key in ("turn_total", "unattributed", "unexplained", "compaction_failed"):
                bucket[key] += int(values.get(key) or 0)
            bucket["routes"].update(str(m) for m in values.get("usage_partial") or [])

    total = spend.get("turn_total", 0)
    harness = {
        "spend": {
            "turn_total": total,
            **{k: spend.get(k, 0) for k in ("main", "summarizer", "prepare", "delegated", "unattributed")},
            "unattributed_pct": _pct(spend.get("unattributed", 0), total),
        },
        "stalls": {k: stalls.get(k, 0) for k in ("compaction", "provider retry", "unexplained")},
        "ttft": [
            {
                "band": name,
                "p50_ms": round(quantile(values, 0.5) * 1000),
                "p90_ms": round(quantile(values, 0.9) * 1000),
                "samples": len(values),
            }
            for name, values in sorted(ttft.items(), key=lambda item: band_order(item[0]))
            if values
        ],
        "compaction": dict(compaction.most_common()),
        "prepares": dict(prepares.most_common()),
        "usage_partial": [{"model": m, "sessions": n} for m, n in partial.most_common()],
        # The checks that had only a range total, per day (2026-10-13). A
        # session folded before the days existed contributes to the totals above only.
        "daily": [
            {
                "day": day,
                "turn_total": bucket["turn_total"],
                "unattributed": bucket["unattributed"],
                "unattributed_pct": _pct(bucket["unattributed"], bucket["turn_total"]),
                "unexplained": bucket["unexplained"],
                "compaction_failed": bucket["compaction_failed"],
                "usage_partial": len(bucket["routes"]),
            }
            for day, bucket in sorted(days.items())
        ],
    }

    where, params = ("WHERE day >= ?", [since_day]) if since_day else ("", [])
    with lock:
        daily_rows = conn.execute(
            "SELECT day, provider, SUM(calls) AS calls, SUM(tokens_in) AS tokens_in,"
            " SUM(tokens_out) AS tokens_out, SUM(cache_read) AS cache_read,"
            " SUM(cache_write) AS cache_write FROM model_usage "
            + where
            + " GROUP BY day, provider ORDER BY day",
            params,
        ).fetchall()
        background_rows = conn.execute(
            "SELECT session_id, SUM(calls) AS calls,"
            " SUM(tokens_in + tokens_out + cache_read + cache_write) AS tokens FROM model_usage "
            + (where + " AND" if where else "WHERE")
            + " session_id GLOB '__*__' GROUP BY session_id ORDER BY 3 DESC",
            params,
        ).fetchall()
    usage_daily = []
    for row in daily_rows:
        parts = {k: int(row[k] or 0) for k in ("tokens_in", "tokens_out", "cache_read", "cache_write")}
        usage_daily.append(
            {
                "day": str(row["day"] or ""),
                "provider": str(row["provider"] or "") or "unknown",
                "calls": int(row["calls"] or 0),
                **parts,
                "tokens": sum(parts.values()),
            }
        )
    # The eval sentinel's rows stay in the totals (it was real spend) and leave this list: evals are
    # not on the page, they run by hand (2026-10-14), and nothing writes `__eval__` any more.
    background = [
        {
            "purpose": str(row["session_id"]).strip("_"),
            "calls": int(row["calls"] or 0),
            "tokens": int(row["tokens"] or 0),
        }
        for row in background_rows
        if str(row["session_id"]) != "__eval__"
    ]
    return {"harness": harness, "usage_daily": usage_daily, "background": background}


def usage_between(conn: sqlite3.Connection, lock: Any, since_day: str, until_day: str) -> dict[str, Any]:
    """Tokens and calls from the durable rollup for days in [since_day, until_day): the
    period before the one on screen, so each number can say how it moved."""
    with lock:
        row = conn.execute(
            "SELECT SUM(calls) AS calls, SUM(tokens_in) AS tokens_in, SUM(tokens_out) AS tokens_out,"
            " SUM(cache_read) AS cache_read, SUM(cache_write) AS cache_write"
            " FROM model_usage WHERE day >= ? AND day < ?",
            (since_day, until_day),
        ).fetchone()
    parts = {k: int((row[k] if row else 0) or 0) for k in ("tokens_in", "tokens_out", "cache_read", "cache_write")}
    return {
        "since": since_day,
        "until": until_day,
        "calls": int((row["calls"] if row else 0) or 0),
        **parts,
        "tokens": sum(parts.values()),
        "cache_hit": _pct(parts["cache_read"], parts["tokens_in"] + parts["cache_read"] + parts["cache_write"]),
    }


__all__ = ["harness_blocks", "usage_between"]
