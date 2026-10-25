"""`apply_patch` is ours, on one matcher with `replace_in_file`.

The toolkit's `apply_patch` wrote through `Path.write_text`, matched whole lines
exactly, and wrote each file as soon as it was done. Measured on this machine before the
change: an LF file came back CRLF on Windows, a hunk touching the first line of a BOM file
missed, a file whose line carried trailing spaces missed where `replace_in_file` would
have matched, and a patch that failed on its second file left the first one edited. Each
of those is pinned here, on real files, plus the envelope semantics the core owns.
"""

from __future__ import annotations

import pytest

from harness.tools.file_writers import file_writers
from harness.tools.patch_envelope import apply_hunks, parse_patch


def _patch(*body: str) -> str:
    return "\n".join(["*** Begin Patch", *body, "*** End Patch"]) + "\n"


@pytest.fixture
def apply_patch(tmp_path):
    return {t.__name__: t for t in file_writers(str(tmp_path))}["apply_patch"]


# -- the writer: the file keeps what it was ---------------------------------------------


def test_an_lf_file_stays_lf_and_a_crlf_file_stays_crlf(tmp_path, apply_patch):
    (tmp_path / "lf.py").write_bytes(b"a = 1\nb = 2\nc = 3\n")
    (tmp_path / "crlf.py").write_bytes(b"a = 1\r\nb = 2\r\nc = 3\r\n")
    for name in ("lf.py", "crlf.py"):
        apply_patch(patch=_patch(f"*** Update File: {name}", "@@", " a = 1", "-b = 2", "+b = 20", " c = 3"))
    assert (tmp_path / "lf.py").read_bytes() == b"a = 1\nb = 20\nc = 3\n"
    assert (tmp_path / "crlf.py").read_bytes() == b"a = 1\r\nb = 20\r\nc = 3\r\n"


def test_a_bom_file_keeps_its_bom_and_its_first_line_is_editable(tmp_path, apply_patch):
    (tmp_path / "bom.txt").write_bytes("\ufeffx = 1\ny = 2\n".encode("utf-8"))
    apply_patch(patch=_patch("*** Update File: bom.txt", "@@", "-x = 1", "+x = 10", " y = 2"))
    assert (tmp_path / "bom.txt").read_bytes() == "\ufeffx = 10\ny = 2\n".encode("utf-8")


def test_trailing_whitespace_in_the_file_matches_and_says_so(tmp_path, apply_patch):
    (tmp_path / "ws.py").write_bytes(b"def f():   \n    return 1\n")
    result = apply_patch(
        patch=_patch("*** Update File: ws.py", "@@", " def f():", "-    return 1", "+    return 2")
    )
    assert (tmp_path / "ws.py").read_bytes() == b"def f():\n    return 2\n"
    assert result["matched_via"] == "ws.py hunk 1: matched ignoring trailing whitespace"
    # ...on the line the model reads, not only in the result object.
    from harness.tools.files.renders import _patch_render

    assert _patch_render({}, result) == (
        "Applied patch: 1 file, 1 hunk (ws.py hunk 1: matched ignoring trailing whitespace)"
    )


def test_a_patch_that_misses_anywhere_writes_nothing(tmp_path, apply_patch):
    (tmp_path / "a.py").write_text("one\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("two\n", encoding="utf-8")
    patch = _patch(
        "*** Update File: a.py", "@@", "-one", "+ONE",
        "*** Update File: b.py", "@@", "-three", "+THREE",
        "*** Add File: c.py", "+new",
    )
    with pytest.raises(ValueError, match="Patch context does not match b.py"):
        apply_patch(patch=patch)
    assert (tmp_path / "a.py").read_text(encoding="utf-8") == "one\n"
    assert not (tmp_path / "c.py").exists()


def test_add_move_and_delete_in_one_envelope(tmp_path, apply_patch):
    (tmp_path / "old.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "gone.py").write_text("bye\n", encoding="utf-8")
    result = apply_patch(patch=_patch(
        "*** Add File: pkg/new.py", "+hello", "+world",
        "*** Update File: old.py", "*** Move to: moved.py", "@@", "-x = 1", "+x = 2",
        "*** Delete File: gone.py",
    ))
    assert (tmp_path / "pkg/new.py").read_bytes() == b"hello\nworld\n"
    assert (tmp_path / "moved.py").read_text(encoding="utf-8") == "x = 2\n"
    assert not (tmp_path / "old.py").exists() and not (tmp_path / "gone.py").exists()
    assert result["added_files"] == ["pkg/new.py"]
    assert result["changed_files"] == ["pkg/new.py", "moved.py"]
    assert sorted(result["deleted_files"]) == ["gone.py", "old.py"]
    assert (result["file_count"], result["hunk_count"]) == (3, 3)


def test_two_sections_for_one_file_both_land(tmp_path, apply_patch):
    (tmp_path / "f.py").write_text("a = 1\nb = 2\n", encoding="utf-8")
    apply_patch(patch=_patch(
        "*** Update File: f.py", "@@", "-a = 1", "+a = 10",
        "*** Update File: f.py", "@@", "-b = 2", "+b = 20",
    ))
    assert (tmp_path / "f.py").read_text(encoding="utf-8") == "a = 10\nb = 20\n"


def test_adding_over_an_existing_file_is_refused_with_the_way_out(tmp_path, apply_patch):
    (tmp_path / "here.py").write_text("x\n", encoding="utf-8")
    with pytest.raises(FileExistsError, match="Update File"):
        apply_patch(patch=_patch("*** Add File: here.py", "+y"))
    assert (tmp_path / "here.py").read_text(encoding="utf-8") == "x\n"


def test_a_path_outside_the_session_is_refused(tmp_path, apply_patch):
    with pytest.raises(PermissionError):
        apply_patch(patch=_patch("*** Add File: ../outside.py", "+x"))


def test_a_local_session_gets_this_apply_patch(tmp_path):
    from harness.modes.agents.base import AgentContext
    from harness.modes.catalog import expand

    tools = expand(["files"], AgentContext(workspace=tmp_path))
    patches = [t for t in tools if getattr(t, "__name__", "") == "apply_patch"]
    assert [t.__module__ for t in patches] == ["harness.tools.file_writers"]
    assert not [t for t in tools if getattr(t, "__name__", "") == "apply_unified_diff"]


def test_a_child_without_a_manager_gets_the_same_writers(tmp_path):
    from harness.tools.task_agents import _builtin_agents
    from harness.tools.task_runtime import build_child_engine

    engine = build_child_engine(
        workspace=tmp_path, provider=None, model="m", agent=_builtin_agents()["general"]
    )
    try:
        registry = engine.registry
        for name in ("apply_patch", "write_file", "replace_in_file"):
            spec = registry.get(name)
            assert spec is not None, name
            assert spec.func.__module__ == "harness.tools.file_writers", name
        assert registry.get("apply_unified_diff") is None
    finally:
        close = getattr(engine, "close", None)
        if callable(close):
            close()


# -- the envelope: the core's own terms -------------------------------------------------


def test_hunks_apply_in_order_each_searched_after_the_last():
    # The second hunk's `x` also sits BEFORE the first hunk: order is what picks the last.
    text = "x\ny\nx\n"
    hunks = parse_patch(_patch("*** Update File: f", "@@", "-y", "+Y", "@@", "-x", "+X"))[0].hunks
    assert apply_hunks(text, hunks, "f") == ("x\nY\nX\n", [])


def test_an_anchor_narrows_the_search_and_an_insert_goes_after_it():
    text = "class A:\n    x = 1\nclass B:\n    x = 1\n"
    hunks = parse_patch(_patch("*** Update File: f", "@@ class B:", "-    x = 1", "+    x = 2"))[0].hunks
    assert apply_hunks(text, hunks, "f")[0] == "class A:\n    x = 1\nclass B:\n    x = 2\n"
    insert = parse_patch(_patch("*** Update File: f", "@@ class A:", "+    y = 0"))[0].hunks
    assert apply_hunks(text, insert, "f")[0].startswith("class A:\n    y = 0\n    x = 1\n")
    append = parse_patch(_patch("*** Update File: f", "@@", "+tail = 1"))[0].hunks
    assert apply_hunks(text, append, "f")[0].endswith("    x = 1\ntail = 1\n")


def test_end_of_file_pins_a_hunk_to_the_end():
    text = "x\nmid\nx\n"
    hunks = parse_patch(_patch("*** Update File: f", "@@", "-x", "+END", "*** End of File"))[0].hunks
    assert apply_hunks(text, hunks, "f")[0] == "x\nmid\nEND\n"


def test_a_blank_line_in_a_hunk_is_blank_context():
    text = "a\n\nb\n"
    hunks = parse_patch(_patch("*** Update File: f", "@@", " a", "", "-b", "+B"))[0].hunks
    assert apply_hunks(text, hunks, "f")[0] == "a\n\nB\n"


def test_reader_line_numbers_are_matched_and_never_written():
    text = "def f():\n    return 1\n"
    hunks = parse_patch(_patch(
        "*** Update File: f", "@@", "      1\tdef f():", "-     2\t    return 1", "+     2\t    return 2",
    ))[0].hunks
    new, notes = apply_hunks(text, hunks, "f")
    assert new == "def f():\n    return 2\n"
    assert notes == ["hunk 1: matched ignoring the line numbers in `old`"]


def test_a_miss_names_its_hunk_and_where_the_file_comes_closest():
    text = "def mul(a, b):\n    return a * b\n"
    hunks = parse_patch(_patch("*** Update File: f", "@@", " def mul(a, b):", "-    return a*b", "+    return b * a"))[0].hunks
    with pytest.raises(ValueError) as caught:
        apply_hunks(text, hunks, "f")
    message = str(caught.value)
    assert message.startswith("Patch context does not match f — hunk 1 of 1 does not match:")
    assert "first difference at line 2" in message


@pytest.mark.parametrize(
    ("patch", "error"),
    [
        ("*** Update File: f\n@@\n-x\n+y\n*** End Patch\n", "must start with"),
        ("*** Begin Patch\n*** Update File: f\n@@\n-x\n", "must end with"),
        (_patch("*** Frobnicate: f"), "Unknown patch operation"),
        (_patch("*** Update File: f", "@@", "?x"), "Invalid update patch line"),
        (_patch("*** Update File: f"), "no hunks"),
        (_patch(), "no file operations"),
    ],
)
def test_a_malformed_envelope_says_what_is_wrong(patch, error):
    with pytest.raises(ValueError, match=error):
        parse_patch(patch)
