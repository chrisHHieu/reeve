"""Plugins in the TUI (design §11.3): `/plugins` lists what loads and toggles the
person's own choice, `/reload-plugins` rebuilds the engine with the same conversation, and
a command a plugin or a person defined runs as an ordinary message. Scripted provider."""

from __future__ import annotations

from pathlib import Path

import pytest

from harness.extensions.commands import expand
from harness.extensions.plugins import store
from harness.llm.providers import AssistantTurn, ModelCapabilities, ProviderClient
from harness.interface.tui.app import ReeveApp

RELEASE = Path(__file__).parent / "fixtures" / "plugins" / "release-helper"


class _Recording(ProviderClient):
    def __init__(self, *texts: str):
        self.texts = list(texts)
        self.seen: list[list[dict]] = []

    def complete(self, *, model, messages, tools=None, **settings):
        self.seen.append(messages)
        return AssistantTurn(text=self.texts.pop(0), finish_reason="stop")

    def capabilities(self, model):
        return ModelCapabilities()


async def _submit(pilot, text):
    app = pilot.app
    app.query_one("#prompt").value = text
    await pilot.press("enter")
    await app.workers.wait_for_complete()
    await pilot.pause()


@pytest.mark.parametrize(
    "template, args, expected",
    [
        ("Deploy $1 to $2", "api prod eu", "Deploy api to prod eu"),
        ('Tag $1 as $2', '"big fix" v2', "Tag big fix as v2"),
        ("Say hi to $ARGUMENTS", "Ann and Bo", "Say hi to Ann and Bo"),
        ("Summarize the diff.", "focus on tests", "Summarize the diff.\n\nfocus on tests"),
        ("Use $1 and $3", "a", "Use a and"),
    ],
)
def test_expand_follows_the_gui_s_rules(template, args, expected):
    assert expand(template, args) == expected


async def test_plugins_lists_and_toggles_and_a_plugin_command_runs_as_a_message(tmp_path, monkeypatch):
    monkeypatch.setenv("REEVE_PLUGIN_DIRS", str(RELEASE))
    commands = tmp_path / ".reeve" / "commands"
    commands.mkdir(parents=True)
    (commands / "ship.md").write_text("---\ndescription: Ship it\n---\nShip version $1 carefully.\n")
    provider = _Recording("shipping", "still here")
    app = ReeveApp(workspace=tmp_path, provider=provider)
    async with app.run_test() as pilot:
        await _submit(pilot, "/plugins")
        assert any("release-helper@inline" in line and "session" in line for line in app.rendered)

        await _submit(pilot, "/plugins disable other@acme")
        assert store.effective_enabled("other@acme") == (False, "user")

        await _submit(pilot, "/ship 1.2")
        sent = provider.seen[-1][-1]
        assert sent["role"] == "user" and sent["content"].startswith("Ship version 1.2 carefully.")
        # The transcript keeps what the person typed.
        assert app.engine.messages[-2].get("_display") == "/ship 1.2"

        before = app.engine
        await _submit(pilot, "/reload-plugins")
        assert app.engine is not before
        assert [m["content"] for m in app.engine.messages if m.get("role") == "user"] == ["Ship version 1.2 carefully."]

        await _submit(pilot, "/nothing-here")
        assert any("unknown command" in line for line in app.rendered)
