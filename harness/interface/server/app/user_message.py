"""Turning a `user_message` frame into what a turn is run with, or a reason to refuse it.

Every refusal is an `input_rejected` frame, never a provider error: it must not offer "Retry"
or flush a half-streamed reply in the GUI.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from .limits import MAX_MENTIONS, MAX_MESSAGE_CHARS
from .uploads import attachment_problem


class Rejection(Exception):
    """The frame is refused; the message is what the person is told."""


@dataclass
class UserMessage:
    text: str
    attachments: list = field(default_factory=list)
    model: Optional[str] = None
    display: Optional[str] = None  # what the transcript shows when it differs from `text`
    mentions: list[str] = field(default_factory=list)
    steer: bool = False
    # The slash command the message was sent with, as the transcript draws it.
    command: Optional[dict[str, str]] = None


def _string_or_none(value: Any, what: str) -> Optional[str]:
    if value is not None and not isinstance(value, str):
        raise Rejection(f"Invalid {what}: expected a string.")
    return value


def _steering(message: dict, text: str) -> bool:
    """A steer splices a sentence into the running turn at its next step. It is text only:
    attachments belong to a turn that will read them, and a skill or @mention frames a NEW turn
    (mentions are turn-scoped), so none of them may ride a splice."""
    steer = bool(message.get("steer"))
    if not steer:
        return False
    if message.get("attachments") or message.get("skill") or message.get("mentions"):
        raise Rejection("Steering takes plain text — send files, a skill or @mentions as a queued message instead.")
    if not text:
        raise Rejection("Steering needs some text.")
    return True


#: How long each field of a command's row may be: a name, what was typed after it, a line about it.
COMMAND_FIELDS = {"name": 80, "args": 4000, "description": 300, "source": 40}


def _command(value: Any) -> Optional[dict[str, str]]:
    """The slash command a message was sent with: `/init`, `/review`, a command a
    project defines. The composer expanded it into the text the model reads; this row is only
    how the transcript draws it, so a malformed one is dropped, never a reason to refuse."""
    if not isinstance(value, dict) or not isinstance(value.get("name"), str) or not value["name"].strip():
        return None
    row = {"kind": "prompt"}
    for key, cap in COMMAND_FIELDS.items():
        field_value = value.get(key)
        if isinstance(field_value, str) and field_value.strip():
            row[key] = field_value.strip()[:cap]
    return row


def _with_skill(manager, session_id: str, workspace, skill: Any, text: str) -> tuple[str, str]:
    """Force-run a skill picked in the composer. It must be on the session's menu: a muted or
    unknown one is a visible error and never a silent no-op."""
    if not isinstance(skill, str) or not skill.strip():
        raise Rejection("Invalid skill: expected a name.")
    skill = skill.strip()
    if skill not in manager.effective_skill_names(session_id, workspace):
        raise Rejection(f"Skill '{skill}' is not available in this session.")
    # A skill marked model-only is hidden from the `/` list; this is the check that keeps a
    # stale client from running it anyway.
    policy = manager.skill_invocation(skill, session_id, workspace)
    if policy is not None and policy.get("user_invocable") is False:
        raise Rejection(f"Skill '{skill}' is used by the worker on its own — it can't be run from here.")
    framed = (
        f'Use the skill "{skill}" for this request: first call load_skill("{skill}") '
        "and follow its instructions." + (f"\n\n{text}" if text else "")
    )
    return framed, f"/{skill}" + (f" {text}" if text else "")


def _with_mentions(manager, workspace, agent: str, mentions: Any, text: str) -> tuple[str, list[str]]:
    """@agent names the composer recognised. They add a model-facing instruction to call the
    `task` tool, and mark the turn so that tool skips its spawn prompt. An unknown, primary or
    hidden name is a visible error, the same rule skills follow."""
    if not isinstance(mentions, list) or any(not isinstance(name, str) for name in mentions):
        raise Rejection("Invalid mentions: expected a list of agent names.")
    names = [name.strip() for name in mentions if name.strip()]
    if not names:
        raise Rejection("Invalid mentions: no agent named.")
    if len(names) > MAX_MENTIONS:
        raise Rejection(f"Too many mentions ({len(names)}; limit {MAX_MENTIONS}).")
    plan = manager.mention_instructions(workspace, agent, names)
    if not plan.get("ok"):
        raise Rejection(str(plan.get("error") or "Invalid mention."))
    return text + ("\n\n" if text else "") + str(plan["text"]), names


def read_user_message(manager, session_id: str, workspace, agent: str, message: dict) -> UserMessage:
    """Validate a `user_message` frame. Raises `Rejection`."""
    raw = message.get("text")
    raw = "" if raw is None else raw
    if not isinstance(raw, str):
        raise Rejection("Invalid message text: expected a string.")
    text = raw.strip()
    steer = _steering(message, text)

    attachments = message.get("attachments")
    attachments = [] if attachments is None else attachments
    if not isinstance(attachments, list):
        raise Rejection("Invalid attachments: expected a list.")
    if len(text) > MAX_MESSAGE_CHARS:
        raise Rejection(f"Message too long ({len(text)} chars; limit {MAX_MESSAGE_CHARS}).")
    problem = attachment_problem(attachments)
    if problem:
        raise Rejection(problem)

    # The composer sends its visible model with every message. The first binds the session
    # (safe across reconnects), a later one may switch it with a notice.
    model = _string_or_none(message.get("model"), "model")

    display = None
    command = None if steer else _command(message.get("command"))
    skill = message.get("skill")
    if skill is not None:
        typed = text
        # The model sees the framing; the transcript shows the person's own "/name …" line.
        text, display = _with_skill(manager, session_id, workspace, skill, text)
        # A skill's row is the server's (it checked the name); the composer may add its line.
        said = command or {}
        command = {
            "kind": "skill",
            "name": skill.strip(),
            **({"args": typed} if typed else {}),
            **({"description": said["description"]} if said.get("description") else {}),
        }

    names: list[str] = []
    if message.get("mentions") is not None:
        shown = text
        text, names = _with_mentions(manager, workspace, agent, message["mentions"], text)
        # The bubble is what the person typed; the instruction is only ever for the model.
        display = display if display is not None else shown
    return UserMessage(text, attachments, model, display, names, steer, command)
