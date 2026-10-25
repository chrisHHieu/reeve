"""Atomic writers for the model-facing file tools.

What is pinned, and why each one is a real failure rather than a nicety:
- a write lands whole or not at all (temp + fsync + `os.replace`), and no temp file is
  left behind when it fails;
- an existing file keeps its OWN conventions: CRLF stays CRLF, a UTF-8 BOM stays, and
  `replace_in_file` can match CRLF text with the LF text the model read;
- the mode survives a rewrite (an executable stays executable);
- containment is the same rule the reader uses, and a remote world gets NO local writers.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from harness.tools.file_writers import file_writers


def _tools(tmp_path: Path) -> dict:
    return {t.__name__: t for t in file_writers(str(tmp_path))}


def _raw(path: Path) -> bytes:
    return path.read_bytes()


# -- write_file --------------------------------------------------------------------


def test_a_new_file_is_utf8_lf_and_returns_a_relative_path(tmp_path):
    tools = _tools(tmp_path)
    out = tools["write_file"](path="notes/a.txt", content="hello\nworld\n")
    assert out == "notes/a.txt"
    assert _raw(tmp_path / "notes" / "a.txt") == b"hello\nworld\n"


def test_a_crlf_file_stays_crlf_and_keeps_its_bom(tmp_path):
    target = tmp_path / "win.txt"
    target.write_bytes(b"\xef\xbb\xbfone\r\ntwo\r\n")
    _tools(tmp_path)["write_file"](path="win.txt", content="one\ntwo\nthree\n")
    assert _raw(target) == b"\xef\xbb\xbfone\r\ntwo\r\nthree\r\n"


def test_the_mode_survives_a_rewrite(tmp_path):
    target = tmp_path / "run.sh"
    target.write_text("#!/bin/sh\necho old\n", encoding="utf-8")
    if os.name != "nt":
        target.chmod(0o755)
    _tools(tmp_path)["write_file"](path="run.sh", content="#!/bin/sh\necho new\n")
    assert target.read_text(encoding="utf-8") == "#!/bin/sh\necho new\n"
    if os.name != "nt":
        assert target.stat().st_mode & 0o111, "the executable bit was preserved"


def test_no_temp_file_is_left_behind(tmp_path):
    tools = _tools(tmp_path)
    tools["write_file"](path="a.txt", content="x")
    assert [p.name for p in tmp_path.iterdir()] == ["a.txt"]


def test_a_write_that_cannot_land_leaves_the_original_whole(tmp_path, monkeypatch):
    """The point of temp+replace: a failure in the middle costs the WRITE, not the file."""
    target = tmp_path / "keep.txt"
    target.write_text("original\n", encoding="utf-8")
    tools = _tools(tmp_path)

    import harness.tools.file_writers as writers

    real_replace = writers.os.replace

    def boom(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(writers.os, "replace", boom)
    with pytest.raises(OSError):
        tools["write_file"](path="keep.txt", content="clobbered\n")
    monkeypatch.setattr(writers.os, "replace", real_replace)
    assert target.read_text(encoding="utf-8") == "original\n"
    assert list(tmp_path.iterdir()) == [target], "the temp file was cleaned up"


def test_overwrite_false_refuses_an_existing_file(tmp_path):
    tools = _tools(tmp_path)
    tools["write_file"](path="a.txt", content="one")
    with pytest.raises(FileExistsError):
        tools["write_file"](path="a.txt", content="two", overwrite=False)


def test_a_write_outside_the_roots_is_refused(tmp_path):
    tools = _tools(tmp_path)
    with pytest.raises(PermissionError) as excinfo:
        tools["write_file"](path="../escape.txt", content="x")
    assert "outside the session's folders" in str(excinfo.value)
    assert not (tmp_path.parent / "escape.txt").exists()


# -- replace_in_file ---------------------------------------------------------------


def test_replace_matches_crlf_text_with_lf_needles(tmp_path):
    """The model reads a normalised view; an edit written against it must still land."""
    target = tmp_path / "win.txt"
    target.write_bytes(b"alpha\r\nbeta\r\ngamma\r\n")
    out = _tools(tmp_path)["replace_in_file"](
        path="win.txt", old="beta", new="BETA"
    )
    assert out["replacements"] == 1
    assert out["chars_before"] == len("alpha\nbeta\ngamma\n")
    assert _raw(target) == b"alpha\r\nBETA\r\ngamma\r\n", "CRLF survived the edit"


def test_a_mismatched_count_is_refused_with_the_numbers(tmp_path):
    target = tmp_path / "a.txt"
    target.write_text("x\nx\n", encoding="utf-8")
    with pytest.raises(ValueError) as excinfo:
        _tools(tmp_path)["replace_in_file"](path="a.txt", old="x", new="y")
    assert "Expected 1 replacement(s), found 2" in str(excinfo.value)
    assert target.read_text(encoding="utf-8") == "x\nx\n", "nothing was written"


def test_replace_all_writes_every_occurrence_atomically(tmp_path):
    target = tmp_path / "a.txt"
    target.write_text("x\nx\n", encoding="utf-8")
    out = _tools(tmp_path)["replace_in_file"](
        path="a.txt", old="x", new="y", expected_replacements=2
    )
    assert out["replacements"] == 2
    assert target.read_text(encoding="utf-8") == "y\ny\n"


def test_an_empty_needle_is_refused(tmp_path):
    (tmp_path / "a.txt").write_text("x", encoding="utf-8")
    with pytest.raises(ValueError):
        _tools(tmp_path)["replace_in_file"](path="a.txt", old="", new="y")


def test_a_missing_file_is_an_error_not_a_creation(tmp_path):
    with pytest.raises(ValueError):
        _tools(tmp_path)["replace_in_file"](path="nope.txt", old="a", new="b")


# -- the wiring --------------------------------------------------------------------


def test_a_remote_world_gets_no_local_writers():
    assert file_writers("C:/whatever", backend=object()) == []


def test_the_catalog_registers_our_writers_for_a_local_session(tmp_path):
    """The name-based contract/schema annotation is what makes a swap invisible; if the
    catalog ever goes back to the toolkit's `write_text`, this fails."""
    from harness.modes.agents.base import AgentContext
    from harness.modes.catalog import expand

    context = AgentContext(workspace=str(tmp_path), roots=None, executor=None)
    tools = {t.__name__: t for t in expand(["files"], context)}
    assert tools["write_file"].__module__ == "harness.tools.file_writers"
    assert tools["replace_in_file"].__module__ == "harness.tools.file_writers"
    # The borrowed contract/schema still rides along (that is how the card and the model
    # description stay identical to before).
    assert tools["write_file"].__tool_schema__["function"]["name"] == "write_file"
    assert tools["write_file"].__aisuite_tool_metadata__.requires_approval is True

# -- the matcher ladder --------------------------------------


def test_a_needle_copied_from_a_numbered_read_still_lands(tmp_path):
    """The most common failed edit: `old` pasted from what `read_file` showed, line numbers
    and all. The rung that matched is reported so the model knows it was not exact."""
    target = tmp_path / "a.py"
    target.write_text("def one():\n    return 1\n\ndef two():\n    return 2\n", encoding="utf-8")
    out = _tools(tmp_path)["replace_in_file"](
        path="a.py",
        old="     4\tdef two():\n     5\t    return 2\n",
        new="def two():\n    return 22\n",
    )
    assert out["replacements"] == 1
    assert "line numbers" in out["matched_via"]
    assert target.read_text(encoding="utf-8") == "def one():\n    return 1\n\ndef two():\n    return 22\n"


def test_literal_escape_sequences_are_understood_and_not_written_back(tmp_path):
    target = tmp_path / "a.py"
    target.write_text("one\ntwo\nthree\n", encoding="utf-8")
    out = _tools(tmp_path)["replace_in_file"](
        path="a.py", old="one\\ntwo", new="uno\\ndos"
    )
    assert "unescaping" in out["matched_via"]
    assert target.read_text(encoding="utf-8") == "uno\ndos\nthree\n"


def test_trailing_whitespace_in_the_file_does_not_fail_the_edit(tmp_path):
    target = tmp_path / "a.py"
    target.write_text("value = 1   \nother = 2\n", encoding="utf-8")
    out = _tools(tmp_path)["replace_in_file"](
        path="a.py", old="value = 1\nother = 2", new="value = 9\nother = 2"
    )
    assert "trailing whitespace" in out["matched_via"]
    assert target.read_text(encoding="utf-8") == "value = 9\nother = 2\n"


def test_an_exact_match_is_never_overridden_by_a_fallback(tmp_path):
    """The ladder stops at the first rung with the EXPECTED count, and exact is first: a
    file where the numbered reading would match twice must not win over the real one."""
    target = tmp_path / "a.py"
    target.write_text("x = 1\ny = 2\n", encoding="utf-8")
    out = _tools(tmp_path)["replace_in_file"](path="a.py", old="x = 1", new="x = 2")
    assert "matched_via" not in out, "an exact match says nothing extra"
    assert target.read_text(encoding="utf-8") == "x = 2\ny = 2\n"


def test_a_refusal_names_the_other_readings(tmp_path):
    """A model told "found 0" retries the same call; told which reading found one, it can
    fix the actual problem."""
    target = tmp_path / "a.py"
    target.write_text("alpha\nbeta\n", encoding="utf-8")
    with pytest.raises(ValueError) as excinfo:
        _tools(tmp_path)["replace_in_file"](
            path="a.py",
            old="     1\talpha\n     2\tbeta\n",
            new="gamma\n",
            expected_replacements=2,
        )
    message = str(excinfo.value)
    assert "Expected 2 replacement(s), found 0" in message
    assert "ignoring line numbers: 1" in message


def test_an_ambiguous_needle_is_still_refused_after_falling_through(tmp_path):
    target = tmp_path / "a.py"
    target.write_text("same\nsame\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Expected 1 replacement\\(s\\), found 2"):
        _tools(tmp_path)["replace_in_file"](path="a.py", old="same\n", new="other\n")


def test_replace_all_through_a_fallback_writes_every_occurrence(tmp_path):
    target = tmp_path / "a.py"
    target.write_text("a = 1   \nb = 1   \n", encoding="utf-8")
    out = _tools(tmp_path)["replace_in_file"](
        path="a.py", old="= 1\n", new="= 2\n", expected_replacements=2
    )
    assert out["replacements"] == 2
    assert target.read_text(encoding="utf-8") == "a = 2\nb = 2\n"


def test_a_rename_waits_out_a_brief_hold_on_windows(tmp_path, monkeypatch):
    # An indexer or a scan holding the target makes the rename fail for a moment on Windows: a
    # real session's edit of DESIGN.md failed with "Access is denied" (2026-10-19).
    from harness.tools import file_writers

    real = file_writers.os.replace
    tries = {"n": 0}

    def held(src, dst):
        tries["n"] += 1
        if tries["n"] <= 2:
            raise PermissionError(5, "Access is denied")
        return real(src, dst)

    monkeypatch.setattr(file_writers, "_WINDOWS", True)
    monkeypatch.setattr(file_writers, "_REPLACE_WAITS_S", (0.0, 0.0, 0.0))
    monkeypatch.setattr(file_writers.os, "replace", held)
    target = tmp_path / "DESIGN.md"
    target.write_text("old", encoding="utf-8")
    file_writers._atomic_write(target, b"new")
    assert target.read_text(encoding="utf-8") == "new" and tries["n"] == 3

    # A hold that does not end fails the write, and leaves no temp file behind.
    monkeypatch.setattr(file_writers.os, "replace", lambda src, dst: (_ for _ in ()).throw(PermissionError(5, "Access is denied")))
    import pytest

    with pytest.raises(PermissionError):
        file_writers._atomic_write(target, b"newer")
    assert target.read_text(encoding="utf-8") == "new"
    assert [p.name for p in tmp_path.iterdir()] == ["DESIGN.md"]

    # Off Windows a PermissionError is the file's own: no waiting.
    monkeypatch.setattr(file_writers, "_WINDOWS", False)
    tries["n"] = 0
    monkeypatch.setattr(file_writers.os, "replace", held)
    with pytest.raises(PermissionError):
        file_writers._atomic_write(target, b"newest")
    assert tries["n"] == 1
