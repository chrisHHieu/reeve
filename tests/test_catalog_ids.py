"""A Vertex or Bedrock model is found in the catalogue under the id the catalogue uses.

Their routed ids carry a routing segment (`vertex:gemini/…`, `bedrock:claude/…`) that models.dev does not,
so every curated Vertex row missed its facts: the picker showed `Reasoning —` / `Cost —` and a Vertex
session's spend estimate was None. What has to hold:

  - a routed id reaches the catalogue's row for the same model (reasoning, cost, context, levels);
  - Claude on Vertex finds its versioned row (`@default`, else the newest dated one);
  - nothing is guessed: a model the catalogue does not carry stays unknown, and a routing segment is only
    removed from the provider that has one;
  - an id that is already the catalogue's own is unchanged.
"""

from __future__ import annotations

import pytest

from harness.llm.providers.catalog import ModelCatalog, ModelsDevCatalog, estimate_cost_usd, set_catalog, set_models_dev
from harness.llm.providers.catalog_ids import catalogue_id
from harness.llm.providers.matrix import MATRIX, model_details

GEMINI = {"name": "Gemini 3.7 Flash", "reasoning": True, "context_length": 1_048_576, "source": "models.dev",
          "cost": {"input": 0.5, "output": 3.0}, "reasoning_options": [{"type": "effort", "values": ["low", "high"]}]}
CLAUDE = {"name": "Claude Sonnet 4.6", "reasoning": True, "context_length": 200_000, "source": "models.dev",
          "cost": {"input": 3.0, "output": 15.0}}
LLAMA = {"name": "Llama 4 Maverick", "reasoning": False, "context_length": 1_000_000, "source": "models.dev",
         "cost": {"input": 0.35, "output": 1.15}}
HAIKU = {"name": "Claude Haiku 4.5", "reasoning": True, "source": "models.dev", "cost": {"input": 1.0, "output": 5.0}}

ENTRIES = {
    "vertex:gemini-3.7-flash": GEMINI,
    "vertex:claude-sonnet-4-6@default": CLAUDE,
    "vertex:claude-haiku-4-5@20251001": HAIKU,
    "vertex:meta/llama-4-maverick-17b-128e-instruct-maas": LLAMA,
    "bedrock:anthropic.claude-haiku-4-5-20251001-v1:0": HAIKU,
    "bedrock:meta.llama4-maverick-17b-instruct-v1:0": LLAMA,
}


class _Fixed(ModelsDevCatalog):
    def __init__(self, entries, path):
        super().__init__(path)
        self._entries = dict(entries)
        self._fetched_at = float("inf")
        self._tried = True


class _FixedBase(ModelCatalog):
    def __init__(self, path):
        super().__init__(path)
        self._entries = {}
        self._fetched_at = float("inf")
        self._tried = True


@pytest.fixture(autouse=True)
def _catalogues(tmp_path):
    set_catalog(_FixedBase(tmp_path / "c.json"))
    set_models_dev(_Fixed(ENTRIES, tmp_path / "f.json"))
    yield
    set_catalog(None)
    set_models_dev(None)


def test_the_curated_vertex_rows_are_found():
    for model, row in (
        ("vertex:gemini/gemini-3.7-flash", GEMINI),
        ("vertex:claude/claude-sonnet-4-6", CLAUDE),
        ("vertex:openweight/meta/llama-4-maverick-17b-128e-instruct-maas", LLAMA),
    ):
        assert model in MATRIX
        details = model_details(model)
        assert details["reasoning"] is row["reasoning"], model
        assert details["cost"] == row["cost"], model
        assert details["context"] == row["context_length"], model
        assert details["source"] == "models.dev", model


def test_the_picker_offers_the_levels_the_catalogue_states_for_a_vertex_model():
    assert model_details("vertex:gemini/gemini-3.7-flash")["reasoning_levels"] == ["low", "high"]


def test_a_vertex_session_has_a_spend_estimate():
    cost = estimate_cost_usd("vertex:gemini/gemini-3.7-flash", input_tokens=1_000_000, output_tokens=1_000_000)
    assert cost == pytest.approx(3.5)
    assert estimate_cost_usd("vertex:claude/claude-sonnet-4-6", input_tokens=1_000_000) == pytest.approx(3.0)


def test_claude_takes_the_default_row_else_the_newest_dated_one():
    assert catalogue_id("vertex:claude/claude-sonnet-4-6", ENTRIES) == "vertex:claude-sonnet-4-6@default"
    assert catalogue_id("vertex:claude/claude-haiku-4-5", ENTRIES) == "vertex:claude-haiku-4-5@20251001"
    dated = {"vertex:claude-opus-4@20250101": {}, "vertex:claude-opus-4@20250514": {}}
    assert catalogue_id("vertex:claude/claude-opus-4", dated) == "vertex:claude-opus-4@20250514"
    assert catalogue_id("vertex:claude-sonnet-4-6", ENTRIES) == "vertex:claude-sonnet-4-6@default"  # no family segment


def test_bedrock_loses_only_its_routing_segment():
    assert catalogue_id("bedrock:claude/anthropic.claude-haiku-4-5-20251001-v1:0", ENTRIES) == (
        "bedrock:anthropic.claude-haiku-4-5-20251001-v1:0"
    )
    assert catalogue_id("bedrock:other/meta.llama4-maverick-17b-instruct-v1:0", ENTRIES) == (
        "bedrock:meta.llama4-maverick-17b-instruct-v1:0"
    )


def test_nothing_is_guessed():
    assert catalogue_id("vertex:gemini/gemini-9-ultra", ENTRIES) is None  # not in the catalogue
    assert catalogue_id("bedrock:claude/anthropic.claude-sonnet-4-6-v1:0", ENTRIES) is None  # a different id, not fuzzy-matched
    assert catalogue_id("gemini:gemini/gemini-3.7-flash", ENTRIES) is None  # only Vertex and Bedrock have the segment
    assert catalogue_id("vertex:claude/claude-sonnet-9", ENTRIES) is None
    unknown = model_details("vertex:gemini/gemini-9-ultra")
    assert unknown["reasoning"] is None and unknown["cost"] is None  # unknown stays unknown, never "No" / free
    assert estimate_cost_usd("vertex:gemini/gemini-9-ultra", input_tokens=1000) is None


def test_the_catalogues_own_id_is_unchanged():
    assert catalogue_id("vertex:gemini-3.7-flash", ENTRIES) == "vertex:gemini-3.7-flash"
    assert catalogue_id("vertex:claude-sonnet-4-6@default", ENTRIES) == "vertex:claude-sonnet-4-6@default"
    assert catalogue_id("vertex:meta/llama-4-maverick-17b-128e-instruct-maas", ENTRIES) == (
        "vertex:meta/llama-4-maverick-17b-128e-instruct-maas"
    )
