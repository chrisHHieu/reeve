"""What a model takes as input — one answer, for the picker and for the sender.

The model picker shows "Inputs: text, image, audio, video" and a person reads that as a promise: pick
the model, attach the file. It is only a promise if what is SHOWN and what is DONE come from the same
place. They did not: the picker read the catalogues' `input` list, while the decision to send a
recording read a second lookup (`capabilities_for`), and the two could disagree (a Gemini row the
matrix vouches for showed "text, image" until models.dev was cached, yet was sent audio).

`input_modalities` is that one place. A catalogue that lists modalities is the fact; without one, the
curated matrix and the flags the catalogues do state (`vision`, OpenRouter's audio/video list) build the
same list. `capabilities_for` reads its `audio`/`video` from here and `model_details` shows it.
"""

from __future__ import annotations


def _route_carries_media(model: str, facts: dict) -> bool:
    """Whether a file attached to this model can reach it: the provider's builder is marked
    `carries_media`, narrowed where one provider serves several wires (Vertex by family, OpenCode Go
    by each model's wire)."""
    from .registry import get_descriptor

    provider, _, rest = model.partition(":")
    if not rest:
        provider = "openai"  # a bare id routes to the OpenAI default
    descriptor = get_descriptor(provider)
    if descriptor is None or not getattr(descriptor.build, "carries_media", False):
        return False
    if provider == "vertex":
        # the adapter's own routing (family segment, else by name), so a catalogue id like
        # `vertex:gemini-3.7-flash` and a curated `vertex:gemini/gemini-3.7-flash` agree
        from .vertex import VertexProvider

        return VertexProvider._split(rest)[0] == "gemini"
    if provider == "opencode-go":
        return (facts.get("wire") or "chat") in ("chat", "gemini")
    return True


def input_modalities(model: str) -> list[str]:
    """`["text", "image", "pdf", "audio", "video"]`, whichever the model takes here. Never empty.

    "Here" matters: a catalogue says what the MODEL reads, and audio or video is listed only where the
    route in this app can carry it too (`_route_carries_media`). The picker shows this list, so it
    never promises an input the app has no way to send.
    """
    from .catalog import facts_for

    facts = facts_for(model) or {}
    out = _stated(model, facts)
    if not _route_carries_media(model, facts):
        out = [m for m in out if m not in ("audio", "video")]
    return out


def _stated(model: str, facts: dict) -> list[str]:
    from .matrix import entry_for

    listed = facts.get("input")
    if isinstance(listed, list):
        return [str(m) for m in listed]

    entry = entry_for(model)
    if entry is None and ":" in model:
        entry = entry_for(model.split(":", 1)[1])  # the matrix stores OpenAI rows bare
    caps = entry.caps if entry is not None else None

    out = ["text"]
    vision = bool(facts["vision"]) if "vision" in facts else bool(caps and caps.vision)
    if vision:
        out.append("image")
    if caps and caps.pdf:
        out.append("pdf")
    media = facts.get("media")
    if isinstance(media, list):
        out += [m for m in ("audio", "video") if m in media]
    elif caps:
        out += [m for m, on in (("audio", caps.audio), ("video", caps.video)) if on]
    return out
