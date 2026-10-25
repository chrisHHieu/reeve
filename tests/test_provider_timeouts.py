"""Provider clients get a transport timeout their own SDK accepts.

`anthropic` 1.0 moved to `httpx2`; handed a plain `httpx.Timeout`, its transport passed
the object itself to `socket.settimeout` and EVERY Anthropic-wire request died on its
first connect with `TypeError: 'Timeout' object cannot be interpreted as an integer` —
native Claude and opencode-go's `messages` models alike (harness eval on qwen3.8-flash,
2026-09-24). A connect to a closed local port must fail as a CONNECTION error.
"""

from __future__ import annotations

import socket

import pytest

from harness.llm.providers.claude import AnthropicProvider
from harness.llm.providers.timeouts import STREAM_IDLE_SECONDS, stream_timeout


def _closed_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _chain(exc: BaseException) -> list[BaseException]:
    seen = []
    while exc is not None and exc not in seen:
        seen.append(exc)
        exc = exc.__cause__ or exc.__context__
    return seen


def test_an_anthropic_wire_request_reaches_the_socket(tmp_path):
    provider = AnthropicProvider(api_key="sk-ant-test", base_url=f"http://127.0.0.1:{_closed_port()}")
    with pytest.raises(Exception) as caught:
        provider.complete(model="claude-test", messages=[{"role": "user", "content": "hi"}])
    chain = _chain(caught.value)
    assert not any(isinstance(e, TypeError) for e in chain), [repr(e) for e in chain]
    assert any(isinstance(e, (ConnectionError, OSError)) for e in chain) or "Connection" in str(
        caught.value
    ), [repr(e) for e in chain]


def test_the_timeout_is_the_sdks_own_class():
    import anthropic
    import openai

    for sdk in (anthropic, openai):
        timeout = stream_timeout(sdk)
        assert type(timeout) is type(sdk.DEFAULT_TIMEOUT)
        assert timeout.read == STREAM_IDLE_SECONDS
