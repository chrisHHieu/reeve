"""Connector failures, with the next move attached.

A tool result is read by two audiences at once: the model, which decides what to do next,
and the person watching, on the result card. `channel_not_found` serves neither. The model
retries the same call because nothing told it not to, and the person gets a word that is
not in their language attached to a thing that did not happen — which is a support ticket,
not an error message.

The pattern already existed in this package. `_send_slack` translated exactly one code:

    if err == "not_in_channel":
        err = "not_in_channel — invite @Reeve to the channel in Slack, then retry"

Good sentence, wrong home. Its two sibling senders — the interactive one and the file
upload, in the same file — return the raw code, and so does every Telegram failure and
every network error. So this module is not a new idea; it is the existing one, moved to
where all of them pass.

Two rules, both borrowed from `providers/errors.py`, which solved the same problem for
model access failures:

**An unrecognized error is returned unchanged.** Never invent a next step. A confident
wrong instruction costs more than a word the reader has to look up, because they will
follow it.

**Match on the error text, not on a status.** The codes are what the platforms actually
return; the shape of the transport says nothing about what to do.
"""

from __future__ import annotations

#: Slack Web API `error` codes, verbatim. Each maps to what the reader should DO — never
#: to a restatement of what failed.
_SLACK = {
    "not_in_channel": (
        "Reeve isn't in that channel — invite it in Slack, then try again."
    ),
    "channel_not_found": (
        "no such channel in the connected workspace — check the name, or connect the "
        "workspace that has it under Settings, Connectors."
    ),
    "is_archived": (
        "that channel is archived — unarchive it in Slack, or send to another one."
    ),
    "invalid_auth": (
        "the Slack connection is no longer valid — reconnect Slack under Settings, "
        "Connectors."
    ),
    "token_revoked": (
        "the Slack connection was revoked — reconnect Slack under Settings, Connectors."
    ),
    "account_inactive": (
        "the Slack account for this connection is deactivated — reconnect with an active "
        "account under Settings, Connectors."
    ),
    "missing_scope": (
        "the Slack app is missing a permission it needs — reconnect Slack under Settings, "
        "Connectors to grant it."
    ),
    "not_allowed_token_type": (
        "this Slack connection is the wrong kind of app for that action — reconnect Slack "
        "under Settings, Connectors."
    ),
    "restricted_action": (
        "the workspace's rules don't allow posting there — ask a Slack admin, or send to "
        "another channel."
    ),
    "no_permission": (
        "this Slack app isn't allowed to do that — ask a Slack admin to grant it, or send "
        "to another channel."
    ),
    "msg_too_long": "the message is too long for Slack — send it in shorter parts.",
    "rate_limited": "Slack is rate-limiting right now — wait a minute and try again.",
    "ratelimited": "Slack is rate-limiting right now — wait a minute and try again.",
}

#: Telegram returns prose in `description`, so these match on a fragment of it.
_TELEGRAM = (
    (
        "chat not found",
        "Telegram doesn't know that chat — the person has to message the bot once before "
        "it can write to them.",
    ),
    (
        "bot was blocked by the user",
        "that person blocked the bot on Telegram — nothing can reach them until they "
        "unblock it.",
    ),
    (
        "user is deactivated",
        "that Telegram account is deactivated — nothing can be delivered to it.",
    ),
    (
        "not enough rights",
        "the bot isn't allowed to post in that Telegram chat — give it permission in the "
        "chat's settings.",
    ),
    (
        "unauthorized",
        "the Telegram connection is no longer valid — reconnect Telegram under Settings, "
        "Connectors.",
    ),
    (
        "message is too long",
        "the message is too long for Telegram — send it in shorter parts.",
    ),
    ("too many requests", "Telegram is rate-limiting right now — wait a minute and try again."),
)

#: The transport never arrived. Matched last, and on any platform: these are exception
#: strings, not API codes.
_UNREACHABLE = (
    "getaddrinfo",
    "name or service not known",
    "temporary failure in name resolution",
    "connecttimeout",
    "connecterror",
    "connection refused",
    "timed out",
    "readtimeout",
    "ssl",
)


def actionable(error: str, *, platform: str = "") -> str:
    """The same failure with the next move attached, or the error unchanged.

    Unchanged is the honest answer for anything unrecognized: the reader is no worse off
    than today, and no one is sent to do the wrong thing.
    """
    raw = str(error or "").strip()
    if not raw:
        return raw
    text = raw.lower()

    if platform == "slack":
        # Slack returns a bare code, so match the whole string first; the code can also
        # arrive with detail appended, hence the prefix check.
        for code, sentence in _SLACK.items():
            if text == code or text.startswith(code + " ") or text.startswith(code + ":"):
                return sentence
    if platform == "telegram":
        for fragment, sentence in _TELEGRAM:
            if fragment in text:
                return sentence

    if any(marker in text for marker in _UNREACHABLE):
        where = platform.title() if platform else "the service"
        return f"couldn't reach {where} — check the network connection and try again."
    return raw
