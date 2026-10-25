"""PermissionBrokerMixin — the session-scoped permission queue.

A map of pending asks whose deferreds the tools await; a reply of `once`/`always`/`reject`
resolves them, `always` accumulates approval rules and resolves matching pending asks of the
same session, and `reject` cascades to every other pending ask of that session. The GUI half is
the composer dock plus a toast when the ask belongs to a session you are not viewing.

The registry lives on the SessionManager (`{request_id: PendingPermission}`), because a child
engine must ask across a thread boundary — its approver runs on the turn's loop while the reply
arrives on the server loop. Two frames ride the existing transport (like `session_created` /
`session_status` / `tool_metadata`):

    {"type": "permission_asked", "data": {
        "request_id", "session_id", "parent_session", "permission", "patterns",
        "metadata", "always", "tool"}}

    {"type": "permission_replied", "data": {"request_id", "session_id", "reply"}}

`permission_asked` goes to the requesting session's PARENT stream (the viewed ancestor's
dock), or to the requesting session itself when it is a root, AND to the app-wide stream
(the toast while you are elsewhere). `permission_replied` rides the same two targets so
the dock clears wherever it was drawn. `tool` is `{"call_id": …}` when the engine had one
(calls are named, messages are not).

`always` grants are SESSION-scoped, not instance-global. Authority granted in one conversation —
or one child — must not silently cover another session; it is the same rule the fork path states
when it refuses to carry grants across a branch. Within the session, a grant auto-resolves the
other pending asks its patterns cover.
"""
from __future__ import annotations

import asyncio
import uuid
from harness.loop import ApprovalOutcome

from dataclasses import dataclass, field
from typing import Any, Optional


from harness.tools.task_agents import ask_permission_covered, reject_message


@dataclass
class PermissionOutcome:
    """One settled ask, as the awaiting approver reads it.

    `reply` is `once`, `always` or `reject`; `feedback` is the
    optional text a reject carries. `denial_message` is what the MODEL is told — the
    exact `CorrectedError`/`RejectedError` strings.
    """

    reply: str
    feedback: str = ""

    @property
    def allowed(self) -> bool:
        return self.reply != "reject"

    @property
    def denial_message(self) -> str:
        return reject_message(self.feedback)


@dataclass
class PendingPermission:
    """One parked ask: the info the frames carry, plus where to resolve it."""

    request_id: str
    session_id: str
    parent_id: Optional[str]
    permission: str
    patterns: list[str]
    metadata: dict[str, Any] = field(default_factory=dict)
    always: list[str] = field(default_factory=list)
    tool: Optional[dict[str, Any]] = None
    loop: Optional[asyncio.AbstractEventLoop] = None
    future: Optional[asyncio.Future] = None

    def info(self) -> dict[str, Any]:
        """The REST/GET shape — no future, no loop: transport state only."""
        return {
            "request_id": self.request_id,
            "session_id": self.session_id,
            "parent_session": self.parent_id,
            "permission": self.permission,
            "patterns": list(self.patterns),
            "metadata": dict(self.metadata),
        }


class PermissionBrokerMixin:
    """`request_permission` / `reply_permission` + the two frames."""

    # -- asking ------------------------------------------------------------------
    def request_permission(
        self,
        *,
        session_id: str,
        parent_id: Optional[str] = None,
        permission: str,
        patterns: Optional[list[str]] = None,
        metadata: Optional[dict[str, Any]] = None,
        always: Optional[list[str]] = None,
        tool: Optional[dict[str, Any]] = None,
    ) -> "asyncio.Future[PermissionOutcome]":
        """Register one ask and return the future its approver awaits.

        A session grant that already covers every pattern answers immediately with no
        frame: the ask is re-evaluated against the accumulated approvals before it is
        published. Otherwise the future parks until `reply_permission` (or a cancel) settles it.
        """
        try:
            loop: Optional[asyncio.AbstractEventLoop] = asyncio.get_running_loop()
        except RuntimeError:
            # An off-loop caller (an HTTP test, a tool bridge): park on the manager loop.
            loop = self._loop
        if loop is None:
            raise RuntimeError("no event loop available to park a permission request")

        patterns = [str(p) for p in (patterns or [])]
        # Full access asks nobody: an ask from a session the person runs in full access,
        # or from a child of one (a spawn ask, a child's ruleset ask), is answered here and never
        # reaches a dock.
        if self._permission_covered(session_id, permission, patterns) or self._in_full_access(session_id, parent_id):
            future: "asyncio.Future[PermissionOutcome]" = loop.create_future()
            future.set_result(PermissionOutcome("always"))
            return future

        request_id = "per_" + uuid.uuid4().hex[:10]
        pending = PendingPermission(
            request_id=request_id,
            session_id=session_id,
            parent_id=parent_id,
            permission=permission,
            patterns=patterns,
            metadata=dict(metadata or {}),
            always=[str(p) for p in (always or [])],
            tool=tool,
            loop=loop,
            future=loop.create_future(),
        )
        self._permission_pending[request_id] = pending
        self._emit_permission_frame(
            pending.parent_id or pending.session_id,
            {
                "type": "permission_asked",
                "data": {
                    "request_id": request_id,
                    "session_id": session_id,
                    "parent_session": parent_id,
                    "permission": permission,
                    "patterns": patterns,
                    "metadata": pending.metadata,
                    "always": pending.always,
                    "tool": tool,
                },
            },
        )
        return pending.future

    # -- replying -----------------------------------------------------------------
    def reply_permission(
        self, request_id: str, reply: str, feedback: str = ""
    ) -> dict[str, Any]:
        """Settle one ask with `once` / `always` / `reject`.

        - `once` resolves just this ask.
        - `always` records the ask's `always` patterns (falling back to its matched
          `patterns`) as a SESSION grant, then auto-resolves every other pending ask of
          the same session whose patterns the grant covers, scoped to
          the session instead of the instance.
        - `reject` rejects every other pending ask of the same session (their plain
          `RejectedError` text) and this one with the feedback when given.

        Returns `{ok: true}` or `{ok: false, error}` for an unknown / already-answered /
        malformed request, which is also what the REST route answers.
        """
        pending = self._permission_pending.get(request_id)
        if pending is None:
            return {"ok": False, "error": "unknown permission request"}
        if pending.future is not None and pending.future.done():
            return {"ok": False, "error": "this permission request was already answered"}
        reply = str(reply or "")
        if reply not in ("once", "always", "reject"):
            return {"ok": False, "error": f"unknown reply: {reply}"}
        feedback = str(feedback or "")

        self._settle_permission(pending, PermissionOutcome(reply, feedback))
        followed: list[tuple[PendingPermission, str]] = []

        if reply == "always":
            grants = self._permission_grants.setdefault(pending.session_id, [])
            for pattern in pending.always or pending.patterns:
                if (pending.permission, pattern) not in grants:
                    grants.append((pending.permission, pattern))
            for other in list(self._permission_pending.values()):
                if other.session_id != pending.session_id:
                    continue
                if self._permission_covered(
                    other.session_id, other.permission, other.patterns
                ):
                    self._settle_permission(other, PermissionOutcome("always"))
                    followed.append((other, "always"))
        elif reply == "reject":
            # The cascade: denying one ask in a
            # session denies every other ask still parked in it. The others get the
            # plain reject text, never this reply's feedback — feedback answers THIS
            # request, and a second card the person never saw must not read as if they
            # wrote about it.
            for other in list(self._permission_pending.values()):
                if other.session_id != pending.session_id:
                    continue
                self._settle_permission(other, PermissionOutcome("reject"))
                followed.append((other, "reject"))

        for item, item_reply in [(pending, reply), *followed]:
            self._emit_permission_frame(
                item.parent_id or item.session_id,
                {
                    "type": "permission_replied",
                    "data": {
                        "request_id": item.request_id,
                        "session_id": item.session_id,
                        "reply": item_reply,
                    },
                },
            )
        return {"ok": True}

    def pending_permissions(self) -> list[dict[str, Any]]:
        """The parked asks, oldest first — the `GET /v1/permissions` body."""
        return [p.info() for p in self._permission_pending.values()]

    # -- internals -----------------------------------------------------------------
    def _permission_covered(
        self, session_id: str, permission: str, patterns: list[str]
    ) -> bool:
        """Whether the session's grants already allow every pattern of a would-be ask.

        A request is covered only when EVERY one of its patterns evaluates to `allow` against the grants
        accumulated for this session.
        """
        return ask_permission_covered(
            self._permission_grants.get(session_id), permission, patterns
        )

    def _settle_permission(
        self, pending: PendingPermission, outcome: PermissionOutcome
    ) -> None:
        """Resolve one parked future, from any thread. Idempotent: a Stop racing a reply
        must not double-set, and the registry entry always goes."""
        self._permission_pending.pop(pending.request_id, None)
        future = pending.future
        if future is None or future.done():
            return

        def _set() -> None:
            if not future.done():
                future.set_result(outcome)

        loop = pending.loop
        try:
            here = asyncio.get_running_loop()
        except RuntimeError:
            here = None
        if loop is None or loop is here:
            _set()
        else:
            loop.call_soon_threadsafe(_set)

    def _emit_permission_frame(self, session_id: str, frame: dict[str, Any]) -> None:
        """Broadcast one frame to the session stream AND app-wide, from any thread.

        Same bridge shape as `_subagent_metadata_emitter`: on the turn's loop the task is
        created directly; off-loop it is handed to the manager loop with
        `run_coroutine_threadsafe`. Best-effort throughout — a frame that cannot be
        delivered must never fail the ask it belongs to.
        """
        coro = self._broadcast_permission_frame(session_id, frame)
        try:
            asyncio.get_running_loop().create_task(coro)
            return
        except RuntimeError:
            pass
        loop = self._loop
        if loop is not None and loop.is_running():
            try:
                asyncio.run_coroutine_threadsafe(coro, loop)
                return
            except RuntimeError:
                pass
        coro.close()

    async def _broadcast_permission_frame(
        self, session_id: str, frame: dict[str, Any]
    ) -> None:
        try:
            await self.broadcast_session(session_id, frame)
        except Exception:
            pass
        # App-wide: the toast while the user is elsewhere. `_announce_session_event` is
        # the same best-effort wrapper `session_created` rides.
        await self._announce_session_event(frame)

    def _cancel_permission_requests(self, session_id: str) -> None:
        """Resolve every ask a cancelled session still has parked (no dangling futures).

        Failing the deferreds is not enough; the DOCK must be cleared too, because the two frames are what draw and clear it — a cancelled child's
        prompt would otherwise sit there until a reply answers nothing.
        """
        for pending in list(self._permission_pending.values()):
            if pending.session_id != session_id:
                continue
            self._settle_permission(pending, PermissionOutcome("reject"))
            self._emit_permission_frame(
                pending.parent_id or pending.session_id,
                {
                    "type": "permission_replied",
                    "data": {
                        "request_id": pending.request_id,
                        "session_id": pending.session_id,
                        "reply": "reject",
                    },
                },
            )

    # -- the approvers -------------------------------------------------------------
    def _broker_approver(self, child_id: str, parent_id: Optional[str]):
        """The child engine's approver: ask through the broker, answer the engine.

        The engine's `rule_decider` runs BEFORE the approver is ever called (it sits
        above the risk shortcuts in `PermissionEngine.evaluate`), so the only asks that
        reach here are the ruleset's `ask` fall-through and the engine's own floors/gate
        — a ruleset `allow` never prompts (the F-fix). The request's value is the same
        string the ruleset matched (`rule_value`), and `always` is the standing-rule
        candidate when the call has one (`standing_rule_candidate`), else the matched
        value — so "Allow always" records the narrowest thing the person was shown.
        """
        from harness.safety.permissions import standing_rule_candidate
        from harness.tools.task_agents import rule_value

        async def approve(request):
            tool_name = request.tool_name
            value = rule_value(tool_name, request.arguments)
            patterns = [value] if value else ["*"]
            candidate = standing_rule_candidate(
                tool_name, request.arguments, request.metadata
            )
            always = [candidate] if candidate else list(patterns)
            future = self.request_permission(
                session_id=child_id,
                parent_id=parent_id,
                permission=tool_name,
                patterns=patterns,
                metadata={},
                always=always,
                tool=(
                    {"call_id": request.tool_call_id}
                    if request.tool_call_id
                    else None
                ),
            )
            outcome = await future
            if not outcome.allowed:
                # `authorize.py` reads this off the request when the outcome is DENY
                # (`PermissionRequest.denial_message`) — the standard reject string.
                request.denial_message = outcome.denial_message
                return ApprovalOutcome.DENY
            return ApprovalOutcome.ONCE

        return approve

    def _permission_asker(self, session_id: str):
        """The `task` tool's sync door into the broker for its spawn ask.

        The tool runs on a worker thread; the ask must park its future on the manager
        loop and block until it is answered — `_spawn_subagent_sync`'s exact bridge. A
        manager with no loop (a bare test) runs the wait on a private loop so the ask
        still works; the CLI/thread path never receives this callable at all
        (`build_engine` passes it only from the manager), so it keeps today's behaviour.

        Returns the reply verbatim ("once" / "always" / "reject").
        """

        def ask(
            *,
            permission: str,
            patterns: list[str],
            metadata: Optional[dict[str, Any]] = None,
            always: Optional[list[str]] = None,
            tool: Optional[dict[str, Any]] = None,
        ) -> str:
            kwargs = {
                "session_id": session_id,
                "parent_id": self._permission_parent_of(session_id),
                "permission": permission,
                "patterns": list(patterns or []),
                "metadata": dict(metadata or {}),
                "always": list(always or []),
                "tool": tool,
            }
            loop = self._loop
            if loop is None or loop.is_closed():
                return asyncio.run(self._await_permission(**kwargs))
            future = asyncio.run_coroutine_threadsafe(
                self._await_permission(**kwargs), loop
            )
            return future.result()

        return ask

    async def _await_permission(self, **kwargs: Any) -> str:
        outcome = await self.request_permission(**kwargs)
        return outcome.reply

    def _in_full_access(self, session_id: str, parent_id: Optional[str] = None) -> bool:
        """True when this session or a parent it routes asks to runs in full access. The parent
        counts because the person chose the preset for the conversation they see, and a child's
        ask would land in that conversation's dock. `parent_id` is the routing parent the ask
        named, checked before the stored lineage."""
        engines = getattr(self, "_engines", {})
        seen: set[str] = set()
        pending = [sid for sid in (session_id, parent_id) if sid]
        while pending:
            current = pending.pop(0)
            if current in seen:
                continue
            seen.add(current)
            engine = engines.get(current)
            if engine is not None and getattr(getattr(engine, "permissions", None), "full_access", False):
                return True
            parent = self._permission_parent_of(current)
            if parent:
                pending.append(parent)
        return False

    def _permission_parent_of(self, session_id: str) -> Optional[str]:
        """The routing parent of a session, for the frame and the dock.

        Only a `kind="subagent"` session has one: a FORK records lineage in the same
        column but is an independent root (step A), and a workspace session has
        none — its own stream is the dock's target.
        """
        record = self.session_store.load(session_id)
        if record is None or record.kind != "subagent":
            return None
        return record.parent_session
