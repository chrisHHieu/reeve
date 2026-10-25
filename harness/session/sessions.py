"""`SessionRecord`: one conversation as it is stored, its settings and its messages.

`harness.session.conversations.ConversationStore` keeps them: an index in SQLite, by project, and
each conversation's messages in a `.jsonl` file that is only ever appended to.

Fields are only ever added at the end. A few callers build a record by position, and a field put
in the middle would hand their values to the wrong names.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class SessionRecord:
    session_id: str
    workspace: str
    model: str
    mode: str
    messages: list[dict[str, Any]] = field(default_factory=list)
    title: Optional[str] = None
    agent: str = "reeve"
    message_count: int = 0
    updated_at: Optional[str] = None
    # Folders given to the conversation besides its scratch folder, each {path, writable, label}.
    # The scratch folder is made again whenever the engine is built, so only these are stored.
    extra_roots: list[dict[str, Any]] = field(default_factory=list)
    # What the person chose "Always allow" for here, {tools: [...], commands: [...]}. They belong
    # to the conversation, and the conversation outlives the process, so they are stored with it
    # (2026-07-22: every restart had forgotten them).
    grants: dict[str, Any] = field(default_factory=dict)
    pinned: bool = False
    archived: bool = False
    # Where a conversation the person did not start came from: a key and a label to show,
    # e.g. origin="slack", origin_label="#general · T0ABCD". Set once, when it is started.
    origin: Optional[str] = None
    origin_label: Optional[str] = None
    # `CompactionState.as_dict()`, {} until the first compaction; kept so a conversation
    # opened again sends the compacted view it had.
    compaction: dict[str, Any] = field(default_factory=dict)
    # Projects bound by hand ("memory", "board" -> a name in project_names); {} derives them from
    # the workspace.
    bindings: dict[str, Any] = field(default_factory=dict)
    # Agent teams; {} outside one. A worker has {team_id, role: "worker", actor, lead_session,
    # space}; a lead gets its entry when the staffing gate makes the team. It decides which board
    # identity the tools act as and the sidebar's expandable row.
    team: dict[str, Any] = field(default_factory=dict)
    # The thinking level by name (`low`, `high`, …); None is the model's own default. The name,
    # not provider settings, so every surface speaks the same words and the mapping stays in
    # `providers/variants.py`.
    thinking: Optional[str] = None
    # A fork's origin (`manager.fork_session`): the conversation it branched from and how many of
    # its messages it started with; None and 0 otherwise. Written when it is made and never by the
    # save after each turn, whose upsert leaves these columns alone, as it does `team`.
    parent_session: Optional[str] = None
    seed_length: int = 0
    # "root" for every conversation the person sees, a fork included (`parent_session` is where it
    # came from, not what holds it), and "subagent" for one a `task` call started.
    # Written once, when it is made.
    kind: str = "root"
    # A message-level undo: {} when none is pending, else {"boundary": int,
    # "staged_at": int, "ts": float}, the index the model's view stops at and how long the
    # transcript was then (harness/session/revert.py). Nothing is deleted; the view sent is cut.
    # Only the revert routes write it (`ConversationStore.set_revert`), never the per-turn save.
    revert: dict[str, Any] = field(default_factory=dict)
    # Plan: the conversation rehearses and does not act. Not a mode; it sits over `mode` and
    # refuses every consequential call.
    plan: bool = False
    # The SSH profile a remote workspace is reached through; None when local.
    # Set on insert and through `set_environment`; the per-turn save does not carry it.
    environment_id: Optional[str] = None
