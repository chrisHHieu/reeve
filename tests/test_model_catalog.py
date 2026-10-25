"""The provider's catalogue outranks our copy of it — and the copy gets checked.

Two halves. The first is the mechanism, offline and deterministic. The second is a drift
check against the live catalogue, skipped without network, which exists because the drift
is what actually bit: on 2026-08-26 reeve called 250 of OpenRouter's 417 models blind
and had the wrong context window for `z-ai/glm-5.2` by a factor of eight.
"""

from __future__ import annotations

import json

import pytest

from harness.llm.providers.capabilities import capabilities_for
from harness.llm.providers.catalog import ModelCatalog, fetch_openrouter, set_catalog
from harness.llm.providers.matrix import MATRIX, model_context_windows


class _Fixed(ModelCatalog):
    """A catalogue that never touches the network."""

    def __init__(self, entries, path):
        super().__init__(path)
        self._entries = dict(entries)
        self._fetched_at = float("inf")  # never stale, never fetches
        self._tried = True


@pytest.fixture(autouse=True)
def _restore():
    yield
    set_catalog(None)


# -- the catalogue outranks the copy ------------------------------------------------


def test_a_published_capability_beats_the_matrix(tmp_path):
    """The whole point. `llama-4-maverick` is in the matrix; if the provider says it
    accepts images, the matrix row is a copy that drifted, not a second opinion."""
    mid = "openrouter:meta-llama/llama-4-maverick"
    assert mid in MATRIX
    set_catalog(_Fixed({mid: {"vision": False, "tools": True}}, tmp_path / "c.json"))
    assert capabilities_for(mid).vision is False  # the catalogue said no

    set_catalog(_Fixed({mid: {"vision": True, "tools": True}}, tmp_path / "c.json"))
    assert capabilities_for(mid).vision is True  # and yes


def test_a_model_nobody_curated_is_answered_from_the_catalogue(tmp_path):
    """The 250-model case: not in the matrix, no name heuristic matches, and the old
    answer was a conservative `vision=False` that happened to be wrong."""
    mid = "openrouter:stealth/ox-alpha"
    assert mid not in MATRIX
    set_catalog(_Fixed({}, tmp_path / "c.json"))
    assert capabilities_for(mid).vision is False  # what it used to do, always

    set_catalog(_Fixed({mid: {"vision": True, "tools": True}}, tmp_path / "c.json"))
    assert capabilities_for(mid).vision is True


def test_only_what_the_catalogue_actually_states_is_taken(tmp_path):
    """OpenRouter's schema says nothing about parallel tool calls or PDF. Those must keep
    the matrix's answer — filling them from a default would be inventing a fact out of
    the absence of one."""
    mid = "openrouter:meta-llama/llama-4-maverick"
    from_matrix = MATRIX[mid].caps
    set_catalog(_Fixed({mid: {"vision": True, "tools": True}}, tmp_path / "c.json"))
    got = capabilities_for(mid)
    assert got.parallel_tool_calls == from_matrix.parallel_tool_calls
    assert got.pdf == from_matrix.pdf
    assert got.streaming == from_matrix.streaming


def test_the_context_window_is_taken_from_the_catalogue_too(tmp_path):
    """Not cosmetic: `engine._compaction_config` reads this map, so a wrong window is
    money — an eight-times-small one compacts eight times too early."""
    mid = "openrouter:z-ai/glm-5.2"
    set_catalog(_Fixed({mid: {"context_length": 1_048_576}}, tmp_path / "c.json"))
    assert model_context_windows()[mid] == 1_048_576


# -- it must never cost a session ---------------------------------------------------


def test_an_unreachable_catalogue_falls_through_to_todays_behaviour(tmp_path):
    class _Broken(ModelCatalog):
        def get(self, model):
            raise OSError("no network")

    set_catalog(_Broken(tmp_path / "c.json"))
    # The matrix still answers, and an unknown model still gets the heuristic.
    assert capabilities_for("anthropic:claude-opus-4-8").vision is True
    assert capabilities_for("openrouter:something/unknown").vision is False


def test_a_corrupt_cache_file_is_not_fatal(tmp_path):
    path = tmp_path / "c.json"
    path.write_text("{not json", encoding="utf-8")
    cat = ModelCatalog(path)
    cat._tried = True  # do not go to the network in a unit test
    assert cat.get("openrouter:anything") is None


def test_a_stale_cache_is_used_rather_than_discarded(tmp_path):
    """A stale answer beats a guess. If the refresh fails, what is on disk still stands."""
    path = tmp_path / "c.json"
    path.write_text(
        json.dumps({"fetched_at": 1.0, "entries": {"openrouter:x/y": {"vision": True}}}),
        encoding="utf-8",
    )
    cat = ModelCatalog(path)
    cat._tried = True  # simulate: the refresh was attempted and failed
    assert cat.get("openrouter:x/y") == {"vision": True}


def test_the_lookup_never_reaches_the_network_twice(tmp_path):

    calls = []

    class _Counting(ModelCatalog):
        def _fetch(self):
            calls.append(1)
            return {}

    cat = _Counting(tmp_path / "c.json")
    for _ in range(5):
        cat.get("openrouter:a/b")
    assert len(calls) == 1  # `capabilities_for` runs on every outbound assembly


def test_opencode_go_projection_keeps_every_model_and_tags_its_wire(monkeypatch):
    """models.dev marks a model that needs another transport with `provider.npm`. The
    projection keeps ALL of them — dropping the non-chat third is why Reeve showed fewer
    models than other catalogues — and records the transport as `wire`, which the multi-wire
    OpenCode Go client reads to route each call (chat / Messages / Responses)."""
    import httpx

    from harness.llm.providers.catalog import fetch_models_dev

    payload = {
        "opencode-go": {
            "models": {
                "deepseek-v4.1-flash": {"name": "DeepSeek V4.1 Flash"},
                "glm-5.3": {
                    "name": "GLM-5.3",
                    "provider": {"npm": "@ai-sdk/openai-compatible"},
                },
                "minimax-m3": {
                    "name": "MiniMax M3",
                    "provider": {"npm": "@ai-sdk/anthropic", "api": "https://opencode.ai/inference/go/anthropic/v1/"},
                },
                "grok-4.6": {"name": "Grok 4.6", "provider": {"npm": "@ai-sdk/openai"}},
                "gemini-4-argon-contributor": {
                    "name": "Gemini 4 Argon Contributor",
                    "provider": {"npm": "@ai-sdk/google"},
                },
            }
        }
    }

    class _Resp:
        status_code = 200

        def json(self):
            return payload

    monkeypatch.delenv("REEVE_DISABLE_MODELS_FETCH", raising=False)
    monkeypatch.delenv("REEVE_MODELS_URL", raising=False)
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _Resp())

    entries = fetch_models_dev()
    # Default wire (no override) → chat; explicit overrides → their transport.
    assert "wire" not in entries["opencode-go:deepseek-v4.1-flash"]
    assert entries["opencode-go:glm-5.3"]["wire"] == "chat"
    assert entries["opencode-go:minimax-m3"]["wire"] == "messages"
    assert entries["opencode-go:grok-4.6"]["wire"] == "responses"
    assert entries["opencode-go:gemini-4-argon-contributor"]["wire"] == "gemini"
    # The model's own endpoint is kept when the catalogue names one (the gateway moved its
    # wires to their own roots on 2026-10-20), and absent when it does not.
    assert entries["opencode-go:minimax-m3"]["api"] == "https://opencode.ai/inference/go/anthropic/v1"
    assert "api" not in entries["opencode-go:grok-4.6"]


# -- drift, against the live catalogue ----------------------------------------------


@pytest.mark.skipif(
    not __import__("os").environ.get("REEVE_NET_TESTS"),
    reason="hits the network; set REEVE_NET_TESTS=1 to check the matrix for drift",
)
def test_the_curated_openrouter_rows_have_not_drifted():
    """Apples to apples: the SAME provider serving the SAME id. Rows for other resellers
    are deliberately not checked this way — Together serving Llama 4 is a different
    endpoint, and copying OpenRouter's answer onto it would be an inference, not a fact.
    """
    published = fetch_openrouter()
    if not published:
        pytest.skip("catalogue unreachable")

    drift = []
    for mid, entry in MATRIX.items():
        row = published.get(mid)
        if row is None:
            continue
        if bool(entry.caps.vision) != bool(row.get("vision")):
            drift.append(f"{mid}: vision ours={entry.caps.vision} theirs={row.get('vision')}")
        window = row.get("context_length")
        if entry.context_window and window and entry.context_window != window:
            drift.append(f"{mid}: window ours={entry.context_window} theirs={window}")
    assert not drift, "the matrix has drifted from the provider:\n  " + "\n  ".join(drift)
