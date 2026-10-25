"""Plugins, phase P4: withdrawn plugins and updates that apply themselves (design
§6.1, §8.4). Offline: the marketplace is a local folder and the plugin a local git
repository reached by a file:// URL.

Pinned: a plugin its marketplace stops listing stops loading until a person keeps it;
auto-update is off until turned on per marketplace, and even then applies only an update
whose code the person already agreed to — a code change waits for a person.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from fastapi.testclient import TestClient

from harness.extensions.plugins import manager, resolve_plugins, store, updates
from harness.extensions.plugins.marketplace import add_marketplace, refresh_marketplace
from harness.platform.spawn import run

FIXTURES = Path(__file__).parent / "fixtures" / "plugins"
GIT = ["git", "-c", "user.email=t@example.com", "-c", "user.name=T"]


def _write_market(root: Path, plugins: list[dict]) -> None:
    (root / ".claude-plugin").mkdir(parents=True, exist_ok=True)
    (root / ".claude-plugin" / "marketplace.json").write_text(
        json.dumps({"name": "acme", "owner": {"name": "Acme"}, "plugins": plugins}), encoding="utf-8"
    )


def _commit(repo: Path, message: str) -> None:
    for argv in (GIT + ["add", "."], GIT + ["commit", "-q", "-m", message]):
        assert run(argv, cwd=str(repo), capture_output=True).returncode == 0


def _git_plugin(tmp_path: Path) -> Path:
    """release-helper (it has a hook, so it runs code) in a repo, versioned by commit."""
    repo = tmp_path / "repo"
    shutil.copytree(FIXTURES / "release-helper", repo)
    manifest = repo / ".claude-plugin" / "plugin.json"
    data = json.loads(manifest.read_text())
    data.pop("version")
    manifest.write_text(json.dumps(data))
    assert run(["git", "init", "-q"], cwd=str(repo), capture_output=True).returncode == 0
    _commit(repo, "one")
    market = tmp_path / "market"
    _write_market(market, [{"name": "release-helper", "source": {"source": "url", "url": repo.as_uri()}}])
    add_marketplace(str(market))
    manager.confirm_install(manager.stage_install("release-helper@acme")["token"])
    return repo


def _ids(plugins) -> list[str]:
    return [p.id for p in plugins.plugins]


# -- withdrawn --------------------------------------------------------------------------------


def _bare_market(tmp_path: Path, listed: bool = True) -> Path:
    market = tmp_path / "market"
    skill = market / "plugins" / "bare" / "skills" / "tidy"
    skill.mkdir(parents=True, exist_ok=True)
    (skill / "SKILL.md").write_text("---\nname: tidy\ndescription: Tidy.\n---\nTidy.\n")
    _write_market(market, [{"name": "bare", "source": "./plugins/bare"}] if listed else [])
    return market


def test_a_plugin_its_marketplace_withdraws_stops_loading_until_kept(tmp_path):
    add_marketplace(str(_bare_market(tmp_path)))
    manager.confirm_install(manager.stage_install("bare@acme")["token"])
    assert _ids(resolve_plugins(workspace=tmp_path)) == ["bare@acme"]

    _bare_market(tmp_path, listed=False)
    assert refresh_marketplace("acme")["flagged"] == ["bare@acme"]

    withdrawn = resolve_plugins(workspace=tmp_path)
    assert _ids(withdrawn) == []
    assert "no longer lists it" in withdrawn.errors[0]["error"]
    assert store.list_installed()[0]["flagged"]["marketplace"] == "acme"

    updates.keep_flagged("bare@acme")
    assert _ids(resolve_plugins(workspace=tmp_path)) == ["bare@acme"]
    # A second refresh does not flag it again, and listing it again clears the mark.
    assert refresh_marketplace("acme")["flagged"] == []
    _bare_market(tmp_path, listed=True)
    refresh_marketplace("acme")
    assert store.flagged() == {}


def test_uninstalling_a_withdrawn_plugin_clears_its_mark(tmp_path):
    add_marketplace(str(_bare_market(tmp_path)))
    manager.confirm_install(manager.stage_install("bare@acme")["token"])
    _bare_market(tmp_path, listed=False)
    refresh_marketplace("acme")
    manager.uninstall("bare@acme")
    assert store.flagged() == {}


# -- auto-update ------------------------------------------------------------------------------


def test_auto_update_is_off_until_a_person_turns_it_on(tmp_path):
    repo = _git_plugin(tmp_path)
    before = store.install_record("release-helper@acme")["version"]
    (repo / "README.md").write_text("more\n")
    _commit(repo, "two")

    assert updates.auto_update() == {"updated": [], "waiting": [], "flagged": [], "errors": []}
    assert store.install_record("release-helper@acme")["version"] == before


def test_an_update_that_runs_the_same_code_applies_itself(tmp_path):
    repo = _git_plugin(tmp_path)
    before = store.install_record("release-helper@acme")["version"]
    extra = repo / "skills" / "notes"
    extra.mkdir()
    (extra / "SKILL.md").write_text("---\nname: notes\ndescription: Notes.\n---\nx\n")
    _commit(repo, "two")
    updates.set_auto_update("acme", True)

    report = updates.auto_update()

    assert [u["id"] for u in report["updated"]] == ["release-helper@acme"]
    after = store.install_record("release-helper@acme")["version"]
    assert after != before
    plugin = resolve_plugins(workspace=tmp_path).plugins[0]
    assert plugin.code == "running" and plugin.components["skills"] == 2


def test_an_update_that_changes_the_code_waits_for_a_person(tmp_path):
    repo = _git_plugin(tmp_path)
    before = store.install_record("release-helper@acme")["version"]
    (repo / "scripts" / "guard.py").write_text("import sys; sys.exit(0)\n")
    _commit(repo, "two")
    updates.set_auto_update("acme", True)

    report = updates.auto_update()

    assert report["updated"] == []
    assert report["waiting"] == [{"id": "release-helper@acme", "reason": "the code it runs changed"}]
    assert store.install_record("release-helper@acme")["version"] == before
    assert store.list_installed()[0]["update_waiting"]["reason"] == "the code it runs changed"
    assert resolve_plugins(workspace=tmp_path).plugins[0].code == "running"  # the old code, as agreed
    # The person's own update clears it.
    manager.confirm_install(manager.stage_update("release-helper@acme")["token"])
    assert store.waiting_updates() == {}


def test_no_auto_update_when_nonessential_traffic_is_off(tmp_path, monkeypatch):
    repo = _git_plugin(tmp_path)
    before = store.install_record("release-helper@acme")["version"]
    (repo / "README.md").write_text("more\n")
    _commit(repo, "two")
    updates.set_auto_update("acme", True)
    monkeypatch.setenv(updates.NONESSENTIAL_ENV, "1")
    updates.auto_update()
    assert store.install_record("release-helper@acme")["version"] == before


def test_the_api_turns_auto_update_on_and_keeps_a_withdrawn_plugin(tmp_path):
    from harness.interface.server import SessionManager, create_app

    add_marketplace(str(_bare_market(tmp_path)))
    manager.confirm_install(manager.stage_install("bare@acme")["token"])
    client = TestClient(create_app(SessionManager(workspace=tmp_path)))

    assert client.patch("/v1/plugin-marketplaces/acme", json={"autoUpdate": True}).json()["autoUpdate"] is True
    assert store.marketplaces()["acme"]["autoUpdate"] is True
    assert client.post("/v1/plugins/bare@acme/keep").status_code == 400  # not withdrawn

    _bare_market(tmp_path, listed=False)
    client.post("/v1/plugin-marketplaces/acme/refresh")
    view = client.get("/v1/plugins").json()
    assert view["installed"][0]["flagged"]["marketplace"] == "acme"
    assert client.post("/v1/plugins/bare@acme/keep").json() == {"id": "bare@acme", "kept": True}
    assert [p["id"] for p in client.get("/v1/plugins").json()["plugins"]] == ["bare@acme"]


async def test_the_server_runs_a_pass_only_while_idle_and_stops_with_it(tmp_path, monkeypatch):
    import asyncio

    from harness.interface.server import SessionManager
    from harness.interface.server.manager import plugins as loop

    passes: list[int] = []
    monkeypatch.setattr(loop, "PLUGIN_UPDATE_FIRST_DELAY", 0.0)
    monkeypatch.setattr(loop, "PLUGIN_UPDATE_BUSY_RETRY", 0.01)
    monkeypatch.setattr(updates, "auto_update", lambda **_: passes.append(1) or {"updated": []})
    manager = SessionManager(workspace=tmp_path)
    manager.try_mark_running("busy")
    manager.start_plugin_updates()
    await asyncio.sleep(0.1)
    assert passes == []  # a turn is running
    manager.mark_idle("busy")
    for _ in range(100):
        if passes:
            break
        await asyncio.sleep(0.01)
    assert passes == [1]
    await manager.stop_plugin_updates()
    assert manager._plugin_update_task is None
