"""Where the Anthropic provider marks its prompt cache. Every request asks for the
ephemeral cache at two points: the last system block, which covers the tools and the system prompt,
and the last block of the last message, which covers the conversation so far. The marks are put on
what is sent only; the stored history never has them. The one-hour tier can be asked for instead.
"""

from __future__ import annotations

import pytest

from harness.llm.providers.claude import AnthropicProvider

FIVE_MINUTES = {"type": "ephemeral"}
ONE_HOUR = {"type": "ephemeral", "ttl": "1h"}
SYSTEM = {"role": "system", "content": "be terse"}


def _sent(messages, cache_ttl=None):
    provider = AnthropicProvider(client=object(), cache_ttl=cache_ttl) if cache_ttl is not None else AnthropicProvider(client=object())
    return provider._request_kwargs(model="claude-haiku-4-5", messages=messages, tools=None, settings={})


def _conversation():
    return [dict(SYSTEM), {"role": "user", "content": "first"}, {"role": "assistant", "content": "reply"}, {"role": "user", "content": "second"}]


def _last_mark(sent):
    return sent["messages"][-1]["content"][-1]["cache_control"]


# -- the two marks -------------------------------------------------------------------------------


def test_the_system_prompt_is_sent_as_one_marked_block():
    assert _sent([SYSTEM, {"role": "user", "content": "hi"}])["system"] == [{"type": "text", "text": "be terse", "cache_control": FIVE_MINUTES}]


def test_only_the_last_message_is_marked():
    sent = _sent(_conversation()[1:])
    assert _last_mark(sent) == FIVE_MINUTES
    # the earlier turns stay as they were, so the start of the request matches the last one's cache
    assert all("cache_control" not in block for message in sent["messages"][:-1] for block in message["content"])


def test_a_tool_result_at_the_end_is_the_block_marked():
    call = {"id": "t1", "type": "function", "function": {"name": "ls", "arguments": "{}"}}
    sent = _sent([{"role": "user", "content": "run it"}, {"role": "assistant", "content": "", "tool_calls": [call]}, {"role": "tool", "tool_call_id": "t1", "content": "README.md"}])
    last = sent["messages"][-1]["content"][-1]
    assert last["type"] == "tool_result" and last["cache_control"] == FIVE_MINUTES


def test_the_stored_history_is_left_untouched():
    history = [dict(SYSTEM), {"role": "user", "content": "hi"}]
    _sent(history)
    assert history == [SYSTEM, {"role": "user", "content": "hi"}]


def test_with_no_system_prompt_only_the_message_is_marked():
    sent = _sent([{"role": "user", "content": "hi"}])
    assert "system" not in sent and _last_mark(sent) == FIVE_MINUTES


# -- the one-hour tier ---------------------------------------------------------------------------


def test_the_one_hour_tier_lands_on_both_breakpoints():
    """One tier a request, never two: Anthropic needs a one-hour mark to come before a five-minute
    one, so a pair that differed by accident would be a 400 the first time they swapped places."""
    sent = _sent(_conversation(), "1h")
    assert sent["system"] == [{"type": "text", "text": "be terse", "cache_control": ONE_HOUR}]
    marks = [sent["system"][0]["cache_control"]] + [block["cache_control"] for message in sent["messages"] for block in (message.get("content") or []) if isinstance(block, dict) and "cache_control" in block]
    assert marks and all(mark == ONE_HOUR for mark in marks)


@pytest.mark.parametrize("value", [None, "", "   ", "5m", "24h", "nonsense"])
def test_anything_but_one_hour_keeps_the_five_minute_tier(value):
    # the setting can never make a request Anthropic refuses
    sent = _sent(_conversation(), value)
    assert sent["system"][0]["cache_control"] == FIVE_MINUTES and _last_mark(sent) == FIVE_MINUTES


@pytest.mark.parametrize("value", ["1H", " 1h ", "1h"])
def test_one_hour_is_read_whatever_its_case_and_spaces(value):
    assert _sent(_conversation(), value)["system"][0]["cache_control"] == ONE_HOUR


def test_the_profile_field_reaches_the_provider():
    # the hidden profile setting is passed through, as `thinking_budget` is
    from harness.llm.providers.registry import builders

    assert builders.build_anthropic({"api_key": "sk-ant-x", "cache_ttl": "1h"}, None).cache_ttl == "1h"
    assert builders.build_anthropic({"api_key": "sk-ant-x"}, None).cache_ttl is None
