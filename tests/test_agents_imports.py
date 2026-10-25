"""`@path` imports in AGENTS.md (memory v2 Phase 2) — the expansion rules:
relative to the importing file, recursive with a hop cap, never inside code, and a
workspace boundary that a committed project file cannot cross."""

from __future__ import annotations

from harness.session.project import expand_imports, load_agents_md


def test_import_expands_relative_to_the_importing_file(tmp_path):
    root = tmp_path / "repo"
    (root / "docs").mkdir(parents=True)
    (root / "AGENTS.md").write_text("See @docs/style.md for style.", encoding="utf-8")
    (root / "docs" / "style.md").write_text("TWO-SPACE INDENT", encoding="utf-8")

    block = load_agents_md(root, managed_path=root / "missing.md")

    assert "TWO-SPACE INDENT" in block
    assert "@docs/style.md" not in block


def test_imports_chain_and_stop_at_the_depth_cap(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "a.md").write_text("A RULE\n@b.md", encoding="utf-8")
    (root / "b.md").write_text("B RULE\n@c.md", encoding="utf-8")
    (root / "c.md").write_text("C RULE\n@d.md", encoding="utf-8")
    (root / "d.md").write_text("D RULE\n@e.md", encoding="utf-8")
    (root / "e.md").write_text("E RULE", encoding="utf-8")

    text = expand_imports(
        "@a.md", source=root / "AGENTS.md", workspace=root
    )

    assert "A RULE" in text and "B RULE" in text and "C RULE" in text and "D RULE" in text
    # Four hops: AGENTS → a → b → c → d. The fifth token stays literal.
    assert "E RULE" not in text
    assert "@e.md" in text


def test_code_spans_and_fences_are_not_import_sites(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "secret.md").write_text("MUST NOT LOAD", encoding="utf-8")
    source = root / "AGENTS.md"
    source.write_text(
        "Inline `@secret.md` stays literal.\n\n```\n@secret.md\n```\n",
        encoding="utf-8",
    )

    text = expand_imports(source.read_text(encoding="utf-8"), source=source, workspace=root)

    assert "MUST NOT LOAD" not in text
    assert "@secret.md" in text  # both sites keep their literal text


def test_a_token_that_is_not_a_file_stays_literal(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    source = root / "AGENTS.md"
    source.write_text("Ping me at user@example.com when done.", encoding="utf-8")

    text = expand_imports(source.read_text(encoding="utf-8"), source=source, workspace=root)

    assert text == "Ping me at user@example.com when done."


def test_external_import_is_marked_in_project_files_and_allowed_from_global(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (tmp_path / "private.md").write_text("PRIVATE NOTES", encoding="utf-8")
    (ws / "AGENTS.md").write_text("Project: @../private.md", encoding="utf-8")
    glob = tmp_path / "global-AGENTS.md"
    glob.write_text("User: @private.md", encoding="utf-8")

    block = load_agents_md(ws, managed_path=glob)

    assert "PRIVATE NOTES" in block  # the managed (administrator) file may read anywhere, like SOUL.md
    assert "import skipped" in block  # a committed project file may not
