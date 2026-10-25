"""The gap since the previous message, stated in the live context.

A clock alone cannot tell a model whether the last exchange was a second ago or after
lunch, and the two read very differently. The line is volatile (same half as the clock),
so it costs the cached prefix nothing — and under 90 seconds it is noise, so it is not
said at all.
"""

from __future__ import annotations

import time

from harness.compose import build_engine, describe_elapsed


def test_elapsed_phrases_are_coarse_and_silent_when_short():
    assert describe_elapsed(0) == ""
    assert describe_elapsed(89) == ""
    assert describe_elapsed(-5) == ""  # a clock that moved backwards is not a gap
    assert describe_elapsed(200) == "3 min"
    assert describe_elapsed(3600) == "1h"
    assert describe_elapsed(9000) == "2h 30m"
    assert describe_elapsed(90_000) == "1d 1h"


async def test_a_long_gap_reaches_the_model_as_context():
    from harness.modes.agents.chat import chat_agent
    from harness.llm.providers import AssistantTurn, ModelCapabilities, ProviderClient

    class Spy(ProviderClient):
        def __init__(self):
            self.captured = None

        def complete(self, *, model, messages, tools=None, **settings):
            self.captured = [dict(m) for m in messages]
            return AssistantTurn(text="ok", finish_reason="stop")

        def capabilities(self, model):
            return ModelCapabilities()

    spy = Spy()
    now = time.time()
    engine = build_engine(
        agent=chat_agent(),
        model="gpt-4o",
        provider=spy,
        messages=[
            {"role": "user", "content": "first", "ts": now - 7300},
            {"role": "assistant", "content": "hello", "ts": now - 7290},
        ],
    )
    async for _ in engine.run("next"):
        pass

    last_user = [m for m in (spy.captured or []) if m.get("role") == "user"][-1]
    assert "since the previous message" in str(last_user.get("content"))
    assert "2h" in str(last_user.get("content"))


async def test_a_quick_follow_up_says_nothing_about_time():
    from harness.modes.agents.chat import chat_agent
    from harness.llm.providers import AssistantTurn, ModelCapabilities, ProviderClient

    class Spy(ProviderClient):
        def __init__(self):
            self.captured = None

        def complete(self, *, model, messages, tools=None, **settings):
            self.captured = [dict(m) for m in messages]
            return AssistantTurn(text="ok", finish_reason="stop")

        def capabilities(self, model):
            return ModelCapabilities()

    spy = Spy()
    now = time.time()
    engine = build_engine(
        agent=chat_agent(),
        model="gpt-4o",
        provider=spy,
        messages=[
            {"role": "user", "content": "first", "ts": now - 10},
            {"role": "assistant", "content": "hello", "ts": now - 5},
        ],
    )
    async for _ in engine.run("next"):
        pass

    last_user = [m for m in (spy.captured or []) if m.get("role") == "user"][-1]
    assert "since the previous message" not in str(last_user.get("content"))
