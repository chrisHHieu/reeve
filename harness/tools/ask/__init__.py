"""The `ask_user` tool: the agent asks the person a question and waits for the answer.

This is the general human-in-the-loop primitive. A question, optional quick-reply `options`, and by
default a free-text escape that is always available, plus `multi` to choose several. Like
`request_tool`, the turn engine intercepts it. The question becomes an inbox item that is
answered inline in the live session, or from the inbox when the session runs unattended. The agent
suspends until it is resolved, and the answer comes back as the tool result. The callable here only
carries the schema and is the fallback when nothing is wired to ask.

Options may be objects (`{label, description, recommended, preview}`) and not just strings, and
`questions` groups up to twelve questions into one call, drawn as a stepper below five and as one form
from five, so one round trip does the work of several. Plain-string options and the single `question` form remain valid, so older sessions
and simple asks look as they always did.
"""

from __future__ import annotations

from aisuite.agents import ToolMetadata, tool

from .questions import answer_result, normalize_option, normalize_questions, option_label, question_item_fields
from .schema import ASK_SCHEMA, MAX_GROUPED_QUESTIONS, SHORT_DESCRIPTION

__all__ = [
    "MAX_GROUPED_QUESTIONS",
    "answer_result",
    "ask_user_tool",
    "normalize_option",
    "normalize_questions",
    "option_label",
    "question_item_fields",
]


def ask_user_tool() -> object:
    def ask_user(
        question: str = "",
        options: list | None = None,
        allow_text: bool = True,
        multi: bool = False,
        header: str = "",
        questions: list | None = None,
    ) -> dict:
        """Ask the user a question and wait for their answer — use when you genuinely need a human
        decision or information you can't infer (a preference, a missing fact, a choice between real
        alternatives). Prefer this over guessing or stalling.

        Never use it to ask permission for a specific action you are about to take ("shall I
        open a PR?") — propose the action instead: the approval flow shows the user the exact
        command/arguments and does the asking, which is stronger consent than a chat yes.

        Single form returns `{"answer": "..."}` — the chosen option label(s) or the typed text.
        Grouped form (`questions`) returns `{"answers": {"<header or question>": "..."}}` — one
        entry per question. Don't ask what you can reasonably decide yourself; reserve this for
        choices that are actually the user's to make.
        """
        # The engine does the real work, since it needs the out-of-band inbox round trip. This body
        # runs only when no question asker is wired, as on a headless surface.
        return {
            "answer": "",
            "error": "asking the user isn't available in this surface",
        }

    wrapped = tool(
        ask_user,
        metadata=ToolMetadata(
            category="interaction",
            risk_level="low",
            capabilities=["ask_user"],
            description=SHORT_DESCRIPTION,
        ),
    )
    wrapped.__tool_schema__ = ASK_SCHEMA
    return wrapped
