"""Several accounts for any connector (`connectors/accounts`): the one implementation the later
connectors share, the same model Gmail and Calendar have. Each account is kept at
`<connector>:account:<id>`, `:default` only says which one is the default, and a profile from before
accounts becomes one the first time it is touched.
"""

from __future__ import annotations

import pytest

from harness.integrations.connectors import accounts, descriptors
from harness.integrations.connectors.descriptors import ConnectorDescriptor, Field, ValidationResult
from harness.integrations.connectors.lifecycle import connect_connector, connector_list, disconnect_connector
from harness.safety.secrets import SecretStore


def _descriptor(name="acmeapp", account_field="project_id", fields=None):
    return ConnectorDescriptor(
        name=name, title="AcmeApp", icon="◇", blurb="test connector", auth="api_token", two_way=False,
        fields=fields or [Field("api_key", "API key", secret=True), Field("project_id", "Project ID", secret=False)],
        instructions=[], validate=lambda creds: ValidationResult(True, identity="acme@example.com"), account_field=account_field,
    )


def _registered(descriptor):
    descriptors.register_descriptor(descriptor)
    return descriptor


def _unregistered(descriptor):
    descriptors.DESCRIPTORS.remove(descriptor)
    descriptors._BY_NAME.pop(descriptor.name, None)


@pytest.fixture
def acme():
    """A connector with accounts, there for one test."""
    descriptor = _registered(_descriptor())
    yield descriptor
    _unregistered(descriptor)


@pytest.fixture
def secrets(tmp_path):
    return SecretStore(tmp_path / "secrets.json")


def _ids(secrets):
    return [account_id for account_id, _ in accounts.list_accounts(secrets, "acmeapp")]


def _two(secrets):
    for n in (1, 2):
        accounts.add_account(secrets, "acmeapp", f"p{n}", {"api_key": f"k{n}"})


def _row(secrets):
    return next(c for c in connector_list(secrets) if c["name"] == "acmeapp")


def test_accounts_are_kept_apart_and_the_first_is_the_default_until_moved(acme, secrets):
    assert accounts.add_account(secrets, "acmeapp", "p1", {"api_key": "k1"})["ok"]
    accounts.add_account(secrets, "acmeapp", "p2", {"api_key": "k2"})
    assert _ids(secrets) == ["p1", "p2"] and accounts.default_account(secrets, "acmeapp") == "p1"
    account_id, key, profile = accounts.resolve(secrets, "acmeapp", "p2")
    assert (account_id, key, profile["api_key"]) == ("p2", "acmeapp:account:p2", "k2")
    account_id, _, profile = accounts.resolve(secrets, "acmeapp")  # none named: the default
    assert account_id == "p1" and profile["api_key"] == "k1"
    assert accounts.resolve(secrets, "acmeapp", "nope")[2] is None
    assert "api_key" not in (secrets.get("acmeapp:default") or {})  # the pointer holds no credentials
    assert accounts.set_default(secrets, "acmeapp", "p2")["ok"] and accounts.default_account(secrets, "acmeapp") == "p2"
    assert not accounts.set_default(secrets, "acmeapp", "ghost")["ok"]


def test_the_default_moves_when_its_account_leaves_and_goes_with_the_last(acme, secrets):
    _two(secrets)
    assert accounts.disconnect_account(secrets, "acmeapp", "p1")["ok"]
    assert accounts.default_account(secrets, "acmeapp") == "p2"
    last = accounts.disconnect_account(secrets, "acmeapp", "p2")
    assert last["ok"] and last["remaining_accounts"] == 0
    assert secrets.get("acmeapp:default") is None


def test_a_profile_from_before_accounts_becomes_one_when_first_touched(acme, secrets):
    # nothing for the person to do, as with Gmail and Calendar
    secrets.put("acmeapp:default", {"type": "token", "enabled": True, "api_key": "old", "project_id": "42"})
    assert _ids(secrets) == ["42"]
    assert accounts.resolve(secrets, "acmeapp")[2]["api_key"] == "old"
    pointer = secrets.get("acmeapp:default")
    assert pointer["default_account"] == "42" and "api_key" not in pointer


def test_connecting_with_a_new_id_adds_an_account_and_with_a_known_one_updates_it(acme, secrets):
    first = connect_connector(secrets, "acmeapp", {"api_key": "k1", "project_id": "11"}, validate=False)
    assert first["ok"] and first["account_id"] == "11"
    for key in ("k2", "k2b"):
        connect_connector(secrets, "acmeapp", {"api_key": key, "project_id": "22"}, validate=False)
    assert _ids(secrets) == ["11", "22"]
    assert accounts.resolve(secrets, "acmeapp", "22")[2]["api_key"] == "k2b"
    assert accounts.default_account(secrets, "acmeapp") == "11"


def test_an_account_can_be_named_by_who_the_validator_says_it_is(secrets):
    descriptor = _registered(_descriptor(name="acmemail", account_field=accounts.IDENTITY, fields=[Field("api_key", "API key", secret=True)]))
    try:
        connected = connect_connector(secrets, "acmemail", {"api_key": "k"})  # validated
        assert connected["ok"] and connected["account_id"] == "acme@example.com"
    finally:
        _unregistered(descriptor)


def test_the_connector_row_lists_the_accounts_and_a_full_disconnect_empties_it(acme, secrets):
    accounts.add_account(secrets, "acmeapp", "p1", {"api_key": "k1", "account": "Proj One"})
    accounts.add_account(secrets, "acmeapp", "p2", {"api_key": "k2"})
    row = _row(secrets)
    assert row["connected"] and row["enabled"]
    assert row["accounts"] == [{"account_id": "p1", "name": "Proj One", "default": True}, {"account_id": "p2", "name": "p2", "default": False}]
    assert row["account"] == "Proj One"  # the default's name
    assert disconnect_connector(secrets, "acmeapp")["ok"]
    assert accounts.list_accounts(secrets, "acmeapp") == [] and not _row(secrets)["connected"]


def test_the_account_routes_serve_connectors_with_accounts_and_refuse_the_rest(acme, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from harness.interface.server.app import create_app
    from harness.interface.server.manager import SessionManager
    from harness.llm.providers import ModelCapabilities, ProviderClient

    class _NoTurns(ProviderClient):
        def complete(self, *, model, messages, tools=None, **settings):
            raise AssertionError("no turns expected")

        def capabilities(self, model):
            return ModelCapabilities()

    monkeypatch.setenv("REEVE_STATE_DIR", str(tmp_path / "state"))
    manager = SessionManager(workspace=tmp_path, provider=_NoTurns())
    _two(manager.secrets)
    client = TestClient(create_app(manager))

    def route(connector, account, action):
        return client.post(f"/v1/connectors/{connector}/accounts/{account}/{action}").json()

    assert route("acmeapp", "p2", "default")["ok"]
    assert accounts.default_account(manager.secrets, "acmeapp") == "p2"
    assert route("acmeapp", "p1", "disconnect")["ok"]
    assert _ids(manager.secrets) == ["p2"]
    refused = route("linear", "x", "default")
    assert not refused["ok"] and "not a multi-account" in refused["error"]
