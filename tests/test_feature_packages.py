"""A package replaces a built-in feature or adds one through the `reeve.features` entry points,
with no edit to `FEATURES`: same id replaces in place, a new id goes after the one it
names (else before `discovery`, which stays last), and a broken package is left out."""

from __future__ import annotations

import importlib
import logging

from harness.compose import build_engine
from harness.compose import features as features_module
from harness.compose.features import FEATURES, FEATURE_IDS, session_features
from harness.compose.features.installed import merge
from harness.modes.agents.reeve import reeve_agent

# `features.installed` is also the name of the function the package exports.
installed_module = importlib.import_module("harness.compose.features.installed")


class _Named:
    def __init__(self, id, after=None):
        self.id = id
        if after is not None:
            self.after = after

    def setup(self, ctx):
        pass


def _ids(features):
    return [feature.id for feature in features]


def test_a_feature_with_a_built_in_id_replaces_it_in_its_place():
    merged = merge(FEATURES, [_Named("web")])
    assert _ids(merged) == list(FEATURE_IDS)
    assert isinstance(merged[FEATURE_IDS.index("web")], _Named)


def test_a_new_feature_goes_where_it_asks_and_discovery_stays_last(caplog):
    merged = _ids(merge(FEATURES, [_Named("hello", after="web"), _Named("plain"), _Named("late", after="discovery")]))
    assert merged[merged.index("web") + 1] == "hello"
    assert merged[-1] == "discovery"
    assert merged[-3:] == ["plain", "late", "discovery"]
    with caplog.at_level(logging.WARNING):
        lost = _ids(merge(FEATURES, [_Named("lost", after="no-such")]))
    assert lost[-2:] == ["lost", "discovery"]
    assert any("no-such" in record.message for record in caplog.records)


def test_the_first_of_two_packages_with_one_id_is_kept():
    first, second = _Named("web"), _Named("web")
    merged = merge(FEATURES, [first, second])
    assert merged[FEATURE_IDS.index("web")] is first
    assert _ids(merge(FEATURES, [_Named("x"), _Named("x")])).count("x") == 1


class _Entry:
    def __init__(self, name, load):
        self.name, self.value, self._load = name, f"pkg:{name}", load

    def load(self):
        return self._load()


class _Good:
    id = "good"

    def setup(self, ctx):
        pass


def _boom():
    raise ImportError("missing dependency")


def test_a_package_that_fails_to_load_is_left_out(monkeypatch, caplog):
    monkeypatch.setattr(installed_module, "_entry_points", lambda: [
        _Entry("good", lambda: _Good),
        _Entry("broken", _boom),
        _Entry("shapeless", lambda: object()),
    ])
    installed_module.installed.cache_clear()
    try:
        with caplog.at_level(logging.WARNING):
            loaded = installed_module.installed()
        # A class is instantiated; the error and the object with no id/setup are logged, not raised.
        assert len(loaded) == 1 and type(loaded[0]) is _Good
        assert any("broken" in record.message for record in caplog.records)
        assert any("shapeless" in record.message for record in caplog.records)
    finally:
        installed_module.installed.cache_clear()


def hello_tool(name: str) -> str:
    """Say hello to someone by name."""
    return f"hello {name}"


class _CompanyWeb:
    id = "web"

    def setup(self, ctx):
        def web_search(query: str) -> str:
            """Search the web with the company's own engine."""
            return query

        ctx.registry.register(web_search)
        ctx.approval_extra("web_search", lambda args: {"search_provider": "company"})


class _Hello:
    id = "hello"
    after = "web"

    def setup(self, ctx):
        ctx.registry.register(hello_tool)
        ctx.prompt(30, lambda: "Greet people with hello_tool." if ctx.registry.get("hello_tool") else "")


def _engine(tmp_path, monkeypatch, remove=None):
    monkeypatch.setattr(features_module, "installed", lambda: (_CompanyWeb(), _Hello()))
    ws = tmp_path / "ws"
    ws.mkdir(parents=True)
    if remove:
        (ws / ".reeve").mkdir()
        (ws / ".reeve" / "config.toml").write_text(f'[features]\nremove = ["{remove}"]\n', encoding="utf-8")
    return build_engine(agent=reeve_agent(), workspace=ws, model="openai:gpt-5.6-sol", session_id="s1",
                        question_asker=object())


def test_a_session_runs_the_packages_features_in_their_places(tmp_path, monkeypatch):
    engine = _engine(tmp_path, monkeypatch)
    names = engine.registry.names()
    # The company's web replaced the built-in: its search, no `web_fetch`, its approval extra.
    assert engine.registry.get("web_search").func.__doc__.startswith("Search the web with the company")
    assert "web_fetch" not in names
    assert engine.approval_extras("web_search", {}) == {"search_provider": "company"}
    # The new feature sits right after the web's tool, before `ask_user`, and brings its words.
    assert names.index("hello_tool") == names.index("web_search") + 1
    assert names.index("ask_user") > names.index("hello_tool")
    assert "Greet people with hello_tool." in engine.messages[0]["content"]
    # The prompt names no tool the replacement dropped.
    assert "web_fetch" not in engine.messages[0]["content"]
    assert [feature.id for feature in session_features()].count("web") == 1


def test_a_packages_feature_is_removed_like_a_built_in(tmp_path, monkeypatch):
    engine = _engine(tmp_path, monkeypatch, remove="hello")
    assert engine.registry.get("hello_tool") is None
    assert "Greet people" not in engine.messages[0]["content"]
