"""A model switch drops the old model's context measurement (2026-09-23).

The context signal is the provider's reported prompt count plus a bias-adjusted
estimate of what was appended since — both are tokenizer-specific. Live: a session
switched to a 128k-window model and compacted while the local estimate sat at 89k,
because `_last_context_tokens` still held a number the PREVIOUS model had reported.
"""

from __future__ import annotations

from harness.loop import TurnEngine
from harness.safety.permissions import PermissionEngine
from harness.llm.providers import ModelCapabilities, ProviderClient
from harness.tools import ToolRegistry


class _Idle(ProviderClient):
    def stream(self, **kwargs):
        raise AssertionError("no request is made here")

    def complete(self, **kwargs):
        raise AssertionError("no request is made here")

    def capabilities(self, model):
        return ModelCapabilities()


def _engine(tmp_path):
    return TurnEngine(
        provider=_Idle(),
        registry=ToolRegistry(),
        permissions=PermissionEngine(workspace_root=tmp_path),
        model="openrouter:openai/gpt-6-sol-pro",
        messages=[
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
        ],
    )


def test_switch_model_clears_the_provider_measurement(tmp_path):
    engine = _engine(tmp_path)
    engine._last_context_tokens = 131_000
    engine._context_measured_at = 0
    engine._estimate_bias = 1.4

    engine.switch_model("openrouter:openai/gpt-6-astra-pro")

    assert engine._last_context_tokens is None
    assert engine._context_measured_at == len(engine.messages)
    assert engine._estimate_bias is None


def test_the_signal_falls_back_to_the_estimate_after_a_switch(tmp_path):
    engine = _engine(tmp_path)
    engine._last_context_tokens = 131_000  # the old model's number
    engine._context_measured_at = 0

    engine.switch_model("openrouter:openai/gpt-6-astra-pro")

    # Not the stale 131k: a fresh estimate of the whole outbound view.
    assert engine._context_signal() < 131_000
    assert engine._context_signal() > 0


def test_a_switch_does_not_make_compaction_due_on_a_stale_number(tmp_path):
    """The bug the session showed: trigger 125k, estimate 89k, yet compaction ran."""
    engine = _engine(tmp_path)
    engine._last_context_tokens = 10_000_000  # a huge number from the previous model
    engine._context_measured_at = 0

    assert engine._compaction_due() is True  # before the switch, the stale number fires

    engine.switch_model("openrouter:openai/gpt-6-astra-pro")

    assert engine._compaction_due() is False
