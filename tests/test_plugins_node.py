"""A plugin's Node packages (design §8.5): installed only for a plugin whose code
runs, only from a lockfile, always with --ignore-scripts, after the person's yes — and the
lockfile is part of what they agreed to. A failure is a warning, never a failed install."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from harness.extensions.plugins import manager, store
from harness.extensions.plugins.consent import executable_surface
from harness.extensions.plugins.manifest import read_manifest
from harness.extensions.plugins.marketplace import add_marketplace
from harness.extensions.plugins.node import install_node_packages, node_setup
from harness.platform.spawn import run

FIXTURES = Path(__file__).parent / "fixtures" / "plugins"
GIT = ["git", "-c", "user.email=t@example.com", "-c", "user.name=T"]


def _repo(tmp_path: Path, files: dict[str, str]) -> Path:
    repo = tmp_path / "repo"
    shutil.copytree(FIXTURES / "release-helper", repo)  # it has a hook, so its code runs
    for rel, body in files.items():
        (repo / rel).write_text(body)
    for argv in (["git", "init", "-q"], GIT + ["add", "."], GIT + ["commit", "-q", "-m", "one"]):
        assert run(argv, cwd=str(repo), capture_output=True).returncode == 0
    market = tmp_path / "market"
    (market / ".claude-plugin").mkdir(parents=True)
    (market / ".claude-plugin" / "marketplace.json").write_text(
        json.dumps({"name": "acme", "owner": {"name": "Acme"}, "plugins": [{"name": "release-helper", "source": {"source": "url", "url": repo.as_uri()}}]})
    )
    add_marketplace(str(market))
    return repo


NPM = {"package.json": '{"name": "x", "dependencies": {"left-pad": "1.3.0"}}', "package-lock.json": '{"lockfileVersion": 3}'}


@pytest.mark.parametrize(
    "files, expected",
    [
        ({}, None),
        (NPM, ["npm", "ci", "--ignore-scripts"]),
        ({"package.json": "{}", "bun.lock": "{}"}, ["bun", "install", "--frozen-lockfile", "--ignore-scripts"]),
        ({"package.json": "{}", "yarn.lock": ""}, "yarn.lock"),
        ({"package.json": "{}"}, "no lockfile"),
    ],
)
def test_the_lockfile_decides_how_or_whether(tmp_path, files, expected):
    for rel, body in files.items():
        (tmp_path / rel).write_text(body)
    setup = node_setup(tmp_path)
    if expected is None:
        assert setup is None
    elif isinstance(expected, list):
        assert setup["argv"] == expected
    else:
        assert expected in setup["skip"]


def test_the_consent_screen_shows_the_install_and_the_lockfile_is_agreed_to(tmp_path):
    repo = _repo(tmp_path, NPM)
    consent = manager.stage_install("release-helper@acme")["consent"]
    (row,) = [r for r in consent["executable"] if r["kind"] == "node"]
    assert row["detail"].startswith("npm ci --ignore-scripts")

    raw = read_manifest(repo).raw
    before = executable_surface(repo, raw, "release-helper")[1]
    (repo / "package-lock.json").write_text('{"lockfileVersion": 3, "packages": {"": {}}}')
    assert executable_surface(repo, raw, "release-helper")[1] != before


def test_packages_install_into_the_installed_copy_after_the_yes(tmp_path, monkeypatch):
    _repo(tmp_path, NPM)
    calls: list[dict] = []

    def fake_run(argv, **kwargs):
        if not str(argv[0]).startswith("/bin/"):
            return run(argv, **kwargs)  # the git fetch runs for real
        calls.append({"argv": argv, **kwargs})
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr("harness.platform.spawn.run", fake_run)
    monkeypatch.setattr("harness.extensions.plugins.node.shutil.which", lambda name: f"/bin/{name}")
    staged = manager.stage_install("release-helper@acme")
    assert calls == []  # nothing runs before the yes
    done = manager.confirm_install(staged["token"])

    root = store.install_root(store.install_record("release-helper@acme"))
    assert [c["argv"] for c in calls] == [["/bin/npm", "ci", "--ignore-scripts"]]
    assert calls[0]["cwd"] == str(root) and calls[0]["timeout"] == 60.0
    assert "warnings" not in done


def test_a_failed_install_is_a_warning_and_the_plugin_still_installs(tmp_path, monkeypatch):
    _repo(tmp_path, NPM)
    monkeypatch.setattr("harness.extensions.plugins.node.shutil.which", lambda name: None)
    done = manager.confirm_install(manager.stage_install("release-helper@acme")["token"])
    assert done["warnings"] == ["npm is not installed, so its Node packages were not installed"]
    assert store.install_record("release-helper@acme")["warnings"] == done["warnings"]


def test_a_failing_package_manager_is_reported_not_raised(tmp_path):
    for rel, body in NPM.items():
        (tmp_path / rel).write_text(body)

    def failing(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 1, "", "npm ERR! network\n")

    def slow(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, 60)

    assert install_node_packages(tmp_path, run=failing, which=lambda n: n) == "installing its Node packages failed (exit 1) npm ERR! network"
    assert install_node_packages(tmp_path, run=slow, which=lambda n: n) == "installing its Node packages failed: TimeoutExpired"


def test_a_plugin_that_runs_no_code_gets_no_packages(tmp_path):
    for rel, body in NPM.items():
        (tmp_path / rel).write_text(body)
    skill = tmp_path / "skills" / "tidy"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: tidy\ndescription: Tidy.\n---\nTidy.\n")
    rows, digest, _ = executable_surface(tmp_path, {"name": "tidy"}, "tidy")
    assert rows == [] and digest == ""
