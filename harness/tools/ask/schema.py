"""What the model is told about `ask_user`: its parameters and how it describes itself.

The wording is read by the model, so change it only with evidence from a run."""

from __future__ import annotations

# One call asks at most this many. Up to FORM_FROM - 1 are stepped through one at a time; from
# FORM_FROM the card is one form answered in any order, so a brief of ten questions is
# one screen and one Send. It was 4 when every group was a stepper.
MAX_GROUPED_QUESTIONS = 12
#: From this many grouped questions the GUI draws a form (`surfaces/gui/src/components/questionAnswers.ts`).
FORM_FROM = 5

# An option is a plain string or an object. `label` is what the person picks and what comes back as
# the answer. `description` is drawn under it. `recommended` adds the green tag, so list that option
# first. `preview` is monospace text for the side pane (code, config, an ASCII mockup, SQL), and
# once any option has one the card switches to two panes.
OPTION_SCHEMA = {
    "anyOf": [
        {"type": "string"},
        {
            "type": "object",
            "properties": {
                "label": {"type": "string"},
                "description": {"type": "string"},
                "recommended": {"type": "boolean"},
                "preview": {"type": "string"},
            },
            "required": ["label"],
        },
    ]
}

# Written out in full, the same way `todo.py` does it: a string-or-object union and a nested array
# of questions cannot be generated from the signature reliably.
ASK_SCHEMA = {
    "type": "function",
    "function": {
        "name": "ask_user",
        "description": (
            "Ask the user one or more questions and wait for their answer. Use for decisions or "
            "information only the user can provide. Do not use it to ask permission for a "
            "specific action you are about to take — propose the action instead; the approval "
            "flow shows the user exactly what would run and does the asking. Group related "
            f"questions (up to {MAX_GROUPED_QUESTIONS}) into one call via `questions` instead "
            f"of asking serially; from {FORM_FROM} they are shown as one form the user fills in "
            "any order. Give grouped questions options with the one you would pick marked "
            "`recommended`: it starts selected, and a question the user leaves comes back as "
            "theirs to leave to you — then choose, and say what you chose."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "The full question, in plain language (single-question form).",
                },
                "options": {
                    "type": "array",
                    "items": OPTION_SCHEMA,
                    "description": (
                        "Optional quick-reply choices: plain strings, or objects with `label` "
                        "(required — this is the answer value), `description` (why/when to pick "
                        "it), `recommended` (green tag; list that option first), and `preview` "
                        "(monospace text — code, a table, a quote, a mockup — shown in a side pane)."
                    ),
                },
                "allow_text": {
                    "type": "boolean",
                    "description": (
                        "Keep a free-text answer available even when options exist (default true; "
                        "the \"Other / type your own\" escape). Set false only when the options "
                        "are exhaustive."
                    ),
                },
                "multi": {
                    "type": "boolean",
                    "description": "Allow the user to pick more than one option.",
                },
                "header": {
                    "type": "string",
                    "description": "Short (≤ ~12 char) chip label for the card, e.g. \"Region\".",
                },
                "questions": {
                    "type": "array",
                    "maxItems": MAX_GROUPED_QUESTIONS,
                    "items": {
                        "type": "object",
                        "properties": {
                            "question": {"type": "string"},
                            "header": {
                                "type": "string",
                                "description": (
                                    "Short (≤ ~12 char) label — names this step in the stepper "
                                    "chips and keys its answer in the result."
                                ),
                            },
                            "options": {"type": "array", "items": OPTION_SCHEMA},
                            "allow_text": {"type": "boolean"},
                            "multi": {"type": "boolean"},
                        },
                        "required": ["question"],
                    },
                    "description": (
                        f"Grouped form: up to {MAX_GROUPED_QUESTIONS} questions asked in ONE "
                        f"round-trip: a stepper below {FORM_FROM}, one form from {FORM_FROM}. When "
                        "set, the singular question/options fields are ignored."
                    ),
                },
            },
            "required": [],
        },
    },
}

SHORT_DESCRIPTION = (
    "Ask the user a question (free-text or multiple-choice) and wait for their answer. "
    "Use for decisions or information only the user can provide — never to ask "
    "permission for a specific action; propose the action and let the approval flow ask."
)
