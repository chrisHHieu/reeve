"""A stream that dies after the first chunk (engine.py + providers/errors.py).

The SDKs already retry the REQUEST — OpenAI and Anthropic default to two attempts on
connection errors and 5xx, boto3 runs standard mode — so the initial claim that reeve
"has no retry" was wrong. The real defect was narrower and stranger: an exception raised
while ITERATING a stream is not a failed request, it is a failed iteration, and no SDK
retries that. An answer cut at byte 1 got two more attempts; an answer cut at byte 40,000
got none and ended the turn.

Two decisions are pinned here beyond "it retries":

**Unrecognized means no retry.** A retry of a permanent failure spends the whole prompt
again, waits again, and prints the same error. Quota and access failures arrive with the
same HTTP status as the transient ones — only the body separates them.

**The partial does not enter history before a retry.** The retry re-sends the same
request, so an extra assistant message would break the cached prefix AND hand the model
its own truncated sentence to continue from. The same conclusion follows from the
other direction: "failed chunks never enter derived messages". It is still written when
the retries are spent, which is the only case where anyone can read it.
"""

from __future__ import annotations

import asyncio
import threading

from harness.loop import STREAM_RETRIES, TurnEngine
from harness.session.events import EventType
from harness.safety.permissions import PermissionEngine
from harness.llm.providers import AssistantTurn, ModelCapabilities, ProviderClient, StreamChunk
from harness.llm.providers.errors import EmptyCompletion, error_chain, is_transient, retry_after_seconds
from harness.tools import ToolRegistry


# -- the classifier ---------------------------------------------------------------


class _Named(Exception):
    """An exception whose CLASS name is the signal, as the SDKs' are."""


def _as(name: str, message: str = "boom") -> Exception:
    return type(name, (_Named,), {})(message)


def test_transport_failures_are_worth_another_attempt():
    for name in ("APIConnectionError", "APITimeoutError", "InternalServerError",
                 "RemoteProtocolError", "IncompleteRead", "RateLimitError"):
        assert is_transient(_as(name)), name


def test_providers_that_wrap_everything_are_read_from_the_body():
    """Bedrock, Vertex and OpenAI-compatible gateways raise one generic type, so the
    class name says nothing and the message is all there is."""
    for text in ("Error: overloaded_error", "503 Service Unavailable",
                 "Connection reset by peer", "Too Many Requests"):
        assert is_transient(Exception(text)), text


def test_a_server_error_streamed_by_a_gateway_is_retried():
    """OpenCode Go's SSE error event, raised as a bare APIError: it ended 2 of 9 GLM
    baseline probe runs at their first steps with nothing retrying them."""
    from harness.llm.providers.errors import classify_stream_error, retry_budget

    exc = _as("APIError", "Streaming response failed: [server_error] upstream service timeout")
    assert is_transient(exc)
    assert classify_stream_error(exc) == "transport" and retry_budget(exc) == 2


def test_a_quota_429_is_not_a_rate_limit_429():
    """The status is identical and the body is the only difference. Retrying "you are out
    of credits" spends the prompt three times to print the same sentence."""
    assert not is_transient(Exception("429 insufficient_quota: exceeded your current quota"))
    assert not is_transient(_as("RateLimitError", "insufficient_quota"))
    assert is_transient(_as("RateLimitError", "please slow down"))


def test_permanent_failures_are_left_alone():
    for text in ("model_not_found", "permission_error", "invalid tool schema",
                 "credit balance is too low"):
        assert not is_transient(Exception(text)), text


def test_an_unrecognized_failure_is_not_retried():
    """The conservative direction: a false negative restores today's behaviour, a false
    positive charges the user for it."""
    assert not is_transient(Exception("something nobody has seen before"))


class _WithHeaders(Exception):
    def __init__(self, seconds):
        super().__init__("rate limit")
        self.response = type("R", (), {"headers": {"retry-after": seconds}})()


def test_the_providers_own_delay_wins_when_it_sent_one():
    assert retry_after_seconds(_WithHeaders("2")) == 2.0


def test_a_long_retry_after_survives_to_be_reported():
    """The header used to be swallowed above 10s, which silently turned "come back in
    five minutes" into a ten-second retry guaranteed to fail — and then three more,
    spending the rate-limit budget to find out. The value now survives; the loop owns
    the decision (wait it out, or end the turn with the number said out loud)."""
    assert retry_after_seconds(_WithHeaders("300")) == 300.0
    # Beyond credibility it is ignored as if unsent: a mis-set clock or a broken proxy
    # is not a wait at all. This is what keeps "asked too long" and "sent nothing"
    # distinguishable at the loop.
    assert retry_after_seconds(_WithHeaders("3601")) is None
    assert retry_after_seconds(_WithHeaders("not-a-number")) is None
    assert retry_after_seconds(Exception("no headers at all")) is None


# -- the engine -------------------------------------------------------------------


class _DiesMidStream(ProviderClient):
    """Streams a few words, then breaks — `failures` times, then answers properly."""

    def __init__(self, failures: int, error: Exception):
        self.failures = failures
        self.error = error
        self.attempts = 0

    def stream(self, *, model, messages, tools=None, **settings):
        self.attempts += 1
        yield StreamChunk(text_delta="Here is the first half")
        if self.attempts <= self.failures:
            raise self.error
        yield StreamChunk(turn=AssistantTurn(text="Here is the whole answer",
                                             finish_reason="stop"))

    def complete(self, *, model, messages, tools=None, **settings):
        raise AssertionError("the engine must stream, not fall back to complete()")

    def capabilities(self, model):
        return ModelCapabilities()


def _engine(tmp_path, provider):
    return TurnEngine(
        provider=provider,
        registry=ToolRegistry(),
        permissions=PermissionEngine(workspace_root=tmp_path),
        model="gpt-5.5",
    )


def _run(engine, text="hello"):
    async def go():
        return [ev async for ev in engine.run(text)]

    return asyncio.run(go())


def test_a_stream_that_dies_mid_answer_is_tried_again(tmp_path, monkeypatch):
    monkeypatch.setattr("harness.loop.step_recovery.BACKOFF_SECONDS", (0.0, 0.0))
    provider = _DiesMidStream(1, _as("APIConnectionError"))
    engine = _engine(tmp_path, provider)
    events = _run(engine)

    assert provider.attempts == 2
    assert events[-1].data["status"] == "completed"
    answer = next(e for e in events if e.type is EventType.ASSISTANT_MESSAGE)
    assert answer.data["text"] == "Here is the whole answer"


def test_the_retry_is_announced_before_it_waits(tmp_path, monkeypatch):
    """A stream that silently restarts is indistinguishable from a stall."""
    monkeypatch.setattr("harness.loop.step_recovery.BACKOFF_SECONDS", (0.0, 0.0))
    engine = _engine(tmp_path, _DiesMidStream(1, _as("APIConnectionError")))
    events = _run(engine)

    notice = next(e for e in events if e.type is EventType.RETRYING)
    assert notice.data["attempt"] == 1
    assert notice.data["of"] == STREAM_RETRIES
    # The surface rendered deltas for an answer being thrown away.
    assert notice.data["discard_partial"] is True


def test_the_discarded_half_answer_never_reaches_the_model(tmp_path, monkeypatch):
    """It would break the cached prefix and hand the model its own truncated sentence to
    continue from."""
    monkeypatch.setattr("harness.loop.step_recovery.BACKOFF_SECONDS", (0.0, 0.0))
    engine = _engine(tmp_path, _DiesMidStream(1, _as("APIConnectionError")))
    _run(engine)

    assistants = [m for m in engine.messages if m.get("role") == "assistant"]
    assert len(assistants) == 1
    assert assistants[0]["content"] == "Here is the whole answer"
    assert "first half" not in assistants[0]["content"]


def test_the_budget_is_spent_then_the_error_surfaces(tmp_path, monkeypatch):
    monkeypatch.setattr("harness.loop.step_recovery.BACKOFF_SECONDS", (0.0, 0.0))
    provider = _DiesMidStream(99, _as("APIConnectionError"))
    engine = _engine(tmp_path, provider)
    events = _run(engine)

    assert provider.attempts == STREAM_RETRIES + 1  # the first try plus its retries
    assert len([e for e in events if e.type is EventType.RETRYING]) == STREAM_RETRIES
    assert events[-1].type is EventType.ERROR


def test_giving_up_still_keeps_what_the_user_watched_arrive(tmp_path, monkeypatch):
    """The partial is withheld from history only while a retry might replace it. Once
    nothing will, it is the only record of the answer and belongs in the transcript."""
    monkeypatch.setattr("harness.loop.step_recovery.BACKOFF_SECONDS", (0.0, 0.0))
    engine = _engine(tmp_path, _DiesMidStream(99, _as("APIConnectionError")))
    _run(engine)

    assistants = [m for m in engine.messages if m.get("role") == "assistant"]
    assert len(assistants) == 1
    assert "Here is the first half" in assistants[0]["content"]


def test_a_retry_after_within_the_ceiling_is_the_delay(tmp_path, monkeypatch):
    """The provider's number wins over the local backoff, and the wait is announced."""
    monkeypatch.setattr("harness.loop.step_recovery.BACKOFF_SECONDS", (9.9, 9.9))
    provider = _DiesMidStream(1, _WithHeaders("0.01"))
    engine = _engine(tmp_path, provider)
    events = _run(engine)

    assert provider.attempts == 2
    retrying = next(e for e in events if e.type is EventType.RETRYING)
    assert retrying.data["delay"] == 0.01  # not the 9.9s local backoff
    assert events[-1].data["status"] == "completed"


def test_a_retry_after_over_the_ceiling_ends_the_turn_instead_of_burning_retries(
    tmp_path, monkeypatch
):
    """A hard rate limit says "not now". Retrying at 10s is the one thing the header
    exists to prevent, so the turn ends with the number said out loud — one attempt,
    no RETRYING events, and the error is still retriable by the user."""
    monkeypatch.setattr("harness.loop.step_recovery.BACKOFF_SECONDS", (0.0, 0.0))
    provider = _DiesMidStream(99, _WithHeaders("300"))
    engine = _engine(tmp_path, provider)
    events = _run(engine)

    assert provider.attempts == 1  # no retry was attempted
    assert not any(e.type is EventType.RETRYING for e in events)
    assert events[-1].type is EventType.ERROR
    assert events[-1].data["retry_after"] == 300.0
    assert "300s" in events[-1].data["error"]
    # Nothing is lost: the half-answer the user watched arrive is in the transcript.
    assistants = [m for m in engine.messages if m.get("role") == "assistant"]
    assert len(assistants) == 1 and "first half" in assistants[0]["content"]
    # And the failure notice carries the wait, so the persisted transcript says why.
    notice = next(
        m
        for m in engine.messages
        if m.get("role") == "notice" and m.get("kind") == "error"
    )
    assert notice.get("retry_after") == 300.0


def test_the_abandoned_retry_is_logged_with_its_reason(tmp_path, monkeypatch):
    """The log should show that a retry was declined and on what grounds —
    otherwise the ERROR looks like the provider simply failed once."""
    from harness.session.session_log import SessionLog

    monkeypatch.setattr("harness.loop.step_recovery.BACKOFF_SECONDS", (0.0, 0.0))
    engine = _engine(tmp_path, _DiesMidStream(99, _WithHeaders("300")))
    engine.log = SessionLog(tmp_path / "events.jsonl")
    _run(engine)

    declined = [
        e
        for e in engine.log.read()
        if e.get("type") == "llm/retry" and e.get("abandoned")
    ]
    assert len(declined) == 1
    assert declined[0]["reason"] == "rate_limit"
    assert declined[0]["retry_after"] == 300.0


def test_a_discarded_attempt_never_enters_history_or_the_log(tmp_path, monkeypatch):
    """The retry replaces a stream that died mid-answer: the transcript holds only the
    completed answer, and the log carries the failure (`llm/retry`), not the dead text."""
    from harness.session.session_log import SessionLog

    monkeypatch.setattr("harness.loop.step_recovery.BACKOFF_SECONDS", (0.0, 0.0))
    engine = _engine(tmp_path, _DiesMidStream(1, _as("APIConnectionError")))
    engine.log = SessionLog(tmp_path / "events.jsonl")
    _run(engine)

    kinds = [e.get("type") for e in engine.log.read()]
    assert "llm/attempt" not in kinds
    assert kinds.count("llm/retry") == 1
    assert all("Here is the first half" not in str(e) for e in engine.log.read())
    assistants = [m for m in engine.messages if m.get("role") == "assistant"]
    assert len(assistants) == 1 and "first half" not in assistants[0]["content"]


def test_a_permanent_failure_is_not_retried_at_all(tmp_path):
    """Today's behaviour, unchanged, for everything the classifier does not recognise."""
    provider = _DiesMidStream(99, Exception("model_not_found"))
    engine = _engine(tmp_path, provider)
    events = _run(engine)

    assert provider.attempts == 1
    assert not any(e.type is EventType.RETRYING for e in events)
    assert events[-1].type is EventType.ERROR


def test_stop_during_the_backoff_ends_the_turn(tmp_path):
    """The wait is real time with a half-written answer on screen. Stop has to work in
    it, or the button is dead for as long as the backoff lasts."""
    provider = _DiesMidStream(99, _as("APIConnectionError"))
    engine = _engine(tmp_path, provider)

    async def go():
        events = []
        async for ev in engine.run("hello"):
            events.append(ev)
            if ev.type is EventType.RETRYING:
                engine.request_interrupt()
        return events

    events = asyncio.run(go())
    assert events[-1].type is EventType.INTERRUPTED
    assert provider.attempts == 1  # the second attempt never started


# -- the taxonomy: what failed decides how many retries it gets (P0) ----------------


def test_the_taxonomy_routes_each_failure_to_its_retry_budget():
    from harness.llm.providers.errors import classify_stream_error, retry_budget

    assert classify_stream_error(_as("RateLimitError", "please slow down")) == "rate_limit"
    assert retry_budget(_as("RateLimitError", "please slow down")) == 4
    assert classify_stream_error(Exception("upstream overloaded_error")) == "overloaded"
    assert retry_budget(Exception("Error: overloaded_error")) == 4
    assert classify_stream_error(_as("APIConnectionError")) == "transport"
    assert retry_budget(_as("APITimeoutError")) == 2


def test_stream_retries_agrees_with_the_budget_the_loop_actually_reads():
    """`STREAM_RETRIES` is documentation, not a control: the loop asks `retry_budget(exc)`.

    The other tests in this file assert against the constant and passed anyway, because for
    a mid-stream death the budget happens to be the same 2 — a real agreement, but an
    accidental one, which would have kept them green while the constant went on describing
    behaviour the runtime no longer had. Pinned here, so a change to either side fails next
    to the comment that explains why they should match.
    """
    from harness.llm.providers.errors import retry_budget

    for exc in (
        _as("APIConnectionError"),  # a stream that died on the wire
        _as("APITimeoutError"),
        Exception("Upstream idle timeout exceeded"),  # the gateway case, seen live
    ):
        assert retry_budget(exc) == STREAM_RETRIES, str(exc)


class _StallsForever(ProviderClient):
    """Streams one chunk, then sends nothing at all until released.

    The SDKs bound the whole request (ten minutes by default), so before the engine's
    watchdog a wedged connection held the turn for ten minutes. The producer thread here
    waits on an event instead of sleeping so the test can release it before the loop
    closes.
    """

    def __init__(self):
        self.attempts = 0
        self.release = threading.Event()

    def stream(self, *, model, messages, tools=None, **settings):
        self.attempts += 1
        yield StreamChunk(text_delta="Here is the first half")
        self.release.wait(2.0)

    def complete(self, *, model, messages, tools=None, **settings):
        raise AssertionError("the engine must stream, not fall back to complete()")

    def capabilities(self, model):
        return ModelCapabilities()


def test_a_wedged_stream_is_declared_dead_by_the_idle_watchdog(tmp_path, monkeypatch):
    """The watchdog bounds the SILENCE, not the request. A connection that stops
    producing bytes is re-issued on the ordinary transport budget instead of sitting in
    front of the user until the SDK's ten-minute timeout."""
    monkeypatch.setattr("harness.loop.step_recovery.BACKOFF_SECONDS", (0.0, 0.0))
    provider = _StallsForever()
    engine = TurnEngine(
        provider=provider,
        registry=ToolRegistry(),
        permissions=PermissionEngine(workspace_root=tmp_path),
        model="gpt-5.5",
        stream_idle_timeout=0.05,
    )

    async def go():
        events = [ev async for ev in engine.run("hello")]
        provider.release.set()  # let the abandoned producer threads exit in-loop
        await asyncio.sleep(0.1)
        return events

    events = asyncio.run(go())

    assert provider.attempts == STREAM_RETRIES + 1
    assert len([e for e in events if e.type is EventType.RETRYING]) == STREAM_RETRIES
    assert events[-1].type is EventType.ERROR
    assert events[-1].data["error_type"] == "StreamStalled"


def test_the_idle_watchdog_failure_travels_the_transport_budget():
    """Nothing new to learn at the loop: the stall is transient by class name, so it
    spends the same budget as any other dead stream."""
    from harness.llm.providers.errors import StreamStalled, classify_stream_error, retry_budget

    exc = StreamStalled("stream idle timeout after 300s with no chunk")
    assert is_transient(exc)
    assert classify_stream_error(exc) == "transport"
    assert retry_budget(exc) == STREAM_RETRIES


def test_gateway_idle_timeout_is_transient_transport():
    """A relay that idled out mid-stream ("Upstream idle timeout exceeded" — seen live
    2026-09-06, an 18-minute turn dying at the gateway) wraps the stall in a generic
    exception no class name covers. The connection stalled; re-sending re-rolls the
    routing dice, so it must classify transport, not unknown (zero retries)."""
    from harness.llm.providers.errors import classify_stream_error, retry_budget

    exc = Exception("Upstream idle timeout exceeded")
    assert is_transient(exc)
    assert classify_stream_error(exc) == "transport"
    assert retry_budget(exc) == 2


def test_permanent_classes_get_zero_retries_and_surface_at_once():
    from harness.llm.providers.errors import classify_stream_error, retry_budget

    for exc, reason in (
        (Exception("429 insufficient_quota: exceeded your current quota"), "no_quota"),
        (Exception("model_not_found: does not exist or you do not have access"), "no_access"),
        (Exception("content_policy_violation: blocked by the safety system"), "content_policy"),
        (Exception("something nobody has seen before"), "unknown"),
    ):
        assert classify_stream_error(exc) == reason, reason
        assert retry_budget(exc) == 0, reason


# -- deterministic empties -----------------------------------------


class _EmptyCompletionProvider(ProviderClient):
    """Raises `EmptyCompletion` with a signature per attempt, then keeps repeating the
    last one. A real provider's upstream is what varies; this stands in for it."""

    def __init__(self, signatures: list):
        self.signatures = signatures
        self.attempts = 0

    def stream(self, *, model, messages, tools=None, **settings):
        self.attempts += 1
        signature = self.signatures[min(self.attempts - 1, len(self.signatures) - 1)]
        raise EmptyCompletion("the model returned an empty response", signature=signature)
        yield  # pragma: no cover - keeps this a generator, like the real clients

    def complete(self, *, model, messages, tools=None, **settings):
        raise AssertionError("the engine must stream, not fall back to complete()")

    def capabilities(self, model):
        return ModelCapabilities()


def test_two_identical_empty_completions_stop_the_budget(tmp_path, monkeypatch):
    """The same route serving the same nothing twice is deterministic: another attempt
    reroutes the dice at best, so one retry is all this gets, not two."""
    monkeypatch.setattr("harness.loop.step_recovery.BACKOFF_SECONDS", (0.0, 0.0))
    provider = _EmptyCompletionProvider([("gpt-5.5", "z-ai", "stop")])
    engine = _engine(tmp_path, provider)
    events = _run(engine)

    assert provider.attempts == 2
    assert events[-1].type is EventType.ERROR


def test_a_different_upstream_keeps_the_normal_budget(tmp_path, monkeypatch):
    """A reroute is the whole reason the empty class is retried: only an IDENTICAL
    signature twice is deterministic."""
    monkeypatch.setattr("harness.loop.step_recovery.BACKOFF_SECONDS", (0.0, 0.0))
    provider = _EmptyCompletionProvider(
        [("m", "gmicloud", "stop"), ("m", "parasail", "stop"), ("m", "modal", "stop")]
    )
    engine = _engine(tmp_path, provider)
    _run(engine)

    assert provider.attempts == 3  # the first try plus the class' two retries


def test_the_log_row_carries_the_innermost_cause():
    """SDK wrappers hide the real failure (`APIConnectionError("Connection error.")`);
    the row should read like the cause, not the wrapper."""
    inner = ConnectionError("connect to api.example refused")
    outer = RuntimeError("Connection error.")
    outer.__cause__ = inner

    assert "refused" in error_chain(outer)
    assert error_chain(inner) == str(inner)


# -- a 400 that names no error: the session's backend, not the request ----------


class BadRequestError(Exception):
    """The OpenAI SDK's shape: `status_code`, `body`, and `Error code: 400 - {body}`."""

    def __init__(self, body):
        super().__init__(f"Error code: 400 - {body!r}")
        self.status_code = 400
        self.body = body


def test_a_400_that_names_no_error_is_a_routing_failure():
    """OpenCode Go's `400 {'model': X}`: a parallel session on the same model was answered
    around each failure, and the same request re-sent later went through 10 of 10."""
    from harness.llm.providers.errors import classify_stream_error, retry_budget
    from harness.llm.providers.fallback import fallback_eligible

    opaque = BadRequestError({"model": "deepseek-v4.1-flash"})
    assert is_transient(opaque) and classify_stream_error(opaque) == "routing"
    assert retry_budget(opaque) == 2 and fallback_eligible(opaque)
    # The same text through a wrapper that kept only the message.
    assert classify_stream_error(Exception("Error code: 400 - {'model': 'glm-5.3'}")) == "routing"


def test_a_400_that_says_what_is_wrong_stays_permanent():
    from harness.llm.providers.errors import classify_stream_error

    for body in (
        {"error": {"message": "invalid tool schema", "type": "invalid_request_error"}},
        {"type": "error", "error": {"type": "invalid_request_error", "message": "bad"}},
        {"model": "x", "message": "the model does not support tools"},
    ):
        exc = BadRequestError(body)
        assert not is_transient(exc), body
        assert classify_stream_error(exc) == "unknown", body


class _PinnedBackendDown(ProviderClient):
    """Refuses every request routed under `bad_key` with the opaque 400, answers the rest,
    and records the routing key each attempt carried."""

    def __init__(self, bad_key):
        self.bad_key = bad_key
        self.keys = []

    def stream(self, *, model, messages, tools=None, **settings):
        key = settings.get("cache_affinity")
        self.keys.append(key)
        if key == self.bad_key:
            raise BadRequestError({"model": model})
        yield StreamChunk(turn=AssistantTurn(text="answered", finish_reason="stop"))

    def complete(self, *, model, messages, tools=None, **settings):
        raise AssertionError("the engine must stream")

    def capabilities(self, model):
        return ModelCapabilities()


def test_the_retry_goes_out_on_a_new_routing_key_and_the_turn_finishes(tmp_path, monkeypatch):
    """Retried on the same key it reaches the same failing backend (a real session's retry
    52s later failed again); on a new key the turn goes on, and stays on it."""
    monkeypatch.setattr("harness.loop.step_recovery.BACKOFF_SECONDS", (0.0, 0.0))
    provider = _PinnedBackendDown("session-1")
    engine = _engine(tmp_path, provider)
    engine.model_settings = {"cache_affinity": "session-1"}
    events = _run(engine)

    assert events[-1].data["status"] == "completed"
    assert provider.keys == ["session-1", "session-1~1"]
    _run(engine, "and again")
    assert provider.keys[-1] == "session-1~1"  # the new backend keeps the session


def test_a_session_without_a_routing_key_retries_as_it_was(tmp_path, monkeypatch):
    monkeypatch.setattr("harness.loop.step_recovery.BACKOFF_SECONDS", (0.0, 0.0))
    provider = _PinnedBackendDown(None)
    engine = _engine(tmp_path, provider)
    events = _run(engine)

    assert provider.keys == [None, None, None]  # the budget, spent on the only route
    assert events[-1].type is EventType.ERROR
