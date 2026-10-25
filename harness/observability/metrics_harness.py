"""Harness-health folds over one session's event log — shared by the Metrics tab and
`scripts/harness_kpis.py`, so the page and the script can never disagree on a number.

`metrics.summarize` answers "what did this session do"; this answers "did the HARNESS
behave", with the signals the 2026-09-24/25 work built:

    spend      `turn/end` tokens split by the logged call that paid for them; the residue
               is UNATTRIBUTED and must stay ~0 (a regression hid there once);
    stalls     every wait over STALL_SECONDS before a step, by cause — compaction,
               provider retry, or UNEXPLAINED (must stay 0);
    ttft       time to first token, sampled per prompt-size band;
    compaction what each compaction did (summarized / prepared / pruned / failed …) and
               what each background prepare did (parked / failed / stale);
    usage      routes the engine caught reporting a partial prompt (`notice` usage_partial);
    by_day     tokens per (day, provider, model), from the assistant messages' own stamps —
               a session that spans days is split by the day each call ran;
    days       the checks that had only a total, per day (`harness_days`): unattributed
               spend on the day its turn closed, an unexplained wait on the day its step
               started, a failed compaction and a partial-usage route on the day they were
               logged — so each check on the Metrics page has its week (2026-10-13).

Pure: events in, dicts out. No I/O, no clock.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from typing import Any, Optional

from .metrics import provider_of_model

#: Prompt-size bands for time-to-first-token (tokens, upper bounds; the last is open).
TTFT_BANDS = (50_000, 100_000, 200_000, 400_000)
#: A wait before a step this long is a stall the person sits through.
STALL_SECONDS = 10.0
#: TTFT samples kept per band per session — enough for a percentile, bounded in storage.
TTFT_SAMPLES_MAX = 200

_PROMPT_KEYS = ("input", "cache_read", "cache_write")


def band(tokens: int) -> str:
    low = 0
    for high in TTFT_BANDS:
        if tokens < high:
            return f"{low // 1000}-{high // 1000}k"
        low = high
    return f">{low // 1000}k"


def band_order(label: str) -> int:
    return int(label.lstrip(">").split("-")[0].rstrip("k"))


def quantile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    return round(ordered[min(len(ordered) - 1, int(q * len(ordered)))], 1) if ordered else 0.0


def spend_part(event: dict[str, Any]) -> Optional[tuple[str, int]]:
    """(bucket, tokens) for an event that logs a model call's spend, else None."""
    kind = event.get("type")
    if kind == "assistant/message" and isinstance(event.get("usage"), dict):
        usage = event["usage"]
        return "main", sum(int(usage.get(k) or 0) for k in (*_PROMPT_KEYS, "output"))
    field = {
        "compaction/end": ("summarizer", "summarizer_tokens"),
        "compaction/prepare": ("prepare", "tokens"),
        "tool/result": ("delegated", "tokens"),
        "delegation/background": ("delegated", "tokens"),
    }.get(str(kind))
    if field is not None and isinstance(event.get(field[1]), int):
        return field[0], event[field[1]]
    return None


def spend_fold(events: list[dict[str, Any]]) -> Counter[str]:
    """`turn_total` plus each logged bucket, over CLOSED turns only.

    A reloaded session numbers its turns again from its own counter, so an abandoned
    turn's calls are dropped at the next `turn/start` under the same number instead of
    folding into the turn that later closes.
    """
    spend: Counter[str] = Counter()
    open_turns: dict[Any, Counter[str]] = {}
    for event in events:
        kind = event.get("type")
        if kind == "turn/start":
            open_turns.pop(event.get("turn"), None)
        part = spend_part(event)
        if part is not None:
            open_turns.setdefault(event.get("turn"), Counter())[part[0]] += part[1]
        if kind == "turn/end" and isinstance(event.get("tokens"), int):
            spend["turn_total"] += event["tokens"]
            spend.update(open_turns.pop(event.get("turn"), Counter()))
    return spend


def spend_report(spend: Counter[str]) -> dict[str, Any]:
    total = spend["turn_total"]
    parts = {k: spend[k] for k in ("main", "summarizer", "prepare", "delegated")}
    unattributed = max(0, total - sum(parts.values()))
    return {"turn_total": total, **parts, "unattributed": unattributed}


def latency_fold(
    events: list[dict[str, Any]],
    ttft: dict[str, list[float]],
    stalls: Counter[str],
    unexplained_days: Optional[Counter[str]] = None,
) -> None:
    """Time to first token by prompt band, and the waits before steps.

    A wait runs from the user's message (or the previous step's end) to the next
    `step/start`. Past STALL_SECONDS it is a stall: `compaction` when a compaction ran in
    it (the person was told), `provider retry` when the provider failed and the loop
    backed off, `unexplained` otherwise. A wait spanning a turn boundary is idle time.
    """
    since: Optional[float] = None
    between: set[str] = set()
    for event in events:
        kind = event.get("type")
        stamp = event.get("t")
        if kind == "assistant/message":
            timing, usage = event.get("timing") or {}, event.get("usage") or {}
            sent, first = timing.get("dispatched"), timing.get("first_token")
            if isinstance(sent, (int, float)) and isinstance(first, (int, float)) and usage:
                prompt = sum(int(usage.get(k) or 0) for k in _PROMPT_KEYS)
                ttft.setdefault(band(prompt), []).append(max(0.0, first - sent))
        if kind in ("user/message", "step/end") and isinstance(stamp, (int, float)):
            since, between = stamp, set()
            continue
        if kind == "step/start":
            if since is not None and isinstance(stamp, (int, float)):
                gap = stamp - since
                if gap > STALL_SECONDS and not between & {"turn/end", "turn/start"}:
                    if "compaction/start" in between:
                        stalls["compaction"] += 1
                    elif "llm/retry" in between or "llm/fallback" in between:
                        stalls["provider retry"] += 1
                    else:
                        stalls["unexplained"] += 1
                        if unexplained_days is not None:
                            unexplained_days[_day(stamp)] += 1
            since, between = None, set()
            continue
        if since is not None and kind:
            between.add(str(kind))


def _day(stamp: Any) -> str:
    if not isinstance(stamp, (int, float)):
        return ""
    return datetime.fromtimestamp(stamp, tz=timezone.utc).strftime("%Y-%m-%d")


def usage_by_day(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Tokens per (day, provider, model) from the assistant messages' own stamps."""
    buckets: dict[tuple[str, str, str], dict[str, int]] = {}
    for event in events:
        if event.get("type") != "assistant/message":
            continue
        model = str(event.get("model") or "")
        if not model:
            continue
        key = (_day(event.get("t")), provider_of_model(model), model)
        bucket = buckets.setdefault(
            key, {"calls": 0, "tokens_in": 0, "tokens_out": 0, "cache_read": 0, "cache_write": 0}
        )
        bucket["calls"] += 1
        usage = event.get("usage") if isinstance(event.get("usage"), dict) else {}
        for source, target in (
            ("input", "tokens_in"),
            ("output", "tokens_out"),
            ("cache_read", "cache_read"),
            ("cache_write", "cache_write"),
        ):
            value = usage.get(source)
            if isinstance(value, (int, float)):
                bucket[target] += int(value)
    return [
        {"day": day, "provider": provider, "model": model, **bucket}
        for (day, provider, model), bucket in sorted(buckets.items())
    ]


def spend_days(events: list[dict[str, Any]]) -> dict[str, Counter[str]]:
    """`spend_fold` per day: each closed turn's spend lands on the day its `turn/end` ran."""
    days: dict[str, Counter[str]] = {}
    open_turns: dict[Any, Counter[str]] = {}
    for event in events:
        kind = event.get("type")
        if kind == "turn/start":
            open_turns.pop(event.get("turn"), None)
        part = spend_part(event)
        if part is not None:
            open_turns.setdefault(event.get("turn"), Counter())[part[0]] += part[1]
        if kind == "turn/end" and isinstance(event.get("tokens"), int):
            day = days.setdefault(_day(event.get("t")), Counter())
            day["turn_total"] += event["tokens"]
            day.update(open_turns.pop(event.get("turn"), Counter()))
    return days


def harness_days(events: list[dict[str, Any]], unexplained: Counter[str]) -> dict[str, dict[str, Any]]:
    """The harness checks per day: unattributed spend, unexplained waits, failed compactions and
    the routes caught under-reporting usage. `unexplained` comes from `latency_fold`."""
    out: dict[str, dict[str, Any]] = {}

    def day(stamp: Any) -> dict[str, Any]:
        return out.setdefault(
            _day(stamp) if not isinstance(stamp, str) else stamp,
            {"turn_total": 0, "unattributed": 0, "unexplained": 0, "compaction_failed": 0, "usage_partial": []},
        )

    for name, spend in spend_days(events).items():
        report = spend_report(spend)
        bucket = day(name)
        bucket["turn_total"] = report["turn_total"]
        bucket["unattributed"] = report["unattributed"]
    for name, count in unexplained.items():
        day(name)["unexplained"] = count
    for event in events:
        kind = event.get("type")
        if kind == "compaction/end" and str(event.get("reason") or "") == "failed":
            day(event.get("t"))["compaction_failed"] += 1
        elif kind == "notice" and event.get("kind") == "usage_partial" and event.get("model"):
            routes = day(event.get("t"))["usage_partial"]
            if str(event["model"]) not in routes:
                routes.append(str(event["model"]))
    out.pop("", None)  # an event with no stamp has no day to land on
    return out


def harness_fold(events: list[dict[str, Any]]) -> dict[str, Any]:
    """Everything the Metrics tab's harness section reads, for one session."""
    ttft: dict[str, list[float]] = {}
    stalls: Counter[str] = Counter()
    unexplained_days: Counter[str] = Counter()
    latency_fold(events, ttft, stalls, unexplained_days)
    compaction: Counter[str] = Counter()
    prepares: Counter[str] = Counter()
    partial: set[str] = set()
    for event in events:
        kind = event.get("type")
        if kind == "compaction/end":
            compaction[str(event.get("reason") or "?")] += 1
        elif kind == "compaction/prune":
            compaction["pruned (cache cold)"] += 1
        elif kind == "compaction/prepare":
            prepares[str(event.get("outcome") or "?")] += 1
        elif kind == "notice" and event.get("kind") == "usage_partial":
            partial.add(str(event.get("model") or ""))
    return {
        "spend": spend_report(spend_fold(events)),
        "stalls": dict(stalls),
        "ttft": {name: [round(v, 3) for v in values[-TTFT_SAMPLES_MAX:]] for name, values in ttft.items()},
        "compaction": dict(compaction),
        "prepares": dict(prepares),
        "usage_partial": sorted(m for m in partial if m),
        "days": harness_days(events, unexplained_days),
    }


__all__ = [
    "STALL_SECONDS",
    "TTFT_BANDS",
    "band",
    "band_order",
    "harness_days",
    "harness_fold",
    "latency_fold",
    "quantile",
    "spend_days",
    "spend_fold",
    "spend_part",
    "spend_report",
    "usage_by_day",
]
