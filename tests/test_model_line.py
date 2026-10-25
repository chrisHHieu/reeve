"""The system prompt names the model it runs on, and a switch says so (context/model_line.py).

A model was told it was "Reeve's coding agent" and never which model, so it guessed when asked, and
after a switch it read the earlier replies as its own. The line names the model and the ones that
wrote earlier replies; a switch rewrites it and the next request's header carries the new prompt.
"""

from __future__ import annotations

import asyncio
import json

from harness.context.model_line import PREFIX, model_line, with_model_line
from harness.llm.providers import AssistantTurn, ModelCapabilities, ProviderClient, StreamChunk
from harness.loop import TurnEngine
from harness.safety.permissions import PermissionEngine
from harness.session.session_log import SessionLog
from harness.tools import ToolRegistry

# Two models the static matrix names, so the test reads no catalogue.
SOL = "gpt-5.6-sol"
TERRA = "gpt-5.6-terra"
PROMPT = f"You are Reeve's coding agent.\n\n{model_line(SOL)}\n\nNarration: say what you do."


class _Answers(ProviderClient):
    def __init__(self):
        self.systems: list[str] = []

    def complete(self, **kwargs):
        raise AssertionError("streamed")

    def stream(self, *, model, messages, tools=None, **settings):
        self.systems.append(next(m["content"] for m in messages if m.get("role") == "system"))
        yield StreamChunk(turn=AssistantTurn(text="ok", finish_reason="stop"))

    def capabilities(self, model):
        return ModelCapabilities()


def _engine(tmp_path, provider, messages=None):
    engine = TurnEngine(
        provider=provider,
        registry=ToolRegistry(),
        permissions=PermissionEngine(workspace_root=tmp_path),
        model=SOL,
        instructions=PROMPT,
        messages=messages,
    )
    engine.log = SessionLog(tmp_path / "events.jsonl", trace="t1")
    return engine


def _turn(engine, text):
    async def go():
        return [event async for event in engine.run(text)]

    return asyncio.run(go())


def test_the_line_names_the_model_by_its_picker_name_and_id():
    line = model_line(SOL)
    assert line.startswith(PREFIX)
    assert "GPT-5.6 Sol" in line and f"`{SOL}`" in line
    assert "Earlier in this conversation" not in line
    # The same model listed as earlier is not "another" model.
    assert "Earlier" not in model_line(SOL, [SOL])
    assert with_model_line("no line here", TERRA) == "no line here"


def test_the_composed_prompt_carries_a_line_a_switch_can_find(tmp_path):
    """`with_model_line` leaves a prompt with no model paragraph unchanged, so a composer that stopped
    writing the line as its own paragraph would make every switch a silent no-op. The tests above use
    a hand-written prompt; this one asks the composer the sessions actually run on."""
    from harness.compose.instructions import compose_instructions
    from harness.modes.agents.registry import get_agent
    from harness.tools.shell_env import ShellEnv

    prompt = compose_instructions(
        agent=get_agent(),
        model=SOL,
        # A person's rules may open a paragraph the same way; the agent's own line is the one replaced.
        user_rules=f"{PREFIX}a pirate.",
        remote=False,
        environment=None,
        remote_root="",
        ws=tmp_path,
        shell_env=ShellEnv(),
        has_shell=False,
    )
    assert f"\n\n{model_line(SOL)}\n\n" in prompt

    restated = with_model_line(prompt, TERRA, [SOL])
    assert restated == prompt.replace(model_line(SOL), model_line(TERRA, [SOL]), 1)
    assert f"{PREFIX}a pirate." in restated


def test_a_switch_names_the_new_model_and_the_one_that_wrote_the_replies_above(tmp_path):
    provider = _Answers()
    engine = _engine(tmp_path, provider)
    _turn(engine, "hello")
    assert "you are GPT-5.6 Sol" in provider.systems[-1]

    engine.switch_model(TERRA)
    _turn(engine, "which model are you?")

    sent = provider.systems[-1]
    assert "you are GPT-5.6 Terra" in sent
    assert "Earlier in this conversation the user ran it on GPT-5.6 Sol" in sent
    assert "Narration: say what you do." in sent  # only the model paragraph changed
    # The prompt the model read is in the log: a header for the opening, one for the switch.
    rows = [json.loads(line) for line in (tmp_path / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    headers = [row for row in rows if row["type"] == "request/header"]
    assert [h["reason"] for h in headers] == ["initial", "system"]
    assert headers[-1]["system"] == sent and headers[-1]["model"] == TERRA


def test_a_reloaded_transcript_still_knows_which_models_answered(tmp_path):
    """The models a conversation ran on are read back from its switch notices."""
    provider = _Answers()
    first = _engine(tmp_path, provider)
    _turn(first, "hello")
    first.switch_model(TERRA)
    saved = [message for message in first.messages if message.get("role") != "system"]

    reloaded = TurnEngine(
        provider=provider,
        registry=ToolRegistry(),
        permissions=PermissionEngine(workspace_root=tmp_path),
        model=TERRA,
        instructions=PROMPT.replace(model_line(SOL), model_line(TERRA)),
        messages=saved,
    )
    system = reloaded.messages[0]["content"]
    assert "you are GPT-5.6 Terra" in system and "ran it on GPT-5.6 Sol" in system
