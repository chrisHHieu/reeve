"""Telling a failure worth re-sending from one that is not, and how many re-sends it earns.

The SDKs already retry the REQUEST: OpenAI and Anthropic make two attempts on connection errors,
408/409/429/5xx, and boto3 runs standard mode. None of that covers a stream that dies AFTER the first
chunk, because an exception raised while iterating is a failed iteration and no SDK retries it. An
answer cut at byte 1 got two more attempts and one cut at byte 40,000 got none, and this classifier
closes that gap.

It is conservative on purpose: anything unrecognized is NOT retried. Retrying a permanent failure
costs another full prompt in tokens and another wait before the same error shows, which is worse
than showing it at once. A false negative only restores the old behaviour.
"""

from __future__ import annotations

import re

from .markers import (
    CONTENT_POLICY_MARKERS,
    MEDIA_WORDS,
    NO_ACCESS,
    NO_QUOTA,
    OVERLOADED_MARKERS,
    REFUSAL_WORDS,
    THROTTLE_MARKERS,
    THROTTLE_TYPES,
    TRANSIENT_MARKERS,
    TRANSIENT_TYPES,
    class_names,
    has_any,
)

#: The whole text of a 400 that names no error, only the model it was asked for.
_UNEXPLAINED_400 = re.compile(r"^error code: 400 - \{'model': '[^']*'\}$")

#: Attempts per class. Rate limits and overloaded upstreams are the provider asking for patience, so
#: they get more than a flaky connection. A class not listed, which includes every permanent one,
#: gets zero: surface it and let the person decide.
_RETRY_BUDGETS = {
    "rate_limit": 4,
    "overloaded": 4,
    "transport": 2,
    "empty": 2,
    "routing": 2,
}


def unexplained_refusal(exc: BaseException) -> bool:
    """A 400 whose body says nothing about what was wrong: a routing failure dressed as a bad
    request.

    A gateway that pins each session to one backend answers `400 {'model': '<id>'}` when that
    backend fails, a body with no error in it. A real bad request always names its fault. Measured
    2026-09-29/30:
    - two history-eval turns died on it mid-fix, while a parallel session on the same model and key
      was answered 1s before and 9s after each failure;
    - the identical requests, re-sent later, were answered 10 times out of 10, under a fresh routing
      key and under the original one;
    - in real sessions a retry on the same session 52s later failed again, and the person got going
      only by switching provider.

    So for a while the fault is the session's backend, not the request.
    """
    status = getattr(exc, "status_code", None)
    body = getattr(exc, "body", None)
    if status == 400 and isinstance(body, dict):
        return not any(key in body for key in ("error", "message", "detail", "type", "code"))
    return bool(_UNEXPLAINED_400.match(str(exc).strip().lower()))


def media_rejected(exc: BaseException) -> bool:
    """A 400/422 whose text blames an audio or video input.

    The picker lists what a model takes and the sender acts on it, so a recording goes out. A route
    that still refuses (a gateway that does not forward the part, a variant that cannot hear) answers
    400, and because the recording stays in the history every later request would fail the same way.
    The engine uses this to stop sending it on that model and say so; a false positive only means the
    recording is named instead of sent.
    """
    status = getattr(exc, "status_code", None)
    text = str(exc).lower()
    if status not in (400, 422) and "error code: 400" not in text and "error code: 422" not in text:
        return False
    return has_any(text, MEDIA_WORDS) and has_any(text, REFUSAL_WORDS)


def is_transient(exc: BaseException) -> bool:
    """True when re-sending the same request has a real chance of a different answer.

    Permanent failures are checked FIRST: a quota 429 and a rate-limit 429 share a status and only
    the body tells them apart, and retrying "you are out of credits" three times spends the prompt
    three times to print the same sentence.
    """
    text = str(exc).lower()
    if has_any(text, NO_QUOTA + NO_ACCESS):
        return False
    if class_names(exc) & set(TRANSIENT_TYPES):
        return True
    return unexplained_refusal(exc) or has_any(text, TRANSIENT_MARKERS)


def classify_stream_error(exc: BaseException) -> str:
    """One of: no_quota, no_access, content_policy, empty, rate_limit, overloaded, routing,
    transport, unknown. Permanent classes are checked first, as in `is_transient`. `routing` is
    `unexplained_refusal`: the retry goes out on a new routing key (the engine's `_reroute`), because
    the same key keeps reaching the same failing backend.

    `is_transient` answers yes or no; the loop needs a third column, since WHAT failed decides how
    many retries it gets. A rate limit is routine (waiting works, and one prompt re-sent is cheaper
    than a dead turn), while an account out of credits is permanent and its friendly message should
    appear on the FIRST failure.
    """
    text = str(exc).lower()
    if has_any(text, NO_QUOTA):
        return "no_quota"
    if has_any(text, NO_ACCESS):
        return "no_access"
    if has_any(text, CONTENT_POLICY_MARKERS):
        return "content_policy"
    names = class_names(exc)
    if "EmptyCompletion" in names:
        return "empty"
    if names & set(THROTTLE_TYPES) or has_any(text, THROTTLE_MARKERS):
        return "rate_limit"
    if has_any(text, OVERLOADED_MARKERS):
        return "overloaded"
    if unexplained_refusal(exc):
        return "routing"
    if names & (set(TRANSIENT_TYPES) - set(THROTTLE_TYPES)) or has_any(text, TRANSIENT_MARKERS):
        return "transport"
    return "unknown"


def retry_budget(exc: BaseException) -> int:
    """How many mid-stream retries this failure is worth."""
    if not is_transient(exc):
        return 0
    return _RETRY_BUDGETS.get(classify_stream_error(exc), 0)
