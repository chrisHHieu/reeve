"""What of a tool call may be written to the audit log: arguments with secrets and bodies removed, and
a short note of the resource the call touched."""

from __future__ import annotations

from typing import Any

_SECRET_KEYS = (
    "token",
    "secret",
    "password",
    "api_key",
    "access_token",
    "bot_token",
    "app_token",
    "raw",
)
_BODY_KEYS = ("body", "content", "html")

# Argument names that identify what a call acted on, in the order they are tried.
_RESOURCE_KEYS = (
    "url",
    "owner",
    "repo",
    "issue_key",
    "page_id",
    "ticket_id",
    "calendar_id",
    "message_id",
)

_SUMMARY_LIST_ITEMS = 10
_SUMMARY_DICT_ITEMS = 20


def truncate(text: str, limit: int = 500) -> str:
    text = text.replace("\n", "\\n")
    return text if len(text) <= limit else text[: limit - 3] + "..."


def summarize(value: Any) -> Any:
    """A value reduced to something small enough to keep: strings clipped, collections cut short."""
    if isinstance(value, str):
        return truncate(value)
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    if isinstance(value, list):
        return [summarize(item) for item in value[:_SUMMARY_LIST_ITEMS]]
    if isinstance(value, dict):
        return {str(k): summarize(v) for k, v in list(value.items())[:_SUMMARY_DICT_ITEMS]}
    return truncate(str(value))


def sanitize_args(tool: str, args: dict[str, Any]) -> dict[str, Any]:
    """The call's arguments as logged. Anything that looks like a credential is replaced, so is the
    text typed into a browser, and so is a message body or document, which can hold anything."""
    if not isinstance(args, dict):
        return {}
    cleaned: dict[str, Any] = {}
    for key, value in args.items():
        name = str(key).lower()
        if any(secret in name for secret in _SECRET_KEYS):
            cleaned[key] = "[redacted]"
        elif tool == "browser_type" and name == "text":
            cleaned[key] = "[redacted input]"
        elif any(name == body or name.endswith("_" + body) for body in _BODY_KEYS):
            cleaned[key] = "[redacted body]"
        else:
            cleaned[key] = summarize(value)
    return cleaned


def resource_of(tool: str, args: dict[str, Any], result: Any) -> str:
    """What the call acted on, from its arguments or else its result, or an empty string."""
    if isinstance(args, dict):
        for key in _RESOURCE_KEYS:
            if args.get(key):
                return str(args[key])
        if args.get("subdomain"):
            return f"{args['subdomain']}.zendesk.com"
    if isinstance(result, dict) and result.get("url"):
        return str(result["url"])
    return ""
