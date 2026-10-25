"""Session metrics folded from the event log (session_log.py).

The log answers "what happened"; this answers "what did it cost and how well did it
run" — the numbers a production harness is judged on: token spend with the cache split,
latency (time to first token, step time), retry and failure rates, and tool efficiency
(count, error/timeout share, wall time). Pure functions over a list of events, so a
test pins each number from a fixture instead of a live provider run.

Deliberately NOT a new event stream: everything here is a fold over what the engine
already records, so adding a metric never changes what the engine does. A metric that
would need new events is a proposal about the LOG, not about this module.
"""

from __future__ import annotations

from collections import Counter
from typing import Any, Iterable, Optional

from harness.session.turn_numbers import monotonic_turns


def provider_of_model(model: str) -> str:
    """The provider a model id was CONNECTED through, for the usage rollup: the prefix of a routed id
    (`openrouter:deepseek/...` -> openrouter), and `openai` for a bare one, which is where the router sends
    an id with no prefix. Never the serving upstream a router picked. Empty only for no model at all: a
    row with a model and no provider would show as "unknown" in the Usage tab, which is a thing nobody
    can act on."""
    text = str(model or "").strip()
    if not text:
        return ""
    return text.split(":", 1)[0] if ":" in text else "openai"

#: Timing keys the engine records on `assistant/message` and `tool/result`.
_DISPATCHED = "dispatched"
_FIRST_TOKEN = "first_token"
_COMPLETED = "completed"

#: How many times one identical (tool, arguments) call inside a single turn marks the turn
#: as thrashing. Three repeats is ordinary (read, re-read after an edit); five is a loop.
LOOP_REPEAT_THRESHOLD = 5


def _avg(values: list[float]) -> Optional[float]:
    return round(sum(values) / len(values), 1) if values else None


def _p95(values: list[float]) -> Optional[float]:
    """Nearest-rank p95, rounded like `_avg`. A latency card without a tail number hides
    exactly the turns the ops reader is looking for."""
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int(round(0.95 * (len(ordered) - 1)))))
    return round(ordered[index], 1)


def _bump_tool(stats: dict[str, dict[str, Any]], name: str, key: str, by: int = 1) -> None:
    row = stats.setdefault(name, {"calls": 0, "error": 0, "timeout": 0, "blocked": 0, "interrupted": 0})
    row[key] = int(row.get(key, 0)) + by


def _pct(numerator: int, denominator: int) -> Optional[float]:
    if denominator <= 0:
        return None
    return round(numerator / denominator, 4)


def summarize(
    events: Iterable[dict[str, Any]], *, model: Optional[str] = None
) -> dict[str, Any]:
    """Fold one session's events into a JSON-serializable metric report.

    `model` is optional and only used to price the run: when given, the report carries
    `cost_usd` computed from the models.dev price sheet on disk (None when the sheet does
    not price that model — unknown is not free). Tokens are reported either way; a
    session that changed models mid-run prices the whole fold at the given model, which
    is the honest approximation of a fold that keeps no per-model split.
    """
    events = list(events)

    turns_started = 0
    turn_reasons: Counter[str] = Counter()
    turn_tokens: list[int] = []
    steps = 0
    llm_calls = 0
    llm_with_usage = 0
    retries = 0
    abandoned_retries = 0
    retry_reasons: Counter[str] = Counter()
    ttfts: list[float] = []
    step_ms: list[float] = []
    tokens = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}
    # Per-model fold for the durable usage rollup (`metrics_store.model_usage`): the Ops
    # question "tokens per model, per provider" must survive the session's deletion, so
    # it is keyed by what the wire said (upstream + model), not by the session.
    by_model: dict[tuple[str, str], dict[str, Any]] = {}
    tool_calls = 0
    tool_results = 0
    tool_status: Counter[str] = Counter()
    tool_ms: list[float] = []
    tool_names: Counter[str] = Counter()
    # Per-tool health for the ops surface: calls / failures / durations, keyed by name.
    tool_stats: dict[str, dict[str, Any]] = {}
    partials = 0
    delegated = 0
    subagents = 0
    budgeted = 0
    checkpoints = 0
    compactions = 0
    compaction_blocks = 0
    compaction_saved = 0
    compaction_summarizer = 0
    nudges: Counter[str] = Counter()
    errors = 0
    notices: Counter[str] = Counter()
    first_t: Optional[float] = None
    last_t: Optional[float] = None

    for event in events:
        kind = event.get("type")
        stamp = event.get("t")
        if isinstance(stamp, (int, float)):
            first_t = stamp if first_t is None else min(first_t, stamp)
            last_t = stamp if last_t is None else max(last_t, stamp)

        if kind == "turn/start":
            turns_started += 1
        elif kind == "turn/end":
            turn_reasons[str(event.get("reason") or "unknown")] += 1
            value = event.get("tokens")
            if isinstance(value, int):
                turn_tokens.append(value)
            if event.get("reason") == "error":
                errors += 1
        elif kind == "step/start":
            steps += 1
        elif kind == "assistant/message":
            llm_calls += 1
            if event.get("partial"):
                partials += 1
            usage = event.get("usage")
            if isinstance(usage, dict):
                llm_with_usage += 1
                for key in tokens:
                    value = usage.get(key)
                    if isinstance(value, (int, float)):
                        tokens[key] += int(value)
            event_model = str(event.get("model") or "")
            # The provider is the one the model was CONNECTED through — the model id's
            # prefix ("openrouter:deepseek/…" → openrouter). `event.upstream` is the
            # serving backend a router picked (DeepInfra, Relace…) and is not it.
            provider = provider_of_model(event_model)
            if event_model:
                bucket = by_model.setdefault(
                    (provider, event_model),
                    {"calls": 0, "tokens_in": 0, "tokens_out": 0, "cache_read": 0, "cache_write": 0},
                )
                bucket["calls"] += 1
                if isinstance(usage, dict):
                    for key, field in (
                        ("input", "tokens_in"),
                        ("output", "tokens_out"),
                        ("cache_read", "cache_read"),
                        ("cache_write", "cache_write"),
                    ):
                        value = usage.get(key)
                        if isinstance(value, (int, float)):
                            bucket[field] += int(value)
            timing = event.get("timing")
            if isinstance(timing, dict):
                dispatched = timing.get(_DISPATCHED)
                first_token = timing.get(_FIRST_TOKEN)
                completed = timing.get(_COMPLETED)
                if all(isinstance(v, (int, float)) for v in (dispatched, first_token)):
                    ttfts.append(float(first_token) - float(dispatched))
                if all(isinstance(v, (int, float)) for v in (dispatched, completed)):
                    step_ms.append((float(completed) - float(dispatched)) * 1000.0)
        elif kind == "llm/retry":
            retries += 1
            if event.get("abandoned"):
                abandoned_retries += 1
            retry_reasons[str(event.get("reason") or "unknown")] += 1
        elif kind == "tool/call":
            tool_calls += 1
            name = event.get("name")
            if isinstance(name, str) and name:
                tool_names[name] += 1
                _bump_tool(tool_stats, name, "calls")
                if name == "task":
                    subagents += 1
        elif kind == "tool/result":
            tool_results += 1
            status = str(event.get("status") or "unknown")
            tool_status[status] += 1
            name = event.get("name")
            if isinstance(name, str) and name and status != "ok":
                _bump_tool(tool_stats, name, status if status in ("error", "timeout", "blocked", "interrupted") else "error")
            timing = event.get("timing")
            if isinstance(timing, dict):
                started = timing.get("started")
                ended = timing.get("ended")
                if all(isinstance(v, (int, float)) for v in (started, ended)):
                    ms = (float(ended) - float(started)) * 1000.0
                    tool_ms.append(ms)
                    if isinstance(name, str) and name:
                        tool_stats[name].setdefault("ms", []).append(ms)
            if event.get("delegation"):
                delegated += 1
        elif kind == "tool/result/budget":
            budgeted += 1
        elif kind == "turn/checkpoint":
            checkpoints += 1
        elif kind == "notice":
            notice_kind = str(event.get("notice_kind") or "unknown")
            notices[notice_kind] += 1
            if notice_kind == "compacted":
                compactions += 1
        elif kind == "compaction/summary":
            # A compacted block entered the outbound view (summarized or trimmed).
            # Distinct from `compactions` above, which counts every adoption including
            # a prune-only pass — a prune reclaims tokens without spending a summarizer.
            compaction_blocks += 1
            saved = event.get("saved_tokens")
            if isinstance(saved, (int, float)):
                compaction_saved += int(saved)
            spent = event.get("summarizer_tokens")
            if isinstance(spent, (int, float)):
                compaction_summarizer += int(spent)
        elif kind == "verify/nudge":
            nudges["verify"] += 1
        elif kind == "todo/nudge":
            nudges["todo"] += 1

    token_total = sum(tokens.values())
    cached = tokens["cache_read"] + tokens["cache_write"]
    turn_avg = _avg([float(t) for t in turn_tokens])
    loop_flags = sum(1 for row in turns_report(events) if row.get("loop"))
    cost_usd: Optional[float] = None
    if model:
        from harness.llm.providers.catalog import estimate_cost_usd

        cost_usd = estimate_cost_usd(
            model,
            input_tokens=tokens["input"],
            output_tokens=tokens["output"],
            cache_read_tokens=tokens["cache_read"],
            cache_write_tokens=tokens["cache_write"],
        )
    return {
        "events": len(events),
        "model": model,
        # The same fold split by (upstream, model), ranked by tokens. The store keeps
        # this in a table that session deletion does not touch.
        "by_model": [
            {"provider": provider, "model": name, **bucket}
            for (provider, name), bucket in sorted(
                by_model.items(),
                key=lambda item: -(item[1]["tokens_in"] + item[1]["tokens_out"]),
            )
        ],
        # USD at the model's published rates, or None when the price sheet does not know
        # it. The GUI prices in tokens by recorded decision; this serves API/CLI/bench
        # consumers that compare cost across runs.
        "cost_usd": round(cost_usd, 6) if cost_usd is not None else None,
        "wall_seconds": (
            round(last_t - first_t, 3)
            if first_t is not None and last_t is not None
            else None
        ),
        "turns": {
            "started": turns_started,
            "ended": sum(turn_reasons.values()),
            "by_reason": dict(turn_reasons),
            "tokens": turn_tokens,
            "tokens_avg": int(turn_avg) if turn_avg is not None else None,
        },
        "steps": steps,
        "llm": {
            "calls": llm_calls,
            "with_usage": llm_with_usage,
            "partial": partials,
            "retries": retries,
            "retry_rate": _pct(retries, llm_calls),
            "retries_by_reason": dict(retry_reasons),
            "errors": errors,
            "error_rate": _pct(errors, turns_started),
        },
        "tokens": {
            **tokens,
            "total": token_total,
            # Share of the whole prompt that came back from the cache: cache_read over
            # (fresh input + cache read + cache write). The number a cache-aligned loop
            # moves, and the one a regression shows up in first.
            "cache_read_share": _pct(
                tokens["cache_read"], tokens["input"] + cached
            ),
            "per_llm_call": round(token_total / llm_calls, 1) if llm_calls else None,
        },
        "latency": {
            "ttft_ms_avg": _avg([value * 1000.0 for value in ttfts]),
            "ttft_ms_p95": _p95([value * 1000.0 for value in ttfts]),
            "ttft_samples": len(ttfts),
            "step_ms_avg": _avg(step_ms),
            "step_ms_max": round(max(step_ms), 1) if step_ms else None,
        },
        "tools": {
            "calls": tool_calls,
            "results": tool_results,
            "by_status": dict(tool_status),
            "error_rate": _pct(
                sum(v for k, v in tool_status.items() if k != "ok"), tool_results
            ),
            "timeout_or_error": sum(
                v for k, v in tool_status.items() if k in ("error", "timeout")
            ),
            "blocked": tool_status.get("blocked", 0),
            "interrupted": tool_status.get("interrupted", 0),
            "duration_ms_avg": _avg(tool_ms),
            "duration_ms_max": round(max(tool_ms), 1) if tool_ms else None,
            "budgeted_results": budgeted,
            "top": dict(tool_names.most_common(10)),
            "delegated": delegated,
            # Per-tool health, sorted by calls — the ops surface's Tool health table.
            "by_name": {
                name: {
                    "calls": row.get("calls", 0),
                    "errors": row.get("error", 0),
                    "timeouts": row.get("timeout", 0),
                    "blocked": row.get("blocked", 0),
                    "interrupted": row.get("interrupted", 0),
                    "p95_ms": _p95(row.get("ms", [])),
                }
                for name, row in sorted(
                    tool_stats.items(), key=lambda item: -int(item[1].get("calls", 0))
                )
            },
        },
        "process": {
            # Steps are the model's work unit; a rising steps-per-turn average with a loop
            # flag is the cheapest early signal that a prompt or tool regressed.
            "steps": steps,
            "steps_per_turn": round(steps / turns_started, 2) if turns_started else None,
            "loop_flags": loop_flags,
            "subagents": subagents,
            # Turns that died on a provider failure + retries the provider's own
            # Retry-After made us abandon. Tool-side failures live under `tools`.
            "model_errors": errors + abandoned_retries,
        },
        "compactions": compactions,
        "compaction": {
            # Block adoptions only: a prune pass reclaims context without a summarizer
            # call, so it lands in `compactions` above but not here.
            "blocks": compaction_blocks,
            "saved_tokens": compaction_saved,
            # Provider traffic the summarizer spent to buy that saving. A compaction
            # whose cost approaches its saving is a round-trip worth questioning.
            "summarizer_tokens": compaction_summarizer,
            "net_tokens": compaction_saved - compaction_summarizer,
        },
        "checkpoints": checkpoints,
        "nudges": dict(nudges),
        "notices": dict(notices),
        "error_notices": notices.get("error", 0),
    }


def turns_report(events: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Per-turn fold of one session's events — the rows behind the session aggregate.

    A turn's record spans its `turn/start` … `turn/end` window: wall time, steps, the
    model it ran on (the last `request/header` in the window — a session can switch
    models mid-run), tokens, tool outcomes by status, TTFT samples, and the loop flag.

    `loop` marks a turn where ONE identical (tool, arguments) signature repeated
    `LOOP_REPEAT_THRESHOLD` or more times — thrashing, the failure a step count alone
    cannot tell from honest work. Events outside any turn (none in practice) are
    attached to an implicit turn keyed by their own `turn` number, so a torn log still
    yields rows instead of silently dropping work.
    """
    import json as _json

    rows: list[dict[str, Any]] = []
    current: Optional[dict[str, Any]] = None
    repeats: dict[int, Counter[str]] = {}

    def start_row(turn: Any, stamp: Optional[float]) -> dict[str, Any]:
        return {
            "turn": turn,
            "started": stamp,
            "ended": None,
            "model": "",
            "reason": "",
            "failed": False,
            "steps": 0,
            "tokens": {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0},
            "tool_calls": 0,
            "tool_errors": 0,
            "tool_timeouts": 0,
            "blocked": 0,
            "interrupted": 0,
            "ttft_ms": [],
            "wall_ms": None,
            "errors": [],
        }

    # Renumbered on the way in: rows are keyed (session, turn) downstream, and a
    # log an engine rebuild wrote holds the same number twice.
    for event in monotonic_turns(events):
        kind = event.get("type")
        stamp = event.get("t")
        stamp = float(stamp) if isinstance(stamp, (int, float)) else None
        if kind == "turn/start":
            current = start_row(event.get("turn"), stamp)
            continue
        if current is None:
            # Torn or legacy log: open an implicit turn so the row is not lost.
            current = start_row(event.get("turn"), None)
        if kind == "request/header":
            model = event.get("model")
            if isinstance(model, str) and model:
                current["model"] = model
        elif kind == "step/start":
            current["steps"] += 1
        elif kind == "assistant/message":
            usage = event.get("usage")
            if isinstance(usage, dict):
                for key in current["tokens"]:
                    value = usage.get(key)
                    if isinstance(value, (int, float)):
                        current["tokens"][key] += int(value)
            timing = event.get("timing")
            if isinstance(timing, dict):
                dispatched = timing.get(_DISPATCHED)
                first_token = timing.get(_FIRST_TOKEN)
                if all(isinstance(v, (int, float)) for v in (dispatched, first_token)):
                    current["ttft_ms"].append((float(first_token) - float(dispatched)) * 1000.0)
        elif kind == "tool/call":
            current["tool_calls"] += 1
            name = event.get("name")
            arguments = event.get("arguments")
            if isinstance(name, str) and name:
                try:
                    signature = name + "|" + _json.dumps(arguments, sort_keys=True, default=str)
                except (TypeError, ValueError):
                    signature = name + "|" + str(arguments)
                bucket = repeats.setdefault(id(current), Counter())
                bucket[signature] += 1
        elif kind == "tool/result":
            status = str(event.get("status") or "")
            if status == "error":
                current["tool_errors"] += 1
            elif status == "timeout":
                current["tool_timeouts"] += 1
            elif status == "blocked":
                current["blocked"] += 1
            elif status == "interrupted":
                current["interrupted"] += 1
            if status and status != "ok" and len(current["errors"]) < 5:
                content = str(event.get("content") or "").strip().splitlines()
                current["errors"].append({
                    "tool": str(event.get("name") or ""),
                    "status": status,
                    # First line only: the drilldown list is a pointer, the trace is the detail.
                    "reason": (content[0][:160] if content else ""),
                })
        elif kind == "turn/end":
            current["ended"] = stamp
            current["reason"] = str(event.get("reason") or "")
            current["failed"] = current["reason"] == "error"
            if current["started"] is not None and stamp is not None:
                current["wall_ms"] = round((stamp - current["started"]) * 1000.0, 1)
            bucket = repeats.pop(id(current), Counter())
            current["repeats_max"] = max(bucket.values()) if bucket else 0
            current["loop"] = current["repeats_max"] >= LOOP_REPEAT_THRESHOLD
            current["ttft_ms_avg"] = _avg(current.pop("ttft_ms"))
            rows.append(current)
            current = None

    if current is not None:  # interrupted log: the turn never closed
        bucket = repeats.pop(id(current), Counter())
        current["repeats_max"] = max(bucket.values()) if bucket else 0
        current["loop"] = current["repeats_max"] >= LOOP_REPEAT_THRESHOLD
        current["ttft_ms_avg"] = _avg(current.pop("ttft_ms"))
        rows.append(current)
    return rows


__all__ = ["summarize", "turns_report", "LOOP_REPEAT_THRESHOLD"]