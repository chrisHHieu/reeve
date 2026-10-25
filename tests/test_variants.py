"""Thinking levels (`providers/variants.py`).

The architectural bet: LEVELS are data (read from the provider's
own catalogue) and every provider-specific request body lives in one module, so the UI
only ever deals with ``["default", *levels]``. These tests pin the two rules that keep it
honest — the catalogue wins, and a model with no stated levels gets NONE rather than an
invented effort that would silently 400 on the wire.
"""

from __future__ import annotations

from harness.llm.providers import variants
from harness.llm.providers.catalog import _models_dev_entry
from harness.llm.providers.catalog import ModelCatalog, ModelsDevCatalog, set_catalog, set_models_dev
from harness.llm.providers.matrix import model_details


class _Fixed(ModelCatalog):
    def __init__(self, entries, path):
        super().__init__(path)
        self._entries = dict(entries)
        self._fetched_at = float("inf")
        self._tried = True


class _FixedFacts(ModelsDevCatalog):
    def __init__(self, entries, path):
        super().__init__(path)
        self._entries = dict(entries)
        self._fetched_at = float("inf")
        self._tried = True


import pytest


@pytest.fixture(autouse=True)
def _restore():
    yield
    set_catalog(None)
    set_models_dev(None)


# -- the catalogue projection -------------------------------------------------------


def test_reasoning_options_are_kept_and_reduced():
    entry = _models_dev_entry(
        "m",
        {
            "reasoning": True,
            "reasoning_options": [
                {"type": "effort", "values": ["low", "high", None]},
                {"type": "budget_tokens", "min": 100, "max": 900},
                {"type": "toggle"},
                {"type": "nonsense"},
            ],
        },
    )
    assert entry["reasoning_options"] == [
        {"type": "effort", "values": ["low", "high"]},
        {"type": "budget_tokens", "min": 100, "max": 900},
        {"type": "toggle"},
    ]


def test_a_malformed_option_is_dropped_not_defaulted():
    entry = _models_dev_entry("m", {"reasoning_options": [{"type": "effort"}, "junk"]})
    assert "reasoning_options" not in entry


# -- levels_for ---------------------------------------------------------------------


def test_effort_values_are_ordered_and_de_duped():
    facts = {"reasoning_options": [{"type": "effort", "values": ["high", "low", "high", "zzz"]}]}
    assert variants.levels_for("openai:x", facts) == ["low", "high", "zzz"]


def test_budget_shaped_models_get_named_levels_per_provider():
    budget = {"reasoning_options": [{"type": "budget_tokens", "min": 1, "max": 2}]}
    assert variants.levels_for("anthropic:claude-sonnet-4-5", budget) == [
        "low",
        "medium",
        "high",
        "max",
    ]
    assert variants.levels_for("gemini:gemini-2.5-pro", budget) == [
        "none",
        "low",
        "medium",
        "high",
    ]


def test_curated_fallback_only_when_the_model_reasons():
    assert variants.levels_for("openai:gpt-5.5", {"reasoning": True}) == [
        "none",
        "low",
        "medium",
        "high",
    ]
    assert variants.levels_for("openai:gpt-5.5", {"reasoning": False}) == []
    assert variants.levels_for("openai:gpt-5.5", {}) == []


def test_a_model_with_no_statement_gets_no_levels():
    """The honesty rule: absent a catalogue statement and a curated row, offer nothing."""
    assert variants.levels_for("bedrock:whatever", {"reasoning": True}) == []


def test_toggle_offers_no_levels_today():
    facts = {"reasoning_options": [{"type": "toggle"}]}
    assert variants.levels_for("x:y", facts) == []


# -- patch_for ----------------------------------------------------------------------


def test_effort_patch_is_the_canonical_knob():
    assert variants.patch_for("openai:gpt-5.5", "high") == {"reasoning_effort": "high"}
    assert variants.patch_for("gemini:gemini-2.5-pro", "low") == {"reasoning_effort": "low"}
    # The GLM wire takes the same bare knob, on Z.AI and on Zhipu's Coding Plan alike.
    assert variants.patch_for("zhipuai-coding-plan:glm-5.3", "high") == {
        "reasoning_effort": "high"
    }
    assert variants.patch_for("zai-coding-plan:glm-5.3", "high") == {
        "reasoning_effort": "high"
    }


def test_anthropic_patch_is_a_budget_only_on_budget_models():
    assert variants.patch_for("anthropic:claude-sonnet-4-5", "high") == {
        "thinking": {"type": "enabled", "budget_tokens": 16384}
    }
    # Adaptive-only families (4.6+/Claude 5) reject budget_tokens; no patch.
    assert variants.patch_for("anthropic:claude-fable-5", "high") == {}


def test_default_and_unknown_levels_contribute_nothing():
    assert variants.patch_for("openai:gpt-5.5", None) == {}
    assert variants.patch_for("openai:gpt-5.5", "default") == {}
    assert variants.patch_for("bedrock:x", "high") == {}


# -- surfaced to the picker ---------------------------------------------------------


def test_model_details_carries_the_levels(tmp_path):
    mid = "openai:gpt-5.5"
    set_models_dev(
        _FixedFacts(
            {mid: {"reasoning": True, "reasoning_options": [{"type": "effort", "values": ["low", "high"]}]}},
            tmp_path / "facts.json",
        )
    )
    set_catalog(_Fixed({}, tmp_path / "catalog.json"))
    detail = model_details(mid)
    assert detail["reasoning_levels"] == ["low", "high"]
    assert detail["reasoning_default"] is None


# -- the request merge --------------------------------------------------------------


class _Stub:
    def complete(self, **kwargs):  # pragma: no cover
        raise NotImplementedError

    def capabilities(self, model):
        from harness.llm.providers import ModelCapabilities

        return ModelCapabilities()


def _engine(tmp_path, **kwargs):
    from harness.compose import build_engine
    from harness.modes.agents import reeve_agent

    return build_engine(agent=reeve_agent(), workspace=tmp_path, provider=_Stub(), **kwargs)


def test_build_engine_merges_the_level_patch(tmp_path):
    engine = _engine(tmp_path, model="gpt-5.5", thinking_level="high")
    try:
        assert engine.model_settings["reasoning_effort"] == "high"
    finally:
        engine.executor.close()


def test_an_explicit_setting_still_wins_over_the_level(tmp_path):
    engine = _engine(
        tmp_path,
        model="gpt-5.5",
        thinking_level="high",
        model_settings={"reasoning_effort": "low"},
    )
    try:
        assert engine.model_settings["reasoning_effort"] == "low"
    finally:
        engine.executor.close()


def test_no_level_contributes_nothing(tmp_path):
    engine = _engine(tmp_path, model="gpt-5.5")
    try:
        assert "reasoning_effort" not in engine.model_settings
    finally:
        engine.executor.close()


def test_switch_thinking_recomputes_and_clears_the_patch(tmp_path):
    engine = _engine(tmp_path, model="gpt-5.5")
    try:
        engine.messages.append({"role": "user", "content": "hi"})
        notice = engine.switch_thinking("high")
        assert notice and engine.model_settings["reasoning_effort"] == "high"
        assert engine.switch_thinking("high") is None  # idempotent
        engine.switch_thinking(None)
        assert "reasoning_effort" not in engine.model_settings
    finally:
        engine.executor.close()


def test_switching_model_reapplies_or_drops_the_level(tmp_path):
    engine = _engine(tmp_path, model="anthropic:claude-sonnet-4-5", thinking_level="high")
    try:
        assert engine.model_settings["thinking"]["type"] == "enabled"
        engine.switch_model("bedrock:some-model")  # no thinking body here
        assert engine.thinking_level is None
        assert "thinking" not in engine.model_settings
    finally:
        engine.executor.close()


def test_switching_model_drops_a_level_the_new_model_does_not_list(tmp_path):
    """Owner-hit 2026-10-24: "medium" on a model offering three levels was carried onto one
    offering low and high, and still sent. A level the new model lists is kept."""
    three, two = "openai:three-levels", "openai:two-levels"
    set_models_dev(
        _FixedFacts(
            {
                three: {"reasoning_options": [{"type": "effort", "values": ["low", "medium", "high"]}]},
                two: {"reasoning_options": [{"type": "effort", "values": ["low", "high"]}]},
            },
            tmp_path / "facts.json",
        )
    )
    engine = _engine(tmp_path, model=three, thinking_level="medium")
    try:
        engine.switch_model(two)
        assert engine.thinking_level is None
        assert "reasoning_effort" not in engine.model_settings
        engine.switch_model(three)
        engine.switch_thinking("high")
        engine.switch_model(two)
        assert engine.thinking_level == "high"
        assert engine.model_settings["reasoning_effort"] == "high"
    finally:
        engine.executor.close()


def test_thinking_level_survives_a_conversation_round_trip(tmp_path):
    from harness.session.conversations import ConversationStore
    from harness.session.sessions import SessionRecord

    store = ConversationStore(tmp_path)
    store.save(
        SessionRecord(
            session_id="s1", workspace=str(tmp_path), model="gpt-5.5", mode="interactive",
            thinking="high",
        )
    )
    assert store.load("s1").thinking == "high"


# -- provider consumption -----------------------------------------------------------


def test_responses_folds_the_effort_into_reasoning():
    from harness.llm.providers.responses import OpenAIResponsesProvider

    kwargs = OpenAIResponsesProvider(client=object())._request_kwargs(
        model="gpt-5.6-sol",
        messages=[{"role": "user", "content": "hi"}],
        tools=None,
        settings={"reasoning_effort": "high"},
    )
    assert kwargs["reasoning"] == {"summary": "auto", "effort": "high"}
    assert "reasoning_effort" not in kwargs


def test_gemini_maps_a_level_to_a_thinking_budget():
    from harness.llm.providers.gemini import GeminiProvider

    provider = GeminiProvider(client=object())
    high = provider._request_kwargs(
        model="gemini-2.5-pro",
        messages=[{"role": "user", "content": "hi"}],
        tools=None,
        settings={"reasoning_effort": "high"},
    )
    assert high["config"]["thinking_config"]["thinking_budget"] == 24576
    none = provider._request_kwargs(
        model="gemini-2.5-pro",
        messages=[{"role": "user", "content": "hi"}],
        tools=None,
        settings={"reasoning_effort": "none"},
    )
    assert none["config"]["thinking_config"] == {
        "include_thoughts": False,
        "thinking_budget": 0,
    }
