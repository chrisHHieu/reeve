"""The memory file shape: read every shape that exists on disk,
write one, and move a directory over once, with a backup."""

from __future__ import annotations

from harness.context.memory import memory_format as fmt
from harness.context.memory.auto_memory import AutoMemory

CC = "---\nname: no-db-mocks\ndescription: Tests hit a real DB\nmetadata:\n  type: feedback\n---\nBody.\n"


def test_the_claude_code_shape_round_trips():
    meta, body = fmt.split(CC)
    assert meta == {"name": "no-db-mocks", "description": "Tests hit a real DB", "type": "feedback"}
    assert body == "Body.\n"
    assert fmt.render(meta) + body == CC


def test_the_flat_shape_and_a_doubled_block_read_as_one():
    """1 in 3 real files carried two blocks (the old write path wrapped content that had
    its own); the first value of a key wins and both blocks are consumed."""
    doubled = "---\ntype: feedback\nmodified: 2026-09-20T11:11:08+00:00\n---\n---\ntype: project\n---\n\nThe fact.\n"
    meta, body = fmt.split(doubled)
    assert meta == {"type": "feedback", "modified": "2026-09-20T11:11:08+00:00"}
    assert body.strip() == "The fact."


def test_a_vietnamese_slug_keeps_its_letters():
    assert fmt.slug("Nguyên tắc tích hợp Jev vào harness") == "nguyen-tac-tich-hop-jev-vao"
    assert fmt.slug("Đặt lịch") == "dat-lich"
    assert fmt.slug("!!!") == "note"


def test_a_write_with_its_own_frontmatter_is_merged_not_nested(tmp_path):
    mem = AutoMemory(tmp_path)
    mem.write("no-db-mocks.md", CC, mode="overwrite", note_type="feedback")
    text = (tmp_path / "no-db-mocks.md").read_text(encoding="utf-8")
    assert text.count("---\n") == 2
    meta, body = fmt.split(text)
    assert meta["description"] == "Tests hit a real DB" and body.strip() == "Body."
    assert [f["description"] for f in mem.list_files()] == ["Tests hit a real DB"]


def test_upgrade_moves_a_directory_once_and_keeps_a_backup(tmp_path):
    (tmp_path / "MEMORY.md").write_text(
        "# Memory Index\n\n- [Jev rules](feedback_jev.md) — Jev runs after the static gate\n",
        encoding="utf-8",
    )
    legacy = (
        "---\ntype: feedback\nmodified: 2026-09-20T11:11:08+00:00\n---\n---\ntype: feedback\n---\n"
        "Jev runs after the static gate.\n\n**Liên quan:** [Other](project_other.md) · [Web](https://x.io/a.md)\n"
    )
    (tmp_path / "feedback_jev.md").write_text(legacy, encoding="utf-8")
    (tmp_path / "project_other.md").write_text("# Other thing\nDetails.\n", encoding="utf-8")

    assert fmt.upgrade_dir(tmp_path, reserved=("MEMORY.md",)) == {"upgraded": 2}

    meta, body = fmt.split((tmp_path / "feedback_jev.md").read_text(encoding="utf-8"))
    assert meta["name"] == "feedback_jev" and meta["type"] == "feedback"
    assert meta["description"] == "Jev runs after the static gate"  # from the index hook
    assert "[[project_other]]" in body and "[Web](https://x.io/a.md)" in body
    other, _ = fmt.split((tmp_path / "project_other.md").read_text(encoding="utf-8"))
    assert other["description"] == "Other thing"  # no index line: the first heading
    # The index is left as written; the originals are backed up; a second run is a no-op.
    assert (tmp_path / "MEMORY.md").read_text(encoding="utf-8").startswith("# Memory Index")
    backup = tmp_path / ".history" / f"pre-{fmt.FORMAT_VERSION}" / "feedback_jev.md"
    assert backup.read_text(encoding="utf-8") == legacy
    assert fmt.upgrade_dir(tmp_path, reserved=("MEMORY.md",)) == {"upgraded": 0}


def test_what_the_removed_dream_pass_left_is_moved_aside_once_and_never_deleted(tmp_path):
    (tmp_path / ".dream.json").write_text('{"last": "2026-10-04"}', encoding="utf-8")
    (tmp_path / "PROPOSALS.md").write_text("## 2026-10-04 - a-note.md (project)\nproposed\n", encoding="utf-8")
    (tmp_path / "a-note.md").write_text("---\nname: a-note\ndescription: d\n---\nfact\n", encoding="utf-8")
    aside = tmp_path / ".history" / fmt.RETIRED_DIR
    (aside).mkdir(parents=True)
    (aside / "PROPOSALS.md").write_text("an earlier retirement\n", encoding="utf-8")

    done = AutoMemory(tmp_path).upgrade()
    assert done["retired"] == [".dream.json", "PROPOSALS.md"]
    assert not (tmp_path / ".dream.json").exists() and not (tmp_path / "PROPOSALS.md").exists()
    assert (aside / ".dream.json").read_text(encoding="utf-8") == '{"last": "2026-10-04"}'
    assert (aside / "PROPOSALS-2.md").read_text(encoding="utf-8").endswith("proposed\n")  # nothing overwritten
    assert (aside / "PROPOSALS.md").read_text(encoding="utf-8") == "an earlier retirement\n"
    assert (tmp_path / "a-note.md").is_file()  # a note is never the pass's
    assert "retired" not in AutoMemory(tmp_path).upgrade()  # the second start has nothing to move


def test_a_proposals_file_with_no_dream_state_beside_it_is_left_alone(tmp_path):
    (tmp_path / "PROPOSALS.md").write_text("# what the person wants to propose\n", encoding="utf-8")
    assert fmt.retire_removed(tmp_path) == []
    assert (tmp_path / "PROPOSALS.md").is_file()
    assert fmt.retire_removed(tmp_path / "no-such-dir") == []


def test_list_files_reads_what_a_model_wrote_with_write_file(tmp_path):
    (tmp_path / "deploy-ci.md").write_text(
        "---\nname: deploy-ci\ndescription: Deploys go through CI\nmetadata:\n  type: project\n---\nfact\n",
        encoding="utf-8",
    )
    [row] = AutoMemory(tmp_path).list_files()
    assert (row["type"], row["description"], row["title"]) == (
        "project", "Deploys go through CI", "Deploys go through CI"
    )


def test_upgrade_removes_a_header_stranded_mid_body_but_not_a_rule(tmp_path):
    """The old append mode concatenated a whole file, header and all (4 of 488 real
    files). A markdown horizontal rule is not a header and stays."""
    (tmp_path / "servers.md").write_text(
        "---\ntype: project\n---\nFirst fact.\n---\ntype: project\nmodified: 2026-09-18\n---\n"
        "Second fact.\n\n---\n\nAfter a rule.\n",
        encoding="utf-8",
    )
    fmt.upgrade_dir(tmp_path, reserved=("MEMORY.md",))
    text = (tmp_path / "servers.md").read_text(encoding="utf-8")
    _meta, body = fmt.split(text)
    assert "type: project" not in body and "modified:" not in body
    assert "First fact." in body and "Second fact." in body
    assert "\n---\n\nAfter a rule." in body
