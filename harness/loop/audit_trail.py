"""AuditTrailMixin: the facts the engine records about a tool call for an audit sink."""

from __future__ import annotations

from typing import Any

from harness.context import session_facts
from harness.llm.providers import ToolCall


class AuditTrailMixin:
    def _note_ingestion(self, tool_call: ToolCall, status: str) -> None:
        """Record that outside content entered this session, and from where. Only the fact and the
        source are kept, never the content, not even truncated.

        Nothing reads this yet. It exists so a future judge of proposed actions can replay a shadow
        run and not argue the question again (see `session_facts.py` and the spec's Part 0). A failed
        call is skipped, because a fetch that errored brought nothing in."""
        if self.session_facts is None or status != "ok":
            return
        spec = self.registry.get(tool_call.name)
        if not session_facts.is_ingesting(spec.metadata if spec else None):
            return
        record = self.session_facts.note(tool_call.name, tool_call.arguments)
        self._audit(tool_call, **record.to_audit())

    def _audit(self, tool_call: ToolCall, **event: Any) -> None:
        if self.audit_sink is None:
            return
        payload = {**self.audit_context, "tool": tool_call.name, "arguments": tool_call.arguments, **event}
        try:
            self.audit_sink(payload)
        except Exception:
            pass  # an audit sink must never cost a turn
