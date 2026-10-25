"""Plugins, phase P2: marketplaces, installing with consent, scopes (design §6-§9).

Everything runs offline: a marketplace is a local folder, a git source is a local
repository reached by a file:// URL, and an archive comes from a stand-in download. What is
pinned is what a person relies on: nothing installs before they confirm, what installs is
what they previewed, a project can only declare a plugin (and only in a trusted
workspace), and the agent cannot write any of this state.
"""

from __future__ import annotations

import io
import json
import shutil
import zipfile
from pathlib import Path

import pytest

from harness.extensions.plugins import manager, resolve_plugins, store
from harness.extensions.plugins.marketplace import PluginError, add_marketplace, list_marketplaces, remove_marketplace
from harness.extensions.plugins.sources import SourceError, extract_zip, fetch
from harness.platform.spawn import run

FIXTURES = Path(__file__).parent / "fixtures" / "plugins"


def _market(tmp_path: Path, plugins: list[dict], name: str = "acme") -> Path:
    root = tmp_path / "market"
    (root / ".claude-plugin").mkdir(parents=True)
    (root / ".claude-plugin" / "marketplace.json").write_text(
        json.dumps({"name": name, "owner": {"name": "Acme"}, "plugins": plugins}), encoding="utf-8"
    )
    return root


def _local_market(tmp_path: Path) -> Path:
    root = _market(
        tmp_path,
        [
            {"name": "release-helper", "source": "./plugins/release-helper"},
            {
                "name": "bare",
                "source": "./plugins/bare",
                "description": "A plugin whose entry is its manifest",
                "version": "2.0.0",
            },
            {"name": "clash", "source": "./plugins/clash", "strict": False, "commands": ["./extra"]},
            {"name": "runner", "source": {"source": "command", "command": "curl | sh"}},
        ],
    )
    shutil.copytree(FIXTURES / "release-helper", root / "plugins" / "release-helper")
    bare = root / "plugins" / "bare" / "skills" / "tidy"
    bare.mkdir(parents=True)
    (bare / "SKILL.md").write_text("---\nname: tidy\ndescription: Tidy.\n---\nTidy.\n")
    clash = root / "plugins" / "clash"
    (clash / ".claude-plugin").mkdir(parents=True)
    (clash / ".claude-plugin" / "plugin.json").write_text(json.dumps({"name": "clash"}))
    return root


def _ids(plugins) -> list[str]:
    return [p.id for p in plugins.plugins]


# -- marketplaces ------------------------------------------------------------------------------


def test_a_local_marketplace_is_added_and_listed(tmp_path):
    added = add_marketplace(str(_local_market(tmp_path)))
    assert added == {"name": "acme", "plugins": 4, "location": str((tmp_path / "market").resolve())}
    assert [m["name"] for m in list_marketplaces()] == ["acme"]


@pytest.mark.parametrize("name", ["inline", "claude-official", "anthropic-tools", "reeve-official", "has space"])
def test_a_reserved_or_malformed_marketplace_name_is_refused(tmp_path, name):
    with pytest.raises(PluginError):
        add_marketplace(str(_market(tmp_path, [], name=name)))


# -- the install flow: preview, then yes -----------------------------------------------------------


def test_nothing_installs_before_the_person_confirms(tmp_path):
    add_marketplace(str(_local_market(tmp_path)))
    staged = manager.stage_install("release-helper@acme")
    consent = staged["consent"]
    assert consent["name"] == "release-helper" and consent["version"] == "0.3.0"
    assert consent["skills"] == ["release-helper:changelog"]
    assert consent["commands"] == ["release-helper:notes:draft", "release-helper:ship"]
    assert consent["agents"] == ["release-helper:release-reviewer", "release-helper:review:audit"]
    # What would run code is shown converted and verbatim: the event, the matcher as
    # Reeve reads it (Bash is run_shell), and the command with its script.
    # Its SessionEnd hook runs too since `session_end` exists, so it is shown.
    assert [e["kind"] for e in consent["executable"]] == ["hook", "hook"]
    assert consent["executable"][0]["detail"].startswith("pre_tool_use ^(?:run_shell)$: python ")
    assert consent["executable"][0]["detail"].endswith("/scripts/guard.py")
    assert consent["executable"][1]["detail"].startswith("session_end")
    assert consent["executable_digest"].startswith("sha256:")
    assert store.installed() == {}
    assert resolve_plugins(workspace=tmp_path).plugins == []

    done = manager.confirm_install(staged["token"])

    assert done == {"id": "release-helper@acme", "version": "0.3.0", "scope": "user"}
    plugins = resolve_plugins(workspace=tmp_path)
    assert _ids(plugins) == ["release-helper@acme"]
    assert plugins.plugins[0].components == {"skills": 1, "commands": 2, "agents": 2}
    assert plugins.agents["release-helper:release-reviewer"]["_plugin"] == "release-helper@acme"
    assert store.effective_enabled("release-helper@acme") == (True, "user")


def test_a_token_installs_what_was_previewed_once(tmp_path):
    add_marketplace(str(_local_market(tmp_path)))
    token = manager.stage_install("release-helper@acme")["token"]
    manager.confirm_install(token)
    with pytest.raises(PluginError, match="expired"):
        manager.confirm_install(token)
    with pytest.raises(PluginError, match="not a staging token"):
        manager.confirm_install("../../etc")


def test_an_entry_with_no_manifest_is_the_manifest(tmp_path):
    add_marketplace(str(_local_market(tmp_path)))
    manager.confirm_install(manager.stage_install("bare@acme")["token"])
    info = resolve_plugins(workspace=tmp_path).plugins[0]
    assert (info.id, info.version, info.description) == ("bare@acme", "2.0.0", "A plugin whose entry is its manifest")


def test_a_non_strict_entry_that_declares_components_is_refused(tmp_path):
    add_marketplace(str(_local_market(tmp_path)))
    with pytest.raises(PluginError, match="conflicting manifests"):
        manager.stage_install("clash@acme")


def test_a_command_source_is_refused(tmp_path):
    add_marketplace(str(_local_market(tmp_path)))
    with pytest.raises(PluginError, match="does not install plugins that way"):
        manager.stage_install("runner@acme")


def _git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    shutil.copytree(FIXTURES / "release-helper", repo)
    manifest = repo / ".claude-plugin" / "plugin.json"
    data = json.loads(manifest.read_text())
    data.pop("version")
    manifest.write_text(json.dumps(data))
    for argv in (
        ["git", "init", "-q"],
        ["git", "-c", "user.email=t@example.com", "-c", "user.name=T", "add", "."],
        ["git", "-c", "user.email=t@example.com", "-c", "user.name=T", "commit", "-q", "-m", "one"],
    ):
        assert run(argv, cwd=str(repo), capture_output=True).returncode == 0
    return repo


def test_a_git_source_installs_into_the_cache_at_its_commit(tmp_path):
    repo = _git_repo(tmp_path)
    sha = run(["git", "rev-parse", "HEAD"], cwd=str(repo), capture_output=True, text=True).stdout.strip()
    add_marketplace(str(_market(tmp_path, [{"name": "release-helper", "source": {"source": "url", "url": repo.as_uri()}}])))
    staged = manager.stage_install("release-helper@acme")
    # No version anywhere: the commit names it (design §8.3).
    assert staged["consent"]["version"] == sha[:12]
    manager.confirm_install(staged["token"])
    record = store.install_record("release-helper@acme")
    root = store.install_root(record)
    assert root == store.plugins_dir() / "cache" / "acme" / "release-helper" / sha[:12]
    assert not (root / ".git").exists()
    assert record["computedHash"].startswith("sha256:")


def _zip(files: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, body in files.items():
            archive.writestr(name, body)
    return buffer.getvalue()


def test_an_archive_is_checked_against_its_sha256_and_unpacked_safely(tmp_path):
    good = _zip({"p/.claude-plugin/plugin.json": json.dumps({"name": "zipped"}), "p/skills/a/SKILL.md": "x"})
    fetched = fetch({"source": "archive", "url": "https://example.com/p.zip"}, tmp_path / "a", download=lambda url: good)
    assert (fetched.path / ".claude-plugin" / "plugin.json").is_file()  # the top folder is the plugin
    with pytest.raises(SourceError, match="sha256"):
        fetch({"source": "archive", "url": "https://example.com/p.zip", "sha256": "0" * 64}, tmp_path / "b", download=lambda url: good)
    with pytest.raises(SourceError, match="leaves the plugin folder"):
        extract_zip(_zip({"../escape.txt": "x"}), tmp_path / "c")
    with pytest.raises(SourceError, match="https"):
        fetch({"source": "archive", "url": "http://example.com/p.zip"}, tmp_path / "d", download=lambda url: good)


def test_a_symlink_out_of_the_plugin_is_refused(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (tmp_path / "secret.txt").write_text("no")
    try:
        (src / "link.txt").symlink_to(tmp_path / "secret.txt")
    except OSError:
        pytest.skip("this machine cannot create symlinks")
    with pytest.raises(SourceError, match="points outside"):
        fetch(str(src), tmp_path / "dest")


# -- scopes -----------------------------------------------------------------------------------------


def _installed(tmp_path: Path) -> None:
    add_marketplace(str(_local_market(tmp_path)))
    manager.confirm_install(manager.stage_install("release-helper@acme")["token"])


def test_a_plugin_turned_off_does_not_load_and_managed_cannot_be_overridden(tmp_path):
    from harness.safety.secrets import state_dir

    _installed(tmp_path)
    store.set_enabled("release-helper@acme", False)
    assert resolve_plugins(workspace=tmp_path).plugins == []
    store.set_enabled("release-helper@acme", True)
    managed = state_dir() / "managed" / "plugins.json"
    managed.parent.mkdir(parents=True)
    managed.write_text(json.dumps({"enabledPlugins": {"release-helper@acme": False}}))
    store.set_enabled("release-helper@acme", True, "local", tmp_path)
    assert resolve_plugins(workspace=tmp_path).plugins == []


def test_a_project_s_choice_counts_only_in_a_trusted_workspace(tmp_path):
    from harness.safety.workspace_trust import WorkspaceTrustStore

    _installed(tmp_path)
    ws = tmp_path / "ws"
    ws.mkdir()
    store.set_enabled("release-helper@acme", False)
    (ws / ".reeve").mkdir()
    (ws / ".reeve" / "plugins.json").write_text(json.dumps({"enabledPlugins": {"release-helper@acme": True}}))

    untrusted = resolve_plugins(workspace=ws)
    assert untrusted.plugins == [] and "not trusted" in untrusted.errors[0]["error"]
    WorkspaceTrustStore().set_trusted(ws, True)
    assert _ids(resolve_plugins(workspace=ws)) == ["release-helper@acme"]


def test_a_project_can_declare_a_plugin_but_never_install_it(tmp_path):
    ws = tmp_path / "ws"
    (ws / ".claude").mkdir(parents=True)
    (ws / ".claude" / "settings.json").write_text(json.dumps({"enabledPlugins": {"formatter@somewhere": True}}))
    plugins = resolve_plugins(workspace=ws)
    assert plugins.plugins == []
    assert plugins.errors == [{"id": "formatter@somewhere", "error": "this project uses it; it is not installed here"}]
    assert store.installed() == {}


# -- removing ----------------------------------------------------------------------------------------


def test_uninstall_removes_the_plugin_and_its_data_unless_kept(tmp_path):
    from harness.extensions.plugins.resolve import data_dir

    _installed(tmp_path)
    data = data_dir("release-helper@acme")
    data.mkdir(parents=True)
    with pytest.raises(PluginError, match="still installed"):
        remove_marketplace("acme")
    assert manager.uninstall("release-helper@acme")["data_removed"] is True
    assert not data.exists() and store.installed() == {}
    assert resolve_plugins(workspace=tmp_path).plugins == []
    remove_marketplace("acme")
    assert list_marketplaces() == []


# -- the agent cannot touch it -------------------------------------------------------------------------


def test_the_agent_cannot_write_plugin_state_or_installed_code(tmp_path):
    from harness.safety.boundary import Boundary

    # A git install: its code lives in the cache (a local marketplace's loads in place,
    # from the person's own folder).
    repo = _git_repo(tmp_path)
    add_marketplace(str(_market(tmp_path, [{"name": "release-helper", "source": {"source": "url", "url": repo.as_uri()}}])))
    manager.confirm_install(manager.stage_install("release-helper@acme")["token"])
    boundary = Boundary(tmp_path)
    installed_file = store.plugins_dir() / "installed.json"
    cached = store.install_root(store.install_record("release-helper@acme")) / "skills" / "changelog" / "SKILL.md"
    assert boundary.settings_hit("write_file", {"path": str(installed_file), "content": "{}"}, False)
    assert boundary.settings_hit("write_file", {"path": str(tmp_path / "market" / "x.md"), "content": ""}, False) is None
    assert boundary.settings_hit("run_shell", {"command": f"echo x > {installed_file}"}, True)
    from harness.safety.boundary import is_protected_in_project

    assert is_protected_in_project(tmp_path / ".claude" / "settings.json")
    assert is_protected_in_project(tmp_path / "p" / ".claude-plugin" / "plugin.json")
    # An installed plugin's code is out of bounds: editing it is the way around consent.
    assert cached.is_file()
    assert boundary.settings_hit("write_file", {"path": str(cached), "content": "x"}, False)



# -- settings a plugin asks for (userConfig) -----------------------------------------------------


def _configured_market(tmp_path: Path) -> None:
    root = _market(tmp_path, [{"name": "deployer", "source": "./plugins/deployer"}])
    plugin = root / "plugins" / "deployer"
    (plugin / ".claude-plugin").mkdir(parents=True)
    (plugin / ".claude-plugin" / "plugin.json").write_text(
        json.dumps(
            {
                "name": "deployer",
                "userConfig": {
                    "branch": {"type": "string", "title": "Branch", "description": "Deploy from", "required": True},
                    "token": {"type": "string", "title": "Token", "description": "API token", "sensitive": True},
                    "retries": {"type": "number", "title": "Retries", "description": "How many", "min": 0, "default": 2},
                },
            }
        )
    )
    (plugin / "commands").mkdir()
    (plugin / "commands" / "deploy.md").write_text(
        "---\ndescription: Deploy\n---\nDeploy from ${user_config.branch} with ${user_config.token}, "
        "retrying ${user_config.retries} times.\n"
    )
    add_marketplace(str(root))


def test_a_required_setting_must_be_given_and_a_secret_never_lands_in_a_file(tmp_path):
    from harness.extensions.commands import load_commands
    from harness.safety.secrets import SecretStore

    _configured_market(tmp_path)
    staged = manager.stage_install("deployer@acme")
    assert [o["key"] for o in staged["consent"]["user_config"]] == ["branch", "token", "retries"]
    assert staged["consent"]["user_config"][1]["sensitive"] is True
    with pytest.raises(PluginError, match="needs a value for branch"):
        manager.confirm_install(staged["token"])
    with pytest.raises(PluginError, match="retries must be at least 0"):
        manager.confirm_install(staged["token"], {"branch": "main", "retries": -1})

    manager.confirm_install(staged["token"], {"branch": "release", "token": "tok-123"})

    on_disk = "".join(p.read_text() for p in store.plugins_dir().glob("*.json"))
    assert "tok-123" not in on_disk and "release" in on_disk
    assert SecretStore().get("plugin:deployer@acme") == {"token": "tok-123"}
    body = load_commands(tmp_path)["deployer:deploy"].body
    # A secret reaches a body the model reads only as a placeholder.
    assert body == "Deploy from release with ‹sensitive:token›, retrying 2 times."

    manager.uninstall("deployer@acme")
    assert SecretStore().get("plugin:deployer@acme") is None
    assert "release" not in (store.plugins_dir() / "config.json").read_text()


def test_an_unknown_setting_key_fails_the_plugin(tmp_path):
    from harness.extensions.plugins.manifest import read_manifest

    root = tmp_path / "p"
    (root / ".claude-plugin").mkdir(parents=True)
    (root / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"name": "p", "userConfig": {"x": {"type": "string", "title": "X", "description": "x", "colour": "red"}}})
    )
    assert "unknown keys colour" in read_manifest(root).errors[0]


# -- plugins that need other plugins -----------------------------------------------------------------


def _dep_market(tmp_path: Path, *, cycle: bool = False, dep_default_off: bool = False) -> None:
    entries = [
        {"name": "app", "source": "./plugins/app"},
        {"name": "base", "source": "./plugins/base", **({"defaultEnabled": False} if dep_default_off else {})},
    ]
    root = _market(tmp_path, entries)
    for name, needs in (("app", ["base"]), ("base", ["app"] if cycle else [])):
        plugin = root / "plugins" / name
        (plugin / ".claude-plugin").mkdir(parents=True)
        (plugin / ".claude-plugin" / "plugin.json").write_text(json.dumps({"name": name, "dependencies": needs}))
        (plugin / "skills" / f"{name}-skill").mkdir(parents=True)
        (plugin / "skills" / f"{name}-skill" / "SKILL.md").write_text(f"---\nname: {name}-skill\ndescription: x\n---\nx\n")
    add_marketplace(str(root))


def test_one_consent_installs_a_plugin_with_what_it_needs(tmp_path):
    _dep_market(tmp_path)
    staged = manager.stage_install("app@acme")
    assert [(d["id"], d["installed"]) for d in staged["consent"]["dependencies"]] == [("base@acme", False)]
    manager.confirm_install(staged["token"])
    assert sorted(store.installed()) == ["app@acme", "base@acme"]
    assert sorted(p.id for p in resolve_plugins(workspace=tmp_path).plugins) == ["app@acme", "base@acme"]


def test_a_plugin_whose_dependency_is_off_does_not_load_and_the_dependency_cannot_be_removed(tmp_path):
    _dep_market(tmp_path)
    manager.confirm_install(manager.stage_install("app@acme")["token"])
    with pytest.raises(PluginError, match="app@acme needs base@acme"):
        manager.uninstall("base@acme")
    store.set_enabled("base@acme", False)
    plugins = resolve_plugins(workspace=tmp_path)
    assert plugins.plugins == []
    assert plugins.errors == [{"id": "app@acme", "error": "it needs base@acme, which is off or not installed"}]
    assert "app:app-skill" not in [f"{r.plugin}:{r.path.name}" for r in plugins.skill_roots]


def test_a_dependency_off_by_default_comes_on_for_the_plugin_that_needs_it(tmp_path):
    _dep_market(tmp_path, dep_default_off=True)
    manager.confirm_install(manager.stage_install("app@acme")["token"])
    store.forget_enabled("base@acme", "user")  # only its default says off
    assert sorted(p.id for p in resolve_plugins(workspace=tmp_path).plugins) == ["app@acme", "base@acme"]


def test_a_dependency_cycle_is_refused_at_install(tmp_path):
    _dep_market(tmp_path, cycle=True)
    with pytest.raises(PluginError, match="depend on each other"):
        manager.stage_install("app@acme")
    assert not any((store.plugins_dir() / "staged").iterdir())


# -- updating ------------------------------------------------------------------------------------------


def test_an_update_shows_what_changed_and_installs_only_when_confirmed(tmp_path):
    repo = _git_repo(tmp_path)
    add_marketplace(str(_market(tmp_path, [{"name": "release-helper", "source": {"source": "url", "url": repo.as_uri()}}])))
    manager.confirm_install(manager.stage_install("release-helper@acme")["token"])
    assert manager.stage_update("release-helper@acme")["up_to_date"] is True

    extra = repo / "skills" / "notes"
    extra.mkdir()
    (extra / "SKILL.md").write_text("---\nname: notes\ndescription: Notes.\n---\nx\n")
    for argv in (
        ["git", "-c", "user.email=t@example.com", "-c", "user.name=T", "add", "."],
        ["git", "-c", "user.email=t@example.com", "-c", "user.name=T", "commit", "-q", "-m", "two"],
    ):
        assert run(argv, cwd=str(repo), capture_output=True).returncode == 0
    old = store.install_record("release-helper@acme")

    staged = manager.stage_update("release-helper@acme")

    assert staged["diff"]["skills"] == {"added": ["release-helper:notes"], "removed": []}
    assert staged["diff"]["executable_changed"] is False
    assert store.install_record("release-helper@acme")["version"] == old["version"]
    manager.confirm_install(staged["token"])
    new = store.install_record("release-helper@acme")
    assert new["version"] != old["version"] and new["installedAt"] == old["installedAt"]
    assert (store.install_root(old) / ".orphaned_at").is_file()


# -- over HTTP (the GUI's path) ---------------------------------------------------------------------


def test_the_api_installs_only_on_a_confirmed_token_and_speaks_errors(tmp_path):
    from fastapi.testclient import TestClient

    from harness.interface.server import create_app
    from harness.interface.server.manager import SessionManager

    client = TestClient(create_app(SessionManager(workspace=tmp_path)))
    market = _local_market(tmp_path)
    assert client.post("/v1/plugin-marketplaces", json={"source": str(market)}).json()["name"] == "acme"
    offered = client.get("/v1/plugin-marketplaces/acme/plugins").json()["plugins"]
    assert [p["id"] for p in offered][:2] == ["release-helper@acme", "bare@acme"]
    assert offered[0]["installed"] is False

    staged = client.post("/v1/plugins/install", json={"id": "release-helper@acme"}).json()
    assert client.get("/v1/plugins").json()["installed"] == []
    done = client.post(f"/v1/plugins/install/{staged['token']}/confirm", json={}).json()
    assert done["id"] == "release-helper@acme"
    view = client.get("/v1/plugins").json()
    assert [p["id"] for p in view["plugins"]] == ["release-helper@acme"]
    assert view["installed"][0]["enabled"] is True

    assert client.patch("/v1/plugins/release-helper@acme", json={"enabled": False}).json()["ok"]
    assert client.get("/v1/plugins").json()["plugins"] == []

    refused = client.post("/v1/plugins/install", json={"id": "runner@acme"})
    assert refused.status_code == 400 and "does not install plugins that way" in refused.json()["detail"]
    assert client.delete("/v1/plugins/release-helper@acme").json()["id"] == "release-helper@acme"
    assert client.get("/v1/plugins").json()["installed"] == []


# -- the command line -----------------------------------------------------------------------------


def test_the_cli_asks_before_installing_and_refuses_yes_for_code(tmp_path, capsys):
    from harness.extensions.plugins.cli import main

    market = _local_market(tmp_path)
    assert main(["marketplace", "add", str(market)]) == 0
    assert "added acme (4 plugins)" in capsys.readouterr().out

    # release-helper carries a hook: a script may not consent to it.
    assert main(["install", "release-helper@acme", "--yes"]) == 1
    assert "refusing --yes" in capsys.readouterr().err
    assert main(["install", "release-helper@acme"], ask=lambda prompt: "n") == 1
    out = capsys.readouterr().out
    assert "RUNS CODE (hook)" in out and "not installed" in out
    assert store.installed() == {}
    assert main(["install", "release-helper@acme"], ask=lambda prompt: "y") == 0
    assert "installed release-helper@acme 0.3.0 (user)" in capsys.readouterr().out

    # A content-only plugin may be installed unattended.
    assert main(["install", "bare@acme", "--yes"]) == 0
    assert sorted(store.installed()) == ["bare@acme", "release-helper@acme"]
    assert main(["install", "nope@acme", "--yes"]) == 2
    assert "acme has no plugin named nope" in capsys.readouterr().err
