"""How an MCP call fails, and how it is described to the caller.

Split out of `client.py` when the reconnect work pushed it past the architecture ceiling.
Cohesive on its own: the two classifiers that decide whether a failure is a
DEAD CONNECTION (worth one reconnect) or a broken tool (re-raised untouched), and the one
message every timeout path shares.
"""

from __future__ import annotations

#: Exception type names the MCP SDK uses for a transport that is gone. Type-first because
#: a message can say anything; these three are the SDK's own signals.
_DISCONNECT_TYPES = frozenset(
    {"ClosedResourceError", "BrokenResourceError", "EndOfStream"}
)

#: Message fragments, for the failures that arrive as an ordinary exception from a
#: transport we do not own (an HTTP client, a wrapper). Deliberately narrow: only
#: only on the exact "Not connected" error, and a broad match here would turn a tool bug
#: into a reconnect loop.
_DISCONNECT_MARKERS = (
    "not connected",
    "connection closed",
    "closed resource",
    "transport is closed",
)


def looks_disconnected(exc: BaseException) -> bool:
    """Whether this failure is a dead connection rather than a broken tool."""
    if type(exc).__name__ in _DISCONNECT_TYPES:
        return True
    text = str(exc).lower()
    return any(marker in text for marker in _DISCONNECT_MARKERS)


def timeout_error(name: str, tool: str, budget: float) -> TimeoutError:
    """The one timeout message every path raises, so the text a model reads is stable.

    It says the request was CANCELLED, not merely abandoned: `wait_for` stops the in-flight
    request, which is the difference between "the server is still working on it" and "it
    will never answer" — and the model's next move depends on which is true.
    """
    return TimeoutError(
        f"MCP tool {name}/{tool} did not answer within {budget:g}s "
        "(the request was cancelled)"
    )
