"""The Ops surface's data plane — fold, store, backfill, and the report payload.

`session_log.py` records what happened; `metrics.py` folds one log into numbers;
`metrics_store.py` keeps those folds so the page never rescans history. This mixin is the
seam that connects them to the session lifecycle:

    each finished step → a fold + upsert on a worker thread, at most every ~1.5 s per session
                         (`note_metrics_progress`), then a `metrics_changed` push to the page
    save()             → the same scheduled fold (never blocks or breaks the save)
    delete_session()   → numbers stay, identity goes (soft delete)
    boot + a 60s tick  → backfill any log newer than its row's watermark

Everything here is best-effort by design: an ops report must never be able to fail a
turn, and a missing row is a stale number, not a broken session.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from harness.observability.metrics import summarize, turns_report
from harness.observability.metrics_harness import harness_fold, usage_by_day
from harness.session.session_log import SessionLog
from harness.observability.self_review import ships, sources
from .metrics_by_model import harness_between, with_harness
from .metrics_harness_report import usage_between
from .metrics_store import MetricsStore, day_of_record, utc_day

logger = logging.getLogger("harness.interface.server")

#: A session's log is folded at most this often through a step or a `save()` — `flush_session`
#: runs before every consequential tool call, and a full fold each time would be quadratic. A fold
#: of the largest real log (14 MB, 11,000 events) took 156 ms, so about 1.5 s keeps it a few
#: percent of one thread while the page still moves with the agent. It was 10 s, and only `save()`
#: asked, so a step that was not a tool call did not reach the page until the turn ended.
_FOLD_MIN_INTERVAL_S = 1.5
#: A step is folded no sooner than this after it asks, so the row it just logged has landed.
_FOLD_SETTLE_S = 0.25
#: The background tick re-folds dirty logs so a session that stops saving (idle, crashed,
#: app closed) still lands in the report within a minute.
_TICK_S = 60.0
#: Backfill work per pass — the store is incremental, so a big history fills over ticks.
_BACKFILL_LIMIT = 200


class MetricsApiMixin:
    # -- the fold ---------------------------------------------------------------
    def _record_session_metrics(self, session_id: str, *, force: bool = False) -> None:
        """Fold one session's event log into the ops store (best-effort).

        `force=True` is the backfill path: folded now, on the caller's thread. Anything else is
        scheduled (`_schedule_metrics_fold`), so a `save()` or a step never waits on a fold.
        """
        if force:
            self._fold_session_metrics(session_id)
            return
        try:
            self._schedule_metrics_fold(session_id)
        except Exception:  # noqa: BLE001 — a report must never break a save
            logger.debug("metrics fold could not be scheduled for %s", session_id, exc_info=True)

    def note_metrics_progress(self, session_id: str) -> None:
        """A step of this session finished (a model reply or a tool result): fold it soon and tell the page.

        Called from the turn loops on `STEP_EVENTS`. Cheap and never raises: it only arms a timer."""
        try:
            self._schedule_metrics_fold(session_id)
        except Exception:  # noqa: BLE001 — telling the page must never cost a turn
            logger.debug("metrics progress note failed for %s", session_id, exc_info=True)

    def _schedule_metrics_fold(self, session_id: str) -> None:
        """Arm one fold for this session, no sooner than `_FOLD_MIN_INTERVAL_S` after the last.

        A request that arrives while one is armed adds nothing: the armed fold reads the newest log when
        it runs, so the last step of a burst is always in it (the trailing fold the old debounce lacked)."""
        with self._metrics_lock:
            if session_id in self._metrics_pending:
                return
            wait = max(
                _FOLD_SETTLE_S,
                self._metrics_folded_at.get(session_id, 0.0) + _FOLD_MIN_INTERVAL_S - time.monotonic(),
            )
            timer = threading.Timer(wait, self._run_scheduled_metrics_fold, args=(session_id,))
            timer.daemon = True
            self._metrics_pending[session_id] = timer
        timer.start()

    def _run_scheduled_metrics_fold(self, session_id: str) -> None:
        with self._metrics_lock:
            self._metrics_pending.pop(session_id, None)  # a step during the fold arms the next one
        if self._fold_session_metrics(session_id):
            self._announce_metrics_changed(session_id)

    def _announce_metrics_changed(self, session_id: str) -> None:
        """Tell every open page the numbers moved (`/ws/events`). From a worker thread, so through the loop."""
        loop = getattr(self, "_loop", None)
        if loop is None or loop.is_closed():
            return
        try:
            asyncio.run_coroutine_threadsafe(
                self.broadcast_event({"type": "metrics_changed", "data": {"session_id": session_id}}), loop
            )
        except Exception:  # noqa: BLE001
            logger.debug("metrics_changed announce failed", exc_info=True)

    def _fold_session_metrics(self, session_id: str) -> bool:
        """Fold one session's event log into the ops store and say whether a row was written.

        One fold at a time: the log is read whole, and a slower fold that started earlier must not land
        after (and over) a newer one. Any failure is logged and swallowed — this is a report, not a
        durability gate.
        """
        with self._metrics_fold_lock:
            self._metrics_folded_at[session_id] = time.monotonic()
            return bool(self._fold_session_body(session_id))

    def _fold_session_body(self, session_id: str) -> bool:
        try:
            log_path = self.session_store.conv_dir / f"{session_id}.events.jsonl"
            if not log_path.exists():
                return
            events = SessionLog(log_path).read()
            if not events:
                return

            report = summarize(events)
            turns = turns_report(events)
            record = self.session_store.load(session_id)
            agent = str(getattr(record, "agent", "") or "")
            model = str(getattr(record, "model", "") or "")
            workspace = str(getattr(record, "workspace", "") or "")
            day = day_of_record(getattr(record, "updated_at", ""))
            # A record whose updated_at is missing still gets a day: the log's own stamps.
            if not day and turns:
                day = turns[-1].get("day") or ""

            turns_block = report.get("turns", {})
            by_reason = turns_block.get("by_reason", {}) if isinstance(turns_block, dict) else {}
            latency = report.get("latency", {})
            tools = report.get("tools", {})
            process = report.get("process", {})
            tokens = report.get("tokens", {})
            llm = report.get("llm", {})

            wall_ms = sum(float(row.get("wall_ms") or 0.0) for row in turns) or None
            row = {
                "day": day,
                "agent": agent,
                "model": model,
                "workspace": workspace,
                "turns_started": int(turns_block.get("started", 0) or 0),
                # Never more than started: a session can log more `turn/end` than
                # `turn/start` (a synthetic close after a crash, a reload renumbering its
                # turns), and the Metrics tab showed a 100.2% completion rate from it.
                "turns_completed": min(
                    int(turns_block.get("ended", 0) or 0),
                    int(turns_block.get("started", 0) or 0),
                ),
                "turns_failed": int(by_reason.get("error", 0) or 0),
                "llm_calls": int(llm.get("calls", 0) or 0),
                "retries": int(llm.get("retries", 0) or 0),
                "model_errors": int(process.get("model_errors", 0) or 0),
                "tokens_in": int(tokens.get("input", 0) or 0),
                "tokens_out": int(tokens.get("output", 0) or 0),
                "cache_read": int(tokens.get("cache_read", 0) or 0),
                "cache_write": int(tokens.get("cache_write", 0) or 0),
                "ttft_avg_ms": latency.get("ttft_ms_avg"),
                "ttft_p95_ms": latency.get("ttft_ms_p95"),
                "wall_ms": wall_ms,
                "tool_calls": int(tools.get("calls", 0) or 0),
                "tool_errors": int(tools.get("by_status", {}).get("error", 0) or 0),
                "tool_timeouts": int(tools.get("by_status", {}).get("timeout", 0) or 0),
                "blocked": int(tools.get("blocked", 0) or 0),
                "steps": int(process.get("steps", 0) or 0),
                "loop_flags": int(process.get("loop_flags", 0) or 0),
                "compactions": int(report.get("compactions", 0) or 0),
                "subagents": int(process.get("subagents", 0) or 0),
                "tools": tools.get("by_name", {}),
                # The per-(upstream, model) split from the same fold; the store keeps it
                # in a table that deleting the session does not touch.
                "by_model": report.get("by_model", []),
                # Tokens by the day each call ran, and the harness-health fold — both
                # outlive the session (`metrics_store.remove_session` keeps numbers).
                "usage_by_day": usage_by_day(events),
                "harness": harness_fold(events),
            }

            turn_rows = []
            for turn in turns:
                token_split = turn.get("tokens", {})
                turn_rows.append({
                    "turn": turn.get("turn"),
                    "day": utc_day(turn.get("started")),
                    "agent": agent,
                    "model": str(turn.get("model") or model),
                    "wall_ms": turn.get("wall_ms"),
                    "steps": int(turn.get("steps", 0) or 0),
                    "failed": bool(turn.get("failed")),
                    "reason": str(turn.get("reason") or ""),
                    "tokens_in": int(token_split.get("input", 0) or 0),
                    "tokens_out": int(token_split.get("output", 0) or 0),
                    "tool_calls": int(turn.get("tool_calls", 0) or 0),
                    "tool_errors": int(turn.get("tool_errors", 0) or 0),
                    "tool_timeouts": int(turn.get("tool_timeouts", 0) or 0),
                    "blocked": int(turn.get("blocked", 0) or 0),
                    "ttft_ms": turn.get("ttft_ms_avg"),
                    "loop": bool(turn.get("loop")),
                    "errors": turn.get("errors", []),
                })

            self.metrics_store.upsert(
                session_id, row=row, turns=turn_rows, log_mtime=log_path.stat().st_mtime
            )
            return True
        except Exception:  # noqa: BLE001 — a report must never break a save
            logger.warning("session metrics fold failed for %s", session_id, exc_info=True)
        return False

    # -- backfill ---------------------------------------------------------------
    def _backfill_metrics(self, *, limit: int = _BACKFILL_LIMIT) -> int:
        """Fold every log newer than its stored watermark; returns how many were folded."""
        folded = 0
        try:
            records = self.session_store.list(include_children=True)
        except Exception:  # noqa: BLE001
            return 0
        for record in records:
            if folded >= limit:
                break
            session_id = record.session_id
            if session_id.startswith("__"):
                continue  # internal threads are not the person's sessions
            log_path = self.session_store.conv_dir / f"{session_id}.events.jsonl"
            try:
                if not log_path.exists():
                    continue
                if log_path.stat().st_mtime <= self.metrics_store.watermark(session_id):
                    continue
            except OSError:
                continue
            self._record_session_metrics(session_id, force=True)
            folded += 1
        return folded

    def _start_metrics_ticker(self) -> None:
        """A daemon tick so idle/crashed sessions still reach the report.

        The first pass is the one-time backfill; later passes are cheap (the watermark
        skips everything already folded).
        """
        def tick() -> None:
            # It ends with its manager: a restarted server builds another one with its own tick.
            while not getattr(self, "shutting_down", False):
                try:
                    self._backfill_metrics()
                except Exception:  # noqa: BLE001
                    logger.warning("metrics backfill tick failed", exc_info=True)
                time.sleep(_TICK_S)

        threading.Thread(target=tick, name="reeve-metrics", daemon=True).start()

    # -- the report -------------------------------------------------------------
    def ops_metrics(self, range_name: str = "7d") -> dict[str, Any]:
        """The Metrics page's whole payload for one range, from the metrics tables alone.

        It joined session titles onto three lists; loading a session record to read its
        title cost ~0.1 s each, 3.2-3.8 s per open on the owner's store, for titles the page
        never showed (2026-10-13). No session record is read here."""
        today = datetime.now(timezone.utc).date()
        span = {"today": 1, "30d": 30, "all": 0}.get(range_name, 7)
        if range_name not in ("today", "30d", "all"):
            range_name = "7d"  # the default the UI ships with
        since: Optional[str] = (today - timedelta(days=span - 1)).isoformat() if span else None

        store = self.metrics_store
        payload = store.report(since)
        payload["range"] = range_name
        payload["backfilling"] = not store.has_rows()
        # Each model's turns, completion, tools and steps beside its tokens: a harness change is
        # judged on one model at a time.
        payload["by_model"] = with_harness(payload.get("by_model") or [], store._sessions(since))
        # The same span just before, so each number can say how it moved; all time has none.
        before = (today - timedelta(days=2 * span - 1)).isoformat() if since else None
        payload["previous"] = (
            {**usage_between(store._conn, store._lock, before, since), "harness": harness_between(store._conn, store._lock, before, since)}
            if since
            else None
        )
        # The days a harness change went live, for the marks on the day charts, and the harness's own
        # review of its logs. Evals are not on the page: they run by hand and spend tokens (2026-10-14).
        found = sources()
        payload["ships"] = [s for s in ships(found["ledger"] if found else None) if not since or s["day"] >= since]
        payload["review"] = self.self_review.payload() if found else None
        return payload

    def refresh_self_review(self) -> bool:
        """Start the self-review now (the page's refresh). False when one is under way or the
        scripts are not in this build."""
        return sources() is not None and self.self_review.start()


__all__ = ["MetricsApiMixin", "MetricsStore"]
