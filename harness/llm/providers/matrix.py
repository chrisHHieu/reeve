"""Reading the curated model matrix, and joining it with the published catalogues.

The rows themselves are in ``matrix_rows.py``. This module answers the questions the rest of the
app asks of them: a model's entry, its label, its context window, the ids a provider offers, and
the facts for one row of the picker.

Where a published catalogue (OpenRouter, models.dev) and a row here disagree, the catalogue wins.
A row is a copy and copies drift: two of the eight rows that could be checked against OpenRouter
were wrong when this was written, one of them by a factor of eight.
"""

from __future__ import annotations

from typing import Any, Optional

from .matrix_rows import MATRIX, ModelEntry
from .variants import levels_for

__all__ = [
    "MATRIX",
    "ModelEntry",
    "entry_for",
    "model_context_windows",
    "model_details",
    "model_labels",
    "models_for_provider",
    "provider_model_ids",
]


def entry_for(model: str) -> ModelEntry | None:
    """The matrix row of a full model id, if it has one."""
    return MATRIX.get(model) if model else None


def model_labels() -> dict[str, str]:
    """Full id to display label, shipped to the GUI so every picker shows human names."""
    return {model_id: entry.label for model_id, entry in MATRIX.items()}


def model_context_windows() -> dict[str, int]:
    """Full id to context window, for the GUI's fill meter and for the compaction trigger.

    The catalogues are merged in and win on conflict. They are read from disk only, so this hot
    check never waits on a download. It is more than a meter: the engine's compaction config
    reads it, so an absent or wrong window costs money. A window eight times too small once made
    compaction fire eight times too early and pay for a summarizer call each time.
    """
    windows = {model_id: entry.context_window for model_id, entry in MATRIX.items() if entry.context_window}
    try:
        from .catalog import catalog, models_dev

        for store in (models_dev(), catalog()):
            try:
                published = store._load(fetch=False)
            except Exception:
                continue
            for model_id, facts in (published or {}).items():
                length = facts.get("context_length")
                if isinstance(length, int) and length > 0:
                    windows[model_id] = length
    except Exception:
        pass  # with no catalogue the rows alone answer, which is never a failure
    return windows


def models_for_provider(provider: str) -> list[str]:
    """The BARE model ids (prefix stripped) the matrix curates for a provider. They feed the
    Settings suggestions and the composer picker, which therefore stay in step with the matrix.
    OpenAI rows are stored without a prefix, since a bare id routes to the OpenAI default."""
    if provider == "openai":
        return [model_id for model_id in MATRIX if ":" not in model_id]
    prefix = provider + ":"
    return [model_id[len(prefix):] for model_id in MATRIX if model_id.startswith(prefix)]


def provider_model_ids(provider: str, *, extra: list[str] = ()) -> list[str]:
    """The BARE model ids to offer for one provider: the curated ones first, then the
    catalogue's, then `extra` (models the person added, compatibility suggestions).

    The curated rows lead because they are verified and labelled, and models.dev extends them
    with whatever the provider has released since. Ollama is the caller's own live listing and is
    deliberately not in models.dev.
    """
    ids = list(models_for_provider(provider))
    try:
        from .catalog import models_dev

        prefix = provider + ":"
        ids += [model_id[len(prefix):] for model_id in models_dev()._load(fetch=False) if model_id.startswith(prefix)]
    except Exception:
        pass
    ids += [str(model) for model in extra if model]
    return list(dict.fromkeys(ids))


def model_details(model: str) -> dict[str, Any]:
    """The facts of one picker row, from the matrix joined with the catalogues.

    None means "unknown" for every optional field, and the UI shows a dash and never a false
    "No". The input kinds, cost and release date come from the catalogues only, while the matrix
    contributes the verified label and capabilities.
    """
    from .modalities import input_modalities

    entry = MATRIX.get(model)
    try:
        from .catalog import facts_for

        facts: dict[str, Any] = facts_for(model) or {}
    except Exception:
        facts = {}
    cost = facts.get("cost") if isinstance(facts.get("cost"), dict) else None
    # The list the sender acts on, so the picker cannot promise an input the sender would not
    # send or hide one that it would.
    inputs = input_modalities(model)

    def capability(name: str) -> Optional[bool]:
        if name in facts:
            return bool(facts[name])
        return bool(getattr(entry.caps, name, False)) if entry is not None else None

    context = facts.get("context_length")
    if not isinstance(context, int) or context <= 0:
        context = entry.context_window if entry is not None else None
    pdf = "pdf" in inputs if isinstance(facts.get("input"), list) else capability("pdf")
    name = (entry.label if entry is not None else None) or str(facts.get("name") or "").strip()
    priced_free = cost is not None and cost.get("input") == 0 and cost.get("output") == 0
    return {
        "id": model,
        "name": name or model.split(":", 1)[-1],
        "provider": model.split(":", 1)[0] if ":" in model else "openai",
        "curated": entry is not None,
        "context": context,
        "output": facts.get("output_length"),
        "vision": capability("vision"),
        "tools": capability("tools"),
        "parallel_tool_calls": capability("parallel_tool_calls"),
        "pdf": pdf,
        "reasoning": bool(facts["reasoning"]) if "reasoning" in facts else None,
        # The effort levels the model accepts, low to high; an empty list shows no control. They
        # are derived in `variants.py` and never a made-up default.
        "reasoning_levels": levels_for(model, facts),
        "reasoning_default": None,
        "temperature": bool(facts["temperature"]) if "temperature" in facts else None,
        "inputs": inputs,
        "cost": cost,
        # Free is claimed only when a catalogue PRICED the model at zero. An unpriced model is
        # not free, it is unknown, which is a different badge.
        "free": bool(priced_free),
        "status": facts.get("status"),
        "release_date": facts.get("release_date"),
        "source": facts.get("source"),
    }
