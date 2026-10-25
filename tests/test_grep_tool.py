"""`grep` has to say WHERE, and on Windows it was saying "D".

Found by measurement, not by reading. Three navigation questions were put to a real model
on this repository. The two hard ones — an aliased import, and a `.resolve()` whose own
comment names a different `resolve` — were answered correctly in four tool calls each. The
easy control, "which file defines `friendly_model_error`", took twenty-nine and came out
wrong, with the agent reporting that "grep looks unreliable here".

It was not unreliable. It parsed `path:line:text` by splitting on the first colon, and an
absolute Windows path begins `D:\\…`, so every match came back as

    {"file": "D", "line": 0, "text": "30:from .providers.errors import …"}

Nothing raised. Every match was present. They simply stopped saying which file they were
in, which is the whole job — and a wrong answer that looks like an answer is the failure
mode this codebase keeps having to design against.

The tests are on the PARSER rather than the filesystem, because that is where the defect
was and because it pins the exact shape: real ripgrep JSON, a real Windows path, asserted
down to the line number. The integration test that follows is a second opinion, and skips
where a temp directory happens to sit inside one of the ignored trees.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from harness.tools.search import _parse_rg, search_tools
from harness.tools.search.ignore import IGNORE_DIRS as _IGNORE_DIRS


def _rg_match(path: str, line: int, text: str) -> str:
    """One `--json` match event, in ripgrep's own shape."""
    return json.dumps(
        {
            "type": "match",
            "data": {
                "path": {"text": path},
                "lines": {"text": text + "\n"},
                "line_number": line,
                "absolute_offset": 0,
                "submatches": [],
            },
        }
    )


# -- the regression ----------------------------------------------------------------


def test_a_windows_path_is_a_path_and_not_a_drive_letter():
    """The exact defect. `D:\\…` split on its first colon gave file "D", line 0, and the
    line number smuggled into the text — which is how a model ends up unable to say where
    anything lives while every match is technically present."""
    root = Path(r"D:\Project\reeve")
    stdout = _rg_match(
        r"D:\Project\reeve\harness\engine.py",
        30,
        "from .providers.errors import friendly_model_error",
    )

    parsed = _parse_rg(stdout, root, 50)
    hit = parsed["matches"][0]

    assert hit["file"].endswith("engine.py")
    assert hit["file"] != "D"
    assert hit["line"] == 30
    assert hit["text"].startswith("from .providers.errors")
    assert not hit["text"].startswith("30:")


def test_a_colon_inside_a_filename_is_not_a_separator_either():
    """The general form. POSIX allows a colon in a filename, so this was never only a
    Windows problem — it was a parser treating something as a separator that is not one."""
    root = Path("/work")
    stdout = _rg_match("/work/od:d.py", 7, "needle = 1")

    hit = _parse_rg(stdout, root, 50)["matches"][0]
    assert hit["file"].endswith("od:d.py")
    assert hit["line"] == 7


def test_text_containing_colons_survives_intact():
    root = Path("/work")
    stdout = _rg_match("/work/a.py", 3, 'url = "https://example.com:8443/x"')

    hit = _parse_rg(stdout, root, 50)["matches"][0]
    assert hit["text"] == 'url = "https://example.com:8443/x"'
    assert hit["line"] == 3


def test_non_match_events_are_ignored():
    """ripgrep's stream also carries `begin`, `end` and `summary` events. Counting them
    as matches would inflate every result."""
    root = Path("/work")
    stdout = "\n".join(
        [
            json.dumps({"type": "begin", "data": {"path": {"text": "/work/a.py"}}}),
            _rg_match("/work/a.py", 1, "hit"),
            json.dumps({"type": "end", "data": {"path": {"text": "/work/a.py"}}}),
            json.dumps({"type": "summary", "data": {}}),
        ]
    )
    parsed = _parse_rg(stdout, root, 50)
    assert parsed["count"] == 1


def test_a_malformed_line_does_not_fail_the_search():
    """One unparseable line must cost that line, not the other results."""
    root = Path("/work")
    stdout = "\n".join(["{not json", _rg_match("/work/a.py", 2, "hit"), ""])
    assert _parse_rg(stdout, root, 50)["count"] == 1


def test_the_cap_is_honoured():
    root = Path("/work")
    stdout = "\n".join(_rg_match("/work/a.py", i, "hit") for i in range(1, 30))
    assert len(_parse_rg(stdout, root, 5)["matches"]) == 5


# -- and once, end to end ----------------------------------------------------------


def test_a_match_keeps_its_non_ascii_text(tmp_path, monkeypatch):
    """ripgrep emits UTF-8, and `text=True` alone decoded it with the locale codec (cp1252
    on Windows), so every Vietnamese match line came back mojibake — `Cầu trúc` read as
    `Cáº§u trÃºc` — and nothing raised, so the model just saw corrupted source."""
    if not shutil.which("rg"):
        pytest.skip("ripgrep not installed; the pure-Python fallback always decoded UTF-8")
    import harness.tools.search as search_mod

    monkeypatch.setattr(search_mod.ignore, "IGNORE_DIRS", set())  # the temp tree is not the subject
    (tmp_path / "note.py").write_text("Cầu trúc nợ\n", encoding="utf-8")

    out = search_mod.search_tools(str(tmp_path))[0](pattern="Cầu trúc", path=".")
    assert out["count"] == 1
    assert out["matches"][0]["text"] == "Cầu trúc nợ"


def test_the_real_tool_reports_a_usable_location(tmp_path):
    if not shutil.which("rg"):
        pytest.skip("ripgrep not installed; the pure-Python fallback never had this bug")
    if any(part in _IGNORE_DIRS for part in tmp_path.parts):
        # On Windows pytest's tmp lives under AppData, which grep excludes on purpose —
        # scratch workspaces live in ~/Reeve, so this is the test's problem only.
        pytest.skip(f"temp dir sits inside an ignored tree: {tmp_path}")

    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "alpha.py").write_text(
        "import os\n\n\ndef find_me(x):\n    return x\n", encoding="utf-8"
    )

    matches = search_tools(str(tmp_path))[0](pattern="def find_me")["matches"]
    assert len(matches) == 1
    hit = matches[0]
    assert hit["file"].endswith("alpha.py") and len(hit["file"]) > 3
    lines = (tmp_path / hit["file"]).read_text(encoding="utf-8").splitlines()
    assert "def find_me" in lines[hit["line"] - 1]
