"""`present` — the declaration tool behind the GUI's delivery row.

What is pinned, and why each one is a real failure rather than a nicety:

- a declaration of an existing file comes back with the card the GUI reads, description
  included, because the description is the model's own words and nothing downstream can
  derive them;
- every refusal NAMES the path and says what to do — a model told only "refused" retries
  the same spelling, and a directory declared as a deliverable opens as an empty preview;
- the ceiling is 8 with 4 RECOMMENDED,
  so a model that dumps its whole output directory is told to pick;
- a remote world gets NO local `present`: the tool resolves this machine's paths, so it
  would validate the controller's disk while the deliverable lives on the other host.
"""

from __future__ import annotations

from pathlib import Path

from harness.tools import cards
from harness.tools.contract import safe_present_result, safe_render
from harness.tools.present import PRESENT_CONTRACT, present_tools


def _tool(tmp_path: Path):
    (tool,) = present_tools(str(tmp_path))
    return tool


def _names(tools) -> set:
    return {getattr(t, "__name__", "") for t in tools}


# -- a declaration that lands -------------------------------------------------------


def test_a_valid_file_returns_the_declaration_and_its_card(tmp_path):
    (tmp_path / "q3.docx").write_text("invented", encoding="utf-8")
    tool = _tool(tmp_path)

    result = tool(files=[{"path": "q3.docx", "description": "Quarterly figures"}])
    assert result["files"] == [{"path": "q3.docx", "description": "Quarterly figures"}]

    shown = safe_present_result(
        PRESENT_CONTRACT, "present", {"files": []}, result, result["_card_meta"]
    )
    # The card is what the GUI stacks under the turn (`Deliverables.tsx`: `card.card ===
    # "files"`, one row per entry, order kept).
    assert shown.card == {
        "card": "files",
        "files": [{"path": "q3.docx", "description": "Quarterly figures"}],
    }
    cards.validate(shown.card)
    assert shown.kind == "write"
    assert shown.summary


def test_a_declaration_without_a_description_still_gets_a_card(tmp_path):
    (tmp_path / "notes.md").write_text("invented", encoding="utf-8")
    tool = _tool(tmp_path)

    result = tool(files=[{"path": "notes.md"}])
    assert result["files"] == [{"path": "notes.md", "description": None}]
    shown = safe_present_result(PRESENT_CONTRACT, "present", {}, result, result["_card_meta"])
    # None, not a missing key: the wire shape is fixed (`cards.py` `files_result`) and the
    # surface already reads an absent/None description as "the model did not explain it".
    assert shown.card["files"] == [{"path": "notes.md", "description": None}]


def test_the_card_keeps_the_order_the_model_declared(tmp_path):
    for name in ("a.md", "b.md", "c.md"):
        (tmp_path / name).write_text("invented", encoding="utf-8")
    tool = _tool(tmp_path)

    result = tool(files=[{"path": "c.md"}, {"path": "a.md"}, {"path": "b.md"}])
    assert [entry["path"] for entry in result["_card_meta"]["files"]["files"]] == [
        "c.md",
        "a.md",
        "b.md",
    ]


def test_a_subdirectory_is_declared_relative_to_the_workspace(tmp_path):
    (tmp_path / "reports").mkdir()
    (tmp_path / "reports" / "q3.docx").write_text("invented", encoding="utf-8")
    tool = _tool(tmp_path)

    result = tool(files=[{"path": "reports/q3.docx"}])
    # POSIX separators whatever the host: the path is a wire value the GUI resolves.
    assert result["files"][0]["path"] == "reports/q3.docx"


def test_a_file_in_an_added_root_is_declared_by_its_absolute_path(tmp_path):
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    granted = tmp_path / "granted"
    granted.mkdir()
    delivered = granted / "summary.md"
    delivered.write_text("invented", encoding="utf-8")
    (tool,) = present_tools(str(scratch), roots=[granted])

    result = tool(files=[{"path": str(delivered)}])
    assert result["files"][0]["path"] == str(delivered)


# -- refusals ------------------------------------------------------------------------


def test_a_missing_file_is_refused_by_name_with_the_remedy(tmp_path):
    tool = _tool(tmp_path)

    result = tool(files=[{"path": "ghost.docx"}])
    assert "ghost.docx" in result["error"]
    assert "file not found" in result["error"]
    assert "retry" in result["error"] and "create" in result["error"]


def test_a_directory_is_refused(tmp_path):
    (tmp_path / "reports").mkdir()
    tool = _tool(tmp_path)

    result = tool(files=[{"path": "reports"}])
    assert "reports" in result["error"]
    assert "directory" in result["error"]


def test_a_path_escaping_the_session_roots_is_refused(tmp_path):
    outside = tmp_path.parent / "outside-the-session.txt"
    outside.write_text("invented", encoding="utf-8")
    session = tmp_path / "session"
    session.mkdir()
    tool = _tool(session)

    result = tool(files=[{"path": str(outside)}])
    # The same sentence every file tool refuses with, naming the directories that work.
    assert "outside the session's folders" in result["error"]
    assert str(session) in result["error"]


def test_a_blank_path_is_refused(tmp_path):
    tool = _tool(tmp_path)

    result = tool(files=[{"path": "   "}])
    assert "error" in result
    assert "non-empty file path" in result["error"]


def test_an_empty_list_is_refused(tmp_path):
    tool = _tool(tmp_path)

    result = tool(files=[])
    assert "error" in result
    assert "non-empty file path" in result["error"]


def test_five_files_are_allowed_even_though_four_are_recommended(tmp_path):
    # 4 is the per-call ADVICE (the schema description and the system prompt say so); the
    # enforced ceiling is 8. A tool that refused five would make the
    # ceiling untestable and the card's fold-at-four unreachable.
    for index in range(5):
        (tmp_path / f"part-{index}.md").write_text("invented", encoding="utf-8")
    tool = _tool(tmp_path)

    result = tool(files=[{"path": f"part-{index}.md"} for index in range(5)])
    assert len(result["files"]) == 5


def test_more_than_eight_files_are_refused(tmp_path):
    for index in range(9):
        (tmp_path / f"part-{index}.md").write_text("invented", encoding="utf-8")
    tool = _tool(tmp_path)

    result = tool(files=[{"path": f"part-{index}.md"} for index in range(9)])
    assert "1 to 8" in result["error"]


def test_a_single_entry_object_is_read_as_one_declaration(tmp_path):
    (tmp_path / "only.md").write_text("invented", encoding="utf-8")
    tool = _tool(tmp_path)

    # The schema asks for an array; a model that sends the entry alone is asking the same
    # thing, and refusing it would cost a turn over a shape the tool can read.
    result = tool(files={"path": "only.md", "description": "The one"})
    assert result["files"] == [{"path": "only.md", "description": "The one"}]


def test_the_model_reads_one_line_per_file_not_the_json_object(tmp_path):
    (tmp_path / "q3.docx").write_text("invented", encoding="utf-8")
    tool = _tool(tmp_path)
    result = tool(files=[{"path": "q3.docx", "description": "Quarterly figures"}])

    rendered = safe_render(PRESENT_CONTRACT, "present", {}, result)
    assert rendered == "Presented q3.docx — Quarterly figures"


def test_a_refusal_renders_as_the_sentence_the_model_needs(tmp_path):
    tool = _tool(tmp_path)
    result = tool(files=[{"path": "ghost.docx"}])

    rendered = safe_render(PRESENT_CONTRACT, "present", {}, result)
    assert rendered.startswith("Error: Cannot present ghost.docx: file not found.")


# -- purity, containment and the world -----------------------------------------------


def test_the_result_presenter_asks_the_filesystem_nothing(tmp_path, monkeypatch):
    """A presenter that stats would render the same call differently on replay."""
    (tmp_path / "gone.md").write_text("invented", encoding="utf-8")
    tool = _tool(tmp_path)
    result = tool(files=[{"path": "gone.md"}])
    (tmp_path / "gone.md").unlink()

    shown = safe_present_result(PRESENT_CONTRACT, "present", {}, result, result["_card_meta"])
    assert shown.card["files"][0]["path"] == "gone.md"


def test_a_remote_world_gets_no_local_present(tmp_path):
    # Same rule as `file_writers`: the tool resolves THIS machine's paths, and a remote
    # session's deliverable lives on the other host.
    assert present_tools(str(tmp_path), backend=object()) == []


def test_build_engine_registers_present_for_a_local_workspace(tmp_path):
    from harness.compose import build_engine
    from harness.modes.agents.reeve import reeve_agent

    engine = build_engine(agent=reeve_agent(), workspace=tmp_path, provider=object())
    assert "present" in engine.registry.names()
    schema = engine.registry.get("present").schema
    assert schema["function"]["name"] == "present"
    assert schema["function"]["parameters"]["required"] == ["files"]


def test_a_rootless_session_registers_no_present(tmp_path):
    # No local workspace → nothing to resolve paths against, so the tool is absent rather
    # than registered to always refuse (the same choice the plan file tool makes).
    from harness.compose import build_engine
    from harness.modes.agents.reeve import reeve_agent

    engine = build_engine(
        agent=reeve_agent(),
        workspace=None,
        roots=[{"path": "/home/dev/project", "writable": True, "label": "workspace"}],
        provider=object(),
    )
    assert "present" not in _names(engine.registry.names())
