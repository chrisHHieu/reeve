"""`MeteredProvider`: every call's usage reaches the sink; a broken sink costs nothing."""

from __future__ import annotations

from harness.llm.providers import AssistantTurn
from harness.llm.providers.base import TokenUsage
from harness.llm.providers.metered import MeteredProvider


class _Inner:
    name = "inner"

    def complete(self, *, model, messages, tools=None, **settings):
        return AssistantTurn(text="ok", finish_reason="stop", usage=TokenUsage(input=3, output=2))


def test_each_call_reports_its_usage_and_the_answer_is_untouched():
    seen = []
    provider = MeteredProvider(_Inner(), lambda model, usage: seen.append((model, usage.total)))
    turn = provider.complete(model="m", messages=[], temperature=0.1)
    assert turn.text == "ok"
    assert seen == [("m", 5)]
    assert provider.name == "inner"  # everything else is the inner provider's


def test_a_sink_that_raises_never_fails_the_call():
    def broken(model, usage):
        raise RuntimeError("metrics store gone")

    assert MeteredProvider(_Inner(), broken).complete(model="m", messages=[]).text == "ok"
