"""A feature of the session is ablated the way a person leaves it out: the variant's
workspace config says `[features] remove`, the product's own engine is built from it, and the
baseline counts as having used the feature when the model called one of its tools. Offline:
engines are built, no request is made."""

from __future__ import annotations

import json

import pytest

from benchmarks.ablation import LONG, suites
from benchmarks.harness_eval import (
    ABLATABLE,
    FEATURE_ABLATABLE,
    _Silent,
    _remove_features,
    feature_tools,
    fired,
    product_engine,
    release,
)


def _engine(tmp_path, tag, ablate=frozenset()):
    workspace = tmp_path / tag / "ws"
    workspace.mkdir(parents=True)
    engine = product_engine("fake:model", workspace, tmp_path / tag / "state", tag, ablate, provider=_Silent())
    names = set(engine.registry.names())
    prompt = engine.messages[0]["content"]
    release(engine)
    return names, prompt, workspace


def test_a_feature_variant_is_the_product_with_that_feature_left_out(tmp_path):
    base, base_prompt, _ = _engine(tmp_path, "base")
    off, off_prompt, workspace = _engine(tmp_path, "off", frozenset({"feature:web"}))
    assert {"web_search", "web_fetch"} <= base
    assert base - off == {"web_search", "web_fetch"}
    assert "web_search" in base_prompt and "web_search" not in off_prompt
    # The switch is the person's own: the workspace config, not a hand-made engine.
    assert '[features]\nremove = ["web"]' in (workspace / ".reeve" / "config.toml").read_text(encoding="utf-8")


def test_the_tools_a_feature_brings_are_read_off_the_product_engine():
    assert feature_tools("feature:web") == frozenset({"web_search", "web_fetch"})
    assert "task" in feature_tools("feature:delegation")


def test_a_baseline_used_the_feature_when_the_model_called_its_tools(tmp_path):
    log = tmp_path / "conversations" / "s.events.jsonl"
    log.parent.mkdir(parents=True)
    events = [
        {"type": "tool/call", "name": "web_search"},
        {"type": "tool/call", "name": "read_file"},
        {"type": "tool/call", "name": "web_fetch"},
    ]
    log.write_text("\n".join(json.dumps(event) for event in events), encoding="utf-8")
    assert fired(tmp_path, "feature:web") == 2
    assert fired(tmp_path, "feature:lsp") == 0


def test_a_workspace_that_already_says_which_features_is_refused(tmp_path):
    config = tmp_path / ".reeve" / "config.toml"
    config.parent.mkdir()
    config.write_text('model = "x"', encoding="utf-8")
    _remove_features(tmp_path, frozenset({"feature:lsp", "todo_nudge"}))
    assert config.read_text(encoding="utf-8") == 'model = "x"\n[features]\nremove = ["lsp"]\n'
    with pytest.raises(ValueError):
        _remove_features(tmp_path, frozenset({"feature:web"}))


def test_features_run_on_the_long_session_and_only_when_named():
    assert suites(["feature:web"]) == {"feature:web": LONG}
    assert not set(FEATURE_ABLATABLE) & set(ABLATABLE)
    assert "feature:web" in FEATURE_ABLATABLE and len(FEATURE_ABLATABLE) == 16
