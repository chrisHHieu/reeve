"""Locking a design, and the handoff the harness measures from it.

What is pinned: a lock names the version it measured (sha256) and says when the page has changed
since; the handoff is measured by a real browser (the page's ground, its own tokens, its type, its
parts by name at each width, where its layout changes, its motion, its font files), not written by
a model; what the check still finds is what the person accepts, and only that; the preview writes
nothing and the lock reuses its measurement; only a page under design/ can be locked; a session
with no project folder has nothing to build into; and a check of a locked page that changed says so.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from harness.interface.server.manager.design_lock import DesignLockMixin
from harness.tools.design import find_browser
from harness.tools.design import handoff as handoff_mod
from harness.tools.design.check import DesignReport, check_page
from harness.tools.design.handoff import Locked, accepted_lines, handoff_markdown, lock_record, write_lock
from harness.tools.design.locks import LOCK_VERSION, lock_path, lock_state, locked_line, read_lock

_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Pricing</title>
<style>
@font-face { font-family: "Northwind Serif"; src: url("fonts/northwind.woff2") format("woff2"); font-weight: 400; }
:root { --ink: #1b1b1b; --paper: #fbf7f0; --brand: #1f5c4a; }
body { margin: 0; background: var(--paper); color: var(--ink); font: 16px/1.5 Georgia, serif; }
header { display: flex; justify-content: space-between; padding: 16px 24px; }
h1 { font-size: 48px; line-height: 1.1; margin: 0 0 16px; }
.plans { display: grid; grid-template-columns: repeat(3, 1fr); gap: 24px; padding: 40px 24px; }
.card { background: #ffffff; border: 1px solid #e6dfd3; border-radius: 12px; padding: 24px; }
button { min-height: 44px; padding: 0 16px; background: var(--brand); color: #ffffff; border: 0; border-radius: 8px;
  font: inherit; transition: background-color 200ms ease-out; }
@media (max-width: 720px) { .plans { grid-template-columns: 1fr; } h1 { font-size: 32px; } }
@media (prefers-reduced-motion: reduce) { button { transition: none; } }
</style></head><body>
<header><b>Northwind</b><a href="#plans">Plans</a></header>
<main>
<section class="hero"><h1>Notes for the team</h1><p>Clear prices.</p></section>
<section class="plans" id="plans" aria-label="Plans">
<div class="card"><h2>Free</h2><button type="button">Start</button></div>
<div class="card"><h2>Team</h2><button type="button">Start</button></div>
<div class="card"><h2>Business</h2><button type="button">Talk to us</button></div>
</section>
</main>
<footer><p>Northwind</p></footer>
</body></html>
"""


def _project(root: Path) -> Path:
    page = root / "design" / "pricing.html"
    (page.parent / "fonts").mkdir(parents=True)
    (page.parent / "fonts" / "northwind.woff2").write_bytes(b"invented font bytes")
    page.write_text(_PAGE, encoding="utf-8")
    return page


class _Manager(DesignLockMixin):
    def __init__(self, root: Path, *, scratch: bool = False) -> None:
        self.root = root
        self.scratch = scratch
        self.session_store = self
        self.measured = 0

    def load(self, session_id):
        return {}

    def _is_remote_record(self, record):
        return False

    def is_temp_workspace(self, path):
        return self.scratch

    def _session_folders(self, session_id):
        return [self.root.resolve()]

    def _artifact_target(self, session_id, path):
        target = (self.root / path).resolve()
        return (target, None) if target.is_file() and target.is_relative_to(self.root.resolve()) else (None, "not found")


@pytest.mark.skipif(find_browser() is None, reason="no Chromium on this machine")
def test_a_real_page_is_measured_into_its_lock_and_handoff(tmp_path):
    page = _project(tmp_path)
    locked = handoff_mod.measure_for_lock(page, shown="design/pricing.html", roots=[tmp_path])
    assert not locked.error
    assert [system["width"] for system in locked.system] == [1440, 834, 390]
    record = write_lock(locked)
    assert record["handoff"] == "design/pricing.handoff.md"
    assert record["lock_file"] == "design/.locks/pricing.html.json"
    assert json.loads((tmp_path / "design" / ".locks" / "pricing.html.json").read_text(encoding="utf-8"))["sha256"] == locked.sha256
    assert set(record["inventory"]) == {"1440", "834", "390"}

    text = (tmp_path / "design" / "pricing.handoff.md").read_text(encoding="utf-8")
    # The page's ground is the body's, measured, and the colours keep their roles.
    assert "| Background | `#fbf7f0` |" in text
    assert "| Text | `#1b1b1b` |" in text and "| Border | `#e6dfd3` |" in text
    # Its own tokens, its type, its font file, where it changes, its motion.
    assert "- `--brand: #1f5c4a`" in text
    assert "| Georgia | 48 px | 52.8 px | 700 |" in text
    assert "At 390 px the largest sizes become: Georgia 48 → 32 px" in text
    assert "- Northwind Serif 400: `fonts/northwind.woff2`" in text
    assert "max 720 px" in text
    assert "| transition | `background-color` | 200 ms | `ease-out` |" in text
    # Its parts by name, laid out per width: three plans across wide, one on a phone.
    plans = next(line for line in text.splitlines() if "`section#plans`" in line)
    assert "| Plans |" in plans and "3 across" in plans.split("|")[4]
    assert "3 across" not in plans.split("|")[6]
    assert "`footer`" in text
    assert "no model wrote this file" in text

    assert lock_state(page) == {
        "locked": True,
        "locked_at": record["locked_at"],
        "changed": False,
        "handoff": "design/pricing.handoff.md",
        "accepted": len(record["accepted"]),
        "kept": 0,
        "reviewed": None,
    }
    assert locked_line(page) == ""
    assert check_page(page, shown="design/pricing.html", roots=[tmp_path], shots=False).locked_changed == ""

    # A later write changes the page: the lock says so, and so does the check after the write.
    page.write_text(_PAGE.replace("Clear prices.", "Clearer prices."), encoding="utf-8")
    assert lock_state(page)["changed"] is True
    report = check_page(page, shown="design/pricing.html", roots=[tmp_path], shots=False)
    assert "- To check: this page was locked by the person on" in report.text()
    assert report.facts()["design_locked_changed"] == 1
    assert report.issues == check_page(page, shown="design/pricing.html", roots=[tmp_path], shots=False).issues


def _locked(tmp_path: Path, report: DesignReport) -> Locked:
    page = tmp_path / "design" / "a" / "home.html"
    page.parent.mkdir(parents=True)
    page.write_text("<!doctype html><title>x</title>", encoding="utf-8")
    return Locked(page=page, shown="design/a/home.html", sha256="ab" * 32, report=report)


def test_what_the_person_accepts_is_what_the_check_counts_and_nothing_said_only(tmp_path):
    report = DesignReport(shown="design/a/home.html", widths=[390])
    report.overflow = [{"width": 390, "excess": 40, "past": [{"where": "div.wide", "count": 1}]}]
    report.blocked = ["https://fonts.example.com/a.css"]
    report.locked_changed = "this page was locked"
    assert accepted_lines(report) == ["At 390 px the page is 40 px wider than the window; first past the edge: div.wide."]
    assert accepted_lines(DesignReport(shown="x", widths=[390])) == []
    assert accepted_lines(DesignReport(shown="x", widths=[390], error="no browser")) == []

    locked = _locked(tmp_path, report)
    record = write_lock(locked, now=1_790_000_000)
    assert record["accepted"] == accepted_lines(report)
    assert record["lock_file"] == "design/.locks/a__home.html.json"
    assert lock_path(locked.page) == tmp_path / "design" / ".locks" / "a__home.html.json"
    text = (tmp_path / "design" / "a" / "home.handoff.md").read_text(encoding="utf-8")
    assert "## Accepted at lock" in text and "- At 390 px the page is 40 px wider" in text
    assert "Not loaded" not in text.split("## Accepted at lock")[1]


def test_a_lock_is_read_only_when_this_reeve_can_and_only_under_design(tmp_path):
    locked = _locked(tmp_path, DesignReport(shown="design/a/home.html", widths=[390]))
    write_lock(locked)
    where = lock_path(locked.page)
    data = json.loads(where.read_text(encoding="utf-8"))
    assert read_lock(locked.page)["sha256"] == "ab" * 32
    where.write_text(json.dumps({**data, "v": LOCK_VERSION + 1}), encoding="utf-8")
    assert read_lock(locked.page) is None and lock_state(locked.page) == {"locked": False}
    where.write_text("{ not json", encoding="utf-8")
    assert read_lock(locked.page) is None

    outside = tmp_path / "reports" / "flow.html"
    outside.parent.mkdir()
    outside.write_text("<!doctype html>", encoding="utf-8")
    assert lock_path(outside) is None
    with pytest.raises(ValueError):
        write_lock(Locked(page=outside, shown="reports/flow.html", sha256="", report=DesignReport(shown="x", widths=[390])))


def test_the_handoff_says_how_to_build_and_what_it_cannot_know(tmp_path):
    locked = _locked(tmp_path, DesignReport(shown="design/a/home.html", widths=[1440, 390]))
    text = handoff_markdown(locked, lock_record(locked, handoff="design/a/home.handoff.md", now=1_790_000_000))
    assert text.startswith("# Handoff: design/a/home.html\n")
    # Which component a part becomes is read from the app and proposed, then the person's go.
    assert "wait for their go" in text
    assert "never as loose values" in text
    assert "## Accepted at lock" not in text


def _measure(manager: _Manager):
    def fake(page, *, shown, roots, widths=None, browser=None):
        manager.measured += 1
        return Locked(
            page=page,
            shown=shown,
            sha256=handoff_mod.page_sha(page),
            report=DesignReport(shown=shown, widths=[1440, 834, 390]),
            system=[{"width": 1440, "colors": {"background": [{"key": "#fbf7f0", "count": 1}]}, "sections": [{"tag": "header", "name": "header"}]}],
        )

    return fake


def test_the_preview_writes_nothing_and_the_lock_reuses_it_while_the_page_is_the_same(tmp_path, monkeypatch):
    page = _project(tmp_path)
    manager = _Manager(tmp_path)
    monkeypatch.setattr("harness.interface.server.manager.design_lock.measure_for_lock", _measure(manager))

    preview = manager.design_lock_preview("s1", "design/pricing.html")
    assert preview["ok"] and preview["summary"]["colors"] == ["#fbf7f0"] and preview["summary"]["parts"] == ["header"]
    assert preview["state"] == {"locked": False}
    assert preview["target"] == {"name": tmp_path.resolve().name, "path": str(tmp_path.resolve())}
    assert not (tmp_path / "design" / ".locks").exists() and not (tmp_path / "design" / "pricing.handoff.md").exists()

    done = manager.design_lock("s1", "design/pricing.html")
    assert done["ok"] and done["handoff"] == "design/pricing.handoff.md" and done["state"]["locked"]
    assert manager.measured == 1
    assert (tmp_path / "design" / "pricing.handoff.md").is_file()

    # Changed in between: measured again, so the lock names the version it measured.
    manager.design_lock_preview("s1", "design/pricing.html")
    page.write_text(_PAGE + "<!-- changed -->", encoding="utf-8")
    again = manager.design_lock("s1", "design/pricing.html")
    # The second preview reused the lock's measurement (same file); the changed page was measured anew.
    assert manager.measured == 2
    assert read_lock(page)["sha256"] == handoff_mod.page_sha(page)
    assert again["state"]["changed"] is False


def test_only_a_design_page_is_locked_and_a_scratch_session_has_nothing_to_build_into(tmp_path, monkeypatch):
    _project(tmp_path)
    (tmp_path / "flow.html").write_text("<!doctype html>", encoding="utf-8")
    manager = _Manager(tmp_path, scratch=True)
    monkeypatch.setattr("harness.interface.server.manager.design_lock.measure_for_lock", _measure(manager))
    refused = manager.design_lock_preview("s1", "flow.html")
    assert refused == {"ok": False, "error": "only a page under a design/ folder can be locked"}
    assert manager.design_lock("s1", "missing.html")["ok"] is False
    assert manager.design_lock_preview("s1", "design/pricing.html")["target"] is None
    assert manager.design_lock_state("s1", "design/pricing.html") == {"ok": True, "state": {"locked": False}, "target": None}


# -- compare_design (step 2) ---------------------------------------------------------------------

from harness.tools.design.compare import compare, part_key  # noqa: E402
from harness.tools.design.compare_tool import compare_tool, on_this_machine  # noqa: E402


def _system(width: int, columns: int, color: str, *, faq: bool = True, size: float = 48) -> dict:
    sections = [
        {"tag": "header", "name": "header", "heading": "", "label": "", "h": 50, "columns": 2},
        {"tag": "section", "name": "section#plans", "heading": "Free", "label": "Plans", "h": 240, "columns": columns},
    ]
    if faq:
        sections.append({"tag": "section", "name": "section.faq", "heading": "Questions", "label": "", "h": 90, "columns": 1})
    return {
        "width": width,
        "colors": {"background": [{"key": color, "where": "button"}, {"key": "#fbf7f0", "where": "body"}], "text": [{"key": "#1b1b1b", "where": "p"}]},
        "type": [{"family": "Georgia", "size": size, "weight": 700, "tag": "h1", "sample": "Notes"}],
        "sections": sections,
    }


def test_the_app_is_held_to_the_design_by_what_a_person_sees_differ():
    design = [_system(1440, 3, "#1f5c4a"), _system(390, 1, "#1f5c4a")]
    app = [
        {"width": 1440, "scroll_width": 1440, "handoff": _system(1440, 1, "#2563eb", faq=False, size=40), "body_text": "Plans Free"},
        {"width": 390, "scroll_width": 430, "past": [{"where": "table.compare"}], "handoff": _system(390, 1, "#2563eb", faq=False), "body_text": "Plans Free"},
    ]
    inventory = [{"text": "Free"}, {"text": "Can I cancel?"}, {"text": "ok"}]
    result = compare(url="http://localhost:5173/pricing", page="design/pricing.html", design_system=design,
                     design_inventory=inventory, app_measured=app, widths=[1440, 390], locked_at=1)
    assert result.fix == [
        "Parts of the design the app does not have: “Questions”",
        "At 1440 px “Plans” is 1 across in the app and 3 in the design",
        "Design colors the app does not use: #1f5c4a (background, `button` in the design)",
        "Type sizes of the design the app does not use: Georgia 48 px (`<h1>` “Notes”)",
        "At 390 px the app is 40 px wider than the window; first past the edge: table.compare",
    ]
    # An added colour and words said another way may be right in the app: said, not counted.
    assert result.check == [
        "Colors in the app the design does not have: #2563eb (background, `button`)",
        "Words of the design the app does not show: “Can I cancel?”",
    ]
    assert result.facts()["compare_issues"] == 5 and result.facts()["compare_missing_parts"] == 1
    assert result.text("2026-10-20 12:00").startswith(
        "[design compare] http://localhost:5173/pricing against design/pricing.html (locked 2026-10-20 12:00) at 1440, 390 px: 5 to fix."
    )
    same = compare(url="u", page="p", design_system=design, design_inventory=[], app_measured=[
        {"width": 1440, "scroll_width": 1440, "handoff": _system(1440, 3, "#1f5c4a")},
        {"width": 390, "scroll_width": 390, "handoff": _system(390, 1, "#1f5c4a")},
    ], widths=[1440, 390])
    assert same.fix == [] and same.text().endswith("at 1440, 390 px: the same in what was measured.")
    assert "as it is now (not locked)" in same.text()


def test_parts_are_matched_by_what_names_them_and_only_this_machine_is_opened():
    assert part_key({"tag": "section", "label": " Plans ", "heading": "Free"}) == "plans"
    assert part_key({"tag": "section", "heading": "Questions"}) == "questions"
    assert part_key({"tag": "footer"}) == "footer"
    assert part_key({"tag": "div"}) == ""
    assert on_this_machine("http://localhost:5173/pricing") and on_this_machine("http://127.0.0.1:3000/")
    assert on_this_machine("http://app.localhost:8080/") and on_this_machine("https://[::1]:8443/x")
    for url in ("https://example.com/", "http://localhost.example.com/", "file:///C:/x.html", "javascript:alert(1)", "http://10.0.0.5/"):
        assert not on_this_machine(url), url


def test_compare_design_refuses_what_it_should_not_open(tmp_path):
    _project(tmp_path)
    (tmp_path / "flow.html").write_text("<!doctype html>", encoding="utf-8")
    tool = compare_tool(str(tmp_path), [str(tmp_path)])
    assert "not on this machine" in tool(url="https://example.com/pricing", page="design/pricing.html")["error"]
    assert "not a design page" in tool(url="http://localhost:1/", page="flow.html")["error"]
    assert "needs `url`" in tool(url="", page="design/pricing.html")["error"]
    assert "error" in tool(url="http://localhost:1/", page="../outside/design/x.html")


@pytest.mark.skipif(find_browser() is None, reason="no Chromium on this machine")
def test_a_real_app_is_compared_with_its_locked_design(tmp_path):
    import functools
    import http.server
    import threading

    page = _project(tmp_path)
    write_lock(handoff_mod.measure_for_lock(page, shown="design/pricing.html", roots=[tmp_path]))
    app = tmp_path / "app"
    app.mkdir()
    built = _PAGE.replace("repeat(3, 1fr)", "1fr").replace("--brand: #1f5c4a", "--brand: #2563eb")
    (app / "pricing.html").write_text(built, encoding="utf-8")
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(app))
    handler.log_message = lambda *args, **kwargs: None  # type: ignore[attr-defined]
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        tool = compare_tool(str(tmp_path), [str(tmp_path)])
        url = f"http://127.0.0.1:{server.server_address[1]}/pricing.html"
        out = tool(url=url, page="design/pricing.html")
        report = out["report"]
        assert report.startswith(f"[design compare] {url} against design/pricing.html (locked ")
        assert "- At 1440 px “Plans” is 1 across in the app and 3 in the design." in report
        assert "- At 834 px “Plans” is 1 across in the app and 3 in the design." in report
        # At 390 the design is one across too: no difference said there.
        assert "At 390 px “Plans”" not in report
        assert "- Design colors the app does not use: #1f5c4a" in report
        assert "- To check: Colors in the app the design does not have: #2563eb" in report
        assert out["_facts"]["compare_locked"] is True and out["_facts"]["compare_columns"] == 2
        assert [part["text"] for part in out["_attach"] if part["type"] == "text"] == ["The app at 1440 px", "The app at 390 px"]

        # The page changed after the lock: still held to the locked version, and said.
        page.write_text(_PAGE.replace("Clear prices.", "Other words."), encoding="utf-8")
        again = tool(url=url, page="design/pricing.html")["report"]
        assert "has changed since it was locked; this compares with the locked version" in again

        # Built as designed: nothing to fix.
        page.write_text(_PAGE, encoding="utf-8")
        (app / "pricing.html").write_text(_PAGE, encoding="utf-8")
        clean = tool(url=url, page="design/pricing.html")
        assert clean["report"].splitlines()[0].endswith(": the same in what was measured."), clean["report"]
        assert "_attach" not in clean
    finally:
        server.shutdown()


# -- the review (step 3) -------------------------------------------------------------------------

from harness.interface.server.manager.design_review import DesignReviewMixin  # noqa: E402
from harness.tools.design.locks import kept_ids  # noqa: E402


class _ReviewManager(_Manager, DesignReviewMixin):
    pass


def test_a_kept_difference_is_said_as_kept_and_not_counted():
    design = [_system(1440, 3, "#1f5c4a")]
    app = [{"width": 1440, "scroll_width": 1440, "handoff": _system(1440, 3, "#2563eb")}]
    first = compare(url="u", page="p", design_system=design, design_inventory=[], app_measured=app, widths=[1440], locked_at=1)
    unused = next(finding for finding in first.findings if finding["kind"] == "fix")
    assert unused["id"].startswith("colors-unused:")
    again = compare(url="u", page="p", design_system=design, design_inventory=[], app_measured=app, widths=[1440], locked_at=1,
                    accepted={unused["id"]})
    assert again.issues == first.issues - 1
    assert again.accepted == [unused["text"]]
    assert f"- Kept as the app has it, by the person: {unused['text']}." in again.text()
    assert again.facts()["compare_accepted"] == 1
    # The same kind of difference with other colours is another difference: not kept.
    other = compare(url="u", page="p", design_system=[_system(1440, 3, "#123456")], design_inventory=[], app_measured=app,
                    widths=[1440], locked_at=1, accepted={unused["id"]})
    assert other.accepted == []


def test_keep_and_done_are_recorded_on_the_lock_and_need_one(tmp_path):
    page = _project(tmp_path)
    manager = _ReviewManager(tmp_path)
    assert manager.design_review_keep("s1", "design/pricing.html", "order", "x")["ok"] is False
    assert manager.design_review_done("s1", "design/pricing.html", "http://localhost:5173/", 0)["ok"] is False
    write_lock(_locked_page(page))
    kept = manager.design_review_keep("s1", "design/pricing.html", "order", "The app has the parts in another order")
    assert kept["ok"] and kept["state"]["kept"] == 1 and kept_ids(page) == {"order"}
    assert manager.design_review_done("s1", "design/pricing.html", "https://example.com/", 0)["ok"] is False
    done = manager.design_review_done("s1", "design/pricing.html", "http://localhost:5173/pricing", 2)
    assert done["ok"] and done["state"]["reviewed"]["url"] == "http://localhost:5173/pricing"
    assert done["state"]["reviewed"]["issues"] == 2
    # A new lock keeps what was kept of the app, and drops the review of the old version.
    write_lock(_locked_page(page))
    assert kept_ids(page) == {"order"} and lock_state(page)["reviewed"] is None
    assert manager.design_review("s1", "design/pricing.html", "https://example.com/", 1440)["ok"] is False
    assert manager.design_review("s1", "design/pricing.html", "http://localhost:1/", 99999)["ok"] is False


def _locked_page(page: Path) -> Locked:
    return Locked(page=page, shown="design/pricing.html", sha256=handoff_mod.page_sha(page),
                  report=DesignReport(shown="design/pricing.html", widths=[1440]), system=[_system(1440, 3, "#1f5c4a")])


@pytest.mark.skipif(find_browser() is None, reason="no Chromium on this machine")
def test_the_review_draws_both_whole_pages_and_says_what_differs(tmp_path):
    import base64
    import functools
    import http.server
    import struct
    import threading

    page = _project(tmp_path)
    write_lock(handoff_mod.measure_for_lock(page, shown="design/pricing.html", roots=[tmp_path]))
    app = tmp_path / "app"
    app.mkdir()
    (app / "pricing.html").write_text(_PAGE.replace("repeat(3, 1fr)", "1fr"), encoding="utf-8")
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(app))
    handler.log_message = lambda *args, **kwargs: None  # type: ignore[attr-defined]
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{server.server_address[1]}/pricing.html"
        manager = _ReviewManager(tmp_path)
        review = manager.design_review("s1", "design/pricing.html", url, 834)
        assert review["ok"], review
        assert review["locked"] is True and review["widths"] == [1440, 834, 390]
        columns = [finding for finding in review["findings"] if finding["id"].startswith("columns:")]
        assert [finding["id"] for finding in columns] == ["columns:plans@1440", "columns:plans@834"]

        def size(data_url):
            png = base64.b64decode(data_url.split(",", 1)[1])
            return struct.unpack(">II", png[16:24])

        design_w, design_h = size(review["design_png"])
        app_w, app_h = size(review["app_png"])

        # Whole pages at the width asked (at least one window tall).
        assert design_w == app_w == 834 and design_h >= 900 and app_h >= design_h

        kept = manager.design_review_keep("s1", "design/pricing.html", columns[0]["id"], columns[0]["text"])
        assert kept["ok"]
        again = manager.design_review("s1", "design/pricing.html", url, 834)
        assert next(f for f in again["findings"] if f["id"] == "columns:plans@1440")["kind"] == "accepted"
        assert again["issues"] == review["issues"] - 1
        # Only the width: no compare.
        picture = manager.design_review("s1", "design/pricing.html", url, 390, compare=False)
        assert "findings" not in picture and size(picture["app_png"])[0] == 390
    finally:
        server.shutdown()
