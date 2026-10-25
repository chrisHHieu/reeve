"""Per-model capability probe. Two sources, in order of how much they know.

1. The provider's own catalogue (`catalog.py`), where one exists. This is not a better
   guess, it is the fact: OpenRouter states which of its models accept images and which
   accept tools, and reeve was contradicting it on 250 models.
2. The curated matrix — models we actively suggest and vouch for.

Anything else gets a conservative default (tools + streaming, nothing else). The
name-shape heuristics that used to guess from the id were removed: a hand-copied
capability ages silently, and a model that is wrongly called blind looks exactly like a
model that cannot see. Accepts bare model names (`gpt-5.5`) or provider-qualified ones
(`openai:gpt-5.5`).
"""

from __future__ import annotations

from .base import ModelCapabilities

#: Unknown models: tool calling and streaming assumed (that is what Reeve points at),
#: everything riskier left off rather than guessed.
_UNKNOWN = ModelCapabilities(
    tools=True, vision=False, pdf=False, parallel_tool_calls=False, streaming=True
)


def capabilities_for(model: str) -> ModelCapabilities:
    """The model's capabilities. Audio and video are read from `input_modalities`, the same list the
    picker shows, so what a person is told a model takes and what is sent to it cannot differ."""
    from dataclasses import replace as _replace

    from .modalities import input_modalities

    caps = _base_capabilities(model)
    try:
        modalities = input_modalities(model)
    except Exception:
        return caps
    return _replace(caps, audio="audio" in modalities, video="video" in modalities)


def _base_capabilities(model: str) -> ModelCapabilities:
    # The provider's own statement first. It is cached on disk and never reaches the
    # network on this path more than once per process; every failure returns None and
    # falls through, so an unreachable catalogue costs exactly today's behaviour.
    from .catalog import catalog, models_dev
    from .matrix import entry_for

    entry = entry_for(model)  # the hand-kept row, when the matrix has one
    if entry is None and ":" in model:
        # The matrix stores OpenAI rows bare (`gpt-5.5`), so a routed id still resolves.
        entry = entry_for(model.split(":", 1)[1])
    published = None
    try:
        published = catalog().get(model)
    except Exception:
        published = None
    if published is None:
        # models.dev covers every provider, but is read DISK-ONLY on this path: a
        # session build must not download a 4.5 MB catalogue to answer a capability
        # question. An empty cache simply falls through, which is today's behaviour.
        try:
            published = models_dev().get(model)
        except Exception:
            published = None
    if published is not None:
        base = entry.caps if entry is not None else _UNKNOWN
        # Only facts the catalogue actually states are taken. `parallel_tool_calls` and
        # `streaming` are in neither schema, so they keep whatever the matrix or the
        # conservative default decided — replacing them with a default would be
        # inventing an answer out of the absence of one.
        from dataclasses import replace as _replace

        overrides: dict[str, bool] = {}
        if "vision" in published:
            overrides["vision"] = bool(published["vision"])
        if "tools" in published:
            overrides["tools"] = bool(published["tools"])
        if isinstance(published.get("input"), list):
            # A stated modality list is the one place PDF support is a fact rather than a
            # per-provider assumption. (Audio and video come from `input_modalities`, below.)
            overrides["pdf"] = "pdf" in published["input"]
        return _replace(base, **overrides) if overrides else base
    return entry.caps if entry is not None else _UNKNOWN
