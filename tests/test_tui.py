"""The Textual TUI, driven by its test pilot with a scripted model: it starts, shows a turn, and a
write waits for the key that approves it.
"""

from __future__ import annotations

from harness.interface.tui.app import ReeveApp
from harness.llm.providers import AssistantTurn, ModelCapabilities, ProviderClient, ToolCall


class _Scripted(ProviderClient):
    def __init__(self, *turns):
        self._turns = list(turns)

    def complete(self, *, model, messages, tools=None, **settings):
        return self._turns.pop(0)

    def capabilities(self, model):
        return ModelCapabilities()


def _says(text):
    return AssistantTurn(text=text, finish_reason="stop")


def _shown(app, text):
    return any(text in line for line in app.rendered)


async def _type(pilot, text):
    pilot.app.query_one("#prompt").value = text
    await pilot.press("enter")


async def test_the_tui_starts_and_shows_the_answer(tmp_path):
    app = ReeveApp(workspace=tmp_path, provider=_Scripted(_says("hi, I am the agent")))
    async with app.run_test() as pilot:
        await _type(pilot, "hello")
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert _shown(app, "hi, I am the agent")


async def test_a_write_runs_once_y_is_pressed(tmp_path):
    write = AssistantTurn(tool_calls=[ToolCall(id="call_1", name="write_file", arguments={"path": "made.py", "content": "print(1)\n"})], finish_reason="tool_calls")
    app = ReeveApp(workspace=tmp_path, provider=_Scripted(write, _says("done, wrote made.py")))
    async with app.run_test() as pilot:
        await _type(pilot, "create made.py")
        await pilot.pause()
        await pilot.press("y")
        await app.workers.wait_for_complete()
        await pilot.pause()
    assert (tmp_path / "made.py").read_text() == "print(1)\n"
    assert _shown(app, "done, wrote made.py")
