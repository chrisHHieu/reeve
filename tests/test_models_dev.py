"""models.dev as a second, provider-agnostic catalogue (harness/llm/providers/catalog.py).

OpenRouter alone cannot answer for DeepSeek direct, Gemini, Bedrock, … — and the
curated matrix is a copy that ages. models.dev is the community catalogue most of the
ecosystem reads: every provider, every model, with name, modalities, reasoning, context and cost.
Two rules are pinned here: hot paths read DISK ONLY (a session build must not download
4.5 MB), and the picker's list path is the one place allowed to fetch.
"""

from __future__ import annotations


import pytest

from harness.llm.providers.capabilities import capabilities_for
from harness.llm.providers.catalog import ModelCatalog, ModelsDevCatalog, fetch_models_dev, set_catalog, set_models_dev
from harness.llm.providers.matrix import MATRIX, model_context_windows, model_details, provider_model_ids

FIXTURE = {
    "deepseek": {
        "id": "deepseek",
        "name": "DeepSeek",
        "models": {
            "deepseek-v4-flash": {
                "id": "deepseek-v4-flash",
                "name": "DeepSeek V4 Flash",
                "attachment": True,
                "reasoning": True,
                "tool_call": True,
                "temperature": True,
                "release_date": "2026-09-10",
                "modalities": {"input": ["text", "image"], "output": ["text"]},
                "limit": {"context": 1_000_000, "output": 384_000},
                "cost": {"input": 0.15, "output": 0.6, "cache_read": 0.003},
            },
        },
    },
    "google": {
        "id": "google",
        "name": "Google",
        "models": {
            "gemini-9-flash": {
                "id": "gemini-9-flash",
                "name": "Gemini 9 Flash",
                "attachment": True,
                "reasoning": True,
                "tool_call": True,
                "modalities": {"input": ["text", "image", "pdf"], "output": ["text"]},
                "limit": {"context": 2_000_000, "output": 100_000},
            },
        },
    },
    "not-a-reeve-provider": {
        "id": "not-a-reeve-provider",
        "models": {"mystery": {"name": "Mystery"}},
    },
    "zhipuai-coding-plan": {
        "id": "zhipuai-coding-plan",
        "name": "Zhipu AI Coding Plan",
        "api": "https://open.bigmodel.cn/api/coding/paas/v4",
        "models": {
            "glm-5.3-flash": {
                "id": "glm-5.3-flash",
                "name": "GLM-5.3-Flash",
                "attachment": True,
                "reasoning": True,
                "tool_call": True,
                "modalities": {"input": ["text", "image", "pdf"], "output": ["text"]},
                "limit": {"context": 1_000_000, "output": 131_072},
                "cost": {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0},
            },
        },
    },
    # A sibling the plan borrows facts from: models.dev's plan entry lists only the newest
    # models, while the plan still serves older ones (the borrow table in catalog.py).
    "zai-coding-plan": {
        "id": "zai-coding-plan",
        "name": "Z.AI Coding Plan",
        "models": {
            "glm-5.2": {
                "id": "glm-5.2",
                "name": "GLM-5.2",
                "attachment": False,
                "reasoning": True,
                "tool_call": True,
                "reasoning_options": [{"type": "effort", "values": ["high", "max"]}],
                "limit": {"context": 1_000_000, "output": 131_072},
                "cost": {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0},
            },
        },
    },
}


class _FixedBase(ModelCatalog):
    def __init__(self, entries, path):
        super().__init__(path)
        self._entries = dict(entries)
        self._fetched_at = float("inf")
        self._tried = True


class _FixedDev(ModelsDevCatalog):
    def __init__(self, entries, path):
        super().__init__(path)
        self._entries = dict(entries)
        self._fetched_at = float("inf")
        self._tried = True


@pytest.fixture(autouse=True)
def _restore():
    yield
    set_catalog(None)
    set_models_dev(None)


def _project_fixture(monkeypatch) -> dict:
    import httpx

    class _Response:
        status_code = 200

        def json(self):
            return FIXTURE

    monkeypatch.setattr(httpx, "get", lambda *a, **k: _Response())
    return fetch_models_dev()


# -- projection ---------------------------------------------------------------------


def test_the_catalogue_is_projected_onto_reeve_provider_ids(monkeypatch):
    entries = _project_fixture(monkeypatch)
    assert "deepseek:deepseek-v4-flash" in entries
    assert "gemini:gemini-9-flash" in entries  # models.dev `google` → reeve gemini
    # Zhipu's Coding Plan and its international Z.AI sibling map 1:1 (same id on both
    # sides, like opencode-go).
    assert "zhipuai-coding-plan:glm-5.3-flash" in entries
    assert "zai-coding-plan:glm-5.2" in entries
    assert not any("not-a-reeve" in key for key in entries)


def test_a_plan_borrows_facts_for_models_its_own_entry_omits(monkeypatch):
    """models.dev's plan entry can shrink (Zhipu's listed 10 models on 2026-09-14 and 4 on
    2026-09-18) while the plan keeps serving them; the sibling provider's row is the same
    model, so its facts are borrowed — and never overwrite the plan's own statement."""
    entries = _project_fixture(monkeypatch)
    borrowed = entries["zhipuai-coding-plan:glm-5.2"]
    assert borrowed["context_length"] == 1_000_000
    assert borrowed["reasoning_options"] == [{"type": "effort", "values": ["high", "max"]}]
    # The plan's own row (glm-5.3-flash) is present and untouched by the borrow pass.
    assert entries["zhipuai-coding-plan:glm-5.3-flash"]["name"] == "GLM-5.3-Flash"


def test_a_projected_entry_carries_the_facts_the_picker_needs(monkeypatch):
    entry = _project_fixture(monkeypatch)["deepseek:deepseek-v4-flash"]
    assert entry["name"] == "DeepSeek V4 Flash"
    assert entry["vision"] is True and entry["reasoning"] is True and entry["tools"] is True
    assert entry["context_length"] == 1_000_000 and entry["output_length"] == 384_000
    assert entry["input"] == ["text", "image"]
    assert entry["cost"]["input"] == 0.15
    assert entry["release_date"] == "2026-09-10"


def test_a_malformed_body_is_an_empty_map_not_an_exception(monkeypatch):
    import httpx

    class _Broken:
        status_code = 200

        def json(self):
            return ["not", "a", "dict"]

    monkeypatch.setattr(httpx, "get", lambda *a, **k: _Broken())
    assert fetch_models_dev() == {}


def test_the_fetch_can_be_disabled_and_redirected_by_env(monkeypatch):
    import httpx

    class _Response:
        status_code = 200

        def json(self):
            return FIXTURE

    calls: list[str] = []
    monkeypatch.setattr(httpx, "get", lambda url, **k: calls.append(url) or _Response())
    monkeypatch.setenv("REEVE_DISABLE_MODELS_FETCH", "1")
    assert fetch_models_dev() == {}
    assert calls == []

    monkeypatch.delenv("REEVE_DISABLE_MODELS_FETCH")
    monkeypatch.setenv("REEVE_MODELS_URL", "https://mirror.example/api.json")
    fetch_models_dev()
    assert calls == ["https://mirror.example/api.json"]


# -- hot/cold split -----------------------------------------------------------------


def test_get_is_disk_only_and_entries_may_fetch(tmp_path):
    """The session-build path must never wait on the 4.5 MB download."""
    calls = []

    class _Counting(ModelsDevCatalog):
        def _fetch(self):
            calls.append(1)
            return {"deepseek:x": {"vision": True}}

    cat = _Counting(tmp_path / "facts.json")
    assert cat.get("deepseek:x") is None  # no disk, and `get` does not fetch
    assert calls == []
    assert cat.entries()  # the list path fetches once
    assert calls == [1]
    assert cat.get("deepseek:x") == {"vision": True}  # and warms the hot path


def test_a_stale_cache_still_answers_the_hot_path(tmp_path):
    import json

    path = tmp_path / "facts.json"
    path.write_text(
        json.dumps({"fetched_at": 1.0, "entries": {"deepseek:x": {"vision": True}}}),
        encoding="utf-8",
    )
    cat = ModelsDevCatalog(path)
    assert cat.get("deepseek:x") == {"vision": True}


# -- the merge into caps / context / picker ----------------------------------------


def test_capabilities_use_models_dev_when_openrouter_has_no_opinion(tmp_path):
    mid = "deepseek:deepseek-v4-flash"
    set_catalog(_FixedBase({}, tmp_path / "c.json"))
    set_models_dev(
        _FixedDev({"deepseek:deepseek-v4-flash": {"vision": True, "tools": True}}, tmp_path / "f.json")
    )
    # The matrix row exists and says no vision; the catalogue's statement wins.
    assert mid in MATRIX
    assert capabilities_for(mid).vision is True
    # PDF is a fact only when a modality list states it.
    set_models_dev(
        _FixedDev(
            {"deepseek:deepseek-v4-flash": {"vision": True, "tools": True, "input": ["text", "pdf"]}},
            tmp_path / "f.json",
        )
    )
    assert capabilities_for(mid).pdf is True


def test_context_windows_follow_models_dev(tmp_path):
    set_catalog(_FixedBase({}, tmp_path / "c.json"))
    set_models_dev(
        _FixedDev({"deepseek:deepseek-v4-flash": {"context_length": 1_000_000}}, tmp_path / "f.json")
    )
    assert model_context_windows()["deepseek:deepseek-v4-flash"] == 1_000_000


def test_provider_model_ids_puts_curated_first_then_catalogue_extras(tmp_path):
    set_models_dev(
        _FixedDev(
            {
                "deepseek:deepseek-v4-flash": {},
                "deepseek:deepseek-v5": {},
            },
            tmp_path / "f.json",
        )
    )
    ids = provider_model_ids("deepseek")
    assert ids[0] == "deepseek-v4-flash"  # the matrix head
    assert "deepseek-v5" in ids  # the catalogue extra
    assert len(ids) == len(set(ids))


def test_model_details_composes_matrix_and_catalogue(tmp_path):
    set_catalog(_FixedBase({}, tmp_path / "c.json"))
    set_models_dev(
        _FixedDev(
            {
                "deepseek:deepseek-v4-flash": {
                    "name": "DeepSeek V4 Flash",
                    "vision": True,
                    "tools": True,
                    "reasoning": True,
                    "context_length": 1_000_000,
                    "output_length": 384_000,
                    "input": ["text", "image"],
                    "cost": {"input": 0.15, "output": 0.6},
                    "release_date": "2026-09-10",
                }
            },
            tmp_path / "f.json",
        )
    )
    row = model_details("deepseek:deepseek-v4-flash")
    assert row["curated"] is True  # the matrix vouches for it
    assert row["provider"] == "deepseek"
    assert row["context"] == 1_000_000
    assert row["vision"] is True and row["reasoning"] is True
    assert row["free"] is False


def test_a_zero_priced_model_is_free_and_an_unpriced_one_is_not(tmp_path):
    set_catalog(_FixedBase({}, tmp_path / "c.json"))
    set_models_dev(
        _FixedDev(
            {
                "deepseek:free-model": {"cost": {"input": 0, "output": 0}},
                "deepseek:unknown-model": {},
            },
            tmp_path / "f.json",
        )
    )
    assert model_details("deepseek:free-model")["free"] is True
    assert model_details("deepseek:unknown-model")["free"] is False
    assert model_details("deepseek:unknown-model")["reasoning"] is None


# -- the manager's picker payload ---------------------------------------------------


def _manager(tmp_path):
    from harness.llm.providers import ModelCapabilities, ProviderClient
    from harness.interface.server import SessionManager

    class _Provider(ProviderClient):
        def complete(self, *, model, messages, tools=None, **s):
            raise NotImplementedError

        def capabilities(self, model):
            return ModelCapabilities()

    return SessionManager(data_dir=tmp_path / "data", provider=_Provider())


def test_models_catalog_lists_only_configured_providers(tmp_path, monkeypatch):
    """A model nobody can call is not a suggestion: a provider with no key contributes
    no rows — including matrix/user-added ids whose provider has no key — and the
    curated id the user already has is always present."""
    mgr = _manager(tmp_path)
    monkeypatch.setattr(mgr, "_provider_configured", lambda name: name == "deepseek")
    monkeypatch.setattr(
        mgr,
        "_curated_models",
        lambda: ["deepseek:deepseek-v4-flash", "gemini:gemini-9-flash"],
    )
    monkeypatch.setattr(mgr, "_ollama_models", lambda: [])
    set_catalog(_FixedBase({}, tmp_path / "c.json"))
    set_models_dev(
        _FixedDev(
            {
                "deepseek:deepseek-v4-flash": {
                    "name": "DeepSeek V4 Flash",
                    "vision": True,
                    "reasoning": True,
                    "context_length": 1_000_000,
                    "cost": {"input": 0.15, "output": 0.6},
                },
                "gemini:gemini-9-flash": {"context_length": 2_000_000},
            },
            tmp_path / "f.json",
        )
    )

    out = mgr.models_catalog()
    rows = {row["id"]: row for row in out["models"]}

    assert "deepseek:deepseek-v4-flash" in rows
    assert not any(mid.startswith("gemini:") for mid in rows), (
        "a curated id whose provider has no key must be filtered like any other"
    )
    row = rows["deepseek:deepseek-v4-flash"]
    assert row["provider_title"] == "DeepSeek"
    assert row["selected"] is True
    assert row["context"] == 1_000_000
    assert row["vision"] is True and row["reasoning"] is True
