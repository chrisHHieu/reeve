"""A fetched page is a document the session holds: fetched once, then read on by
position, searched, or answered with quotes that are checked against it.

Real session, 2026-10-05: an agent asked for one paper with `max_chars` 100,000 and then
200,000, got the same 48,000 characters and two identical files, called it a hard cap, and
noted in its memory not to trust an answered fetch. The page was returned as its two ends.
Scripted providers and fake responses only.
"""

from __future__ import annotations

import pytest

from harness.llm.providers.base import AssistantTurn, ModelCapabilities, ProviderClient
from harness.tools.contract import FULL_CHARS, LOCATOR, SHOWN_CHARS
from harness.tools.retention import BODY_MAX_CHARS, BODY_MAX_LINES, RESULT_CEILING_CHARS, RESULT_CEILING_LINES
from harness.tools.spill import SpillStore
from harness.integrations.web import ask as ask_mod
from harness.integrations.web import fetch as fetch_mod
from harness.integrations.web import pages
from harness.integrations.web.ask import PageAsker
from harness.integrations.web.pages import PageStore

SECTIONS = ["Abstract", "Introduction", "Method", "Experiments", "Limitations", "Conclusion"]
METHOD = "The context is mirrored to a file and the compaction trigger fires at 75% of the budget."


def paper() -> str:
    """A long page whose one load-bearing sentence is in the MIDDLE."""
    parts = []
    for name in SECTIONS:
        parts.append(f"# {name}\n\n")
        if name == "Method":
            parts.append(METHOD + "\n\n")
        parts.append((f"Filler sentence of the {name.lower()} section, with nothing to find. " * 3 + "\n\n") * 90)
    return "".join(parts)


class _Resp:
    def __init__(self, body: str, status: int = 200):
        self.status_code = status
        self.content = body.encode()
        self.headers = {"content-type": "text/plain; charset=utf-8"}
        self.encoding = "utf-8"
        self.extensions: dict = {}

    def iter_bytes(self):
        yield self.content

    def close(self) -> None:
        pass


class Site:
    """Counts downloads; serves whatever `body` is at the time."""

    def __init__(self, body: str, status: int = 200):
        self.body, self.status, self.downloads = body, status, 0

    def __call__(self, client, url, **kw):
        self.downloads += 1
        return _Resp(self.body, self.status)


def make(monkeypatch, tmp_path, site, *, asker=None, clock=None):
    monkeypatch.setattr(fetch_mod, "get_checked", site)
    store = PageStore(clock=clock) if clock is not None else None
    return fetch_mod.make_web_fetch_tool(SpillStore(tmp_path), asker=asker, pages=store)


def render(out) -> str:
    return fetch_mod.WEB_FETCH_CONTRACT.render({}, out)


def files(tmp_path):
    return sorted(p.name for p in tmp_path.rglob("*.log"))


URL = "https://papers.example.com/long"


# -- fetched once ---------------------------------------------------------------------------


def test_a_page_is_downloaded_once_however_often_it_is_read(monkeypatch, tmp_path):
    site = Site(paper())
    tool = make(monkeypatch, tmp_path, site)

    first = tool(URL)
    again = tool(URL, max_chars=200_000)
    on = tool(URL, start=first["end"])
    hits = tool(URL, find="compaction trigger")

    assert site.downloads == 1
    assert len(files(tmp_path)) == 1  # and the whole page is on disk once, not once per call
    assert "cached_seconds" not in first and again["cached_seconds"] == 0
    assert again["full_output"] == first["full_output"] == on["full_output"] == hits["full_output"]
    assert "this session's copy" in render(again) and "refresh=true" in render(again)
    assert fetch_mod.WEB_FETCH_CONTRACT.facts({}, again)["cached"] is True
    assert "cached" not in {k for k, v in fetch_mod.WEB_FETCH_CONTRACT.facts({}, first).items() if v is not None}


def test_a_copy_is_good_for_fifteen_minutes_and_refresh_downloads_at_once(monkeypatch, tmp_path):
    now = [1_000.0]
    site = Site("version one of the status page")
    tool = make(monkeypatch, tmp_path, site, clock=lambda: now[0])

    assert "version one" in tool(URL)["text"]
    site.body = "version two of the status page"
    now[0] += pages.PAGE_TTL_SECONDS - 1
    assert "version one" in tool(URL)["text"] and site.downloads == 1

    fresh = tool(URL, refresh=True)
    assert "version two" in fresh["text"] and site.downloads == 2 and "cached_seconds" not in fresh

    site.body = "version three of the status page"
    now[0] += pages.PAGE_TTL_SECONDS + 1
    assert "version three" in tool(URL)["text"] and site.downloads == 3


def test_an_error_page_is_a_result_and_is_not_kept(monkeypatch, tmp_path):
    site = Site("Service unavailable", status=503)
    tool = make(monkeypatch, tmp_path, site)
    assert tool(URL)["status_code"] == 503
    site.body, site.status = "back up", 200
    assert "back up" in tool(URL)["text"] and site.downloads == 2


def test_each_format_of_a_url_is_its_own_copy(monkeypatch, tmp_path):
    site = Site("plain body")
    tool = make(monkeypatch, tmp_path, site)
    tool(URL)
    tool(URL, format="raw")
    tool(URL + "#section-3")  # a fragment names a place in the page, not another page
    assert site.downloads == 2


# -- read on by position --------------------------------------------------------------------


def test_reading_on_from_each_next_start_gives_the_page_in_order_and_whole(monkeypatch, tmp_path):
    """The page used to come back as its head and its tail. For a paper the middle is the
    method: here it is in the second or third window, and nothing is skipped or repeated."""
    text = paper()
    tool = make(monkeypatch, tmp_path, Site(text))

    read, at, calls = "", 0, 0
    while True:
        out = tool(URL, start=at)
        assert out["start"] == at and out["page_chars"] == len(text)
        read += out["text"]
        calls += 1
        said = render(out)
        if out["end"] >= len(text):
            assert "this is the end of the page" in said
            break
        assert f"next: start={out['end']}" in said
        assert out["text"].endswith("\n")  # a window ends on a line, so the next starts on one
        at = out["end"]

    assert read == text and calls > 2
    assert METHOD in read


def test_a_larger_window_is_not_how_a_long_page_is_read(monkeypatch, tmp_path):
    text = paper()
    tool = make(monkeypatch, tmp_path, Site(text))
    out = tool(URL, max_chars=200_000)
    said = render(out)

    assert len(out["text"]) <= BODY_MAX_CHARS and out["truncated"] is True
    assert len(said) <= RESULT_CEILING_CHARS and said.count("\n") + 1 <= RESULT_CEILING_LINES
    # One statement of where this text sits in the page, and the call that continues it.
    assert f"characters 0–{out['end']:,} of {len(text):,}" in said and "omitted" not in said
    facts = fetch_mod.WEB_FETCH_CONTRACT.facts({}, out)
    assert facts[FULL_CHARS] == len(text) and facts[SHOWN_CHARS] == len(out["text"])
    assert facts[LOCATOR] == out["full_output"] and facts["start"] == 0


def test_a_page_that_fits_is_returned_whole_and_says_nothing_about_position(monkeypatch, tmp_path):
    tool = make(monkeypatch, tmp_path, Site("A short page.\n"))
    out = tool(URL)
    assert out["text"] == "A short page.\n" and out["truncated"] is False
    assert "next: start" not in render(out) and "full_output" not in out and files(tmp_path) == []


def test_a_start_past_the_end_says_how_long_the_page_is(monkeypatch, tmp_path):
    tool = make(monkeypatch, tmp_path, Site("A short page.\n"))
    out = tool(URL, start=5_000)
    assert "past the end of the page, which is 14 characters" in out["error"]


def test_a_window_of_short_lines_stays_inside_the_line_ceiling(monkeypatch, tmp_path):
    text = "".join(f"row {i}\n" for i in range(9_000))
    tool = make(monkeypatch, tmp_path, Site(text))
    out = tool(URL, max_chars=BODY_MAX_CHARS)
    assert out["text"].count("\n") <= BODY_MAX_LINES
    assert render(out).count("\n") + 1 <= RESULT_CEILING_LINES
    assert tool(URL, start=out["end"])["text"].startswith(f"row {out['text'].count(chr(10))}\n")


def test_an_argument_sent_as_a_string_is_the_number_it_holds(monkeypatch, tmp_path):
    tool = make(monkeypatch, tmp_path, Site(paper()))
    out = tool(URL, start="600", max_chars="1000")
    assert out["start"] == 600 and len(out["text"]) <= 1_000


# -- find ------------------------------------------------------------------------------------


def test_find_gives_every_place_with_a_position_that_start_takes(monkeypatch, tmp_path):
    text = paper()
    tool = make(monkeypatch, tmp_path, Site(text))

    out = tool(URL, find="compaction   TRIGGER")  # plain text: case and spacing do not matter
    (match,) = out["matches"]
    assert out["format"] == "find" and out["match_count"] == 1
    assert text[match["at"]:].lower().startswith("compaction trigger")
    assert METHOD in match["text"]
    said = render(out)
    assert f"at {match['at']:,}:" in said and "read around a match with start=<its position>" in said

    there = tool(URL, start=match["at"], max_chars=200)
    assert there["text"].lower().startswith("compaction trigger")


def test_find_counts_all_and_shows_the_first_few(monkeypatch, tmp_path):
    tool = make(monkeypatch, tmp_path, Site(paper()))
    out = tool(URL, find="nothing to find")
    assert out["match_count"] > pages.FIND_SHOWN == len(out["matches"])
    assert f"the first {pages.FIND_SHOWN} shown" in render(out)
    assert fetch_mod.WEB_FETCH_CONTRACT.facts({}, out)["matches"] == out["match_count"]


def test_a_phrase_that_is_not_there_says_so_and_how_the_search_works(monkeypatch, tmp_path):
    tool = make(monkeypatch, tmp_path, Site(paper()))
    out = tool(URL, find="quantum annealing")
    assert out["match_count"] == 0 and out["matches"] == []
    assert 'No match for "quantum annealing"' in render(out) and "case-insensitive" in render(out)


# -- an answer shows what it rests on --------------------------------------------------------


class Reader(ProviderClient):
    def __init__(self, reply: str):
        self.reply = reply
        self.calls: list[str] = []

    def complete(self, *, model, messages, tools=None, **settings):
        self.calls.append(messages[-1]["content"])
        self.system = messages[0]["content"]
        return AssistantTurn(text=self.reply, finish_reason="stop")

    def capabilities(self, model):
        return ModelCapabilities()


def asker(provider) -> PageAsker:
    made = PageAsker(settings=lambda: {})
    made.bind(provider, lambda: "session-model")
    return made


def test_an_answers_quotes_are_looked_up_in_the_page_and_given_their_place(monkeypatch, tmp_path):
    text = paper()
    reader = Reader(
        "The trigger is 75% of the budget.\n\nEVIDENCE:\n"
        '- "the compaction  trigger fires at 75% of the budget"\n'
        '- "the trigger is configurable per model"\n'
    )
    tool = make(monkeypatch, tmp_path, Site(text), asker=asker(reader))
    out = tool(URL, prompt="when does compaction fire?")

    assert out["text"] == "The trigger is 75% of the budget."  # the section is data, not prose
    real, invented = out["evidence"]
    assert text[real["at"]:].startswith("the compaction trigger fires")
    assert invented["at"] is None
    said = render(out)
    assert f'(at {real["at"]:,})' in said
    assert "NOT FOUND in the page: treat what it supports as unverified" in said
    assert "quotes found in the page: 1 of 2" in said
    facts = fetch_mod.WEB_FETCH_CONTRACT.facts({}, out)
    assert facts["quotes"] == 2 and facts["quotes_found"] == 1 and LOCATOR not in facts
    # The place a quote is at is a place to read from.
    assert tool(URL, start=real["at"], max_chars=120)["text"].startswith("the compaction trigger")


def test_the_reader_is_told_to_end_with_quotes_copied_exactly(monkeypatch, tmp_path):
    reader = Reader("It does not say.\nEVIDENCE: none")
    tool = make(monkeypatch, tmp_path, Site("A short page.\n"), asker=asker(reader))
    out = tool(URL, prompt="what is the price?")
    assert "End with a line `EVIDENCE:`" in reader.system and "character for character" in reader.system
    assert out["text"] == "It does not say." and out["evidence"] == []
    assert "the answer gave no quotes to check" in render(out)


@pytest.mark.parametrize(
    "quote, found",
    [
        ("“mirrored to a file” — and", True),          # typographic quotes and dashes
        ('"mirrored to a file" - and', True),
        ("The context is ... the trigger fires at 75% of the budget", True),  # the reader's own ellipsis
        ("The context is mirrored to a database", False),
        ("75% of the budget ... The context is", False),  # both pieces are there, in the other order
    ],
)
def test_a_quote_is_found_through_what_copying_does_to_text(quote, found):
    page = "Intro.\n\nThe context is “mirrored to a file” — and the trigger fires at\n75% of the budget.\n"
    assert (pages.locate(page, quote) is not None) is found


def test_an_answer_without_the_section_is_returned_as_it_came():
    assert pages.split_evidence("Just an answer.") == ("Just an answer.", [])
    said, quotes = pages.split_evidence('Answer.\n**EVIDENCE:** "a passage long enough to check"')
    assert said == "Answer." and quotes == ["a passage long enough to check"]


def test_a_long_page_read_in_parts_carries_its_quotes_to_the_answer(monkeypatch, tmp_path):
    """Each part quotes what it read; the call that writes the answer has not seen the page,
    so it may only pass those quotes on, and they are checked like any other."""
    monkeypatch.setattr(ask_mod, "UNKNOWN_WINDOW_CHARS", 20_000)
    text = paper()

    class Parts(ProviderClient):
        def __init__(self):
            self.combine = ""

        def complete(self, *, model, messages, tools=None, **settings):
            user = messages[-1]["content"]
            if "<page-part>" in user:
                if METHOD in user:
                    return AssistantTurn(text=f'It mirrors the context.\nEVIDENCE:\n"{METHOD}"', finish_reason="stop")
                return AssistantTurn(text=ask_mod.NOTHING_HERE, finish_reason="stop")
            self.combine = user
            return AssistantTurn(text=f'FINAL: mirrored to a file.\nEVIDENCE:\n"{METHOD}"', finish_reason="stop")

        def capabilities(self, model):
            return ModelCapabilities()

    provider = Parts()
    out = make(monkeypatch, tmp_path, Site(text), asker=asker(provider))(URL, prompt="how does it work?")

    assert out["page_parts"] > 1 and out["text"] == "FINAL: mirrored to a file."
    assert "copied unchanged" in provider.combine and METHOD in provider.combine
    (quote,) = out["evidence"]
    assert text[quote["at"]:].startswith(METHOD)
    assert f"read in {out['page_parts']} parts" in render(out)


def test_the_schema_says_how_a_long_page_is_read():
    function = fetch_mod._SCHEMA["function"]
    properties = function["parameters"]["properties"]
    assert {"start", "find", "refresh", "prompt", "max_chars"} <= set(properties)
    assert f"max {BODY_MAX_CHARS}" in properties["max_chars"]["description"]
    assert "read on" in function["description"] and "larger" in function["description"]
