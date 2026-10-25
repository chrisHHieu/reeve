"""A message the person types into a running turn does not wait behind what the step waits on.

The engine's `person_waiting` is set by `person_steered` and cleared when the message is delivered;
the shell's waits read it and give their command a short grace, then return (the command goes on in
the background). The end-to-end case, a 60 s command and a steer, is in `test_server.py`.
"""

from __future__ import annotations

import threading
import time

from harness.tools import shell_wait
from harness.tools.shell_jobs import wait_for_end


class _Running:
    """A background task that never ends."""

    done = False


def test_a_wait_for_a_job_returns_soon_after_the_person_steers(monkeypatch):
    monkeypatch.setattr(shell_wait, "RELEASE_GRACE_SECONDS", 0.3)
    released = threading.Event()
    threading.Timer(0.2, released.set).start()
    started = time.monotonic()
    assert wait_for_end(_Running(), 30, None, released) is True
    assert time.monotonic() - started < 3


def test_a_wait_nobody_steers_into_runs_to_its_own_end():
    started = time.monotonic()
    assert wait_for_end(_Running(), 0.5, None, threading.Event()) is False
    assert time.monotonic() - started >= 0.5


class _Log:
    def __init__(self):
        self.rows = []

    def append(self, kind, **fields):
        self.rows.append((kind, fields))


def _engine(tmp_path):
    from harness.llm.providers import ModelCapabilities
    from harness.loop import TurnEngine
    from harness.safety.permissions import Mode, PermissionEngine
    from harness.tools import ToolRegistry

    class _Stub:
        def complete(self, **kwargs):  # pragma: no cover
            raise NotImplementedError

        def capabilities(self, model):
            return ModelCapabilities()

    engine = TurnEngine(
        provider=_Stub(),
        registry=ToolRegistry(),
        permissions=PermissionEngine(workspace_root=tmp_path, mode=Mode.WORKSPACE_WRITE),
        model="m",
    )
    engine.log = _Log()
    return engine


def test_the_persons_message_says_how_long_it_waited_and_clears_the_flag(tmp_path):
    engine = _engine(tmp_path)
    woken = []
    engine.add_person_steer_hook(lambda: woken.append(True))
    engine.queue_steering("answer me first")
    engine.person_steered()
    assert engine.person_waiting.is_set() and woken == [True]
    time.sleep(0.1)
    engine._inject_steering()
    assert not engine.person_waiting.is_set()
    (kind, row), = engine.log.rows
    assert kind == "steering/message" and row["waited_ms"] >= 80


def test_a_note_the_harness_queues_is_not_the_person_waiting(tmp_path):
    """A restart note or a parent's message to its child queues the same way, and the waits
    must not give up for it."""
    engine = _engine(tmp_path)
    engine.queue_steering("Reeve restarted; the calls above were not run")
    assert not engine.person_waiting.is_set()
    engine._inject_steering()
    (_, row), = engine.log.rows
    assert "waited_ms" not in row
