"""The per-session connections surface, with one agent.

The per-session connections endpoints, exercised through ``TestClient(create_app(mgr))``.
Connectors are "connected" by writing their secret profile directly (no network). (The persona
detail, default-connection and enable endpoints went with personas.)
"""

from fastapi.testclient import TestClient

from harness.llm.providers import ModelCapabilities, ProviderClient
from harness.interface.server import create_app
from harness.interface.server.manager import SessionManager
from harness.session.sessions import SessionRecord


class ScriptedProvider(ProviderClient):
    def __init__(self, turns=None):
        self._turns = list(turns or [])

    def complete(self, *, model, messages, tools=None, **settings):
        return self._turns.pop(0)

    def capabilities(self, model):
        return ModelCapabilities()


def _mgr(tmp_path, monkeypatch) -> SessionManager:
    # Isolate the SecretStore (which is otherwise the machine-global state dir) so a connector the
    # developer happens to have connected locally can't leak into "is it connected?" assertions.
    monkeypatch.setenv("REEVE_STATE_DIR", str(tmp_path / "state"))
    return SessionManager(workspace=tmp_path, provider=ScriptedProvider([]))


def _connect_github(mgr) -> None:
    mgr.secrets.put("github:default", {"token": "ghp_test", "enabled": True})


def _connect_slack(mgr) -> None:
    mgr.secrets.put(
        "slack:default",
        {"bot_token": "xoxb-test", "app_token": "xapp-test", "enabled": True},
    )


def _ops_session(mgr, session_id: str) -> None:
    # Recorded under a persona that no longer exists: it runs on the one agent.
    mgr.session_store.save(
        SessionRecord(
            session_id=session_id,
            workspace=str(mgr.default_workspace),
            model="gpt-5.5",
            mode="interactive",
            agent="ops",
        )
    )


def test_session_connections_endpoint(tmp_path, monkeypatch):
    mgr = _mgr(tmp_path, monkeypatch)
    _connect_github(mgr)
    _connect_slack(mgr)
    _ops_session(mgr, "incident")
    mgr.subscriptions.subscribe("incident", "slack:C123")  # drives the detail string
    client = TestClient(create_app(mgr))

    view = client.get("/v1/sessions/incident/connections").json()
    conn = {c["connector"]: c for c in view["connected"]}
    # github + slack connected, and on: nothing has turned them off
    assert {"github", "slack"} <= set(conn)
    assert conn["github"]["enabled"] is True
    # slack's detail surfaces the subscribed channel id
    assert "C123" in conn["slack"]["detail"]

    # Nothing recommends a connector any more: that was a persona's manifest.
    assert view["recommended"] == []
    assert view["attention"] == 0


def test_session_set_override(tmp_path, monkeypatch):
    mgr = _mgr(tmp_path, monkeypatch)
    _connect_slack(mgr)
    _connect_github(mgr)
    _ops_session(mgr, "s1")
    client = TestClient(create_app(mgr))

    # slack starts effective (connected, nothing turned it off)
    assert "slack" in mgr.effective_connectors("s1")
    before = {
        c["connector"]
        for c in client.get("/v1/sessions/s1/connections").json()["connected"]
    }
    assert "slack" in before

    # mute slack for this session
    resp = client.post(
        "/v1/sessions/s1/connections", json={"connector": "slack", "enabled": False}
    ).json()
    assert resp["ok"] is True
    assert mgr.session_connections.get("s1") == {"slack": False}
    assert "slack" not in mgr.effective_connectors("s1")
    # a muted connector stays VISIBLE in the drawer as toggled-off (owner finding
    # 2026-07-03: "where did Slack go?") — both in the returned view and a fresh GET
    view_conn = {c["connector"]: c for c in resp["connections"]["connected"]}
    assert view_conn["slack"]["enabled"] is False
    fresh = {
        c["connector"]: c
        for c in client.get("/v1/sessions/s1/connections").json()["connected"]
    }
    assert fresh["slack"]["enabled"] is False

    # clear → back to the default (slack on again)
    resp2 = client.post(
        "/v1/sessions/s1/connections", json={"connector": "slack", "clear": True}
    ).json()
    assert resp2["ok"] is True
    assert mgr.session_connections.get("s1") == {}
    assert "slack" in mgr.effective_connectors("s1")
    assert "slack" in {c["connector"] for c in resp2["connections"]["connected"]}


def test_declared_connector_allowlist_gates_session_tools(tmp_path):
    """Owner-hit 2026-08-15: a security agent declaring only [code tools]
    had browser_read_page in-session, because `connectors: true` exposed EVERY connected
    connector. The grant is now declared ∩ connected — an undeclared connector's tools
    never enter the session, regardless of what the user has connected."""
    from harness.compose import build_engine
    from harness.modes.agents.base import Agent
    from harness.integrations.connectors import connect_connector
    from harness.safety.secrets import SecretStore

    secrets = SecretStore(tmp_path / "secrets.json")
    for name, fields in (
        ("redmine", {"base_url": "https://r.example", "api_key": "k"}),
        ("figma", {"access_token": "figtok"}),
    ):
        assert connect_connector(secrets, name, fields, validate=False)["ok"] is True

    def names_for(connectors):
        agent = Agent(
            name="p", title="P", system_prompt="x", connectors=connectors
        )
        engine = build_engine(agent=agent, workspace=tmp_path, secrets=secrets)
        return set(engine.registry.names())

    scoped = names_for(("redmine",))
    assert any(n.startswith("redmine_") for n in scoped)
    assert not any(n.startswith("figma_") for n in scoped)

    general = names_for(True)  # the `all` sentinel: general builtins only
    assert any(n.startswith("redmine_") for n in general)
    assert any(n.startswith("figma_") for n in general)

    none = names_for(False)
    assert not any(n.startswith(("redmine_", "figma_")) for n in none)
