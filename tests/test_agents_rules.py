"""Path-scoped rules (memory v2 Phase 2): `.agents/rules/**/*.md` with a `paths:`
frontmatter list load at the next turn's start once the agent works with a matching
file; rules without `paths:` load at build. `.claude/rules` is read-only compat."""

from __future__ import annotations

from pathlib import Path

from harness.context.project_rules import load_rules, matches_paths, nested_rules


def _rule(directory: Path, name: str, body: str, *, paths: list[str] | None = None) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    header = ""
    if paths:
        header = "---\npaths:\n" + "".join(f"  - {p}\n" for p in paths) + "---\n\n"
    path.write_text(header + body, encoding="utf-8")
    return path


def test_unconditional_rules_load_at_build_and_path_scoped_do_not(tmp_path):
    ws = tmp_path / "ws"
    _rule(ws / ".agents" / "rules", "style.md", "ALWAYS RULE")
    _rule(ws / ".agents" / "rules", "api.md", "API RULE", paths=["src/api/**"])

    block = load_rules(ws)

    assert "ALWAYS RULE" in block
    assert "API RULE" not in block


def test_path_scoped_rules_match_globs_and_braces(tmp_path):
    ws = tmp_path / "ws"
    _rule(ws / ".agents" / "rules", "ui.md", "UI RULE", paths=["src/components/**/*.{ts,tsx}"])
    _rule(ws / ".agents" / "rules", "api.md", "API RULE", paths=["src/api/**"])

    api = nested_rules(ws, [str(ws / "src" / "api" / "handlers" / "users.ts")])
    ui = nested_rules(ws, [str(ws / "src" / "components" / "button.tsx")])
    other = nested_rules(ws, [str(ws / "docs" / "readme.md")])

    assert [key for key, _ in api] == ["api.md"]
    assert [key for key, _ in ui] == ["ui.md"]
    assert other == []
    assert "API RULE" in dict(api)["api.md"]


def test_star_stays_in_one_directory(tmp_path):
    assert matches_paths(["*.md"], "notes.md") is True
    assert matches_paths(["*.md"], "sub/notes.md") is False
    assert matches_paths(["**/*.md"], "sub/deep/notes.md") is True


def test_rules_are_stated_once_and_a_pass_is_capped(tmp_path):
    ws = tmp_path / "ws"
    for index in range(8):
        _rule(ws / ".agents" / "rules", f"r{index}.md", f"RULE {index}", paths=["**/*.py"])

    found = nested_rules(ws, [str(ws / "pkg" / "m.py")])
    assert len(found) == 6  # each rule is prompt weight; the next turn continues

    rest = nested_rules(
        ws, [str(ws / "pkg" / "m.py")], skip=[key for key, _ in found]
    )
    assert len(rest) == 2


def test_claude_rules_compat_is_read_and_user_rules_are_keyed(tmp_path):
    ws = tmp_path / "ws"
    _rule(ws / ".claude" / "rules", "compat.md", "COMPAT RULE", paths=["*.md"])
    user = tmp_path / "user-rules"
    _rule(user, "mine.md", "MY RULE", paths=["*.md"])

    found = nested_rules(ws, [str(ws / "notes.md")], global_dir=user)

    keys = [key for key, _ in found]
    assert "compat.md" in keys
    assert "user/mine.md" in keys
