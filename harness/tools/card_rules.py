"""The card vocabulary's rules: which kinds exist, what a well-formed card is, how large one
may be.

`cards.py` builds cards; this module says what a card must be. They were one file until the
builders outgrew it. Everything here is reached through `cards` (`cards.validate`,
`cards.slim`, `cards.RESULT_KINDS`), which is how callers already spell it.
"""

from __future__ import annotations

from typing import Any, Optional

#: The card kinds a CALL may carry.
CALL_KINDS = frozenset(("generic", "terminal", "diff"))

#: The card kinds a RESULT may carry. A superset of the call kinds: `read`, `search`,
#: `web`, `skill` and `files` have no call-time analogue, because the pending state has
#: nothing to show. A search call is a `generic` card and only its result is a `search`
#: card; `present`'s declaration is the same shape — the call row is `generic` and the
#: card exists only once the files have been checked and declared. `fold` is the same: the
#: numbers of a fold exist only once it has been applied, and so is `visual`: its
#: blocks are drawn from what the harness checked and read.
RESULT_KINDS = frozenset(
    ("generic", "terminal", "diff", "read", "search", "web", "skill", "files", "fold", "visual")
)

#: The counts a fold card carries, all of them: a surface draws the row from these.
FOLD_COUNTS = ("folded_messages", "kept_messages", "before_tokens", "after_tokens", "saved_tokens")


def require(condition: bool, message: str) -> None:
    """Fail loud at construction, exactly like `ToolPresentation.__post_init__`.

    Builders run inside the presenters, which the runtime wraps in `safe_*` — so a raise
    degrades the row to the pre-card rendering AND leaves a logged defect. Strict
    contract, safe runtime: the alternative is a half-built card reaching a surface.
    """
    if not condition:
        raise ValueError(f"tool card: {message}")


def validate(card: Any) -> None:
    """Raise unless `card` is a well-formed card.

    Called from `ToolPresentation.__post_init__`, so a presenter that builds a card
    wrongly fails at the same moment and with the same consequence as one that returns a
    blank summary: a logged defect and the pre-card row.

    The check is deliberately shallow about the PAYLOAD — it proves the discriminant and
    the fields a surface branches on exist and have the right type, and stops there. A
    card is wire data; the depth of a deep validator would only be a second place to keep
    in step with the first.
    """
    require(isinstance(card, dict), "a card must be a dict")
    kind = card.get("card")
    require(
        kind in CALL_KINDS or kind in RESULT_KINDS,
        f"unknown card kind {kind!r}",
    )
    if kind == "generic":
        # A generic RESULT card may omit the title (it keeps the call's); a generic CALL
        # card may not — there would be nothing on the row at all.
        if "title" in card:
            require(
                isinstance(card["title"], str) and card["title"].strip() != "",
                "a generic card's title must be a non-empty string",
            )
    elif kind == "terminal":
        # A title is REQUIRED on a call card and OPTIONAL on a result card: a result is
        # allowed to defer to the pending title ("omit to keep the pending-state title"),
        # and the builders enforce the difference — `terminal_call` refuses an empty one,
        # `terminal_result` does not. Validation only checks the type, so a result card
        # cannot carry a title that is a number.
        if "title" in card:
            require(
                isinstance(card["title"], str) and card["title"].strip() != "",
                "a terminal card's title must be a non-empty string",
            )
        for field in ("output", "cwd", "description", "signal"):
            if field in card:
                require(isinstance(card[field], str), f"terminal card {field} must be a string")
        if "exit_code" in card:
            require(
                isinstance(card["exit_code"], int) and not isinstance(card["exit_code"], bool),
                "terminal card exit_code must be an int",
            )
    elif kind == "diff":
        require(isinstance(card.get("diffs"), list) and card["diffs"], "a diff card needs diffs")
        for hunk in card["diffs"]:
            require(isinstance(hunk, dict), "a diff hunk must be a dict")
            require(
                isinstance(hunk.get("path"), str) and hunk["path"].strip() != "",
                "a diff hunk needs a path",
            )
            require(isinstance(hunk.get("new_text"), str), "a diff hunk needs new_text")
            old = hunk.get("old_text")
            require(old is None or isinstance(old, str), "a diff hunk's old_text must be str or None")
            for field in ("added", "removed"):
                value = hunk.get(field)
                if value is not None:
                    require(
                        isinstance(value, int) and not isinstance(value, bool) and value >= 0,
                        f"a diff hunk's {field} must be a non-negative int",
                    )
            for field in ("old_start", "new_start"):
                value = hunk.get(field)
                if value is not None:
                    require(
                        isinstance(value, int) and not isinstance(value, bool) and value >= 1,
                        f"a diff hunk's {field} must be a 1-based line",
                    )
    elif kind == "read":
        require(isinstance(card.get("path"), str), "a read card needs a path")
        require(isinstance(card.get("lines"), list), "a read card needs lines")
        require(isinstance(card.get("total_lines"), int), "a read card needs total_lines")
        require(isinstance(card.get("offset"), int), "a read card needs an offset")
    elif kind == "search":
        shape = card.get("shape")
        require(shape in ("matches", "paths"), f"unknown search card shape {shape!r}")
        key = "files" if shape == "matches" else "paths"
        require(isinstance(card.get(key), list) and card[key], f"a search card needs {key}")
    elif kind == "web":
        require(card.get("kind") in ("search", "fetch"), "a web card needs kind search|fetch")
        if card["kind"] == "search":
            require(isinstance(card.get("sources"), list), "a web search card needs sources")
        else:
            require(isinstance(card.get("url"), str), "a web fetch card needs a url")
    elif kind == "files":
        # A card with no file is a delivery row with nothing to open, so it is refused at
        # construction exactly like an empty search card: "nothing to declare" is the
        # sentence's job, not the card's.
        entries = card.get("files")
        require(isinstance(entries, list) and entries, "a files card needs at least one file")
        for entry in entries:
            require(isinstance(entry, dict), "a files card entry must be a dict")
            require(
                isinstance(entry.get("path"), str) and entry["path"].strip() != "",
                "a files card entry needs a path",
            )
            # None and a string are both legal (a file declared without a reason); a number
            # or a nested object is not, because a surface would render it as `[object]`.
            description = entry.get("description")
            require(
                description is None or isinstance(description, str),
                "a files card entry's description must be a string or None",
            )
    elif kind == "visual":
        blocks = card.get("blocks")
        require(isinstance(blocks, list) and blocks, "a visual card needs blocks")
        for block in blocks:
            require(isinstance(block, dict) and isinstance(block.get("type"), str), "a visual block needs a type")
    elif kind == "fold":
        for name in FOLD_COUNTS:
            value = card.get(name)
            require(
                isinstance(value, int) and not isinstance(value, bool) and value >= 0,
                f"a fold card needs {name} as a non-negative int",
            )


def slim(card: Any, limit: int = 120_000) -> Optional[dict[str, Any]]:
    """`card` if it fits a payload budget, else None.

    Cards travel on the same websocket frame as the event they describe, and the terminal
    card is the one that can be large: it carries a command's output, which a tool is
    already allowed to keep tens of thousands of characters of. The tool's own cap is the
    real bound; this is the belt to that pair of braces — a presenter that hands back a
    card past the budget yields no card, and the row renders the plain text it would have
    rendered before cards existed.

    A cheaper proxy than serialising, which would cost a full JSON encode of a payload we
    are about to encode anyway: the sum of the string lengths is an upper bound on the
    encoded size for every card shape here.
    """
    if not isinstance(card, dict):
        return None
    if _weigh(card) > limit:
        return None
    return card


def _weigh(value: Any) -> int:
    if isinstance(value, str):
        return len(value)
    if isinstance(value, dict):
        return sum(len(str(key)) + _weigh(item) for key, item in value.items())
    if isinstance(value, (list, tuple)):
        return sum(_weigh(item) for item in value)
    return 0
