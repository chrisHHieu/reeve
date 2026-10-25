"""How a `run_code` program's positional tool call becomes keyword arguments.

Split out of `tools/code_child.py`; stdlib-only for the same reason that module is (the
program's process must not load the host's policy code).
"""

from __future__ import annotations


def _param_names(signature: str) -> list[str]:
    """Parameter names, in order, from a `tool_signatures` line: `name(a: str, b: int | None
    = None)`. The types there are bare names (`str`, `dict`, `Any`), so a top-level comma
    split is exact."""
    head = signature.split("\n", 1)[0]
    inside = head[head.find("(") + 1 : head.rfind(")")] if "(" in head else ""
    return [part.split(":", 1)[0].strip() for part in inside.split(",") if part.strip()]


def _bind_positional(
    name: str, values: tuple, arguments: dict, signatures: dict
) -> dict:
    """Turn a positional call into the keyword arguments a tool takes.

    Two shapes models write (found live, 2026-09-24: `tools.x({})` failed five programs in
    a row): ONE dict whose keys are all parameter names is the arguments object — the
    shape `tool_call` and JSON-schema tools use; otherwise values fill the signature's
    parameters in order. Anything else is refused here, with the signature, before the
    host is asked.
    """
    params = _param_names(signatures.get(name, ""))
    signature = signatures.get(name)
    if (
        len(values) == 1
        and not arguments
        and isinstance(values[0], dict)
        and all(isinstance(k, str) and k in params for k in values[0])
    ):
        return dict(values[0])
    if len(values) > len(params):
        problem = f"tools.{name} takes at most {len(params)} positional value(s), got {len(values)}"
    else:
        bound = dict(zip(params, values))
        clash = sorted(set(bound) & set(arguments))
        if not clash:
            return {**bound, **arguments}
        problem = f"tools.{name} got {', '.join(clash)} both positionally and by keyword"
    hint = f"\nSignature: tools.{signature}" if signature else ""
    raise TypeError(f"{problem}.{hint}\nCall tools.describe(name) before using a tool.")
