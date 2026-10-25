"""What a tool declares about its reach, safety and output budget (the
`ToolMetadata`). Split from `contract.py` when it crossed the architecture ceiling;
`contract.py` re-exports it, so existing imports keep working."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Optional


SIDE_EFFECT_SCOPES = ("none", "workspace", "git", "network", "system", "session", "user_interaction")


@dataclass(frozen=True, slots=True)
class ToolCapabilities:
    """What a tool declares about its reach, its safety and its output budget.

    The point of declaring rather than
    deriving is one source of truth: the scheduler, the permission layer and the approval
    card should answer "may this run beside others?" and "is this dangerous?" from the
    same facts, not from a risk class that three call sites interpret differently.

    Everything here is OPTIONAL and a declaration is a promise by the tool's author, so the
    engine falls back to today's derivation (low risk + no approval) when a tool says
    nothing. `resolve` is the input-aware override (the
    `resolvePermissionCapability`): given the arguments, answer what actually applies for
    THIS call — the seam a shell command classified read-only would use, and the seam
    speculative execution will read.

    What a declaration may NOT do: clear a human gate. `needs_approval=False` is honoured
    only as far as the risk classifier already allows (see `risk.classify`), because a
    third-party server claiming "read-only" must not turn an approval into an auto-run.
    """

    read_only: bool = False
    destructive: bool = False
    concurrent_safe: bool = False
    side_effect_scope: str = "none"
    #: True/False pins the approval answer; None keeps the risk-derived one.
    needs_approval: Optional[bool] = None
    #: Per-call override. Returns the capabilities that apply to these arguments; an
    #: exception or a non-`ToolCapabilities` answer falls back to the declared ones, so a
    #: buggy resolver degrades to a conservative tool rather than a permissive one.
    resolve: Optional[Callable[[dict[str, Any]], "ToolCapabilities"]] = None

    def __post_init__(self) -> None:
        if self.side_effect_scope not in SIDE_EFFECT_SCOPES:
            raise ValueError(
                f"ToolCapabilities.side_effect_scope must be one of {SIDE_EFFECT_SCOPES}, "
                f"got {self.side_effect_scope!r}"
            )
        if self.resolve is not None and not callable(self.resolve):
            raise TypeError("ToolCapabilities.resolve must be callable or None")

    def for_call(self, arguments: Optional[dict[str, Any]]) -> "ToolCapabilities":
        """The capabilities that apply to one call (the override, or the declaration)."""
        if self.resolve is None:
            return self
        try:
            resolved = self.resolve(arguments or {})
        except Exception:  # noqa: BLE001 — a broken resolver must not loosen anything
            return self
        return resolved if isinstance(resolved, ToolCapabilities) else self

    def parallel_safe(self) -> bool:
        """May this call overlap others? The rule: read-only AND
        concurrency-safe AND not destructive AND no side effects at all."""
        return (
            self.read_only
            and self.concurrent_safe
            and not self.destructive
            and self.side_effect_scope == "none"
        )


#: The two declarations almost every tool needs, written once: a pure read (parallel-safe)
#: and a workspace write (never parallel, always asked). A tool with something more
#: specific declares its own.
READ_ONLY_CAPS = ToolCapabilities(
    read_only=True, concurrent_safe=True, side_effect_scope="none"
)
WORKSPACE_WRITE_CAPS = ToolCapabilities(
    read_only=False,
    destructive=False,
    concurrent_safe=False,
    side_effect_scope="workspace",
    needs_approval=True,
)
#: A command that runs on this machine: system scope, and destructive by definition — the
#: engine cannot know what a shell string does, so it is never allowed to overlap.
SYSTEM_EXEC_CAPS = ToolCapabilities(
    read_only=False,
    destructive=True,
    concurrent_safe=False,
    side_effect_scope="system",
)
#: Something that leaves the machine (search, fetch, a connector call): egress is its own
#: scope, and two calls to the same destination are not a read that can be overlapped.
NETWORK_CAPS = ToolCapabilities(
    read_only=False,
    destructive=False,
    concurrent_safe=False,
    side_effect_scope="network",
)


