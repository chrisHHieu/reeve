"""The edit tools follow the model.

`apply_patch`'s Codex envelope is a format GPT/Codex models are trained on. On
deepseek-v4.1-flash it failed 13.2% of real calls against 1.9% for `replace_in_file`, so a
model outside that family is not offered it — and a mid-session switch moves the tool with
the model, both ways.
"""

from __future__ import annotations

from harness.compose import build_engine
from harness.modes.agents.reeve import reeve_agent
from harness.tools.edit_surface import patch_native
from harness.llm.providers import AssistantTurn, ModelCapabilities, ProviderClient


class Provider(ProviderClient):
    def complete(self, *, model, messages, tools=None, **settings):
        return AssistantTurn(text="ok", finish_reason="stop")

    def capabilities(self, model):
        return ModelCapabilities()


def _engine(tmp_path, model: str):
    return build_engine(
        agent=reeve_agent(),
        workspace=tmp_path,
        provider=Provider(),
        model=model,
        session_id="edit-surface",
    )


def test_patch_native_is_the_gpt_and_o_series_family_on_any_route():
    for model in ("gpt-5.6-sol", "openai-codex:gpt-5.5", "openrouter:openai/gpt-6-luna-pro",
                  "opencode-go:gpt-6-luna", "openai:o4-mini", "openai:codex-mini"):
        assert patch_native(model), model
    for model in ("opencode-go:deepseek-v4.1-flash", "zai-coding-plan:glm-5.3",
                  "vertex:gemini/gemini-3.7-flash", "anthropic:claude-opus-4.8",
                  "opencode-go:qwen3.8-flash", "openrouter:inclusionai/ling-3.0-flash", ""):
        assert not patch_native(model), model


def test_a_model_outside_the_family_gets_replace_in_file_and_not_apply_patch(tmp_path):
    engine = _engine(tmp_path, "opencode-go:deepseek-v4.1-flash")
    names = set(engine.registry.names())
    assert "apply_patch" not in names
    assert {"replace_in_file", "write_file"} <= names
    assert "apply_patch" not in str(engine.registry.schemas())


def test_a_gpt_model_keeps_apply_patch(tmp_path):
    assert "apply_patch" in _engine(tmp_path, "openai-codex:gpt-5.5").registry.names()


def test_a_mid_session_switch_moves_the_tool_with_the_model(tmp_path):
    engine = _engine(tmp_path, "opencode-go:deepseek-v4.1-flash")
    engine.switch_model("openai-codex:gpt-5.5")
    restored = engine.registry.get("apply_patch")
    assert restored is not None and "*** Begin Patch" in str(restored.schema)
    engine.switch_model("zai-coding-plan:glm-5.3")
    assert "apply_patch" not in engine.registry.names()
    engine.switch_model("gpt-5.6-sol")
    assert "apply_patch" in engine.registry.names()
