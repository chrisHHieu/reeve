"""The "auto" working window is the model's own, up to an accuracy ceiling.

Left to the catalogue, a 1M-window model compacted at ~980k. The 2026-09-24 audit
bucketed 4,700 real steps by prompt size: the tool-error rate held at 3.2-5.4% up to 400k
and doubled past it (7.1%), led by edits refused as written against files that had
changed since the model read them. `"auto"` caps the working window at
`AUTO_ACCURACY_CEILING` (400k) for every model, whatever it costs; an
explicit number (the user's own setting) is still obeyed exactly, 0 included.
"""

from __future__ import annotations

from harness.context.compaction import AUTO_ACCURACY_CEILING, DEFAULT_BUFFER_TOKENS
from harness.loop import TurnEngine
from harness.safety.permissions import PermissionEngine
from harness.tools import ToolRegistry


def _engine(tmp_path, settings) -> TurnEngine:
    engine = TurnEngine(
        provider=object(),
        registry=ToolRegistry(),
        permissions=PermissionEngine(workspace_root=tmp_path),
        model="unpriced:big-window",
    )
    engine.compaction_settings = lambda: dict(settings)
    return engine


def test_auto_caps_an_unpriced_1m_window_at_the_ceiling(tmp_path):
    engine = _engine(tmp_path, {"max_context_tokens": "auto", "context_window": 1_000_000})
    assert engine._history_trigger_tokens() == AUTO_ACCURACY_CEILING - DEFAULT_BUFFER_TOKENS
    # The verbatim keep scales with the capped window, as for any cap.
    assert engine._compaction_config()["keep_tokens"] == 40_000


def test_auto_is_the_same_window_whatever_the_model_costs(tmp_path, monkeypatch):
    """Until 2026-10-05 a model whose cache reads cost 10% of fresh input was
    held to 250k and one at 18% to 150k. A ratio is not a cost — it capped Gemini 3.8
    Flash, whose 400k cached prompt is $0.03 — and no other harness sets its window
    from a price. The price sheet is not read at all now."""

    def never(*_a, **_k):
        raise AssertionError("the working window must not read the price sheet")

    monkeypatch.setattr("harness.llm.providers.catalog.models_dev", never)
    for model in ("vertex:gemini-3.8-flash", "zai:glm-5.2", "xai:grok-4.6", "unpriced:big-window"):
        engine = _engine(tmp_path, {"max_context_tokens": "auto", "context_window": 1_000_000})
        engine.model = model
        assert engine._compaction_config()["context_window"] == AUTO_ACCURACY_CEILING
        assert engine._history_trigger_tokens() == AUTO_ACCURACY_CEILING - DEFAULT_BUFFER_TOKENS


def test_auto_never_raises_a_window_smaller_than_the_ceiling(tmp_path):
    engine = _engine(tmp_path, {"max_context_tokens": "auto", "context_window": 128_000})
    assert engine._history_trigger_tokens() == 128_000 - DEFAULT_BUFFER_TOKENS


def test_an_explicit_number_is_obeyed_exactly(tmp_path):
    uncapped = _engine(tmp_path, {"max_context_tokens": 0, "context_window": 1_000_000})
    assert uncapped._history_trigger_tokens() == 1_000_000 - DEFAULT_BUFFER_TOKENS
    wide = _engine(tmp_path, {"max_context_tokens": 800_000, "context_window": 1_000_000})
    assert wide._history_trigger_tokens() == 800_000 - DEFAULT_BUFFER_TOKENS


def test_a_large_declared_output_does_not_halve_the_capped_trigger(tmp_path):
    """deepseek-v4.1-flash declares a 1M window and a 384k output. Reserving that output
    out of the 400k cap put the trigger at 200k and the 0.7 prepare at 140k — inside the
    user's everyday contexts: 9 pre-step summarizer stalls of 30-93s in one session on the
    first afternoon after the ceiling shipped. The cap bounds the prompt; the reserve
    protects the real window."""
    engine = _engine(
        tmp_path,
        {"max_context_tokens": "auto", "context_window": 1_000_000, "output_tokens": 384_000},
    )
    assert engine._history_trigger_tokens() == AUTO_ACCURACY_CEILING - DEFAULT_BUFFER_TOKENS
    # An explicit cap follows the same rule.
    explicit = _engine(
        tmp_path,
        {"max_context_tokens": 250_000, "context_window": 1_000_000, "output_tokens": 384_000},
    )
    assert explicit._history_trigger_tokens() == 250_000 - DEFAULT_BUFFER_TOKENS
    # A real window that cannot fit prompt + answer still wins over the cap.
    tight = _engine(
        tmp_path,
        {"max_context_tokens": 300_000, "context_window": 320_000, "output_tokens": 128_000},
    )
    assert tight._history_trigger_tokens() == 320_000 - 128_000
