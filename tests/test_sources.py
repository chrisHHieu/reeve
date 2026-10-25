"""A source an answer cites is one it read, or the answer says so.

Invented searches and pages throughout: no real result is a fixture."""

from __future__ import annotations

import json

from harness.loop.sources import SourceLedger, source_key, unread_sources
from harness.session.turns import ATTACHED_BY


def _call(call_id, name, **arguments):
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {"id": call_id, "type": "function", "function": {"name": name, "arguments": json.dumps(arguments)}}
        ],
    }


def _result(call_id, text):
    return {"role": "tool", "tool_call_id": call_id, "content": text}


def _searched(*urls, call_id="s1"):
    body = "\n".join(f"{i}. A title\n   {url}\n   a snippet" for i, url in enumerate(urls, 1))
    return [_call(call_id, "web_search", query="anything"), _result(call_id, body)]


def test_a_source_is_one_source_whichever_way_it_was_linked():
    assert source_key("https://arxiv.org/abs/2601.01234") == source_key("https://arxiv.org/pdf/2601.01234v2")
    assert source_key("https://arxiv.org/html/2601.01234v1") == "arxiv:2601.01234"
    assert source_key("https://www.Example.org/a/b/") == source_key("http://example.org/a/b#part")
    # The path is not case-folded: two pages that differ by case are two pages.
    assert source_key("https://example.org/A") != source_key("https://example.org/a")


def test_a_searched_source_never_opened_is_unread():
    messages = _searched("https://example.org/one", "https://example.org/two")
    answer = "Per [one](https://example.org/one), it holds.\n\nSources:\n- https://example.org/two."
    assert unread_sources(messages, answer) == ["https://example.org/one", "https://example.org/two"]


def test_a_fetched_source_is_read_in_any_of_its_forms():
    messages = _searched("https://arxiv.org/abs/2601.01234", "https://example.org/two")
    messages += [_call("f1", "web_fetch", url="https://arxiv.org/html/2601.01234v3", prompt="what does it say")]
    messages += [_result("f1", "the page")]
    answer = "See https://arxiv.org/abs/2601.01234 and https://example.org/two"
    assert unread_sources(messages, answer) == ["https://example.org/two"]


def test_any_other_call_naming_the_url_opened_it():
    messages = _searched("https://example.org/one")
    messages += [_call("c1", "run_shell", command="curl -s https://example.org/one | head")]
    assert unread_sources(messages, "From https://example.org/one") == []


def test_the_person_naming_a_source_or_a_report_naming_it_opens_it():
    person = _searched("https://example.org/one", "https://example.org/two") + [
        {"role": "user", "content": "read https://example.org/one please"}
    ]
    assert unread_sources(person, "https://example.org/one") == []
    report = _searched("https://example.org/two") + [
        _call("t1", "task", prompt="read it"),
        _result("t1", "<task>read https://example.org/two: it says X</task>"),
    ]
    assert unread_sources(report, "https://example.org/two") == []


def test_the_gates_own_note_opens_nothing():
    messages = _searched("https://example.org/one") + [
        {"role": "user", "content": "System note: ... https://example.org/one", ATTACHED_BY: "source_nudge"}
    ]
    assert unread_sources(messages, "https://example.org/one") == ["https://example.org/one"]


def test_a_url_no_search_returned_is_not_a_searched_source():
    """The person's own link, or one the model knew: not a snippet, not this gate's business."""
    messages = _searched("https://example.org/one")
    assert unread_sources(messages, "See https://docs.example.com/guide") == []
    assert unread_sources(messages, "no links at all") == []
    assert unread_sources(messages, None) == []


def test_a_link_in_a_fetched_page_is_not_opened_by_that_fetch():
    messages = _searched("https://example.org/one")
    messages += [_call("f1", "web_fetch", url="https://example.org/index")]
    messages += [_result("f1", "a page linking https://example.org/one")]
    assert unread_sources(messages, "https://example.org/one") == ["https://example.org/one"]


def test_each_source_is_named_once_as_the_answer_wrote_it():
    ledger = SourceLedger()
    for message in _searched("https://example.org/one"):
        ledger.add(message)
    answer = "[a](https://www.example.org/one/) then again https://example.org/one."
    assert ledger.unread(answer) == ["https://www.example.org/one/"]


# -- through a turn --------------------------------------------------------


def _engine(tmp_path, turns, ablated=frozenset()):
    import aisuite as ai  # noqa: F401  (the registry's tool shape)

    from harness.llm.providers import ModelCapabilities, ProviderClient
    from harness.loop import ApprovalOutcome, TurnEngine
    from harness.safety.permissions import PermissionEngine
    from harness.session.session_log import SessionLog
    from harness.tools import ToolRegistry

    class Scripted(ProviderClient):
        def __init__(self):
            self.turns, self.calls = list(turns), 0

        def complete(self, *, model, messages, tools=None, **settings):
            self.calls += 1
            return self.turns.pop(0)

        def capabilities(self, model):
            return ModelCapabilities()

    def web_search(query: str = "") -> str:
        """Search the web."""
        return "1. A paper\n   https://example.org/paper\n   a snippet\n2. A post\n   https://example.org/post\n   another"

    def web_fetch(url: str = "", prompt: str = "") -> str:
        """Read a page."""
        return "the page says what it says"

    async def approve(_request):
        return ApprovalOutcome.ONCE

    registry = ToolRegistry()
    registry.register_all([web_search, web_fetch])
    provider = Scripted()
    engine = TurnEngine(
        provider=provider,
        registry=registry,
        permissions=PermissionEngine(workspace_root=tmp_path),
        model="gpt-5.5",
        approver=approve,
    )
    engine.log = SessionLog(tmp_path / "session.jsonl")
    engine.ablated = ablated
    return engine, provider


def _call_turn(name, arguments, call_id):
    from harness.llm.providers import AssistantTurn, ToolCall

    return AssistantTurn(tool_calls=[ToolCall(id=call_id, name=name, arguments=arguments)], finish_reason="tool_calls")


def _text_turn(text):
    from harness.llm.providers import AssistantTurn

    return AssistantTurn(text=text, finish_reason="stop")


def _run(engine, text="find me a paper"):
    import asyncio

    async def go():
        return [event async for event in engine.run(text)]

    return asyncio.run(go())


def _notes(engine):
    return [m for m in engine.messages if m.get(ATTACHED_BY) == "source_nudge"]


def _nudges(engine):
    return [e for e in engine.log.read() if e["type"] == "verify/nudge" and e.get("reason") == "unread_sources"]


def test_an_answer_resting_on_a_snippet_ends_its_turn_with_one_answer(tmp_path):
    """The source gate is gone (amended 2026-10-10): it spoke after the answer was on
    screen, so the person read two answers and the second was a list of corrections. The turn
    now ends on the first answer; what it cites unread is counted by the review, not sent back."""
    engine, provider = _engine(
        tmp_path,
        [
            _call_turn("web_search", {"query": "papers"}, "s1"),
            _text_turn("It cuts cost in half (https://example.org/paper)."),
        ],
    )
    _run(engine)
    assert provider.calls == 2 and not _notes(engine) and not _nudges(engine)
    assert engine.log.read()[-1]["type"] == "turn/end"


def test_an_answer_whose_source_was_opened_finishes_at_once(tmp_path):
    engine, provider = _engine(
        tmp_path,
        [
            _call_turn("web_search", {"query": "papers"}, "s1"),
            _call_turn("web_fetch", {"url": "https://example.org/paper/", "prompt": "what"}, "f1"),
            _text_turn("It cuts cost in half (https://example.org/paper)."),
        ],
    )
    _run(engine)
    assert provider.calls == 3 and not _notes(engine) and not _nudges(engine)


def test_the_review_counts_answers_that_ended_on_an_unread_source(tmp_path):
    """Read from the transcript beside the log; an answer a gate sent around again did not end
    its turn, and the one after the note is counted as such."""
    from scripts import harness_review

    (tmp_path / "a.events.jsonl").write_text(json.dumps({"type": "turn/start", "turn": 1, "t": 1}) + "\n", encoding="utf-8")
    rows = [
        {"role": "user", "content": "find"},
        *_searched("https://example.org/one", "https://example.org/two"),
        {"role": "assistant", "content": "per https://example.org/one"},
        {"role": "user", "content": "System note: https://example.org/one", ATTACHED_BY: "source_nudge"},
        {"role": "assistant", "content": "seen only in search: https://example.org/one"},
        {"role": "user", "content": "and the other?"},
        _call("f1", "web_fetch", url="https://example.org/two"),
        _result("f1", "page"),
        {"role": "assistant", "content": "https://example.org/two says so"},
        {"role": "user", "content": "thanks"},
        {"role": "assistant", "content": "no sources here"},
    ]
    (tmp_path / "a.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    counts, refs = harness_review.review([tmp_path / "a.events.jsonl"])
    assert counts["searched-citing answers"] == 2
    assert counts["unread-citations"] == 1 and counts["unread-citations after the note"] == 1
    assert refs["unread-citations"] == [("a.events.jsonl", None, "after the note")]


def test_a_search_that_names_a_url_does_not_open_it():
    """A query or a domain filter naming the page is still a search: it returned a snippet."""
    messages = [
        _call("s1", "web_search", query="https://example.org/one", allowed_domains=["https://example.org/one"]),
        _result("s1", "1. One\n   https://example.org/one\n   a snippet"),
    ]
    assert unread_sources(messages, "https://example.org/one") == ["https://example.org/one"]


# A search whose engine read the page gives the model the page (2026-10-19): Exa's public MCP
# returned 2,000-5,200 characters of each page and the result kept 900. Run from the engine's
# raw reply through the result the model reads to the ledger, so the line the count rests on is
# the one the tool writes.


def _engine_reply(*pages):
    """Exa MCP's text for invented results: `(url, highlights)` each."""
    return "\n---\n".join(f"Title: Page {i}\nURL: {url}\nHighlights:\n{text}" for i, (url, text) in enumerate(pages, 1))


def _search(*pages):
    from harness.integrations.web.providers import parse_exa_mcp_text
    from harness.integrations.web.search_present import _web_search_render

    results = parse_exa_mcp_text(_engine_reply(*pages), max_results=len(pages))
    text = _web_search_render({"query": "anything"}, {"provider": "exa", "results": [r.to_dict() for r in results]})
    return [_call("s1", "web_search", query="anything"), _result("s1", text)], text


def test_a_result_given_with_its_page_is_read_and_a_snippet_is_not():
    long_page = "The release adds a free-threaded build. " * 40
    messages, text = _search(
        ("https://example.org/read", long_page + "\n1. a numbered line inside the page https://example.org/inside"),
        ("https://example.org/brief", "Only a line about it."),
    )
    assert "Read from the page:" in text and "Search snippet, page not read: Only a line" in text
    answer = "See https://example.org/read, https://example.org/brief and https://example.org/inside"
    # A link inside a read page is not read by it, and a numbered line in a page starts no result.
    assert unread_sources(messages, answer) == ["https://example.org/brief", "https://example.org/inside"]


def test_past_the_budget_a_result_is_given_as_its_snippet_and_stays_unread():
    from harness.integrations.web.search_present import PAGES_BUDGET

    page = "A long passage about the subject that keeps going. " * 60
    count = PAGES_BUDGET // len(page[:3000]) + 2
    pages = [(f"https://example.org/p{i}", page) for i in range(count)]
    messages, text = _search(*pages)
    answer = " ".join(url for url, _ in pages)
    unread = unread_sources(messages, answer)
    assert unread and len(unread) < count
    assert len(text) < PAGES_BUDGET + 3000

