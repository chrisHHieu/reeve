"""Batch 3/P2 — the community-standard installer (`npx skills add`) flow.

Pins the three safety properties the installer exists for: the exact argv (copy mode, no
shell), the staging-cwd preview (nothing lands before confirm), and the lockfile provenance.
No network: every test injects the runner seam and feeds fixture output.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from harness.safety.secrets import state_dir
from harness.extensions.skills import SkillLoader, SkillStore
from harness.extensions.skills.installer import DEFAULT_SKILL_AGENT, CliResult, npx_prefix, skill_cli_command, skill_install, source_type

FAKE_NPX = ["npx"]


def _argv(source="owner/repo", **kw):
    return skill_cli_command(source, npx=list(FAKE_NPX), **kw)


class FakeRunner:
    """The runner seam: records the call, then lets the test write the CLI's output."""

    def __init__(self, behavior=None, result: CliResult | None = None):
        self.calls: list[dict] = []
        self._behavior = behavior
        self._result = result or CliResult(0)

    def __call__(self, *, command, cwd, timeout):
        self.calls.append({"command": list(command), "cwd": Path(cwd), "timeout": timeout})
        if self._behavior is not None:
            self._behavior(Path(cwd))
        return self._result


def _write_skill(
    root: Path,
    name: str,
    *,
    description: str = "a fixture skill",
    extra: str = "",
    resources: dict[str, str] | None = None,
    agent_dir: str = ".agents/skills",
) -> Path:
    folder = root / agent_dir / name
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n{extra}---\n\nDo the thing.\n",
        encoding="utf-8",
    )
    for rel, content in (resources or {}).items():
        target = folder / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return folder


@pytest.fixture()
def store(tmp_path):
    return SkillStore(global_dir=tmp_path / "global-skills")


# -- argv + source typing -----------------------------------------------------------


def test_exact_argv():
    assert _argv() == [
        "npx", "-y", "skills@latest", "add", "owner/repo", "--copy", "-y",
        "-a", DEFAULT_SKILL_AGENT,
    ]
    assert _argv("owner/repo", skills=["one", "two"], agent="codex") == [
        "npx", "-y", "skills@latest", "add", "owner/repo", "--copy", "-y",
        "-s", "one", "-s", "two", "-a", "codex",
    ]
    # Nothing extra when no names are requested; no `-g` ever (staging stays project-scoped).
    assert "-g" not in _argv() and "--global" not in _argv()


def test_npx_prefix_is_platform_correct():
    if sys.platform == "win32":
        assert npx_prefix() == ["cmd", "/c", "npx"]
    else:
        assert npx_prefix() == ["npx"]


@pytest.mark.parametrize("bad", ["", "   ", "-g", "owner/repo\nevil", "x" * 501])
def test_bad_sources_rejected(bad):
    with pytest.raises(ValueError):
        skill_cli_command(bad)


def test_source_type_vocabulary():
    assert source_type("owner/repo") == "github"
    assert source_type("https://github.com/owner/repo") == "github"
    assert source_type("https://example.com/skills.tar.gz") == "url"
    assert source_type("git@github.com:owner/repo.git") == "git"
    assert source_type("git+https://example.com/x") == "git"
    assert source_type("some/local/dir") == "git"


# -- stage: the CLI runs in a staging token dir --------------------------------------


def test_stage_install_runs_in_staging_and_previews(store):
    runner = FakeRunner(lambda cwd: _write_skill(cwd, "greet", description="says hello"))
    preview = store.stage_install("owner/repo", runner=runner)

    call = runner.calls[0]
    assert call["command"] == [
        *npx_prefix(), "-y", "skills@latest", "add", "owner/repo", "--copy", "-y",
        "-a", DEFAULT_SKILL_AGENT,
    ]
    assert call["cwd"] == store._staging_dir / preview["token"]
    assert call["cwd"].is_dir()
    assert call["timeout"] > 0

    assert preview["source"] == "owner/repo"
    assert preview["sourceType"] == "github"
    assert preview["installable"] is True and preview["warnings"] == []
    skill = preview["skills"][0]
    assert skill["name"] == "greet"
    assert skill["description"] == "says hello"
    assert skill["description_length"] == len("says hello")
    assert skill["description_capped"] is False
    assert skill["skillPath"] == ".agents/skills/greet/SKILL.md"
    assert skill["computedHash"].startswith("sha256:")
    assert skill["blocked"] == "" and skill["replaces"] is False
    # Preview only — nothing in the product root yet.
    assert not (store.global_dir / "greet").exists()
    # The staging manifest exists for confirm to read.
    assert (call["cwd"] / "install-manifest.json").is_file()


def test_stage_install_names_forwarded(store):
    runner = FakeRunner(lambda cwd: _write_skill(cwd, "one"))
    store.stage_install("owner/repo", skills=["one"], agent="claude-code", runner=runner)
    assert runner.calls[0]["command"] == [
        *npx_prefix(), "-y", "skills@latest", "add", "owner/repo", "--copy", "-y",
        "-s", "one", "-a", "claude-code",
    ]


def test_stage_install_contains_bad_files_and_keeps_good_ones(store):
    def behavior(cwd: Path) -> None:
        _write_skill(cwd, "good")
        bad = cwd / ".agents" / "skills" / "bad"
        bad.mkdir(parents=True)
        (bad / "SKILL.md").write_text("---\nname: bad/bad\n---\nbody", encoding="utf-8")

    preview = store.stage_install("owner/repo", runner=FakeRunner(behavior))
    assert [s["name"] for s in preview["skills"]] == ["good"]
    assert any("bad" in w for w in preview["warnings"])


def test_stage_install_parses_flat_md_and_ignores_resource_md(store):
    def behavior(cwd: Path) -> None:
        folder = _write_skill(cwd, "greet")
        notes = folder / "references" / "notes.md"
        notes.parent.mkdir(parents=True)
        notes.write_text("---\nname: not-a-skill\ndescription: resource\n---\nx", encoding="utf-8")
        flat = cwd / ".agents" / "skills" / "flat"
        flat.mkdir(parents=True)
        (flat / "plain.md").write_text(
            "---\nname: plain\ndescription: flat one\n---\nbody", encoding="utf-8"
        )

    preview = store.stage_install("owner/repo", runner=FakeRunner(behavior))
    assert sorted(s["name"] for s in preview["skills"]) == ["greet", "plain"]
    assert len(preview["skills"]) == 2


def test_stage_install_without_skills_is_an_error(store):
    with pytest.raises(ValueError, match="didn't add any skills"):
        store.stage_install("owner/repo", runner=FakeRunner())
    # A readme alone doesn't count either (no frontmatter name).
    with pytest.raises(ValueError, match="didn't add any skills"):
        store.stage_install(
            "owner/repo",
            runner=FakeRunner(lambda cwd: (cwd / "README.md").write_text("hi", encoding="utf-8")),
        )
    assert not store._staging_dir.exists() or not any(store._staging_dir.iterdir())


def test_stage_install_surfaces_cli_failure_and_cleans_staging(store):
    runner = FakeRunner(result=CliResult(1, stderr="fatal: repository not found"))
    with pytest.raises(ValueError, match="repository not found"):
        store.stage_install("owner/repo", runner=runner)
    assert not any(store._staging_dir.iterdir())  # no half-staged dir is left behind


def test_stage_install_rejects_symlink_escape(store, tmp_path):
    outside = tmp_path / "outside-skill.md"
    outside.write_text("---\nname: evil\n---\nx", encoding="utf-8")

    def behavior(cwd: Path) -> None:
        try:
            os.symlink(outside, cwd / "escape.md")
        except (OSError, NotImplementedError):
            pytest.skip("symlinks unavailable on this platform/user")

    with pytest.raises(ValueError, match="outside"):
        store.stage_install("owner/repo", runner=FakeRunner(behavior))


def test_stage_install_description_over_the_500_cap_is_flagged(store):
    import re

    long_desc = "word " * 120  # 600 chars before YAML trims the trailing space
    preview = store.stage_install(
        "owner/repo", runner=FakeRunner(lambda cwd: _write_skill(cwd, "long", description=long_desc))
    )
    skill = preview["skills"][0]
    assert skill["description_capped"] is True
    assert skill["description_length"] == len(re.sub(r"\s+", " ", long_desc).strip())


# -- the real runner: missing npx / timeout (no network) -----------------------------


def test_skill_install_missing_binary_is_actionable(tmp_path):
    with pytest.raises(ValueError, match="Node.js"):
        skill_install(
            command=["definitely-not-a-real-binary-xyz"], cwd=tmp_path, timeout=5
        )


def test_skill_install_timeout_is_actionable(tmp_path):
    with pytest.raises(ValueError, match="timed out"):
        skill_install(
            command=[sys.executable, "-c", "import time; time.sleep(5)"],
            cwd=tmp_path,
            timeout=0.3,
        )


# -- confirm: promote + lockfile -----------------------------------------------------


def _stage(store, name="greet", source="owner/repo"):
    return store.stage_install(
        source, runner=FakeRunner(lambda cwd: _write_skill(cwd, name))
    )


def test_confirm_promotes_and_writes_cline_shape_lockfile(store):
    preview = _stage(store)
    saved = store.confirm_install(preview["token"])
    assert [s["name"] for s in saved] == ["greet"]
    assert (store.global_dir / "greet" / "SKILL.md").is_file()
    assert SkillLoader([store.global_dir]).get("greet").description == "a fixture skill"

    lock = json.loads((state_dir() / "skills-lock.json").read_text(encoding="utf-8"))
    assert lock["version"] == 1
    assert lock["skills"]["greet"] == {
        "source": "owner/repo",
        "sourceType": "github",
        "skillPath": ".agents/skills/greet/SKILL.md",
        "computedHash": preview["skills"][0]["computedHash"],
    }
    assert store.installed_map()["greet"]["sourceType"] == "github"
    # The lockfile's hash matches the promoted copy, file for file.
    from harness.extensions.skills.installer import compute_skill_hash

    assert compute_skill_hash(store.global_dir / "greet", is_flat=False) == lock["skills"]["greet"]["computedHash"]
    assert not (store._staging_dir / preview["token"]).exists()  # staging cleaned


def test_confirm_unknown_token_refused(store):
    with pytest.raises(ValueError, match="Unknown or expired"):
        store.confirm_install("nope")


def test_lock_merges_and_delete_prunes(store):
    a = _stage(store, "alpha", "owner/one")
    store.confirm_install(a["token"])
    b = _stage(store, "beta", "owner/two")
    store.confirm_install(b["token"])
    assert set(store.installed_map()) == {"alpha", "beta"}

    store.delete("alpha")
    assert set(store.installed_map()) == {"beta"}  # the other install survives
    store.delete("beta")
    assert store.installed_map() == {}
    assert not (state_dir() / "skills-lock.json").exists()  # empty lock is dropped


def test_corrupt_lock_is_ignored_not_fatal(store):
    (state_dir()).mkdir(parents=True, exist_ok=True)
    (state_dir() / "skills-lock.json").write_text("{not json", encoding="utf-8")
    assert store.installed_map() == {}
    _ = store.rows()  # nothing raises


# -- shadowing / replacement policy --------------------------------------------------


def test_install_refuses_compat_shadowing(tmp_path):
    home = tmp_path / "home"
    (home / ".agents" / "skills" / "shared").mkdir(parents=True)
    (home / ".agents" / "skills" / "shared" / "SKILL.md").write_text(
        "---\nname: shared\ndescription: community copy\n---\nbody", encoding="utf-8"
    )
    store = SkillStore(global_dir=tmp_path / "skills", home_dir=home)
    preview = store.stage_install(
        "owner/repo", runner=FakeRunner(lambda cwd: _write_skill(cwd, "shared"))
    )
    assert preview["installable"] is False
    assert "shared skills folder" in preview["skills"][0]["blocked"]
    with pytest.raises(ValueError, match="shared skills folder"):
        store.confirm_install(preview["token"])
    # The community copy is untouched.
    assert (home / ".agents" / "skills" / "shared" / "SKILL.md").is_file()


def test_replacing_a_product_skill_requires_the_confirm_flag(store):
    store.create(name="greet", description="mine", instructions="old body")
    (store.global_dir / "greet" / "old-resource.txt").write_text("old", encoding="utf-8")
    preview = store.stage_install(
        "owner/repo",
        runner=FakeRunner(
            lambda cwd: _write_skill(cwd, "greet", description="theirs", resources={"new.txt": "n"})
        ),
    )
    assert preview["skills"][0]["replaces"] is True
    with pytest.raises(ValueError, match="Confirm the replacement"):
        store.confirm_install(preview["token"])
    # Refused = untouched.
    assert (store.global_dir / "greet" / "old-resource.txt").is_file()

    saved = store.confirm_install(preview["token"], overwrite=True)
    assert saved[0]["name"] == "greet"
    assert SkillLoader([store.global_dir]).get("greet").description == "theirs"
    assert (store.global_dir / "greet" / "new.txt").is_file()
    assert not (store.global_dir / "greet" / "old-resource.txt").exists()  # replacement, not merge
    assert store.installed_map()["greet"]["source"] == "owner/repo"


def test_rolling_back_a_partial_promotion(store, monkeypatch):
    """A failure midway through a multi-skill promotion restores everything it touched."""
    import shutil as _shutil

    preview = store.stage_install(
        "owner/repo",
        runner=FakeRunner(
            lambda cwd: (
                _write_skill(cwd, "one"),
                _write_skill(cwd, "two"),
            )
        ),
    )
    real_move = _shutil.move
    calls = {"n": 0}

    def flaky_move(src, dst, *a, **kw):
        calls["n"] += 1
        if calls["n"] == 2:  # skill one promoted, skill two's move fails
            raise OSError("disk full")
        return real_move(src, dst, *a, **kw)

    monkeypatch.setattr("harness.extensions.skills.store.shutil.move", flaky_move)
    with pytest.raises(OSError):
        store.confirm_install(preview["token"])
    assert not (store.global_dir / "one").exists()
    assert not (store.global_dir / "two").exists()
    assert store.installed_map() == {}


# -- the CLI surface (`reeve skill …`) ---------------------------------------------


def test_cli_skill_list_and_install(tmp_path, monkeypatch, capsys):
    from harness.interface import cli
    from harness.extensions.skills import store as store_mod

    monkeypatch.setattr(
        store_mod,
        "skill_install",
        FakeRunner(lambda cwd: _write_skill(cwd, "weekly-report", description="Monday report")),
    )

    code = cli._skill_cli(["install", "owner/repo"])
    out = capsys.readouterr().out
    assert code == 0 and "weekly-report" in out and "installed" in out

    code = cli._skill_cli(["list"])
    out = capsys.readouterr().out
    assert code == 0
    assert "weekly-report" in out and "from owner/repo" in out


def test_cli_skill_install_preview_does_not_install(tmp_path, monkeypatch, capsys):
    from harness.interface import cli
    from harness.extensions.skills import store as store_mod

    monkeypatch.setattr(
        store_mod,
        "skill_install",
        FakeRunner(lambda cwd: _write_skill(cwd, "greet")),
    )
    code = cli._skill_cli(["install", "owner/repo", "--preview"])
    assert code == 0 and "nothing installed" in capsys.readouterr().out
    assert not (state_dir() / "skills" / "greet").exists()


def test_cli_skill_install_error_exits_nonzero(monkeypatch, capsys):
    from harness.interface import cli
    from harness.extensions.skills import store as store_mod

    monkeypatch.setattr(store_mod, "skill_install", FakeRunner(result=CliResult(1, stderr="boom")))
    code = cli._skill_cli(["install", "owner/repo"])
    assert code == 1 and "boom" in capsys.readouterr().err
