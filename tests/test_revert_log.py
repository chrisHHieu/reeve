"""An undo is in the log.

`/undo` and `/redo` change what reaches the provider without adding a message, and wrote
nothing to the session log: a request sent after one could not be rebuilt from its log,
and the cache drop the new prefix causes was counted as unexplained.
"""

from __future__ import annotations

import asyncio
import json

from harness.loop import TurnEngine
from harness.safety.permissions import PermissionEngine
from harness.llm.providers import AssistantTurn, ModelCapabilities, ProviderClient, StreamChunk
from harness.session.session_log import SessionLog
from harness.tools import ToolRegistry

import scripts.harness_review as harness_review


class _Answers(ProviderClient):
    def stream(self, *, model, messages, tools=None, **settings):
        yield StreamChunk(turn=AssistantTurn(text="ok", finish_reason="stop"))

    def complete(self, *, model, messages, tools=None, **settings):
        raise AssertionError("the engine must stream")

    def capabilities(self, model):
        return ModelCapabilities()


def _engine(tmp_path, messages=None):
    engine = TurnEngine(
        provider=_Answers(),
        registry=ToolRegistry(),
        permissions=PermissionEngine(workspace_root=tmp_path),
        model="m",
        messages=messages,
    )
    engine.log = SessionLog(tmp_path / "events.jsonl")
    return engine


def _run(engine, text="next"):
    async def go():
        return [event async for event in engine.run(text)]

    return asyncio.run(go())


def _reverts(log):
    return [{k: v for k, v in e.items() if k in ("boundary", "staged_at", "cleared")} for e in log.read() if e.get("type") == "revert/set"]


def test_a_session_that_never_undid_writes_no_revert_row(tmp_path):
    engine = _engine(tmp_path)
    _run(engine)
    _run(engine)
    assert _reverts(engine.log) == []


def test_the_marker_a_request_is_built_under_is_logged_once_per_change(tmp_path):
    engine = _engine(tmp_path)
    _run(engine, "first")
    _run(engine, "second")

    engine.revert_state = {"boundary": 3, "staged_at": 5, "ts": 1.0}
    _run(engine, "after the undo")
    _run(engine, "same marker, a turn later")
    assert _reverts(engine.log) == [{"boundary": 3, "staged_at": 5}]
    # The row comes before the request it shapes: its turn's first model answer follows it.
    rows = engine.log.read()
    at = next(i for i, e in enumerate(rows) if e.get("type") == "revert/set")
    assert rows[at]["turn"] == 3 and any(e.get("type") == "assistant/message" and e.get("turn") == 3 for e in rows[at:])
    assert not any(e.get("type") == "assistant/message" and e.get("turn") == 3 for e in rows[:at])

    engine.revert_state = {"boundary": 1, "staged_at": 9, "ts": 2.0}   # undone further
    _run(engine)
    engine.revert_state = None                                        # redone to the end
    _run(engine)
    _run(engine)
    assert _reverts(engine.log) == [
        {"boundary": 3, "staged_at": 5},
        {"boundary": 1, "staged_at": 9},
        {"cleared": True},
    ]


def test_a_new_engine_starts_from_what_the_log_last_said(tmp_path):
    """The marker lives on the session record, so every engine built over the session carries
    it: one built after a restart must not log again what the log already holds."""
    first = _engine(tmp_path)
    first.revert_state = {"boundary": 1, "staged_at": 3, "ts": 1.0}
    _run(first)

    second = _engine(tmp_path, messages=list(first.messages))
    second.revert_state = {"boundary": 1, "staged_at": 3, "ts": 1.0}
    _run(second)
    assert _reverts(second.log) == [{"boundary": 1, "staged_at": 3}]

    # Cleared while no engine was alive: the next one says so before its first request.
    third = _engine(tmp_path, messages=list(second.messages))
    _run(third)
    assert _reverts(third.log)[-1] == {"cleared": True} and len(_reverts(third.log)) == 2


def test_a_cache_drop_after_an_undo_is_explained_by_it(tmp_path):
    def step(read):
        return {"type": "assistant/message", "t": 5.0, "turn": 1, "usage": {"cache_read": read, "input": 100}}

    log = tmp_path / "s.events.jsonl"
    rows = [step(60_000), step(60_000), {"type": "revert/set", "t": 5.0, "turn": 1, "boundary": 2, "staged_at": 8}, step(1_000), step(60_000), step(1_000)]
    log.write_text("".join(json.dumps({"seq": i, **row}) + "\n" for i, row in enumerate(rows)), encoding="utf-8")
    counts, _ = harness_review.review([log])
    assert counts["cache-drops revert/set"] == 1 and counts["cache-drops unexplained"] == 1


def test_an_error_ending_is_unnamed_when_nothing_says_what_or_where(tmp_path):
    def end(**fields):
        return {"type": "turn/end", "t": 1.0, "reason": "error", **fields}

    log = tmp_path / "s.events.jsonl"
    rows = [
        end(),                                                      # from before endings had a class
        end(error="unknown"),
        end(error="crashed"),                                       # a crash with no place
        end(error="code"),
        end(error="crashed", error_at="KeyError at engine/x.py:f"),  # located: named
        end(error="code", error_at="TypeError at tools/y.py:g"),
        end(error="no_quota"),                                      # the provider's: the class is the name
        {"type": "turn/end", "t": 1.0, "reason": "completed"},
    ]
    log.write_text("".join(json.dumps({"seq": i, **row}) + "\n" for i, row in enumerate(rows)), encoding="utf-8")
    counts, refs = harness_review.review([log], keep_all="unnamed-error-ends")
    assert counts["error-ends"] == 7 and counts["unnamed-error-ends"] == 4
    assert sorted(ref[2] for ref in refs["unnamed-error-ends"]) == ["code", "crashed", "unclassified", "unknown"]
