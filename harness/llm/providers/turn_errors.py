"""What kind of error ended a turn: `turn/end {error, error_at?}`."""

from __future__ import annotations

import re
import traceback
from pathlib import Path
from typing import Optional

from .errors import classify_stream_error

#: A turn that ended on an error, by what the person or the harness would have to do about it
#: (`turn/end {error}`, read by `harness_review`). Wider than `classify_stream_error`, which
#: decides retries: a missing key and a refused request are both "do not retry", but one is
#: the person's to fix and the other is the request this harness built.
_NO_KEY = ("api key configured", "not signed in", "no credentials", "sign in to")
_PLAN_LIMIT = ("plan limit reached", "usage limit", "openrouter_credits", "limit_source")
_NOT_OFFERED = ("no access to", "is not available in your region", "regionerror", "is not supported")
_BAD_REQUEST = (
    "invalid_argument",
    "invalid_request_error",
    "validation error",
    "tool_call_id",
    "missingsessionid",
)
#: Python errors raised by this app's own code on the way to a request: a bug here, not there.
_CODE_ERRORS = ("ImportError", "AttributeError", "NameError", "KeyError", "TypeError", "IndexError")

#: A provider that answers with a status and no words a rule above reads still said
#: something. The first error ending after endings got a class was a
#: 404 for a model the project could not reach (2026-10-06), and it read `unknown`; a bare
#: 400, 401 or 422 would have too. Only where nothing above matched: an unexplained 400
#: stays `routing` and a 503 `transport`, which is what a retry is decided on.
_BY_STATUS = {
    400: "bad_request",
    401: "no_key",
    402: "no_quota",
    403: "no_access",
    404: "no_access",
    408: "transport",
    413: "bad_request",
    422: "bad_request",
    429: "rate_limit",
}
_STATUS_RE = re.compile(r"\berror code: (\d{3})\b")
#: The engine's package folder, which a traceback frame is named relative to.
_PACKAGE = Path(__file__).resolve().parents[2]


def _status(exc: BaseException, text: str) -> Optional[int]:
    """The HTTP status an error carries: the SDK's attribute, else the code its text opens with."""
    for name in ("status_code", "status"):
        value = getattr(exc, name, None)
        if isinstance(value, int) and 100 <= value <= 599:
            return value
    found = _STATUS_RE.search(text)
    return int(found.group(1)) if found else None


def turn_error_class(exc: BaseException) -> str:
    """One of: no_key | no_quota | no_access | bad_request | code | content_policy | empty |
    rate_limit | overloaded | routing | transport | unknown, for a turn that ended on `exc`.

    Measured 2026-10-06: `classify_stream_error` called 43 of 54 error endings since
    2026-09-01 `unknown`. By their text, about 20 were the provider refusing a request this
    app built (a schema it would not take, an unpaired tool call), 8 an exhausted plan or
    balance, 6 a missing key. What the text does not say, the status does."""
    names = {type(exc).__name__} | {base.__name__ for base in type(exc).__mro__}
    if names & set(_CODE_ERRORS) and not getattr(exc, "status_code", None):
        return "code"
    text = str(exc).lower()
    if any(marker in text for marker in _NO_KEY):
        return "no_key"
    if any(marker in text for marker in _PLAN_LIMIT):
        return "no_quota"
    if any(marker in text for marker in _NOT_OFFERED):
        return "no_access"
    found = classify_stream_error(exc)
    if found != "unknown":
        return found
    if any(marker in text for marker in _BAD_REQUEST):
        return "bad_request"
    status = _status(exc, text)
    if status is not None:
        if status in _BY_STATUS:
            return _BY_STATUS[status]
        if 500 <= status <= 599:
            return "overloaded"
    return "unknown"


def raised_at(exc: BaseException) -> str:
    """`<ExceptionType> at <file>:<function>` for the innermost frame of this app's own code
    that `exc` passed through, or just the type when it never did.

    For an ending that is this app's fault (`code`, `crashed`): the class says a bug is here,
    this says where. Type and location only, never the message, which can quote what the
    person wrote."""
    name = type(exc).__name__
    for frame in reversed(traceback.extract_tb(exc.__traceback__)):
        try:
            where = Path(frame.filename).resolve().relative_to(_PACKAGE)
        except (ValueError, OSError):
            continue
        return f"{name} at {where.as_posix()}:{frame.name}"
    return name


__all__ = ["raised_at", "turn_error_class"]
