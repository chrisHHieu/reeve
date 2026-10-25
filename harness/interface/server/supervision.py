"""Keeping the server answering: uvicorn under a watchdog that restarts a dead listener.

The failure it exists for: on Windows the proactor can raise an accept error when a client
vanishes mid-handshake. Left unhandled it logs a traceback and may kill the accept loop while
the process lives on, so the sidecar looks alive and serves nothing, and the GUI waits on
"Starting Reeve…" for good. A process that is alive with no listener is worse than a crashed
one, since nothing outside can tell it from "still starting".
"""

from __future__ import annotations

import asyncio
import logging
import socket
import threading
import time
from typing import Any, Callable

from .app import MAX_FRAME_BYTES

logger = logging.getLogger(__name__)

# Windows errors that mean "a client went away", not "the server is broken": 64 (the network
# name is no longer available), 10053 and 10054 (software-caused abort, connection reset).
_ACCEPT_NOISE_WINERRORS = (64, 10053, 10054)
_PROBE_EVERY_SECONDS = 10.0
_MISSES_BEFORE_RESTART = 3
_MAX_ATTEMPTS = 10
# A server that stayed up this long before it stopped was working: its restart is the first
# of a new run of them, not one more of the last.
_HEALTHY_SECONDS = 120.0
# How long a stopped server's manager gets to close before the next one is built.
_RETIRE_SECONDS = 15.0


def is_accept_noise(error: BaseException | None) -> bool:
    if isinstance(error, (ConnectionResetError, ConnectionAbortedError)):
        return True
    return isinstance(error, OSError) and getattr(error, "winerror", None) in _ACCEPT_NOISE_WINERRORS


def port_alive(host: str, port: int, *, timeout: float = 1.0) -> bool:
    """Whether something still accepts connections on the bound port."""
    target = "127.0.0.1" if host in ("", "0.0.0.0", "::") else host
    try:
        with socket.create_connection((target, port), timeout=timeout):
            return True
    except OSError:
        return False


def _watch_listener(server: Any, host: str, port: int, stop: threading.Event, restart: dict) -> None:
    """Probe the port until `stop`; after enough consecutive misses ask the server to exit and
    flag that the outer loop should start a fresh one."""
    misses = 0
    while not stop.wait(_PROBE_EVERY_SECONDS):
        if port_alive(host, port):
            misses = 0
            continue
        misses += 1
        if misses >= _MISSES_BEFORE_RESTART:
            logger.warning("sidecar listener stopped answering; restarting the server")
            restart["wanted"] = True
            server.should_exit = True
            return


def _retire(app: Any) -> None:
    """Close the manager of an app whose server just stopped, if its own teardown did not.

    A server that ended in an exception may never have run its lifespan teardown. The next
    attempt builds another manager, and two that are both open would both tick the scheduler
    and both hold the connector sockets."""
    manager = getattr(getattr(app, "state", None), "manager", None)
    if manager is None or getattr(manager, "shutting_down", False):
        return
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(asyncio.wait_for(manager.aclose(), timeout=_RETIRE_SECONDS))
    except Exception:  # noqa: BLE001 - the restart goes ahead either way
        logger.warning("the stopped server's manager did not close cleanly", exc_info=True)
    finally:
        loop.close()


def serve_forever(build: Callable[[], Any], host: str, port: int) -> bool:
    """Run uvicorn with supervision. Accept noise is dropped, a dead listener restarts, and an
    orderly shutdown (a signal) returns.

    `build` makes the app, and it is called again for every restart: a restart is a process
    start in everything but the pid. The app that stopped is not served again, because its
    manager was closed by the lifespan teardown and closing is final. Serving it again on the
    new loop left a server that looked alive and refused every message ("Reeve is closing"),
    with its MCP lock bound to the loop that had just been closed, so every connect failed
    with "is bound to a different event loop" (2026-10-13, proven by running one lifespan and
    then a turn on the same manager).

    Returns whether the server asked to be started again as a new process (`POST /v1/restart`):
    an in-process rebuild keeps the code it started with, and a restart is asked for to run code
    edited since."""
    import uvicorn

    def quiet(loop: asyncio.AbstractEventLoop, context: dict) -> None:
        if not is_accept_noise(context.get("exception")):
            loop.default_exception_handler(context)

    attempts = 0
    while True:
        app = build()
        config = uvicorn.Config(app, host=host, port=port, ws_max_size=MAX_FRAME_BYTES)
        loop = asyncio.new_event_loop()
        loop.set_exception_handler(quiet)
        asyncio.set_event_loop(loop)
        server = uvicorn.Server(config)
        # The graceful-quit handle: `POST /v1/shutdown` sets `should_exit`, so uvicorn stops
        # accepting, drains and runs the lifespan teardown (`manager.aclose`). A plain kill
        # skips the teardown entirely.
        app.state.uvicorn_server = server
        stop = threading.Event()
        restart = {"wanted": False}
        threading.Thread(target=_watch_listener, args=(server, host, port, stop, restart), daemon=True).start()
        crashed: BaseException | None = None
        began = time.monotonic()
        try:
            loop.run_until_complete(server.serve())
        except Exception as error:  # noqa: BLE001 - a supervisor restarts and does not die
            crashed = error
        finally:
            stop.set()
            try:
                loop.close()
            except Exception:
                pass
        if crashed is None and not restart["wanted"]:
            return bool(getattr(app.state, "restart_requested", False))
        # The limit is on a server that cannot stay up, not on a lifetime's worth of hiccups. A
        # server that ran a while before it stopped starts the count again. Without this, ten
        # restarts spread over six hours ended the process with "sidecar listener kept dying"
        # (a dev backend, 2026-10-13: its log shows 11 restarts, each minutes or hours apart).
        if time.monotonic() - began >= _HEALTHY_SECONDS:
            attempts = 0
        attempts += 1
        if crashed is not None:
            logger.warning("server exited with %s; restarting (attempt %d)", crashed, attempts)
        if attempts > _MAX_ATTEMPTS:
            raise crashed if crashed is not None else RuntimeError("sidecar listener kept dying")
        _retire(app)
        time.sleep(min(2.0, 0.25 * attempts) if crashed is not None else 0.5)
