"""A thinking level picked for a Vertex model reaches the request (variants.py).

Found from "I don't see Gemini thinking on Vertex": `patch_for` knew the providers `anthropic` and
`gemini` and nothing else, so a level chosen for `vertex:gemini/...` or `vertex:claude/...` contributed
`{}` and was never sent. Once the catalogue lookup was fixed (catalog_ids) the picker offered the levels,
which made the control look real while doing nothing. What has to hold:

  - a Vertex Gemini level becomes the same thinking budget a direct Gemini model gets, whichever way the id
    is spelled (`gemini/` segment or none), `none` turns thought summaries off and `minimal` is a budget;
  - a Vertex Claude level is the Anthropic budget on the models that take one, and nothing on the ones that do not;
  - an open-weight model on Vertex has no Gemini or Anthropic body sprayed at it;
  - a direct Gemini model is unchanged.
"""

from __future__ import annotations

import pytest

from harness.llm.providers.gemini import GeminiProvider
from harness.llm.providers.variants import levels_for, patch_for, wire_provider
from harness.llm.providers.vertex import VertexProvider

MESSAGES = [{"role": "user", "content": "hi"}]


def _config_sent(model: str, level: str) -> dict:
    """What the Gemini wire would carry for a routed Vertex model and a chosen level."""
    vertex = VertexProvider(gemini_client=GeminiProvider(client=object()))
    family, rest = vertex._split(model.removeprefix("vertex:"))
    assert family == "gemini"
    sub = vertex._family_client(family)
    return sub._request_kwargs(model=rest, messages=MESSAGES, tools=None, settings=dict(patch_for(model, level)))["config"]


@pytest.mark.parametrize("model", ["vertex:gemini/gemini-3.7-flash", "vertex:gemini-3.7-flash"])
def test_a_vertex_gemini_level_becomes_a_thinking_budget(model):
    assert patch_for(model, "high") == {"reasoning_effort": "high"}
    assert _config_sent(model, "high")["thinking_config"] == {"include_thoughts": True, "thinking_budget": 24576}
    assert _config_sent(model, "low")["thinking_config"]["thinking_budget"] == 1024
    assert _config_sent(model, "minimal")["thinking_config"] == {"include_thoughts": True, "thinking_budget": 512}


def test_none_turns_the_summaries_off_and_default_leaves_the_model_to_decide():
    assert _config_sent("vertex:gemini/gemini-3.7-flash", "none")["thinking_config"] == {
        "include_thoughts": False,
        "thinking_budget": 0,
    }
    assert patch_for("vertex:gemini/gemini-3.7-flash", "default") == {}
    assert _config_sent("vertex:gemini/gemini-3.7-flash", "default")["thinking_config"] == {"include_thoughts": True}


def test_a_direct_gemini_model_is_unchanged():
    assert patch_for("gemini:gemini-2.5-pro", "high") == {"reasoning_effort": "high"}
    assert wire_provider("gemini:gemini-2.5-pro") == "gemini"


def test_a_vertex_claude_level_is_the_anthropic_budget_where_the_model_takes_one():
    assert patch_for("vertex:claude/claude-sonnet-4-5", "high") == {
        "thinking": {"type": "enabled", "budget_tokens": 16384}
    }
    assert patch_for("vertex:claude-sonnet-4-5@20250929", "low") == {
        "thinking": {"type": "enabled", "budget_tokens": 2048}
    }
    # 4.6+ takes adaptive thinking, not a budget: no level is sent for it, as on the direct Anthropic route.
    assert patch_for("vertex:claude/claude-sonnet-4-6", "high") == {}


def test_an_open_weight_model_on_vertex_gets_no_gemini_or_anthropic_body():
    model = "vertex:openweight/meta/llama-4-maverick-17b-128e-instruct-maas"
    assert wire_provider(model) == "vertex"
    assert patch_for(model, "high") == {}
    assert levels_for(model, {"reasoning": True}) == []


def test_the_fallback_levels_follow_the_family_when_the_catalogue_is_silent():
    assert levels_for("vertex:gemini/gemini-3.7-flash", {"reasoning": True}) == ["none", "low", "medium", "high"]
    assert levels_for("vertex:gemini/gemini-3.7-flash", {}) == []  # a model that does not reason has none
    assert levels_for("vertex:claude/claude-sonnet-4-5", {"reasoning": True}) == ["low", "medium", "high", "max"]
    assert levels_for("vertex:claude/claude-sonnet-4-6", {"reasoning": True}) == []


def test_every_level_the_picker_offers_a_vertex_gemini_does_something():
    offered = levels_for(
        "vertex:gemini/gemini-3.6-flash",
        {"reasoning_options": [{"type": "effort", "values": ["minimal", "low", "medium", "high"]}]},
    )
    assert offered == ["minimal", "low", "medium", "high"]
    for level in offered:
        assert patch_for("vertex:gemini/gemini-3.6-flash", level) == {"reasoning_effort": level}, level
