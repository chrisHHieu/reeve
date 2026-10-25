"""What a project asks of this machine's plugins (design §7.3, §11.1): declared
plugins that are missing or waiting for this workspace's yes, and marketplaces it suggests.
Nothing is read before the workspace is trusted, and nothing is ever fetched from here."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from fastapi.testclient import TestClient

from harness.extensions.plugins import manager, store
from harness.extensions.plugins.marketplace import add_marketplace
from harness.extensions.plugins.project import project_plugins
from harness.safety.workspace_trust import WorkspaceTrustStore

FIXTURES = Path(__file__).parent / "fixtures" / "plugins"


def _project(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    (ws / ".reeve").mkdir(parents=True)
    (ws / ".claude").mkdir()
    (ws / "tools-market").mkdir()
    (ws / ".reeve" / "plugins.json").write_text(
        json.dumps(
            {
                "enabledPlugins": {"release-helper@acme": True, "formatter@acme": True, "off@acme": False},
                "extraKnownMarketplaces": {"team": {"source": {"source": "directory", "path": "./tools-market"}}},
            }
        )
    )
    (ws / ".claude" / "settings.json").write_text(
        json.dumps(
            {
                "extraKnownMarketplaces": {
                    "acme": {"source": {"source": "github", "repo": "acme/plugins"}},
                    "odd": {"source": {"source": "npm", "package": "x"}},
                }
            }
        )
    )
    return ws


def _market(tmp_path: Path) -> None:
    market = tmp_path / "market"
    (market / ".claude-plugin").mkdir(parents=True)
    (market / ".claude-plugin" / "marketplace.json").write_text(
        json.dumps({"name": "acme", "owner": {"name": "Acme"}, "plugins": [{"name": "release-helper", "source": "./plugins/release-helper"}]})
    )
    shutil.copytree(FIXTURES / "release-helper", market / "plugins" / "release-helper")
    add_marketplace(str(market))


def test_an_untrusted_project_shows_nothing(tmp_path):
    ws = _project(tmp_path)
    assert project_plugins(ws) == {"trusted": False, "plugins": [], "marketplaces": [], "pending": 0}


def test_a_trusted_project_lists_what_is_missing_waiting_and_suggested(tmp_path):
    ws = _project(tmp_path)
    _market(tmp_path)
    manager.confirm_install(manager.stage_install("release-helper@acme")["token"])
    WorkspaceTrustStore().set_trusted(ws, True)

    view = project_plugins(ws)

    # Installed by the person, but a project turning on code needs this workspace's yes.
    assert view["plugins"] == [
        {"id": "formatter@acme", "scope": "project", "state": "missing"},
        {"id": "release-helper@acme", "scope": "project", "state": "needs_consent"},
    ]
    # `acme` is known here already; `team` (a folder named relative to the project) is not;
    # an npm source cannot be added and is left out.
    assert view["marketplaces"] == [
        {"name": "acme", "source": "acme/plugins", "file": ".claude/settings.json", "known": True},
        {"name": "team", "source": str((ws / "tools-market").resolve()), "file": ".reeve/plugins.json", "known": False},
    ]
    assert view["pending"] == 3
    assert store.marketplaces().keys() == {"acme"}  # nothing was added by reading


def test_the_route_reads_the_session_s_workspace(tmp_path):
    from harness.interface.server import SessionManager, create_app

    ws = _project(tmp_path)
    WorkspaceTrustStore().set_trusted(ws, True)
    client = TestClient(create_app(SessionManager(workspace=ws)))
    view = client.get("/v1/plugins/project", params={"workspace": str(ws)}).json()
    assert view["trusted"] is True
    assert [p["state"] for p in view["plugins"]] == ["missing", "missing"]
