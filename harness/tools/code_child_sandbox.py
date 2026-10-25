"""What a workflow script's body may reach inside the code child (`code_child.py`).

The prelude is ours and runs with full builtins; the model-written body runs in a namespace
whose `__builtins__` is `_restricted_builtins()`. Split from `code_child.py` (size ceiling).
"""

from __future__ import annotations


#: Builtins a COORDINATING workflow script may see. The prelude itself is trusted and
#: runs with full builtins; the model-written body runs in a namespace whose
#: `__builtins__` is this table, so `open`/`eval`/`exec`/... are simply not names, and
#: `__import__` is `_guarded_import` (the modules in `WORKFLOW_IMPORTS`, nothing else).
#: Together with the host's AST guard (allowed imports only, no dunder attributes) this
#: raises the bar a long way — it is NOT a security boundary: the boundary is the child
#: process, exactly like `run_code`.
_RESTRICTED_BUILTINS = (
    "abs",
    "all",
    "any",
    "bool",
    "dict",
    "divmod",
    "enumerate",
    "filter",
    "float",
    "format",
    "frozenset",
    "int",
    "isinstance",
    "iter",
    "len",
    "list",
    "map",
    "max",
    "min",
    "next",
    "ord",
    "pow",
    "print",
    "range",
    "repr",
    "reversed",
    "round",
    "set",
    "slice",
    "sorted",
    "str",
    "sum",
    "tuple",
    "zip",
    # Measured 2026-10-05 on the session store: real scripts died on `type(...)` with a
    # NameError. None of these reaches the machine, and with no `getattr` and no dunder
    # attribute (the host's AST guard) a class object goes nowhere.
    "type",
    "chr",
    "callable",
    "Exception",
    "AttributeError",
    "IndexError",
    "KeyError",
    "RuntimeError",
    "StopIteration",
    "TypeError",
    "ValueError",
    "ZeroDivisionError",
)


#: Standard-library modules a workflow script may import: pure computation over values,
#: nothing that reaches the machine, and nothing that evaluates a string or reads an
#: attribute by name (`typing`'s forward references, `operator.attrgetter` would walk
#: around the dunder guard). Models write `import json` / `import re` out of habit, and 4
#: of the 7 failed workflow runs in the session store (2026-10-05) were that refusal.
#: `time`, `random`, `datetime` and `uuid` stay out for another reason: the journal
#: replays a resumed run by position and arguments, and a value that differs per run
#: makes the replay wrong. The host's AST check (`workflow.validate_script`) reads this
#: same set, and says why.
WORKFLOW_IMPORTS = frozenset(
    {
        "json",
        "re",
        "math",
        "statistics",
        "collections",
        "itertools",
        "functools",
        "textwrap",
        "heapq",
        "bisect",
        "difflib",
        "string",
        "copy",
    }
)


def _guarded_import(name, globals=None, locals=None, fromlist=(), level=0):  # noqa: A002
    """`__import__` for a workflow body: an allowed module by its absolute name, nothing
    else. The AST check refuses the rest before the run; this is the enforced half."""
    if level != 0 or str(name).split(".")[0] not in WORKFLOW_IMPORTS:
        raise ImportError(f"import of {name!r} is not available in a workflow script")
    import builtins as _builtins

    return _builtins.__import__(name, globals, locals, fromlist, level)


def _restricted_builtins() -> dict:
    import builtins as _builtins

    table = {
        name: getattr(_builtins, name)
        for name in _RESTRICTED_BUILTINS
        if hasattr(_builtins, name)
    }
    table["__import__"] = _guarded_import
    return table
