"""`/ws/session/{id}`: one client's live view of a session, and its way to drive it.

The socket carries the engine's event stream out and the person's messages, answers and
controls in. It is a view and not the owner: turns can also run in the background (channel
delivery, self-wake, durable resume), so this socket registers itself with the manager and
receives those events as well.
"""

from __future__ import annotations

import asyncio
import json
from collections import deque
from pathlib import Path
from typing import Optional

from fastapi import WebSocket, WebSocketDisconnect

from harness.integrations.inbox import VIS_INLINE
from harness.llm.attachments import build_user_content
from harness.orchestration.remote_env import RemoteEnvError
from harness.safety.permissions import Mode
from harness.session.events import CHECKPOINT_EVENTS, STEP_EVENTS

from .access import POLICY_VIOLATION, Gate
from .attached import KeptFiles, attached_rows
from .background import TaskKeeper
from .limits import RATE_LIMIT_COUNT, RATE_LIMIT_WINDOW_SECONDS
from .prompts import SessionPrompts
from .ready import ready_frame
from .replies import reply_for
from .user_message import Rejection, read_user_message


class RateWindow:
    """At most `RATE_LIMIT_COUNT` frames in any `RATE_LIMIT_WINDOW_SECONDS`."""

    def __init__(self) -> None:
        self._stamps: deque[float] = deque()

    def exceeded(self) -> bool:
        now = asyncio.get_running_loop().time()
        while self._stamps and now - self._stamps[0] > RATE_LIMIT_WINDOW_SECONDS:
            self._stamps.popleft()
        if len(self._stamps) >= RATE_LIMIT_COUNT:
            return True
        self._stamps.append(now)
        return False


class SessionSocket:
    def __init__(self, ws: WebSocket, manager, keeper: TaskKeeper, session_id: str) -> None:
        self.ws = ws
        self.manager = manager
        self.keeper = keeper
        self.session_id = session_id
        self.agent = ws.query_params.get("agent") or "reeve"
        self.workspace = ws.query_params.get("workspace")
        self.prompts = SessionPrompts(manager, ws, session_id, self.agent)
        self.engine = None

    # -- opening ---------------------------------------------------------------------

    async def _refuse(self, text: str, **extra) -> None:
        await self.ws.send_json({"type": "error", "data": {"error": text, **extra}})
        await self.ws.close()

    async def _open_engine(self) -> bool:
        manager, prompts = self.manager, self.prompts
        mcp_tools = await manager.arm_mcp(self.session_id, workspace=self.workspace, agent=self.agent)
        try:
            self.engine = manager.get_engine(
                self.session_id,
                workspace=self.workspace,
                agent=self.agent,
                approver=prompts.approver,
                extra_tools=mcp_tools,
                plan_approver=prompts.plan_approver,
                question_asker=prompts.question_asker,
                tool_requester=prompts.tool_requester,
            )
        except (RemoteEnvError, ValueError) as exc:
            # A bound session's world probes its host before it exists. An unreachable,
            # auth-refused or unsupported host, or a profile that vanished, refuses the session
            # with its typed message and never leaves it silently half-built.
            await self._refuse(str(exc) or "could not start the remote session", kind=getattr(exc, "kind", ""))
            return False
        if self.engine is None:
            await self._refuse("no valid workspace — choose a project folder first")
            return False
        return True

    def _note_failed_mcp_servers(self) -> None:
        # A server that failed to start while this session's tools were prepared leaves one
        # quiet, persistent notice, so the session does not silently lack its tools. `server`
        # makes it structured: the GUI shows one line with the error behind a disclosure and an
        # action that opens Connectors.
        for name, error in self.manager.pop_mcp_failures(self.session_id):
            detail = f": {error}" if error else ""
            self.engine._append_notice(
                "mcp_error", f"MCP server “{name}” failed to start{detail}"[:500], server=name
            )

    # -- running turns ---------------------------------------------------------------

    async def _broadcast(self, kind: str, data: dict) -> None:
        await self.manager.broadcast_session(self.session_id, {"type": kind, "data": data})

    async def _reject(self, reason: str) -> None:
        await self.ws.send_json({"type": "input_rejected", "data": {"error": reason}})

    async def _run_turn(
        self, content, *, retry: bool = False, display=None, mentions=None, attached=None, command=None
    ) -> None:
        # The receive loop has already claimed the session; claiming here would let two
        # back-to-back frames both start.
        manager, engine, sid = self.manager, self.engine, self.session_id
        try:
            # A source connected mid-conversation is picked up on the turn boundary, since the
            # socket opened before it existed. A no-op unless the configuration changed.
            await manager.arm_mcp(sid, workspace=self.workspace, agent=self.agent)
            # Someone typed. That resets the self-wake chain depth, which only exists to bound
            # an agent talking to itself.
            engine.wake_depth = 0
            events = (
                engine.retry()
                if retry
                else engine.run(content, display=display, mentions=mentions, attached=attached, command=command)
            )
            async for event in events:
                # Every socket viewing the session gets it, this one included, so a second view
                # stays in step.
                await self._broadcast(event.type.value, event.data)
                # Saved mid-turn so a crash or quit cannot eat the conversation. The same event
                # set drives the background turn (harness/session/events.py).
                if event.type in CHECKPOINT_EVENTS:
                    manager.save(sid, engine)
                if event.type in STEP_EVENTS:
                    manager.note_metrics_progress(sid)
        finally:
            manager.mark_idle(sid)
            manager.save(sid, engine)
            await self._broadcast("turn_done", {})

    async def _claim_turn(self, *, retry: bool = False, content=None, display=None, mentions=None,
                          steer: bool = False, attached=None, command=None) -> None:
        manager, engine = self.manager, self.engine
        if manager.engine_retired(engine):
            # `/reload-plugins` replaced this socket's engine and told every view to reconnect;
            # a turn here would run the old plugins beside the new engine.
            await self._reject("Plugins were reloaded — reconnecting to this conversation.")
            return
        if not manager.try_mark_running(self.session_id):
            # A steer goes into the LIVE turn at its next step, and its ack is what the surface
            # draws. Only plain text reaches here, and a steer on an idle session never takes
            # this path: it runs as an ordinary turn.
            if steer and isinstance(content, str) and content.strip():
                engine.queue_steering(content)
                # The person now waits on the step: its shell waits give up after a short grace,
                # and a subagent it waits for goes on in the background, so the message is read now.
                engine.person_steered()
                manager.promote_subagent(self.session_id)
                await self.ws.send_json({"type": "steering_queued", "data": {"text": content}})
                return
            await self._reject("This session is already running a turn. Wait for it to finish or stop it.")
            return
        if manager.shutting_down:
            # A turn claimed now would be killed mid-step by the teardown that follows the
            # grace window. Refusing is honest, and nothing is left half-run.
            manager.mark_idle(self.session_id)
            await self._reject("Reeve is closing — send this again when it is back.")
            return
        self.keeper.spawn(
            self._run_turn(
                content, retry=retry, display=display, mentions=mentions, attached=attached, command=command
            ),
            what=f"turn {self.session_id}",
        )

    # -- model, thinking, mode, plan -------------------------------------------------

    async def _apply_model(self, model: Optional[str]) -> None:
        # Switching mid-session is allowed: history is canonical and providers convert per
        # call. A real switch appends a persisted notice, broadcast so live views draw the
        # marker. Never mid-turn, since the running loop reads `engine.model` each iteration.
        if not model or self.manager.is_running(self.session_id):
            return
        before = self.engine.model
        thinking = getattr(self.engine, "thinking_level", None)
        notice = self.engine.switch_model(model)
        if self.engine.model == before:
            return
        # A level the new model does not offer is dropped by the switch; the views hear it, or
        # the picker keeps showing the old model's level.
        dropped = getattr(self.engine, "thinking_level", None) != thinking
        # The first bind on a fresh session has no notice (no history to mark), but the views
        # still hear the new model: a later `mode_changed` re-applies the facts they hold, and
        # a stale model there put the picker back on the default (owner-hit 2026-10-12).
        if notice is not None or dropped:
            self.manager.persist_session(self.session_id)
        await self._broadcast("model_changed", {"model": model, "text": notice})
        if dropped:
            await self._broadcast("thinking_changed", {"thinking": self.engine.thinking_level or "", "text": None})

    async def _apply_thinking(self, level: Optional[str]) -> None:
        # Same contract as the model: only between turns, a real change appends a notice.
        if self.manager.is_running(self.session_id):
            return
        before = getattr(self.engine, "thinking_level", None)
        notice = self.manager.set_session_thinking(self.session_id, level)
        after = getattr(self.engine, "thinking_level", None)
        if notice is not None or after != before:
            await self._broadcast("thinking_changed", {"thinking": after or "", "text": notice})

    async def _set_mode(self, message: dict) -> None:
        try:
            mode = Mode(message.get("mode"))
        except (TypeError, ValueError):
            return
        previous = self.engine.permissions.mode
        # `set_mode` is the one live write path: it logs the change and does nothing when the
        # mode did not move. The audit row and the broadcast follow a real change only.
        if self.engine.set_mode(mode):
            self.manager.audit_autonomy_change(self.session_id, "mode", previous.value, mode.value)
            await self._broadcast("mode_changed", {"mode": mode.value})

    async def _set_plan(self, message: dict) -> None:
        plan = message.get("plan")
        if not isinstance(plan, bool):
            await self._reject("Invalid plan: expected a boolean.")
            return
        previous = bool(self.engine.permissions.plan)
        if self.engine.set_plan(plan):
            self.manager.audit_autonomy_change(self.session_id, "plan", previous, plan)
            await self._broadcast("plan_changed", {"plan": plan})

    # -- a message from the person ---------------------------------------------------

    def _save_attachment(self, name: str, data: bytes) -> Optional[str]:
        """Write an attachment into the session scratch, a root the model can read, and return
        its path. The model gets a line naming the path and not the bytes. Best effort: on
        failure the reference text says it could not be saved."""
        try:
            folder = self.manager.scratch_base() / self.session_id / "attachments"
            folder.mkdir(parents=True, exist_ok=True)
            target = folder / (Path(name or "attachment").name or "attachment")
            index = 1
            while target.exists():
                target = folder / f"{target.stem}-{index}{target.suffix}"
                index += 1
            target.write_bytes(data)
            return str(target)
        except Exception:
            return None

    async def _user_message(self, message: dict) -> None:
        try:
            sent = read_user_message(self.manager, self.session_id, self.workspace, self.agent, message)
        except Rejection as rejection:
            await self._reject(str(rejection))
            return
        await self._apply_model(sent.model)
        if not (sent.text or sent.attachments):
            return
        engine = self.engine
        # A large text attachment spills into the session's output area and the model gets its
        # head plus the path. A rootless session has no sink, so it stays inline.
        sink = engine.spill.sink("attachment") if getattr(engine, "spill", None) is not None else None
        # What was attached, as the transcript draws it: each file is kept once in the
        # session's attachments folder, and the copy the model is told about is that same file.
        kept = KeptFiles(self._save_attachment)
        attached = attached_rows(sent.attachments, kept) or None
        content = build_user_content(
            sent.text,
            sent.attachments,
            spill=sink,
            save_file=kept.save,
            model=engine.model,
            provider=engine.provider,
        )
        await self._claim_turn(
            content=content,
            display=sent.display,
            mentions=sent.mentions or None,
            steer=sent.steer,
            attached=attached,
            command=sent.command,
        )

    # -- the receive loop ------------------------------------------------------------

    async def _handle(self, kind: str, message: dict) -> None:
        reply = reply_for(kind, message)
        if reply is not None:
            self.prompts.resolve_pending(reply)
        elif kind == "interrupt":
            # Stop cascades into `task` descendants: the session's engine is interrupted and
            # its subagent children follow, so a detached child can never outlive the Stop that
            # hit its parent.
            self.manager.cancel_session(self.session_id)
        elif kind == "retry":
            # Re-run after a provider error. The engine checks the error notice at the tail, so
            # a stray frame is a no-op that still ends with turn_done.
            await self._claim_turn(retry=True)
        elif kind == "set_mode":
            await self._set_mode(message)
        elif kind == "set_plan":
            await self._set_plan(message)
        elif kind in ("set_model", "set_thinking"):
            field, what = ("model", "model") if kind == "set_model" else ("thinking", "thinking level")
            value = message.get(field)
            if value is not None and not isinstance(value, str):
                await self._reject(f"Invalid {what}: expected a string.")
            elif kind == "set_model":
                await self._apply_model(value)
            else:
                await self._apply_thinking(value)
        elif kind == "user_message":
            await self._user_message(message)
        else:
            await self._reject(f"Unknown WebSocket message type: {kind}.")

    async def _receive_loop(self) -> None:
        rate = RateWindow()
        while True:
            try:
                message = await self.ws.receive_json()
            except (json.JSONDecodeError, UnicodeDecodeError):
                await self._reject("Invalid WebSocket message: expected JSON.")
                continue
            if rate.exceeded():
                await self._reject("Too many WebSocket messages; reconnect and try again.")
                await self.ws.close(code=POLICY_VIOLATION)
                return
            if not isinstance(message, dict):
                await self._reject("Invalid WebSocket message: expected an object.")
                continue
            kind = message.get("type")
            if not isinstance(kind, str):
                await self._reject("Invalid WebSocket message: missing string type.")
                continue
            await self._handle(kind, message)

    async def serve(self, gate: Gate) -> None:
        if not await gate.admit(self.ws):
            return
        if not await self._open_engine():
            return
        manager, engine = self.manager, self.engine
        self._note_failed_mcp_servers()
        # Only an attended session may be asked to Retry or Trim after a failed compaction;
        # unattended runs trim on their own (the policy in the engine's compaction).
        engine.is_attended = lambda: self.prompts.visibility() == VIS_INLINE
        await self.ws.send_json(ready_frame(manager, engine, self.session_id))
        # This socket is now a live view: background turns broadcast to it as well.
        manager.register_session_client(self.session_id, self.ws.send_json)
        try:
            await self._receive_loop()
        except WebSocketDisconnect:
            pass
        finally:
            manager.unregister_session_client(self.session_id, self.ws.send_json)
