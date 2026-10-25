"""The Ops surface's summary store — one row per session, one row per turn.

`session_log.py` is the source of truth; this is a pre-aggregated fold beside it, so the
Ops page never rescans history (the dashboard rule: don't hammer the source). Rows are
upserted when a session is saved, refreshed by a slow background tick, and backfilled
once from any log whose file is newer than its row's watermark. Deleting a session
deletes its rows.

Two tables:

    session_metrics   one row per session — the headline/breakdown source
    turn_metrics      one row per turn   — the drilldown lists (slowest, failures)

Days are UTC (`YYYY-MM-DD` from the event stamps and the record's updated_at): the
desktop may run anywhere, and a local-day bucket would make two machines disagree about
"today" for the same session.

Nothing here computes a metric — every number was folded by `metrics.summarize` /
`metrics.turns_report` over events the engine already wrote. This module only stores and
aggregates them.
"""

from __future__ import annotations

import json
import math
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Optional

from harness.observability.metrics import provider_of_model
from .metrics_harness_report import harness_blocks

# The durable per-provider/model rollup. `provider` is the one the model was CONNECTED
# through (the model id's prefix: "openrouter:deepseek/…" → openrouter), never the wire
# upstream a router picked (DeepInfra, Relace…). Kept as its own constant because the
# v2 migration drops and recreates exactly this table.
_MODEL_USAGE_DDL = """
CREATE TABLE IF NOT EXISTS model_usage (
    session_id    TEXT NOT NULL,
    day           TEXT NOT NULL DEFAULT '',
    provider      TEXT NOT NULL DEFAULT '',
    model         TEXT NOT NULL DEFAULT '',
    calls         INTEGER NOT NULL DEFAULT 0,
    tokens_in     INTEGER NOT NULL DEFAULT 0,
    tokens_out    INTEGER NOT NULL DEFAULT 0,
    cache_read    INTEGER NOT NULL DEFAULT 0,
    cache_write   INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (session_id, day, provider, model)
);
"""

_SCHEMA = _MODEL_USAGE_DDL + """
CREATE TABLE IF NOT EXISTS session_metrics (
    session_id      TEXT PRIMARY KEY,
    day             TEXT NOT NULL DEFAULT '',
    agent           TEXT NOT NULL DEFAULT '',
    model           TEXT NOT NULL DEFAULT '',
    workspace       TEXT NOT NULL DEFAULT '',
    turns_started   INTEGER NOT NULL DEFAULT 0,
    turns_completed INTEGER NOT NULL DEFAULT 0,
    turns_failed    INTEGER NOT NULL DEFAULT 0,
    llm_calls       INTEGER NOT NULL DEFAULT 0,
    retries         INTEGER NOT NULL DEFAULT 0,
    model_errors    INTEGER NOT NULL DEFAULT 0,
    tokens_in       INTEGER NOT NULL DEFAULT 0,
    tokens_out      INTEGER NOT NULL DEFAULT 0,
    cache_read      INTEGER NOT NULL DEFAULT 0,
    cache_write     INTEGER NOT NULL DEFAULT 0,
    ttft_avg_ms     REAL,
    ttft_p95_ms     REAL,
    wall_ms         REAL,
    tool_calls      INTEGER NOT NULL DEFAULT 0,
    tool_errors     INTEGER NOT NULL DEFAULT 0,
    tool_timeouts   INTEGER NOT NULL DEFAULT 0,
    blocked         INTEGER NOT NULL DEFAULT 0,
    steps           INTEGER NOT NULL DEFAULT 0,
    loop_flags      INTEGER NOT NULL DEFAULT 0,
    compactions     INTEGER NOT NULL DEFAULT 0,
    subagents       INTEGER NOT NULL DEFAULT 0,
    tools_json      TEXT NOT NULL DEFAULT '{}',
    log_mtime       REAL NOT NULL DEFAULT 0,
    updated_at      TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS turn_metrics (
    session_id    TEXT NOT NULL,
    turn          INTEGER NOT NULL,
    day           TEXT NOT NULL DEFAULT '',
    agent         TEXT NOT NULL DEFAULT '',
    model         TEXT NOT NULL DEFAULT '',
    wall_ms       REAL,
    steps         INTEGER NOT NULL DEFAULT 0,
    failed        INTEGER NOT NULL DEFAULT 0,
    reason        TEXT NOT NULL DEFAULT '',
    tokens_in     INTEGER NOT NULL DEFAULT 0,
    tokens_out    INTEGER NOT NULL DEFAULT 0,
    tool_calls    INTEGER NOT NULL DEFAULT 0,
    tool_errors   INTEGER NOT NULL DEFAULT 0,
    tool_timeouts INTEGER NOT NULL DEFAULT 0,
    blocked       INTEGER NOT NULL DEFAULT 0,
    ttft_ms       REAL,
    loop          INTEGER NOT NULL DEFAULT 0,
    errors_json   TEXT NOT NULL DEFAULT '[]',
    PRIMARY KEY (session_id, turn)
);
CREATE INDEX IF NOT EXISTS turn_metrics_day ON turn_metrics(day);
CREATE INDEX IF NOT EXISTS turn_metrics_wall ON turn_metrics(wall_ms DESC);
CREATE INDEX IF NOT EXISTS model_usage_day ON model_usage(day);
"""

_EPOCH_DAY = time.strftime("%Y-%m-%d", time.gmtime(0))


def utc_day(stamp: Optional[float]) -> str:
    """`YYYY-MM-DD` in UTC — the one day bucket both machines can agree on."""
    if not isinstance(stamp, (int, float)) or stamp <= 0:
        return ""
    return time.strftime("%Y-%m-%d", time.gmtime(float(stamp)))


def day_of_record(updated_at: Optional[str]) -> str:
    """A session record's `updated_at` ("YYYY-MM-DD HH:MM:SS", already UTC)."""
    return str(updated_at or "")[:10]


def _pct(part: int, whole: int) -> Optional[float]:
    return round(part / whole, 4) if whole > 0 else None


def _div(part: float, whole: float) -> Optional[float]:
    return round(part / whole, 2) if whole > 0 else None


def _percentile(values: list[float], fraction: float) -> float:
    """Nearest-rank percentile, rounded like the latency numbers elsewhere."""
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int(round(fraction * (len(ordered) - 1)))))
    return round(ordered[index], 1)


class MetricsStore:
    """SQLite store for the Ops fold. One writer (the manager), many readers (routes)."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(_SCHEMA)
            # One-time migration (v2): v1 keyed rows on the wire upstream a router picked
            # (DeepInfra, Relace…), which is not the provider the model was connected
            # through. The table is a fold, not a record: drop it, recreate, and reset
            # watermarks so every log re-folds once with the provider from the model id.
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL DEFAULT '')"
            )
            migrated = self._conn.execute(
                "SELECT 1 FROM meta WHERE key = 'model_usage_v2'"
            ).fetchone()
            if not migrated:
                self._conn.execute("DROP TABLE IF EXISTS model_usage")
                self._conn.execute(_MODEL_USAGE_DDL)
                self._conn.execute(
                    "CREATE INDEX IF NOT EXISTS model_usage_day ON model_usage(day)"
                )
                self._conn.execute("UPDATE session_metrics SET log_mtime = 0")
                self._conn.execute(
                    "INSERT OR REPLACE INTO meta (key, value) VALUES ('model_usage_v2', '1')"
                )
            # v3 (2026-09-25): `harness_json` carries the harness-health fold, `deleted`
            # keeps a deleted session's numbers in every aggregate (the Metrics tab must
            # not forget spend or failures because a conversation was cleaned up), and
            # model_usage is re-folded once so tokens land on the day each call RAN, not
            # on the session's last-updated day.
            have = {r["name"] for r in self._conn.execute("PRAGMA table_info(session_metrics)")}
            if "harness_json" not in have:
                self._conn.execute(
                    "ALTER TABLE session_metrics ADD COLUMN harness_json TEXT NOT NULL DEFAULT '{}'"
                )
            if "deleted" not in have:
                self._conn.execute(
                    "ALTER TABLE session_metrics ADD COLUMN deleted INTEGER NOT NULL DEFAULT 0"
                )
            if not self._conn.execute("SELECT 1 FROM meta WHERE key = 'metrics_v3'").fetchone():
                self._conn.execute("UPDATE session_metrics SET log_mtime = 0 WHERE deleted = 0")
                self._conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('metrics_v3', '1')")
            # v4 (2026-10-01): off-turn spend (titles, memory passes) was written with no provider, so
            # the Usage tab showed those rows as "unknown". Their sessions are sentinels with no log to
            # re-fold, so the rows are repaired where they are: the provider the model id names, merged
            # into the row that already exists for it, if any.
            if not self._conn.execute("SELECT 1 FROM meta WHERE key = 'model_usage_provider'").fetchone():
                orphans = self._conn.execute(
                    "SELECT session_id, day, model, calls, tokens_in, tokens_out, cache_read, cache_write"
                    " FROM model_usage WHERE provider = '' AND model != ''"
                ).fetchall()
                for r in orphans:
                    self._add_usage(
                        r["session_id"], r["day"], provider_of_model(r["model"]), r["model"],
                        r["calls"], r["tokens_in"], r["tokens_out"], r["cache_read"], r["cache_write"],
                    )
                    self._conn.execute(
                        "DELETE FROM model_usage WHERE session_id = ? AND day = ? AND provider = '' AND model = ?",
                        (r["session_id"], r["day"], r["model"]),
                    )
                self._conn.execute(
                    "INSERT OR REPLACE INTO meta (key, value) VALUES ('model_usage_provider', '1')"
                )
            # v5 (2026-10-04): `turn_metrics` is keyed (session_id, turn), and a log
            # an engine rebuild wrote held the same turn number twice, so the later turn
            # replaced the earlier one (460 of 1,589 turns in the real store). The fold now
            # renumbers on the way in; every live session is folded once more so the rows it
            # lost come back. A deleted session has no log left to read and keeps what it has.
            if not self._conn.execute("SELECT 1 FROM meta WHERE key = 'turn_numbers_v5'").fetchone():
                self._conn.execute("UPDATE session_metrics SET log_mtime = 0 WHERE deleted = 0")
                self._conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('turn_numbers_v5', '1')")
            # v6 (2026-10-13): the harness fold carries its checks per day (`days`), so the Metrics
            # page can draw each check's week; every live log folds once more to gain them.
            if not self._conn.execute("SELECT 1 FROM meta WHERE key = 'harness_days_v6'").fetchone():
                self._conn.execute("UPDATE session_metrics SET log_mtime = 0 WHERE deleted = 0")
                self._conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('harness_days_v6', '1')")
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # -- writes -----------------------------------------------------------------
    def watermark(self, session_id: str) -> float:
        """The log mtime the stored row was folded from — 0 when there is no row."""
        with self._lock:
            row = self._conn.execute(
                "SELECT log_mtime FROM session_metrics WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        return float(row["log_mtime"]) if row else 0.0

    def upsert(
        self,
        session_id: str,
        *,
        row: dict[str, Any],
        turns: list[dict[str, Any]],
        log_mtime: float,
    ) -> None:
        columns = (
            "session_id", "day", "agent", "model", "workspace",
            "turns_started", "turns_completed", "turns_failed",
            "llm_calls", "retries", "model_errors",
            "tokens_in", "tokens_out", "cache_read", "cache_write",
            "ttft_avg_ms", "ttft_p95_ms", "wall_ms",
            "tool_calls", "tool_errors", "tool_timeouts", "blocked",
            "steps", "loop_flags", "compactions", "subagents",
            "tools_json", "harness_json", "log_mtime", "updated_at",
        )
        values = (
            session_id,
            row.get("day") or "",
            row.get("agent") or "",
            row.get("model") or "",
            row.get("workspace") or "",
            int(row.get("turns_started", 0)),
            int(row.get("turns_completed", 0)),
            int(row.get("turns_failed", 0)),
            int(row.get("llm_calls", 0)),
            int(row.get("retries", 0)),
            int(row.get("model_errors", 0)),
            int(row.get("tokens_in", 0)),
            int(row.get("tokens_out", 0)),
            int(row.get("cache_read", 0)),
            int(row.get("cache_write", 0)),
            row.get("ttft_avg_ms"),
            row.get("ttft_p95_ms"),
            row.get("wall_ms"),
            int(row.get("tool_calls", 0)),
            int(row.get("tool_errors", 0)),
            int(row.get("tool_timeouts", 0)),
            int(row.get("blocked", 0)),
            int(row.get("steps", 0)),
            int(row.get("loop_flags", 0)),
            int(row.get("compactions", 0)),
            int(row.get("subagents", 0)),
            json.dumps(row.get("tools", {}), separators=(",", ":")),
            json.dumps(row.get("harness", {}), separators=(",", ":")),
            float(log_mtime),
            time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()),
        )
        placeholders = ",".join("?" for _ in columns)
        updates = ",".join(f"{name}=excluded.{name}" for name in columns if name != "session_id")
        with self._lock:
            self._conn.execute(
                f"INSERT INTO session_metrics ({','.join(columns)}) VALUES ({placeholders}) "
                f"ON CONFLICT(session_id) DO UPDATE SET {updates}",
                values,
            )
            self._conn.execute("DELETE FROM turn_metrics WHERE session_id = ?", (session_id,))
            for turn in turns:
                self._conn.execute(
                    "INSERT OR REPLACE INTO turn_metrics ("
                    "session_id, turn, day, agent, model, wall_ms, steps, failed, reason,"
                    "tokens_in, tokens_out, tool_calls, tool_errors, tool_timeouts, blocked,"
                    "ttft_ms, loop, errors_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        session_id,
                        int(turn.get("turn") or 0),
                        turn.get("day", ""),
                        turn.get("agent", ""),
                        turn.get("model", ""),
                        turn.get("wall_ms"),
                        int(turn.get("steps", 0)),
                        1 if turn.get("failed") else 0,
                        turn.get("reason", ""),
                        int(turn.get("tokens_in", 0)),
                        int(turn.get("tokens_out", 0)),
                        int(turn.get("tool_calls", 0)),
                        int(turn.get("tool_errors", 0)),
                        int(turn.get("tool_timeouts", 0)),
                        int(turn.get("blocked", 0)),
                        turn.get("ttft_ms"),
                        1 if turn.get("loop") else 0,
                        json.dumps(turn.get("errors", []), separators=(",", ":")),
                    ),
                )
            # The per-model rollup: rewritten from this session's fold so re-upserts are
            # idempotent. `remove_session` deliberately leaves these rows alone.
            self._conn.execute("DELETE FROM model_usage WHERE session_id = ?", (session_id,))
            day = str(row.get("day", ""))
            # Per real day when the fold split it (`usage_by_day`); the session's day is
            # only the fallback for a caller that does not.
            for usage in row.get("usage_by_day") or row.get("by_model", []):
                name = str(usage.get("model") or "")
                provider = str(usage.get("provider") or "") or provider_of_model(name)
                if not provider and not name:
                    continue
                self._conn.execute(
                    "INSERT OR REPLACE INTO model_usage ("
                    "session_id, day, provider, model, calls, tokens_in, tokens_out,"
                    "cache_read, cache_write) VALUES (?,?,?,?,?,?,?,?,?)",
                    (
                        session_id,
                        str(usage.get("day") or day),
                        provider,
                        name,
                        int(usage.get("calls", 0)),
                        int(usage.get("tokens_in", 0)),
                        int(usage.get("tokens_out", 0)),
                        int(usage.get("cache_read", 0)),
                        int(usage.get("cache_write", 0)),
                    ),
                )
            self._conn.commit()

    def remove_session(self, session_id: str) -> None:
        """A deleted session keeps its NUMBERS and loses everything that identifies it.

        The Metrics tab tracks the harness and the providers over time; a conversation
        cleaned up yesterday still ran, still spent, and still failed where it failed, and
        dropping its rows rewrote history (owner call, 2026-09-25). So the counts stay in
        every aggregate, while the workspace path and the per-turn error texts go, and the
        session drops out of every per-session list."""
        with self._lock:
            self._conn.execute(
                "UPDATE session_metrics SET deleted = 1, workspace = '' WHERE session_id = ?",
                (session_id,),
            )
            self._conn.execute(
                "UPDATE turn_metrics SET errors_json = '[]' WHERE session_id = ?", (session_id,)
            )
            self._conn.commit()

    def model_usage(self, since_day: Optional[str]) -> list[dict[str, Any]]:
        """Tokens per (provider, model) from the durable rollup — the one report section
        that survives deleting its sessions. Rows keep the `breakdown` shape so the Ops
        table renders them unchanged; fields the rollup cannot know are None, never 0."""
        sql = (
            "SELECT provider, model, COUNT(DISTINCT session_id) AS sessions,"
            " SUM(calls) AS calls, SUM(tokens_in) AS tokens_in, SUM(tokens_out) AS tokens_out,"
            " SUM(cache_read) AS cache_read, SUM(cache_write) AS cache_write"
            " FROM model_usage"
        )
        params: list[Any] = []
        if since_day:
            sql += " WHERE day >= ?"
            params.append(since_day)
        sql += " GROUP BY provider, model"
        with self._lock:
            rows = list(self._conn.execute(sql, params).fetchall())
        out = []
        for row in rows:
            tokens_in = int(row["tokens_in"] or 0)
            tokens_out = int(row["tokens_out"] or 0)
            cache_read = int(row["cache_read"] or 0)
            cache_write = int(row["cache_write"] or 0)
            name = str(row["model"] or "")
            provider = str(row["provider"] or "") or provider_of_model(name)
            out.append({
                # `name` is the full model id (it already carries the provider prefix);
                # the GUI strips the prefix into its own column.
                "name": name or provider or "unknown",
                "provider": provider,
                "sessions": int(row["sessions"] or 0),
                "turns": None,
                "llm_calls": int(row["calls"] or 0),
                "retries": 0,
                "tokens_in": tokens_in,
                "tokens_out": tokens_out,
                "cache_read": cache_read,
                "cache_write": cache_write,
                "tokens": tokens_in + tokens_out + cache_read + cache_write,
                "tool_calls": 0,
                "tool_failures": 0,
                "steps": 0,
                "tool_success": None,
                "cache_hit": _pct(cache_read, tokens_in + cache_read + cache_write),
                "retry_rate": None,
                "steps_per_turn": None,
                "ttft_avg_ms": None,
            })
        out.sort(key=lambda item: -item["tokens"])
        return out

    def record_model_usage(
        self,
        session_id: str,
        *,
        provider: str = "",
        model: str = "",
        calls: int = 1,
        tokens_in: int = 0,
        tokens_out: int = 0,
        cache_read: int = 0,
        cache_write: int = 0,
        day: str = "",
    ) -> None:
        """Add one call's usage to the rollup, outside any session fold (titles, review
        assists). `session_id` must be a sentinel like `__title__`: the session fold
        rewrites rows by session_id, so a real id would be deleted on its next upsert."""
        bucket_day = day or utc_day(time.time())
        with self._lock:
            # The provider the caller did not name is the one the model id names: a title passes only
            # the model, and a row with no provider reads as "unknown" in the Usage tab.
            self._add_usage(
                session_id, bucket_day, provider or provider_of_model(model), model,
                int(calls), int(tokens_in), int(tokens_out), int(cache_read), int(cache_write),
            )
            self._conn.commit()

    def _add_usage(
        self,
        session_id: str,
        day: str,
        provider: str,
        model: str,
        calls: int,
        tokens_in: int,
        tokens_out: int,
        cache_read: int,
        cache_write: int,
    ) -> None:
        """Add to the (session, day, provider, model) row, creating it. The caller holds the lock and commits."""
        self._conn.execute(
            "INSERT INTO model_usage (session_id, day, provider, model, calls, tokens_in,"
            " tokens_out, cache_read, cache_write) VALUES (?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT(session_id, day, provider, model) DO UPDATE SET"
            " calls = calls + excluded.calls,"
            " tokens_in = tokens_in + excluded.tokens_in,"
            " tokens_out = tokens_out + excluded.tokens_out,"
            " cache_read = cache_read + excluded.cache_read,"
            " cache_write = cache_write + excluded.cache_write",
            (session_id, day, provider, model, calls, tokens_in, tokens_out, cache_read, cache_write),
        )

    # -- reads ------------------------------------------------------------------
    def _sessions(self, since_day: Optional[str]) -> list[sqlite3.Row]:
        sql = "SELECT * FROM session_metrics"
        params: list[Any] = []
        if since_day:
            sql += " WHERE day >= ?"
            params.append(since_day)
        with self._lock:
            return list(self._conn.execute(sql, params).fetchall())

    def has_rows(self) -> bool:
        with self._lock:
            row = self._conn.execute("SELECT 1 FROM session_metrics LIMIT 1").fetchone()
        return row is not None

    def report(self, since_day: Optional[str]) -> dict[str, Any]:
        """The whole Metrics payload for one range, assembled from the two tables.

        `since_day=None` means "all time". Nothing here reads a session's own record: the
        page tracks the harness and the providers, not sessions. The per-session
        lists (slowest turns, sessions by tokens, by agent, by workspace) and the titles
        joined onto them were dropped on 2026-10-13: the page drew none of them, and reading
        39 session records for their titles was 3.2-3.8 s of the 4.5 s the page took to open,
        against 6-9 ms for this report (measured on the owner's store).
        """
        sessions = self._sessions(since_day)
        gone = {row["session_id"] for row in sessions if int(row["deleted"] or 0)}
        with self._lock:
            turns_sql = "SELECT * FROM turn_metrics"
            turn_params: list[Any] = []
            if since_day:
                turns_sql += " WHERE day >= ?"
                turn_params.append(since_day)
            turns = list(self._conn.execute(turns_sql, turn_params).fetchall())

        totals = {
            key: sum(int(row[key] or 0) for row in sessions)
            for key in (
                "turns_started", "turns_completed", "turns_failed", "llm_calls",
                "retries", "model_errors", "tokens_in", "tokens_out", "cache_read",
                "cache_write", "tool_calls", "tool_errors", "tool_timeouts", "blocked",
                "steps", "loop_flags", "compactions", "subagents",
            )
        }
        # Weighted latency: sum(session_avg × turns) would need per-session turn counts;
        # every session's ttft_avg already averages its own calls, so weight by llm_calls.
        weighted_ttft = [
            (row["ttft_avg_ms"], int(row["llm_calls"] or 0))
            for row in sessions
            if row["ttft_avg_ms"] is not None and int(row["llm_calls"] or 0) > 0
        ]
        ttft_avg = (
            round(sum(v * n for v, n in weighted_ttft) / sum(n for _, n in weighted_ttft), 1)
            if weighted_ttft
            else None
        )
        p95_values = [row["ttft_p95_ms"] for row in sessions if row["ttft_p95_ms"] is not None]
        failed_turns = totals["turns_failed"]
        ok_turns = max(0, totals["turns_completed"] - failed_turns)
        failed_tokens = sum(int(row["tokens_in"] or 0) + int(row["tokens_out"] or 0) for row in turns if row["failed"])
        ok_tokens = sum(int(row["tokens_in"] or 0) + int(row["tokens_out"] or 0) for row in turns if not row["failed"])
        failed_spend = (
            round((failed_tokens / failed_turns) / (ok_tokens / ok_turns), 2)
            if failed_turns > 0 and ok_turns > 0 and ok_tokens > 0
            else None
        )
        cache_denominator = totals["tokens_in"] + totals["cache_read"] + totals["cache_write"]
        tokens_total = (
            totals["tokens_in"] + totals["tokens_out"] + totals["cache_read"] + totals["cache_write"]
        )

        headline = {
            "sessions": len(sessions),
            "turns_started": totals["turns_started"],
            "turns_completed": totals["turns_completed"],
            "turns_failed": failed_turns,
            "completion": _pct(totals["turns_completed"], totals["turns_started"]),
            "tool_calls": totals["tool_calls"],
            "tool_ok": max(0, totals["tool_calls"] - totals["tool_errors"] - totals["tool_timeouts"] - totals["blocked"]),
            "tool_success": _pct(
                max(0, totals["tool_calls"] - totals["tool_errors"] - totals["tool_timeouts"] - totals["blocked"]),
                totals["tool_calls"],
            ),
            "tool_errors": totals["tool_errors"],
            "tool_timeouts": totals["tool_timeouts"],
            "blocked": totals["blocked"],
            "llm_calls": totals["llm_calls"],
            "retries": totals["retries"],
            "retry_rate": _pct(totals["retries"], totals["llm_calls"]),
            "model_errors": totals["model_errors"],
            "tokens_in": totals["tokens_in"],
            "tokens_out": totals["tokens_out"],
            "cache_read": totals["cache_read"],
            "cache_write": totals["cache_write"],
            # The two numbers the "how many tokens did I use" question actually wants:
            # everything that crossed the wire, and how much of it the cache served.
            "tokens_total": tokens_total,
            "cache_served": totals["cache_read"],
            "cache_hit": _pct(totals["cache_read"], cache_denominator),
            "tokens_per_turn": _div(tokens_total, totals["turns_completed"]),
            "ttft_avg_ms": ttft_avg,
            "ttft_p95_ms": max(p95_values) if p95_values else None,
            "steps": totals["steps"],
            "steps_per_turn": _div(totals["steps"], totals["turns_started"]),
            "loop_flags": totals["loop_flags"],
            "failed_spend": failed_spend,
            "compactions": totals["compactions"],
            "subagents": totals["subagents"],
        }

        daily: dict[str, dict[str, Any]] = {}
        for row in turns:
            day = str(row["day"] or "")
            if not day:
                continue
            bucket = daily.setdefault(
                day,
                {"day": day, "turns": 0, "failed": 0, "llm_calls": 0, "tool_calls": 0, "tool_failures": 0,
                 "tokens_in": 0, "tokens_out": 0, "cache_read": 0, "cache_write": 0, "wall_ms": 0.0, "loops": 0,
                 "steps": 0, "blocked": 0, "ttft": []},
            )
            bucket["turns"] += 1
            bucket["failed"] += int(row["failed"] or 0)
            bucket["tool_calls"] += int(row["tool_calls"] or 0)
            bucket["tool_failures"] += int(row["tool_errors"] or 0) + int(row["tool_timeouts"] or 0)
            bucket["tokens_in"] += int(row["tokens_in"] or 0)
            bucket["tokens_out"] += int(row["tokens_out"] or 0)
            bucket["wall_ms"] += float(row["wall_ms"] or 0.0)
            bucket["loops"] += int(row["loop"] or 0)
            bucket["steps"] += int(row["steps"] or 0)
            bucket["blocked"] += int(row["blocked"] or 0)
            if row["ttft_ms"] is not None:
                bucket["ttft"].append(float(row["ttft_ms"]))
        # llm_calls/cache live on the session rows only; add them per day from sessions.
        for row in sessions:
            bucket = daily.get(str(row["day"] or ""))
            if bucket is not None:
                bucket["llm_calls"] += int(row["llm_calls"] or 0)
                bucket["cache_read"] += int(row["cache_read"] or 0)
                bucket["cache_write"] += int(row["cache_write"] or 0)
        for bucket in daily.values():
            bucket["tool_error_rate"] = _pct(bucket["tool_failures"], bucket["tool_calls"])
            bucket["tokens"] = bucket["tokens_in"] + bucket["tokens_out"] + bucket["cache_read"] + bucket["cache_write"]
            bucket["completion"] = _pct(bucket["turns"] - bucket["failed"], bucket["turns"])
            bucket["steps_per_turn"] = _div(bucket["steps"], bucket["turns"])
            samples = bucket.pop("ttft")
            bucket["ttft_p95_ms"] = _percentile(samples, 0.95) if samples else None
            bucket["ttft_avg_ms"] = round(sum(samples) / len(samples), 1) if samples else None
        daily_rows = [daily[day] for day in sorted(daily)]

        def breakdown(key: str) -> list[dict[str, Any]]:
            grouped: dict[str, dict[str, Any]] = {}
            for row in sessions:
                name = str(row[key] or "")
                if not name:
                    continue
                bucket = grouped.setdefault(
                    name,
                    {"name": name, "sessions": 0, "turns": 0, "llm_calls": 0, "retries": 0,
                     "tokens": 0, "cache_read": 0, "tokens_in": 0, "cache_write": 0,
                     "tool_calls": 0, "tool_failures": 0, "steps": 0, "ttft": []},
                )
                bucket["sessions"] += 1
                bucket["turns"] += int(row["turns_started"] or 0)
                bucket["llm_calls"] += int(row["llm_calls"] or 0)
                bucket["retries"] += int(row["retries"] or 0)
                bucket["tokens_in"] += int(row["tokens_in"] or 0)
                bucket["tokens_out"] = bucket.get("tokens_out", 0) + int(row["tokens_out"] or 0)
                bucket["cache_read"] += int(row["cache_read"] or 0)
                bucket["cache_write"] += int(row["cache_write"] or 0)
                bucket["tool_calls"] += int(row["tool_calls"] or 0)
                bucket["tool_failures"] += int(row["tool_errors"] or 0) + int(row["tool_timeouts"] or 0) + int(row["blocked"] or 0)
                bucket["steps"] += int(row["steps"] or 0)
                if row["ttft_avg_ms"] is not None:
                    bucket["ttft"].append((row["ttft_avg_ms"], int(row["llm_calls"] or 0)))
            out = []
            for bucket in grouped.values():
                weights = bucket.pop("ttft")
                total_tokens = (
                    bucket["tokens_in"] + bucket.get("tokens_out", 0) + bucket["cache_read"] + bucket["cache_write"]
                )
                out.append({
                    **bucket,
                    "tokens": total_tokens,
                    "tool_success": _pct(bucket["tool_calls"] - bucket["tool_failures"], bucket["tool_calls"]),
                    "cache_hit": _pct(bucket["cache_read"], bucket["tokens_in"] + bucket["cache_read"] + bucket["cache_write"]),
                    "retry_rate": _pct(bucket["retries"], bucket["llm_calls"]),
                    "steps_per_turn": _div(bucket["steps"], bucket["turns"]),
                    "ttft_avg_ms": (
                        round(sum(v * n for v, n in weights) / sum(n for _, n in weights), 1)
                        if weights and sum(n for _, n in weights) > 0
                        else None
                    ),
                })
            out.sort(key=lambda item: -item["tokens"])
            return out

        tools: dict[str, dict[str, Any]] = {}
        for row in sessions:
            try:
                fold = json.loads(row["tools_json"] or "{}")
            except (TypeError, ValueError):
                fold = {}
            for name, stats in (fold or {}).items():
                bucket = tools.setdefault(name, {"name": name, "calls": 0, "errors": 0, "timeouts": 0, "blocked": 0, "p95": []})
                bucket["calls"] += int(stats.get("calls", 0))
                bucket["errors"] += int(stats.get("errors", 0))
                bucket["timeouts"] += int(stats.get("timeouts", 0))
                bucket["blocked"] += int(stats.get("blocked", 0))
                if stats.get("p95_ms") is not None:
                    bucket["p95"].append(float(stats["p95_ms"]))
        tool_rows = []
        for bucket in tools.values():
            p95 = bucket.pop("p95")
            tool_rows.append({**bucket, "p95_ms": max(p95) if p95 else None})
        tool_rows.sort(key=lambda item: -item["calls"])

        failures: list[dict[str, Any]] = []
        for row in sorted(turns, key=lambda item: (item["day"] or "", item["turn"]), reverse=True):
            if not (row["tool_errors"] or row["tool_timeouts"] or row["blocked"]):
                continue
            if row["session_id"] in gone:
                continue
            try:
                errors = json.loads(row["errors_json"] or "[]")
            except (TypeError, ValueError):
                errors = []
            for error in errors[:3]:
                failures.append({
                    "session_id": row["session_id"],
                    "turn": int(row["turn"]),
                    "day": row["day"],
                    "agent": row["agent"],
                    "tool": str(error.get("tool") or ""),
                    "status": str(error.get("status") or ""),
                    "reason": str(error.get("reason") or ""),
                })
            if len(failures) >= 20:
                break

        return {
            "headline": headline,
            "daily": daily_rows,
            # Durable first: the rollup outlives sessions, so it is the honest answer to
            # "what did each model cost me". The session fold is only the fallback for a
            # store whose rollup is still empty (pre-migration, mid-backfill).
            "by_model": self.model_usage(since_day) or breakdown("model"),
            "tools": tool_rows[:12],
            "failures": failures[:20],
            "notable": self._notable(daily_rows),
            **harness_blocks(self._conn, self._lock, sessions, since_day),
        }

    @staticmethod
    def _notable(daily: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """±2σ against the series' own history — static thresholds only cause fatigue.

        Only the LAST day is judged, and only when at least a week of history exists to
        judge it against; a young install gets an empty strip, not noise.
        """
        if len(daily) < 8:
            return []
        history, latest = daily[:-1], daily[-1]
        out: list[dict[str, Any]] = []

        def flag(key: str, *, higher_is_bad: bool, level: str, label: str) -> None:
            values = [float(row[key]) for row in history if row.get(key) is not None]
            current = latest.get(key)
            if current is None or len(values) < 7:
                return
            mean = sum(values) / len(values)
            variance = sum((v - mean) ** 2 for v in values) / len(values)
            sigma = math.sqrt(variance)
            if sigma <= 0:
                # A metric that never moved and then moved: no z-score exists, but the
                # move itself is the signal (a flat 0% failure rate turning non-zero).
                if higher_is_bad and float(current) > max(mean * 1.5, 1e-9):
                    out.append({
                        "day": latest["day"],
                        "level": level,
                        "kind": key,
                        "z": None,
                        "text": f"{label} {current} — above the flat {len(values)}-day baseline ({mean:g}).",
                    })
                return
            z = (float(current) - mean) / sigma
            if (higher_is_bad and z >= 2.0) or (not higher_is_bad and z <= -2.0):
                out.append({
                    "day": latest["day"],
                    "level": level,
                    "kind": key,
                    "z": round(z, 1),
                    "text": f"{label} {current} — {abs(round(z, 1))}σ {'above' if z > 0 else 'below'} the {len(values)}-day baseline.",
                })

        flag("tool_error_rate", higher_is_bad=True, level="warn", label="Tool failure rate")
        flag("tokens", higher_is_bad=True, level="warn", label="Token spend")
        flag("failed", higher_is_bad=True, level="err", label="Failed turns")
        if latest.get("loops"):
            out.append({
                "day": latest["day"],
                "level": "warn",
                "kind": "loops",
                "z": None,
                "text": f"{latest['loops']} turn(s) hit the loop flag — repeated identical tool calls.",
            })
        return out[:4]
