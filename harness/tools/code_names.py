"""The tools a `run_code` program names, read from its text before it starts.

A program that calls a tool the session does not hold fails at that line, after whatever
it did first — and half a program's side effects is the worst outcome available to it.
The description of `run_code` used to prevent that by listing every callable name, which
on a loaded install was 306 names and a quarter of the tool list on every request.

The names are checked here instead: `tools.<name>` is an attribute access a parser can
see, so a program naming a tool that is not there is refused before it runs, before a
person is asked to approve it, with the closest names that do exist. A name the parser
cannot see (`getattr(tools, name)`) still meets the child's own check at the call.
"""

from __future__ import annotations

import ast
import difflib
import textwrap
from typing import Iterable

#: Attributes of the `tools` object itself (`tools/code_child._Tools`), not tools.
OWN_ATTRIBUTES = frozenset({"describe"})
#: Names offered for one unknown name, and unknown names spelled out in one refusal.
MAX_CLOSE = 4
MAX_NAMED = 6


def named_tools(code: str) -> list[str]:
    """Every `tools.<name>` in *code*, once each, in the order written.

    A program that does not parse names nothing: the child reports the syntax error with
    its traceback, which is the more useful answer.
    """
    try:
        # The body may `return` and `await` at the top level, so it is parsed the way the
        # child runs it: as the body of a coroutine.
        tree = ast.parse("async def __program__(tools):\n" + textwrap.indent(code or "pass", "    "))
    except (SyntaxError, ValueError, RecursionError):
        return []
    found: list[tuple[int, int, str]] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "tools"
            and not node.attr.startswith("_")
            and node.attr not in OWN_ATTRIBUTES
        ):
            found.append((node.lineno, node.col_offset, node.attr))
    seen: set[str] = set()
    ordered: list[str] = []
    for _, _, name in sorted(found):
        if name not in seen:
            seen.add(name)
            ordered.append(name)
    return ordered


def close_names(name: str, callable_names: Iterable[str]) -> list[str]:
    """The callable names nearest to *name*: an MCP tool whose own name it is first
    (`search` for `mcp__mail__search`), then the nearest spellings."""
    pool = sorted(callable_names)
    lowered = name.lower()
    suffixed = [candidate for candidate in pool if candidate.lower().endswith("__" + lowered)]
    spelled = difflib.get_close_matches(name, pool, n=MAX_CLOSE, cutoff=0.6)
    short = {candidate.rsplit("__", 1)[-1]: candidate for candidate in pool if "__" in candidate}
    spelled += [short[key] for key in difflib.get_close_matches(name, list(short), n=MAX_CLOSE, cutoff=0.75)]
    ordered: list[str] = []
    for candidate in suffixed + spelled:
        if candidate not in ordered:
            ordered.append(candidate)
    return ordered[:MAX_CLOSE]


def check_names(
    code: str,
    callable_names: Iterable[str],
    *,
    direct_only: Iterable[str] = (),
    searchable: bool = False,
) -> tuple[str, list[str]]:
    """`(refusal, names)` for a program, or `("", [])` when every tool it names exists.

    *direct_only* are tools the session holds that a program may not call (the ones that
    ask a person); *searchable* says the session has deferred tools `tool_search` finds.
    """
    held = set(callable_names)
    direct = set(direct_only)
    missing = [name for name in named_tools(code) if name not in held]
    if not missing:
        return "", []
    lines = ["Nothing ran: this program calls a tool this session does not have."]
    for name in missing[:MAX_NAMED]:
        if name in direct:
            lines.append(f"- tools.{name}: not callable from a program. Call {name} directly, outside run_code.")
            continue
        near = close_names(name, held)
        lines.append(f"- tools.{name}: no such tool." + (f" Closest: {', '.join(near)}." if near else ""))
    if len(missing) > MAX_NAMED:
        lines.append(f"- and {len(missing) - MAX_NAMED} more.")
    lines.append(
        "A program may call any tool in this request's tool list"
        + (", and any deferred tool: tool_search finds one by what it does." if searchable else ".")
    )
    return "\n".join(lines), missing


__all__ = ["OWN_ATTRIBUTES", "check_names", "close_names", "named_tools"]
