"""Thinking levels — a named effort preset, derived from what the catalogue states.

This is called a "variant". The lesson worth taking from it is architectural, not
literal: the LEVELS are data (read from the provider-published catalogue), and every
provider-specific request body lives in ONE place. The UI only ever deals with
``["default", *levels]`` and never learns what "high" means to a given API — swapping a
model changes the offered levels without a line of UI code.

Reeve's twist: a large hand-written per-provider
table of levels. Reeve's catalogue doctrine says the provider's own statement wins
(`catalog.py`), and models.dev already publishes a model's `reasoning_options`
(``effort`` values, a ``toggle``, or a ``budget_tokens`` range) — so that is the first
source here. A SMALL curated fallback covers the families we actively vouch for when the
catalogue row is silent, which is the same narrow role `matrix.py` plays.

Absent both, a model has NO levels. The UI shows nothing rather than inventing an effort
the provider cannot honor — a false level looks exactly like a real one until it 400s.
"""

from __future__ import annotations

from typing import Any, Optional

#: Canonical effort vocabulary, low → high. A catalogue's values are reordered into this,
#: with anything unrecognized kept after it in the order the catalogue gave.
_CANONICAL_EFFORTS = ("none", "minimal", "low", "medium", "high", "xhigh", "max")

#: Families Reeve vouches for that take an `effort` knob but whose catalogue row either
#: predates `reasoning_options` or omitted it. Reached ONLY when the catalogue says the
#: model reasons at all — a curated narrowing, never an invention on a non-reasoning model.
_CURATED_EFFORT: dict[str, tuple[str, ...]] = {
    "openai": ("none", "low", "medium", "high"),
    "openai-codex": ("low", "medium", "high"),
    "xai": ("low", "high"),
    "groq": ("low", "medium", "high"),
    "together": ("low", "medium", "high"),
    "fireworks": ("low", "medium", "high"),
}

#: Providers whose wire takes a bare `reasoning_effort`: stock OpenAI plus the
#: OpenAI-compatible vendors Reeve routes. Anything else needs a shaped body (Anthropic,
#: Gemini) or has no effort knob at all (Bedrock, stock Responses), so a canonical level
#: must not be sprayed at it as an unknown field.
_EFFORT_PROVIDERS = frozenset(
    {
        "openai",
        "openai-codex",
        "openrouter",
        "deepseek",
        "xai",
        "groq",
        "together",
        "fireworks",
        "mistral",
        "zai",
        "zhipuai-coding-plan",
        "zai-coding-plan",
        "kimi",
        "minimax",
        "qwen",
        "ark",
    }
)

#: Anthropic `budget_tokens` for the pre-4.6 models that still accept it. 4.6+/Claude 5#: take adaptive thinking instead (Reeve sends no effort for those), so they get no
#: levels rather than a budget the API rejects.
_ANTHROPIC_BUDGETS: dict[str, int] = {
    "low": 2048,
    "medium": 8192,  # the provider's own DEFAULT_THINKING_BUDGET
    "high": 16384,
    "max": 32768,
}

#: Gemini thinking budgets. Thought tokens are billed as output here, so "none" is the
#: only level that also flips `include_thoughts` off (the provider's existing behaviour).
_GEMINI_BUDGETS: dict[str, int] = {
    "none": 0,
    "low": 1024,
    "medium": 8192,
    "high": 24576,
}

#: What a Gemini level MAPS to: the levels above, plus `minimal`, which the catalogue states for
#: Gemini 3.x Flash (`reasoning_options`) and so can be picked. Kept apart from the list above, which
#: is what is OFFERED when the catalogue is silent: 2.5 models have no such level.
_GEMINI_MAPPED: dict[str, int] = {**_GEMINI_BUDGETS, "minimal": 512}


def provider_of(model: str) -> str:
    """The provider prefix a routed model belongs to (bare ids route to the OpenAI default)."""
    return model.split(":", 1)[0] if ":" in (model or "") else "openai"


def bare_model(model: str) -> str:
    """The id a provider client actually receives — the router strips the prefix."""
    return model.split(":", 1)[1] if ":" in (model or "") else (model or "")


def _split_routing(model: str) -> tuple[str, str]:
    """(family, model name) of a routed id: the routing segment Vertex puts in front of the name
    (`vertex:gemini/gemini-3.7-flash`) is the family, everything else has none."""
    bare = bare_model(model)
    if provider_of(model) == "vertex":
        family, slash, rest = bare.partition("/")
        if slash and family in ("gemini", "claude", "openweight"):
            return family, rest
        if bare.startswith("gemini"):
            return "gemini", bare
        if bare.startswith("claude"):
            return "claude", bare
    return "", bare


def wire_provider(model: str) -> str:
    """The provider whose request shape a model takes. Vertex serves Gemini and Claude under its
    own name, and OpenCode Go serves chat, Messages, Responses and Gemini models behind one
    gateway (`VertexProvider` / `OpenCodeGoProvider` hand the call to the matching client)."""
    provider = provider_of(model)
    if provider == "opencode-go":
        try:
            from .catalog import models_dev

            wire = (models_dev().get(model) or {}).get("wire") or "chat"
        except Exception:
            wire = "chat"
        return {
            "gemini": "gemini",
            "messages": "anthropic",
            "responses": "openai",
            "chat": "openai",
        }.get(wire, "openai")
    if provider != "vertex":
        return provider
    family, _name = _split_routing(model)
    return {"gemini": "gemini", "claude": "anthropic"}.get(family, provider)


def levels_for(model: str, facts: Optional[dict[str, Any]] = None) -> list[str]:
    """The effort levels this model accepts, in a stable low→high order; [] = none.

    `facts` is a `catalog.facts_for` row. When omitted it is read from the catalogue
    (disk-only, hot-safe). Every failure degrades to [] — a session build must never
    pay a network call to answer "which levels does this model offer".
    """
    if facts is None:
        try:
            from .catalog import facts_for

            facts = facts_for(model) or {}
        except Exception:
            facts = {}

    options = facts.get("reasoning_options")
    if isinstance(options, list):
        effort = next(
            (o for o in options if isinstance(o, dict) and o.get("type") == "effort"),
            None,
        )
        if effort:
            values = effort.get("values")
            if isinstance(values, list):
                ordered = _order_efforts(values)
                if ordered:
                    return ordered
        budget = next(
            (
                o
                for o in options
                if isinstance(o, dict) and o.get("type") == "budget_tokens"
            ),
            None,
        )
        if budget:
            budget_levels = _budget_levels(wire_provider(model))
            if budget_levels:
                return budget_levels
        # A toggle (on/off only) is not something Reeve exposes as levels today; the
        # provider's own default stands. Falling through keeps the answer honest.
        return []

    # No catalogue statement: only families we vouch for, and only if it reasons at all.
    if facts.get("reasoning"):
        provider = wire_provider(model)
        name = _split_routing(model)[1]
        if provider in _CURATED_EFFORT:
            return list(_CURATED_EFFORT[provider])
        if provider == "anthropic":
            return list(_ANTHROPIC_BUDGETS) if _uses_anthropic_budget(name) else []
        if provider == "gemini" and name.startswith("gemini-"):
            return list(_GEMINI_BUDGETS)
    return []


def patch_for(model: str, level: Optional[str]) -> dict[str, Any]:
    """The request settings a chosen level contributes, or {} for "default"/unknown.

    A dict (not a scalar) because a provider body is rarely one field — merging the whole
    patch at one point is what lets the per-provider knowledge live here and nowhere else.
    """
    if not level or level == "default":
        return {}
    provider = wire_provider(model)  # a Vertex Gemini/Claude takes the body its family does

    if provider == "anthropic":
        budget = _ANTHROPIC_BUDGETS.get(level)
        if budget is None or not _uses_anthropic_budget(_split_routing(model)[1]):
            return {}
        return {"thinking": {"type": "enabled", "budget_tokens": budget}}

    if provider == "gemini" and level in _GEMINI_MAPPED:
        # The Gemini client maps this to a thinking budget / include_thoughts; keeping the
        # canonical name here means the catalogue's effort vocabulary needs no translation.
        return {"reasoning_effort": level}

    if level in _CANONICAL_EFFORTS and provider in _EFFORT_PROVIDERS:
        return {"reasoning_effort": level}
    return {}


def budget_for(provider: str, level: Optional[str]) -> Optional[int]:
    """The token budget a budget-shaped provider uses for `level`, or None.

    Anthropic consumes levels through `patch_for`; the Gemini client calls this directly
    because its thinking config is assembled field-by-field rather than merged.
    """
    if provider == "gemini":
        return _GEMINI_MAPPED.get(level or "")
    return None


def _order_efforts(values: list[Any]) -> list[str]:
    """Known efforts in canonical order, then unknown ones in the order given."""
    seen = [str(v) for v in values if isinstance(v, str) and v]
    known = [e for e in _CANONICAL_EFFORTS if e in seen]
    extra = [v for v in seen if v not in _CANONICAL_EFFORTS]
    # de-dup while preserving order
    out: list[str] = []
    for value in [*known, *extra]:
        if value not in out:
            out.append(value)
    return out


def _budget_levels(provider: str) -> list[str]:
    """Budget-shaped models get Reeve's named levels only where a budget is consumable."""
    if provider == "anthropic":
        return list(_ANTHROPIC_BUDGETS)
    if provider == "gemini":
        return list(_GEMINI_BUDGETS)
    return []


def _uses_anthropic_budget(model: str) -> bool:
    """The provider's own family test, imported lazily so the two cannot drift apart."""
    try:
        from .claude.thinking import uses_budget_thinking

        return bool(uses_budget_thinking(model))
    except Exception:
        return False
