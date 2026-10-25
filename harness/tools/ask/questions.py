"""Turning what the model sent into the shapes the inbox, the cards and the channels all use."""

from __future__ import annotations

import json
from typing import Any, Optional

from .schema import MAX_GROUPED_QUESTIONS


def normalize_option(option: Any) -> dict:
    """An option as `{label, description, recommended, preview}`. A plain string becomes a label with
    the rest empty. The label doubles as the answer value in buttons, pills and resolutions."""
    if isinstance(option, dict):
        return {
            "label": str(option.get("label", "")),
            "description": str(option.get("description", "")),
            "recommended": bool(option.get("recommended", False)),
            "preview": str(option.get("preview", "")),
        }
    return {"label": str(option), "description": "", "recommended": False, "preview": ""}


def option_label(option: Any) -> str:
    """The answer value, and the button text, of an option given as a string or an object."""
    return str(option.get("label", "")) if isinstance(option, dict) else str(option)


def normalize_questions(raw: Any) -> list[dict]:
    """The grouped `questions` argument, capped, with blank entries dropped. Each comes out as
    `{question, header, options, allow_text, multi}` with options already normalized."""
    questions: list[dict] = []
    for entry in list(raw or [])[:MAX_GROUPED_QUESTIONS]:
        if not isinstance(entry, dict):
            continue
        text = str(entry.get("question", "")).strip()
        if not text:
            continue
        questions.append(
            {
                "question": text,
                "header": str(entry.get("header", "")),
                "options": [normalize_option(option) for option in entry.get("options") or []],
                "allow_text": bool(entry.get("allow_text", True)),
                "multi": bool(entry.get("multi", False)),
            }
        )
    return questions


def question_item_fields(args: dict) -> Optional[dict]:
    """The keyword arguments `InboxStore.add_question` takes, from the raw `ask_user` arguments, or
    None when nothing was asked.

    A grouped call also fills the single-question fields from its first question. Surfaces that only
    know one question (channel mirrors, readers of older stored items) then show something sensible."""
    grouped = normalize_questions(args.get("questions"))
    if grouped:
        lead = grouped[0]
        return {
            "title": lead["question"],
            "options": lead["options"],
            "allow_text": lead["allow_text"],
            "multi": lead["multi"],
            "header": lead["header"],
            "questions": grouped,
        }
    question = str(args.get("question", "")).strip()
    if not question:
        return None
    return {
        "title": question,
        # Strings pass through, so a simple ask keeps drawing as pills. Objects are filled in, so
        # nothing downstream meets a half-complete one.
        "options": [
            option if isinstance(option, str) else normalize_option(option)
            for option in args.get("options") or []
        ],
        "allow_text": bool(args.get("allow_text", True)),
        "multi": bool(args.get("multi", False)),
        "header": str(args.get("header", "")),
        "questions": [],
    }


def answer_result(item_questions: list, resolution: Optional[str]) -> dict:
    """The tool result for `ask_user`, from how the inbox item was resolved.

    A grouped item resolves to a JSON object keyed by header or question, which becomes
    `{"answers": {...}}`. Anything else gives the plain `{"answer": text}`."""
    if not item_questions:
        return {"answer": resolution or ""}
    try:
        parsed = json.loads(resolution or "")
    except (ValueError, TypeError):
        parsed = None
    if isinstance(parsed, dict):
        return {"answers": {str(key): str(value) for key, value in parsed.items()}}
    if resolution:
        # A text-only surface, such as a mirrored channel, can answer only once. That answer is given
        # to the first question and not lost.
        first = item_questions[0] if isinstance(item_questions[0], dict) else {}
        key = str(first.get("header") or first.get("question") or "answer")
        return {"answers": {key: str(resolution)}}
    return {"answer": ""}
