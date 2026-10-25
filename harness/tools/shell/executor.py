"""The boundary between the engine and wherever a command actually runs."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Optional

from ..shell_kinds import Shell

_NO_BACKGROUND = {"error": "background execution is not supported by this executor"}


class Executor(ABC):
    """Where `run_shell` commands go. `LocalExecutor` is this machine. The interface exists so a
    container or a VM can stand in later without the engine changing."""

    @abstractmethod
    def run(self, command: str, timeout: Optional[float] = None) -> dict[str, Any]: ...

    def shells(self) -> list[Shell]:
        """The dialects a command may be written in. Empty means this world has exactly
        one and the model is not asked to choose, so `run` and `run_background` never receive a
        `shell`. A world that offers several accepts `shell=<kind>` on both."""
        return []

    def run_background(self, command: str) -> dict[str, Any]:
        return dict(_NO_BACKGROUND)

    def background_output(self, task_id: str, wait: float = 0) -> dict[str, Any]:
        return dict(_NO_BACKGROUND)

    def background_kill(self, task_id: str) -> dict[str, Any]:
        return dict(_NO_BACKGROUND)

    # The listeners below default to doing nothing, because a world that runs no background tasks
    # has nothing to report to them.

    def set_background_listener(self, listener: Any) -> None:
        """Where a finished background task reports itself."""

    def set_background_output_listener(self, listener: Any) -> None:
        """Where a running background task's output goes as it is read. A jobs panel follows this
        and does not poll for the tail."""

    def set_background_roster_listener(self, listener: Any) -> None:
        """Called when the set of background tasks, or one task's state, changes."""

    def set_generation_provider(self, provider: Any) -> None:
        """A callable that answers "what is the owner's revert generation right now?".

        The value is stamped on each new background task and travels with its completion, so a task
        started before an `/undo` can be recognised as belonging to the span the person just hid.
        Subagent children are fenced the same way."""

    def set_person_waiting(self, event: Any) -> None:
        """The engine's `person_waiting` event: set while a message the person typed waits for the
        step to end. A world whose waits read it gives the command a short grace and then hands it
        to the background. One that does not keeps waiting, as before."""

    def wake_waiters(self) -> None:  # pragma: no cover - default no-op
        """Wake every wait in flight, so it reads `person_waiting` now and not at its next line."""

    def interrupt(self) -> None:  # pragma: no cover - default no-op
        pass

    def close(self) -> None:  # pragma: no cover - default no-op
        pass
