"""Telling an authorization failure apart, wherever it ended up in an exception tree."""

from __future__ import annotations


class InteractiveAuthRequired(RuntimeError):
    """The server wants a browser sign-in, and this context must not open one.

    An interactive sign-in (the browser, then waiting on the loopback) is a privilege of an explicit
    connect only. A background context that reaches this, such as an engine turn or a tools listing,
    raises instead and the caller skips the server. Without that, a server whose refresh token the
    vendor rejected (Atlassian rotates them aggressively) would take over the person's browser from
    any code path that touched it. An authorize page once opened at app launch for this reason
    (2026-07-20)."""


def _in_tree(error: BaseException, test) -> bool:
    """Whether `test` holds for the error, for anything in an exception group it carries, or for
    anything in its cause chain. The SDK transport runs in anyio task groups, so the error often
    arrives wrapped in an ExceptionGroup or chained and not bare."""
    if test(error):
        return True
    if any(_in_tree(inner, test) for inner in getattr(error, "exceptions", None) or []):
        return True
    cause = error.__cause__ or error.__context__
    return _in_tree(cause, test) if cause is not None else False


def is_auth_required(error: BaseException) -> bool:
    """Whether `InteractiveAuthRequired` is anywhere in the exception tree."""
    return _in_tree(error, lambda e: isinstance(e, InteractiveAuthRequired))


def is_http_auth_error(error: BaseException) -> bool:
    """Whether an HTTP 401 or 403 is anywhere in the exception tree.

    An anonymous connect met a server that wants credentials, so the remedy is to sign in, by
    switching the entry to `auth: oauth`, and not to change the config otherwise."""
    return _in_tree(
        error,
        lambda e: getattr(getattr(e, "response", None), "status_code", None) in (401, 403),
    )
