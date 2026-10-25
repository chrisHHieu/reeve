"""A design taken out of Reeve, and the Browser tab's page read by Reeve's browser.

The tab row's Export / Share / open-in and the Browser bar's picture and look (the owner's pick from
OpenDesign, 2026-10-19). What is pinned: an export takes only the files the page loads, under its
root and never a hidden one; one HTML file stands alone; a saved file never overwrites one; only a
file Reeve saved can be shown in its folder; an editor is opened by its own address; and the PDF,
the picture and the page's look come from a real browser.
"""

from __future__ import annotations

import base64
import io
import struct
import zipfile
from pathlib import Path

import pytest

from harness.interface.server.manager import design_export as export_mixin
from harness.interface.server.manager.design_export import DesignExportMixin
from harness.tools.design import find_browser
from harness.tools.design.brief import brief_text
from harness.tools.design.export import local_files, save_download, standalone_html, zip_bytes


def _project(root: Path) -> Path:
    """A page with a stylesheet that imports another, a picture, an icon, a script, a link to
    another page, a web font and references that must never be taken."""
    design = root / "design"
    (design / "img").mkdir(parents=True)
    (root / ".env").write_text("TOKEN=invented", encoding="utf-8")
    (root / ".private").mkdir()
    (root / ".private" / "pic.png").write_bytes(b"invented picture")
    (root / "notes.txt").write_text("not a web file", encoding="utf-8")
    (root / "DESIGN.md").write_text("# Look\n- accent: #3b5bdb\n", encoding="utf-8")
    (design / "base.css").write_text("@import 'type.css';\nbody{background:url(img/dot.svg) #fbfbfc}\n", encoding="utf-8")
    (design / "type.css").write_text("h1{font:700 48px/1.1 Georgia;color:#3b5bdb}\n", encoding="utf-8")
    (design / "img" / "dot.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg" width="4" height="4"/>', encoding="utf-8")
    (design / "app.js").write_text("document.body.dataset.ran = '</script>';", encoding="utf-8")
    page = design / "pricing.html"
    page.write_text(
        '<!doctype html><html><head><link rel="stylesheet" href="base.css"><link rel="icon" href="img/dot.svg">'
        '<link rel="stylesheet" href="https://fonts.example.com/inter.css"></head>'
        '<body><h1>Plans</h1><img src="img/dot.svg" alt=""><img src="../.env" alt=""><img src="../notes.txt" alt=""><img src="../.private/pic.png" alt="">'
        '<img src="data:image/png;base64,AA" alt=""><a href="other.html">Other</a>'
        '<div style="background:url(\'img/dot.svg\')"></div><script src="app.js"></script></body></html>',
        encoding="utf-8",
    )
    return page


def test_an_export_takes_the_files_the_page_loads_and_nothing_hidden_or_outside(tmp_path):
    page = _project(tmp_path)
    taken = [path.relative_to(tmp_path).as_posix() for path in local_files(page, tmp_path)]
    assert taken == ["design/pricing.html", "design/base.css", "design/img/dot.svg", "design/app.js", "design/type.css"]
    # Outside the root is nothing, however it is spelled.
    assert local_files(page, tmp_path / "design")[1:] == [p for p in local_files(page, tmp_path)[1:] if p.is_relative_to(tmp_path / "design")]


def test_one_html_file_stands_alone_and_keeps_its_links_and_the_web(tmp_path):
    page = _project(tmp_path)
    html = standalone_html(page, tmp_path)
    assert 'href="base.css"' not in html and "<style>" in html
    assert "Georgia" in html  # the @import came with it
    assert 'src="app.js"' not in html and "dataset.ran" in html
    assert "<\\/script>" in html  # a script that says </script> cannot end the tag early
    assert html.count("data:image/svg+xml;base64,") == 4  # icon, background, picture, inline style
    assert 'href="other.html"' in html and "https://fonts.example.com/inter.css" in html
    assert 'src="../.env"' in html and "TOKEN" not in html and 'src="../notes.txt"' in html
    assert 'src="../.private/pic.png"' in html


def test_a_zip_holds_the_page_its_files_and_its_design_md_beside_each_other(tmp_path):
    page = _project(tmp_path)
    names = zipfile.ZipFile(io.BytesIO(zip_bytes(page, tmp_path, [tmp_path]))).namelist()
    assert sorted(names) == sorted(
        ["design/pricing.html", "design/base.css", "design/img/dot.svg", "design/app.js", "design/type.css", "DESIGN.md"]
    )


def test_a_saved_file_never_overwrites_one_already_there(tmp_path):
    first = save_download("pricing.pdf", b"one", tmp_path)
    second = save_download("pricing.pdf", b"two", tmp_path)
    odd = save_download('a:b?.html', b"x", tmp_path)
    assert [first.name, second.name, odd.name] == ["pricing.pdf", "pricing (2).pdf", "a_b_.html"]
    assert first.read_bytes() == b"one"


def test_a_page_brief_names_colours_type_and_shape_and_asks_not_to_copy():
    text = brief_text(
        {
            "width": 1280,
            "ink": ["#111111", "#555555"],
            "ground": ["#ffffff", "#f4f4f5"],
            "accent": ["#0057ff", "#222222"],
            "fonts": ["Inter"],
            "heads": ["Inter Display"],
            "sizes": {"h1": {"size": 72, "weight": "700"}},
            "body": {"size": 16, "line": "24px"},
            "radius": ["8"],
            "content": 640,
        },
        "example.com",
    )
    assert "text #111111 (also #555555)" in text and "grounds #ffffff, #f4f4f5" in text
    assert "accents #0057ff." in text  # a grey button is not an accent
    assert "headings in Inter Display (h1 72 px / 700)" in text and "text in Inter at 16 px" in text
    assert "8 px corner" in text and "640 px" in text
    assert "Do not copy the site" in text


class _Manager(DesignExportMixin):
    def __init__(self, root: Path) -> None:
        self.root = root

    def _session_folders(self, session_id):
        return [self.root.resolve()]

    def _artifact_target(self, session_id, path):
        target = (self.root / path).resolve()
        return (target, None) if target.is_file() and target.is_relative_to(self.root.resolve()) else (None, "not found")

    def _reveal_command(self, absolute):
        return ["reveal", absolute]


def test_export_saves_to_downloads_and_only_a_saved_file_is_shown_in_its_folder(tmp_path, monkeypatch):
    _project(tmp_path)
    downloads = tmp_path / "Downloads"
    downloads.mkdir()
    monkeypatch.setattr(export_mixin, "save_download", lambda name, data: save_download(name, data, downloads))
    spawned: list[list[str]] = []
    monkeypatch.setattr(export_mixin, "spawn", lambda command, **_: spawned.append(command))
    manager = _Manager(tmp_path)

    done = manager.design_export("s1", "design/pricing.html", "html")
    assert done["ok"] and done["name"] == "pricing.standalone.html" and done["folder"] == "Downloads"
    assert "dataset.ran" in Path(done["saved"]).read_text(encoding="utf-8")
    again = manager.design_export("s1", "design/pricing.html", "zip")
    assert again["name"] == "pricing.zip"
    copied = manager.design_export("s1", "design/pricing.html", "html", deliver="data")
    assert copied["ok"] and "<style>" in copied["html"]

    assert manager.reveal_download(done["saved"])["ok"]
    assert spawned == [["reveal", str(Path(done["saved"]).resolve())]]
    # Any other path is refused, the workspace's own files included.
    assert manager.reveal_download(str(tmp_path / "notes.txt"))["ok"] is False
    assert manager.design_export("s1", "design/pricing.html", "docx")["ok"] is False
    assert manager.design_export("s1", "../outside.html", "html")["ok"] is False
    assert manager.design_export("s1", "design/pricing.html", "pdf", width=99999)["ok"] is False


def test_an_editor_is_opened_by_its_own_address_and_only_when_it_is_there(tmp_path, monkeypatch):
    _project(tmp_path)
    opened: list[str] = []
    monkeypatch.setattr(export_mixin, "_open_address", opened.append)
    monkeypatch.setattr(export_mixin, "_editor_installed", lambda scheme, command: scheme == "vscode")
    manager = _Manager(tmp_path)
    assert manager.design_editors()["editors"] == [{"id": "vscode", "name": "VS Code"}]
    assert manager.open_in_editor("s1", "design/pricing.html", "vscode")["ok"]
    posix = (tmp_path / "design" / "pricing.html").resolve().as_posix()
    assert opened == ["vscode://file" + ("" if posix.startswith("/") else "/") + posix.replace(" ", "%20")]
    assert manager.open_in_editor("s1", "design/pricing.html", "cursor")["ok"] is False
    assert manager.open_in_editor("s1", "design/pricing.html", "notepad")["ok"] is False


def test_the_browser_tab_reads_only_web_addresses(tmp_path):
    manager = _Manager(tmp_path)
    page = _project(tmp_path)
    assert manager.browser_capture(page.as_uri(), "view")["ok"] is False
    assert manager.browser_capture("https://example.com", "nothing")["ok"] is False


@pytest.mark.skipif(find_browser() is None, reason="no Chromium on this machine")
def test_a_pdf_and_a_picture_are_the_whole_page_at_the_width_it_is_seen_at(tmp_path, monkeypatch):
    page = tmp_path / "design" / "long.html"
    page.parent.mkdir()
    page.write_text(
        "<!doctype html><style>body{margin:0}</style><div style='height:2400px;background:#3b5bdb'></div>", encoding="utf-8"
    )
    downloads = tmp_path / "Downloads"
    downloads.mkdir()
    monkeypatch.setattr(export_mixin, "save_download", lambda name, data: save_download(name, data, downloads))
    manager = _Manager(tmp_path)
    picture = manager.design_export("s1", "design/long.html", "png", width=600, deliver="data")
    assert picture["ok"], picture
    png = base64.b64decode(picture["png"].split(",", 1)[1])
    # Past the first screen: the whole page, at the width asked.
    assert struct.unpack(">II", png[16:24]) == (600, 2400)
    pdf = manager.design_export("s1", "design/long.html", "pdf", width=600)
    assert pdf["ok"], pdf
    assert Path(pdf["saved"]).read_bytes()[:5] == b"%PDF-"


@pytest.mark.skipif(find_browser() is None, reason="no Chromium on this machine")
def test_the_browser_tab_page_is_read_by_reeves_browser(tmp_path):
    import functools
    import http.server
    import threading

    (tmp_path / "index.html").write_text(
        "<!doctype html><title>Shop</title><style>body{margin:0;background:#fbfbfc;color:#16181d;font:16px/1.5 Georgia}"
        "h1{font-size:48px}a.cta{background:#3b5bdb;color:#fff;border-radius:10px;padding:8px}</style>"
        "<h1>Things</h1><p>" + "Words in a column. " * 6 + "</p><a class=cta href=#>Buy</a>",
        encoding="utf-8",
    )
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(tmp_path))
    handler.log_message = lambda *args, **kwargs: None  # type: ignore[attr-defined]
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{server.server_address[1]}/index.html"
        manager = _Manager(tmp_path)
        brief = manager.browser_capture(url, "brief", 1280)
        assert brief["ok"], brief
        assert brief["title"] == "Shop"
        assert "grounds #fbfbfc" in brief["brief"] and "accents #3b5bdb" in brief["brief"]
        assert "text in Georgia at 16 px" in brief["brief"] and "10 px corner" in brief["brief"]
        view = manager.browser_capture(url, "view", 800)
        png = base64.b64decode(view["png"].split(",", 1)[1])
        assert struct.unpack(">II", png[16:24]) == (800, 900)
    finally:
        server.shutdown()
