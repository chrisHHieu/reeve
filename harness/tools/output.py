"""Live tool output: from a worker thread to the turn's event stream.

A tool that produces output over time (a shell command, a long-running CLIm) runs on an
executor thread, and the turn's generator is not on that thread. This module is the one-way
channel between them: the tool calls `emit()`, and the engine drains what arrived while it
waits for the call to finish and forwards it as `tool_output_delta` events.

Why contextvars rather than a queue the tool is handed: the engine does not wrap each tool
call — `pipeline.dispatch` owns the `asyncio.to_thread` hop, deep under an `on_execute`
listener chain — and a context variable propagates INTO that hop automatically. So the
engine wraps the dispatch in `capture(...)` and the tool needs to know nothing at all: it
emits, and whether anyone is listening is someone else's business.

The sink is called from the worker thread, so it must be thread-safe; the engine's collector
is (a deque with a lock). Anything outside a capture is a no-op, which is what makes this
safe to call from a tool that is also run by a test, a subagent, or the TUI.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Callable, Iterator, Optional

OutputSink = Callable[[str, str], None]

_sink: ContextVar[Optional[OutputSink]] = ContextVar("tool_output_sink", default=None)
_call: ContextVar[Optional[str]] = ContextVar("tool_output_call", default=None)


@contextmanager
def capture(call_id: str, sink: OutputSink) -> Iterator[None]:
    """Route `emit()` from this call (and anything it spawns) to `sink(call_id, text)`."""
    sink_token = _sink.set(sink)
    call_token = _call.set(call_id)
    try:
        yield
    finally:
        _sink.reset(sink_token)
        _call.reset(call_token)


def emit(text: str) -> None:
    """Publish a chunk of output for the call in flight. No-op when nobody is listening."""
    sink = _sink.get()
    call_id = _call.get()
    if sink is None or call_id is None or not text:
        return
    sink(call_id, text)


def current_call() -> Optional[str]:
    """The id of the call being dispatched on this thread, or None outside a call.

    The engine installs it with `capture(...)` around every dispatch, which is how a tool
    that must address its OWN call learns the id: the `task` tool puts it on the parent's
    live `tool_metadata` frame while its child runs (step C), and the manager
    that receives the callback has no way to know it. A direct invocation (unit test, a
    REPL) has no capture, so this answers None rather than raising.
    """
    return _call.get()
