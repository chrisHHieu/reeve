"""Why a call's arguments cannot be passed to its tool — split from registry.py.

`ToolRegistry.argument_problem` delegates here.
"""

from __future__ import annotations

import difflib
import inspect
import re
from typing import Any, Optional

_REISSUE = " The tool did not run; re-issue the call with those parameters."


def _folded(name: str) -> str:
    """A parameter name with case and separators taken out: `contentId`, `content_id` and
    `content-id` are one name to a person, and three to a server."""
    return re.sub(r"[^a-z0-9]", "", name.lower())


def _nearest(key: str, names: list[str]) -> Optional[str]:
    """The accepted name a wrong one was most likely meant to be, or None."""
    same = [name for name in names if _folded(name) == _folded(key)]
    if same:
        return same[0]
    close = difflib.get_close_matches(key, names, n=1, cutoff=0.6)
    return close[0] if close else None


def _unexpected(unknown: list[str], names: list[str]) -> str:
    said = []
    for key in unknown:
        near = _nearest(key, names)
        said.append(f"'{key}'" + (f" (did you mean '{near}'?)" if near else ""))
    return "unexpected " + ", ".join(said)


def _schema_of(spec: Any) -> dict[str, Any]:
    return ((spec.schema.get("function") or {}).get("parameters") or {}) if spec.schema else {}


def _forwarded_problem(spec: Any, name: str, args: dict[str, Any]) -> Optional[str]:
    """For a tool whose function takes any keyword and forwards it (an MCP server's tool),
    judged by the schema the model was shown, which is the server's own contract.

    Refused only where the server would refuse too: a name its schema requires is missing,
    or a name it does not list is sent to a schema that allows nothing else
    (`additionalProperties: false`). A name it does not list is otherwise passed on, and
    named in the refusal only when the call is refused anyway. Measured on the session
    store (2026-10-07): of the MCP calls that failed and then worked with different
    parameter NAMES, the schema refuses 10 of the 12 whose schema was cached, and the
    remote server's own errors for them were opaque (`{"error": "…"}`)."""
    schema = _schema_of(spec)
    properties = [key for key in (schema.get("properties") or {}) if isinstance(key, str)]
    required = [key for key in (schema.get("required") or []) if isinstance(key, str)]
    missing = [key for key in required if key not in args]
    unknown = [key for key in args if key not in properties]
    closed = schema.get("additionalProperties") is False
    if not missing and not (closed and unknown):
        return None
    problems = []
    if unknown:
        problems.append(_unexpected(unknown, properties))
    if missing:
        problems.append("missing required " + ", ".join(f"'{key}'" for key in missing))
    text = f"Invalid arguments for `{name}`: {'; '.join(problems)}."
    if properties:
        text += f" Accepted parameters: {', '.join(properties)}"
        if required:
            text += f" (required: {', '.join(required)})"
        text += "."
    return text + _REISSUE


def argument_problem(
    spec: Any, name: str, arguments: Optional[dict[str, Any]]
) -> Optional[str]:
    """Why `arguments` cannot be passed to this tool, or None when they can.

    The test is the binding Python performs at call time (`Signature.bind`), not the
    JSON schema: a schema can be looser or stricter than the function (derived schemas),
    and refusing a call the tool would have taken breaks work. The schema only supplies
    the WORDS — the parameter list the model was shown — so the refusal names what to send
    instead of a closure's qualified name (2026-09-24 audit:
    `make_web_fetch_tool.<locals>.web_fetch() got an unexpected keyword argument
    'max_lines'`), and the name a wrong one was probably meant to be. A function that takes
    any keyword forwards its arguments elsewhere, and is judged by its schema instead
    (`_forwarded_problem`). None whenever the signature cannot be read.
    """
    if spec is None:
        return None
    try:
        signature = inspect.signature(spec.func)
    except (TypeError, ValueError):
        return None
    args = dict(arguments or {})
    params = signature.parameters.values()
    if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params):
        return _forwarded_problem(spec, name, args)
    try:
        signature.bind(**args)
        return None
    except TypeError:
        pass
    accepted = [
        p.name
        for p in params
        if p.kind in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY)
    ]
    required = [
        p.name
        for p in params
        if p.kind in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY)
        and p.default is inspect.Parameter.empty
    ]
    shown = list(_schema_of(spec).get("properties") or {}) or accepted
    problems: list[str] = []
    unknown = [key for key in args if key not in accepted]
    if unknown:
        problems.append(_unexpected(unknown, shown))
    missing = [key for key in required if key not in args]
    if missing:
        problems.append("missing required " + ", ".join(f"'{key}'" for key in missing))
    if not problems:
        problems.append("the arguments do not fit the tool's parameters")
    text = f"Invalid arguments for `{name}`: {'; '.join(problems)}."
    if shown:
        text += f" Accepted parameters: {', '.join(shown)}"
        if required:
            text += f" (required: {', '.join(required)})"
        text += "."
    return text + _REISSUE
