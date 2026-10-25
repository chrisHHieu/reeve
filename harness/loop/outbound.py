"""OutboundMixin: the history as the provider is sent it.

`_outbound_messages` is the only feed to the provider. It never changes `self.messages`: nothing it
does is saved or replayed.
"""

from __future__ import annotations

from typing import Any, Optional

from harness.context import compaction
from harness.llm.wire import wire_messages
from harness.session import revert as _revert

from .message_shapes import answer_orphaned_calls, order_tool_responses

# "Project with the session's own compaction state." A sentinel is needed because None is a real
# state to project with: the view with no compaction at all.
CURRENT_STATE: Any = object()

_IMAGE_PLACEHOLDER = {"type": "text", "text": "[image attachment — not viewable by this model]"}


def _has_part(messages: list[dict[str, Any]], kind: str) -> bool:
    return any(
        isinstance(part, dict) and part.get("type") == kind
        for message in messages
        if isinstance(message.get("content"), list)
        for part in message["content"]
    )


class OutboundMixin:
    def _reasoning_echo(self) -> Optional[str]:
        """The wire field this route wants the model's reasoning sent back under, or None."""
        ask = getattr(self.provider, "reasoning_passback", None)
        return ask(self.model) if callable(ask) else None

    def _outbound_messages(self, state: Any = CURRENT_STATE) -> list[dict[str, Any]]:
        """`self.messages` projected for the provider, in six stages.

        1. Revert: a staged marker cuts the view to `messages[:boundary]` plus the tail
           written after the undo.
        2. Compaction: everything before the boundary becomes one block, outbound only.
        3. Each message in wire form (`harness/llm/wire.py` is the one list of what is display-only):
           sidecars dropped, `api_content` substituted, `notice` messages removed, reasoning echoed
           where the route needs it.
        4. A stub result for every tool call that has none (`answer_orphaned_calls`).
        5. PDF parts adapted when the active model cannot read them natively.
        6. Image parts replaced by a visible placeholder when it has no vision.

        The live `<system-context>` block is deliberately not a stage here. It is written into the
        user message once, at the start of the turn (`_stamp_live_context`), so a request is always
        the previous one plus an append, the property a provider's prefix cache is paid for.
        Stamping it here would decide it again on every render and rewrite a message the provider had
        already cached.

        Stages 3 to 6 decide again on every call, so a model switch in the middle of a session always
        does the right thing.

        `state` is the compaction state to project with: the session's own by default, or one being
        weighed (`_view_tokens`), so that a view only being priced passes through every stage the real
        one does."""
        # Revert runs first, on the canonical list. Compaction then applies to that view, but only
        # while its indexes still address it: a state whose summarized span holds hidden messages, or
        # whose prune range crosses the cut, is suspended until `/redo` clears the marker, because
        # sending its block would leak the undone span as a summary.
        view = _revert.revert_view(self.messages, self.revert_state)
        compaction_state = self.compaction_state if state is CURRENT_STATE else state
        if compaction_state is not None and not _revert.compaction_fits(compaction_state, self.revert_state):
            compaction_state = None
        # Everything before the boundary stands in as the compacted block. It is outbound only, the
        # canonical history stays whole, and the block and the tail are byte-stable between turns, so
        # prompt caching keeps working.
        view = compaction.apply_to_outbound(view, compaction_state, spill=self._prune_spill())
        # A tool result's attachment can sit between two parallel results in the canonical history
        # (the shape the engine used to write, and every session saved before that was fixed).
        # Providers need the whole tool run first.
        view = order_tool_responses(view)
        # The route decides whether the model's own thinking goes back on the wire. The thinking-mode
        # families (DeepSeek, Kimi, GLM, MiMo) answer 400 without it on tool-call turns, while
        # aggregators reject a hand-made field. It is asked per request, so a model switch decides it
        # again (`providers/reasoning.py`).
        out = wire_messages(view, echo=self._reasoning_echo())
        # A request holding a call with no recorded result is refused by the strict providers, and so
        # is every compaction replay of it.
        out = answer_orphaned_calls(out)
        out = self._adapt_pdfs(out)
        out = self._adapt_media(out)
        return self._adapt_images(out)

    def _adapt_pdfs(self, out: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """PDFs are stored as `file` parts and adapted to the active model here, never in the saved
        history. A model that reads them natively gets the document, and the rest get the local text
        extract and page images (pdf_support.py)."""
        if not _has_part(out, "file"):
            return out
        caps = self.provider.capabilities(self.model)
        if getattr(caps, "pdf", False):
            return out
        from harness.llm import pdf_support

        return [
            {**message, "content": pdf_support.adapt_content(message["content"], caps)}
            if isinstance(message.get("content"), list)
            else message
            for message in out
        ]

    def _adapt_media(self, out: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Audio and video travel in the same `file` part. A model that takes them gets the newest
        few, and one that does not is told the saved path (media_support.py)."""
        from harness.llm import media_support

        if not media_support.has_media(out):
            return out
        carry = getattr(self.provider, "media_carry", None)
        # A model that already refused a recording this session is not sent another, since it would
        # fail every turn the recording stays in the history.
        refused = self.model in getattr(self, "_media_refused", ())
        return media_support.adapt_messages(
            out,
            self.provider.capabilities(self.model),
            (lambda mime: carry(self.model, mime)) if callable(carry) and not refused else None,
        )

    def _adapt_images(self, out: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """A model without vision gets a visible placeholder in place of a payload it would reject.
        Like the PDF stage it decides again per call, so a switch to or from a vision model always
        does the right thing."""
        if not _has_part(out, "image_url"):
            return out
        if getattr(self.provider.capabilities(self.model), "vision", False):
            # A vision model gets every image fitted to its own route, not to the route of the
            # moment it was attached (`image_outbound.fit_outbound`): one image that does not fit is
            # resent, and refused, on every request after it.
            from harness.llm import image_outbound

            cache = getattr(self, "_image_fit_cache", None)
            if cache is None:
                cache = self._image_fit_cache = {}
            try:
                return image_outbound.fit_outbound(out, model=self.model, provider=self.provider, cache=cache)
            except Exception:
                return out
        return [
            {
                **message,
                "content": [
                    _IMAGE_PLACEHOLDER if isinstance(part, dict) and part.get("type") == "image_url" else part
                    for part in message["content"]
                ],
            }
            if isinstance(message.get("content"), list)
            else message
            for message in out
        ]
