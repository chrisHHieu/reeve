"""The guard stage: floors that run before any mode, allowance or grant can say yes.

The floors are a stage, not `if` statements inside the policy. Two questions are mixed in a
permission decision: does the person consent (modes, prompts), and what must never run whatever the
person says (floors). The stage before the policy is monotonic: a guard may deny or abstain and
cannot grant, so nothing after it (a mode, an allow-list, a session grant) can turn a refusal into
permission just by being ordered later.

The guards combine as a union of restrictions: all run, deny outranks human-only, and human-only
outranks abstain. A human-only verdict is an ask, and full access asks nobody: the
engine turns it into an allow there and keeps every deny. The outcome does not depend on registration order, though the reason reported
follows it. A verdict can be tightened downstream (a human ask in a read-only mode becomes a plain
deny) and cannot be loosened: `GuardVerdict` has no way to say allow, and the policy stage only runs
once every guard has abstained.

Deliberately not a guard: "the write target is outside the session's roots". Reach
geometry still answers it, but through `out_of_root_writes` in the policy stage, because an
out-of-root path is a decision the preset and the person's grants make, not a floor. Making it a
guard would again make every out-of-root write impossible to approve. An explicitly read-only root
is the other case, and `read_only_root` keeps it: someone said "not here", and that outranks the
preset.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable, Optional

from ..boundary import read_paths, reads_files, write_paths
from ..risk import WEB_RESEARCH_TOOLS, RiskClass, is_consequential
from .modes import Mode

if TYPE_CHECKING:
    from .engine import PermissionEngine

# Tools that grant authority outliving this session: instructions the agent will follow in later
# conversations, or a task that runs by itself afterwards. No automatic path clears these, which is
# the same floor as deferred-execution files and for the same reason: the effect arrives after the
# conversation that authorised it, so the person who bears it is not there. `create_scheduled_task`
# says in its own comment that the human granted the permissions by approving the gated call, and
# this keeps that true.
#
# `update_` is here because it can rewrite the instructions and schedule of a task the person
# already approved while keeping its grants, and `delete_` because tampering with standing
# configuration they set up is the same harm in reverse. Narrowing an update is floored along with
# widening it, because telling the two apart means judging intent, which a floor exists to avoid.
PERSISTENT_AUTHORITY_TOOLS = {
    "save_skill",
    "create_scheduled_task",
    "update_scheduled_task",
    "delete_scheduled_task",
}

# The write tools whose target paths are inside the patch text.
_PATCH_TOOLS = ("apply_patch", "apply_unified_diff")
_UNREADABLE_PATCH = (
    "this {tool} names no file Reeve can read: a patch needs real line breaks and its "
    "file headers (`*** Update File: <path>` for apply_patch, `--- a/<path>` / "
    "`+++ b/<path>` for a unified diff). If the newlines were sent as the two characters "
    "\\n, send the patch again with real line breaks."
)


@dataclass(frozen=True)
class GuardVerdict:
    """A binding verdict from before the policy. `deny` refuses outright and no approval can
    follow. `human` refuses every automatic path (mode, allow-list, session grant) and puts a
    person on it. A guard that would grant says nothing: it abstains and the policy decides.

    `axis` names the question the guard answered: "boundary" (what the action can reach: Reeve's
    own settings files, an unlocatable write target, a read-only world) or "consent" (who must see
    it: deferred-execution files, persistent authority). The registry stamps it from the guard
    function's `axis` attribute and a guard never sets it, so each guard is classified in one
    place."""

    kind: str  # "deny" or "human"
    reason: str
    axis: str = "consent"

    @staticmethod
    def deny(reason: str) -> "GuardVerdict":
        return GuardVerdict("deny", reason)

    @staticmethod
    def human(reason: str) -> "GuardVerdict":
        return GuardVerdict("human", reason)


# A guard sees the call and its risk class and answers for its own kind of escalation. Its `axis`
# attribute says which question it answers.
GuardFn = Callable[["PermissionEngine", str, dict[str, Any], Any, RiskClass], Optional[GuardVerdict]]


def self_protection(engine, tool_name, arguments, metadata, risk) -> Optional[GuardVerdict]:
    """Reeve's own settings files. The escalation this stops works in the default mode: approve one
    ordinary-looking command, it quietly appends to the rule file, and every later session is more
    permissive. So it cannot be a property of a mode or a sandbox, and no click by a person in the
    flow may grant it either: loosening means editing the files out of band."""
    if risk not in (RiskClass.WRITE_LOCAL, RiskClass.EXEC):
        return None
    hit = engine.boundary.settings_hit(tool_name, arguments, risk is RiskClass.EXEC)
    if hit is not None:
        return GuardVerdict.deny(f"refusing to modify Reeve's own settings: {hit}")
    return None


self_protection.axis = "boundary"


def secret_reads(engine, tool_name, arguments, metadata, risk) -> Optional[GuardVerdict]:
    """Reeve's credentials files, read through a file tool. What a read returns goes into the
    provider request and the session log, and a key belongs in neither. A deny in every mode,
    full access included: file tools now reach outside the session's folders, and this
    is the one file out there no grant opens."""
    if not reads_files(tool_name):
        return None
    paths, located = read_paths(tool_name, arguments)
    if not located:
        return None
    hit = engine.boundary.secret_hit(paths)
    if hit is not None:
        return GuardVerdict.deny(f"refusing to read Reeve's credentials file: {hit}")
    return None


secret_reads.axis = "boundary"


def out_of_root_reads(engine, tool_name, arguments) -> list[str]:
    """The located paths a file-reading tool would look at outside every root.

    Like `out_of_root_writes`, this is not a floor: reading another folder on the person's own
    machine is ordinary work (a sibling repository, a downloaded file), so the preset and the
    person's grants decide it, and the reach is named so the card can offer the folder."""
    if not reads_files(tool_name):
        return []
    paths, located = read_paths(tool_name, arguments)
    if not located:
        return []
    return engine.boundary.outside_roots(paths)


def read_only_mode(engine, tool_name, arguments, metadata, risk) -> Optional[GuardVerdict]:
    """In a read-only preset the world does not accept consequential actions. That is a boundary
    verdict, so a floor registered later cannot be read as weakening it and the hard deny (not an
    ask) holds in any future arrangement. The plan overlay (`engine.plan`) raises the same floor on
    top of any preset: `/plan` on full access still refuses to touch anything, which is the
    rehearsal the command promises.

    Egress that is research (`web_search`, `web_fetch`) is the one consequential class these modes
    do not refuse outright. The call steps past this guard and reaches the consent stage, where a
    card names the exact query or URL, or a standing domain grant or the session's tool grant
    applies. A silent allow would be a way to send data out, since reads are permitted and file
    contents could ride a URL the model chose, so the guard steps aside and never waves it through.
    The enrichment lookups are egress too and stay refused: their payload is a third party's
    details, and "do nothing consequential" has to include not sending someone's name to a broker."""
    if risk is RiskClass.EGRESS and tool_name in WEB_RESEARCH_TOOLS:
        return None
    # The auto-memory folder holds the agent's own notes and sits outside every workspace. The
    # memory tool this replaced was low risk and saved in these modes too, for
    # "remember that…" in a discuss session. A read-only mode protects the work, not the notebook,
    # and the later guards still judge the write.
    if engine.is_memory_write(tool_name, arguments, risk):
        return None
    # A shell command that only reads inside the roots changes nothing, so these modes refusing it
    # refused the `git log` a plan is made from. It steps past to the consent stage,
    # where a subagent's ruleset can still deny it and the engine allows it as a read.
    if engine.reads_in_roots(tool_name, arguments):
        return None
    if (engine.mode is Mode.READ_ONLY or engine.plan) and is_consequential(risk):
        # The model reads this as the call's result, and it is where the preset says what to do
        # next ("read-only mode is read-only" said neither why nor what then).
        if engine.plan:
            return GuardVerdict.deny("plan mode is read-only until the plan is approved — put this change in the plan")
        return GuardVerdict.deny("read-only: the user chose to have nothing changed in this session — say what you would change instead")
    return None


read_only_mode.axis = "boundary"


def unscoped_write(engine, tool_name, arguments, metadata, risk) -> Optional[GuardVerdict]:
    """A write whose path cannot be located cannot be scoped to the roots, so it goes to a person
    and does not slip through an automatic allowance unchecked."""
    if risk is not RiskClass.WRITE_LOCAL:
        return None
    _paths, located = write_paths(tool_name, arguments)
    if located:
        return None
    if tool_name in _PATCH_TOOLS:
        # A patch with no file header the parser can read cannot be applied either. The usual cause
        # is newlines sent escaped (a backslash and an `n` as text). Asking a person to judge where
        # it writes, which nobody can tell, cost a prompt in every mode, full access included, and
        # the model can fix the call itself.
        return GuardVerdict.deny(_UNREADABLE_PATCH.format(tool=tool_name))
    return GuardVerdict.human("cannot determine the write path to scope")


unscoped_write.axis = "boundary"


def out_of_root_writes(engine, tool_name, arguments, risk) -> list[str]:
    """The located write targets outside every writable root.

    A path outside the roots is left to the permission rules, because a sibling repository the
    person asked for is a legitimate target. That choice is kept in the policy stage and not here,
    so the preset decides it as it decides every other consequential action: read-only refuses,
    `workspace-write` asks, full access runs, and a standing grant covers it. The reach is still
    named so the approval card can say which folder is outside the session; "requires approval"
    alone would not say what to add as a root. An unlocatable write never gets here:
    `unscoped_write` owns it."""
    if risk is not RiskClass.WRITE_LOCAL:
        return []
    paths, located = write_paths(tool_name, arguments)
    if not located:
        return []
    return [path for path in paths if not engine.boundary.under_writable_root(path)]


def read_only_root(engine, tool_name, arguments, metadata, risk) -> Optional[GuardVerdict]:
    """A folder the person added as read-only stays read-only in every preset: the mark is a
    constraint they wrote, not a default the preset may overrule. This is not the out-of-root case.
    A path outside every root is an ordinary ask, while an explicitly non-writable root
    is a refusal. The difference is whether anyone said "not here" or only "here"."""
    if risk is not RiskClass.WRITE_LOCAL:
        return None
    paths, located = write_paths(tool_name, arguments)
    if not located:  # unscoped_write owns that case
        return None
    for path in paths:
        if engine.boundary.in_read_only_root(path):
            return GuardVerdict.deny(f"path is in a read-only folder: {path}")
    return None


read_only_root.axis = "boundary"


def deferred_execution(engine, tool_name, arguments, metadata, risk) -> Optional[GuardVerdict]:
    """Files inside a workspace that run on a later, innocent-looking action: a
    `.git/hooks/pre-commit` written now is run by the next `git commit`. They stay writable, but
    never without a human, and no automatic path clears them. This is a consent verdict and not a
    boundary one: the target is reachable by design, and the point is that a person sees every
    write that will run unattended later."""
    if risk is not RiskClass.WRITE_LOCAL:
        return None
    paths, located = write_paths(tool_name, arguments)
    if not located:
        return None
    if engine.boundary.deferred_execution_hit(paths):
        return GuardVerdict.human("this file runs automatically later — approval required")
    return None


deferred_execution.axis = "consent"


def persistent_authority(engine, tool_name, arguments, metadata, risk) -> Optional[GuardVerdict]:
    """Authority that outlives the session: instructions the agent will follow in later
    conversations, tasks that run on their own afterwards. It is keyed on the tool name alone, not
    on the classification, so a metadata slip cannot switch the floor off. No automatic path clears
    these, because the effect arrives after the conversation that authorised it has ended and the
    person who bears it is not there."""
    if tool_name in PERSISTENT_AUTHORITY_TOOLS:
        return GuardVerdict.human("this outlives the session — approval required")
    return None


persistent_authority.axis = "consent"


# Registration order decides which reason is reported and never the outcome, which is the union of
# restrictions. Boundary guards come first and consent guards after, the order a reader expects
# because reach is decided before anyone is asked.
DEFAULT_GUARDS: tuple[GuardFn, ...] = (
    self_protection,
    secret_reads,
    read_only_mode,
    unscoped_write,
    read_only_root,
    deferred_execution,
    persistent_authority,
)
