"""Stream blocks — a step as it actually happened, in order.

A step is rarely "one thought and one answer". It is reasoning, then a sentence, then a
tool call, then more reasoning — and the ORDER is the thing a person watching the screen
is reading. `StreamChunk` carries a flat pair of deltas, which loses that order the moment
a step does more than one thing: two buckets cannot say which came first.

One option is to solve it in the wire protocol: adapters emit
`block-start` / `*-delta` / `block-end` correlated by a block `index`, and the client folds
them back into ordered blocks. Ported here, with one deliberate difference — **the index is
assigned on this side of the provider**, not by each adapter. Six providers stream, their
wire protocols number blocks differently (Anthropic indexes content blocks, OpenAI indexes
only tool calls, Gemini numbers nothing), and making all six agree on one numbering would
put six chances of being wrong where one will do. Arrival order is the only thing they all
agree on already, and arrival order is exactly what a block index has to preserve.

The rules, and there are only three:

  · A block OPENS when the kind changes. Reasoning after text is a new block, text after
    reasoning is a new block, and text after a tool call is a new block.
  · A tool call is keyed by the PROVIDER's own call index, because argument fragments for
    two concurrent calls interleave and each has to land in its own block.
  · Nothing closes a block. A block ends when the next one opens or the turn does, so
    there is no `block-end` here: that design needs one because its adapters deliver the assembled
    block, while reeve's providers deliver the whole `AssistantTurn` at the end and
    the surface swaps the partial for it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .base import StreamChunk, ToolCallDelta

#: The three kinds a block can be. A surface renders each differently, and the kind is
#: fixed when the block opens — a block never changes what it is.
TEXT = "text"
REASONING = "reasoning"
TOOL_CALL = "tool_call"


@dataclass(frozen=True)
class BlockDelta:
    """One piece of one block, addressed by `index`.

    Frozen because it is an observation on its way to a surface: the engine yields it and
    forgets it, and a consumer that could edit one would be editing history.
    """

    index: int
    kind: str
    #: text / reasoning
    text: str = ""
    #: tool_call — the id and name arrive with the first fragment on most providers, and
    #: an empty string means "not yet known", never "absent".
    call_id: str = ""
    name: str = ""
    arguments_delta: str = ""

    def as_dict(self) -> dict:
        """The surface payload. Only the fields this kind uses, so a reader never has to
        decide whether an empty `name` on a text block means anything."""
        base = {"index": self.index, "block": self.kind}
        if self.kind == TOOL_CALL:
            return {
                **base,
                "call_id": self.call_id,
                "name": self.name,
                "arguments_delta": self.arguments_delta,
            }
        return {**base, "text": self.text}


class BlockAssembler:
    """Assigns block indexes to one step's stream. One instance per step.

    Stateful and deliberately tiny: the whole model is "what kind is the open block, and
    which block does this provider tool index belong to".
    """

    def __init__(self) -> None:
        #: Kind per block index, in arrival order. Its length IS the next index.
        self._kinds: list[str] = []
        #: The block a same-kind delta continues, or None before the first delta.
        self._open: Optional[int] = None
        #: Provider tool-call index → block index. A provider streaming two calls at once
        #: reuses its own indexes to interleave them; without this map their argument
        #: fragments would concatenate into one unparseable string.
        self._tool_blocks: dict[int, int] = {}

    @property
    def kinds(self) -> list[str]:
        """The blocks opened so far, in order. For tests and for a caller that wants to
        know whether a step ever said anything visible."""
        return list(self._kinds)

    def push(self, chunk: StreamChunk) -> list[BlockDelta]:
        """Fold one provider chunk into block deltas.

        Returns a list because one chunk may legally carry several kinds at once — the
        dataclass allows it, and a provider that batches a thought and a sentence into one
        chunk must not lose one of them. The final `turn` carries no delta: it is the
        assembled answer, not a piece of one.
        """
        out: list[BlockDelta] = []
        if chunk.reasoning_delta:
            out.append(self._text_delta(REASONING, chunk.reasoning_delta))
        if chunk.text_delta:
            out.append(self._text_delta(TEXT, chunk.text_delta))
        if chunk.tool_call_delta is not None:
            out.append(self._tool_delta(chunk.tool_call_delta))
        return out

    def _text_delta(self, kind: str, text: str) -> BlockDelta:
        if self._open is None or self._kinds[self._open] != kind:
            self._open = self._new_block(kind)
        return BlockDelta(index=self._open, kind=kind, text=text)

    def _tool_delta(self, delta: ToolCallDelta) -> BlockDelta:
        index = self._tool_blocks.get(delta.index)
        if index is None:
            index = self._new_block(TOOL_CALL)
            self._tool_blocks[delta.index] = index
        # The open block follows the tool call, so text resuming afterwards opens a fresh
        # text block rather than appending to the one before the call.
        self._open = index
        return BlockDelta(
            index=index,
            kind=TOOL_CALL,
            call_id=delta.id,
            name=delta.name,
            arguments_delta=delta.arguments_delta,
        )

    def _new_block(self, kind: str) -> int:
        self._kinds.append(kind)
        return len(self._kinds) - 1
