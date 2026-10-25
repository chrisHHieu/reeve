"""The tools the agent reads code with: `grep` (ripgrep, or Python when there is none), the windowed
`read_file`, and `web_fetch`. Nothing is fetched: `web_fetch` is checked through its address guard
and the page-to-text step, and `grep` runs over temporary folders.
"""

from __future__ import annotations

from types import SimpleNamespace

from harness.integrations.web.fetch import _html_to_markdown, make_web_fetch_tool
from harness.tools.files import file_tools
from harness.tools.search import _py_grep, search_tools

# -- finding text -------------------------------------------------------------------------------


def _seed(tmp_path):
    """Two files that say hello, and a third under node_modules that no search should see."""
    (tmp_path / "a.py").write_text("def hello():\n    return 42\n", encoding="utf-8")
    (tmp_path / "b.txt").write_text("hello world\nbye\n", encoding="utf-8")
    dependency = tmp_path / "node_modules" / "pkg"
    dependency.mkdir(parents=True)
    (dependency / "junk.py").write_text("hello from deps\n", encoding="utf-8")


def _grep(tmp_path):
    return search_tools(str(tmp_path))[0]


def _read_file(tmp_path):
    return file_tools(str(tmp_path))[0]


def test_grep_finds_matches_and_respects_glob(tmp_path):
    _seed(tmp_path)
    grep = _grep(tmp_path)
    found = {match["file"] for match in grep(pattern="hello")["matches"]}
    assert {"a.py", "b.txt"} <= found and not any("node_modules" in name for name in found)
    python = grep(pattern="hello", glob="*.py")["matches"]
    assert all(match["file"].endswith(".py") and "node_modules" not in match["file"] for match in python)
    assert python[0]["line"] == 1


def test_ripgrep_uses_the_same_ignored_dirs_as_the_python_fallback(tmp_path, monkeypatch):
    import subprocess

    import harness.tools.search as search

    ran: list[list[str]] = []
    monkeypatch.setattr(search.shutil, "which", lambda name: "rg")
    # ripgrep is run from `grep_engines` through `spawn.run`, which calls `subprocess.run`: patched
    # on the module every caller shares
    monkeypatch.setattr(subprocess, "run", lambda command, **kwargs: ran.append(command) or SimpleNamespace(returncode=0, stdout="", stderr=""))
    _grep(tmp_path)(pattern="hello", glob="*.py")
    assert ran
    [command] = ran[:1]
    after = command.index("*.py")
    root_parts = {part.lower() for part in tmp_path.resolve().parts}
    for ignored in search.ignore.IGNORE_DIRS:
        # A name the search root itself lies under is left out: on Windows pytest's tmp_path is
        # under AppData, and `!**/AppData/**` matched the root and quietly excluded everything.
        if ignored.lower() in root_parts:
            assert f"!**/{ignored}/**" not in command
        else:
            assert command.index(f"!**/{ignored}/**") > after


def test_grep_rejects_path_escape(tmp_path):
    assert "outside the session's folders" in _grep(tmp_path)(pattern="x", path="../..")["error"]


def test_grep_says_a_missing_path_is_missing_and_that_path_takes_one(tmp_path):
    """ripgrep's "IO error for operation on a|b" named no cause (2026-10-15)."""
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    grep = search_tools(str(tmp_path))[0]
    joined = grep(pattern="x", path="a|b")["error"]
    assert "no file or folder at 'a|b'" in joined and "ONE folder" in joined
    assert "IO error" not in joined
    assert "no file or folder at 'nope'" in grep(pattern="x", path="nope")["error"]
    assert "error" not in grep(pattern="x", path="a")


def test_py_grep_fallback_skips_ignored_dirs(tmp_path):
    _seed(tmp_path)
    found = _py_grep(tmp_path.resolve(), tmp_path.resolve(), "hello", None, 100)
    assert found["count"] == 2 and all("node_modules" not in match["file"] for match in found["matches"])


# -- reading a file -----------------------------------------------------------------------------


def test_read_file_numbers_lines(tmp_path):
    (tmp_path / "a.py").write_text("one\ntwo\nthree\n", encoding="utf-8")
    window = _read_file(tmp_path)(path="a.py")
    assert (window["total_lines"], window["start_line"], window["end_line"]) == (3, 1, 3)
    assert window["content"].splitlines()[0].endswith("1\tone")
    assert "note" not in window  # nothing left to read


def test_read_file_windows_and_tells_how_to_continue(tmp_path):
    (tmp_path / "big.txt").write_text("\n".join(f"line{n}" for n in range(1, 51)) + "\n", encoding="utf-8")
    window = _read_file(tmp_path)(path="big.txt", start_line=5, max_lines=10)
    assert (window["start_line"], window["end_line"], window["total_lines"]) == (5, 14, 50)
    assert "line5" in window["content"] and "line15" not in window["content"]
    assert "start_line=15" in window["note"]


def test_read_file_truncates_long_lines(tmp_path):
    (tmp_path / "wide.txt").write_text("x" * 2000 + "\n", encoding="utf-8")
    shown = _read_file(tmp_path)(path="wide.txt")["content"]
    assert "(line truncated)" in shown and len(shown) < 1000


def test_read_file_stops_at_the_total_budget_and_names_the_continuation(tmp_path):
    """The tool caps itself UNDER the engine's per-result ceiling, so a read is never
    shrunk into a spill stub the model has to recover from — it gets the window it
    asked for and the start_line to continue at."""
    (tmp_path / "wide.txt").write_text(
        "\n".join("x" * 500 for _ in range(200)) + "\n", encoding="utf-8"
    )
    read_file = file_tools(str(tmp_path))[0]
    out = read_file(path="wide.txt")

    assert out["total_lines"] == 200
    assert out["end_line"] < out["total_lines"]  # the budget stopped it
    assert len(out["content"]) <= 40_600  # one entry over the line, at most
    assert f"start_line={out['end_line'] + 1}" in out["note"]
    # The window itself is still exact, line numbers and all.
    assert out["content"].splitlines()[0].endswith("1\t" + "x" * 500)


def test_read_file_budget_never_returns_an_empty_window(tmp_path):
    # A single line larger than the whole budget: the first entry is always admitted
    # (clipped to _MAX_LINE_CHARS), so "over budget" can never mean "no window at all".
    (tmp_path / "one.txt").write_text("z" * 550_000, encoding="utf-8")
    read_file = file_tools(str(tmp_path))[0]
    out = read_file(path="one.txt")

    assert out["start_line"] == 1 and out["end_line"] == 1
    assert "(line truncated)" in out["content"]
    assert "note" not in out  # the single line was shown (clipped), nothing left to page


def test_read_file_rejects_escape_and_non_files(tmp_path):
    read_file = _read_file(tmp_path)
    assert "outside the session's folders" in read_file(path="../secret.txt")["error"]
    assert "does not exist" in read_file(path="missing.txt")["error"]


def test_a_read_that_misses_says_which_miss_it_was(tmp_path):
    """A directory and a missing file used to share one sentence ("not a file"), and the
    recoveries differ. Six real misses were guessed names one extension or letter off."""
    (tmp_path / "lib").mkdir()
    (tmp_path / "lib" / "utils.tsx").write_text("x", encoding="utf-8")
    (tmp_path / "lib" / "unrelated.md").write_text("x", encoding="utf-8")
    read_file = file_tools(str(tmp_path))[0]

    folder = read_file(path="lib")["error"]
    assert "is a directory" in folder and "list_files" in folder

    near = read_file(path="lib/utils.ts")["error"]
    assert near.startswith("file does not exist: lib/utils.ts")
    assert "did you mean: lib/utils.tsx" in near and "unrelated" not in near

    lost = read_file(path="nowhere/deep/file.py")["error"]
    assert "does not exist" in lost and "folder does not exist either" in lost
    assert "did you mean" not in read_file(path="lib/zzzzzz.bin")["error"]


# -- reading a page -----------------------------------------------------------------------------


def test_web_fetch_rejects_non_http():
    web_fetch = make_web_fetch_tool()
    for url in ("file:///etc/passwd", "javascript:alert(1)"):
        assert "http" in web_fetch(url)["error"]


def test_html_to_markdown_keeps_structure_and_drops_noise():
    page = "<html><head><style>x{}</style></head><body><h1>Hi</h1><script>bad()</script><p>Body <b>text</b></p><div hidden>closed banner</div><p style='display:none'>nope</p></body></html>"
    text = _html_to_markdown(page)
    assert "# Hi" in text and "Body **text**" in text
    for hidden in ("bad()", "x{}", "closed banner", "nope"):  # script, style, and what is not shown
        assert hidden not in text


def test_html_to_text_keeps_blocks_and_drops_markup():
    from harness.integrations.web.fetch import _html_to_text

    text = _html_to_text("<h1>Hi</h1><p>One</p><p>Two</p><script>x()</script>")
    assert "Hi" in text and "One" in text and "Two" in text
    assert "<" not in text and "x()" not in text


def test_charset_sniffing():
    from harness.integrations.web.fetch import _declared_charset, _decode_body, _sniff_meta_charset

    assert _declared_charset("text/html; charset=gbk") == "gbk"
    assert _declared_charset("text/html") is None
    assert _sniff_meta_charset(b'<meta charset="windows-1251">') == "windows-1251"
    assert _sniff_meta_charset(b"<meta http-equiv='Content-Type' content='text/html; charset=Big5'>") == "Big5"
    assert _sniff_meta_charset(b"<p>no charset</p>") is None
    # Header charset wins; the meta declaration is the fallback; both absent → utf-8.
    raw = "héllo".encode("cp1252")
    assert _decode_body(raw, "text/html", "cp1252") == "héllo"
    assert _decode_body(raw, "text/html; charset=cp1252", None) == "héllo"
    assert _decode_body(b"<meta charset='utf-8'>hi", "text/html", None).endswith("hi")


class _FakeResp:
    def __init__(self, status, body=b"<p>ok</p>", ctype="text/html; charset=utf-8"):
        self.status_code = status
        self.content = body
        self.headers = {"content-type": ctype}
        self.encoding = "utf-8"
        self.extensions = {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def test_web_fetch_retries_a_403_once_with_a_browser_ua(monkeypatch):
    import harness.integrations.web.fetch as fetch_mod

    uas = []

    def fake_get(client, url, **_kw):
        uas.append(client.headers.get("User-Agent"))
        return _FakeResp(403 if len(uas) == 1 else 200)

    monkeypatch.setattr(fetch_mod, "get_checked", fake_get)
    out = fetch_mod.make_web_fetch_tool()("https://example.com/x", max_chars=1000)
    assert out["ua_retried"] is True
    assert len(uas) == 2
    assert uas[0].startswith("reeve/")
    assert "Chrome" in uas[1]
    assert "ok" in out["text"]


def test_web_fetch_reports_a_403_that_survives_the_browser_retry(monkeypatch):
    import harness.integrations.web.fetch as fetch_mod

    monkeypatch.setattr(fetch_mod, "get_checked", lambda client, url, **_kw: _FakeResp(403))
    out = fetch_mod.make_web_fetch_tool()("https://example.com/x")
    # A non-2xx is a result with its status, not a raised error — the model
    # can read the block page and decide what to do next.
    assert out["status_code"] == 403 and "error" not in out
    assert "ua_retried" not in out


def test_web_fetch_format_raw_and_text(monkeypatch):
    import harness.integrations.web.fetch as fetch_mod

    body = b"<h1>Hi</h1><p>Body</p>"
    monkeypatch.setattr(
        fetch_mod, "get_checked", lambda client, url, **_kw: _FakeResp(200, body)
    )
    tool = fetch_mod.make_web_fetch_tool()
    raw = tool("https://example.com/x", max_chars=1000, format="raw")
    assert raw["format"] == "raw" and raw["text"] == "<h1>Hi</h1><p>Body</p>"
    text = tool("https://example.com/x", max_chars=1000, format="text")
    assert text["format"] == "text" and "<" not in text["text"] and "Body" in text["text"]
    fallback = tool("https://example.com/x", max_chars=1000, format="nonsense")
    assert fallback["format"] == "markdown"  # unknown formats read as the default


# -- what the agent is given ----------------------------------------------------------------------


def test_reeve_agent_has_grep_not_search_files(tmp_path):
    from harness.modes.agents import reeve_agent
    from harness.modes.agents.base import AgentContext

    names = {getattr(tool, "__name__", "") for tool in reeve_agent().build_tools(AgentContext(workspace=tmp_path, executor=None, todo=None))}
    assert "grep" in names and {"read_file", "write_file"} <= names
    assert "search_files" not in names and "read_file_lines" not in names  # grep, and the windowed read_file, took their place
    # git is read through the shell, where a read-only `git log` runs in every preset
    assert not {"git_status", "git_diff", "git_log"} & names


def test_the_agent_has_grep_not_search_files(tmp_path):
    from harness.modes.agents.base import AgentContext
    from harness.modes.agents.reeve import reeve_tool_factory

    names = {getattr(tool, "__name__", "") for tool in reeve_tool_factory(AgentContext(workspace=tmp_path))}
    assert "grep" in names and "search_files" not in names


def test_every_file_tool_refuses_a_path_outside_its_roots_in_one_sentence(tmp_path):
    """The tools keep their own scoping: a call that reached one without the
    engine adding the folder first is refused, in the same words from every tool, naming
    the folders that would have worked."""
    from harness.tools.search import search_tools
    from harness.tools.listing import list_files_tools

    outside = tmp_path.parent / "elsewhere"
    read_file = file_tools(str(tmp_path))[0]
    grep = search_tools(str(tmp_path))[0]
    list_files = list_files_tools(str(tmp_path))[0]
    for error in (
        read_file(path=str(outside / "a.txt"))["error"],
        grep(pattern="x", path=str(outside))["error"],
        list_files(path=str(outside))["error"],
    ):
        assert "outside the session's folders" in error
        assert str(tmp_path.resolve()) in error
