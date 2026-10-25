"""The modal that stops a turn until the person decides on a tool call."""

from __future__ import annotations

import json
from typing import Any

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Label, Static

from harness.loop import ApprovalOutcome, PermissionRequest


def short(value: Any, limit: int = 80) -> str:
    """One line of `value`, cut to `limit` characters with an ellipsis."""
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    text = text.replace("\n", "\\n")
    return text if len(text) <= limit else text[: limit - 1] + "…"


# (key, outcome, footer label, button label, button variant)
_CHOICES = (
    ("y", "once", "Approve", "Approve (y)", "success"),
    ("n", "deny", "Deny", "Deny (n)", "error"),
    ("a", "always_tool", "Always tool", "Always tool (a)", "default"),
    ("c", "always_command", "Always cmd", "Always cmd (c)", "default"),
)


class ApprovalScreen(ModalScreen[ApprovalOutcome]):
    BINDINGS = [Binding(key, f"decide('{outcome}')", label) for key, outcome, label, _, _ in _CHOICES]

    def __init__(self, request: PermissionRequest) -> None:
        super().__init__()
        self.request = request

    def compose(self) -> ComposeResult:
        asked = self.request
        arguments = ", ".join(f"{name}={short(value)}" for name, value in (asked.arguments or {}).items())
        with Vertical(id="approval"):
            yield Label("Permission required", id="approval-title")
            yield Static(f"tool:   {asked.tool_name}")
            yield Static(f"args:   {arguments or '(none)'}")
            yield Static(f"reason: {asked.reason}")
            with Horizontal(id="approval-buttons"):
                for _, outcome, _, label, variant in _CHOICES:
                    yield Button(label, id=outcome, variant=variant)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(ApprovalOutcome(event.button.id))

    def action_decide(self, outcome: str) -> None:
        self.dismiss(ApprovalOutcome(outcome))
