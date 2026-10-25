"""How a wake reaches a session: the scheduler runs one more step on each of its ticks, a wake comes
in as a message that says what woke it, and the tools that set wakes are given to an agent that
schedules and not to one that does not.
"""

from __future__ import annotations

import asyncio

import pytest

from harness.compose import build_engine
from harness.integrations.automation.scheduler import Scheduler
from harness.interface.server.manager import SessionManager
from harness.loop.selfwake import Wake, WakeStore
from harness.modes.agents.chat import chat_agent
from harness.modes.agents.reeve import reeve_agent


class _NothingDue:
    def due(self):
        return []


def test_each_scheduler_tick_also_runs_the_extra_step():
    ticks: list[str] = []

    async def extra():
        ticks.append("woke")

    asyncio.run(Scheduler(_NothingDue(), runner=None, extra_tick=extra)._tick(trigger="schedule"))
    assert ticks == ["woke"]


@pytest.mark.parametrize(
    ("wake", "said"),
    [
        (Wake("1", "s1", "timer", note="poll"), ("timer", "poll")),
        (Wake("2", "s1", "completion", job_id="job-9"), ("job-9",)),
        (Wake("3", "s1", "event", event_key="pr-opened"), ("pr-opened",)),
    ],
)
def test_the_wake_message_says_what_woke_the_session(wake, said):
    message = SessionManager._wake_message(wake)
    assert all(words in message for words in said)


def _tool_names(agent, tmp_path):
    return set(build_engine(agent=agent, workspace=tmp_path, wake_store=WakeStore(tmp_path / "wakes.json"), session_id="s1").registry.names())


def test_an_agent_that_schedules_is_given_the_wake_tools(tmp_path):
    assert {"sleep_until", "wake_on", "wake_on_event"} <= _tool_names(reeve_agent(), tmp_path)


def test_an_agent_without_scheduling_is_not(tmp_path):
    # Chat is the one left that never took up running on its own (Code, which was, joined the one agent 2026-09-20)
    assert "sleep_until" not in _tool_names(chat_agent(), tmp_path)
