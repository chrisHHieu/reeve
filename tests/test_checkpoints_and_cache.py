"""Two small durability/cost fixes that had the same shape: a rule only one caller knew.

**Checkpoints (1.8).** The websocket turn already persisted mid-turn, with an explicit set
of checkpoint events and the right comment. The background turn — self-wake, channel
delivery, scheduled runs — saved once, after the whole loop. Those are exactly the runs
with nobody watching: a restart lost every step they had taken, and no one was there to
notice or retype. The set now lives with the events, so both consumers read one rule.

**Bedrock cache points (1.9).** `_usage_from` has always read `cacheReadInputTokens` and
`cacheWriteInputTokens` off every Converse response, while `_request_kwargs` never asked
for caching. The numbers were structurally zero — a cache that looked broken because it
was never requested.

The interesting part of 1.9 is the gate. The Converse path serves EVERY Bedrock model and
most of them reject `cachePoint` outright rather than ignoring it, so an allowlist would
be a guess with a hard failure on the wrong side — and it cannot be verified without an
AWS account. Asking the service and believing its answer costs one rejected request per
model per process and can never fail a turn.
"""

from __future__ import annotations

import pytest

from harness.session.events import CHECKPOINT_EVENTS, EventType


# -- 1.8: one rule, both consumers ------------------------------------------------


def test_the_checkpoint_set_covers_every_point_a_turn_can_die_at():
    assert EventType.TURN_START in CHECKPOINT_EVENTS  # the user's words
    assert EventType.TOOL_FINISHED in CHECKPOINT_EVENTS  # each completed step
    assert EventType.ITERATION_END in CHECKPOINT_EVENTS  # each model round-trip
    # Parked indefinitely on a human, so a restart is likely rather than possible.
    assert EventType.PERMISSION_REQUIRED in CHECKPOINT_EVENTS
    assert EventType.PLAN_PROPOSED in CHECKPOINT_EVENTS


def test_streaming_events_are_not_checkpoints():
    """A save per token would rewrite the row thousands of times for one answer, and the
    answer is not durable until the message that carries it is appended anyway."""
    assert EventType.ASSISTANT_DELTA not in CHECKPOINT_EVENTS
    assert EventType.REASONING_DELTA not in CHECKPOINT_EVENTS


def test_both_turn_paths_read_the_shared_set():
    """The defect was not a missing save — it was two consumers of the same event stream
    where only one knew the rule. A grep is the honest test for that: a second literal
    set would drift again, silently, exactly as it did the first time.

    The manager is a package of per-responsibility mixins now, so the grep spans its
    modules rather than reading one file's text."""
    import inspect
    from pathlib import Path

    from harness.interface.server import app, manager

    pkg = Path(manager.__file__).parent
    app_pkg = Path(app.__file__).parent
    sources = {
        "harness.interface.server.app": "\n".join(
            p.read_text(encoding="utf-8") for p in sorted(app_pkg.glob("*.py"))
        ),
        "harness.interface.server.manager": "\n".join(
            p.read_text(encoding="utf-8") for p in sorted(pkg.glob("*.py"))
        ),
    }
    for name, source in sources.items():
        assert "CHECKPOINT_EVENTS" in source, name
    assert "_CHECKPOINTS = {" not in sources["harness.interface.server.app"]


def test_the_background_turn_saves_inside_its_loop():
    """Not at the end of it, which is where it used to be and what made an unattended run
    all-or-nothing. The loop is the shared `_drive_turn` now (step B): it is
    what `deliver_to_session` runs, and what a spawned child runs, so the rule covers
    both rather than one file's copy of it."""
    import inspect

    from harness.interface.server.manager import SessionManager

    deliver = inspect.getsource(SessionManager.deliver_to_session)
    assert "_drive_turn" in deliver, "the background turn must run the shared driver"
    body = inspect.getsource(SessionManager._drive_turn)
    # The loop over the turn's events, whichever entry produced them (`engine.run`, or
    # `engine.pick_up` for a turn picked up after a restart).
    body = body[body.index("async for event in ") :]
    checkpoint = body.index("CHECKPOINT_EVENTS")
    loop_exit = body.index("finally:")
    assert checkpoint < loop_exit


# -- 1.9: ask the service, don't guess --------------------------------------------


@pytest.fixture
def converse():
    from harness.llm.providers.bedrock.converse import ConverseClient as _BedrockConverseClient

    return _BedrockConverseClient(client=object())


def _kwargs(converse, model="amazon.nova-pro-v1:0"):
    return converse._request_kwargs(
        model=model,
        messages=[{"role": "system", "content": "be brief"},
                  {"role": "user", "content": "hello"}],
        tools=None,
        settings={},
    )


def test_a_converse_request_now_asks_for_the_cache_it_was_metering(converse):
    kwargs = _kwargs(converse)
    assert kwargs["system"][-1] == {"cachePoint": {"type": "default"}}
    assert kwargs["messages"][-1]["content"][-1] == {"cachePoint": {"type": "default"}}


def test_the_cache_point_follows_the_content_it_caches(converse):
    """Converse's cache point is a separate block marking a boundary — unlike Anthropic's
    `cache_control`, which is an attribute ON the block it caches. Putting it first would
    cache nothing."""
    kwargs = _kwargs(converse)
    system = kwargs["system"]
    assert "cachePoint" not in system[0]
    assert len(system) >= 2


def test_a_model_that_refuses_is_asked_only_once():
    from harness.llm.providers.bedrock.converse import ConverseClient as _BedrockConverseClient

    class _Picky:
        """Rejects any request carrying a cache point, the way Llama or Titan does."""

        def __init__(self):
            self.calls = []

        def converse(self, **kwargs):
            self.calls.append(kwargs)
            blocks = list(kwargs.get("system") or [])
            for message in kwargs.get("messages") or []:
                blocks += list(message.get("content") or [])
            if any("cachePoint" in b for b in blocks):
                raise type("ValidationException", (Exception,), {})(
                    "The model returned the following errors: cachePoint is not supported"
                )
            return {"output": {"message": {"content": [{"text": "ok"}]}},
                    "stopReason": "end_turn", "usage": {}}

    client = _Picky()
    converse = _BedrockConverseClient(client=client)
    messages = [{"role": "user", "content": "hi"}]

    first = converse.complete(model="meta.llama3-70b", messages=messages)
    assert first.text == "ok"
    assert len(client.calls) == 2  # asked, refused, re-sent without

    converse.complete(model="meta.llama3-70b", messages=messages)
    assert len(client.calls) == 3  # remembered: no second rejection


def test_an_unrelated_failure_is_not_retried():
    """The retry is narrow on purpose: a validation error that names the field. Anything
    broader would silently re-send real request errors."""
    from harness.llm.providers.bedrock.converse import ConverseClient as _BedrockConverseClient

    class _Broken:
        def __init__(self):
            self.calls = 0

        def converse(self, **kwargs):
            self.calls += 1
            raise type("ValidationException", (Exception,), {})("modelId is invalid")

    client = _Broken()
    converse = _BedrockConverseClient(client=client)
    with pytest.raises(Exception):
        converse.complete(model="bad", messages=[{"role": "user", "content": "hi"}])
    assert client.calls == 1


def test_stripping_leaves_the_real_content_untouched():
    from harness.llm.providers.bedrock.caching import add_cache_points as _add_cache_points, strip_cache_points as _strip_cache_points

    kwargs = {
        "system": [{"text": "be brief"}],
        "messages": [{"role": "user", "content": [{"text": "hello"}]}],
    }
    _add_cache_points(kwargs)
    assert _strip_cache_points(kwargs) is True
    assert kwargs == {
        "system": [{"text": "be brief"}],
        "messages": [{"role": "user", "content": [{"text": "hello"}]}],
    }
    assert _strip_cache_points(kwargs) is False  # nothing left to remove
