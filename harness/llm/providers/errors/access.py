"""A plain sentence for "your account can't use this model" and "your quota is gone".

The picker defaults to new flagships, and not every account can use them: a vendor may roll a model
out per organization, and calls are rejected once quota or credit runs out. Those failures arrive as
terse SDK exceptions wrapping JSON bodies, and this maps the well-known shapes to one actionable
sentence. Anything unrecognized gives None and the caller shows the raw error unchanged.
"""

from __future__ import annotations

from typing import Optional

from .markers import NO_ACCESS, NO_QUOTA, has_any


def friendly_model_error(model: str, exc: Exception) -> Optional[str]:
    """One actionable sentence for a model-access or quota failure, or None."""
    text = str(exc).lower()
    if has_any(text, NO_QUOTA):
        return (
            f"Your account is out of quota for {model} — add credits or raise the limit "
            "in the provider's billing console, or pick a different model."
        )
    no_access = (
        f"Your account doesn't have access to {model} — new models can roll out "
        "gradually or require a plan upgrade. Pick a different model, or check "
        "the provider's console for availability."
    )
    if has_any(text, NO_ACCESS):
        return no_access
    # Anthropic's 404 body is only "model: <id>" under type not_found_error. Both halves are
    # required, so an unrelated 404 (bad base_url, deleted resource) keeps its raw message.
    if "not_found_error" in text and f"model: {model.split(':')[-1].lower()}" in text:
        return no_access
    return None
