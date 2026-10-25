"""ModelRequestMixin: building a request to the model and reading its stream."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from harness.context import compaction
from harness.llm.providers.errors import StreamStalled
from harness.safety.permissions import Mode
from harness.safety.risk import WRITE_TOOLS
from harness.session.invariants import InvariantError

logger = logging.getLogger(__name__)



def _gap_key(violation: str) -> str:
    """What a reported gap is about: a counted line without its number."""
    head, _, rest = violation.partition(" ")
    return rest if head.isdigit() else violation

class ModelRequestMixin:
    def _audit_model_visibility(self) -> None:
        """The model-visibility invariant (D1): nothing reaches the provider without a trace in the
        session log. It runs at the one place the provider is fed, so every request (tool turns,
        checkpoint handoffs, retries) is checked against the same record. A violation means some
        path added model-visible history without logging it. It is recorded as an
        `invariant/violation` event and not raised, because the log's own contract is that nothing
        there can break a turn (session_log.py); `invariant_mode="strict"` raises it for tests."""
        # `compaction_active` means a compacted block is in the outbound view, not that a compaction
        # state exists. A prune-only state (`boundary_index == 0`) only shortens tool results, and
        # demanding a `compaction/summary` event for it would report a block that is not there.
        violations = self._visibility_audit.check(
            self.log,
            self.messages,
            compaction_active=(
                self.compaction_state is not None and self.compaction_state.boundary_index > 0
            ),
        )
        if not violations:
            return
        # Each gap is reported once. A gap stays in every later request, so comparing whole lists
        # reported it again whenever a new one joined, and a count ("3 assistant message(s) …")
        # changed with every step: one gap became 235 rows in one session (2026-10-15). A count is
        # keyed by what it counts, not by the number.
        fresh = [v for v in violations if _gap_key(v) not in self._visibility_reported]
        if fresh:
            self._visibility_reported.update(_gap_key(v) for v in fresh)
            self.log.append(
                "invariant/violation", turn=self._turn_no, step=self._step_no, violations=fresh
            )
            logger.error("model-visible history is not fully logged: %s", "; ".join(fresh))
        if self.invariant_mode == "strict":
            raise InvariantError("model-visible history is not fully logged: " + "; ".join(violations))

    def _context_breakdown(self, messages: list[dict[str, Any]], *, tools_active: bool) -> dict[str, int]:
        """A chars/4 estimate of what this request is made of, by panel category.

        It uses the estimator the compaction trigger uses, so the numbers agree inside the product
        even though no provider tokenizer is consulted: the panel shows percentages, and
        percentages need consistency and not tokenizer truth.

        The categories are the system message; the conversation body (with the live
        `<system-context>` text written into this turn's user message carved back out, only when the
        full block was written this turn, and counted under skills or meta-context where it came
        from); the tool schemas, split by registry group (a synced group is MCP or external, and the
        ungrouped rest are built-ins); the skill menu; and the instruction files (AGENTS.md,
        CLAUDE.md, rules), carved out of the system prompt and the messages that carry them.
        One project in the store carried about 16k tokens of those on every request, and
        the panel showed it as "system prompt"."""
        from harness.session.project import instruction_chars

        def estimate(text: str) -> int:
            return len(text) // 4

        def instruction_tokens(chosen: list[dict[str, Any]]) -> int:
            total = 0
            for message in chosen:
                content = message.get("content")
                if isinstance(content, str):
                    total += instruction_chars(content)
                elif isinstance(content, list):
                    total += sum(
                        instruction_chars(part.get("text") or "")
                        for part in content
                        if isinstance(part, dict)
                    )
            return total // 4

        system = [m for m in messages if m.get("role") == "system"]
        system_est = compaction.estimate_tokens(system)
        total_est = compaction.estimate_tokens(messages)
        in_system = min(instruction_tokens(system), system_est)
        in_messages = instruction_tokens([m for m in messages if m.get("role") != "system"])
        ephemeral = self._last_ephemeral
        block_est = estimate(f"\n\n<system-context>\n{ephemeral}\n</system-context>") if ephemeral else 0
        # The skills row describes this turn's block. A clock-only update carries no menu, and a
        # full block from an earlier turn is conversation body by now, so counting it here would show
        # it twice, once in the body and once in skills.
        skills_est = (
            estimate(self.context_parts.get("skills", ""))
            if self.context_parts and self._last_context_full
            else 0
        )
        mcp_est = builtin_est = 0
        if tools_active:
            hidden = self._hidden_tool_names()
            for group, schema in self.registry.schemas_by_group():
                if (schema.get("function") or {}).get("name") in hidden:
                    continue
                size = len(json.dumps(schema, default=str, ensure_ascii=False)) // 4
                # Only MCP's group counts as MCP; the connectors' group is Reeve's own tools.
                if group == "mcp":
                    mcp_est += size
                else:
                    builtin_est += size
        messages_est = max(total_est - system_est - block_est, 0)
        in_messages = min(in_messages, messages_est)
        return {
            "messages": messages_est - in_messages,
            "mcp_tools": mcp_est,
            "system_tools": builtin_est,
            "skills": skills_est,
            "instructions": in_system + in_messages,
            "system_prompt": system_est - in_system,
            # Everything ephemeral that is not the skill menu (the clock, mode notices, the roots
            # list, countermands), plus the block wrapper itself.
            "meta_context": max(block_est - skills_est, 0),
        }

    def _reroute(self) -> bool:
        """Move the session to a new routing key for the rest of its life.

        A gateway pins a session to one backend by this key (an `x-…-session` header, or OpenAI's
        `prompt_cache_key`). When that backend is the fault (`errors.unexplained_refusal`), a retry on
        the same key reaches it again: a real session's retry 52s later failed the same way. Giving
        up the prefix cache on the old backend once costs far less than the turn."""
        from harness.llm.providers.base import CACHE_AFFINITY

        key = (self.model_settings or {}).get(CACHE_AFFINITY)
        if not key:
            return False
        self._reroutes = int(getattr(self, "_reroutes", 0)) + 1
        root = str(key).split("~", 1)[0]
        self.model_settings = {**self.model_settings, CACHE_AFFINITY: f"{root}~{self._reroutes}"}
        return True

    def _hidden_tool_names(self) -> frozenset[str]:
        """The tools this mode does not advertise (see `_model_tools`)."""
        if self.permissions.mode is Mode.READ_ONLY or self.permissions.plan:
            return WRITE_TOOLS
        return frozenset()

    def _model_tools(self) -> list[dict[str, Any]]:
        """The schemas this request advertises.

        Read-only and plan mode hide the tools whose risk is write by name. The boundary guard
        refuses every call to them anyway, so advertising them spends tokens and buys a refused round
        trip. Tools whose risk depends on the arguments (`run_shell`, `run_code`) stay, because a
        `git status` is a read."""
        hidden = self._hidden_tool_names()
        if not hidden:
            return self.registry.schemas()
        return [
            schema
            for schema in self.registry.schemas()
            if (schema.get("function") or {}).get("name") not in hidden
        ]

    async def _astream(self, *, with_tools: bool = True):
        """Carry the provider's blocking stream into the async loop through a thread and a queue, so
        text deltas appear live without blocking the event loop.

        Each wait races three things: the next chunk, the person's Stop, and the idle watchdog
        (`stream_idle_timeout`). A connection that wedges without closing used to hold the turn until
        the SDK's own ten-minute request timeout. That was the one failure where the loop had no
        shorter answer than the transport's, and it is the failure the retry taxonomy handles best,
        since re-issuing a dead stream usually just works. The watchdog fires only on silence: any
        chunk resets it, so a slow but live model is never cut off."""
        self._audit_model_visibility()
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue()
        tools = (self._model_tools() or None) if with_tools else None
        model, messages, settings = self.model, self._outbound_messages(), self.model_settings
        # The panel measures the request that is about to be billed, where the tool list and the
        # outbound view are both already in hand.
        self._last_breakdown = self._context_breakdown(messages, tools_active=with_tools and tools is not None)
        provider = self.provider
        # Set when the watchdog expires, so the producer thread stops reading the abandoned stream at
        # its next chunk. It may also be blocked for good, and then the SDK's own timeout is the only
        # thing that ends it, which is no worse than before.
        abandoned = [False]

        def produce():
            try:
                for chunk in provider.stream(model=model, messages=messages, tools=tools, **settings):
                    # The person pressed Stop: drop the stream between chunks. Reading the
                    # asyncio.Event's flag from a thread is safe, since we only read.
                    if self._cancel.is_set() or abandoned[0]:
                        break
                    loop.call_soon_threadsafe(queue.put_nowait, ("chunk", chunk))
            except Exception as exc:  # handed to the consumer that is waiting
                loop.call_soon_threadsafe(queue.put_nowait, ("error", exc))
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, ("done", None))

        loop.run_in_executor(None, produce)
        idle = self.stream_idle_timeout
        while True:
            # The queue races Stop so a stalled stream (no chunks arriving, as in the wait for the
            # first token or on a wedged connection) cannot hold the turn.
            next_chunk = asyncio.ensure_future(queue.get())
            stopped = asyncio.ensure_future(self._cancel.wait())
            done, _ = await asyncio.wait(
                {next_chunk, stopped}, return_when=asyncio.FIRST_COMPLETED, timeout=idle or None
            )
            stopped.cancel()
            if not done:
                # Nothing for the whole idle budget: the stream is declared dead so the loop's retry
                # policy re-issues it. It is classified transient by type (`providers/errors.py`),
                # so it follows the same budget as any other transport failure and there is no
                # second path.
                abandoned[0] = True
                next_chunk.cancel()
                raise StreamStalled(f"stream idle timeout after {idle:g}s with no chunk")
            if next_chunk not in done:
                next_chunk.cancel()
                return  # interrupted: the producer exits on its own at its next chunk
            kind, payload = next_chunk.result()
            if kind == "chunk":
                yield payload
            elif kind == "error":
                raise payload
            else:
                return
