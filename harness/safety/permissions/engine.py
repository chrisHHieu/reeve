"""The permission engine: allow, deny or ask the person, for each proposed tool call.

Two questions, kept apart (see `boundary.py`). The reach axis, `harness.safety.boundary`, asks what
an action can touch: writable roots, protected files, located write paths. It is pure geometry,
with no mode, allow-list or judgement. The consent axis, this module, asks whether anyone agrees:
the presets (`read-only`, `workspace-write`, `danger-full-access`), the plan overlay, and the
narrowing grants (command prefixes, session grants, task rules). The guard stage runs the floors
of both ahead of every policy path, and a verdict it produces is stamped with the axis that made it
(`Decision.axis`).

The presets pair a boundary with an approval rule: `workspace-write` is workspace roots with asking,
and `danger-full-access` is everything with no asking. `read-only` refuses every consequential
action. Plan is not a fourth preset but an overlay (`PermissionEngine.plan`) that raises the
read-only floor on top of whichever preset is selected. The engine only decides: the turn engine
sends `needs_user` decisions to a surface for approval and records the outcome.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, Optional

from ..boundary import Boundary, command_text, write_paths
from ..risk import RiskClass, RiskOverrides, classify, is_consequential
from .domains import domain_allowed, host_of, session_domain
from .guards import DEFAULT_GUARDS, GuardFn, GuardVerdict, out_of_root_reads, out_of_root_writes
from .modes import Decision, Mode
from .shell_rules import command_allowed


def standing_rule_candidate(
    tool_name: str,
    arguments: dict[str, Any],
    metadata: Any = None,
    overrides: Optional[RiskOverrides] = None,
) -> Optional[str]:
    """The target value when this call can use a task's standing rule, else None.

    Only an external-risk call qualifies (never exec or a local write: the shell asks every time),
    the tool must declare a target argument, and the call must actually name a target. A call that
    does not qualify keeps parking its approval as before."""
    from harness.integrations.connectors.catalog import target_arg_for

    if classify(tool_name, metadata, overrides) is not RiskClass.EXTERNAL:
        return None
    argument = target_arg_for(tool_name)
    if argument is None:
        return None
    return str((arguments or {}).get(argument) or "").strip() or None


@dataclass
class PermissionEngine:
    workspace_root: Path
    mode: Mode = Mode.WORKSPACE_WRITE
    # The plan overlay: while set, every consequential action is refused as a boundary verdict,
    # full access included. It is not a preset because it answers another question ("rehearse
    # first" against "what may run at all"), and a session can toggle it without losing its
    # permission choice.
    plan: bool = False
    allowed_commands: list[str] = field(default_factory=list)
    auto_allow_tools: set[str] = field(default_factory=set)
    session_allow_tools: set[str] = field(default_factory=set)
    session_allow_commands: set[str] = field(default_factory=set)
    # Destinations that run without a prompt: `allowed_domains` from the config, and
    # `session_allow_domains` from "Always allow this domain". A host matches exactly or as a
    # subdomain (see `domains.domain_allowed`).
    allowed_domains: list[str] = field(default_factory=list)
    session_allow_domains: set[str] = field(default_factory=set)
    # Where the session's shell stands now, or None for the workspace root. A read-only command is
    # judged where it runs: `ls` with no operand reads that folder, and a relative operand resolves
    # against it, so a shell a `cd` moved away from the roots is not read as if it had stayed.
    shell_cwd: Optional[Callable[[], Optional[str]]] = None
    # Standing rules of the task this run belongs to, `{tool: {allowed targets}}`, seeded from the
    # target-shaped entries of its ScheduledTask. Held by reference and read at every check, so a
    # rule added mid-run ("Allow every time") applies to the run's next call too.
    task_rules: dict[str, set[str]] = field(default_factory=dict)
    # Resolves the person's risk override for a tool. None keeps the base classification.
    risk_overrides: Optional[RiskOverrides] = None
    # The roots, shared and changeable (RootDir-like objects or dicts). When omitted the single
    # `workspace_root` is the only writable root. It is held by reference and read at every check,
    # so adding or removing a folder at runtime takes effect without rebuilding the engine.
    roots: Optional[list] = None
    # The pre-policy guards (deny-only; see `guards.py`), ordered and run before every mode and
    # allow-list path. Append to add a floor. A guard has no allow to give, so nothing added can
    # loosen what another guard or the policy denies.
    guards: list[GuardFn] = field(default_factory=lambda: list(DEFAULT_GUARDS))
    # A subagent's permission ruleset (`tools/task_agents.py`): called with (tool, arguments) and
    # answering "allow", "deny" or None to fall through. `allow` runs the call with no prompt and
    # `deny` refuses it. A child's ruleset is its permission decision, so the parent's approver is
    # consulted only when the ruleset has nothing to say (the `ask` case). None on a person's own
    # session, whose calls are judged by the guards and the policy.
    rule_decider: Optional[Callable[[str, dict[str, Any]], Optional[str]]] = None
    # The session's auto-memory folders. The model keeps its memory with the ordinary
    # file tools, so a file write whose every target is inside one runs without an ask. That is a
    # policy allowance and comes after the guards like any other, so the floors still see each of
    # these writes and may refuse it.
    memory_dirs: list = field(default_factory=list)

    def __post_init__(self) -> None:
        self.workspace_root = Path(self.workspace_root).expanduser().resolve()
        self.auto_allow_tools = set(self.auto_allow_tools)
        if self.roots is None:
            self.roots = [{"path": self.workspace_root, "writable": True}]
        # The reach axis works over the same roots list, by reference, so a folder appended in place
        # by the directory-grant flow is seen by both axes without resyncing. Replacing this object
        # is how a sandbox provider would replace the geometry; the consent axis would not notice.
        self.boundary = Boundary(workspace_root=self.workspace_root, roots=self.roots)

    # -- the decision ----------------------------------------------------------------------

    @property
    def full_access(self) -> bool:
        """The preset that runs everything without asking. The plan overlay outranks it: plan
        refuses every consequential action, full access included."""
        return self.mode is Mode.DANGER_FULL_ACCESS and not self.plan

    def _run_guards(
        self, tool_name: str, arguments: dict[str, Any], metadata: Any, risk: RiskClass
    ) -> Optional[GuardVerdict]:
        """Run every guard in registration order and return the strongest verdict. A deny
        outranks a human-only ask, which outranks abstaining, so the outcome does not depend on
        order even though the reported reason follows it."""
        verdict: Optional[GuardVerdict] = None
        for guard in self.guards:
            result = guard(self, tool_name, arguments, metadata, risk)
            if result is None:
                continue
            # The registry stamps the axis, so each guard is classified in one place.
            result = replace(result, axis=getattr(guard, "axis", "consent"))
            if result.kind == "deny":
                return result  # nothing outranks an outright refusal
            if verdict is None:
                verdict = result
        return verdict

    def evaluate(self, tool_name: str, arguments: dict[str, Any], metadata: Any = None) -> Decision:
        arguments = arguments or {}
        is_connector = getattr(metadata, "category", "") == "connector"
        risk = classify(tool_name, metadata, self.risk_overrides)
        is_shell = risk is RiskClass.EXEC
        is_egress = risk is RiskClass.EGRESS

        # The guard stage is binding and deny-only, ahead of every mode, allow-list and session
        # grant. A verdict here is the decision, and nothing after it runs.
        verdict = self._run_guards(tool_name, arguments, metadata, risk)
        if verdict is not None and verdict.kind == "human" and self.full_access:
            # Full access asks nobody. A floor that puts a person on a call (a file that
            # runs later, authority that outlives the session, a write whose path cannot be
            # located) is the person's to clear, and choosing full access cleared it in advance.
            # The refusals stay: a deny is a fact about reach, not a question.
            return Decision(True, f"full access: {verdict.reason}", axis=verdict.axis)
        if verdict is not None:
            return Decision(
                False,
                verdict.reason,
                needs_user=verdict.kind == "human",
                human_only=verdict.kind == "human",
                axis=verdict.axis,
            )

        # A read outside every folder the session holds. It comes before a subagent's
        # ruleset, so a child whose rules allow `read_file` still asks before it reads another
        # project, and before the low-risk shortcut, which would wave any read through. Full access
        # reads it; every other preset (read-only and plan included: a read changes nothing) asks
        # once, and the card offers the folder, which then becomes a read-only root and asks no
        # more.
        outside_reads = out_of_root_reads(self, tool_name, arguments)
        if outside_reads:
            if self.full_access:
                return Decision(True, "full access")
            folders = sorted({str(self.boundary.read_folder(path)) for path in outside_reads})
            return Decision(
                False,
                "reads outside the session's folders: " + ", ".join(folders),
                needs_user=True,
            )

        # A ruleset, when there is one, sits above the risk shortcuts (a `*: deny` ruleset must
        # refuse a read, or `explore` would keep every tool it denies) and above the allow-lists (a
        # ruleset `allow` is a permission decision, not an exemption from one). The guards are the
        # only thing it cannot loosen.
        if self.rule_decider is not None:
            action = self.rule_decider(tool_name, arguments)
            if action == "allow":
                return Decision(True, "allowed by the agent's ruleset")
            if action == "deny":
                return Decision(False, "the agent's permission rules deny this tool call", needs_user=False)

        if not is_consequential(risk):
            return Decision(True, "low risk")
        if self.is_memory_write(tool_name, arguments, risk):
            return Decision(True, "auto memory")
        # A shell command that only reads, and only inside the roots, is a read: it runs
        # in every preset with no ask, as `read_file` and `grep` do. The read-only floor stepped
        # aside for exactly this command, and a ruleset that denies it has already answered above.
        if self.reads_in_roots(tool_name, arguments):
            return Decision(True, "read-only command")
        if self.mode is Mode.DANGER_FULL_ACCESS:
            return Decision(True, "full access")

        granted = self._granted(tool_name, arguments, metadata, is_shell, is_egress, is_connector)
        if granted is not None:
            return granted

        # Otherwise the person is asked. An out-of-root write is an ordinary ask with a cause worth
        # naming: the card points at the folder and the remedy (add it as a root) in place of a bare
        # "requires approval".
        outside = out_of_root_writes(self, tool_name, arguments, risk)
        if outside:
            return Decision(
                False,
                "path is not in a writable directory: "
                + ", ".join(outside)
                + " — approve to write here, or add the folder to the session",
                needs_user=True,
            )
        return Decision(False, "requires approval", needs_user=True)

    def _granted(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        metadata: Any,
        is_shell: bool,
        is_egress: bool,
        is_connector: bool,
    ) -> Optional[Decision]:
        """In `workspace-write`: the allowances that spare an ask, or None when none applies.

        Session grants ("always allow this…") count here, because the preset promises consent per
        kind and a grant is that consent given once. Standing policy from outside the session (the
        settings allow-list through `command_allowed`, `allowed_domains` in the config) may skip the
        ask too. The grants are checked before the task's rules so each layer stays readable."""
        if is_shell:
            command = command_text(tool_name, arguments)
            if command_allowed(command, self.allowed_commands):
                return Decision(True, "command on allowlist")
            if command and command in self.session_allow_commands:
                return Decision(True, "command allowed for session")
        if is_egress and domain_allowed(
            str(arguments.get("url", "")), self.allowed_domains, self.session_allow_domains
        ):
            return Decision(True, "domain on allowlist")
        if tool_name in self.session_allow_tools and not is_connector:
            return Decision(True, "tool allowed for session")

        # A task's standing rule is a tool plus an exact target, owned by the automation. It is
        # not subject to the connector exclusion above, since the exact-target binding is what
        # makes auto-allowing a connector tool safe. It never covers exec risk (candidates are
        # extracted for external risk only), and it adds to the preset: read-only has already
        # returned before this point.
        if tool_name in self.task_rules:
            target = standing_rule_candidate(tool_name, arguments, metadata, self.risk_overrides)
            if target and target in self.task_rules[tool_name]:
                rule = f"{tool_name} → {target}"
                return Decision(True, f"allowed by standing rule: {rule}", rule=rule)

        # Tools the config's `auto_allow` lists: the person has already decided they never need an
        # ask in this preset.
        if tool_name in self.auto_allow_tools:
            return Decision(True, "auto-allowed by config")
        return None

    def reads_in_roots(self, tool_name: str, arguments: dict[str, Any]) -> bool:
        """Whether this is a `run_shell` command that only reads, and only inside the roots.

        The classifier vets what a command does and the roots vet what it reads. Without the second
        half, "a read runs without asking" would also cover ~/.aws/credentials, another repository's
        history and Reeve's own secrets file, none of which the self-protection floor catches
        because it guards writes and not reads. The folder the shell stands in counts as read (`ls`,
        `git status` and `rg x` name no operand), and a relative operand is resolved from there.
        `terminal_send` is not judged: what its text means depends on the program the terminal holds."""
        if tool_name != "run_shell":
            return False
        command = command_text(tool_name, arguments)
        from ..readonly import is_readonly_command, read_targets

        if not command or not is_readonly_command(command):
            return False
        try:
            here = (self.shell_cwd() if self.shell_cwd is not None else None) or str(self.workspace_root)
        except Exception:  # a shell that cannot say where it is gets no allowance, not a crash
            return False
        if not self.boundary.under_any_root(here):
            return False
        base = self.boundary.candidate(here)
        targets = read_targets(command)
        if targets is None:
            return False  # reads that cannot be named are not known to be inside the roots
        for target in targets:
            given = Path(target).expanduser()
            if not self.boundary.under_any_root(str(given if given.is_absolute() else base / given)):
                return False
        return True

    def is_memory_write(self, tool_name: str, arguments: dict[str, Any], risk: RiskClass) -> bool:
        """A located file write whose every target is inside a memory folder."""
        if risk is not RiskClass.WRITE_LOCAL or not self.memory_dirs:
            return False
        paths, located = write_paths(tool_name, arguments)
        if not located or not paths:
            return False
        folders = [Path(folder).expanduser().resolve() for folder in self.memory_dirs]
        for raw in paths:
            candidate = self.boundary.candidate(raw)
            if not any(candidate == folder or folder in candidate.parents for folder in folders):
                return False
        return True

    # -- what a session remembers ----------------------------------------------------------

    def allow_tool_for_session(self, tool_name: str) -> None:
        self.session_allow_tools.add(tool_name)

    def allow_command_for_session(self, command: str) -> None:
        if command:
            self.session_allow_commands.add(command)

    def allow_domain_for_session(self, url_or_domain: str) -> None:
        """Remember a destination for this session ("Always allow this domain")."""
        host = session_domain(url_or_domain)
        if host:
            self.session_allow_domains.add(host)

    # The reach geometry is on `self.boundary` (boundary.py): `candidate`, `under_any_root`,
    # `under_writable_root`, `settings_hit`, `deferred_execution_hit`. Calls go through that object,
    # which is what a sandbox provider would replace.

    def _domain_allowed(self, url: str) -> bool:
        return domain_allowed(url, self.allowed_domains, self.session_allow_domains)

    def _command_allowed(self, command: str) -> bool:
        return command_allowed(command, self.allowed_commands)


__all__ = ["PermissionEngine", "standing_rule_candidate", "host_of"]
