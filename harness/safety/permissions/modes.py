"""The permission preset and the shape of a decision."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Mode(str, Enum):
    """The permission preset: one value per session, on one axis.

    `read-only` refuses every consequential action. `workspace-write` lets reads run and asks for
    anything that writes or executes, inside the session's roots or outside them, where the card
    names the folder (an out-of-root write is an ordinary ask; see `guards.out_of_root_writes`).
    `danger-full-access` runs everything the hard floors still allow, and asks nobody: a floor
    that would put a person on a call is cleared by the choice of this preset.

    Plan is not a mode. It is an overlay (`PermissionEngine.plan`) that adds the read-only floor on
    top of whichever preset is selected."""

    READ_ONLY = "read-only"
    WORKSPACE_WRITE = "workspace-write"
    DANGER_FULL_ACCESS = "danger-full-access"

    @classmethod
    def _missing_(cls, value: object) -> "Mode | None":
        """Older names from configs, saved sessions and earlier versions of the app. `plan` maps to
        read-only here, and `migrated` recovers it as the plan overlay from the raw stored value.
        `auto` and `bypass-approvals` were the full-access names. `auto-approve` and `custom` land
        on the least surprising write preset now that the reviewer and the custom mode are gone."""
        older = {
            "discuss": cls.READ_ONLY,
            "plan": cls.READ_ONLY,
            "interactive": cls.WORKSPACE_WRITE,
            "auto-approve": cls.WORKSPACE_WRITE,
            "custom": cls.WORKSPACE_WRITE,
            "auto": cls.DANGER_FULL_ACCESS,
            "bypass-approvals": cls.DANGER_FULL_ACCESS,
        }
        return older.get(str(value or "").strip().lower())

    @classmethod
    def migrated(cls, value: object) -> tuple["Mode", bool]:
        """`(mode, plan)` from a stored or configured value. `plan` used to be a mode, and the
        overlay it carried must survive the translation instead of quietly becoming plain
        read-only. An empty value (a row made for an environment binding before the session's first
        save) is the default preset, not an error."""
        raw = str(value or "").strip().lower()
        if not raw:
            return cls.WORKSPACE_WRITE, False
        return cls(raw), raw == "plan"


@dataclass
class Decision:
    allowed: bool
    reason: str = ""
    needs_user: bool = False  # the surface should ask the person
    # No mode below full access, grant or allowlist may clear this ask; a person has to. It is set
    # on decisions whose whole point is that someone sees them: protected files inside the project
    # that run later (git hooks, CI configs) and writes whose path could not be located for
    # scoping. Full access is the person's answer given in advance, so it never asks.
    human_only: bool = False
    # The standing rule ("tool → target") that allowed the call, so the engine can audit it and the
    # tool card can say so.
    rule: str = ""
    # Which question decided this: "boundary" (what the action can reach) or "consent" (who
    # agrees). A guard's decision carries its guard's axis and a policy decision is consent. The
    # surfaces and the audit log can read it without working out why a call was refused.
    axis: str = "consent"
