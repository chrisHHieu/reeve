"""Live DeepSeek cache-read measurement — a live end-to-end measurement.

The unit suite pins the SHAPE of prefix stability (`tests/test_prompt_cache_stability.py`:
step 2 is a byte-prefix of step 1). This file measures the OUTCOME: the provider's own
accounting says the shared prefix came back from cache. That number is also what
`engine/compaction.py` gates cache-aware summarizer replay on, and DeepSeek reports it flat
as `prompt_cache_hit_tokens` rather than nesting it in `prompt_tokens_details`.

Opt-in: skipped unless DEEPSEEK_API_KEY is set. Costs two small calls.
"""

from __future__ import annotations

import os

import pytest

from harness.llm.providers.registry import build_provider_client

pytestmark = pytest.mark.skipif(
    not os.environ.get("DEEPSEEK_API_KEY"),
    reason="live DeepSeek call; set DEEPSEEK_API_KEY to measure cache reads",
)

# Long enough to clear DeepSeek's cache-block granularity (64 tokens),
# short enough to stay cheap.
_SYSTEM = "You are a terse assistant. Answer with one word. " + " ".join(
    f"rule-{i}: keep answers short." for i in range(1, 40)
)


def _model() -> str:
    """Never hardcode a model id in the tests — the env var overrides, else a current row."""
    return os.environ.get("DEEPSEEK_TEST_MODEL") or "deepseek-v4-flash"


def test_the_second_request_reports_a_cache_read():
    client = build_provider_client("deepseek", {}, None)
    prompt = [{"role": "system", "content": _SYSTEM}, {"role": "user", "content": "Say ok."}]
    first = client.complete(model=_model(), messages=prompt, max_tokens=16)
    assert first.usage is not None and first.usage.cache_read == 0  # nothing to reuse yet

    extended = [*prompt, {"role": "user", "content": "Say ok once more."}]
    second = client.complete(model=_model(), messages=extended, max_tokens=16)
    assert second.usage is not None
    assert second.usage.cache_read > 0, second.usage
