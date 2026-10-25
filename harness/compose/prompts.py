"""Text the assembly adds to a session's instructions or to a turn's live context."""

from __future__ import annotations

from typing import Any

# The session's permissions as one line of the live context, for every preset: what the person
# chose and what it means for the next call, stated from the session's side. It says where the
# person stands, not which commands exist: the engine judges each call, and a refused call's
# result says why (`guards.read_only_mode`). It replaces a read-only reminder that listed commands
# and told the model to describe a change instead of trying (amended 2026-10-23), while
# workspace-write and full access said nothing, so a model in full access was never told that no
# approval stands between a call and its effect.
PERMISSIONS_CONTEXT = {
    "read-only": (
        "Permissions: read only. The user chose to have nothing changed in this session: reads "
        "run, and anything that would change something is refused."
    ),
    "workspace-write": (
        "Permissions: workspace write. The user approves each change: reads run on their own, and "
        "a call that changes something, goes to the web or reaches another folder waits for their "
        "yes."
    ),
    "danger-full-access": (
        "Permissions: full access. Nobody is asked in this session: what you run happens at once, "
        "in other folders and connected apps too, and only refusals remain. No approval stands "
        "between a call and its effect, so change things only when the user asked for a change."
    ),
}


def permissions_context(mode: str) -> str:
    """The live context's permissions line for a preset's wire value; empty for an unknown one."""
    return PERMISSIONS_CONTEXT.get(mode, "")


# Added each turn while the plan overlay is active. The overlay can flip mid-session (the `/plan`
# command, plan approval), so this cannot live in the static instructions.
PLAN_MODE_CONTEXT = """\
Plan mode is active: nothing that changes anything runs until the plan is approved; reads
run. Explore, then commit to an approach. When you have one, present it with `propose_plan` — and a plan is not
only for code: say what you'll do, in what order, what you'll touch, and how you'll check it
worked. Don't describe actions as if you were taking them. If the plan is approved, this same
session switches to execution and you carry it out; if rejected, revise it using the feedback."""

# The GUI interleaves these status lines with humanized tool rows inside a collapsed
# "turn", and they are what the person reads while the agent works. It is added for every persona,
# and a model that ignores it degrades to a turn with no narration.
NARRATION_GUIDANCE = """\
Narration: before each batch of tool calls, write ONE short plain sentence saying what \
you're doing and why (e.g. "Checking what merged since yesterday's digest."). It is shown \
to the user as live progress. Don't narrate trivial single-call follow-ups, don't repeat \
the previous line, and never let narration replace your final answer."""


def remote_environment_context(environment: dict[str, Any], root: str) -> str:
    """What the model must know about an SSH world: the host, the anchor and the boundary.

    There is deliberately no local-path vocabulary. The point of the block is that every path in
    the transcript resolves on the remote host, and a local-looking path the model invents is not
    reachable. `root` is the anchor the world resolves relative paths against."""
    name = str(environment.get("name") or "").strip() or "remote host"
    host = str(environment.get("host") or "").strip()
    where = f"{name} ({host})" if host else name
    return (
        f"Remote environment: this session works on {where} over SSH. The workspace is "
        f"{root}; relative paths resolve there, and every file, shell and terminal tool "
        "reads and writes on that host — not on the machine Reeve runs on. Commands "
        "run in a fresh POSIX shell per call; `$REEVE_SCRATCH`, `$REEVE_WORKSPACE` "
        "and `$REEVE_SESSION` are exported for each command. The local machine's "
        "paths, tools and files are not reachable from this session."
    )
