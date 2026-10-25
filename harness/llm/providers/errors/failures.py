"""Failures the harness raises itself, as opposed to ones a provider SDK raises."""

from __future__ import annotations

from typing import Optional


class EmptyCompletion(Exception):
    """The provider closed the stream having sent no text, no thinking and no tool call.

    This is not an HTTP failure: the request succeeded and the answer was nothing. It was seen on
    `openrouter:z-ai/glm-5.3-flash` (2026-08-27). OpenRouter fans one model across several
    upstreams and picks per request, and one upstream returned an empty stream on 2 of 2 attempts
    while the others answered normally. Without this exception the turn ended "completed" with
    nothing in it: the spinner stopped, no message appeared and the log recorded a successful step,
    which is why it was reported as "the UI is slow" instead of as a bug.

    It is classed transient, because a retry re-rolls the routing and that is what has a real chance
    of a different answer.

    `signature` records what "empty" looked like, `(model, upstream, finish_reason)`, so the loop can
    tell a reroute from the same route serving the same nothing twice: two identical signatures in a
    row are deterministic, and the retry budget stops.
    """

    def __init__(self, message: str, signature: Optional[tuple] = None) -> None:
        super().__init__(message)
        self.signature = signature


class StreamStalled(TimeoutError):
    """No chunk at all for the idle budget: the connection stopped producing bytes.

    The engine's stream watchdog (`engine.STREAM_IDLE_SECONDS`) raises it, not the provider. The
    SDKs bound the whole request (ten minutes by default), so a wedged stream would sit in front of
    the person for ten minutes before anything noticed. The watchdog bounds the silence instead and
    the failure takes the ordinary transport-retry path, since re-issuing a dead stream usually
    works. It subclasses `TimeoutError` so a caller that already classifies timeouts recognizes it
    by type too.
    """
