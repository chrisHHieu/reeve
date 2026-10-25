"""`request_tool`: when a check needs a command-line tool that is not installed, the agent
asks for it instead of leaving the check out. The engine takes the call itself, as it does
`ask_user`, and it never passes the permission path: the person's answer on the card is the consent.
"""

from __future__ import annotations

import pytest

from harness.llm.providers import AssistantTurn, ModelCapabilities, ProviderClient, ToolCall
from harness.loop import TurnEngine
from harness.safety.permissions import Mode, PermissionEngine
from harness.session.events import EventType
from harness.tools import ToolRegistry, toolchain


@pytest.fixture(autouse=True)
def _a_platform_the_catalog_builds_for(monkeypatch):
    # These scanners are pinned for darwin and linux only; on Windows `describe()` is None and the
    # card under test could not exist. The tests are about the flow, not about which builds ship.
    monkeypatch.setattr(toolchain.paths, "platform_key", lambda: "darwin_arm64")


class _AsksFor(ProviderClient):
    """Calls `request_tool` for one tool, then answers."""

    def __init__(self, tool: str):
        self.tool, self.calls = tool, 0

    def complete(self, *, model, messages, tools=None, **settings):
        self.calls += 1
        if self.calls > 1:
            return AssistantTurn(text="done", tool_calls=[])
        return AssistantTurn(text="", tool_calls=[ToolCall(id="t1", name="request_tool", arguments={"name": self.tool, "reason": "scan history for secrets"})])

    def capabilities(self, model):
        return ModelCapabilities(tools=True)


def _engine(tmp_path, requester, tool: str = "gitleaks"):
    return TurnEngine(provider=_AsksFor(tool), registry=ToolRegistry(), permissions=PermissionEngine(workspace_root=tmp_path, mode=Mode.WORKSPACE_WRITE), model="m", tool_requester=requester)


async def _run(engine):
    return [event async for event in engine.run("check this repo")]


def _requested(events):
    return [event for event in events if event.type is EventType.TOOL_REQUESTED]


def _status(events):
    return next(event for event in events if event.type is EventType.TOOL_FINISHED).data["status"]


def _result(engine) -> str:
    return str([m for m in engine.messages if m.get("role") == "tool"][-1]["content"])


def _answers(answer, seen=None):
    async def requester(args, tool_call_id=None):
        if seen is not None:
            seen.append(args)
        return answer

    return requester


async def test_the_card_is_shown_and_an_install_is_reported(tmp_path):
    seen: list = []
    events = await _run(_engine(tmp_path, _answers({"installed": True, "path": "/tmp/gitleaks", "version": "8.30.1"}, seen)))
    assert seen[0]["name"] == "gitleaks"
    assert _requested(events)[0].data["name"] == "gitleaks" and _status(events) == "ok"


async def test_the_card_says_what_the_catalog_says_about_the_tool(tmp_path):
    """Owner-hit 2026-08-14: the card offered Install for a tool with no pinned build, guessing
    because the event said nothing. For a catalog tool the event carries the registry's word."""
    said = _requested(await _run(_engine(tmp_path, _answers({"installed": False, "reason": "declined"}))))[0].data
    assert said["installable"] is True and said["version"] == toolchain.MANAGED["gitleaks"].version
    assert said["summary"] and said["source"] == "github.com/gitleaks"


async def test_a_declined_install_tells_the_agent_to_say_what_it_left_out(tmp_path, monkeypatch):
    """A refusal must not read as "check done": the result pushes the agent to a fallback it
    discloses, which is what the tool is for."""
    monkeypatch.setattr(toolchain, "resolve", lambda name: None)  # or the machine's own gitleaks is found
    engine = _engine(tmp_path, _answers({"installed": False, "reason": "the user declined to install it"}))
    events = await _run(engine)
    assert _requested(events) and _status(events) == "denied"
    said = _result(engine).lower()
    assert "degraded" in said or "fallback" in said


async def test_a_copy_installed_by_the_person_while_the_card_was_up_is_handed_over(tmp_path, monkeypatch):
    """The card says "or install it yourself and continue", and that has to hold: a person who
    installs it while the card is up and presses Continue has provided the tool, and the agent gets
    the path of their copy, not a refusal."""
    monkeypatch.setattr(toolchain, "resolve", lambda name: "/opt/homebrew/bin/gitleaks")
    engine = _engine(tmp_path, _answers({"installed": False, "reason": "the user declined to install it"}))
    assert _status(await _run(engine)) == "ok"
    said = _result(engine)
    assert "/opt/homebrew/bin/gitleaks" in said and "own copy" in said  # theirs, not a managed install


async def test_a_tool_outside_the_catalog_gets_no_card_and_is_sent_to_the_shell(tmp_path):
    """Owner-hit 2026-08-20: agents sent ordinary brew and pip installs through the card, which
    could only fail after the person approved. A name outside the catalog asks nobody."""
    asked: list = []
    engine = _engine(tmp_path, _answers({"installed": False, "reason": "declined"}, asked), tool="semgrep")
    assert not _requested(await _run(engine)) and asked == []
    said = _result(engine)
    assert "not in the pinned tool catalog" in said and "shell" in said and "gitleaks" in said  # names what is in it


async def test_with_nobody_to_ask_the_agent_is_still_told_to_disclose(tmp_path):
    engine = _engine(tmp_path, None)  # a surface with no one at it
    assert not _requested(await _run(engine))
    assert "degraded" in _result(engine).lower()


async def test_full_access_installs_the_pinned_build_without_a_card(tmp_path, monkeypatch):
    """Full access asks nobody; the pinned build is installed at once."""
    installed: list = []
    monkeypatch.setattr(toolchain, "install", lambda name, **kw: installed.append(name) or f"/opt/{name}")

    async def no_card(args, tool_call_id=None):
        raise AssertionError("full access must not show the install card")

    engine = _engine(tmp_path, no_card)
    engine.permissions.mode = Mode.DANGER_FULL_ACCESS
    events = await _run(engine)
    assert installed == ["gitleaks"] and not _requested(events) and _status(events) == "ok"
