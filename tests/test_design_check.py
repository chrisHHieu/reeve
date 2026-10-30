"""Design checks: a design page is measured after it is written, and the model reads
what is out of bounds.

Pinned here:
- what a measurement becomes: one line per thing to fix, held against the nearest DESIGN.md, and
  the counts the harness review reads;
- the engine hook: a write of `design/*.html` is checked before its result is recorded, the
  findings join the result the model reads, the screenshots go only with something to fix, a
  `design/check` row is logged, and the row under the write survives a reload without reaching a
  provider; any other write is not checked;
- the tools: the guide quotes DESIGN.md or says how to draft one; `check_design` stays in the roots;
- one real load in the machine's own Chromium, skipped where there is none.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from harness.llm.providers import AssistantTurn, ModelCapabilities, ProviderClient, ToolCall
from harness.llm.wire import wire_message
from harness.loop import ApprovalOutcome, TurnEngine
from harness.safety.permissions import PermissionEngine
from harness.session.events import EventType
from harness.session.session_log import SessionLog
from harness.tools import ToolRegistry
from harness.tools.design import design_card, find_browser, is_design_file
from harness.tools.design.browser import Render, WidthRender
from harness.tools.design.check import DesignReport, check_page, clean_widths, judge
from harness.tools.design.guide import guide_text
from harness.tools.design.lint import lint_source
from harness.tools.design.tokens import DesignTokens, find_design_md, read_tokens
from harness.tools.design.tools import design_tools, summary_line

DESIGN_MD = "# Design\n\n- Accent `#74358c`\n- Ink #1e1a24\n- Ground rgb(250, 249, 251)\n\nType: Manrope everywhere.\n"


def _measured(width: int, **over) -> dict:
    base = {
        "width": width,
        "scroll_width": width,
        "past": [],
        "contrast": [],
        "contrast_count": 0,
        "colors": [{"key": "#1e1a24", "count": 4, "where": ["h1 text"]}],
        "fonts": [{"key": "Manrope", "count": 4, "where": ["h1"]}],
        "small": [],
        "small_count": 0,
        "unlabeled": [],
        "no_alt": [],
    }
    base.update(over)
    return base


def _render(*widths: WidthRender, **over) -> Render:
    return Render(widths=list(widths), **over)


def _tokens(tmp_path: Path) -> DesignTokens:
    (tmp_path / "DESIGN.md").write_text(DESIGN_MD, encoding="utf-8")
    return read_tokens(tmp_path / "DESIGN.md")


# -- what a measurement becomes ----------------------------------------------------------


def test_a_clean_page_has_nothing_to_fix(tmp_path):
    render = _render(*(WidthRender(width=w, measured=_measured(w)) for w in (1440, 834, 390)))
    report = judge(render, shown="design/a.html", widths=[1440, 834, 390], tokens=_tokens(tmp_path), label="DESIGN.md")
    assert report.issues == 0
    assert report.text() == "[design check] design/a.html at 1440, 834, 390 px: nothing to fix."
    assert summary_line(report) == "Checked at 1440, 834 and 390 px: nothing to fix"
    assert report.facts()["design_issues"] == 0 and report.facts()["design_md"] is True


def test_each_finding_names_the_part_and_by_how_much(tmp_path):
    phone = _measured(
        390,
        scroll_width=1058,
        past=[{"where": "div.plan", "count": 2}],
        contrast=[{"where": "p.note", "fg": "#9a9a9a", "bg": "#ffffff", "ratio": 2.8, "need": 4.5, "count": 2}],
        colors=[
            {"key": "#1e1a24", "count": 4, "where": ["h1 text"]},
            {"key": "#4f46e5", "count": 1, "where": ["button.cta background"]},
            {"key": "#faf9fb", "count": 1, "where": ["body background"]},
        ],
        fonts=[{"key": "Manrope", "count": 3, "where": ["h1"]}, {"key": "Arial", "count": 1, "where": ["button.cta"]}],
        small=[{"where": "button.cta", "w": 66, "h": 27}],
        small_count=1,
        unlabeled=["div.plan > input"],
        no_alt=["div.plan > img"],
    )
    render = _render(WidthRender(width=1440, measured=_measured(1440)), WidthRender(width=390, measured=phone, png=b"png"),
                     console_errors=["boom"], blocked=["https://fonts.googleapis.com/css2?family=X"])
    report = judge(render, shown="design/a.html", widths=[1440, 390], tokens=_tokens(tmp_path), label="DESIGN.md")
    text = report.text()
    assert "At 390 px the page is 668 px wider than the window; first past the edge: div.plan (×2)." in text
    assert "p.note is #9a9a9a on #ffffff, 2.8:1 (2 places)" in text
    # #faf9fb is DESIGN.md's rgb(250, 249, 251); #4f46e5 is not in it.
    assert "Colors DESIGN.md does not name: #4f46e5 (button.cta background)." in text
    assert "Fonts DESIGN.md does not name: Arial (button.cta)." in text
    assert "1 control(s) under 44 px at 390 px: button.cta (66×27)" in text
    assert "div.plan > input" in text and "div.plan > img" in text and "boom" in text
    assert "fonts.googleapis.com" in text
    # One per line to fix: overflow, contrast, colors, fonts, small, unlabeled, alt, console.
    assert report.issues == 8
    facts = report.facts()
    assert facts["design_overflow_phone"] == 1 and facts["design_contrast"] == 2
    assert facts["design_blocked"] == 1 and facts["design_off_colors"] == 1
    parts = report.attach_parts()
    assert [part["type"] for part in parts] == ["text", "image_url"]
    assert parts[0]["text"] == "design/a.html at 390 px"


def test_without_a_design_md_colors_and_fonts_are_not_judged():
    phone = _measured(390, colors=[{"key": "#4f46e5", "count": 1, "where": ["a"]}], fonts=[{"key": "Arial", "count": 1, "where": ["a"]}])
    report = judge(_render(WidthRender(width=390, measured=phone)), shown="design/a.html", widths=[390], tokens=DesignTokens())
    assert report.issues == 0 and report.design_md is None
    assert "No DESIGN.md" in report.text()


def test_a_page_that_could_not_be_checked_says_why():
    report = judge(_render(error="no Chromium browser was found"), shown="design/a.html", widths=[390], tokens=DesignTokens())
    assert report.text() == "[design check] design/a.html was not opened: no Chromium browser was found."
    assert report.facts() == {
        "design_checked": False, "design_error": "no Chromium browser was found", "design_lint": 0, "design_lint_check": 0,
        "design_unguided": 0,
        "design_locked_changed": 0,
    }
    assert summary_line(report) == "Not opened: no Chromium browser was found"
    # The source is still read, and said, where no browser could open the page.
    report.lint = lint_source("<div onclick='go()'>Buy</div>")
    assert report.text().endswith("Its source:\n- Line 1: a clickable <div>; use <button> for an action or <a href> to go somewhere.")
    assert summary_line(report) == "Not opened: no Chromium browser was found; its source: 1 to fix"


# -- what the source shows ------------------------------------------------------------------

PAGE = """<!doctype html>
<style>
.hero { background: linear-gradient(90deg, #a855f7, #6366f1); }
.card { border-left: 4px solid #74358c; border-radius: 12px; }
.tag { text-transform: uppercase; }
.menu { transition: height 200ms ease; }
</style>
<h1>🚀 Ship faster</h1>
<p>Lorem ipsum dolor sit amet.</p>
<p>Trusted by 10,000+ teams worldwide.</p>
<div class="buy" onclick="buy()">Buy</div>
<button>OK</button>
<script>const s = "🚀 not shown";</script>
"""


def test_the_source_shows_habits_a_browser_cannot_see():
    findings = lint_source(PAGE)
    by_rule = {finding.rule: finding for finding in findings}
    assert set(by_rule) == {
        "gradient", "side-stripe", "caps-tracking", "layout-motion", "reduced-motion", "emoji", "filler",
        "proof", "clickable-div", "vague-label",
    }
    assert by_rule["gradient"].line == 3 and by_rule["side-stripe"].line == 4
    assert by_rule["emoji"].line == 8 and by_rule["clickable-div"].line == 11
    assert "animates `height`" in by_rule["layout-motion"].message
    # Numbers that read like proof are said, never counted: they may be real.
    assert by_rule["proof"].kind == "check"
    report = DesignReport(shown="design/a.html", widths=[390], lint=findings)
    assert report.issues == 9 and report.facts()["design_lint_check"] == 1
    assert "- To check, line 10: “" in report.text()


def test_a_clean_source_and_what_design_md_allows_say_nothing():
    clean = (
        "<style>.b{transition:opacity 150ms ease}@media (prefers-reduced-motion: reduce){.b{transition:none}}"
        ".t{text-transform:uppercase;letter-spacing:.06em}.c{border-left:1px solid #ddd;border-radius:8px}</style>"
        "<h1>Plans</h1><p>[PRICE] a month.</p><button class='b'>Save changes</button><a href='#x'>Pricing</a>"
    )
    assert lint_source(clean) == []
    gradient = "<style>.h{background:linear-gradient(#fff,#eee)}</style><p>x</p>"
    assert [f.rule for f in lint_source(gradient)] == ["gradient"]
    assert lint_source(gradient, design_md_text="the hero uses a soft gradient") == []


def test_tokens_match_spellings_and_fonts_as_words(tmp_path):
    tokens = _tokens(tmp_path)
    assert tokens.names_color("#74358c") and tokens.names_color("#75368d")  # within the tolerance
    assert tokens.names_color("#faf9fb")  # written as rgb()
    assert not tokens.names_color("#4f46e5")
    assert tokens.names_font("Manrope") and tokens.names_font("system-ui")
    assert not tokens.names_font("Inter")  # not found inside "everywhere" or any other word


def test_the_nearest_design_md_wins_inside_the_root(tmp_path):
    (tmp_path / "DESIGN.md").write_text("root", encoding="utf-8")
    page = tmp_path / "design" / "a.html"
    page.parent.mkdir()
    page.write_text("<p>x</p>", encoding="utf-8")
    assert find_design_md(page, [tmp_path]) == tmp_path / "DESIGN.md"
    (page.parent / "DESIGN.md").write_text("near", encoding="utf-8")
    assert find_design_md(page, [tmp_path]) == page.parent / "DESIGN.md"
    assert find_design_md(page, [tmp_path / "elsewhere"]) is None


def test_what_counts_as_a_design_page_and_a_width():
    assert is_design_file(Path("design/pricing.html")) and is_design_file(Path("app/Design/x.htm"))
    assert not is_design_file(Path("design/notes.md")) and not is_design_file(Path("src/index.html"))
    assert clean_widths(None) == [1440, 834, 390]
    assert clean_widths([100, 390, 390, "x", 5000, 800, 1024]) == [280, 390, 2560, 800]


# -- the guide and the tools ---------------------------------------------------------------


def test_the_guide_quotes_design_md_or_says_how_to_draft_one(tmp_path):
    bare = guide_text(tmp_path, [tmp_path], ["words"])
    assert "no DESIGN.md and no styles" in bare and "Words\n- Buttons" in bare and "Layout\n- One clear" not in bare
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.css").write_text(":root { --accent: #74358c; }", encoding="utf-8")
    (tmp_path / "tailwind.config.js").write_text("module.exports = {}", encoding="utf-8")
    drafting = guide_text(tmp_path, [tmp_path])
    assert "Draft one at the project root" in drafting
    assert "- src/app.css" in drafting and "- tailwind.config.js" in drafting
    assert "Layout\n- One clear" in drafting and "Interaction\n- A page is a working prototype" in drafting
    (tmp_path / "DESIGN.md").write_text(DESIGN_MD, encoding="utf-8")
    assert "Manrope everywhere" in guide_text(tmp_path, [tmp_path])


def test_the_guide_gives_a_designers_brief_not_only_a_list_of_mistakes(tmp_path):
    # The same model drew plainer pages here than in OpenDesign, whose prompt hands it directions,
    # recipes and real copy (2026-10-19). The guide now does too.
    from harness.tools.design.directions import DIRECTIONS
    from harness.tools.design.recipes import KINDS

    bare = guide_text(tmp_path, [tmp_path], kind="pricing")
    # With no DESIGN.md, every direction with its fonts, palette and posture, to choose one from.
    for direction in DIRECTIONS:
        assert f"[{direction.id}]" in bare and direction.palette["accent"] in bare and direction.posture[0] in bare
    assert "Pick the direction below that fits the product" in bare
    # The recipe for the kind asked, the file a page starts from, and the last look.
    assert "Pricing page. You are a product designer" in bare and "Landing page. You are its brand designer" not in bare
    assert "Start each page from this file" in bare and "--accent-ink" in bare and "prefers-reduced-motion" in bare
    assert "score the page silently from 1 to 5" in bare and "Anything under 4 is not done" in bare
    # Copy that sounds like the product, said as invented; no wireframe brackets asked for.
    assert "never lorem ipsum or [PRICE] brackets" in bare and "Say in your answer what you invented" in bare
    assert "one accent per view, used for the main action and the current item;\n  everything else is neutrals" not in bare.lower()
    # No kind: the kinds there are, so the model can ask for one.
    assert all(kind in guide_text(tmp_path, [tmp_path]) for kind in KINDS)
    # A project with its own DESIGN.md follows it, without the directions.
    (tmp_path / "DESIGN.md").write_text(DESIGN_MD, encoding="utf-8")
    own = guide_text(tmp_path, [tmp_path], kind="landing")
    assert "Manrope everywhere" in own and "[editorial]" not in own and "Landing page. You are its brand designer" in own
    # The tool takes the kind, and an unknown one is the list of kinds.
    guide, _ = design_tools(str(tmp_path))
    assert "Dashboard. You are a systems designer" in guide(kind="dashboard")["guide"]
    assert "Recipes by kind of page" in guide(kind="poster")["guide"]


def test_every_direction_passes_the_contrast_the_check_holds_pages_to():
    from harness.tools.design.directions import DIRECTIONS

    def luminance(color: str) -> float:
        channels = [int(color.lstrip("#")[i : i + 2], 16) / 255 for i in (0, 2, 4)]
        linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
        return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]

    def ratio(a: str, b: str) -> float:
        high, low = sorted((luminance(a), luminance(b)), reverse=True)
        return (high + 0.05) / (low + 0.05)

    assert len({d.id for d in DIRECTIONS}) == len(DIRECTIONS) >= 5
    for d in DIRECTIONS:
        p = d.palette
        assert ratio(p["ink"], p["ground"]) >= 7, d.id
        assert ratio(p["muted"], p["ground"]) >= 4.5, d.id
        assert ratio(p["accent-ink"], p["accent"]) >= 4.5, d.id
        assert ratio(p["accent"], p["ground"]) >= 4.5, d.id


def test_a_page_written_before_the_guide_is_told_to_read_it(tmp_path):
    from harness.loop.design_checks import guide_read

    engine, _, _, _ = _run(tmp_path, "design/pricing.html", issues=False)
    result = next(m for m in engine.messages if m.get("role") == "tool")
    assert "design_guide has not been read in this conversation" in result["content"]
    rows = [row for row in engine.log.read() if row["type"] == "design/check"]
    assert rows[0]["facts"]["design_unguided"] == 1
    # Read by name, through the deferred-tool bridge, or seen in a result: then nothing is said.
    def call(name, arguments="{}"):
        return {"role": "assistant", "tool_calls": [{"id": "c", "type": "function", "function": {"name": name, "arguments": arguments}}]}

    assert guide_read([call("design_guide")])
    assert guide_read([call("tool_call", '{"name": "design_guide", "arguments": {}}')])
    assert guide_read([{"role": "tool", "content": "How a design is made in Reeve\n- You are the designer"}])
    assert not guide_read([call("check_design"), {"role": "tool", "content": "Wrote design/a.html"}])


def test_check_design_stays_in_the_roots(tmp_path):
    outside = tmp_path.parent / "outside-design.html"
    _, check = design_tools(str(tmp_path))
    assert "error" in check(path=str(outside))
    assert "file not found" in check(path="design/missing.html")["error"]
    (tmp_path / "notes.md").write_text("x", encoding="utf-8")
    assert "only .html" in check(path="notes.md")["error"]
    assert "needs `path`" in check()["error"]


# -- the engine hook ----------------------------------------------------------------------


class _Scripted(ProviderClient):
    def __init__(self, turns):
        self.turns = list(turns)
        self.seen: list[list] = []

    def complete(self, *, model, messages, tools=None, **settings):
        self.seen.append([wire_message(m) for m in messages])
        return self.turns.pop(0) if self.turns else AssistantTurn(text="done", finish_reason="stop")

    def capabilities(self, model):
        return ModelCapabilities()


async def _approve(_request):
    return ApprovalOutcome.ONCE


def _report(shown: str, issues: bool) -> DesignReport:
    if not issues:
        return DesignReport(shown=shown, widths=[1440, 834, 390], design_md="DESIGN.md")
    return DesignReport(
        shown=shown,
        widths=[1440, 834, 390],
        design_md="DESIGN.md",
        overflow=[{"width": 390, "excess": 212, "past": [{"where": "div.plans", "count": 1}]}],
        shots=[(390, b"\x89PNG fake")],
    )


def _run(tmp_path: Path, path: str, *, issues: bool, content: str = "<p>x</p>"):
    registry = ToolRegistry()

    def write_file(path: str = "", content: str = "") -> str:
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return f"Wrote {path}"

    write_file.__name__ = "write_file"
    registry.register(write_file)
    provider = _Scripted(
        [AssistantTurn(tool_calls=[ToolCall(id="w1", name="write_file", arguments={"path": path, "content": content})], finish_reason="tool_calls")]
    )
    engine = TurnEngine(
        provider=provider,
        registry=registry,
        permissions=PermissionEngine(workspace_root=tmp_path),
        model="gpt-5.5",
        approver=_approve,
    )
    engine.log = SessionLog(tmp_path / "session.jsonl")
    checked: list[tuple[Path, str]] = []

    def checker(target: Path, shown: str, asked: tuple = ()) -> DesignReport:
        checked.append((target, shown))
        return _report(shown, issues)

    engine.design_checker = checker

    async def go():
        return [event async for event in engine.run("draw the pricing page")]

    events = asyncio.run(go())
    return engine, provider, events, checked


def test_a_design_write_is_checked_before_the_model_reads_its_result(tmp_path):
    engine, provider, events, checked = _run(tmp_path, "design/pricing.html", issues=True)
    assert checked == [(tmp_path / "design" / "pricing.html", "design/pricing.html")]
    result = next(m for m in engine.messages if m.get("role") == "tool")
    assert result["content"].startswith("Wrote design/pricing.html")
    assert "[design check] design/pricing.html at 1440, 834, 390 px: 1 to fix." in result["content"]
    assert "At 390 px the page is 212 px wider than the window" in result["content"]
    # The screenshot follows the result, and the next request carries both.
    attached = [m for m in engine.messages if m.get("_attached_by") == "w1"]
    assert attached and any(part.get("type") == "image_url" for part in attached[0]["content"])
    second = provider.seen[1]
    assert any("[design check]" in str(m.get("content")) for m in second if m)
    # The row under the write, kept for a reload, never sent.
    assert result["_design_check"][0]["summary"] == "Checked at 1440, 834 and 390 px: 1 to fix"
    assert all("_design_check" not in m for m in second if m)
    finished = [e for e in events if e.type == EventType.TOOL_FINISHED]
    assert finished[0].data["design_check"][0]["issues"] == 1
    rows = [row for row in engine.log.read() if row["type"] == "design/check"]
    assert len(rows) == 1 and rows[0]["path"] == "design/pricing.html"
    assert rows[0]["facts"]["design_overflow_phone"] == 1


def test_a_clean_design_costs_one_line_and_no_picture(tmp_path, monkeypatch):
    # With the guide read (a page written before it gets a line asking for it, tested below).
    monkeypatch.setattr("harness.loop.design_checks.guide_read", lambda messages: True)
    engine, _, _, checked = _run(tmp_path, "design/pricing.html", issues=False)
    assert len(checked) == 1
    result = next(m for m in engine.messages if m.get("role") == "tool")
    assert result["content"].endswith("[design check] design/pricing.html at 1440, 834, 390 px: nothing to fix.")
    assert not [m for m in engine.messages if m.get("_attached_by") == "w1"]


_PAGE = "<!doctype html><html><head><title>Plans</title></head><body><h1>Northwind plans</h1><p>Start free, move to Team when three people write.</p></body></html>"


def test_a_whole_page_written_outside_design_is_a_file_not_a_design(tmp_path):
    # A flow drawn as HTML, which nobody asked to have designed, came with a design's check, card
    # and tools when every page that opened by itself was a design (owner, 2026-10-20).
    engine, _, _, checked = _run(tmp_path, "sister_flow.html", issues=True, content=_PAGE)
    assert checked == []
    result = next(m for m in engine.messages if m.get("role") == "tool")
    assert "[design check]" not in result["content"] and "_design_check" not in result


def test_a_page_under_design_is_checked_whatever_it_holds(tmp_path):
    engine, _, _, checked = _run(tmp_path, "web/design/part.html", issues=False, content="<p>a fragment</p>")
    assert checked == [(tmp_path / "web" / "design" / "part.html", "web/design/part.html")]


def test_any_other_write_is_not_checked(tmp_path):
    engine, _, events, checked = _run(tmp_path, "src/index.html", issues=True)
    assert checked == []
    result = next(m for m in engine.messages if m.get("role") == "tool")
    assert "[design check]" not in result["content"] and "_design_check" not in result
    assert not [row for row in engine.log.read() if row["type"] == "design/check"]


# -- one real load ------------------------------------------------------------------------


@pytest.mark.skipif(find_browser() is None, reason="no Chromium on this machine")
def test_a_real_page_that_runs_off_a_phone_is_caught(tmp_path):
    page = tmp_path / "design" / "plans.html"
    page.parent.mkdir()
    page.write_text(
        "<!doctype html><meta name=viewport content='width=device-width'>"
        "<style>body{margin:0;font-family:sans-serif}.row{display:flex}.card{flex:0 0 300px;height:80px}"
        ".faint{color:#bbbbbb}</style><div class=row><div class=card>A</div><div class=card>B</div></div>"
        "<p class=faint>small print</p><button>Go</button><span onclick='go()'>more</span>",
        encoding="utf-8",
    )
    report = check_page(page, shown="design/plans.html", roots=[tmp_path])
    assert not report.error, report.error
    widths = {item["width"]: item["excess"] for item in report.overflow}
    assert 1440 not in widths and widths[390] == 600 - 390
    assert any(item["where"] == "p.faint" for item in report.contrast)
    assert report.small_count >= 1  # the button is under 44 px on the phone
    assert len(report.shots) == 3 and all(png.startswith(b"\x89PNG") for _, png in report.shots)
    # The same check reads the source: the button that says "Go" and the clickable span.
    assert {finding.rule for finding in report.lint} == {"vague-label", "clickable-div"}


# -- phase 2: what the page did when used ----------------------------------------------------

from harness.tools.design.behaviour import Behaviour  # noqa: E402
from harness.tools.design.check import has_dark_styles, passes_for  # noqa: E402

CORPUS = Path(__file__).parent / "corpora" / "design"


def test_what_a_click_did_becomes_a_sentence_against_the_rules():
    behaviour = Behaviour(
        width=390,
        interactions=[
            {"where": "button.burger", "promises": True, "opened": "nav.menu", "open_steps": 2, "esc": False, "outside": False},
            {"where": "button.signup", "promises": False, "opened": "div.dialog", "open_steps": 9, "modal": True,
             "focus_inside": False, "esc": True, "outside": True, "close_steps": 2},
            {"where": "button.more", "promises": True, "dead": True},
            {"where": "button.help", "promises": False, "dead": True},
            {"where": "button.ok", "promises": False, "opened": "div.toast", "open_steps": 6, "esc": False, "outside": True, "close_steps": 7},
        ],
        dark={"width": 390, "scroll_width": 390, "contrast": [{"where": "h1", "fg": "#3a3540", "bg": "#1c1922", "ratio": 1.5, "need": 3}]},
        long_text={"changed": 12, "excess": 120, "spill": ["button.cta"]},
    )
    fix, check = behaviour.findings()
    assert fix == [
        "Clicking button.burger shows nav.menu at once; menus and dialogs move in (150–250 ms, opacity and transform)",
        "Nothing closes nav.menu: not Esc, not a click outside",
        "Focus stays behind div.dialog when it opens; move it into the dialog",
        "div.dialog vanishes at once when it closes; it should move out the way it came in",
        "button.more says it opens something (aria-expanded, aria-controls or aria-haspopup) and clicking it changed nothing",
        "Esc does not close div.toast",
        # A button that does nothing is a fix on a working prototype.
        "Clicking button.help changed nothing on the page. Make each one answer (a state, a panel, a message) or a "
        'real link, or, if the page is only a picture of a screen, say so with <meta name="design-fidelity" content="static">',
        "In dark mode, text under 3:1: h1 is #3a3540 on #1c1922, 1.5:1",
        "With longer text (a long German word in labels, a Vietnamese sentence in headings) at 390 px, the page runs "
        "120 px off and text spills out of button.cta; let it wrap, or truncate with an ellipsis and the full text on hover",
    ]
    assert check == []
    # On a page that says it is a static mockup, it is said and not counted.
    behaviour.static = True
    static_fix, static_check = behaviour.findings()
    assert len(static_fix) == len(fix) - 1
    assert static_check == ["Clicking button.help changed nothing on the page, which is declared a static mockup"]
    behaviour.static = False
    # When the page runs off, the parts past the edge are named, so the fix is one write.
    named = Behaviour(width=390, long_text={"changed": 3, "excess": 18, "spill": [], "past": [{"where": "span.price", "by": 18}]})
    assert named.findings()[0] == [
        "With longer text (a long German word in labels, a Vietnamese sentence in headings) at 390 px, the page runs "
        "18 px off (past the edge: span.price by 18 px); let it wrap, or truncate with an ellipsis and the full text on hover"
    ]
    report = DesignReport(shown="design/a.html", widths=[1440, 390], behaviour=behaviour)
    assert report.issues == len(fix)
    assert report.facts()["design_behaviour"] == len(fix) and report.facts()["design_dead_clicks"] == 2
    assert ", 5 control(s) clicked at 390 px: 9 to fix." in report.text()
    assert report.facts()["design_static"] is False


def test_a_page_says_it_is_a_static_mockup_in_its_head():
    from harness.tools.design.behaviour import is_static

    assert is_static('<head><meta name="design-fidelity" content="static"></head>')
    assert is_static("<meta content='static' name='design-fidelity' />")
    assert not is_static('<meta name="viewport" content="width=device-width">')
    assert not is_static('<meta name="design-fidelity" content="prototype">')
    assert not is_static("<p>a static mockup of the screen</p>")


def test_what_runs_besides_the_measuring_follows_the_page():
    assert passes_for("<p>static</p>", DesignTokens()) == {"long_text"}
    assert passes_for("<button onclick='x()'>Go</button>", DesignTokens()) == {"long_text", "interact"}
    assert passes_for("<details><summary>More</summary></details>", DesignTokens()) == {"long_text", "interact"}
    dark = "<style>@media (prefers-color-scheme: dark){body{background:#000}}</style><script></script>"
    assert passes_for(dark, DesignTokens()) == {"long_text", "interact", "dark"}
    assert has_dark_styles("[data-theme='dark'] body{}") and not has_dark_styles(".darker{color:red}")


def test_a_collection_with_no_state_drawn_is_said_once_and_not_counted():
    listing = "<ul><li>Trip</li><li>Standup</li><li>Reading</li></ul>"
    findings = lint_source(listing)
    assert [(f.rule, f.kind, f.line) for f in findings] == [("states", "check", 1)]
    assert lint_source(listing + "<p>No notes yet</p>") == []
    assert lint_source(listing, state_pages=True) == []


def test_state_pages_beside_a_design_lift_the_states_note(tmp_path):
    page = tmp_path / "design" / "notes.html"
    page.parent.mkdir()
    page.write_text("<ul><li>a</li><li>b</li><li>c</li></ul>", encoding="utf-8")
    missing = check_page(page, shown="design/notes.html", roots=[tmp_path], browser=tmp_path / "no-browser.exe")
    assert [f.rule for f in missing.lint] == ["states"]
    (page.parent / "notes.empty.html").write_text("<p>No notes yet</p>", encoding="utf-8")
    drawn = check_page(page, shown="design/notes.html", roots=[tmp_path], browser=tmp_path / "no-browser.exe")
    assert drawn.lint == []


def test_dark_promised_by_design_md_and_missing_from_the_page_is_a_fix(tmp_path):
    (tmp_path / "DESIGN.md").write_text("Colors: ink #1e1a24 (dark grey). Dark mode: ground #1c1922.", encoding="utf-8")
    page = tmp_path / "design" / "a.html"
    page.parent.mkdir()
    page.write_text("<p>hello</p>", encoding="utf-8")
    report = check_page(page, shown="design/a.html", roots=[tmp_path], browser=tmp_path / "no-browser.exe")
    assert report.behaviour.dark_missing
    (tmp_path / "DESIGN.md").write_text("Ink #1e1a24, a dark grey.", encoding="utf-8")
    report = check_page(page, shown="design/a.html", roots=[tmp_path], browser=tmp_path / "no-browser.exe")
    assert not report.behaviour.dark_missing  # a dark colour is not a dark scheme


@pytest.mark.skipif(find_browser() is None, reason="no Chromium on this machine")
def test_a_control_that_only_restyles_or_sets_a_field_answered_and_one_that_does_nothing_is_a_fix(tmp_path):
    """In a real browser: a swatch that sets an inline colour and a stepper that sets an
    input's value both answered; a button with no handler did not, and on a prototype that is a fix."""
    (tmp_path / "DESIGN.md").write_text("Accent #74358c, ink #1e1a24, canvas #faf9fb.", encoding="utf-8")
    folder = tmp_path / "design"
    folder.mkdir()
    page = (
        "<!doctype html><html><head><meta charset='utf-8'><style>body{font-family:sans-serif;color:#1e1a24;"
        "background:#faf9fb} button{min-height:44px;min-width:44px;margin:8px}</style></head><body>"
        "<div id='swatch' style='width:80px;height:40px;background:#faf9fb'></div>"
        "<button class='paint' onclick=\"document.getElementById('swatch').style.background='#74358c'\">Paint</button>"
        "<input id='qty' aria-label='Quantity' value='1'>"
        "<button class='more' onclick=\"document.getElementById('qty').value='2'\">Add one</button>"
        "<button class='nothing'>Buy now</button>"
        "</body></html>"
    )
    (folder / "shop.html").write_text(page, encoding="utf-8")
    report = check_page(folder / "shop.html", shown="s", roots=[tmp_path])
    assert not report.error, report.error
    dead = [entry["where"] for entry in report.behaviour.interactions if entry.get("dead")]
    assert dead == ["button.nothing"]
    fix, _ = report.behaviour.findings()
    assert any(line.startswith("Clicking button.nothing changed nothing") for line in fix)
    (folder / "shop.html").write_text(page.replace("<head>", "<head><meta name='design-fidelity' content='static'>"), encoding="utf-8")
    static = check_page(folder / "shop.html", shown="s", roots=[tmp_path])
    assert static.behaviour.static and not any("button.nothing" in line for line in static.behaviour.findings()[0])


@pytest.mark.skipif(find_browser() is None, reason="no Chromium on this machine")
def test_the_choice_already_made_is_not_tried_and_a_lone_pressed_toggle_still_is(tmp_path):
    """The kit's billing toggle, Monthly pressed and Yearly not, read as a dead Monthly button once a
    still control became a fix. The pressed one of a set, the selected tab and the
    current page are choices made; a lone pressed toggle is a control that turns off."""
    (tmp_path / "DESIGN.md").write_text("Accent #74358c, ink #1e1a24, canvas #faf9fb.", encoding="utf-8")
    folder = tmp_path / "design"
    folder.mkdir()
    flip = "this.setAttribute('aria-pressed', String(this.getAttribute('aria-pressed') !== 'true'))"
    page = (
        "<!doctype html><html><head><meta charset='utf-8'><style>body{font-family:sans-serif;color:#1e1a24;"
        "background:#faf9fb} button,a{display:inline-block;min-height:44px;min-width:44px;margin:8px}</style></head><body>"
        "<div role='group' aria-label='Billing'>"
        "<button class='monthly' aria-pressed='true'>Monthly</button>"
        "<button class='yearly' aria-pressed='false' onclick=\"this.setAttribute('aria-pressed','true')\">Yearly</button></div>"
        "<div role='tablist'><button role='tab' class='first' aria-selected='true'>One</button></div>"
        "<a class='here' href='#top' aria-current='page'>Home</a>"
        f"<p><button class='bold' aria-pressed='true' onclick=\"{flip}\">Bold</button></p>"
        "</body></html>"
    )
    (folder / "plans.html").write_text(page, encoding="utf-8")
    report = check_page(folder / "plans.html", shown="s", roots=[tmp_path])
    assert not report.error, report.error
    tried = {entry["where"] for entry in report.behaviour.interactions}
    assert tried == {"button.yearly", "button.bold"}, tried
    assert not any(entry.get("dead") for entry in report.behaviour.interactions)
    assert not any("changed nothing" in line for line in report.behaviour.findings()[0])


def test_a_real_page_is_clicked_through_and_a_good_one_passes(tmp_path):
    (tmp_path / "DESIGN.md").write_text(
        "Accent #74358c, ink #1e1a24, canvas #faf9fb, panel #ffffff, line #d8d2de. Manrope everywhere.", encoding="utf-8"
    )
    folder = tmp_path / "design"
    folder.mkdir()
    for name in ("interactions_broken.html", "interactions_good.html"):
        (folder / name).write_text((CORPUS / name).read_text(encoding="utf-8"), encoding="utf-8")
    broken = check_page(folder / "interactions_broken.html", shown="b", roots=[tmp_path])
    fix, check = broken.behaviour.findings()
    assert not broken.error, broken.error
    assert "Clicking button.burger shows nav.menu at once; menus and dialogs move in (150–250 ms, opacity and transform)" in fix
    assert "Nothing closes nav.menu: not Esc, not a click outside" in fix
    assert "Focus stays behind div.dialog when it opens; move it into the dialog" in fix
    assert "Nothing closes div.dialog: not Esc, not a click outside" in fix
    assert any(line.startswith("In dark mode, text under 3:1: h1") for line in fix)
    assert any(line.startswith("With longer text") for line in fix)
    assert any("button.help" in line for line in fix)  # a still button is a fix on a prototype
    # The light measurement stays light on a machine set to dark.
    assert not any(item["fg"] == "#3a3540" for item in broken.contrast)
    good = check_page(folder / "interactions_good.html", shown="g", roots=[tmp_path])
    assert not good.error, good.error
    assert len(good.behaviour.interactions) == 2
    assert good.behaviour.findings() == ([], [])
    assert good.issues == 0, good.text()


# -- phase 3d: what a write changed ---------------------------------------------------------

from harness.loop.design_checks import pointed_at  # noqa: E402
from harness.tools.design.versions import changes as design_changes, related, say as say_changes, unasked  # noqa: E402


def _part(key, **over):
    base = {"key": key, "name": key, "text": "", "x": 0, "y": 0, "w": 100, "h": 40, "color": "#111111", "bg": "", "size": 16, "weight": 400}
    base.update(over)
    return base


def test_two_versions_say_what_changed_part_by_part():
    before = [_part("h1", text="Plans", size=28, weight=700), _part("main > button", name="button.cta", text="Choose plan", w=120),
              _part("main > a", text="Privacy"), _part("p", text="Small print", color="#9a9a9a", y=200)]
    after = [_part("h1", text="Plans", size=22, weight=700), _part("main > button", name="button.cta", text="Start free", w=374),
             _part("p", text="Small print", color="#5e5768", y=203), _part("footer", text="Contact")]
    said = [change["said"] for change in design_changes(before, after)]
    assert said == [
        "h1 type 28px/700 → 22px/700",
        "button.cta text “Choose plan” → “Start free”",
        "button.cta size 120×40 → 374×40",
        "p colour #9a9a9a → #5e5768",  # moved 3 px: a reflow, not named
        "footer “Contact” added",
        "main > a “Privacy” removed",
    ]


def test_only_changes_away_from_what_was_pointed_at_are_unasked():
    found = design_changes([_part("main > button", w=120), _part("h1", size=28)], [_part("main > button", w=300), _part("h1", size=22)])
    assert [c["key"] for c in unasked(found, ["main > button"])] == ["h1"]
    assert unasked(found, []) == []
    assert related("main > div > span", "main > div") and related("main", "main > div") and not related("main > divx", "main > div")
    assert say_changes([{"key": str(i), "said": f"c{i}"} for i in range(10)])[-1] == "and 2 more"


def test_the_parts_pointed_at_are_read_from_the_latest_message():
    messages = [
        {"role": "user", "content": "earlier\nselector: h1"},
        {"role": "assistant", "content": "ok"},
        {"role": "user", "content": [
            {"type": "text", "text": "make it bigger"},
            {"type": "text", "text": "[attached text: design/a.html · “Go” button]\nThe person pointed at this element\nselector: main > button"},
            {"type": "text", "text": "1. Darker\n   selector: p.note"},
        ]},
        {"role": "user", "content": [{"type": "text", "text": "selector: not-the-person"}], "_attached_by": "w1"},
    ]
    assert pointed_at(messages) == ("main > button", "p.note")
    assert pointed_at([{"role": "user", "content": "no pointing"}]) == ()


@pytest.mark.skipif(find_browser() is None, reason="no Chromium on this machine")
def test_a_real_second_version_is_told_what_it_changed_outside_what_was_pointed_at(tmp_path):
    page = tmp_path / "design" / "v.html"
    page.parent.mkdir()
    page.write_text("<h1 style='font-size:28px'>Plans</h1><main><button style='width:120px;height:44px'>Choose plan</button>"
                    "<a href='#p'>Privacy</a></main>", encoding="utf-8")
    first = check_page(page, shown="design/v.html", roots=[tmp_path])
    assert {item["key"] for item in first.inventory} >= {"h1", "main > button", "main > a"}
    assert first.thumb.startswith(b"\xff\xd8")  # a JPEG
    page.write_text("<h1 style='font-size:22px'>Plans</h1><main><button style='width:100%;height:48px'>Choose plan</button></main>",
                    encoding="utf-8")
    second = check_page(page, shown="design/v.html", roots=[tmp_path], previous=first.inventory, asked=["main > button"])
    assert [c["key"] for c in second.unasked] == ["h1", "main > a"]
    assert "To check: changed outside what the person pointed at: h1 type 28px/700 → 22px/700; main > a “Privacy” removed." in second.text()
    card = design_card(second)
    assert card["unasked"] == ["h1 type 28px/700 → 22px/700", "main > a “Privacy” removed"]
    assert card["thumb"].startswith("data:image/jpeg;base64,") and card["path"] == "design/v.html"


def test_a_pages_versions_are_the_cards_its_writes_left_in_the_thread(tmp_path):
    from harness.interface.server.manager.design_versions import DesignVersionsMixin

    page = tmp_path / "design" / "a.html"
    page.parent.mkdir()
    page.write_text("<p>x</p>", encoding="utf-8")
    other = tmp_path / "design" / "b.html"
    other.write_text("<p>y</p>", encoding="utf-8")

    def card(path, summary, **more):
        return {"summary": summary, "lines": [], "issues": more.pop("issues", 0), "path": path, **more}

    class Manager(DesignVersionsMixin):
        def _artifact_target(self, session_id, path):
            target = (tmp_path / path).resolve() if not Path(path).is_absolute() else Path(path)
            return (target, None) if target.exists() else (None, "not found")

        def session_messages(self, session_id):
            return [
                {"role": "user", "content": "draw it"},
                {"role": "tool", "tool_call_id": "w1", "ts": 10, "_design_check": [card("design/a.html", "Checked: 2 to fix", issues=2, thumb="data:image/jpeg;base64,AA")]},
                {"role": "tool", "tool_call_id": "w2", "ts": 20, "_design_check": [card("design/b.html", "other page")]},
                {"role": "tool", "tool_call_id": "w3", "ts": 30, "content": "no card"},
                {"role": "tool", "tool_call_id": "w4", "ts": 40, "_design_check": [card(str(page), "Checked: nothing to fix", changes=["h1 moved"], unasked=["h1 moved"])]},
            ]

    found = Manager().design_versions("s1", "design/a.html")
    assert found["ok"] and [v["call_id"] for v in found["versions"]] == ["w1", "w4"]
    assert [v["n"] for v in found["versions"]] == [1, 2]
    assert found["versions"][0]["thumb"] == "data:image/jpeg;base64,AA" and found["versions"][1]["thumb"] is None
    assert found["versions"][1]["changes"] == ["h1 moved"] and found["versions"][1]["unasked"] == ["h1 moved"]
    assert Manager().design_versions("s1", "design/missing.html")["ok"] is False


def test_a_sessions_designs_are_its_design_pages_with_their_last_checks(tmp_path):
    import os
    from harness.interface.server.manager.design_versions import DesignVersionsMixin

    design = tmp_path / "design"
    design.mkdir()
    for name, mtime in (("pricing.html", 100), ("pricing.a.html", 200), ("notes.txt", 300)):
        (design / name).write_text("<p>x</p>", encoding="utf-8")
        os.utime(design / name, (mtime, mtime))
    nested = tmp_path / "web" / "design" / "screens"
    nested.mkdir(parents=True)
    (nested / "login.html").write_text("<p>y</p>", encoding="utf-8")
    os.utime(nested / "login.html", (400, 400))
    skipped = tmp_path / "node_modules" / "design"
    skipped.mkdir(parents=True)
    (skipped / "vendor.html").write_text("<p>z</p>", encoding="utf-8")
    elsewhere = tmp_path / "mock.html"
    elsewhere.write_text("<p>w</p>", encoding="utf-8")
    os.utime(elsewhere, (50, 50))
    (tmp_path / "DESIGN.md").write_text("Accent #6A3577, ink #1e1a22, again #6a3577", encoding="utf-8")

    class Manager(DesignVersionsMixin):
        def _is_remote_record(self, record):
            return False

        class session_store:
            @staticmethod
            def load(session_id):
                return None

        def _session_folders(self, session_id):
            return [tmp_path.resolve()]

        def _artifact_target(self, session_id, path):
            target = (tmp_path / path).resolve()
            return (target, None) if target.is_file() else (None, "not found")

        def session_messages(self, session_id):
            return [
                {"role": "tool", "ts": 1, "_design_check": [{"path": "design/pricing.html", "summary": "Checked: 2 to fix", "issues": 2, "thumb": "data:image/jpeg;base64,AA"}]},
                {"role": "tool", "ts": 2, "_design_check": [{"path": "design/pricing.html", "summary": "Checked: nothing to fix", "issues": 0}]},
                {"role": "tool", "ts": 3, "_design_check": [{"path": "mock.html", "summary": "Checked: nothing to fix", "issues": 0}]},
                {"role": "tool", "ts": 4, "_design_check": [{"path": "design/gone.html", "summary": "x", "issues": 0}]},
            ]

    found = Manager().design_files("s1")
    assert found["ok"]
    # A check of a page outside design/ does not make it a design (owner, 2026-10-20).
    assert [row["path"] for row in found["files"]] == ["web/design/screens/login.html", "design/pricing.a.html", "design/pricing.html"]
    pricing = found["files"][2]
    # The last card says what the page is now; the last picture any card had stays.
    assert (pricing["checks"], pricing["summary"], pricing["issues"], pricing["thumb"], pricing["checked_at"]) == (2, "Checked: nothing to fix", 0, "data:image/jpeg;base64,AA", 2)
    assert found["files"][1]["variant_of"] == "design/pricing.html" and found["files"][1]["letter"] == "a"
    assert found["files"][0]["checks"] == 0 and "variant_of" not in found["files"][0]
    assert found["design_md"] == {"path": "DESIGN.md", "colors": ["#6a3577", "#1e1a22"]}


@pytest.mark.skipif(find_browser() is None, reason="no Chromium on this machine")
def test_longer_text_names_the_parts_past_the_edge(tmp_path):
    # A real session rewrote a page five times on "runs 18 px off" with no part named (2026-10-19).
    page = tmp_path / "design" / "row.html"
    page.parent.mkdir()
    page.write_text(
        "<!doctype html><meta name=viewport content='width=device-width'><style>body{margin:0;font-family:sans-serif}"
        ".row{display:flex;gap:8px;padding:8px}.row a{white-space:nowrap;padding:12px;border:1px solid #333}</style>"
        "<div class=row><a class=plan href=#>Free plan</a><a class=cta href=#>Team plan</a><a class=biz href=#>Business</a></div>"
        "<p>Short note.</p>",
        encoding="utf-8",
    )
    report = check_page(page, shown="design/row.html", roots=[tmp_path])
    line = next(line for line in report.behaviour.findings()[0] if line.startswith("With longer text"))
    assert "(past the edge: a.plan by " in line and "a.biz by " in line


@pytest.mark.skipif(find_browser() is None, reason="no Chromium on this machine")
def test_a_marked_area_is_shot_at_its_own_size_from_the_page_laid_out_at_its_width(tmp_path):
    import base64 as b64
    import struct

    from harness.interface.server.manager.design_versions import DesignVersionsMixin

    page = tmp_path / "design" / "box.html"
    page.parent.mkdir()
    page.write_text(
        "<!doctype html><style>body{margin:0}.box{position:absolute;left:100px;top:1200px;width:200px;height:80px;background:#d03a1a}</style>"
        "<div class=box></div><div style='height:1600px'></div>",
        encoding="utf-8",
    )

    class Manager(DesignVersionsMixin):
        def _artifact_target(self, session_id, path):
            target = (tmp_path / path).resolve()
            return (target, None) if target.is_file() else (None, "not found")

    shot = Manager().design_shot("s1", "design/box.html", 800, {"x": 90, "y": 1190, "w": 220, "h": 100})
    assert shot["ok"], shot
    png = b64.b64decode(shot["png"].split(",", 1)[1])
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    # Below the first screen too: the area is the page's, not the window's.
    assert struct.unpack(">II", png[16:24]) == (220, 100)
    import io

    from PIL import Image

    picture = Image.open(io.BytesIO(png)).convert("RGB")
    assert picture.getpixel((110, 50)) == (0xD0, 0x3A, 0x1A)  # the box, where it is on the page
    assert picture.getpixel((3, 3)) == (255, 255, 255)  # the margin around it
    assert Manager().design_shot("s1", "design/box.html", 99999, {"x": 0, "y": 0, "w": 10, "h": 10})["ok"] is False
    assert Manager().design_shot("s1", "design/missing.html", 800, {"x": 0, "y": 0, "w": 10, "h": 10})["ok"] is False


def test_a_tint_or_shade_of_a_named_colour_is_named_and_a_new_colour_is_not():
    # A badge on a 10% tint of the accent was called a colour DESIGN.md does not name (2026-10-19).
    from harness.tools.design.tokens import DesignTokens, _hex_rgb

    tokens = DesignTokens(colors=[_hex_rgb("#1d746b"), _hex_rgb("#fdf8f3"), _hex_rgb("#2b2420")])
    assert tokens.names_color("#1d746b")           # the colour itself
    assert tokens.names_color("#e8f1f0")           # a tint, mixed with white
    assert tokens.names_color("#e9f1ef")           # mixed with the ground
    assert tokens.names_color("#0f3a36")           # a shade, mixed with black
    assert not tokens.names_color("#8a3fd1")       # a purple nobody chose
    assert not tokens.names_color("#ff8800")


@pytest.mark.skipif(find_browser() is None, reason="no Chromium on this machine")
@pytest.mark.parametrize("direction_id", ["quiet-product", "warm-human", "swiss-grid"])
def test_the_starting_file_bound_to_a_direction_checks_clean(tmp_path, direction_id):
    # The file every page starts from must not hand the model findings of its own: it once had nav
    # links 25 px tall at 390 px and buttons that ran off with longer text (2026-10-19). Bound to a
    # direction means its palette, its fonts and the kit's folders beside it (2026-10-20).
    from design_pages import bind, project_for

    d = project_for(tmp_path, direction_id)
    page = bind(d).replace("<!-- The design goes here. -->", "<h1>Notes that keep up</h1><p>Start free.</p>"
                           '<div class="row"><a class="btn primary" href="#">Start free</a><a class="btn" href="#">Pricing</a></div>')
    target = tmp_path / "design" / "start.html"
    target.write_text(page, encoding="utf-8")
    report = check_page(target, shown="design/start.html", roots=[tmp_path])
    assert not report.error and report.issues == 0, report.text()
    assert not report.unloaded_fonts and not report.missing


def test_a_folder_reached_only_to_read_lends_no_designs(tmp_path):
    """A session that read one file in Downloads listed Downloads' pricing pages and DESIGN.md as its
    own designs (owner, 2026-10-20). A reached folder is someone else's; a person-added one counts."""
    from types import SimpleNamespace

    from harness.interface.server.manager.design_versions import DesignVersionsMixin

    work, downloads, added = tmp_path / "work", tmp_path / "Downloads", tmp_path / "other-app"
    for folder in (work, downloads / "design", added / "design"):
        folder.mkdir(parents=True)
    (downloads / "design" / "pricing.html").write_text("<p>x</p>", encoding="utf-8")
    (downloads / "design" / "written-here.html").write_text("<p>x</p>", encoding="utf-8")
    (downloads / "DESIGN.md").write_text("Accent #123456", encoding="utf-8")
    (added / "design" / "screen.html").write_text("<p>x</p>", encoding="utf-8")
    record = SimpleNamespace(extra_roots=[
        {"path": str(downloads), "writable": False, "reached": True},
        {"path": str(added), "writable": True},
    ])

    class Manager(DesignVersionsMixin):
        def _is_remote_record(self, record):
            return False

        session_store = SimpleNamespace(load=lambda session_id: record)

        def _session_folders(self, session_id):
            return [work.resolve(), downloads.resolve(), added.resolve()]

        def _artifact_target(self, session_id, path):
            target = Path(path) if Path(path).is_absolute() else work / path
            return (target.resolve(), None) if target.is_file() else (None, "not found")

        def session_messages(self, session_id):
            # A page this session itself wrote and checked there still counts.
            return [{"role": "tool", "ts": 1, "_design_check": [{"path": str(downloads / "design" / "written-here.html"), "summary": "Checked", "issues": 0}]}]

    found = Manager().design_files("s1")
    names = sorted(row["name"] for row in found["files"])
    assert names == ["screen.html", "written-here.html"]
    assert found["design_md"] is None


def test_a_design_skill_the_person_loaded_is_guidance_and_another_skill_is_not():
    # A session loaded the person's own `frontend-design` skill, drew by it, and
    # was told on each of 25 writes to read design_guide and redraw.
    from harness.loop.design_checks import guide_read

    def loaded(name: str, instructions: str, *, error: bool = False):
        call = {"role": "assistant", "tool_calls": [{"id": "s1", "type": "function", "function": {"name": "load_skill", "arguments": json.dumps({"name": name})}}]}
        body = {"error": f"unknown skill: {name}"} if error else {"name": name, "instructions": instructions}
        return [call, {"role": "tool", "tool_call_id": "s1", "content": json.dumps(body)}]

    assert guide_read(loaded("frontend-design", "Distinctive, intentional visual design."))
    assert guide_read(loaded("brand-kit", "How our landing pages and UI look."))
    assert not guide_read(loaded("work-messages", "How to write a status update to a manager."))
    assert not guide_read(loaded("frontend-design", "", error=True))
    # A tool result that only names a design skill, without the load, is not a load.
    assert not guide_read([{"role": "tool", "tool_call_id": "x", "content": json.dumps({"name": "frontend-design", "instructions": "design"})}])


def test_a_turn_ending_on_a_page_with_something_to_fix_gets_one_note(tmp_path, monkeypatch):
    monkeypatch.setattr("harness.loop.design_checks.guide_read", lambda messages: True)
    engine, provider, _, _ = _run(tmp_path, "design/pricing.html", issues=True)
    notes = [m for m in engine.messages if m.get("_attached_by") == "design_nudge"]
    assert len(notes) == 1 and "design/pricing.html (1 to fix)" in notes[0]["content"]
    # The model was asked again with the note, and the turn then ended (one note, not a loop).
    assert any("design/pricing.html (1 to fix)" in str(m.get("content")) for m in provider.seen[-1] if m)
    rows = [row for row in engine.log.read() if row["type"] == "verify/nudge"]
    assert [(row["reason"], row["count"]) for row in rows] == [("design_unfixed", 1)]


def test_a_page_whose_last_check_is_clean_gets_no_note(tmp_path, monkeypatch):
    monkeypatch.setattr("harness.loop.design_checks.guide_read", lambda messages: True)
    engine, _, _, _ = _run(tmp_path, "design/pricing.html", issues=False)
    assert not [m for m in engine.messages if m.get("_attached_by") == "design_nudge"]
