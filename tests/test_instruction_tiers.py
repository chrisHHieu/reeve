"""The four instruction tiers: managed → user (SOUL.md, the
only user-level file) → project → local, broadest first so the most specific reads last;
and the agent cannot rewrite the tiers a person or an administrator owns."""

from __future__ import annotations

from harness.context.memory.settings import MemorySettingsStore
from harness.safety.permissions import Mode, PermissionEngine
from harness.session.project import load_agents_md, nested_agents_md
from harness.safety.secrets import state_dir


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_managed_then_parent_then_project_then_local(tmp_path):
    repo = tmp_path / "repo"
    ws = repo / "app"
    _write(repo / "AGENTS.md", "REPO RULE")
    _write(repo / "CLAUDE.local.md", "MY REPO NOTE")
    _write(ws / "AGENTS.md", "APP RULE")
    _write(ws / "AGENTS.local.md", "MY APP NOTE")
    _write(ws / "CLAUDE.local.md", "IGNORED: AGENTS.local.md wins")
    managed = tmp_path / "managed" / "AGENTS.md"
    _write(managed, "COMPANY POLICY")

    block = load_agents_md(ws, managed_path=managed)

    order = [block.index(t) for t in ("COMPANY POLICY", "REPO RULE", "MY REPO NOTE", "APP RULE", "MY APP NOTE")]
    assert order == sorted(order)
    assert "IGNORED" not in block
    assert "<managed AGENTS.md>" in block and "<local AGENTS.local.md>" in block


def test_a_nested_directory_brings_its_local_file_with_its_project_file(tmp_path):
    _write(tmp_path / "pkg" / "AGENTS.md", "PKG RULE")
    _write(tmp_path / "pkg" / "AGENTS.local.md", "PKG MINE")
    _write(tmp_path / "solo" / "CLAUDE.local.md", "SOLO MINE")
    found = nested_agents_md(tmp_path, [str(tmp_path / "pkg" / "a.py"), str(tmp_path / "solo" / "b.py")])
    assert found == [("pkg", "PKG RULE\n\nPKG MINE"), ("solo", "SOLO MINE")]


def test_the_old_user_agents_md_is_merged_into_soul_once(tmp_path):
    _write(tmp_path / "SOUL.md", "I am a product manager.")
    _write(tmp_path / "AGENTS.md", "Answer in Vietnamese.\n@extra.md")
    _write(tmp_path / "extra.md", "Keep it short.")
    settings = MemorySettingsStore(tmp_path / "memory-settings.json")

    assert settings.user_rules == "I am a product manager.\n\n## From AGENTS.md\n\nAnswer in Vietnamese.\n@extra.md\n"
    assert not (tmp_path / "AGENTS.md").exists()
    assert (tmp_path / "AGENTS.md.merged-into-SOUL").is_file()
    # The model reads it with the import expanded; the editor keeps what was written.
    assert "Keep it short." in settings.prompt_rules() and "@extra.md" not in settings.prompt_rules()
    assert settings.user_rules.count("Answer in Vietnamese.") == 1  # never merged twice


def test_the_agent_cannot_rewrite_the_tiers_a_person_owns(tmp_path):
    engine = PermissionEngine(workspace_root=tmp_path, mode=Mode.DANGER_FULL_ACCESS)
    for target in (
        state_dir() / "SOUL.md",
        state_dir() / "managed" / "AGENTS.md",
        state_dir() / "memory-settings.json",
        state_dir() / "prefs.json",
    ):
        decision = engine.evaluate("write_file", {"path": str(target), "content": "x"}, None)
        assert not decision.allowed, target
