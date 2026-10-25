"""What `web_fetch` says about a result: to the model (`render`), to a person (`present_*`)
and to the session log (`facts`).

Split from `fetch.py` (size ceiling): that file gets a page, this one describes what came
back. Pure functions of the result dict. Three shapes: the page from a
position, the places a phrase occurs, and an answer with its checked quotes.
"""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlsplit

from harness.tools import cards
from harness.tools.contract import FULL_CHARS, LOCATOR, NETWORK_CAPS, SHOWN_CHARS, ToolContract, ToolPresentation, clip
from .page_preview import page_preview, text_preview
from .providers import EXTERNAL_CONTENT_NOTICE
from .snippets import clean_snippet

#: What a card carries of an answer's quotes and a phrase's matches: enough to read, bounded.
_CARD_QUOTES, _CARD_MATCHES, _CARD_QUOTE_CHARS, _CARD_MATCH_CHARS = 8, 12, 300, 520


def _age(seconds: Any) -> str:
    seconds = int(seconds or 0)
    return f"{seconds}s" if seconds < 90 else f"{seconds // 60} min"


def _excerpt_kind(fmt: str, content_type: str) -> str:
    """How the card shows the text: Markdown the extractor wrote (or a Markdown file), the
    text as it is, or code (JSON, XML, a page's raw HTML)."""
    mime = content_type.split(";")[0].strip().lower()
    if fmt == "raw" or mime.endswith(("json", "xml")):
        return "code"
    if mime in ("text/markdown", "text/x-markdown") or (
        fmt in ("", "markdown", "full") and mime in ("text/html", "application/xhtml+xml")
    ):
        return "markdown"
    return "text"


def _card_about(value: dict[str, Any]) -> dict[str, Any]:
    """How the text was read, for the card's foot: each key only when it is true."""
    about: dict[str, Any] = {}
    if value.get("scope") in ("main", "clean") and value.get("boilerplate_chars"):
        about["left_out_chars"] = int(value["boilerplate_chars"])
    if value.get("cached_seconds") is not None:
        about["cached_seconds"] = int(value.get("cached_seconds") or 0)
    if value.get("https") == "plain-http":
        about["plain_http"] = True
    if value.get("bytes_truncated"):
        about["source_cut"] = True
    if value.get("js_required"):
        about["js_required"] = True
    if value.get("prompt_unanswered"):
        about["unanswered"] = clip(value["prompt_unanswered"], 200)
    return about


def _fetch_card(arguments: dict[str, Any], value: dict[str, Any], url: str) -> dict[str, Any]:
    """The card: which page, how it answered, and what the call got from it."""
    fmt = str(value.get("format") or "")
    text = str(value.get("text") or "")
    total = int(value.get("page_chars") or 0)
    status = value.get("status_code")
    common: dict[str, Any] = {
        "title": clip(value.get("title"), 300) or None,
        "status_code": int(status) if isinstance(status, int) else None,
        "about": _card_about(value),
    }
    if fmt == "redirect":
        target = str(value.get("redirect_to") or "")
        host = urlsplit(target).hostname or ""
        web = target if target.lower().startswith(("http://", "https://")) else ""
        return cards.web_fetch(
            url, mode="redirect", redirect={k: v for k, v in (("host", host), ("url", web)) if v}, **common
        )
    if fmt in ("image", "pdf"):
        return cards.web_fetch(url, mode=fmt, **common)
    if fmt == "answer":
        quotes = [q for q in value.get("evidence") or [] if isinstance(q, dict)]
        cut = value.get("page_cut_to")
        answer = {
            "question": clip(arguments.get("prompt"), 400),
            "text": page_preview(text, limit=6000, links=True),
            "by": str(value.get("answered_by") or ""),
            "quotes": [
                {"quote": clip(q.get("quote"), _CARD_QUOTE_CHARS), "found": q.get("at") is not None}
                for q in quotes[:_CARD_QUOTES]
            ],
            "quotes_total": len(quotes),
            "quotes_found": sum(1 for q in quotes if q.get("at") is not None),
            "parts": int(value.get("page_parts") or 0),
            "unread_chars": total - int(cut) if isinstance(cut, int) else 0,
        }
        return cards.web_fetch(
            url, mode="answer", answer=answer, read={"page_chars": total}, **common
        )
    if fmt == "find":
        matches = [m for m in value.get("matches") or [] if isinstance(m, dict)]
        found = {
            "phrase": str(value.get("find") or ""),
            "count": int(value.get("match_count") or 0),
            "matches": [
                {"at": int(m.get("at") or 0), "text": clean_snippet(str(m.get("text") or ""), limit=_CARD_MATCH_CHARS)}
                for m in matches[:_CARD_MATCHES]
            ],
        }
        return cards.web_fetch(url, mode="find", found=found, read={"page_chars": total}, **common)
    kind = _excerpt_kind(fmt, str(value.get("content_type") or ""))
    title = str(common["title"] or "")
    mime = str(value.get("content_type") or "").split(";")[0].strip().lower()
    excerpt = (
        page_preview(text, title=title)
        if kind == "markdown"
        else text_preview(text, json=mime.endswith("json"))
    )
    start, end = int(value.get("start") or 0), int(value.get("end") or len(text))
    return cards.web_fetch(
        url,
        mode="page",
        truncated=bool(value.get("truncated")),
        excerpt=excerpt or None,
        excerpt_kind=kind,
        chars=len(text),
        read={"start": start, "end": end, "page_chars": total or len(text)},
        **common,
    )


def _fetch_present_call(arguments: dict[str, Any]) -> ToolPresentation:
    url = clip(arguments.get("url"), 200)
    summary = f"Open {url}" if url else "Open a web page"
    question = arguments.get("prompt")
    phrase = arguments.get("find")
    if isinstance(phrase, str) and phrase.strip():
        detail = f"find: {clip(phrase, 160)}"
    elif isinstance(question, str) and question.strip():
        detail = clip(question, 200)
    else:
        detail = None
    return ToolPresentation(
        summary=summary,
        detail=detail,
        kind="fetch",
        card=cards.generic_call(summary, kind="fetch", raw_input=url or None),
    )


def _fetch_present_result(
    arguments: dict[str, Any], value: Any, card_meta: Any = None
) -> ToolPresentation:
    if not isinstance(value, dict):
        return ToolPresentation(summary="Fetched a web page", kind="fetch")
    error = str(value.get("error") or "").strip()
    if error:
        return ToolPresentation(summary=clip(error), kind="fetch")
    # `url` is the address actually reached after the guard walked the redirect chain, so
    # the card names where the content came from rather than where we asked to go.
    url = str(value.get("url") or arguments.get("url") or "").strip()
    link = value.get("full_output") or None
    truncated = bool(value.get("truncated"))
    text = str(value.get("text") or "")
    fmt = str(value.get("format") or "")
    total = int(value.get("page_chars") or 0)
    if fmt == "redirect":
        target = urlsplit(str(value.get("redirect_to") or "")).hostname or "another site"
        return ToolPresentation(
            summary=f"Redirected to {target}",
            detail="not followed",
            kind="fetch",
            link=link,
            card=_fetch_card(arguments, value, url) if url else None,
        )
    if fmt == "image":
        detail = "image attached"
    elif fmt == "pdf":
        detail = "PDF attached"
    elif fmt == "answer":
        detail = f"answer from {total:,} characters of the page"
        quotes = value.get("evidence") or []
        if quotes:
            found = sum(1 for quote in quotes if isinstance(quote, dict) and quote.get("at") is not None)
            detail += f" · {found} of {len(quotes)} quotes found in it"
    elif fmt == "find":
        count = int(value.get("match_count") or 0)
        detail = f"{count:,} match{'' if count == 1 else 'es'} in {total:,} characters"
    else:
        detail = f"{len(text):,} characters"
        if truncated:
            detail = f"characters {int(value.get('start') or 0):,}–{int(value.get('end') or 0):,} of {total:,}"
        if value.get("boilerplate_chars"):
            detail += " · main content"
        if value.get("bytes_truncated"):
            detail += " · source cut at 5 MB"
    if not url:
        return ToolPresentation(summary="Fetched a web page", detail=detail, kind="fetch", link=link)
    return ToolPresentation(
        summary=f"Read {clip(url, 200)}",
        detail=detail,
        kind="fetch",
        link=link,
        # The status the response carried (a non-2xx is a result now, not a raise), and what
        # the call got: the start of the page, an answer with its quotes, or a phrase's places.
        card=_fetch_card(arguments, value, url),
    )


def _matches_text(value: dict[str, Any]) -> str:
    """The places a phrase occurs: each with its position, which is what `start` takes."""
    phrase = str(value.get("find") or "")
    total = int(value.get("page_chars") or 0)
    count = int(value.get("match_count") or 0)
    matches = [m for m in value.get("matches") or [] if isinstance(m, dict)]
    if not count:
        return (
            f'No match for "{phrase}" in the page ({total:,} characters). The search is plain '
            "text, case-insensitive: try a shorter or a different phrase."
        )
    head = f'{count:,} match{"" if count == 1 else "es"} for "{phrase}" in the page ({total:,} characters)'
    if count > len(matches):
        head += f", the first {len(matches)} shown"
    blocks = [f"at {int(m.get('at') or 0):,}:\n{m.get('text') or ''}" for m in matches]
    return head + ":\n\n" + "\n\n".join(blocks)


def _evidence_text(value: dict[str, Any]) -> str:
    quotes = [q for q in value.get("evidence") or [] if isinstance(q, dict)]
    if not quotes:
        return ""
    lines = []
    for quote in quotes:
        at = quote.get("at")
        where = (
            f"at {int(at):,}"
            if isinstance(at, int)
            else "NOT FOUND in the page: treat what it supports as unverified"
        )
        lines.append(f'- "{quote.get("quote") or ""}" ({where})')
    return "\n\nQuotes this answer rests on, checked against the page:\n" + "\n".join(lines)


def _web_fetch_render(arguments: dict[str, Any], value: Any) -> str:
    """What was asked for, then one source line.

    The JSON form escapes the whole page behind `{"url","content_type","text",…}`; the
    text is what the call was for. The trailing line keeps the source attribution
    (external content), the HTTP status, where this text sits in the page and the call
    that continues it, and the file that holds the whole page. The trust notice leads,
    because the model reads the page text next and a fetched page is data.
    """
    if not isinstance(value, dict):
        return json.dumps(value, default=str)
    error = str(value.get("error") or "").strip()
    if error:
        status = value.get("status_code")
        suffix = f" (HTTP {status})" if isinstance(status, int) else ""
        return f"Error: {error}{suffix}"
    fmt = value.get("format")
    text = str(value.get("text") or "")
    total = int(value.get("page_chars") or 0)
    notes = [str(value.get("url") or "")]
    status = value.get("status_code")
    if isinstance(status, int):
        notes.append(f"HTTP {status}")
    content_type = str(value.get("content_type") or "")
    if content_type:
        notes.append(content_type)
    body = text
    if fmt == "answer":
        cut = value.get("page_cut_to")
        quotes = [q for q in value.get("evidence") or [] if isinstance(q, dict)]
        found = sum(1 for quote in quotes if quote.get("at") is not None)
        notes.append(
            f"answered from the page by {value.get('answered_by')} ({total:,} characters"
            + (f", read in {int(value['page_parts'])} parts" if value.get("page_parts") else "")
            + (f", only {int(cut):,} of them read" if isinstance(cut, int) else "")
            + ")"
        )
        notes.append(
            f"quotes found in the page: {found} of {len(quotes)}"
            if quotes
            else "the answer gave no quotes to check"
        )
        notes.append('to read the page itself: start=<position>, or find="…"')
        body = text + _evidence_text(value)
    elif fmt == "find":
        body = _matches_text(value)
        if value.get("match_count"):
            notes.append("read around a match with start=<its position>")
    elif value.get("truncated"):
        start, end = int(value.get("start") or 0), int(value.get("end") or 0)
        notes.append(f"characters {start:,}–{end:,} of {total:,}")
        notes.append(f"next: start={end}" if end < total else "this is the end of the page")
        notes.append('find="…" searches the whole page')
    if value.get("cached_seconds") is not None:
        notes.append(
            f"this session's copy, fetched {_age(value.get('cached_seconds'))} ago "
            "(refresh=true downloads it again)"
        )
    if value.get("bytes_truncated"):
        notes.append("source cut at the 5 MB transport cap")
    if value.get("scope") in ("main", "clean") and value.get("boilerplate_chars"):
        notes.append(
            f"main content only — {int(value['boilerplate_chars']):,} characters of menus, footers "
            "and sidebars left out (format=full reads everything)"
        )
    if value.get("ua_retried"):
        notes.append("retried with a browser user-agent after a 403")
    if value.get("https") == "upgraded":
        notes.append("fetched over https")
    elif value.get("https") == "plain-http":
        notes.append("https could not connect, so this was fetched over plain http")
    if value.get("prompt_ignored"):
        notes.append(f"your prompt was not used: {value['prompt_ignored']}")
    if value.get("prompt_unanswered"):
        notes.append(f"your prompt could not be answered ({value['prompt_unanswered']}) — this is the page itself")
    full = value.get("full_output")
    if isinstance(full, str) and full:
        notes.append(f"full output: {full}")
    if value.get("js_required"):
        body += (
            "\n\n[This page builds its content with JavaScript and its HTML held almost no text. "
            "Use the browser tools (browser_open_url) to read it rendered.]"
        )
    return (
        f"{EXTERNAL_CONTENT_NOTICE}\n\n{body}\n\n"
        f"[{' · '.join(note for note in notes if note)}]"
    )


def _fetch_facts(arguments: dict[str, Any], value: Any) -> dict[str, Any]:
    """What the log records about a fetch: what came back and how it was read,
    whether it was this session's copy, who answered and from how much of the page, and
    whether the answer's quotes are in it."""
    if not isinstance(value, dict):
        return {}
    fmt = value.get("format")
    page, cut, text = value.get("page_chars"), value.get("page_cut_to"), value.get("text")
    facts: dict[str, Any] = {
        "status_code": value.get("status_code"),
        "format": fmt,
        FULL_CHARS: page if isinstance(page, int) else None,
        "cached": True if value.get("cached_seconds") is not None else None,
    }
    if fmt == "answer":
        # The saved page is NOT this result's complete output (that is the answer), so
        # it has its own name: a bound that had to cut an answer must not point at it.
        quotes = [q for q in value.get("evidence") or [] if isinstance(q, dict)]
        facts.update(
            reader=value.get("answered_by"),
            page_parts=value.get("page_parts"),
            page_unread=(page - cut) if isinstance(page, int) and isinstance(cut, int) else 0,
            page_file=value.get("full_output"),
            quotes=len(quotes),
            quotes_found=sum(1 for quote in quotes if quote.get("at") is not None),
        )
    elif fmt == "find":
        facts.update(matches=value.get("match_count"), page_file=value.get("full_output"))
    else:
        facts[LOCATOR] = value.get("full_output")
        if value.get("truncated") and isinstance(text, str):
            facts[SHOWN_CHARS] = len(text)
            facts["start"] = value.get("start")
    return facts


#: Above the httpx client's own maximum (the model may ask for 120 s, `_MAX_TIMEOUT`) plus the
#: answering call's own deadline (`ask.TIMEOUT_SECONDS`, 75 s) and the https → http retry that can
#: spend the request budget twice; the tool declares no client of its own, so nothing else would
#: bound a library-level stall.
WEB_FETCH_CONTRACT = ToolContract(
    capabilities=NETWORK_CAPS,
    render=_web_fetch_render,
    facts=_fetch_facts,
    present_call=_fetch_present_call,
    present_result=_fetch_present_result,
    timeout_ms=330_000,
)

__all__ = [
    "WEB_FETCH_CONTRACT",
    "_fetch_facts",
    "_fetch_present_call",
    "_fetch_present_result",
    "_web_fetch_render",
]
