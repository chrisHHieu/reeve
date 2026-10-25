"""What the engine asks a person, and the words the model is told about the answer."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Awaitable, Callable, Optional


class ApprovalOutcome(str, Enum):
    ONCE = "once"
    ALWAYS_TOOL = "always_tool"
    ALWAYS_COMMAND = "always_command"
    ALWAYS_DOMAIN = "always_domain"
    # The person edited the call before running it. The approver puts the new arguments on the
    # request and the engine validates and checks them again as a new call, so an edit never
    # inherits the consent the original question would have received.
    MODIFY = "modify"
    DENY = "deny"


@dataclass
class PermissionRequest:
    tool_name: str
    arguments: dict[str, Any]
    metadata: Any
    reason: str
    tool_call_id: Optional[str] = None  # lets a parked request resume as the same inbox item
    # What the tool's contract says about this call (tools/contract.py), already computed for the
    # live card. It travels with the request so an approver that parks it (the inbox, a reconnect,
    # a restart) shows the same sentence and does not rebuild one from the tool name, which is how
    # the parked card came to read "Run `tool`?".
    presentation: Optional[dict[str, Any]] = None
    # The payload the live card was drawn from (the PERMISSION_REQUIRED event). A parked prompt
    # keeps it, so coming back to a session waiting on an approval shows the same card, with
    # "Always allow", "Edit" and the read-only offer, and not a thinner one built from the tool
    # name and arguments.
    card: Optional[dict[str, Any]] = None
    # A refusal in the approver's own words for the model. Without it the engine's default
    # declined-by-the-person text is used. A broker that refuses on behalf of a child sets this so
    # its reply reaches the child unchanged.
    denial_message: str = ""
    # Set by an approver returning `ApprovalOutcome.MODIFY`: the arguments the person wants run in
    # place of the model's. The engine treats them as a new call, validated and checked against the
    # floors and the preset again, so this feeds a decision and is never a permission of its own.
    edited_arguments: Optional[dict[str, Any]] = None


Approver = Callable[[PermissionRequest], Awaitable[ApprovalOutcome]]


async def deny_all(_request: PermissionRequest) -> ApprovalOutcome:
    return ApprovalOutcome.DENY


# What the model is told when a person declines. A bare "denied by user" gave it three words and
# no instruction, and it did the sensible thing: it tried another route, sometimes a near variation
# of the same call. A refusal needs instructions attached; a person's refusal is the stronger
# signal and was carrying none.
USER_DENY_MESSAGE = (
    "the user declined this action. Do not retry it or attempt a variation of it. If "
    "there is a genuinely different way to make progress on their request, take it; "
    "otherwise stop and tell them plainly what you could not do."
)

# Stop pressed while an approval card is on screen. That already declined the action and ended the
# turn, which was never the gap. The gap was that the model read "interrupted by user" and could not
# tell an abandoned turn from a refused action, so on the next turn it proposed the same thing
# again. Here the interrupt is an answer to a question about one specific action, and saying so is
# the whole difference.
USER_STOP_MESSAGE = (
    "the user stopped the turn at the approval prompt for this action, which declines "
    "it. Do not retry it, do not look for a way around it, and do not start something "
    "else."
)

# The person declined to repeat a call that was already in flight when the session stopped. This
# says what the ordinary refusal does not: the action may have taken effect. Told only that it was
# declined, the model reports "I did not send it", a confident claim about the one case where
# nobody knows.
#: The same call in full access, which asks nobody: it is not re-run, and the model
#: decides. A call it makes again is a new call and runs.
UNCERTAIN_RETRY_MESSAGE = (
    "not re-run. This call was already under way when the session stopped, so it may "
    "have taken effect. Check whether it happened if you have a way to check; call it "
    "again only if it did not."
)

UNCERTAIN_SKIP_MESSAGE = (
    "not re-run. This call was already under way when the session stopped, so it may "
    "have taken effect — nobody knows, including the user. Do not simply try again. "
    "Check whether it happened if you have a way to check; if you do not, say so "
    "plainly and let the user decide."
)

# A `modify` outcome carrying nothing usable is answered like a refusal: there is no call to run,
# and inventing one would be worse than saying so.
EDIT_EMPTY_MESSAGE = (
    "the edited call arrived with no arguments, so nothing was run. Re-issue the call "
    "with the parameters you intended."
)

# The start of "the edited call was judged again and refused". The reason after it names the rule
# that refused, so the model learns which part of the edit was the problem.
EDIT_NOT_RUN_MESSAGE = "your edited version of this call was not run:"
