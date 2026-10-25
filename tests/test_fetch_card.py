"""The fetch card (`web_fetch`'s `present_result`): what a person sees of a fetched page.

Measured 2026-10-05 on real pages (docs.python.org, Wikipedia, a blog, a 404, a JSON API): the
card named the page by its address twice, showed the model's text as raw Markdown with its
furniture, coloured a 404 like a 200, and showed nothing of an answer's quotes or a phrase's
matches. These pin the card the GUI now draws (`cards/FetchCard.tsx`). The pages are invented
in the shape the extractor produced from the real ones.
"""

from __future__ import annotations

from harness.integrations.web import fetch as fetch_mod
from harness.integrations.web.extract import extract
from harness.integrations.web.fetch_present import _fetch_present_result, _web_fetch_render
from harness.integrations.web.page_preview import page_preview, text_preview

# What html2text makes of a docs page: a title heading with its anchor, a link-only language
# list, a link with a relative target, an image, a rule, prose, an indented code block.
DOCS = """# Widget tasks¶

  * [Català](<https://ca.widgets.example/tasques> "Català")
  * [Français](<https://fr.widgets.example/taches> "Français")

[Edit links](<https://widgets.example/edit>)

This section outlines the widget APIs. See [the glossary](</3/glossary.html#widget>) first.

![diagram](<https://cdn.widgets.example/diagram.png>)

* * *

## Running a widget¶

    >>> import widgets
    >>> widgets.run()

Widgets run until they are cancelled."""


class _Resp:
    """What `get_checked` hands the tool: one HTML answer."""

    def __init__(self, body: bytes):
        self.status_code, self.content, self.encoding = 200, body, "utf-8"
        self.headers = {"content-type": "text/html; charset=utf-8"}
        self.extensions: dict = {}

    def iter_bytes(self):
        yield self.content

    def close(self) -> None:
        pass


def _card(arguments, value):
    return _fetch_present_result(arguments, value).card


def _page(text, **extra):
    return {
        "url": "https://docs.widgets.example/3/library/widget-tasks.html",
        "status_code": 200,
        "content_type": "text/html; charset=utf-8",
        "format": "markdown",
        "page_chars": len(text),
        "text": text,
        "start": 0,
        "end": len(text),
        "truncated": False,
        "title": "Widget tasks — Widgets 3.1 documentation",
        **extra,
    }


# -- the page's start, as Markdown a card can render -------------------------------------------


def test_the_preview_keeps_the_page_and_drops_its_furniture():
    preview = page_preview(DOCS, title="Widget tasks — Widgets 3.1 documentation")
    for furniture in ("¶", "](", "![", "* * *", "Català", "Edit links", "# Widget tasks"):
        assert furniture in DOCS  # the corpus really carries it
        assert furniture not in preview, furniture
    # The words of a link stay; the page's structure stays.
    assert "See the glossary first." in preview
    assert "## Running a widget" in preview
    assert "    >>> import widgets\n    >>> widgets.run()" in preview
    assert preview.endswith("Widgets run until they are cancelled.")


def test_a_first_heading_stays_when_it_is_not_the_title():
    assert page_preview("# Simon's Weblog\n\nA post.", title="Building tools").startswith("# Simon's Weblog")


def test_the_cut_falls_between_blocks_and_never_inside_a_fence():
    text = "First paragraph.\n\n```python\n" + "x = 1\n" * 50 + "```\n\nLast."
    preview = page_preview(text, limit=60)
    assert preview == "First paragraph."  # the fence did not fit, so it is not half-shown
    long = "word " * 100
    cut = page_preview(long, limit=40)
    assert len(cut) <= 40 and cut.endswith("…") and not cut.endswith(" …")
    fence = page_preview("```\n" + "line\n" * 40 + "```", limit=50)
    assert fence.startswith("```") and fence.endswith("```") and fence.count("```") == 2
    assert page_preview("```\nnever closed", limit=500).endswith("```")


def test_prose_a_model_wrote_keeps_its_links():
    answer = "See [the docs](https://widgets.example/docs).\n\n[Changelog](https://widgets.example/c)"
    kept = page_preview(answer, links=True)
    assert "[the docs](https://widgets.example/docs)" in kept and "[Changelog]" in kept
    assert "![x](https://cdn.example/x.png)" not in page_preview("![x](https://cdn.example/x.png) ok", links=True)


def test_json_on_one_line_is_indented_even_when_the_window_cut_it():
    raw = '{"info":{"name":"widgets","tags":["a, b","{not nested}"],"quote":"say \\", then"},"releases":{"1.0":[{"size":12'
    shown = text_preview(raw, json=True)
    assert shown.splitlines()[:3] == ["{", '  "info": {', '    "name": "widgets",']
    # Punctuation inside a string is the string's, and an escaped quote does not end it.
    assert '"a, b",' in shown and '"{not nested}"' in shown and '"quote": "say \\", then"' in shown
    assert shown.rstrip().endswith('"size": 12')
    assert text_preview('{"a":1}\n') == '{"a":1}'  # not asked: as it came


# -- the page's own name -------------------------------------------------------------------------


def test_the_card_names_the_page_by_its_title_and_the_model_text_does_not_carry_it(monkeypatch):
    html = (
        b"<html><head><title>  Widget\n  tasks | Widgets </title></head><body>"
        b"<svg><title>an icon</title></svg><main><h1>Widget tasks</h1><p>"
        + b"Widgets run until they are cancelled. " * 8
        + b"</p></main></body></html>"
    )
    monkeypatch.setattr(fetch_mod, "get_checked", lambda client, u, **kw: _Resp(html))
    value = fetch_mod.make_web_fetch_tool()("https://docs.widgets.example/tasks")
    card = _card({"url": "https://docs.widgets.example/tasks"}, value)
    assert card["title"] == "Widget tasks | Widgets"
    assert "Widget tasks | Widgets" not in _web_fetch_render({}, value)
    assert extract("<svg><title>icon</title></svg><p>x</p>").title == ""
    assert extract("<title>\n  Widget\n  tasks </title><p>x</p>").title == "Widget tasks"


# -- what the call got, by how it read the page ----------------------------------------------------


def test_a_page_read_says_where_the_text_sits_in_the_page_and_how_it_was_read():
    value = _page(DOCS, start=0, end=400, page_chars=5400, truncated=True, scope="main",
                  boilerplate_chars=1900, cached_seconds=75, https="plain-http")
    card = _card({"url": value["url"]}, value)
    assert card["mode"] == "page" and card["excerpt_kind"] == "markdown"
    assert card["read"] == {"start": 0, "end": 400, "page_chars": 5400}
    assert card["about"] == {"left_out_chars": 1900, "cached_seconds": 75, "plain_http": True}
    assert "](" not in card["excerpt"]


def test_a_refusal_keeps_its_status_and_its_page():
    card = _card({}, _page("# Page not found\n\nNothing here.", status_code=404, title="Page not found"))
    assert card["status_code"] == 404 and card["excerpt"] == "Nothing here."


def test_text_that_is_not_markdown_is_shown_as_it_is():
    assert _card({}, _page("a\nb", content_type="text/plain"))["excerpt_kind"] == "text"
    assert _card({}, _page('{"a":1}', content_type="application/json"))["excerpt_kind"] == "code"
    assert _card({}, _page("<p>x</p>", format="raw"))["excerpt_kind"] == "code"


def test_an_answer_carries_the_question_and_every_quote_counted():
    quotes = [{"quote": f"quote {i}", "at": i * 10 if i % 3 else None} for i in range(11)]
    value = {**_page(""), "format": "answer", "text": "The **answer**.", "answered_by": "reader-model",
             "evidence": quotes, "page_parts": 3, "page_chars": 9000, "page_cut_to": 6000}
    card = _card({"prompt": "What is it?"}, value)
    answer = card["mode"], card["answer"]
    assert answer[0] == "answer"
    shown = answer[1]
    assert shown["question"] == "What is it?" and shown["text"] == "The **answer**."
    assert shown["by"] == "reader-model" and shown["parts"] == 3 and shown["unread_chars"] == 3000
    # The card shows eight quotes and counts all eleven: 0, 3, 6, 9 are not in the page.
    assert len(shown["quotes"]) == 8 and shown["quotes_total"] == 11 and shown["quotes_found"] == 7
    assert shown["quotes"][0] == {"quote": "quote 0", "found": False}
    assert shown["quotes"][1] == {"quote": "quote 1", "found": True}


def test_a_find_carries_each_place_without_the_pages_markup():
    matches = [{"at": 2700, "text": "* The `widgets.Group` class is [newer](</x>)."}]
    value = {**_page(""), "format": "find", "find": "Group", "match_count": 18, "matches": matches}
    found = _card({"find": "Group"}, value)["found"]
    assert found == {"phrase": "Group", "count": 18,
                     "matches": [{"at": 2700, "text": "The widgets.Group class is newer."}]}


def test_a_redirect_names_where_it_goes_and_offers_only_a_web_address():
    moved = {"url": "https://short.example/a", "status_code": 301, "format": "redirect",
             "redirect_to": "https://docs.widgets.example/start", "text": ""}
    assert _card({}, moved)["redirect"] == {"host": "docs.widgets.example", "url": "https://docs.widgets.example/start"}
    hostile = {**moved, "redirect_to": "javascript:alert(1)"}
    assert "url" not in (_card({}, hostile).get("redirect") or {})


def test_an_attachment_says_what_it_is():
    for fmt in ("image", "pdf"):
        card = _card({}, {"url": "https://x.example/f", "status_code": 200, "format": fmt, "text": "[…]"})
        assert card["mode"] == fmt and "excerpt" not in card


def test_the_tool_module_still_offers_the_presenter():
    assert fetch_mod._fetch_present_result is _fetch_present_result
