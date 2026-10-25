"""Hooking the finished engine up to the session around it.

The engine is constructible on its own, and everything here is set after construction by whoever
owns it: facts about the world, the listeners on its pipeline, the stores it reads. Each listener has
a place in the order it runs, and the order is part of the design.
"""

from __future__ import annotations

from typing import Any

from harness.context import session_facts
from harness.context.memory.admission import Admission, project_anchor
from harness.loop import TurnEngine
from harness.tools.intents import intent_listener
from harness.tools.observed import ObservedFiles
from harness.tools.observed_gate import observation_gate, observation_recorder
from harness.tools.observed_seed import seed_from_messages
from harness.tools.undo import snapshot_listener

from .features import FeatureContext
from .session_helpers import verify_agent_enabled


def attach_session_state(engine: TurnEngine, p: FeatureContext) -> None:
    """The fields the manager, the tools and the surfaces read off a built engine."""
    engine.executor = p.executor  # type: ignore[attr-defined]
    # The live language-server pool (None when `[lsp] enabled = false`). The post-edit syntax hook
    # beside it also tells the servers which files just changed on disk.
    engine.lsp_manager = p.services.get("lsp_manager")  # type: ignore[attr-defined]
    # A workflow run that dies must not leave its `agent()` children working for nobody: the host
    # hook stops them by the root call id. It is None on hosts that own no sessions.
    engine.subagent_cancel_origin = p.inputs.subagent_cancel_origin  # type: ignore[attr-defined]
    # A background workflow's door, and the parent row's live frames for it.
    engine.workflow_background = p.inputs.workflow_spawner  # type: ignore[attr-defined]
    engine.subagent_metadata = p.inputs.subagent_metadata  # type: ignore[attr-defined]
    # The workflow tool's per-run settings, resolved once from config: the script's wall clock, its
    # fatal caps, and how many children it may overlap, bounded by the session's own
    # `subagent_max_parallel` so a script can never outrun its host.
    from harness.tools.task_agents import subagent_parallel_limit

    engine.workflow_options = {  # type: ignore[attr-defined]
        "timeout": p.config.workflow_timeout,
        "max_agents": p.config.workflow_max_agents,
        "max_tokens": p.config.workflow_max_tokens,
        "concurrency": max(1, min(8, subagent_parallel_limit(p.config) or 4)),
    }
    # The session's one world, kept on the engine so the manager can tell a remote session from a
    # local one when it saves the workspace (a remote cwd must not go through `os.path.realpath`)
    # and so tool routing can find the read backend.
    engine.world = p.world  # type: ignore[attr-defined]
    # The context this build composed, kept by reference because it holds the shared, changeable
    # roots list. A child spawn rebuilds the inherited path-scoped tools from its own context
    # (`catalog.replace_path_tools`), the same owner isolation the terminal and shell rebinds
    # express.
    engine.context = p.context  # type: ignore[attr-defined]
    # The local workspace root for the nested-AGENTS pass (`project.nested_agents_md`). A remote
    # world's root is a POSIX path on another host, so it is deliberately not published here, and the
    # pass checks `world.is_remote` anyway.
    engine.workspace = (  # type: ignore[attr-defined]
        str(p.world.root) if p.world is not None and not getattr(p.world, "is_remote", False) else None
    )
    engine.inject_agents = bool(getattr(p.agent, "inject_agents", True))  # type: ignore[attr-defined]
    compaction_config = p.config.compaction or {}
    # The context-awareness line (`[compaction] context_budget_line`, on by default).
    engine.context_budget_line = bool(compaction_config.get("context_budget_line", True))  # type: ignore[attr-defined]
    # The model may fold its own context (`[compaction] model_fold`, on by default). Only a
    # session's own engine is offered it: a subagent (seeded from `base_registry`) runs one bounded
    # task in a context of its own.
    if p.inputs.base_registry is None and compaction_config.get("model_fold", True):
        engine.enable_model_fold()
    engine.todo = p.todo  # declared on TurnEngine: compaction and the event log both read it
    engine.agent_name = p.agent.name  # type: ignore[attr-defined]
    # The session's resolved `[goal] max_rounds`, read by the manager's human route when a goal is
    # created without a cap, exactly as the `goal` tool reads it at registration.
    engine.goal_default_max_rounds = p.config.goal_max_rounds  # type: ignore[attr-defined]
    engine._held_edit_tools = p.held_edit_tools  # type: ignore[attr-defined]  # edit_surface
    # Whether a check-less goal can be proven by the `verify` subagent here, the question the `goal`
    # tool's `done` asks, read by the driver's end-of-round judge.
    engine.goal_verifier = lambda: (  # type: ignore[attr-defined]
        p.registry.get("task") is not None and verify_agent_enabled(p.config)
    )
    engine.roots = p.root_list  # type: ignore[attr-defined]  # shared list, changed in place
    engine.edit_exempt_roots = p.edit_exempt_roots  # type: ignore[attr-defined]
    # Session facts: freeze the known world now, before the agent has acted.
    # Freezing is the point. Compared against live state, an agent that runs `git remote add backup
    # https://attacker.net/…` would make its own destination look familiar. Nothing consumes this
    # yet, and ingestion is recorded to the audit log only.
    engine.session_facts = session_facts.SessionFacts(
        world=session_facts.capture(
            # The snapshot's git-remote probe is a local subprocess, and a remote root has no local
            # handle to run it against. The remote session freezes its domains only, and does not
            # invent facts from the controller's disk.
            roots=[] if p.remote else p.root_list,
            allowed_domains=p.config.allowed_domains,
            workspace=None if p.remote else p.ws,
        )
    )

    # What an approval card shows besides the call, from the feature that owns the tool (the web
    # search card names the live search provider). Resolved when the card is raised, so a
    # mid-session Settings change shows through.
    extras = dict(p.approval_extras)

    def approval_extras(tool_name: str, arguments: dict) -> dict:
        extra = extras.get(tool_name)
        return extra(arguments) if extra is not None else {}

    engine.approval_extras = approval_extras
    engine.audit_context = {
        "session_id": p.inputs.session_id or "",
        "agent": p.agent.name,
        "workspace": str(p.ws) if p.ws else (p.remote_root if p.remote else ""),
    }
    engine.skill_loader = p.services.get("skill_loader")  # type: ignore[attr-defined]
    # The `update_plan` write target (None where the session has no writable root). It is set after
    # construction like `todo` and `log`, so the engine stays constructible without a plan file and a
    # surface that never wires one never gets the tool.
    engine.plan_path = p.plan_path  # type: ignore[attr-defined]
    # Kept on the engine so a background delegation can mark its own completion wake due
    # (`WakeStore.complete_job`) the moment it finishes. The tool module already holds the engine, so
    # this is the one route that needs no global and no extra plumbing.
    engine.wake_store = p.inputs.wake_store


def attach_pipeline(engine: TurnEngine, p: FeatureContext) -> None:
    """The listeners on the engine's tool pipeline, in the order they are registered."""
    # The wall-clock ceiling on every tool call (tools/deadline.py). It goes on `on_execute`, a stage
    # that had no tenant before this and the only one that wraps a dispatch and does not veto or
    # transform it, which is what a deadline is. It is the backstop under each tool's own deadline and
    # never a replacement: shell still kills its process group and MCP still cancels its request, and
    # both fire first because every self-declared budget sits below the ceiling (pinned by
    # tests/test_tool_deadline.py). Before this, the promise that no call was unbounded rested on every
    # tool remembering its own, and `tools/files.py`, the largest family after shell, had not.
    from harness.tools.deadline import DEADLINE_CEILING_MS, deadline_listener

    engine.pipeline.on_execute(deadline_listener(p.inputs.deadline_ceiling_ms or DEADLINE_CEILING_MS))
    if p.ws is not None or p.remote:
        _attach_observation(engine, p)
    if p.memory_root is not None:
        _attach_memory(engine, p)
    if p.undo is not None:
        # `write_paths` is injected and not imported by the tool module. It lives in the permission
        # layer, and reaching into permissions from `tools/` would invert the dependency for one
        # lookup.
        from harness.safety.permissions import write_paths

        engine.undo = p.undo
        engine.pipeline.on_pre_execute(snapshot_listener(p.undo, write_paths))
    if p.intents is not None:
        # The second listener has the opposite failure policy: a snapshot that fails must not block a
        # write, but an intent that fails to record must, because the note is the only thing between a
        # crash and a second live send. Risk comes from the permission layer's own classifier, so
        # "irreversible" has one definition.
        from harness.safety.risk import classify

        def risk_of(name: str) -> Any:
            spec = p.registry.get(name)
            return classify(name, spec.metadata if spec else None, p.permissions.risk_overrides)

        engine.intents = p.intents
        engine.pipeline.on_pre_execute(intent_listener(p.intents, risk_of))
    _attach_receipt_and_durability(engine, p)


def _attach_observation(engine: TurnEngine, p: FeatureContext) -> None:
    """Read-before-edit and the version guard (tools/observed.py). It is registered first so it is the
    outermost pre-execute listener: a refusal here should land before the undo snapshot below copies a
    file for a write that is not going to happen.

    It needs a workspace. `provenance.resolve` anchors a relative path to it, and without one the
    anchor would be this process's cwd, so a gate keyed on the wrong file would block and permit the
    wrong things, which is worse than the unguarded behaviour it replaces. `ChangeLog` makes the same
    call for the same reason. A remote session anchors at the POSIX workspace and digests through the
    remote backend, because the gate must compare the remote file and never a local spelling of it."""
    from harness.safety.permissions import write_paths as locate

    if p.remote:
        from harness.safety.provenance import resolve_remote

        backend = p.world.read_backend(p.root_list)  # type: ignore[union-attr]
        home = str(getattr(getattr(p.world, "session", None), "home", "") or "")
        engine.observed = ObservedFiles(
            resolve=lambda raw: resolve_remote(raw, p.remote_root, home), access=backend
        )
    else:
        from harness.safety.provenance import resolve

        engine.observed = ObservedFiles(resolve=lambda raw: resolve(raw, p.ws))
    # A resumed session's reads happened in a process that is gone, and the model still carries them,
    # so the transcript is what establishes them again.
    if p.inputs.messages:
        seed_from_messages(engine.observed, p.inputs.messages)
    engine.pipeline.on_check(observation_gate(engine.observed, locate))
    engine.pipeline.on_post_execute(observation_recorder(engine.observed, locate))


def _attach_memory(engine: TurnEngine, p: FeatureContext) -> None:
    """The tools' door into the memory folder: the write runs, the file it left is
    judged, and one that is not admitted is put back. A call that cannot say where it writes is judged
    by the folder, when it runs alone."""
    from harness.safety.permissions import write_paths as locate
    from harness.safety.provenance import resolve
    from harness.tools.memory_admission import memory_admission

    anchor = project_anchor([root.path for root in p.root_list if not root.managed], p.ws)
    w = p.witnesses
    engine.pipeline.on_execute(
        memory_admission(
            [Admission(p.memory_root, anchor=anchor, stamp=w.stamp, unsettled=w.unsettled, external=w.external)],
            locate,
            lambda raw: resolve(raw, p.ws),
            alone=lambda execution: engine.runs_alone(execution.name, execution.arguments),
        )
    )
    # What this project has repaired: the harness's own record, beside the memories and not
    # one of them. A remote session's files are on another host, so their digests could not be taken
    # here.
    if not p.remote:
        from harness.context.memory.repairs import Repairs

        engine.repairs = Repairs(
            p.memory_root,
            resolve=anchor,
            stamp=w.stamp,
            # A lesson is asked for only while the person's memory setting allows saving one.
            asks=lambda: p.inputs.auto_memory_enabled is None or bool(p.inputs.auto_memory_enabled()),
        )


def _attach_receipt_and_durability(engine: TurnEngine, p: FeatureContext) -> None:
    """The receipt, then the flush. The receipt (item 4.1) is a third pre-execute listener, and the
    policy split on that stage is the interesting thing about it: intents must block when they fail,
    while snapshots and receipts must not. Each says so in its own module, and none of them infers it
    from the stage."""
    from harness.safety.permissions import write_paths
    from harness.safety.provenance import resolve
    from harness.safety.risk import RiskClass, classify
    from harness.session.changes import ChangeLog, change_listener
    from harness.session.durability import durable_before_effect
    from harness.tools.contract import safe_present_call

    registry, permissions = p.registry, p.permissions

    def describe(name: str, arguments: dict) -> str:
        """The tool's own sentence, the one the approval card and the transcript already show. A
        second vocabulary here would be a second thing to keep in step."""
        shown = safe_present_call(registry.contract(name), name, arguments)
        return shown.summary if shown else name

    def risk_of(name: str) -> RiskClass:
        spec = registry.get(name)
        return classify(name, spec.metadata if spec else None, permissions.risk_overrides)

    def consequential(name: str) -> bool:
        return risk_of(name) is not RiskClass.READ

    engine.changes = ChangeLog(
        locate=write_paths,
        describe=describe,
        consequential=consequential,
        # The same anchor and the same rule the permission engine scopes by.
        resolve=(lambda raw: resolve(raw, p.ws)) if p.ws is not None else None,
        # Read at summary time, when the manager has long since attached the undo store to the engine.
        # The receipt's per-file Undo button is real exactly when this says so.
        undoable=lambda call_id: bool(
            getattr(engine, "undo", None) is not None and engine.undo.undoable(call_id)
        ),
        # A command names no path, so what it changed is read from git (G4). The tools classed EXEC
        # are the ones that can write files without saying which.
        root=str(p.ws) if p.ws is not None else None,
        opaque=lambda name: risk_of(name) is RiskClass.EXEC,
    )
    engine.pipeline.on_pre_execute(change_listener(engine.changes))
    # Registered last of the pre-execute listeners, so it is the innermost and the closest to the call
    # actually running. A call another gate refuses then never pays for a flush, and a call that
    # proceeds is known to have its record on disk before the effect and not after it
    # (`harness/session/durability.py`). It does nothing when the host installed no flush, which is
    # every test and the CLI.
    engine.pipeline.on_pre_execute(
        durable_before_effect(lambda: engine.flush, consequential=lambda name, args: consequential(name))
    )
