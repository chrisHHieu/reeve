"""Transport timeouts for provider clients: a stalled stream must not hang a turn.

The SDKs default to a 10-minute read timeout and no total cap. A provider that accepts
the connection and then stops sending bytes hangs the turn with no logged reason until
that default expires — the same class is answered with an
idle watchdog (consumer think time is not provider idle time). At
the HTTP layer the same guarantee is a **read** timeout: it counts only time waiting on
the socket, so a slow consumer never triggers it, and it fires exactly when the
provider stops sending mid-stream. The engine's own retry loop then sees a transport
failure and acts on it (one visible attempt per stream — see `max_retries=0`).
"""

from __future__ import annotations

#: Time with no bytes on the wire before the request fails. Generous enough for a
#: reasoning model's long first token, tight enough that a dead stream surfaces as a
#: retryable failure rather than a hung turn.
STREAM_IDLE_SECONDS = 180.0
CONNECT_SECONDS = 15.0
WRITE_SECONDS = 30.0
POOL_SECONDS = 15.0


def stream_timeout(sdk=None):
    """A Timeout for SDK clients: idle reads bounded, no total cap.

    Built from the class of the SDK's OWN `DEFAULT_TIMEOUT` when `sdk` (the imported SDK
    module) is given. `anthropic` 1.0 moved to `httpx2`; handed a plain `httpx.Timeout`,
    its transport did not recognise the type and passed the object itself down to
    `socket.settimeout` — `TypeError: 'Timeout' object cannot be interpreted as an
    integer` on the first connect, so every route over the Anthropic wire failed before
    sending a byte: native Claude and opencode-go's `messages` models (qwen3.8-flash,
    minimax-m2.5/m2.7/m3). Found by the harness eval on qwen3.8-flash, 2026-09-24.
    """
    default = getattr(sdk, "DEFAULT_TIMEOUT", None) if sdk is not None else None
    if default is not None:
        cls = type(default)
    else:
        import httpx

        cls = httpx.Timeout
    return cls(
        connect=CONNECT_SECONDS,
        read=STREAM_IDLE_SECONDS,
        write=WRITE_SECONDS,
        pool=POOL_SECONDS,
    )


__all__ = [
    "CONNECT_SECONDS",
    "POOL_SECONDS",
    "STREAM_IDLE_SECONDS",
    "WRITE_SECONDS",
    "stream_timeout",
]
