"""What `grep` shows: the model-facing render and the person-facing presenters.

Split out of `tools/search.py` (the tool, its schema and its engines' wiring) when grep
gained output modes. Pure functions of the call's arguments and result value.
"""

from __future__ import annotations

import json
from typing import Any, Optional

from . import cards
from .contract import ToolPresentation, clip
from .grep_engines import _DEFAULT_MAX_RESULTS, _MAX_RESULTS


# -- what a person sees (tools/contract.py) -------------------------------------
# `grep` already returns `{file, line, text}` per hit, which is everything a grouped
# result card needs; before the contract existed a surface got a 300-character preview of
# that JSON and had to guess. Its `present_call` carries a `generic` card rather than a
# search card because a call has no matches yet — there is nothing structured to show
# until it runs, which is the rule for search tools too.


def _grep_present_call(arguments: dict[str, Any]) -> ToolPresentation:
    pattern = clip(arguments.get("pattern"), 160)
    where = str(arguments.get("path") or "").strip()
    summary = f"Search for “{pattern}”" if pattern else "Search the workspace"
    if where and where != ".":
        summary += f" in {clip(where, 80)}"
    include = str(arguments.get("glob") or "").strip()
    if include:
        summary += f" ({clip(include, 60)})"
    return ToolPresentation(
        summary=summary,
        detail=clip(pattern),
        kind="search",
        card=cards.generic_call(summary, kind="search", raw_input=pattern or None),
    )


def _grep_present_result(
    arguments: dict[str, Any], value: Any, card_meta: Any = None
) -> ToolPresentation:
    if not isinstance(value, dict):
        return ToolPresentation(summary="Search finished", kind="search")
    error = str(value.get("error") or "").strip()
    if error:
        return ToolPresentation(summary=clip(error), kind="search")
    paths = value.get("files")
    if not isinstance(paths, list) and isinstance(value.get("counts"), list):
        paths = [str(r.get("file")) for r in value["counts"] if isinstance(r, dict)]
    if isinstance(paths, list):
        if not paths:
            return ToolPresentation(
                summary=f"No matches for “{clip(arguments.get('pattern'), 60)}”",
                kind="search",
            )
        return ToolPresentation(
            summary=f"Found {len(paths)} file{'' if len(paths) == 1 else 's'}",
            kind="search",
            card=cards.search_paths(paths, truncated=_truncated(arguments, value)),
        )
    found = value.get("matches")
    hits = (
        [m for m in found if isinstance(m, dict) and not m.get("context") and not m.get("cont")]
        if isinstance(found, list)
        else []
    )
    count = value.get("count")
    total = count if isinstance(count, int) else len(hits)
    if total == 0:
        # Zero hits is a result, not an empty card: "no matches" IS the answer to the
        # question that was asked, and a card that renders nothing reads as a failure.
        return ToolPresentation(
            summary=f"No matches for “{clip(arguments.get('pattern'), 60)}”",
            kind="search",
        )
    files = cards.matches_by_file(hits)
    if not files:
        # Hits that carried no usable file/line (a malformed engine payload): the count is
        # still the truth, and a card built on nothing would be a lie with a shape.
        return ToolPresentation(summary=f"Found {total} matches", kind="search")
    searched = value.get("engine")
    detail = (
        f"{total} match{'' if total == 1 else 'es'} in "
        f"{len(files)} file{'' if len(files) == 1 else 's'}"
    )
    if isinstance(searched, str) and searched:
        detail += f" ({searched})"
    # The cap is reported rather than hidden: someone reading 100 hits from a 1000-hit
    # search must not conclude there were exactly 100.
    truncated = _truncated(arguments, value)
    return ToolPresentation(
        summary=f"Found {total} matches",
        detail=detail,
        kind="search",
        card=cards.search_matches(files, truncated=truncated, total=total),
    )


#: The Python fallback below (`_py_grep`) walks the tree and opens each file with no
#: deadline of its own — only the ripgrep branch carries a 30 s `subprocess` timeout — so
#: this budget is the one that covers both, and it is deliberately above 30 s so the
#: branch that DOES bound itself keeps firing first.
def _grep_body(matches: list[Any]) -> str:
    """Matches grouped by file, in first-seen order, as `path:` + `  line: text`.

    Context lines (`context: true`) print as `  line- text`, grep's own convention, and a
    gap between two blocks in one file is marked `  --` so neighbours are never read as
    one excerpt."""
    sections: list[str] = []
    current: Optional[str] = None
    rows: list[str] = []
    previous = 0
    for match in matches:
        if not isinstance(match, dict):
            continue
        path = str(match.get("file") or "")
        if path != current:
            if rows and current is not None:
                sections.append(f"{current}:\n" + "\n".join(rows))
            current, rows, previous = path, [], 0
        line = match.get("line")
        if isinstance(line, int) and previous and line > previous + 1:
            rows.append("  --")
        mark = "-" if match.get("context") else ":"
        rows.append(f"  {line}{mark} {match.get('text')}")
        previous = line if isinstance(line, int) else 0
    if rows and current is not None:
        sections.append(f"{current}:\n" + "\n".join(rows))
    return "\n\n".join(sections)


def _grep_render(arguments: dict[str, Any], value: Any) -> str:
    """A count line plus the result as text, instead of the JSON value.

    The JSON default repeats `"file"`, `"line"`, and `"text"` for every match and
    escapes every newline; this is the same facts as text, for roughly half the
    tokens — grep is the second-heaviest tool output after `read_file`.
    """
    if not isinstance(value, dict):
        return json.dumps(value, default=str)
    error = str(value.get("error") or "").strip()
    if error:
        return f"Error: {error}"
    more = _truncated(arguments, value)
    start = value.get("offset") if isinstance(value.get("offset"), int) else 0
    files = value.get("files")
    if isinstance(files, list):
        if not files:
            return "No matches found" + _past(start)
        head = f"Found {len(files)} file{'' if len(files) == 1 else 's'}" + _from(start)
        if more:
            head += _next_page("files", start + len(files))
        return head + "\n\n" + "\n".join(str(f) for f in files)
    counts = value.get("counts")
    if isinstance(counts, list):
        if not counts:
            return "No matches found" + _past(start)
        total = value.get("count") if isinstance(value.get("count"), int) else 0
        head = (
            f"Found {total} match{'' if total == 1 else 'es'} in {len(counts)} "
            f"file{'' if len(counts) == 1 else 's'}" + _from(start)
        )
        if more:
            head += _next_page("files", start + len(counts))
        return head + "\n\n" + "\n".join(f"{r.get('file')}: {r.get('count')}" for r in counts)
    matches = value.get("matches")
    if not isinstance(matches, list) or not matches:
        return "No matches found" + _past(start)
    count = value.get("count")
    total = count if isinstance(count, int) else len(matches)
    header = f"Found {total} match{'' if total == 1 else 'es'}" + _from(start)
    if more:
        header += _next_page("matches", start + total)
    return f"{header}\n\n{_grep_body(matches)}"


def _from(start: int) -> str:
    return f", from result {start + 1}" if start else ""


def _past(start: int) -> str:
    return f" past result {start}" if start else ""


def _next_page(what: str, after: int) -> str:
    """How to read on. Telling the model only to narrow the search (the old wording) sent it to
    `run_shell` with `| head`/`| tail` to page: 158 of 238 shell searches did (2026-09-28 to
    10-12). Narrowing is still the better move when the page is noise."""
    return (
        f" (more {what} exist: call again with offset={after} for the next page, "
        "or narrow the pattern, path, glob or type)"
    )


def _grep_facts(arguments: dict[str, Any], value: Any) -> dict[str, Any]:
    """What a review counts about a search: the mode, the engine, how many results
    the page shows, whether it stopped short, and where it started."""
    if not isinstance(value, dict):
        return {}
    if value.get("error"):
        return {"failed": True}
    facts: dict[str, Any] = {
        "mode": str(value.get("mode") or "content"),
        "capped": bool(_truncated(arguments, value)),
        "offset": value.get("offset") if isinstance(value.get("offset"), int) else 0,
    }
    if isinstance(value.get("engine"), str):
        facts["engine"] = value["engine"]
    for key in ("files", "counts"):
        if isinstance(value.get(key), list):
            facts["shown"] = len(value[key])
            break
    else:
        facts["shown"] = value.get("count") if isinstance(value.get("count"), int) else 0
    for flag in ("multiline", "ignore_case"):
        if arguments.get(flag) is True:
            facts[flag] = True
    if arguments.get("type"):
        facts["type"] = str(arguments["type"])[:20]
    return facts


def _truncated(arguments: dict[str, Any], value: dict[str, Any]) -> bool:
    """Whether the search stopped short of every match.

    The tool answers this itself (`truncated`, from one match seen past the cap). It used
    to be inferred from the MODEL's `max_results` argument, so the ordinary call — the
    default cap — was cut silently (2026-09-24 audit). A value without the flag falls back
    to comparing against the cap actually in force, default included.
    """
    flag = value.get("truncated")
    if isinstance(flag, bool):
        return flag
    cap = arguments.get("max_results")
    cap = cap if isinstance(cap, int) and cap > 0 else _DEFAULT_MAX_RESULTS
    matches = value.get("matches")
    count = value.get("count")
    total = count if isinstance(count, int) else len(matches if isinstance(matches, list) else [])
    return total >= min(cap, _MAX_RESULTS)
