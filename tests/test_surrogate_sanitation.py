"""Lone surrogates never reach a provider request.

Windows text paths produce them (a command's output decoded with
`errors="surrogateescape"`, a filename), and `json`/UTF-8 encoding a string that carries
one raises `UnicodeEncodeError` — after the turn has spent its context and its time. The
providers replace them with U+FFFD right before the request is built.
"""

from __future__ import annotations

from harness.llm.providers.base import sanitize_messages, sanitize_text


def test_a_lone_surrogate_is_replaced():
    assert sanitize_text("ok\ud800bad") == "ok\ufffdbad"
    assert sanitize_text("a\udfff") == "a\ufffd"


def test_clean_text_comes_back_as_the_same_object():
    """The fast path matters: every request runs through this."""
    text = "clean text"
    assert sanitize_text(text) is text


def test_nested_messages_are_sanitized_without_mutating_the_input():
    messages = [
        {"role": "tool", "content": [{"type": "text", "text": "x\udfff"}]},
        {"role": "assistant", "tool_calls": [{"id": "c1", "function": {"arguments": "y\ud800"}}]},
    ]

    out = sanitize_messages(messages)

    assert out[0]["content"][0]["text"] == "x\ufffd"
    assert out[1]["tool_calls"][0]["function"]["arguments"] == "y\ufffd"
    # History is untouched: the engine's list is never rewritten by a request build.
    assert messages[0]["content"][0]["text"] == "x\udfff"
    assert messages[1]["tool_calls"][0]["function"]["arguments"] == "y\ud800"


def test_non_string_leaves_pass_through():
    assert sanitize_messages({"n": 1, "b": True, "z": None}) == {"n": 1, "b": True, "z": None}
