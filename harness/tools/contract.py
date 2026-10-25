"""Tool contract — what a tool declares about its OUTPUT, beyond its input schema.

`ToolSpec.schema` already says how to CALL a tool. Nothing said how to read what it
gives back, so the runtime had to guess: `_preview(value, 300)` truncates blindly for
the GUI, `json.dumps` feeds the model, the approval card prints raw arguments, and
there was no place to put a per-tool time budget. Four separate gaps, one missing
declaration.

A contract fills it with five optional pieces:

    render          the value → what the MODEL reads
    present_call    the arguments → what a PERSON reads before it runs
    present_result  the value → what a PERSON reads after it ran
    facts           the value → what the SESSION LOG records as data
    timeout_ms      how long the runtime waits before giving up

Each presenter may also attach a structured `card` to its `ToolPresentation` — the diff,
the terminal output, the file window — for a surface that draws one. `harness.tools.cards`
owns that vocabulary; `present_result` receives the call's card metadata as an optional
third argument, which is where facts the model-facing text cannot carry (a read tool's
line numbers, a mutation's before-image) reach it.

Every piece is optional and a tool without a contract behaves exactly as before, which
is the only way this lands: the registry holds ~100 builtin tools plus 40 connectors
plus MCP tools discovered at runtime, and they cannot all be migrated at once.

Declaration rides on the function, matching the existing `__tool_schema__` /
`__aisuite_tool_metadata__` precedent, so bulk `register_all(funcs)` keeps working and
tool factories can attach a contract to the closures they return.

**Presenters must be pure.** They run on the turn's hot path and are given already-
computed values; they must not perform I/O, mutate their arguments, or depend on
anything but what they are passed — a presenter that reads the filesystem would make
the same log replay differently later. Purity is not enforceable here, so the runtime
defends the weaker property instead: every contract call is wrapped (`safe_*` below)
and a raising presenter degrades to the pre-contract rendering rather than failing the
tool call. A third-party or MCP tool cannot break a turn with a bad presenter.

The split between the two presenters is deliberate and load-bearing: `present_call`
answers "what is about to happen in the world" (the approval decision), while
`present_result` answers "what happened" (the verification decision). They are the two
different questions a person is asked, at the two different moments.

Deliberately NOT here:
- reversibility — derivable from `harness.safety.risk.classify()`; a second source of truth
  for "can this be undone" is exactly the kind of drift that outlives the person who
  introduced it.
- the affected paths / recipients — `permissions.write_paths()` and
  `connectors.catalog.target_arg_for()` already own those, and the approval card
  reads them from there.
"""

from __future__ import annotations

import inspect
import logging
from dataclasses import dataclass, replace
from typing import Any, Callable, Optional

from .capabilities import (  # NETWORK/READ_ONLY/WORKSPACE_WRITE/SYSTEM_EXEC are
    # re-exported here for the tools that import them from contract
    NETWORK_CAPS,
    READ_ONLY_CAPS,
    SYSTEM_EXEC_CAPS,
    WORKSPACE_WRITE_CAPS,
    ToolCapabilities,
)
from .cards import slim as _slim_card
from .presentation import ToolPresentation

logger = logging.getLogger(__name__)

@dataclass(frozen=True, slots=True)
class ToolContract:
    """What one tool declares about its output. Every field optional; see module doc."""

    render: Optional[Callable[[dict[str, Any], Any], str]] = None
    present_call: Optional[Callable[[dict[str, Any]], ToolPresentation]] = None
    #: The completed-state presenter. The third argument is the call's card metadata —
    #: structured facts the model-facing result cannot carry (a read tool's line numbers,
    #: a mutation's before-image). It always has a default, so a presenter written
    #: against the two-argument form keeps working, and `ToolContract.__post_init__`
    #: normalises a two-argument callable into this shape rather than making every
    #: existing presenter learn a parameter it does not want.
    present_result: Optional[
        Callable[[dict[str, Any], Any, Optional[dict[str, Any]]], ToolPresentation]
    ] = None
    timeout_ms: Optional[int] = None
    #: Reach/safety declarations (see `ToolCapabilities`). None keeps the engine's
    #: derived answers, so a tool that says nothing behaves exactly as before.
    capabilities: Optional[ToolCapabilities] = None
    #: The tool bounds its own output under the per-result ceiling AND names how to get
    #: the rest (`read_file`'s window + `next: start_line=…`). The engine's aggregate
    #: output budget never shrinks such a result: a shrunk read can only be re-requested,
    #: and the re-request is shrunk again — the persist→read loop that pins `read_file`
    #: at an infinite threshold to break. The per-result ceiling still applies.
    self_bounded: bool = False
    #: What happened, as data: a flat dict of scalars the engine writes on the call's
    #: `tool/result` row (`safe_facts`). The rendered text is written for a model and
    #: changes whenever its wording does; anything that COUNTS results — the harness
    #: review, the bound below — reads this instead. Three names mean the same thing
    #: for every tool: `locator` (where the complete output was saved), `full_chars`
    #: (its size) and `shown_chars` (what the model was given of it).
    facts: Optional[Callable[[dict[str, Any], Any], dict[str, Any]]] = None

    def __post_init__(self) -> None:
        # A mistyped budget must fail at import, not silently never fire at runtime.
        if self.timeout_ms is not None and (
            not isinstance(self.timeout_ms, int)
            or isinstance(self.timeout_ms, bool)
            or self.timeout_ms <= 0
        ):
            raise ValueError(
                f"ToolContract.timeout_ms must be a positive int, got {self.timeout_ms!r}"
            )
        if self.capabilities is not None and not isinstance(
            self.capabilities, ToolCapabilities
        ):
            raise TypeError(
                "ToolContract.capabilities must be a ToolCapabilities or None"
            )
        if not isinstance(self.self_bounded, bool):
            raise TypeError("ToolContract.self_bounded must be a bool")
        for field in ("render", "present_call", "present_result", "facts"):
            value = getattr(self, field)
            if value is not None and not callable(value):
                raise TypeError(f"ToolContract.{field} must be callable or None")
        # Frozen + slots, so the normalised callable goes in through the back door. It is
        # still construction-time: nothing observes the un-normalised form.
        object.__setattr__(
            self, "present_result", _normalize_present_result(self.present_result)
        )


def _positional_arity(fn: Any) -> Optional[int]:
    """How many positional arguments `fn` accepts, or None when that cannot be told.

    None means "takes whatever it is given" (a `*args` callable) or "the signature is not
    readable" (a C builtin). Both are left unwrapped: guessing an arity we could not read
    would replace one unknown with another, and the runtime already degrades safely.
    """
    try:
        parameters = inspect.signature(fn).parameters.values()
    except (TypeError, ValueError):
        return None
    count = 0
    for parameter in parameters:
        if parameter.kind in (
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
        ):
            count += 1
        elif parameter.kind is inspect.Parameter.VAR_POSITIONAL:
            return None
    return count


def _normalize_present_result(fn: Any) -> Any:
    """Give every completed-state presenter the same three-argument shape.

    Two reasons this is normalisation rather than a longer signature on the field. The
    first is that the five presenters this codebase already has, and every test that calls
    one directly, are two-argument — a required third parameter would be a breaking change
    to prove a point. The second is that a three-argument presenter is only usable if its
    third parameter is optional, since a direct two-argument call must still work: that is
    checked here, at import, instead of surfacing as a `TypeError` inside `_guard` on the
    turn's hot path, where it would look like a broken presenter rather than a broken
    declaration.
    """
    if fn is None:
        return None
    arity = _positional_arity(fn)
    if arity is None:
        return fn
    if arity < 2:
        raise TypeError(
            "ToolContract.present_result must accept (arguments, value); "
            f"got a callable taking {arity} positional argument(s)"
        )
    if arity == 2:

        def present_result(
            arguments: dict[str, Any], value: Any, card_meta: Optional[dict[str, Any]] = None
        ) -> ToolPresentation:
            # A two-argument presenter has no use for card metadata; dropping it here is
            # what keeps the declaration two-argument.
            return fn(arguments, value)

        return present_result
    third = list(inspect.signature(fn).parameters.values())[2]
    if third.default is inspect.Parameter.empty:
        raise TypeError(
            "ToolContract.present_result's third parameter must have a default, so the "
            "presenter stays callable as (arguments, value)"
        )
    return fn


#: Where a contract lives on the tool function. Mirrors `__tool_schema__`.
CONTRACT_ATTR = "__tool_contract__"


def tool_contract(
    *,
    render: Optional[Callable[[dict[str, Any], Any], str]] = None,
    present_call: Optional[Callable[[dict[str, Any]], ToolPresentation]] = None,
    present_result: Optional[
        Callable[[dict[str, Any], Any], ToolPresentation]
    ] = None,
    timeout_ms: Optional[int] = None,
) -> Callable[[Any], Any]:
    """Attach a `ToolContract` to a tool function.

    Keyword-only on purpose: four optional callables are unreadable positionally, and
    the set will grow. Validation happens here, at import time.
    """
    contract = ToolContract(
        render=render,
        present_call=present_call,
        present_result=present_result,
        timeout_ms=timeout_ms,
    )

    def decorate(func: Any) -> Any:
        setattr(func, CONTRACT_ATTR, contract)
        return func

    return decorate


def attach_contract(func: Any, contract: ToolContract) -> Any:
    """Attach a contract to a function this codebase did not author.

    The decorator covers tools we write; this covers the larger half of the registry —
    aisuite toolkit tools, connector closures built by a factory, MCP tools discovered
    at runtime. Same mechanism as the existing `__tool_schema__` assignments, named
    so it greps. Returns `func` so it composes inside a list comprehension.
    """
    setattr(func, CONTRACT_ATTR, contract)
    return func


def contract_of(func: Any) -> Optional[ToolContract]:
    """The contract declared on `func`, or None. Never raises."""
    contract = getattr(func, CONTRACT_ATTR, None)
    return contract if isinstance(contract, ToolContract) else None


def clip(text: Any, limit: int = 160) -> str:
    """One line, bounded — for the `detail` of a presentation.

    Every presenter needs this and none of them should reinvent it: a card that renders
    a heredoc or a 5 MB stdout verbatim is a broken card. Newlines collapse because a
    detail line is one line by construction.
    """
    flat = " ".join(str(text or "").split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


# -- safe invocation ------------------------------------------------------------
# The runtime calls contracts on the turn's hot path. Each helper returns None for
# "nothing declared" AND for "declared but it blew up", so every call site has exactly
# one fallback branch instead of two. A defect is logged once, with the tool named, and
# the turn continues on the pre-contract path.


def _guard(tool: str, what: str, call: Callable[[], Any]) -> Optional[Any]:
    try:
        return call()
    except Exception:
        logger.warning("tool %s: %s raised; falling back", tool, what, exc_info=True)
        return None


def safe_render(
    contract: Optional[ToolContract],
    tool: str,
    arguments: dict[str, Any],
    value: Any,
) -> Optional[str]:
    """The model-facing text for a result, or None to use the default JSON encoding.

    A non-str return is a defect, not a coercion opportunity: silently `str()`-ing it
    would ship whatever `repr` happens to say straight into the model's context.
    """
    if contract is None or contract.render is None:
        return None
    rendered = _guard(tool, "render", lambda: contract.render(arguments, value))
    if rendered is None:
        return None
    if not isinstance(rendered, str):
        logger.warning(
            "tool %s: render returned %s, expected str; falling back",
            tool,
            type(rendered).__name__,
        )
        return None
    return rendered


#: A fact is a number, a flag or a short name. The bounds keep a careless `facts` from
#: putting a page of output on the log row under another key.
FACT_KEYS = 16
FACT_CHARS = 400
LOCATOR = "locator"
FULL_CHARS = "full_chars"
SHOWN_CHARS = "shown_chars"


def safe_facts(
    contract: Optional[ToolContract], tool: str, arguments: dict[str, Any], value: Any
) -> dict[str, Any]:
    """What the log records about this result, as data. Empty when nothing is declared,
    when the declaration raised, or when it returned nothing usable; never raises.

    Only scalars survive, strings clipped: a fact that needs a paragraph is content, and
    content belongs in the result."""
    if contract is None or contract.facts is None:
        return {}
    found = _guard(tool, "facts", lambda: contract.facts(arguments, value))
    if not isinstance(found, dict):
        return {}
    facts: dict[str, Any] = {}
    for key, item in found.items():
        if len(facts) >= FACT_KEYS:
            break
        if item is None or not isinstance(key, str):
            continue
        if isinstance(item, str):
            facts[key] = item[:FACT_CHARS]
        elif isinstance(item, (bool, int, float)):
            facts[key] = item
    return facts


def safe_present_call(
    contract: Optional[ToolContract], tool: str, arguments: dict[str, Any]
) -> Optional[ToolPresentation]:
    """What a person should read BEFORE this call runs, or None."""
    if contract is None or contract.present_call is None:
        return None
    shown = _guard(tool, "present_call", lambda: contract.present_call(arguments))
    return shown if isinstance(shown, ToolPresentation) else None


def safe_present_result(
    contract: Optional[ToolContract],
    tool: str,
    arguments: dict[str, Any],
    value: Any,
    card_meta: Optional[dict[str, Any]] = None,
) -> Optional[ToolPresentation]:
    """What a person should read AFTER this call ran, or None.

    `card_meta` is the call's presentation metadata, lifted off the result before either
    projection saw it (`engine._record_result`) — structured facts the model-facing value
    cannot carry. A presenter that does not declare a third parameter never receives it.
    """
    if contract is None or contract.present_result is None:
        return None
    shown = _guard(
        tool,
        "present_result",
        lambda: contract.present_result(arguments, value, card_meta),
    )
    if not isinstance(shown, ToolPresentation):
        return None
    if shown.card is not None and _slim_card(shown.card) is None:
        # The row degrades to the sentence it always had. Logged rather than silent: a
        # tool that builds a 5 MB card has a bug worth seeing, and it is not a bug that
        # should take the turn down.
        logger.warning("tool %s: card exceeds the payload budget; dropping it", tool)
        return replace(shown, card=None)
    return shown
