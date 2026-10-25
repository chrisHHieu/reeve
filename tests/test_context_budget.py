"""Context awareness — the model-facing budget line, stamped at the turn start once per
ten-percent band (P7). The recorded decision stands that no wrap-up instruction rides it:
the harness states the facts, the model decides."""

from __future__ import annotations

from harness.loop import TurnEngine
from harness.safety.permissions import PermissionEngine
from harness.llm.providers import AssistantTurn, ModelCapabilities, ProviderClient
from harness.tools import ToolRegistry


class _Provider(ProviderClient):
    def complete(self, **_kw):
        return AssistantTurn(text="ok", finish_reason="stop")

    def capabilities(self, model):
        return ModelCapabilities()


def _engine(tmp_path) -> TurnEngine:
    engine = TurnEngine(
        provider=_Provider(),
        registry=ToolRegistry(),
        permissions=PermissionEngine(workspace_root=tmp_path),
        model="gpt-5.5",
        messages=[{"role": "system", "content": "sys"}],
    )
    engine.compaction_settings = lambda: {
        "context_window": 100_000,
        "buffer_tokens": 0,
        "keep_tokens": 0,
        "output_tokens": 0,
    }
    return engine


def test_the_line_is_stamped_once_per_band(tmp_path):
    engine = _engine(tmp_path)
    engine._last_context_tokens = 63_000
    engine._context_measured_at = len(engine.messages)  # nothing appended since

    first = {"role": "user", "content": "go"}
    engine._stamp_context_budget(first)
    assert "<context-budget>" in first["api_content"]
    assert "~63k of ~100k tokens before compaction (60%)" in first["api_content"]

    # Same band: a new line would be prompt weight for no new information.
    same_band = {"role": "user", "content": "go"}
    engine._stamp_context_budget(same_band)
    assert "api_content" not in same_band

    engine._last_context_tokens = 72_000
    higher = {"role": "user", "content": "go"}
    engine._stamp_context_budget(higher)
    assert "(70%)" in higher["api_content"]


def test_a_recovery_restates_the_budget(tmp_path):
    """After a compaction the reading drops; the model should be told it got its budget
    back, which is the whole point of a downward band change being stamped too."""
    engine = _engine(tmp_path)
    engine._last_context_tokens = 95_000
    engine._context_measured_at = len(engine.messages)
    engine._stamp_context_budget({"role": "user", "content": "go"})

    engine._last_context_tokens = None  # compaction resets the provider-priced reading
    message = {"role": "user", "content": "go"}
    engine._stamp_context_budget(message)

    assert "api_content" in message
    assert "(0%)" in message["api_content"]


def test_the_line_can_be_turned_off(tmp_path):
    engine = _engine(tmp_path)
    engine.context_budget_line = False
    engine._last_context_tokens = 63_000
    engine._context_measured_at = len(engine.messages)

    message = {"role": "user", "content": "go"}
    engine._stamp_context_budget(message)

    assert "api_content" not in message


def test_no_trigger_means_no_line(tmp_path):
    """A defensive guard: the trigger always resolves today (a window of 0 falls back to
    the default window), but a line with no denominator must never be stamped."""
    engine = _engine(tmp_path)
    engine._history_trigger_tokens = lambda: 0
    message = {"role": "user", "content": "go"}
    engine._stamp_context_budget(message)
    assert "api_content" not in message


async def test_every_stamped_line_is_logged(tmp_path):
    """Model-visible means logged: the line rides `api_content`, which the
    `user/message` event does not carry. It left no trace — an ablation could not even
    tell whether it fired. A stamped turn now writes `context/budget` right after its
    `user/message`, with the numbers the line is rebuilt from."""
    from harness.session.session_log import SessionLog

    engine = _engine(tmp_path)
    engine.log = SessionLog(tmp_path / "s.events.jsonl")
    engine._last_context_tokens = 63_000
    engine._context_measured_at = len(engine.messages)
    async for _event in engine.run("go"):
        pass
    kinds = [row["type"] for row in engine.log.read()]
    assert kinds[kinds.index("user/message") + 1] == "context/budget"
    row = next(row for row in engine.log.read() if row["type"] == "context/budget")
    assert (row["turn"], row["trigger"], row["band"]) == (1, 100_000, 6)
