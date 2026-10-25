"""Which model a conversation runs on, said in its system prompt.

A model was told it was "Reeve's coding agent" and never which model it was, so asked "which model
are you?" it guessed, and after a switch it read the replies above as its own, written by another
model. The line names the model by its picker name and id, and the models that wrote earlier replies.

It is part of the system prompt and not of a turn's live context: it is true for the whole
conversation, the prompt is logged with every change (`request/header`), and a switch
costs the prompt cache anyway, since the new model has none for this conversation.
"""

from __future__ import annotations

from typing import Iterable

#: How the line starts, so a switch can find it in a prompt and replace it.
PREFIX = "Model: you are "


def model_name(model: str) -> str:
    """The model's name as the picker shows it, or its id without the provider."""
    try:
        from harness.llm.providers.matrix import model_details

        # A curated label carries its provider ("GPT-5.6 Sol · OpenAI"); the model is the first part.
        name = str(model_details(model).get("name") or "").split(" · ")[0].strip()
    except Exception:
        name = ""
    return name or model.split(":", 1)[-1]


def model_line(model: str, earlier: Iterable[str] = ()) -> str:
    """The prompt's model paragraph: this model, and the others that answered in this conversation."""
    name = model_name(model)
    text = (
        f"{PREFIX}{name} (model id `{model}`), working inside Reeve, the app the user talks to. "
        f"Asked which model you are, you are {name}."
    )
    others = list(dict.fromkeys(model_name(m) for m in earlier if m and m != model))
    others = [other for other in others if other != name]
    if others:
        text += (
            f" Earlier in this conversation the user ran it on {', '.join(others)}: the replies "
            "written then were that model's, not yours."
        )
    return text


def with_model_line(system: str, model: str, earlier: Iterable[str] = ()) -> str:
    """`system` with its model paragraph replaced; unchanged when it has none."""
    paragraphs = system.split("\n\n")
    for i, paragraph in enumerate(paragraphs):
        if paragraph.startswith(PREFIX):
            paragraphs[i] = model_line(model, earlier)
            return "\n\n".join(paragraphs)
    return system


__all__ = ["PREFIX", "model_line", "model_name", "with_model_line"]
