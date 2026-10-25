"""Keeping the Socket Mode connection alive."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable

logger = logging.getLogger("harness.integrations.connectors")


def _is_alive(client: Any) -> bool:
    try:
        return bool(client and client.is_connected())
    except Exception:
        return False


async def watch(
    socket: Callable[[], Any],
    is_closing: Callable[[], bool],
    interval: float,
    on_reconnect: Callable[[], int],
) -> None:
    """Reconnect the Socket Mode connection when it dies without saying so.

    The SDK keeps the socket in background tasks and normally reconnects, but it can give up after a
    transient error while Slack cycles connections, leaving a dead socket that never recovers, and
    `start_async()` sleeps forever either way. So `is_connected()` is polled every `interval` seconds
    and a fresh endpoint is opened when it is down. `on_reconnect` counts each revival and returns
    the running total.
    """
    while not is_closing():
        try:
            await asyncio.sleep(interval)
        except asyncio.CancelledError:
            break
        handler = socket()
        if is_closing() or handler is None:
            break
        client = getattr(handler, "client", None)
        if _is_alive(client):
            continue
        logger.warning("slack socket mode connection down — reconnecting (watchdog)")
        try:
            await client.connect_to_new_endpoint(force=True)
            logger.info("slack socket mode reconnected (watchdog, #%d)", on_reconnect())
        except asyncio.CancelledError:
            break
        except Exception:
            logger.exception("slack watchdog reconnect failed — will retry")
