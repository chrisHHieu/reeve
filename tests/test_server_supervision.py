"""Supervision of the sidecar server loop (supervision.py): Windows accept noise is classified
as noise, the port watchdog tells a live listener from a dead one, and a restart serves an app
that was built for it."""

from __future__ import annotations

import asyncio
import socket
import sys
from types import SimpleNamespace

from harness.interface.server import SessionManager, create_app, supervision
from harness.interface.server.supervision import is_accept_noise as _is_accept_noise, port_alive as _port_alive
from harness.llm.providers import ModelCapabilities, ProviderClient


class _NoProvider(ProviderClient):
    def complete(self, *, model, messages, tools=None, **settings):  # pragma: no cover - never asked
        raise AssertionError("no turn runs in these tests")

    def capabilities(self, model):
        return ModelCapabilities()


def _fake_uvicorn(monkeypatch, serve):
    """A uvicorn whose server runs `serve(server)` on the loop supervision made for it."""

    class _Config:
        def __init__(self, app, **kwargs):
            self.app = app

    class _Server:
        def __init__(self, config):
            self.config = config
            self.should_exit = False

        async def serve(self):
            await serve(self)

    monkeypatch.setitem(sys.modules, "uvicorn", SimpleNamespace(Config=_Config, Server=_Server))


def test_a_restart_serves_a_manager_built_for_it(tmp_path, monkeypatch):
    """The watchdog's restart used to serve the same app on a new loop. Its manager had been
    closed by the lifespan teardown, so it refused every message, and its MCP lock belonged to
    the loop that was gone (2026-10-13)."""
    managers = []

    def build():
        manager = SessionManager(workspace=tmp_path, provider=_NoProvider())
        managers.append(manager)
        return create_app(manager)

    open_for_work = []

    async def serve(server):
        app = server.config.app
        async with app.router.lifespan_context(app):
            manager = app.state.manager
            open_for_work.append(not manager.shutting_down)

            async def hold():
                async with manager.mcp._lock:
                    await asyncio.sleep(0.01)

            # Two takers at once, the way two servers connect in one arm: the lock has to
            # belong to the loop that is running now.
            await asyncio.gather(hold(), hold())
            if len(managers) == 1:
                while not server.should_exit:
                    await asyncio.sleep(0.01)

    def watch(server, host, port, stop, restart):
        if len(managers) == 1:  # the first listener "stops answering"
            restart["wanted"] = True
            server.should_exit = True

    _fake_uvicorn(monkeypatch, serve)
    monkeypatch.setattr(supervision, "_watch_listener", watch)
    monkeypatch.setattr(supervision.time, "sleep", lambda seconds: None)

    supervision.serve_forever(build, "127.0.0.1", 0)

    assert len(managers) == 2 and managers[0] is not managers[1]
    assert open_for_work == [True, True]
    assert managers[0].shutting_down is True  # the one that stopped stays closed


def _serve_until_told(monkeypatch, lifetimes):
    """Run `serve_forever` over servers that each "run" for one of `lifetimes` seconds and then
    crash, on a clock the test moves. Returns how many servers were built before it gave up or
    ran out of lifetimes (the last one exits cleanly)."""
    clock = {"now": 0.0}
    built = []

    def build():
        built.append(len(built))
        return SimpleNamespace(state=SimpleNamespace(manager=None))

    async def serve(server):
        number = len(built) - 1
        if number >= len(lifetimes):
            return  # an orderly exit ends the supervision
        clock["now"] += lifetimes[number]
        raise RuntimeError("the accept loop died")

    _fake_uvicorn(monkeypatch, serve)
    monkeypatch.setattr(supervision, "_watch_listener", lambda *args: None)
    monkeypatch.setattr(supervision.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(supervision.time, "monotonic", lambda: clock["now"])
    try:
        supervision.serve_forever(build, "127.0.0.1", 0)
        return len(built), None
    except RuntimeError as error:
        return len(built), str(error)


def test_restarts_far_apart_do_not_add_up_to_giving_up(monkeypatch):
    """Ten restarts over six hours ended a dev backend with "sidecar listener kept dying". The
    limit is for a server that cannot stay up; one that ran for minutes starts the count again."""
    built, error = _serve_until_told(monkeypatch, [600.0] * 25)
    assert error is None
    assert built == 26  # every one of them was replaced, and the last exited cleanly


def test_a_server_that_cannot_stay_up_is_given_up_on(monkeypatch):
    built, error = _serve_until_told(monkeypatch, [1.0] * 25)
    assert error == "the accept loop died"
    assert built == supervision._MAX_ATTEMPTS + 1


def test_a_crashed_server_has_its_manager_closed_before_the_next_is_built(monkeypatch):
    """A server that dies in an exception may not have run its teardown. Two open managers
    would both run the scheduler and both hold the connector sockets."""
    events = []

    class _Manager:
        def __init__(self, number):
            self.number = number
            self.shutting_down = False

        async def aclose(self):
            self.shutting_down = True
            events.append(f"closed {self.number}")

    apps = []

    def build():
        events.append(f"built {len(apps)}")
        apps.append(SimpleNamespace(state=SimpleNamespace(manager=_Manager(len(apps)))))
        return apps[-1]

    async def serve(server):
        if len(apps) == 1:
            raise RuntimeError("the accept loop died")

    _fake_uvicorn(monkeypatch, serve)
    monkeypatch.setattr(supervision, "_watch_listener", lambda *args: None)
    monkeypatch.setattr(supervision.time, "sleep", lambda seconds: None)

    supervision.serve_forever(build, "127.0.0.1", 0)

    assert events == ["built 0", "closed 0", "built 1"]
    assert apps[1].state.manager.shutting_down is False


def test_accept_noise_classification():
    win64 = OSError(0, "The specified network name is no longer available")
    win64.winerror = 64
    assert _is_accept_noise(win64) is True
    assert _is_accept_noise(ConnectionResetError(10054, "connection reset")) is True
    assert _is_accept_noise(ConnectionAbortedError()) is True
    assert _is_accept_noise(OSError(2, "No such file")) is False
    assert _is_accept_noise(ValueError("nope")) is False
    assert _is_accept_noise(None) is False


def test_port_alive_distinguishes_a_listener_from_a_closed_port():
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(5)  # room for both probes: they queue until accepted
    port = listener.getsockname()[1]
    try:
        assert _port_alive("127.0.0.1", port) is True
        # 0.0.0.0 is probed on loopback, not connected to as an address.
        assert _port_alive("0.0.0.0", port) is True
    finally:
        listener.close()
    assert _port_alive("127.0.0.1", port) is False
