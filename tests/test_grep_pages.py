"""`grep` in pages, by type, across lines and in either case — the same on every engine.

The store showed why these exist: 158 of 238 searches the model wrote as shell commands
(2026-09-28 to 10-12) piped into `head`/`tail`/`Select-Object` to page through results the
tool would only cut, and the result told it to narrow the search, never how to read on.
"""

from __future__ import annotations

import shutil

import pytest

from harness.tools.contract import contract_of, safe_facts
from harness.tools.grep_engines import TYPES
from harness.tools.search import search_tools
from harness.tools.search.remote import remote_grep


def _tree(root):
    (root / "pkg").mkdir()
    for name, body in {
        "a.py": "needle 1\nx\nneedle 2\n",
        "b.py": "needle 3\n",
        "pkg/c.py": "y\nneedle 4\nNEEDLE 5\n",
        "pkg/d.ts": "needle 6\n",
        "pkg/e.tsx": "needle 7\n",
        "notes.md": "@route('/x')\n\ndef handler():\n    pass\n",
    }.items():
        (root / name).write_text(body, encoding="utf-8")


def _engines(monkeypatch):
    """Both engines: ripgrep when this machine has it, and the Python walk."""
    found = []
    if shutil.which("rg"):
        found.append("ripgrep")
    found.append("python")
    return found


def _grep(root, monkeypatch, engine):
    import harness.tools.search as search

    if engine == "python":
        monkeypatch.setattr(search, "carried_rg", lambda: None)
        monkeypatch.setattr(search.shutil, "which", lambda name: None)
    return search_tools(str(root))[0]


@pytest.mark.parametrize("engine", ["ripgrep", "python"])
def test_pages_cover_every_match_once_in_one_order(tmp_path, monkeypatch, engine):
    if engine == "ripgrep" and not shutil.which("rg"):
        pytest.skip("no ripgrep on this machine")
    _tree(tmp_path)
    grep = _grep(tmp_path, monkeypatch, engine)
    seen, flags = [], []
    for offset in (0, 2, 4):
        page = grep(pattern="needle", max_results=2, offset=offset)
        assert page["offset"] == offset
        seen += [(m["file"], m["line"]) for m in page["matches"] if not m.get("context")]
        flags.append(page["truncated"])
    assert len(seen) == len(set(seen)) == 6  # case-sensitive: NEEDLE 5 is not one
    assert flags == [True, True, False]
    # The same search twice lists the same first page: a page means something.
    first = grep(pattern="needle", max_results=2)["matches"]
    assert first == grep(pattern="needle", max_results=2)["matches"]


@pytest.mark.parametrize("engine", ["ripgrep", "python"])
def test_case_type_and_lines_around(tmp_path, monkeypatch, engine):
    if engine == "ripgrep" and not shutil.which("rg"):
        pytest.skip("no ripgrep on this machine")
    _tree(tmp_path)
    grep = _grep(tmp_path, monkeypatch, engine)

    folded = grep(pattern="needle 5", ignore_case=True)
    assert [m["text"] for m in folded["matches"]] == ["NEEDLE 5"]

    # 'ts' is every TypeScript extension: a `*.ts` glob would have left out e.tsx.
    typed = grep(pattern="needle", type="ts", output_mode="files_with_matches")
    assert sorted(f.replace("\\", "/") for f in typed["files"]) == ["pkg/d.ts", "pkg/e.tsx"]
    assert grep(pattern="needle", type="cobol")["error"].startswith("unknown type 'cobol'")

    around = grep(pattern="needle 2", before=2, after=0)
    lines = [(m["line"], bool(m.get("context"))) for m in around["matches"]]
    assert lines == [(1, True), (2, True), (3, False)]


@pytest.mark.parametrize("engine", ["ripgrep", "python"])
def test_a_multiline_pattern_finds_a_decorator_and_its_function(tmp_path, monkeypatch, engine):
    if engine == "ripgrep" and not shutil.which("rg"):
        pytest.skip("no ripgrep on this machine")
    _tree(tmp_path)
    grep = _grep(tmp_path, monkeypatch, engine)
    single = grep(pattern=r"@route.*\n\s*\ndef handler")  # one line at a time: never found
    assert single.get("count") == 0 or "multiline=true" in single["error"]
    found = grep(pattern=r"@route.*\n\s*\ndef handler", multiline=True)
    assert found["count"] == 1
    rows = found["matches"]
    assert rows[0]["line"] == 1 and [r["line"] for r in rows] == [1, 2, 3]
    assert all(r.get("cont") for r in rows[1:])


def test_the_page_says_where_the_next_one_starts_and_the_log_counts_it(tmp_path, monkeypatch):
    _tree(tmp_path)
    grep = _grep(tmp_path, monkeypatch, "python")
    from harness.tools.grep_present import _grep_render

    args = {"pattern": "needle", "max_results": 2, "offset": 2}
    page = grep(**args)
    text = _grep_render(args, page)
    assert "from result 3" in text and "offset=4" in text
    facts = safe_facts(contract_of(grep), "grep", args, page)
    assert facts["capped"] is True and facts["offset"] == 2 and facts["shown"] == 2


def test_types_are_one_table():
    assert "*.tsx" in TYPES["ts"] and "*.pyi" in TYPES["py"]


class _Backend:
    def __init__(self, hits):
        self.hits, self.calls = hits, []

    def search_files(self, query, path, **kw):
        self.calls.append(kw)
        return self.hits[: kw["max_results"]]


def test_a_remote_search_pages_folds_case_and_refuses_multiline_in_words():
    hits = [{"path": f"f{i}.py", "line": i, "text": "needle"} for i in range(1, 6)]
    backend = _Backend(hits)
    page = remote_grep(backend, "needle", ".", None, 2, offset=2, ignore_case=True, types=("*.py",))
    assert [m["line"] for m in page["matches"]] == [3, 4] and page["truncated"] is True
    assert backend.calls[0]["ignore_case"] is True and backend.calls[0]["includes"] == ["*.py"]
    refused = remote_grep(backend, "a\nb", ".", None, 2, multiline=True)
    assert "multiline search is not available" in refused["error"]
