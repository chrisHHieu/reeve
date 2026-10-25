"""What the log shows for each engine event: pure functions from an event to its lines."""

from __future__ import annotations

from typing import Callable, Optional

from harness.session.events import Event, EventType

from .approval import short


def _assistant(data: dict, shown: Optional[dict] = None) -> list[str]:
    from harness.tools.visual import plain_visual_markers

    # A visual cannot be drawn here: its placing line becomes the visual written as text.
    return [f"[b green]assistant[/b green]\n{plain_visual_markers(data['text'], shown)}"] if data.get("text") else []


def remember_visual(data: dict, shown: dict) -> None:
    """Keep a finished `show_visual` call's card by its name, for the answer that places it."""
    presentation = data.get("presentation")
    card = presentation.get("card") if isinstance(presentation, dict) else None
    if isinstance(card, dict) and card.get("card") == "visual" and isinstance(card.get("id"), str):
        shown[card["id"]] = card


def _proposed(data: dict) -> list[str]:
    return [f"[yellow]→ {data['name']}[/yellow] {short(data.get('arguments'), 100)}"]


def _finished(data: dict) -> list[str]:
    status = data.get("status")
    colour = "green" if status == "ok" else "red"
    detail = data.get("result_preview") or data.get("reason") or ""
    return [f"  [{colour}]✓ {data['name']} · {status}[/{colour}] {short(detail, 100)}"]


def _retrying(data: dict) -> list[str]:
    return [
        "[yellow]↻ connection dropped mid-answer — retrying "
        f"({data.get('attempt')}/{data.get('of')})[/yellow]"
    ]


def _spend_stop(data: dict) -> str:
    # The spend rail, named with its number: the checkpoint above it holds what remains.
    return (
        f"[red]⚠ stopped after {int(data.get('tokens') or 0):,} tokens "
        "this turn — the handoff above says what remains[/red]"
    )


def _step_stop(data: dict) -> str:
    # Name what it was stuck on, not the counter that stopped it.
    stuck = data.get("repeated")
    if stuck:
        return (
            f"[red]⚠ stopped after {data.get('iterations')} steps — "
            f"kept retrying the same thing ({stuck['summary']}) "
            f"{stuck['count']}x with the same result[/red]"
        )
    return f"[red]⚠ stopped after {data.get('iterations')} steps without finishing[/red]"


def _check_stop(data: dict) -> str:
    # The check kept failing with no change between runs: name the check, not the counter.
    loop = data.get("check_loop") or {}
    return (
        f"[red]⚠ stopped: {loop.get('command') or 'a check'} kept failing "
        f"{loop.get('count', '?')} times with no change[/red]"
    )


_TURN_STOPS: dict[str, Callable[[dict], str]] = {
    "token_budget_exceeded": _spend_stop,
    "max_iterations_exceeded": _step_stop,
    "no_progress_stopped": _check_stop,
}


def _turn_end(data: dict) -> list[str]:
    describe: Optional[Callable[[dict], str]] = _TURN_STOPS.get(data.get("status"))
    return [describe(data)] if describe else []


_RENDERERS: dict[EventType, Callable[[dict], list[str]]] = {
    EventType.ASSISTANT_MESSAGE: _assistant,
    EventType.TOOL_PROPOSED: _proposed,
    EventType.TOOL_FINISHED: _finished,
    EventType.INTERRUPTED: lambda data: ["[red]⏹ interrupted[/red]"],
    EventType.RETRYING: _retrying,
    EventType.ERROR: lambda data: [f"[red]error: {data.get('error')}[/red]"],
    EventType.TURN_END: _turn_end,
}


def lines_for(event: Event, shown: Optional[dict] = None) -> list[str]:
    """The log lines `event` produces; empty for an event the log does not show. `shown` holds
    the visuals the session has shown so far, by name, so an answer can write the ones it places."""
    if shown is not None and event.type == EventType.TOOL_FINISHED:
        remember_visual(event.data, shown)
    if event.type == EventType.ASSISTANT_MESSAGE:
        return _assistant(event.data, shown)
    render = _RENDERERS.get(event.type)
    return render(event.data) if render else []
