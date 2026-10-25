"""The managed environment a shell command runs in.

`environment_context` already tells the MODEL where it is — workspace, platform, git state
— as prose in the system prompt. The shell itself is told nothing. So a command that needs
a fact about its own session has to be handed it in the command string, which means the
model has to have been told the value, remember it, and interpolate it correctly every
time. The scratch directory is the one that hurts: it is where deliverables go, it is a
long generated path, and every `run_shell` that writes one has to spell it out.

The shell environment publishes a `REEVE_*` set into every model shell call. Two
properties matter, and they are why it is not a dict set once at spawn:

**Contributors declare their keys up front.** A contributor names the complete set it may
return, so two contributors claiming one key collide at registration — before the first
command — instead of one silently winning at some later call. It also means the model can
be told what exists without running anything.

**Values resolve PER CALL.** reeve's roots list is mutated in place while the session
runs (`roots.py`: "Slice C can mutate it in place at runtime"), so a value computed once at
spawn describes a session that has since changed. A contributor is asked again for each
command, and answers about the session as it stands now.

The shell is long-lived, so these cannot ride on the process environment: they are exported
into the shell immediately before each command, in that shell's own dialect.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Callable, Optional

logger = logging.getLogger(__name__)

#: Every managed variable starts here. A prefix makes the set enumerable and makes it
#: obvious in a command which values came from the harness rather than the user's shell.
PREFIX = "REEVE_"


@dataclass(frozen=True)
class EnvContributor:
    """One source of managed variables.

    `variables` is the complete set this contributor may return, mapped to what each one
    means — the declaration that makes a conflict detectable and lets the model be told
    what exists. `resolve` is asked once per command and may return a SUBSET: a value that
    does not apply right now is absent, never an empty string, because a command testing
    `-n "$REEVE_SCRATCH"` must be able to tell "no scratch" from "scratch is nowhere".
    """

    name: str
    variables: dict[str, str]
    resolve: Callable[[], dict[str, str]]


class ShellEnv:
    """The registry the shell asks before each command."""

    def __init__(self) -> None:
        self._contributors: list[EnvContributor] = []
        self._owner: dict[str, str] = {}  # key -> contributor name
        self._lock = threading.Lock()

    # -- registration --------------------------------------------------------------

    def register(self, contributor: EnvContributor) -> Callable[[], None]:
        """Add a contributor. Returns a disposer, like the pipeline's listeners do — a
        registration that cannot be undone is a leak the moment anything is scoped to one
        session or toggled from settings.

        Raises on a key another contributor already owns, or on one missing the prefix.
        Both are programming errors in composition, and both are worth failing at
        registration rather than at the first command that reads the wrong value.
        """
        for key in contributor.variables:
            if not key.startswith(PREFIX):
                raise ValueError(f"{key} must start with {PREFIX}")
            owner = self._owner.get(key)
            if owner is not None:
                raise ValueError(
                    f"{key} is already contributed by {owner!r}; "
                    f"{contributor.name!r} cannot claim it too"
                )
        with self._lock:
            self._contributors.append(contributor)
            for key in contributor.variables:
                self._owner[key] = contributor.name

        def dispose() -> None:
            with self._lock:
                if contributor in self._contributors:
                    self._contributors.remove(contributor)
                for key in contributor.variables:
                    if self._owner.get(key) == contributor.name:
                        del self._owner[key]

        return dispose

    # -- reading -------------------------------------------------------------------

    def declared(self) -> dict[str, str]:
        """Every variable that may appear, and what it means. For the model."""
        out: dict[str, str] = {}
        with self._lock:
            for contributor in self._contributors:
                out.update(contributor.variables)
        return dict(sorted(out.items()))

    def resolve(self) -> dict[str, str]:
        """The values for THIS command.

        A contributor that raises is skipped rather than allowed to fail the command: this
        is context, and a command that would have worked must not be lost because a fact
        about it could not be computed. A key it returns without having declared is
        dropped — the declaration is the contract, and honouring an undeclared key would
        make the conflict check above a formality.
        """
        out: dict[str, str] = {}
        with self._lock:
            contributors = list(self._contributors)
        for contributor in contributors:
            try:
                values = contributor.resolve() or {}
            except Exception:  # noqa: BLE001 — see the docstring
                logger.warning(
                    "shell env contributor %r failed; its variables are absent",
                    contributor.name,
                    exc_info=True,
                )
                continue
            for key, value in values.items():
                if key in contributor.variables and value is not None:
                    out[key] = str(value)
        return out


# -- rendering into a shell ------------------------------------------------------------


def _bash_literal(value: str) -> str:
    return "'" + value.replace("'", "'\\''") + "'"


def _pwsh_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def export_preamble(values: dict[str, str], *, powershell: bool) -> str:
    """The lines that put these values into the shell, in its own dialect (`powershell` is the
    dialect the command is written in, not the operating system).

    One line per variable, each producing no output of its own so the command's captured
    output stays the command's. Values are single-quoted with the dialect's own escape:
    a path can contain a space, and on POSIX it can contain a quote, and a value spliced
    in raw would either break the line or execute part of itself.
    """
    if not values:
        return ""
    lines = []
    for key in sorted(values):
        value = values[key]
        if powershell:
            lines.append(f"$env:{key} = {_pwsh_literal(value)}")
        else:
            lines.append(f"export {key}={_bash_literal(value)}")
    return "\n".join(lines) + "\n"


def describe(env: Optional[ShellEnv]) -> str:
    """The block that tells the model these exist. Empty when nothing is registered, so a
    session without a shell says nothing about shell variables."""
    declared = env.declared() if env is not None else {}
    if not declared:
        return ""
    lines = [f"- {key}: {meaning}" for key, meaning in declared.items()]
    body = "\n".join(lines)
    return (
        "Shell variables (already exported in every `run_shell` command — use them "
        f"instead of retyping the paths):\n{body}"
    )


# -- the built-in contributor ----------------------------------------------------------

WORKSPACE = f"{PREFIX}WORKSPACE"
SCRATCH = f"{PREFIX}SCRATCH"
ROOTS = f"{PREFIX}ROOTS"
SESSION = f"{PREFIX}SESSION"


def session_contributor(
    *,
    workspace: Optional[object],
    roots: Optional[list] = None,
    session_id: str = "",
) -> EnvContributor:
    """Where this session may work, resolved fresh for each command.

    `roots` is the live list, held by reference on purpose: it is the same object the
    permission engine scopes by and the file tools resolve against, and it changes when the
    user grants a folder mid-session. Copying it here would publish the grants the session
    started with and quietly stop matching the ones it has.
    """
    live_roots = roots if roots is not None else []

    def resolve() -> dict[str, str]:
        import os

        values: dict[str, str] = {}
        if workspace is not None:
            values[WORKSPACE] = str(workspace)
        scratch = next(
            (r for r in live_roots if getattr(r, "label", "") == "scratch"), None
        )
        if scratch is not None:
            values[SCRATCH] = str(scratch.path)
        if live_roots:
            values[ROOTS] = os.pathsep.join(str(r.path) for r in live_roots)
        if session_id:
            values[SESSION] = session_id
        return values

    return EnvContributor(
        name="session",
        variables={
            WORKSPACE: "the workspace root — where relative paths resolve",
            SCRATCH: "the session's scratch folder, where deliverables belong",
            ROOTS: (
                "every folder this session may touch, joined by the platform path "
                "separator"
            ),
            SESSION: "this session's id",
        },
        resolve=resolve,
    )
