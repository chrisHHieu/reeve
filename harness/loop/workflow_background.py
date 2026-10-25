"""A `workflow` call that runs in the background.

The call is authorized HERE, in the parent's turn, exactly as a foreground run is — the
guards, the mode and any ask are the parent's — and then handed to the host, which runs
it as a child session (`server/manager/workflow_runs.py`). The call answers at once with
the running `<task>` envelope; the script's return value is injected under the same id
when it ends. Split from `authorize.py` (size ceiling).
"""

from __future__ import annotations

from typing import Any, AsyncIterator, Callable

from harness.session.events import Event, EventType


def wants_background(engine: Any, arguments: dict[str, Any]) -> bool:
    """Whether this call runs in the background: the host offers it, and the call did not
    ask to wait (`background` defaults to true)."""
    return (
        getattr(engine, "workflow_background", None) is not None
        and arguments.get("background", True) is not False
    )


async def run_in_background(engine: Any, tool_call: Any) -> AsyncIterator[Event]:
    allowed = False
    async for item in engine._authorize(tool_call):
        if isinstance(item, Event):
            yield item
        else:
            allowed = item
    if not allowed:
        return
    yield Event(EventType.TOOL_STARTED, {"name": tool_call.name, "call_id": tool_call.id})
    engine._log_call(tool_call)
    engine._audit(tool_call, stage="started")
    try:
        result = await engine.workflow_background(
            tool_call.id, dict(tool_call.arguments or {}), _row_tap(engine, tool_call.id)
        )
    except Exception as exc:  # a host failure is the call's answer, not a dead turn
        result = {"error": f"the workflow could not start in the background: {exc}"}
    if not isinstance(result, dict):
        result = {"error": "the workflow could not start in the background"}
    yield engine._record_result(tool_call, result, "error" if result.get("error") else "ok")


def _row_tap(engine: Any, call_id: str) -> Callable[[dict], None] | None:
    """The parent row's live frames for the run, the shape the `task` tool sends."""
    emit = getattr(engine, "subagent_metadata", None)
    if emit is None:
        return None

    def tap(metadata: dict) -> None:
        try:
            emit({"type": "tool_metadata", "data": {"call_id": call_id, "metadata": metadata}})
        except Exception:
            pass

    return tap


__all__ = ["run_in_background", "wants_background"]
