"""`propose_plan` and `update_plan`: presenting a plan for approval, and keeping a working draft.

`propose_plan` is registered for every session. It works in every permission mode (decision
0005). Approval is the consent, so a session running in bypass can lay out a plan before it touches
anything. Plan mode is not needed to propose. It is the read-only rehearsal, and approving there is
how it ends.

As with `request_tool`, the turn engine intercepts the call. It emits PLAN_PROPOSED, waits for
the person's decision out of band, and reports the outcome, including the mode the session now runs
in when the approver chose one. A rejection returns the person's feedback so the agent can revise.
The callables here only carry the schema, and answer when no approver is wired.
"""

from __future__ import annotations

from aisuite.agents import ToolMetadata, tool


def propose_plan_tool() -> object:
    def propose_plan(plan: str) -> dict:
        """Present a plan to the user and ask to go ahead. Use it for ANY multi-step job
        where the approach deserves a yes before you start — editing code, writing or
        restructuring documents, research, money and administrative work, or a sequence of
        actions across connected apps — and especially when the plan touches no files at
        all (a plan is not an attachment to file edits). Say what you'll do, in what order,
        what you'll touch, and how you'll check it worked. You do not need to be in plan
        mode. If approved, carry it out; if rejected, revise using the feedback in the
        result. In full access nobody is asked: the plan is kept and you go ahead at once.
        Don't narrate the steps as if you were already taking them — propose first.
        """
        # The engine answers this call itself: it waits for the person's decision outside the
        # turn. This body answers only where nobody can approve, such as a surface with no UI.
        return {"approved": False, "error": "plan approval isn't available in this surface"}

    return tool(
        propose_plan,
        metadata=_planning(
            "Put a plan to the user before multi-step work (code, documents, research, actions in "
            "connected apps) and wait for a yes. Any permission mode; in plan mode the yes ends the "
            "read-only rehearsal and the work starts."
        ),
    )


def _planning(description: str) -> ToolMetadata:
    return ToolMetadata(category="planning", risk_level="low", capabilities=["plan"], description=description)


def update_plan_tool() -> object:
    def update_plan(content: str) -> dict:
        """Write your working plan to this session's plan file. Use it while you explore:
        build the plan in sections instead of holding it all in one answer, then keep it
        current as you learn more. Send the FULL plan each call — it REPLACES the file, so
        a partial call loses what is not in it (the same rule as todo_write). The file is
        readable with read_file and survives compaction, so after a compaction read it
        before continuing. This is not the approval step: when the plan is ready, present
        it with propose_plan.
        """
        # The engine does the real work, since it owns the session's plan-file path. This body runs
        # only when the tool is wired outside a real session.
        return {"updated": False, "error": "plan files aren't available here"}

    return tool(
        update_plan,
        metadata=_planning(
            "Write this session's plan file while you plan, the whole plan each time: it replaces "
            "the file. read_file opens it and it outlives compaction. propose_plan, once it is ready."
        ),
    )


__all__ = ["propose_plan_tool", "update_plan_tool"]
