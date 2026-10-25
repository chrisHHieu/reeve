"""The skill store: skills are folders, and where the folder is decides the scope.

What is pinned here is what keeps that safe: a skill's name becomes a folder name, so it cannot
climb out; an upload is staged and previewed before anything lands; a file that cannot be read is
skipped, never fatal; and turning a skill off is the person's own setting, never a file committed
with a project.
"""

from __future__ import annotations

import io
import logging
import os
import zipfile
from pathlib import Path

import pytest
import yaml

from harness.extensions.skills import SkillLoader, SkillStore, save_skill_tool, validate_name

SKILL_MD = "---\nname: greet\ndescription: says hello\n---\n\nSay hello warmly.\n"


@pytest.fixture()
def store(tmp_path):
    return SkillStore(global_dir=tmp_path / "global-skills")


@pytest.fixture()
def workspace(tmp_path):
    folder = tmp_path / "proj"
    folder.mkdir()
    return folder


@pytest.fixture()
def session_dir(tmp_path):
    folder = tmp_path / "session-root"
    folder.mkdir()
    return folder


def _zip(entries: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content in entries.items():
            archive.writestr(name, content)
    return buffer.getvalue()


def _manual_skill(base: Path, folder: str, text: str) -> None:
    (base / folder).mkdir(parents=True)
    (base / folder / "SKILL.md").write_text(text, encoding="utf-8")


def _loaded(store, name):
    return SkillLoader([store.global_dir]).get(name)


def _project_skill(workspace, name):
    return workspace / ".reeve" / "skills" / name / "SKILL.md"


# -- making a skill ------------------------------------------------------------------------------


def test_a_skill_made_globally_is_found_by_the_loader(store):
    assert store.create(name="weekly-report", description="Monday status report", instructions="1. Gather updates\n2. Write the report")["scope"] == "global"
    skill = _loaded(store, "weekly-report")
    assert skill.description == "Monday status report" and "Gather updates" in skill.instructions


def test_a_project_skill_lives_in_the_projects_folder(store, workspace):
    store.create(name="release-checklist", description="repo release steps", instructions="Run the checklist.", scope="project", workspace=workspace)
    assert _project_skill(workspace, "release-checklist").is_file()


def test_a_name_already_taken_or_empty_instructions_are_refused(store):
    store.create(name="dup", description="first", instructions="x")
    with pytest.raises(ValueError, match="already exists"):
        store.create(name="dup", description="second", instructions="y")
    with pytest.raises(ValueError, match="instructions"):
        store.create(name="empty", description="d", instructions="   ")


@pytest.mark.parametrize("name", ["", "   ", "a" * 65, "../evil", "a/b", "a\\b", ".hidden", "café"])
def test_a_name_that_is_not_a_plain_folder_name_is_refused(name):
    with pytest.raises(ValueError):
        validate_name(name)


# -- changing, removing and moving it ------------------------------------------------------------


def test_an_update_changes_only_what_it_names_and_keeps_the_skills_files(store):
    store.create(name="weekly-report", description="first wording", instructions="draft steps")
    bundled = store.global_dir / "weekly-report" / "outline.md"
    bundled.write_text("a file the skill ships with", encoding="utf-8")
    store.update("weekly-report", instructions="final steps")
    skill = _loaded(store, "weekly-report")
    assert (skill.instructions, skill.description) == ("final steps", "first wording")
    assert bundled.read_text(encoding="utf-8") == "a file the skill ships with"


def test_a_deleted_skill_is_gone_and_deleting_it_again_says_it_is_unknown(store):
    store.create(name="old-habit", description="d", instructions="x")
    store.delete("old-habit")
    assert not (store.global_dir / "old-habit").exists()
    with pytest.raises(ValueError, match="Unknown skill"):
        store.delete("old-habit")  # the second time


def test_deleting_a_linked_skill_folder_never_deletes_what_it_points_to(store, tmp_path):
    elsewhere = tmp_path / "kept-elsewhere"
    elsewhere.mkdir()
    (elsewhere / "SKILL.md").write_text(SKILL_MD, encoding="utf-8")
    store.global_dir.mkdir(parents=True, exist_ok=True)
    try:
        os.symlink(elsewhere, store.global_dir / "greet", target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable on this platform/user")
    try:  # refused, or the link removed in place: either way the target stays
        store.delete("greet")
    except ValueError:
        pass
    assert (elsewhere / "SKILL.md").is_file()


def test_a_skill_moves_between_global_and_project_and_back(store, workspace):
    store.create(name="mover", description="d", instructions="x")
    assert store.move("mover", to_scope="project", workspace=workspace)["scope"] == "project"
    assert _project_skill(workspace, "mover").is_file() and not (store.global_dir / "mover").exists()
    store.move("mover", to_scope="global", workspace=workspace)
    assert (store.global_dir / "mover" / "SKILL.md").is_file()


def test_a_move_onto_a_taken_name_is_refused_and_leaves_the_source(store, workspace):
    store.create(name="both", description="global copy", instructions="g")
    store.create(name="both", description="project copy", instructions="p", scope="project", workspace=workspace)
    with pytest.raises(ValueError, match="already exists"):
        store.move("both", to_scope="global", workspace=workspace)
    # The nearest copy, the project's, was the one being moved, and it is still there.
    assert _project_skill(workspace, "both").is_file()


# -- reading skill files -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("folder", "text", "description"),
    [
        ("bare", "Just instructions, no frontmatter.", ""),
        ("broken", "---\nname: broken\nno closing fence", None),
        ("hollow", "", None),
    ],
)
def test_a_skill_with_missing_or_broken_frontmatter_is_listed_under_its_folder(store, folder, text, description):
    _manual_skill(store.global_dir, folder, text)
    [row] = store.rows()
    assert row["name"] == folder and row["enabled"] is True
    if description is not None:
        assert row["description"] == description


def test_unicode_and_windows_line_endings_are_read(store):
    _manual_skill(store.global_dir, "emoji", "---\r\nname: emoji\r\ndescription: says 你好 🎉\r\n---\r\n\r\nGreet with 🎉.\r\n")
    description = _loaded(store, "emoji").description
    assert "🎉" in description and "你好" in description


def test_the_frontmatter_name_is_the_skills_name_and_the_project_copy_wins(store, workspace):
    store.create(name="brand", description="global copy", instructions="g")
    _manual_skill(workspace / ".reeve" / "skills", "other-folder", "---\nname: brand\ndescription: project copy\n---\nbody")
    [brand] = [row for row in store.rows(workspace) if row["name"] == "brand"]  # one row per name, not per folder
    assert brand["scope"] == "project"


# -- description contract (P0-3) ----------------------------------------------------


def test_create_rejects_blank_description(store):
    with pytest.raises(ValueError, match="description"):
        store.create(name="nodesc", description="   ", instructions="x")
    assert not (store.global_dir / "nodesc").exists()  # nothing half-written


def test_update_rejects_blank_description_without_touching_the_file(store):
    store.create(name="keepdesc", description="kept", instructions="x")
    with pytest.raises(ValueError, match="description"):
        store.update("keepdesc", description="   ")
    assert _loaded(store, "keepdesc").description == "kept"


# -- writer safety + unknown-key preservation (P0-1/P1-6) ---------------------------


def _frontmatter_of(md: Path) -> dict:
    return yaml.safe_load(md.read_text(encoding="utf-8").split("---", 2)[1])


def test_writer_quotes_yaml_hostile_description(store):
    # A raw `description: Extract: text` (old writer) was invalid YAML; safe_dump quotes it.
    description = "Extract: text, images — and [tables] # not a comment"
    store.create(name="hostile", description=description, instructions="x")
    assert _frontmatter_of(store.global_dir / "hostile" / "SKILL.md")["description"] == description
    assert _loaded(store, "hostile").description == description


def test_update_preserves_unknown_frontmatter(store):
    # P1-6 (toggle only your key):
    # update() must not destroy a shared skill's license/metadata/source.
    _manual_skill(store.global_dir, "licensed", "---\nname: licensed\ndescription: before\nlicense: MIT\nmetadata:\n  owner: me\nsource: uploaded\n---\n\nold body\n")
    store.update("licensed", description="after", instructions="new body")
    data = _frontmatter_of(store.global_dir / "licensed" / "SKILL.md")
    assert (data["description"], data["license"], data["metadata"], data["source"]) == ("after", "MIT", {"owner": "me"}, "uploaded")
    skill = _loaded(store, "licensed")
    assert skill.instructions == "new body"
    assert skill.extra["license"] == "MIT" and skill.extra["metadata"] == {"owner": "me"}  # unknown keys ride on the Skill too
    assert store.rows()[0]["source"] == "uploaded"


def test_update_without_description_keeps_a_legacy_blank_file_loadable(store):
    _manual_skill(store.global_dir, "quiet", "---\nname: quiet\n---\n\nbody\n")
    store.update("quiet", instructions="body v2")
    skill = _loaded(store, "quiet")
    assert skill.instructions == "body v2"
    assert skill.description == ""  # still hidden-from-model, not invented


def test_update_preserves_first_class_frontmatter(store):
    """P1-5/P1-7: `update()` rewrites only the edited keys — whenToUse/license/compatibility/
    metadata and both invocation flags survive verbatim."""
    _manual_skill(
        store.global_dir, "detailed",
        "---\nname: detailed\ndescription: before\nwhenToUse: asked\nlicense: MIT\ncompatibility: OpenCode 1.x\n"
        "metadata:\n  author: Ada\ndisable-model-invocation: true\nuser-invocable: false\n---\n\nold body\n",
    )
    store.update("detailed", description="after", instructions="new body")
    data = _frontmatter_of(store.global_dir / "detailed" / "SKILL.md")
    assert (data["whenToUse"], data["license"], data["compatibility"], data["metadata"]) == ("asked", "MIT", "OpenCode 1.x", {"author": "Ada"})
    assert data["disable-model-invocation"] is True and data["user-invocable"] is False
    skill = _loaded(store, "detailed")
    assert skill.model_invocable is False and skill.user_invocable is False


def test_legacy_writer_output_roundtrips_through_update(store):
    # P0-1 legacy fallback round-trip: read the old writer's invalid YAML, rewrite it
    # safely (update), and the value survives both hops.
    _manual_skill(store.global_dir, "legacy", "---\nname: legacy\ndescription: steps: do the thing\n---\n\nold\n")
    assert _loaded(store, "legacy").description == "steps: do the thing"
    store.update("legacy", instructions="new")
    assert _frontmatter_of(store.global_dir / "legacy" / "SKILL.md")["description"] == "steps: do the thing"  # valid YAML now
    reloaded = _loaded(store, "legacy")
    assert (reloaded.description, reloaded.instructions) == ("steps: do the thing", "new")


def test_corrupt_file_is_contained_in_rows(store, caplog):
    # one unreadable SKILL.md is warned about
    # and skipped; the rest of the listing survives.
    store.create(name="fine", description="d", instructions="x")
    (store.global_dir / "corrupt").mkdir()
    (store.global_dir / "corrupt" / "SKILL.md").write_bytes(b"\xff\xfe\x00 not utf-8 \xff")
    with caplog.at_level(logging.WARNING):
        rows = store.rows()
    assert [r["name"] for r in rows] == ["fine"]
    assert any("skill ignored:" in record.getMessage() for record in caplog.records)


# -- uploading a skill ---------------------------------------------------------------------------


@pytest.mark.parametrize("entries", [{"SKILL.md": SKILL_MD}, {"greet/SKILL.md": SKILL_MD, "greet/notes.txt": "extra"}])
def test_a_zip_with_the_skill_at_its_root_or_in_a_folder_previews_the_skill(store, entries):
    preview = store.stage_upload(_zip(entries))
    assert (preview["name"], preview["description"]) == ("greet", "says hello")
    store.discard_upload(preview["token"])


def test_an_upload_that_is_not_a_skill_says_what_was_expected(store):
    with pytest.raises(ValueError, match="SKILL.md"):
        store.stage_upload(_zip({"readme.txt": "not a skill"}))
    # A file that says it is a zip fails as a zip, not as markdown.
    with pytest.raises(ValueError, match="zip"):
        store.stage_upload(b"garbage bytes", filename="broken.zip")
    # Anything else names both shapes that are taken.
    with pytest.raises(ValueError, match=r"\.zip or a SKILL\.md"):
        store.stage_upload(b"\xff\xfe\x00\x01binary junk")


def test_a_markdown_file_with_frontmatter_is_a_skill_and_one_without_a_name_is_not(store):
    preview = store.stage_upload(SKILL_MD.encode(), filename="greet.md")
    assert preview["name"] == "greet" and preview["files"] == []
    assert store.confirm_upload(preview["token"], scope="global")["name"] == "greet"
    assert store.rows()[0]["source"] == "uploaded"
    with pytest.raises(ValueError, match="frontmatter"):
        store.stage_upload(b"Just instructions, no frontmatter.", filename="notes.md")


def test_a_zip_made_on_a_mac_installs_without_its_hidden_files(store):
    """Finder's Compress adds __MACOSX/._* shadows and .DS_Store; none is shown or installed."""
    preview = store.stage_upload(_zip({
        "greet/SKILL.md": SKILL_MD, "greet/notes.txt": "real resource", "greet/.DS_Store": "junk",
        "__MACOSX/greet/._SKILL.md": "junk", "__MACOSX/greet/._notes.txt": "junk",
    }), filename="greet.zip")
    assert preview["name"] == "greet" and preview["files"] == ["notes.txt"]
    installed = Path(store.confirm_upload(preview["token"], scope="global")["path"])
    assert sorted(p.name for p in installed.rglob("*")) == ["SKILL.md", "notes.txt"]


def test_a_zip_entry_that_climbs_out_is_refused(store, tmp_path):
    with pytest.raises(ValueError, match="unsafe"):
        store.stage_upload(_zip({"../evil/SKILL.md": SKILL_MD}))
    assert not (tmp_path / "evil").exists()


def test_confirming_saves_what_was_previewed_once(store):
    preview = store.stage_upload(_zip({"greet/SKILL.md": SKILL_MD, "greet/notes.txt": "extra"}))
    assert store.confirm_upload(preview["token"], scope="global")["name"] == "greet"
    assert _loaded(store, "greet").description == preview["description"]
    assert (store.global_dir / "greet" / "notes.txt").is_file()
    assert store.rows()[0]["source"] == "uploaded"
    with pytest.raises(ValueError, match="expired"):
        store.confirm_upload(preview["token"])


# -- turning a skill off -------------------------------------------------------------------------


def test_a_skill_turned_off_stays_off_across_a_restart_and_turns_back_on(store):
    store.create(name="sleepy", description="d", instructions="x")
    store.set_enabled("sleepy", False)
    reopened = SkillStore(global_dir=store.global_dir)
    assert "sleepy" in reopened.disabled_names() and reopened.rows()[0]["enabled"] is False
    reopened.set_enabled("sleepy", True)
    assert reopened.rows()[0]["enabled"] is True


def test_a_settings_file_that_cannot_be_read_counts_as_none_and_is_rewritten(store):
    store._settings_path.parent.mkdir(parents=True, exist_ok=True)
    store._settings_path.write_text("{not json", encoding="utf-8")
    assert store.disabled_names() == set()
    store.set_enabled("x", False)
    assert store.disabled_names() == {"x"}


# -- the agent saving a skill -------------------------------------------------


def test_the_agent_adds_a_new_skill(store, session_dir):
    result = save_skill_tool(store, allowed_dirs=[session_dir])(name="weekly-report", description="Monday status report", instructions="1. Gather updates\n2. Write the report")
    assert result["ok"] and result["action"] == "added"
    assert _loaded(store, "weekly-report").description == "Monday status report"


def test_files_from_the_session_are_bundled_by_absolute_or_relative_path(store, session_dir):
    (session_dir / "fetch_prs.py").write_text("print('prs')", encoding="utf-8")
    (session_dir / "sub").mkdir()
    (session_dir / "sub" / "example-report.md").write_text("# Example", encoding="utf-8")
    result = save_skill_tool(store, allowed_dirs=[session_dir])(name="gh-report", description="report", instructions="Run fetch_prs.py", files=[str(session_dir / "fetch_prs.py"), "sub/example-report.md"])
    assert result["ok"] and sorted(result["files"]) == ["example-report.md", "fetch_prs.py"]
    folder = store.global_dir / "gh-report"
    assert (folder / "fetch_prs.py").read_text(encoding="utf-8") == "print('prs')" and (folder / "example-report.md").is_file()


def test_saving_under_a_taken_name_updates_it_and_keeps_its_files(store, session_dir):
    store.create(name="gh-report", description="old", instructions="old body")
    (store.global_dir / "gh-report" / "keep.txt").write_text("keep", encoding="utf-8")
    result = save_skill_tool(store, allowed_dirs=[session_dir])(name="gh-report", description="new", instructions="new body")
    assert result["ok"] and result["action"] == "updated"
    skill = _loaded(store, "gh-report")
    assert skill.description == "new" and "new body" in skill.instructions
    assert (store.global_dir / "gh-report" / "keep.txt").is_file()


def test_a_file_from_outside_the_session_is_refused_before_anything_is_written(store, session_dir, tmp_path):
    secret = tmp_path / "outside.txt"
    secret.write_text("secret", encoding="utf-8")
    result = save_skill_tool(store, allowed_dirs=[session_dir])(name="x", description="d", instructions="i", files=[str(secret)])
    assert "outside this session's folders" in result["error"] and not (store.global_dir / "x").exists()


def test_what_the_agent_must_give_and_a_bundled_skill_md_left_out(store, session_dir):
    save = save_skill_tool(store, allowed_dirs=[session_dir])
    assert "description" in save(name="x", description=" ", instructions="i")["error"]
    assert "instructions" in save(name="x", description="d", instructions=" ")["error"]
    assert "error" in save(name="../evil", description="d", instructions="i")
    # The instructions become SKILL.md, so a draft of it the model bundles is skipped, not an
    # error: refusing cost a second approval round (live drive, 2026-07-27).
    (session_dir / "SKILL.md").write_text("x", encoding="utf-8")
    result = save(name="x", description="d", instructions="i", files=["SKILL.md"])
    assert result["ok"] and result["files"] == []
    written = (store.global_dir / "x" / "SKILL.md").read_text(encoding="utf-8")
    assert "i" in written and written != "x"


def test_saving_a_skill_always_asks_and_names_what_it_needs(store):
    tool = save_skill_tool(store)
    assert tool.__aisuite_tool_metadata__.requires_approval is True  # an external action: asked every time
    function = tool.__tool_schema__["function"]
    assert function["name"] == "save_skill" and function["parameters"]["required"] == ["name", "description", "instructions"]


def test_a_rows_file_count_is_its_bundled_files_counted_through_folders(store):
    for name in ("bare", "packed"):
        store.create(name=name, description="d", instructions="i")
    packed = store.global_dir / "packed"
    (packed / "scripts").mkdir()
    (packed / "scripts" / "pull.py").write_text("x", encoding="utf-8")
    (packed / "reference.md").write_text("x", encoding="utf-8")
    counts = {row["name"]: row["files"] for row in store.rows()}
    assert counts == {"bare": 0, "packed": 2}  # SKILL.md itself is not a bundled file


# -- where skills come from ----------------------------------------------------------------------


def test_no_bundled_skills_and_loader_works_from_product_roots(tmp_path, caplog):
    """The zero-bundled-skills contract (owner 2026-09-17): a fresh
    install ships no feature skills; discovery still serves compat/global/project (and
    persona) roots, and the absent builtin dir is not a warning."""
    import harness

    assert not (Path(harness.__file__).resolve().parent / "skills-builtin").exists()
    home, ws = tmp_path / "home", tmp_path / "ws"
    ws.mkdir()
    _manual_skill(home / ".claude" / "skills", "shared", "---\nname: shared\ndescription: from the community root\n---\nbody\n")
    persona = tmp_path / "persona-skills"
    _manual_skill(persona, "bundle", "---\nname: bundle\ndescription: carried by a persona\n---\nbody\n")
    store = SkillStore(global_dir=tmp_path / "global-skills", home_dir=home)
    store.create(name="mine", description="d", instructions="i")
    store.create(name="local", description="d", instructions="i", scope="project", workspace=ws)
    with caplog.at_level(logging.WARNING):
        rows = store.rows(ws)
        names = set(SkillLoader([persona, *[p for p, _ in store.root_dirs(ws)]]).names())
    assert {r["name"] for r in rows} == {"shared", "mine", "local"}
    assert {r["scope"] for r in rows} == {"compat", "global", "project"}
    assert all("builtin" not in r for r in rows)
    assert names == {"bundle", "shared", "mine", "local"}
    assert not any("ignored" in record.getMessage() for record in caplog.records)


# -- compat roots (P1-2, read-only) --------------------------------------------------


def test_compat_skill_appears_read_only(tmp_path):
    """A skill in `~/.claude/skills` (etc.) is discovered, badged `compat`, and every
    write — edit, delete, toggle, move — is refused with a friendly error."""
    home, ws = tmp_path / "home", tmp_path / "ws"
    ws.mkdir()
    _manual_skill(home / ".claude" / "skills", "shared-skill", "---\nname: shared-skill\ndescription: from the community root\nwhenToUse: the user asks for it\nlicense: MIT\nmetadata:\n  author: Ada\n---\nbody\n")
    store = SkillStore(global_dir=tmp_path / "global-skills", home_dir=home)
    row = next(r for r in store.rows(ws) if r["name"] == "shared-skill")
    assert row["scope"] == "compat" and row["compat"] is True
    assert row["license"] == "MIT" and row["metadata"] == {"author": "Ada"} and row["when_to_use"] == "the user asks for it"
    for call in (
        lambda: store.update("shared-skill", description="nope"),
        lambda: store.delete("shared-skill"),
        lambda: store.set_enabled("shared-skill", False, ws),
        lambda: store.move("shared-skill", to_scope="global", workspace=ws),
    ):
        with pytest.raises(ValueError, match="shared skills folder"):
            call()
    assert (home / ".claude" / "skills" / "shared-skill" / "SKILL.md").is_file()


def test_product_roots_shadow_compat_roots(tmp_path):
    home, ws = tmp_path / "home", tmp_path / "ws"
    ws.mkdir()
    _manual_skill(home / ".agents" / "skills", "shadowed", "---\nname: shadowed\ndescription: compat copy\n---\ncompat body\n")
    store = SkillStore(global_dir=tmp_path / "global-skills", home_dir=home)

    def row():
        return next(r for r in store.rows(ws) if r["name"] == "shadowed")

    # Global compat only → compat row; editing it is refused.
    assert row()["scope"] == "compat"
    with pytest.raises(ValueError, match="shared skills folder"):
        store.update("shadowed", description="nope")
    # A product global copy shadows it and is editable.
    store.create(name="shadowed", description="mine", instructions="mine")
    assert row()["scope"] == "global" and row()["compat"] is False
    store.update("shadowed", description="edited")
    assert store.find("shadowed", ws)[1] == "global"
    # A project compat copy outranks the global product copy…
    _manual_skill(ws / ".opencode" / "skills", "shadowed", "---\nname: shadowed\ndescription: project compat\n---\nproject compat\n")
    assert row()["scope"] == "compat" and "opencode" in row()["path"]
    # …and the project product copy outranks everything.
    store.create(name="shadowed", description="project mine", instructions="mine", scope="project", workspace=ws)
    assert row()["scope"] == "project" and row()["compat"] is False
    assert store.find("shadowed", ws)[1] == "project"


def test_missing_compat_roots_are_silent(tmp_path, caplog):
    store = SkillStore(global_dir=tmp_path / "global-skills", home_dir=tmp_path / "home")
    ws = tmp_path / "ws"
    ws.mkdir()
    with caplog.at_level(logging.WARNING):
        assert store.rows(ws) == []
    assert not any("ignored" in r.getMessage() for r in caplog.records)


def test_rows_surface_invocation_flags(tmp_path):
    store = SkillStore(global_dir=tmp_path / "global-skills")
    _manual_skill(store.global_dir, "user-only", "---\nname: user-only\ndescription: d\ndisable-model-invocation: true\n---\nbody\n")
    _manual_skill(store.global_dir, "model-only", "---\nname: model-only\ndescription: d\nuser-invocable: false\n---\nbody\n")
    rows = {r["name"]: r for r in store.rows()}
    assert (rows["user-only"]["model_invocable"], rows["user-only"]["user_invocable"]) == (False, True)
    assert (rows["model-only"]["model_invocable"], rows["model-only"]["user_invocable"]) == (True, False)


def test_rows_carry_how_often_and_when_a_skill_was_used(store):
    from harness.extensions.skills.store import record_skill_use

    store.create(name="greet", description="says hello", instructions="Say hello warmly.")
    store.create(name="quiet", description="says nothing", instructions="Stay quiet.")
    record_skill_use("greet")
    record_skill_use("greet")
    rows = {row["name"]: row for row in store.rows()}
    assert rows["greet"]["uses"] == 2 and isinstance(rows["greet"]["last_used_at"], float)
    assert rows["quiet"]["uses"] == 0 and rows["quiet"]["last_used_at"] is None
