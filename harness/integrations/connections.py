"""Which connectors a conversation can reach.

Two things decide it. The account side: a connector with a working profile is connected, which is
the secret store's business and not recorded here. The conversation side: a switch the person
flipped for this one conversation, kept in `session_connections.json` by `SessionConnectionStore`.

A connected connector is usable unless its conversation switched it off. Personas once added a
layer of defaults between the two; it left with them, and the file it used,
`persona_connections.json`, is no longer read.
"""

from __future__ import annotations

from harness.session.toggles import SessionToggles


class SessionConnectionStore(SessionToggles):
    """A conversation's connector switches. A connector it never switched is on."""


def effective(*, connected: set[str], session_overrides: dict[str, bool]) -> dict[str, bool]:
    """The connectors this conversation can use, as `{name: True}`; switched-off ones are absent."""
    return {name: True for name in connected if session_overrides.get(name, True)}
