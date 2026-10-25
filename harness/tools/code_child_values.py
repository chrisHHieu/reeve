"""What a tool call returns inside a `run_code` program.

Tools return different shapes: a built-in returns a dict, an MCP tool often returns text,
sometimes JSON text. A program has to guess which, and it guessed wrong in both
directions. In the real session store, 7 of the 17 errors in model-written programs were
this guess:
- a dict read as text: `'dict' object has no attribute 'strip'` / `'splitlines'`;
- text read as a dict: `'str' object has no attribute 'get'`, `string indices must be
  integers`.

So the value keeps its real type and also answers the other way:
- `ToolDict` / `ToolList`: the dict or list itself, so `isinstance`, `[key]`, iteration
  and `print` work as before. String methods (`.strip()`, `.splitlines()`, `.lower()`
  ...) read its JSON text, and `.text` is that text.
- `ToolText`: the string itself. `.get(key)` and `["key"]` read it as JSON when it is a
  JSON object. When it is not, they raise a KeyError that says it is text.

Stdlib-only, like the rest of the child.
"""

from __future__ import annotations

import json
from typing import Any

_MISSING = object()


def _text_of(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _missing_key(key: Any, keys: Any) -> KeyError:
    available = ", ".join(repr(k) for k in list(keys)[:30]) or "(none)"
    return KeyError(f"{key!r} is not in this result. Its keys: {available}")


class _AsText:
    """String methods on a structured result read its JSON text."""

    __slots__ = ()

    @property
    def text(self) -> str:
        return _text_of(self)

    # `str()` / `print()` keep Python's own rendering: what a program prints is what the
    # model reads back, and that is not this module's to change.

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_") or not hasattr(str, name):
            raise AttributeError(
                f"{type(self).__name__.replace('Tool', '').lower()} result has no attribute {name!r}"
            )
        return getattr(_text_of(self), name)


class ToolDict(_AsText, dict):
    def __getitem__(self, key: Any) -> Any:
        try:
            return dict.__getitem__(self, key)
        except KeyError:
            raise _missing_key(key, self.keys()) from None


class ToolList(_AsText, list):
    pass


class ToolText(str):
    """A text result. `.get` and `[str]` read it as JSON when it is a JSON object."""

    @property
    def text(self) -> str:
        return str.__str__(self)

    def _object(self) -> Any:
        try:
            parsed = json.loads(self)
        except ValueError:
            return _MISSING
        return parsed if isinstance(parsed, dict) else _MISSING

    def get(self, key: Any, default: Any = None) -> Any:
        parsed = self._object()
        if parsed is _MISSING:
            raise KeyError(
                f"this tool returned text, not a dict, so it has no {key!r}: "
                "use it as a string (e.g. .splitlines(), `in`, a regex)"
            )
        return wrap(parsed.get(key, default)) if key in parsed else default

    def __getitem__(self, key: Any) -> Any:
        if not isinstance(key, str):
            return str.__getitem__(self, key)
        parsed = self._object()
        if parsed is _MISSING:
            raise KeyError(
                f"this tool returned text, not a dict, so it has no {key!r}: "
                "use it as a string (e.g. .splitlines(), `in`, a regex)"
            )
        if key not in parsed:
            raise _missing_key(key, parsed.keys())
        return wrap(parsed[key])


def wrap(value: Any) -> Any:
    """The value a program receives for one tool result."""
    if isinstance(value, dict):
        return ToolDict({k: wrap(v) for k, v in value.items()})
    if isinstance(value, list):
        return ToolList(wrap(v) for v in value)
    if isinstance(value, str):
        return ToolText(value)
    return value
