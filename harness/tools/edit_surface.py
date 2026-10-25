"""The edit tools a session offers follow its model.

`apply_patch` takes Codex's `*** Begin Patch` envelope, a format OpenAI's GPT/Codex models
are trained to write. Every other model has to produce its context lines from memory, and
the real store says what that costs: after the 2026-09-25 context-miss hint shipped,
deepseek-v4.1-flash still failed 13.2% of its 53 `apply_patch` calls against 1.9% of its
214 `replace_in_file` calls — and each failure re-reads the whole context (~170k tokens a
step) to try again. 93% of those patches touched one file and 70% one hunk, so what the
envelope buys over several exact replacements in one step is rarely used. Tool choice is a
known lever (Vercel: 80% of the tools removed, success 80% → 100%): a tool a model cannot
drive well is a tool it should not be offered.

So `apply_patch` is on the surface for models trained on its format and held back for the
rest; `replace_in_file` (several calls in one step for several edits) and `write_file`
cover every edit it could make. The held tool stays on the engine, so a mid-session switch
to a GPT model puts it back and a switch away takes it off — the surface always matches
the model answering.
"""

from __future__ import annotations

import re
from typing import Any

#: Model families trained on the Codex patch envelope: GPT and the o-series, whatever
#: route serves them (`openai-codex:gpt-5.5`, `openrouter:openai/gpt-6-luna`, `gpt-5.6-sol`).
_PATCH_NATIVE = re.compile(r"^(gpt-|o\d|codex)", re.IGNORECASE)
PATCH_TOOL = "apply_patch"


def patch_native(model: str) -> bool:
    """Whether `model` belongs to a family trained on `apply_patch`'s envelope."""
    name = str(model or "").rsplit(":", 1)[-1].rsplit("/", 1)[-1].strip()
    return bool(_PATCH_NATIVE.match(name))


def fit_edit_tools(registry: Any, model: str, held: dict[str, Any]) -> dict[str, Any]:
    """Put `apply_patch` on `registry` exactly when `model` is patch-native, keeping a
    held-back spec in `held` so it can come back. Returns `held`. Never raises: a surface
    that cannot be fitted keeps the tools it has."""
    try:
        native = patch_native(model)
        spec = registry.get(PATCH_TOOL)
        if native and spec is None and PATCH_TOOL in held:
            registry.restore(held.pop(PATCH_TOOL))
        elif not native and spec is not None:
            held[PATCH_TOOL] = spec
            registry.remove(PATCH_TOOL)
    except Exception:
        pass
    return held


def sync_edit_surface(engine: Any) -> None:
    """Re-fit the engine's edit tools to its current model (every model switch)."""
    held = engine.__dict__.setdefault("_held_edit_tools", {})
    fit_edit_tools(getattr(engine, "registry", None), getattr(engine, "model", ""), held)


__all__ = ["PATCH_TOOL", "fit_edit_tools", "patch_native", "sync_edit_surface"]
