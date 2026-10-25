"""Model fallback chain + per-route cooldown.

A session has one route; the retry taxonomy re-sends to the SAME model, so an out-of-quota
account (zero retries, by design) and a rate-limited route (four attempts, then death)
both end the turn. With a configured chain the failed route is cooled and the turn
continues on the next one. Pinned here:

- which failures are a ROUTE problem (and the two deliberate exclusions);
- the cooldown ladder (60s·2ⁿ, capped, reset on success) and the chain walk;
- the in-turn switch: model field, `llm/fallback` row, `model_fallback` event, the
  persisted `model_switch` notice, patience-before-fallback for rate limits;
- the paths that must NOT change: no chain, a content-policy failure, an exhausted chain;
- config normalization and the global-only rule.
"""

from __future__ import annotations

import asyncio

import pytest

from harness.config.config import load_config
from harness.loop import TurnEngine
from harness.session.events import EventType
from harness.safety.permissions import Mode, PermissionEngine
from harness.llm.providers import AssistantTurn, ModelCapabilities, ProviderClient
from harness.llm.providers.fallback import RouteCooldown, cooldown_seconds, fallback_eligible, next_route
from harness.session.session_log import SessionLog
from harness.tools import ToolRegistry

PRIMARY = "reeve:primary"
BACKUP = "reeve:backup"


class _Response:
    def __init__(self, headers):
        self.headers = headers


class RateLimitError(Exception):
    """Named so the taxonomy sees a throttle by class, like the SDKs' own."""

    def __init__(self, message="429 too many requests", headers=None):
        super().__init__(message)
        self.response = _Response(headers or {})


class _Scripted(ProviderClient):
    """Raises or answers the items in order; text when the script runs out."""

    def __init__(self, script):
        self._script = list(script)

    def complete(self, *, model, messages, tools=None, **settings):
        item = (
            self._script.pop(0)
            if self._script
            else AssistantTurn(text="done", finish_reason="stop")
        )
        if isinstance(item, BaseException):
            raise item
        return item

    def capabilities(self, model):
        return ModelCapabilities()


def _engine(tmp_path, provider, chain=(), cooldown=None) -> TurnEngine:
    permissions = PermissionEngine(workspace_root=tmp_path)
    permissions.mode = Mode.WORKSPACE_WRITE
    engine = TurnEngine(
        provider=provider,
        registry=ToolRegistry(),
        permissions=permissions,
        model=PRIMARY,
        model_fallback=list(chain),
        fallback_cooldown=cooldown or RouteCooldown(),
        max_iterations=8,
    )
    engine.log = SessionLog(tmp_path / "events.jsonl")
    return engine


def _run(engine):
    async def go():
        return [event async for event in engine.run("hello")]

    return asyncio.run(go())


def _rows(engine, kind):
    return [event for event in engine.log.read() if event["type"] == kind]


@pytest.fixture
def fast_backoff(monkeypatch):
    """The retry ladder's real waits are 0.5s/1.5s — irrelevant to what is pinned here."""
    from harness.loop import step_recovery

    monkeypatch.setattr(step_recovery, "BACKOFF_SECONDS", (0.01, 0.01))


# -- eligibility -----------------------------------------------------------------------


def test_fallback_eligibility_is_route_problems_only():
    assert fallback_eligible(RateLimitError())
    assert fallback_eligible(Exception("insufficient_quota: out of credits"))
    assert fallback_eligible(
        Exception("The model does not exist or you do not have access to it")
    )
    assert fallback_eligible(Exception("upstream overloaded"))
    assert fallback_eligible(Exception("connection reset by peer"))
    # Deliberately excluded: a different censorship answer is a policy choice, and an
    # unrecognized failure keeps the retry classifier's conservative default.
    assert not fallback_eligible(Exception("content policy violation"))
    assert not fallback_eligible(ValueError("what even is this"))


# -- the cooldown table ------------------------------------------------------------------


def test_cooldown_escalates_caps_and_resets():
    now = [1_000.0]
    cd = RouteCooldown(clock=lambda: now[0])
    assert cd.cool("a") == 60.0
    assert cd.cool("a") == 120.0
    assert cd.cool("a") == 240.0
    assert cd.remaining("a") > 0
    cd.reset("a")
    assert cd.remaining("a") == 0.0
    now[0] += 1.0
    cd.cool("b", 10)
    now[0] += 11
    assert cd.remaining("b") == 0.0


def test_cooldown_seconds_by_class():
    assert cooldown_seconds(Exception("insufficient_quota")) == 4 * 3600.0
    assert cooldown_seconds(Exception("connection reset")) == 30.0
    assert cooldown_seconds(Exception("content policy violation")) is None
    assert cooldown_seconds(RateLimitError(headers={"retry-after": "120"})) == 120.0


def test_next_route_skips_current_cooling_and_blanks():
    cd = RouteCooldown()
    assert next_route("a", ["a", "b", "c"], cd) == "b"
    cd.cool("b", 60)
    assert next_route("a", ["a", "b", "c"], cd) == "c"
    cd.cool("c", 60)
    assert next_route("a", ["a", "b", "c"], cd) is None
    assert next_route("a", ["", "a"], cd) is None


# -- the engine switch -------------------------------------------------------------------


def test_a_rate_limited_route_falls_back_after_its_retries(tmp_path, fast_backoff):
    provider = _Scripted(
        [RateLimitError()] * 5 + [AssistantTurn(text="answered", finish_reason="stop")]
    )
    engine = _engine(tmp_path, provider, chain=[BACKUP])
    events = _run(engine)

    assert engine.model == BACKUP
    # Patience first: the class's four retries, then the switch.
    assert len(_rows(engine, "llm/retry")) == 4
    fallbacks = [event for event in events if event.type is EventType.FALLBACK]
    assert len(fallbacks) == 1
    assert fallbacks[0].data["from"] == PRIMARY and fallbacks[0].data["to"] == BACKUP
    assert fallbacks[0].data["reason"] == "rate_limit"
    row = _rows(engine, "llm/fallback")[0]
    assert row["from"] == PRIMARY and row["to"] == BACKUP
    # The user reads the switch in the transcript, through the established shape.
    assert any(
        m.get("kind") == "model_switch" for m in engine.messages if isinstance(m, dict)
    )
    assert any(
        m.get("role") == "assistant" and m.get("content") == "answered"
        for m in engine.messages
    )


def test_an_out_of_quota_route_falls_back_immediately(tmp_path):
    provider = _Scripted(
        [Exception("insufficient_quota: out of credits"), AssistantTurn(text="ok", finish_reason="stop")]
    )
    engine = _engine(tmp_path, provider, chain=[BACKUP])
    _run(engine)

    assert engine.model == BACKUP
    assert not _rows(engine, "llm/retry")
    row = _rows(engine, "llm/fallback")[0]
    assert row["reason"] == "no_quota" and row["cooldown"] == 4 * 3600.0


def test_a_long_retry_after_cools_the_route_and_falls_back(tmp_path, fast_backoff):
    provider = _Scripted(
        [
            RateLimitError(headers={"retry-after": "300"}),
            AssistantTurn(text="ok", finish_reason="stop"),
        ]
    )
    engine = _engine(tmp_path, provider, chain=[BACKUP])
    _run(engine)

    assert engine.model == BACKUP
    assert not _rows(engine, "llm/retry")  # nothing retried inside the wait window
    assert _rows(engine, "llm/fallback")[0]["cooldown"] == 300.0


def test_a_cooling_route_is_skipped(tmp_path):
    cd = RouteCooldown()
    cd.cool(BACKUP, 60)
    provider = _Scripted(
        [Exception("insufficient_quota"), AssistantTurn(text="ok", finish_reason="stop")]
    )
    engine = _engine(tmp_path, provider, chain=[BACKUP, "reeve:third"], cooldown=cd)
    _run(engine)

    assert engine.model == "reeve:third"


def test_an_exhausted_chain_surfaces_the_error(tmp_path):
    provider = _Scripted([Exception("insufficient_quota")] * 2)
    engine = _engine(tmp_path, provider, chain=[BACKUP])
    events = _run(engine)

    assert engine.model == BACKUP
    assert len(_rows(engine, "llm/fallback")) == 1
    assert [event for event in events if event.type is EventType.ERROR]


def test_a_content_policy_failure_does_not_switch_models(tmp_path):
    provider = _Scripted([Exception("content policy violation")])
    engine = _engine(tmp_path, provider, chain=[BACKUP])
    events = _run(engine)

    assert engine.model == PRIMARY
    assert not [event for event in events if event.type is EventType.FALLBACK]
    assert [event for event in events if event.type is EventType.ERROR]


def test_no_chain_keeps_the_error_path(tmp_path):
    provider = _Scripted([Exception("insufficient_quota")])
    engine = _engine(tmp_path, provider)
    events = _run(engine)

    assert engine.model == PRIMARY
    assert not [event for event in events if event.type is EventType.FALLBACK]
    assert [event for event in events if event.type is EventType.ERROR]


# -- config ------------------------------------------------------------------------------


def test_config_normalizes_the_chain(tmp_path):
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text('fallback_models = ["a", "", "a", "b"]\n', encoding="utf-8")
    assert load_config(global_path=cfg_path).fallback_models == ["a", "b"]


def test_a_workspace_cannot_add_a_fallback_route(tmp_path):
    """Global-only, like `allowed_domains`: a cloned repository must not redirect model
    traffic when the primary fails."""
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text('fallback_models = ["a"]\n', encoding="utf-8")
    ws = tmp_path / "ws"
    (ws / ".reeve").mkdir(parents=True)
    (ws / ".reeve" / "config.toml").write_text(
        'fallback_models = ["evil:model"]\n', encoding="utf-8"
    )
    assert load_config(ws, global_path=cfg_path).fallback_models == ["a"]


def test_build_engine_passes_the_chain_through(tmp_path):
    from harness.compose import build_engine
    from harness.modes.agents.chat import chat_agent

    engine = build_engine(
        agent=chat_agent(),
        workspace=tmp_path,
        provider=object(),
        model_fallback=[BACKUP],
        fallback_cooldown=RouteCooldown(),
    )
    assert engine.model_fallback == [BACKUP]
