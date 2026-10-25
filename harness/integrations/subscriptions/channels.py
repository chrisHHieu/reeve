"""Turning what a person pasted into a channel address, `<platform>:<chat_id>`."""

from __future__ import annotations

import re

# A typed `#channel` reaches us as `<#C0123|name>`: the id is in the answer itself.
_MENTION = re.compile(r"<#(C[A-Z0-9]+)\|?[^>]*>")
# "Copy link" on a channel gives https://acme.slack.com/archives/C0123ABC. Taking the paste is
# easier on people than asking them to find the id on the About tab.
_ARCHIVE_LINK = re.compile(r"slack\.com/archives/([A-Za-z0-9]+)")


def resolve_channel(ref: str, *, default_platform: str = "slack") -> str:
    """The address for a channel mention, a "Copy link" URL, a full address (`slack:C0123`) or a
    bare id (taken to be on `default_platform`). A bare `#name` gives "": names cannot be looked up
    here, and saving one would make a subscription no real message ever matches."""
    ref = (ref or "").strip()
    if found := _MENTION.search(ref):
        return f"slack:{found.group(1)}"
    if found := _ARCHIVE_LINK.search(ref):
        return f"slack:{found.group(1).upper()}"
    if ref.startswith("#"):
        return ""
    if ":" in ref or not ref:
        return ref
    return f"{default_platform}:{ref}"
