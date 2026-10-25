"""Every row of the usage rollup has the provider its model id names (Usage tab, "By provider and model").

Found from the tab showing an "unknown" provider: 35 rows (about 967k tokens) from the off-turn sessions
`__memory__` and `__title__` had `provider = ''`. `record_model_usage` was called with the model only, and the
store did not derive the rest, so a title or memory pass on `zai-coding-plan:glm-5.3` was filed under no provider.
The same shape hid a second case: a model with no prefix (a bare OpenAI id) got `''` from both session folds, though
the router sends it to OpenAI. What has to hold:

  - one rule says which provider a model id was connected through (`provider_of_model`);
  - a write that names no provider gets that one, in the store and in both session folds;
  - rows already stored without a provider are repaired once, merged into the row that exists for the same
    session, day and model, and nothing is lost or counted twice;
  - an explicit provider is never overwritten.
"""

from __future__ import annotations

import sqlite3

from harness.observability.metrics import provider_of_model
from harness.observability.metrics_harness import usage_by_day
from harness.interface.server.manager.metrics_store import MetricsStore


def test_the_provider_is_the_one_the_model_id_names():
    assert provider_of_model("opencode-go:deepseek-v4.1-flash") == "opencode-go"
    assert provider_of_model("openrouter:openai/gpt-6-luna-pro") == "openrouter"
    assert provider_of_model("vertex:gemini/gemini-3.7-flash") == "vertex"
    assert provider_of_model("ollama:qwen2.5:32b") == "ollama"  # only the first colon splits
    assert provider_of_model("gpt-5.6-sol") == "openai"  # the router's default for a bare id
    assert provider_of_model("") == "" and provider_of_model("  ") == ""


def test_an_off_turn_pass_with_no_provider_is_filed_under_its_models_provider(tmp_path):
    store = MetricsStore(tmp_path / "metrics.db")
    try:
        store.record_model_usage("__title__", model="zai-coding-plan:glm-5.3", tokens_in=100, tokens_out=10)
        store.record_model_usage("__memory__", model="vertex:gemini/gemini-3.7-flash", tokens_in=50)
        rows = {r["name"]: r for r in store.report(None)["by_model"]}
        assert rows["zai-coding-plan:glm-5.3"]["provider"] == "zai-coding-plan"
        assert rows["vertex:gemini/gemini-3.7-flash"]["provider"] == "vertex"
        assert {r["provider"] for r in rows.values()} == {"zai-coding-plan", "vertex"}
        assert {row["provider"] for row in store.report(None)["usage_daily"]} == {"zai-coding-plan", "vertex"}
    finally:
        store.close()


def test_an_explicit_provider_is_kept(tmp_path):
    store = MetricsStore(tmp_path / "metrics.db")
    try:
        store.record_model_usage("__title__", provider="acme", model="gpt-5.5", tokens_in=10)
        assert [r["provider"] for r in store.report(None)["by_model"]] == ["acme"]
    finally:
        store.close()


def test_a_session_fold_files_a_bare_model_under_openai():
    events = [
        {"type": "assistant/message", "t": 1_790_000_000, "model": "gpt-5.6-sol", "usage": {"input": 5, "output": 2}},
        {"type": "assistant/message", "t": 1_790_000_000, "model": "opencode-go:mimo-v2.5", "usage": {"input": 1}},
    ]
    assert {row["provider"] for row in usage_by_day(events)} == {"openai", "opencode-go"}


def _seed_orphans(path):
    """A database as an older build left it: rows with no provider, some beside a row that already has one."""
    store = MetricsStore(path)
    store.close()
    conn = sqlite3.connect(path)
    conn.execute("DELETE FROM meta WHERE key = 'model_usage_provider'")
    insert = (
        "INSERT INTO model_usage (session_id, day, provider, model, calls, tokens_in, tokens_out, cache_read, cache_write)"
        " VALUES (?,?,?,?,?,?,?,?,?)"
    )
    conn.execute(insert, ("__title__", "2026-09-30", "", "zai-coding-plan:glm-5.3", 2, 100, 10, 0, 0))
    conn.execute(insert, ("__title__", "2026-09-30", "zai-coding-plan", "zai-coding-plan:glm-5.3", 1, 40, 4, 0, 0))
    conn.execute(insert, ("__memory__", "2026-09-30", "", "vertex:gemini-3.8-flash", 3, 300, 30, 20, 0))
    conn.execute(insert, ("__memory__", "2026-09-30", "", "", 1, 7, 0, 0, 0))  # no model at all: nothing to derive
    conn.commit()
    conn.close()


def test_rows_stored_without_a_provider_are_repaired_once_and_merged(tmp_path):
    path = tmp_path / "metrics.db"
    _seed_orphans(path)
    store = MetricsStore(path)
    try:
        rows = {
            (r["session_id"], r["provider"], r["model"]): r
            for r in store._conn.execute("SELECT * FROM model_usage").fetchall()
        }
        # Merged into the existing row for that session, day and model: sums, not a duplicate.
        merged = rows[("__title__", "zai-coding-plan", "zai-coding-plan:glm-5.3")]
        assert (merged["calls"], merged["tokens_in"], merged["tokens_out"]) == (3, 140, 14)
        assert ("__title__", "", "zai-coding-plan:glm-5.3") not in rows
        # Moved when there was no row to merge into.
        moved = rows[("__memory__", "vertex", "vertex:gemini-3.8-flash")]
        assert (moved["calls"], moved["tokens_in"], moved["cache_read"]) == (3, 300, 20)
        # A row with no model has nothing to derive and stays as it was.
        assert ("__memory__", "", "") in rows
        total = sum(r["tokens_in"] + r["tokens_out"] + r["cache_read"] + r["cache_write"] for r in rows.values())
        assert total == 100 + 10 + 40 + 4 + 300 + 30 + 20 + 7  # nothing lost, nothing counted twice
    finally:
        store.close()

    # Opening it again does not repair again (the marker is set), and the numbers do not move.
    again = MetricsStore(path)
    try:
        assert again._conn.execute("SELECT SUM(tokens_in) FROM model_usage").fetchone()[0] == 100 + 40 + 300 + 7
    finally:
        again.close()


def test_the_report_shows_no_unknown_provider_for_a_model_that_names_one(tmp_path):
    path = tmp_path / "metrics.db"
    _seed_orphans(path)
    store = MetricsStore(path)
    try:
        providers = {r["provider"] for r in store.report(None)["by_model"] if r["name"] != "unknown"}
        assert "" not in providers
        assert providers == {"zai-coding-plan", "vertex"}
    finally:
        store.close()
