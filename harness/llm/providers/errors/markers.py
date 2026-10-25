"""The words and class names by which failures are recognized.

Matching is on the error BODY text (codes and types) as well as the HTTP status: a 404 can mean a
wrong base_url and a 429 can mean "slow down", and neither should be dressed up as an access problem.
"""

from __future__ import annotations

# Markers taken verbatim from the vendors' own error codes and messages.
#   OpenAI: {"error": {"code": "model_not_found", "message": "The model `X` does not exist or you do
#     not have access to it."}} (404/403) and {"code": "insufficient_quota"} (429).
#   Anthropic: {"type": "not_found_error", "message": "model: X"} (404), {"type": "permission_error"}
#     (403) and "credit balance is too low" (400).
NO_ACCESS = (
    "model_not_found",
    "does not exist or you do not have access",
    "does not have access to model",
    "permission_error",
    "permission denied",
)
NO_QUOTA = (
    "insufficient_quota",
    "exceeded your current quota",
    "credit balance is too low",
    "billing hard limit",
    # DeepSeek's 402 (seen 2026-10-06) read as `unknown`, so it was not treated as permanent.
    "insufficient balance",
)

# Class names, shared across the OpenAI and Anthropic SDKs, plus the httpx and stdlib transport
# failures a broken stream actually raises.
TRANSIENT_TYPES = (
    "EmptyCompletion",
    # The engine's own idle watchdog: a stream that stopped producing bytes is the same class of
    # failure as a read timeout on a live one.
    "StreamStalled",
    "APIConnectionError",
    "APITimeoutError",
    "InternalServerError",
    "RateLimitError",
    "ConnectError",
    "ConnectTimeout",
    "ReadTimeout",
    "ReadError",
    "RemoteProtocolError",
    "IncompleteRead",
    "ChunkedEncodingError",
    "ProtocolError",
    "ServiceUnavailable",
    "ThrottlingException",
)

# Body markers for providers that wrap everything in one generic exception type (Bedrock, Vertex,
# OpenAI-compatible gateways), where the class name alone says nothing.
TRANSIENT_MARKERS = (
    "overloaded",
    "too many requests",
    "rate limit",
    "service unavailable",
    "temporarily unavailable",
    "try again later",
    "connection reset",
    "connection aborted",
    "incomplete chunked read",
    "peer closed connection",
    # A gateway idled out mid-stream ("Upstream idle timeout exceeded"): the connection stalled, and
    # re-sending re-rolls the routing, which is the whole case for transient.
    "idle timeout",
    # OpenAI's documented type for a failure on the server's side, which may be retried. A gateway
    # streams it as an SSE error event ("Streaming response failed: [server_error] upstream service
    # timeout"), the SDK raises a bare APIError around it and the class name says nothing. It ended
    # 2 of 9 GLM baseline probe runs on 2026-09-30 at their first or second step, unretried.
    "server_error",
    "upstream service timeout",
    "502 bad gateway",
    "503",
    "504 gateway",
    "529",
)

THROTTLE_TYPES = ("RateLimitError", "ThrottlingException")
THROTTLE_MARKERS = ("too many requests", "rate limit", "throttl", "429")
OVERLOADED_MARKERS = ("overloaded", "529")
CONTENT_POLICY_MARKERS = (
    "content_policy",
    "content policy",
    "content filter",
    "content_filter",
    "safety system",
)

MEDIA_WORDS = ("input_audio", "video_url", "audio", "video", "modalit", "media")
REFUSAL_WORDS = ("not support", "unsupported", "invalid", "cannot", "can't", "unknown", "not allowed", "not accept")


def has_any(text: str, markers: tuple[str, ...]) -> bool:
    return any(marker in text for marker in markers)


def class_names(exc: BaseException) -> set[str]:
    """The exception's own class name and those of every base."""
    return {type(exc).__name__} | {base.__name__ for base in type(exc).__mro__}
