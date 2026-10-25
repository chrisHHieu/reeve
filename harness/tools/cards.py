"""Tool cards — the STRUCTURED half of a presentation.

`ToolPresentation` answers "what happened" in one sentence. That is enough for an
approval prompt and for a receipt line, and it is not enough for the row a person watches
while the agent works: `+412 −0` is a sentence, but "here is the file, here are the lines
that changed" is a card. This module is that second vocabulary.

The design is worth restating because every choice here follows from it:

- **Two card families, one per question.** A CALL card describes what is about to
  happen (the terminal command and its working directory, the diff a write proposes); a
  RESULT card describes what happened (the output, the exit status, the lines that
  landed). They are separate unions because a call can say `terminal` while its result
  says `generic` — a background run has a command but no exit status.
- **Generic is the fallback, never an error.** A surface that does not recognise a card
  renders the plain row. That is why unknown `card` values are safe: a newer engine can
  ship a new kind to an older GUI and the worst case is a plainer row, not a crash.
- **Omission is the only way to say "no card".** There is no `card: None` shape; a
  presenter that has nothing to add simply does not set the field.

Wire shape is snake_case, matching the `_display` sidecar the GUI already reads
unmapped (`surfaces/gui/src/itemsFromMessages.ts`). Cards are NOT re-validated on the way
back in: nothing in Python reads a stored card, and the GUI treats every unknown shape as
the generic row by construction.

Deliberately NOT here:
- **How a card is drawn.** Radius, caps, fonts, whether a diff is unified or side-by-side
  — all of that is the surface's business, and none of it is the engine's.
- **The one-line summary.** That stays `ToolPresentation.summary`; a card ADDS to the
  sentence, it never replaces it. A card with no summary is a bug, not a valid state.
"""

from __future__ import annotations

from typing import Any, Iterable, Optional

from .card_rules import CALL_KINDS, FOLD_COUNTS, RESULT_KINDS, require as _require, slim, validate

#: Presentational category for a `generic` card, mirroring `ToolPresentation.kind`. Named
#: separately because it is the same vocabulary for the same reason: it says how a call
#: LOOKS, never how risky it is.
CardKind = str


def _one_line(text: Any) -> str:
    return " ".join(str(text or "").split())


def file_diff(
    path: str,
    old_text: Optional[str],
    new_text: str,
    *,
    added: Optional[int] = None,
    removed: Optional[int] = None,
    old_start: Optional[int] = None,
    new_start: Optional[int] = None,
) -> dict[str, Any]:
    """One file's change.

    `old_text` is None for a creation AND for an overwrite whose prior content was not
    available (past the read cap, or read before this codebase started keeping it). The
    two are deliberately the same value: a card may only claim a removal it actually saw.

    `added`/`removed` are exact line counts where the caller knows them. They exist because
    the two sides are CONTEXT-WINDOWED: a hunk carries three unchanged lines either side of
    an edit, those lines appear on both sides, and a surface counting each side separately
    would report a one-line change as `+7 −7`. The question is avoided by giving the hunks
    no counts at all and having the block count sides; that is right for its hunks, which
    come from a patch the tool authored, and wrong here, where the same numbers also appear
    on the turn's receipt and the two must agree.

    Omit them when the sides are literal fragments rather than a context window — a
    replacement's `old`/`new` arguments, a write's content — because then counting each
    side IS the true count.
    """
    _require(bool(str(path or "").strip()), "a diff hunk needs a path")
    _require(isinstance(new_text, str), "a diff hunk needs new_text as a string")
    _require(
        old_text is None or isinstance(old_text, str),
        "a diff hunk's old_text must be a string or None",
    )
    out: dict[str, Any] = {"path": str(path), "old_text": old_text, "new_text": new_text}
    if added is not None:
        out["added"] = int(added)
    if removed is not None:
        out["removed"] = int(removed)
    # Where a context window starts in its file (1-based). Without it a surface cannot
    # number the file's real lines, and should not number them at all.
    if old_start is not None:
        out["old_start"] = int(old_start)
    if new_start is not None:
        out["new_start"] = int(new_start)
    return out


def location(path: str, line: Optional[int] = None) -> dict[str, Any]:
    """A file a call touches, for a surface that offers to open it."""
    _require(bool(str(path or "").strip()), "a location needs a path")
    out: dict[str, Any] = {"path": str(path)}
    if line is not None:
        out["line"] = int(line)
    return out


# -- call cards -----------------------------------------------------------------


def generic_call(
    title: str,
    *,
    kind: CardKind = "other",
    raw_input: Any = None,
    content: Optional[Iterable[str]] = None,
    locations: Optional[Iterable[dict[str, Any]]] = None,
) -> dict[str, Any]:
    """The default call card: a titled row, an optional salient input, file locations.

    `raw_input` is the one argument worth showing on the row before the call runs — a
    command, a pattern, a path. Not the whole argument blob: a card that dumps JSON is a
    card nobody reads.
    """
    _require(bool(str(title or "").strip()), "a generic call card needs a title")
    out: dict[str, Any] = {"card": "generic", "title": str(title), "kind": kind}
    if raw_input is not None:
        out["raw_input"] = raw_input
    if content is not None:
        out["content"] = [str(part) for part in content]
    if locations is not None:
        out["locations"] = list(locations)
    return out


def terminal_call(
    title: str, *, description: str = "", cwd: Optional[str] = None
) -> dict[str, Any]:
    """A call that IS a shell command running in a working directory.

    `cwd` is passed through verbatim: an absolute path is used as-is, a relative one is
    the surface's to resolve against the session workspace. Omitted means "the session's
    workspace" — the surface knows it and this card does not.
    """
    _require(bool(str(title or "").strip()), "a terminal call card needs a command title")
    out: dict[str, Any] = {"card": "terminal", "title": str(title)}
    if description:
        out["description"] = str(description)
    if cwd:
        out["cwd"] = str(cwd)
    return out


def diff_call(
    title: str,
    diffs: Iterable[dict[str, Any]],
    *,
    locations: Optional[Iterable[dict[str, Any]]] = None,
) -> dict[str, Any]:
    """A call card for a mutation, derived from the ARGUMENTS.

    This is what a write proposes, shown before it runs — which is the whole point of a
    call card on a consent surface. For a `write` the prior content is not knowable from
    the arguments, so `old_text` is None and the card shows an all-added change; the
    result card, when the engine can supply the before-image, shows the real hunks.
    """
    _require(bool(str(title or "").strip()), "a diff call card needs a title")
    hunks = list(diffs)
    _require(bool(hunks), "a diff card needs at least one hunk")
    out: dict[str, Any] = {"card": "diff", "title": str(title), "diffs": hunks}
    if locations is not None:
        out["locations"] = list(locations)
    return out


# -- result cards ---------------------------------------------------------------


def generic_result(
    *, title: Optional[str] = None, content: Optional[Iterable[str]] = None
) -> dict[str, Any]:
    """The default result card: an optional replacement title, optional text.

    A title replaces the pending one only when the call's title no longer describes what
    happened — a rename, a resolved target. Omitting it keeps the call's title, which is
    usually right.
    """
    out: dict[str, Any] = {"card": "generic"}
    if title:
        out["title"] = str(title)
    if content is not None:
        out["content"] = [str(part) for part in content]
    return out


def terminal_result(
    *,
    title: Optional[str] = None,
    output: Optional[str] = None,
    exit_code: Optional[int] = None,
    signal: Optional[str] = None,
    cwd: Optional[str] = None,
    truncated: bool = False,
    omitted_lines: int = 0,
    shell: Optional[str] = None,
) -> dict[str, Any]:
    """What a shell command did.

    `exit_code` and `signal` are the two ways a command stops, and both are carried
    separately from `output` rather than as a trailing marker line inside it: the card
    draws them as a status pill, so they must not be text a surface has to parse back out.

    A surface is expected to treat a non-zero `exit_code` as a failed row even though the
    CALL itself succeeded — a command that ran and reported failure is a failure to a
    reader, and it is drawn in red for exactly that reason.

    `cwd` and `title` are here, not only on the call card, because of a
    difference in what each engine can see. A shell tool may know its working directory
    from its own arguments, so it can put it on the pending card and let the completed one
    inherit it. This engine's executor owns the cwd and only reports it in the RESULT —
    and the result is the only half that survives a reload, so a card that did not carry
    them would come back as a nameless row with no directory.
    """
    out: dict[str, Any] = {"card": "terminal"}
    if title:
        out["title"] = str(title)
    if output is not None:
        out["output"] = str(output)
    if exit_code is not None:
        out["exit_code"] = int(exit_code)
    if signal:
        out["signal"] = str(signal)
    if cwd:
        out["cwd"] = str(cwd)
    if truncated:
        out["truncated"] = True
    if omitted_lines > 0:
        # How MUCH was cut, so a surface can say it instead of a bare "truncated": the
        # count is computed where the output is retained (tools/retention.py) and has no
        # other way to reach the card.
        out["omitted_lines"] = int(omitted_lines)
    if shell:
        # The shell it ran in (`powershell`, `pwsh`, `bash`, `cmd`): the prompt and colours follow
        # it, and the session's default is a Setting the surface cannot see.
        out["shell"] = str(shell)
    return out


def diff_result(
    diffs: Iterable[dict[str, Any]], *, title: Optional[str] = None
) -> dict[str, Any]:
    """What a mutation did, from the before-image when one is available.

    A mutation returns this even when it merely repeats the call-time diff: a completed
    card REPLACES the pending one, and omitting it would let the raw result text displace
    the diff the person was watching.
    """
    hunks = list(diffs)
    _require(bool(hunks), "a diff result card needs at least one hunk")
    out: dict[str, Any] = {"card": "diff", "diffs": hunks}
    if title:
        out["title"] = str(title)
    return out


def search_matches(
    files: Iterable[dict[str, Any]],
    *,
    title: Optional[str] = None,
    truncated: bool = False,
    total: Optional[int] = None,
) -> dict[str, Any]:
    """Search hits, already grouped by file.

    `shape` is the discriminant, not `kind`, so it never collides with the `kind` an icon
    picks off a `generic` card.

    `truncated` and `total` exist so a surface can say "showing 100 of 412" and never
    present a capped group as the whole answer — the failure mode this guards against is
    a person concluding "there are 100 matches" from a list that was cut.
    """
    groups = list(files)
    _require(bool(groups), "a matches card needs at least one file group")
    out: dict[str, Any] = {"card": "search", "shape": "matches", "files": groups}
    if title:
        out["title"] = str(title)
    out["truncated"] = bool(truncated)
    out["total"] = int(total) if total is not None else sum(
        len(group.get("matches", [])) for group in groups
    )
    return out


def search_paths(
    paths: Iterable[str],
    *,
    title: Optional[str] = None,
    truncated: bool = False,
    total: Optional[int] = None,
) -> dict[str, Any]:
    """A file listing — the `glob` shape of a search result."""
    found = [str(path) for path in paths]
    _require(bool(found), "a paths card needs at least one path")
    out: dict[str, Any] = {"card": "search", "shape": "paths", "paths": found}
    if title:
        out["title"] = str(title)
    out["truncated"] = bool(truncated)
    out["total"] = int(total) if total is not None else len(found)
    return out


def read_result(
    path: str,
    lines: Iterable[dict[str, Any]],
    total_lines: int,
    *,
    offset: int = 1,
    lang: Optional[str] = None,
    title: Optional[str] = None,
) -> dict[str, Any]:
    """A file window, with the file's OWN line numbers.

    `lines` and `total_lines` cannot be reconstructed from the model-facing text alone —
    that text is one string with the numbers baked into it — so a read tool projects this
    through its card metadata and the presenter narrows it back. A surface that cannot
    draw a read card falls back to the plain text.

    `offset` is 1-based and `lines[].number` keeps the position in the FILE, not in the
    window: a card showing lines 200-215 must say 200, or the reader cannot find them.
    """
    _require(bool(str(path or "").strip()), "a read card needs a path")
    window = list(lines)
    _require(isinstance(total_lines, int) and total_lines >= 0, "a read card needs total_lines")
    last = 0
    for entry in window:
        number = int(entry.get("number", 0))
        _require(
            number > last and number <= max(total_lines, number),
            "read card line numbers must ascend and stay within total_lines",
        )
        last = number
    out: dict[str, Any] = {
        "card": "read",
        "path": str(path),
        "offset": int(offset),
        "lines": window,
        "total_lines": int(total_lines),
    }
    if lang:
        out["lang"] = str(lang)
    if title:
        out["title"] = str(title)
    return out


def web_search(
    sources: Iterable[dict[str, Any]],
    *,
    title: Optional[str] = None,
    answer: Optional[str] = None,
    truncated: bool = False,
    provider: Optional[str] = None,
    query: Optional[str] = None,
    fallback_from: Optional[str] = None,
    fallback_reason: Optional[str] = None,
    queries: Optional[list[str]] = None,
    filtered_out: Optional[int] = None,
    failed_queries: Optional[int] = None,
) -> dict[str, Any]:
    """Web results: an optional provider-written answer over a citation list.

    `kind` here is this union's own discriminant, NOT the `generic` card's `kind`. The two
    values happen to match the tools' pending categories, which is convenience, not a
    contract — a new arm is a union edit plus a consumer branch.
    """
    found = list(sources)
    _require(bool(found) or bool(answer), "a web search card needs sources or an answer")
    out: dict[str, Any] = {"card": "web", "kind": "search", "sources": found}
    if title:
        out["title"] = str(title)
    if answer:
        out["answer"] = str(answer)
    # The provider names the destination the sources came from, and the query lets the
    # surface highlight the terms the reader searched for. Both are display-only.
    if provider:
        out["provider"] = str(provider)
    if query:
        out["query"] = str(query)
    if fallback_from:
        out["fallback_from"] = str(fallback_from)
        if fallback_reason:
            out["fallback_reason"] = str(fallback_reason)[:300]
    if queries:
        out["queries"] = [str(q) for q in queries]
    if filtered_out:
        out["filtered_out"] = int(filtered_out)
    if failed_queries:
        out["failed_queries"] = int(failed_queries)
    out["truncated"] = bool(truncated)
    return out


def web_fetch(
    url: str,
    *,
    title: Optional[str] = None,
    status_code: Optional[int] = None,
    truncated: bool = False,
    excerpt: Optional[str] = None,
    chars: Optional[int] = None,
    mode: Optional[str] = None,
    excerpt_kind: Optional[str] = None,
    read: Optional[dict[str, int]] = None,
    about: Optional[dict[str, Any]] = None,
    answer: Optional[dict[str, Any]] = None,
    found: Optional[dict[str, Any]] = None,
    redirect: Optional[dict[str, str]] = None,
) -> dict[str, Any]:
    """One fetched page.

    `status_code` is optional only because a transport failure carries none; a response
    that arrived reports its status — a non-2xx is a result now, not a raise. `excerpt` is
    a bounded preview of the extracted text (the surface shows it in a scrollable box) and
    `chars` the length of the text the model got, so the card can say how much there was.

    What the call was, since a fetch reads a page three ways: `mode` is `page`,
    `answer` (a model read the page: `answer` holds the question, the answer and each quote
    with whether it is in the page), `find` (the places a phrase occurs: `found`), or what
    came back instead of text (`redirect`, `image`, `pdf`). `excerpt_kind` says how to show
    the excerpt: `markdown` (the page's structure, cleaned by `web/page_preview.py`), `text`
    or `code`. `read` is where the text sits in the page (`start`, `end`, `page_chars`) and
    `about` how it was read (main content only, this session's copy, plain http, …).
    """
    _require(bool(str(url or "").strip()), "a web fetch card needs a url")
    out: dict[str, Any] = {"card": "web", "kind": "fetch", "url": str(url)}
    if title:
        out["title"] = str(title)
    if status_code is not None:
        out["status_code"] = int(status_code)
    if truncated:
        out["truncated"] = True
    if excerpt:
        out["excerpt"] = str(excerpt)
    if chars is not None:
        out["chars"] = int(chars)
    if mode:
        out["mode"] = str(mode)
    if excerpt and excerpt_kind:
        out["excerpt_kind"] = str(excerpt_kind)
    if read:
        out["read"] = {key: int(value) for key, value in read.items()}
    if about:
        out["about"] = dict(about)
    for key, value in (("answer", answer), ("found", found), ("redirect", redirect)):
        if value:
            out[key] = dict(value)
    return out


def skill(
    name: str,
    *,
    description: Optional[str] = None,
    instructions: Optional[str] = None,
) -> dict[str, Any]:
    """One loaded skill, so the transcript can show its body instead of a JSON dump.

    `load_skill`'s result already carries the instructions to the MODEL; without a card the
    person watching saw an IN/OUT pair and a ~300-char escaped preview. `instructions` is the
    skill body, bounded before it reaches here (a skill that ships 200 KB of prose is a skill
    the transcript should not embed whole).
    """
    _require(bool(str(name or "").strip()), "a skill card needs a name")
    out: dict[str, Any] = {"card": "skill", "name": str(name)}
    if description:
        out["description"] = str(description)
    if instructions:
        out["instructions"] = str(instructions)
    return out


def files_result(
    files: Iterable[dict[str, Any]], *, title: Optional[str] = None
) -> dict[str, Any]:
    """The files a turn hands over — `present`'s declaration, for the delivery row.

    `description` is the MODEL's words about the file, which is the one thing on the row
    that code cannot derive: a surface that fell back to the file's kind would be right
    about a `.docx` and silent about what is in it. It stays None when the model declared a
    file without explaining it — the entry is the declaration, and a row with a name and no
    line is still a row (`Deliverables.tsx` supplies the kind itself).

    Order is kept, and kept for the WHOLE call: this is a declaration list, not a search
    result, so its order is the model's own ranking. Deduplication is deliberately NOT here
    — a path declared twice is the surface's to fold, since only the surface knows whether
    the later description corrects the earlier one.
    """
    entries: list[dict[str, Any]] = []
    for entry in files:
        if not isinstance(entry, dict):
            continue
        path = str(entry.get("path") or "").strip()
        if not path:
            continue
        description = entry.get("description")
        entries.append(
            {
                "path": path,
                "description": str(description) if isinstance(description, str) and description else None,
            }
        )
    _require(bool(entries), "a files card needs at least one file")
    out: dict[str, Any] = {"card": "files", "files": entries}
    if title:
        out["title"] = str(title)
    return out


def fold(**counts: Any) -> dict[str, Any]:
    """What a fold of the model's own context did (`fold_context`), in numbers.

    The note itself is not here: it is the call's own argument, which a surface already
    holds and which is persisted with the call, so the card would only carry a second copy
    of up to 16,000 characters. An older card may also say `replaced_notes`
    (the note stood in for the earlier ones); a fold no longer can.
    """
    out: dict[str, Any] = {"card": "fold", **{name: counts.get(name) for name in FOLD_COUNTS}}
    validate(out)  # every count, as a non-negative int: one rule, in `card_rules`
    return out


def matches_by_file(
    matches: Iterable[dict[str, Any]],
    *,
    path_key: str = "file",
    line_key: str = "line",
    text_key: str = "text",
) -> list[dict[str, Any]]:
    """Fold `{file, line, text}` triples into the `files` shape a search card takes.

    Grouping is a fold over hits a tool already has, not a second search — and doing it
    here means every search-shaped tool produces the same card instead of each one
    re-implementing the grouping and disagreeing about the order files come out in.

    First-seen order is kept, for files and for hits within a file: a search returns its
    best hits first, and re-sorting them alphabetically would bury what the tool ranked.
    """
    groups: dict[str, list[dict[str, Any]]] = {}
    for hit in matches:
        if not isinstance(hit, dict):
            continue
        path = hit.get(path_key)
        if not isinstance(path, str) or not path:
            continue
        line = hit.get(line_key)
        text = hit.get(text_key)
        groups.setdefault(path, []).append(
            {
                "line_number": int(line) if isinstance(line, int) else 0,
                "line": str(text if text is not None else ""),
            }
        )
    return [{"path": path, "matches": rows} for path, rows in groups.items()]
