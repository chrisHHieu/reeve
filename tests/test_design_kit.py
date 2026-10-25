"""The designer's brief, second pass (2026-10-20): the font kit, the layouts, the tells of
a machine-made page, the role per kind, the system said first, and the scored last look.

What is pinned: the kit is whole (every face it links is there, Vietnamese included, with its
licence); every direction uses only the kit's families and copies all it names; the guide hands the
model all of it; every layout, filled and bound to a direction with its fonts copied, checks clean
in a real browser; and the check says when a page links a file that is not there, names a kit
family it never loads, or keeps a layout's slot.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from design_pages import bind, page_with, project_for
from harness.tools.design import find_browser
from harness.tools.design.check import check_page
from harness.tools.design.directions import DIRECTIONS
from harness.tools.design.fonts import BY_SLUG, KIT, KIT_DIR, copy_commands, kit_families
from harness.tools.design.guide import guide_text
from harness.tools.design.layouts import LAYOUTS, SHELLS, layout_text
from harness.tools.design.lint import lint_source
from harness.tools.design.loads import missing_files, unloaded_fonts
from harness.tools.design.recipes import KINDS


def test_the_kit_is_whole_every_face_with_vietnamese_and_its_licence():
    assert len(kit_families()) == len(KIT)
    for family in KIT:
        folder = KIT_DIR / family.slug
        css = (folder / "font.css").read_text(encoding="utf-8")
        files = re.findall(r"url\(\./([^)]+)\)", css)
        assert files and all((folder / name).is_file() for name in files), family.slug
        assert f"font-family: '{family.family}'" in css
        assert any("vietnamese" in name for name in files), f"{family.slug} has no Vietnamese"
        assert "SIL Open Font License" in (folder / "OFL.txt").read_text(encoding="utf-8")
    # Nothing in the kit that a page would not load.
    assert {path.suffix for path in KIT_DIR.rglob("*") if path.is_file()} <= {".woff2", ".css", ".txt"}


def test_every_direction_uses_the_kit_and_copies_every_family_it_names():
    for direction in DIRECTIONS:
        assert direction.fonts and all(slug in BY_SLUG for slug in direction.fonts), direction.id
        named = {stack.split(",")[0].strip().strip("'\"") for stack in (direction.display, direction.body, direction.mono)}
        copied = {BY_SLUG[slug].family for slug in direction.fonts}
        assert named <= copied, f"{direction.id} names {named - copied} it does not copy"
    # The violet every default reaches for is no direction's accent.
    assert all(not d.palette["accent"].lower().startswith(("#5b2", "#7c3", "#8b5")) for d in DIRECTIONS)


def test_the_copy_command_names_the_kit_and_the_page_folder():
    line = copy_commands(["newsreader", "be-vietnam-pro"])[0]
    assert str(KIT_DIR / "newsreader") in line and str(KIT_DIR / "be-vietnam-pro") in line
    assert "design/fonts" in line


def test_the_guide_hands_over_the_fonts_the_layout_the_tells_and_the_bar(tmp_path):
    text = guide_text(tmp_path, [tmp_path], kind="landing")
    assert "Before the first write, say in one or two sentences the system you will use" in text
    assert "What makes a page read as machine-made" in text and "a purple or violet gradient wash" in text
    assert "Fonts that come with Reeve, each with Vietnamese" in text and str(KIT_DIR) in text
    assert "fonts to copy beside the page: newsreader, be-vietnam-pro, jetbrains-mono" in text
    assert "Layout for landing: paste it in place of the starting file's <main>" in text and LAYOUTS["landing"] in text
    assert "Landing page. You are its brand designer" in text
    assert "Anything under 4 is not done" in text
    assert "a performance claim" in text and "leave the section out rather than fake it" in text
    # A shell kind replaces the whole body; no kind, no layout.
    assert "everything inside the starting file's <body>" in layout_text("dashboard")
    assert "Layout for" not in guide_text(tmp_path, [tmp_path])
    assert set(LAYOUTS) == set(KINDS) and SHELLS <= set(KINDS)


def test_a_slot_left_and_a_default_display_face_are_named():
    page = "<style>h1{font-family: Inter, sans-serif} p{font-family: Arial}</style><h1>[[the outcome for the reader]]</h1>"
    found = {(finding.rule, finding.kind) for finding in lint_source(page)}
    assert ("slot", "fix") in found and ("default-display", "check") in found
    # Body text in Arial is not a display face; a DESIGN.md that chose Inter has chosen it.
    assert [f for f in lint_source(page, design_md_text="display: inter") if f.rule == "default-display"] == []


def test_a_missing_file_is_named_from_the_page_and_an_unloaded_kit_family_by_its_name(tmp_path):
    page = tmp_path / "design" / "pricing.html"
    url = (tmp_path / "design" / "fonts" / "newsreader" / "font.css").as_uri()
    assert missing_files([url, url], page) == ["fonts/newsreader/font.css"]
    assert unloaded_fonts(["Newsreader", "Segoe UI", "Be Vietnam Pro"], ["Be Vietnam Pro"]) == ["Newsreader"]


needs_browser = pytest.mark.skipif(find_browser() is None, reason="no Chromium on this machine")


@needs_browser
@pytest.mark.parametrize("kind", sorted(LAYOUTS))
@pytest.mark.parametrize("direction_id", ["editorial", "technical"])
def test_every_layout_filled_and_bound_to_a_direction_checks_clean(tmp_path, kind, direction_id):
    # The layouts are what a page is built from; one that hands the model findings of its own would
    # put them in every page (probing found nav links that spilled and footer links 32 px wide).
    direction = project_for(tmp_path, direction_id)
    target = tmp_path / "design" / f"{kind}.html"
    target.write_text(page_with(direction, kind, LAYOUTS[kind]), encoding="utf-8")
    report = check_page(target, shown=f"design/{kind}.html", roots=[tmp_path])
    assert not report.error and report.issues == 0, report.text()
    assert not report.unloaded_fonts and not report.missing


@needs_browser
def test_a_page_whose_fonts_were_not_copied_is_told_how_to_bring_them(tmp_path):
    direction = project_for(tmp_path, "editorial")
    for folder in (tmp_path / "design" / "fonts").iterdir():
        for path in folder.iterdir():
            path.unlink()
        folder.rmdir()
    target = tmp_path / "design" / "start.html"
    target.write_text(bind(direction).replace("<!-- The design goes here. -->", "<h1>Notes</h1>"), encoding="utf-8")
    report = check_page(target, shown="design/start.html", roots=[tmp_path])
    assert "fonts/newsreader/font.css" in report.missing
    assert "Newsreader" in report.unloaded_fonts
    text = report.text()
    assert "The page links files that are not there" in text and "Copy-Item" in text or "cp -R" in text
    assert report.facts()["design_missing_files"] >= 1 and report.facts()["design_unloaded_fonts"] >= 1


def test_the_kit_folder_is_package_data():
    # The built sidecar takes every non-Python file under harness/ (packaging/reeve-server.spec,
    # collect_data_files("harness")); the kit must live there to ship.
    assert KIT_DIR.is_relative_to(Path(__file__).resolve().parents[1] / "harness")
