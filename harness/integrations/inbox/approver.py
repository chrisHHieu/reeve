"""An approver that sends a permission request to the inbox and waits for a person's answer."""

from __future__ import annotations

from .store import InboxStore


def inbox_approver(store: InboxStore, session_id: str, *, inbox: str = "default"):
    """Allow becomes ONCE, always becomes ALWAYS_TOOL, anything else is a denial."""
    from harness.loop import ApprovalOutcome, PermissionRequest

    outcomes = {"always": ApprovalOutcome.ALWAYS_TOOL, "allow": ApprovalOutcome.ONCE}

    async def approve(request: "PermissionRequest") -> "ApprovalOutcome":
        item = store.add_approval(
            session_id, title=f"Run `{request.tool_name}`?", body=request.reason or "", inbox=inbox
        )
        return outcomes.get(await store.wait(item.id), ApprovalOutcome.DENY)

    return approve
