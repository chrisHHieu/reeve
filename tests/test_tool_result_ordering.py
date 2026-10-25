"""Tool responses must form one contiguous run after their assistant message.

`read_image` returns its picture as a separate user-role message (no provider takes an
image inside a tool result). Appended inline, two parallel reads produced
`assistant(A,B), tool(A), user(image A), tool(B), user(image B)` — DeepSeek rejects that
("insufficient tool messages following tool_calls message") while lenient providers
tolerate it. The fix is write-path (attachments queue until the batch ends) plus a
PROJECTION normalizer, so sessions already saved with the interleaved order are repaired
on the wire without rewriting disk state.
"""

from __future__ import annotations

import asyncio

import aisuite as ai

from harness.loop import TurnEngine
from harness.loop.message_shapes import order_tool_responses as _order_tool_responses
from harness.safety.permissions import PermissionEngine
from harness.llm.providers import AssistantTurn, ModelCapabilities, ProviderClient, ToolCall
from harness.tools import ToolRegistry
from harness.session.turns import ATTACHED_BY


class _Scripted(ProviderClient):
    def __init__(self, turns):
        self._turns = list(turns)
        self.seen: list[list] = []

    def complete(self, *, model, messages, tools=None, **settings):
        self.seen.append([dict(m) for m in messages])
        return self._turns.pop(0)

    def capabilities(self, model):
        return ModelCapabilities()


def _image() -> dict:
    return {"_attach": [{"type": "image_url", "image_url": {"url": "data:image/png;base64,AA"}}]}


def _order(messages) -> list[str]:
    """A compact label per message: role + call id/attachment tag.

    Canonical messages carry `_attached_by`; the outbound view strips every sidecar, so
    the test's image bodies ("imgA"/"imgB") stand in for the tag there.
    """
    out = []
    for m in messages:
        role = m.get("role")
        content = m.get("content")
        if role == "tool":
            out.append(f"tool:{m.get('tool_call_id')}")
        elif role == "assistant" and m.get("tool_calls"):
            out.append("assistant:" + ",".join(tc["id"] for tc in m["tool_calls"]))
        elif m.get(ATTACHED_BY):
            out.append(f"attach:{m[ATTACHED_BY]}")
        elif role == "user" and isinstance(content, str) and content.startswith("img"):
            out.append(f"attach-content:{content}")
        else:
            out.append(str(role))
    return out


# -- the projection normalizer ------------------------------------------------------


def test_attachments_move_after_the_last_result():
    messages = [
        {"role": "assistant", "tool_calls": [{"id": "a"}, {"id": "b"}]},
        {"role": "tool", "tool_call_id": "a", "content": "A"},
        {"role": "user", "content": "imgA", ATTACHED_BY: "a"},
        {"role": "tool", "tool_call_id": "b", "content": "B"},
        {"role": "user", "content": "imgB", ATTACHED_BY: "b"},
        {"role": "assistant", "content": "done"},
    ]
    assert _order(_order_tool_responses(messages)) == [
        "assistant:a,b",
        "tool:a",
        "tool:b",
        "attach:a",
        "attach:b",
        "assistant",
    ]


def test_already_ordered_runs_are_left_alone_and_the_fix_is_idempotent():
    messages = [
        {"role": "assistant", "tool_calls": [{"id": "a"}, {"id": "b"}]},
        {"role": "tool", "tool_call_id": "a", "content": "A"},
        {"role": "tool", "tool_call_id": "b", "content": "B"},
        {"role": "user", "content": "imgA", ATTACHED_BY: "a"},
        {"role": "user", "content": "imgB", ATTACHED_BY: "b"},
    ]
    once = _order_tool_responses(messages)
    assert once == messages
    assert _order_tool_responses(once) == once


def test_a_real_user_message_still_ends_the_run():
    messages = [
        {"role": "assistant", "tool_calls": [{"id": "a"}]},
        {"role": "tool", "tool_call_id": "a", "content": "A"},
        {"role": "user", "content": "thanks"},
    ]
    assert _order_tool_responses(messages) == messages


# -- the write path -----------------------------------------------------------------


def _multi_tool_turn(pairs):
    return AssistantTurn(
        tool_calls=[ToolCall(id=cid, name=name, arguments=args) for name, args, cid in pairs],
        finish_reason="tool_calls",
    )


def _tool_with_image(label: str):
    def _fn() -> dict:
        return {"label": label, **_image()}

    _fn.__name__ = label  # the registry keys on the function name
    return _fn


def test_parallel_attachments_are_appended_after_the_whole_run(tmp_path):
    low = ai.ToolMetadata(category="search", risk_level="low", requires_approval=False)
    provider = _Scripted(
        [
            _multi_tool_turn([("shot_a", {}, "a"), ("shot_b", {}, "b")]),
            AssistantTurn(text="done", finish_reason="stop"),
        ]
    )
    registry = ToolRegistry()
    registry.register(_tool_with_image("shot_a"), metadata=low)
    registry.register(_tool_with_image("shot_b"), metadata=low)
    engine = TurnEngine(
        provider=provider,
        registry=registry,
        permissions=PermissionEngine(workspace_root=tmp_path),
        model="gpt-5.5",
    )

    async def go():
        return [event async for event in engine.run("look")]

    asyncio.run(go())
    assert _order(engine.messages) == [
        "user",
        "assistant:a,b",
        "tool:a",
        "tool:b",
        "attach:a",
        "attach:b",
        "assistant",
    ]


def test_a_saved_interleaved_session_is_repaired_on_the_wire(tmp_path):
    """The exact shape session `1e5e635f` had on disk: the normalizer fixes the request
    without touching canonical history."""
    engine = TurnEngine(
        provider=_Scripted([]),
        registry=ToolRegistry(),
        permissions=PermissionEngine(workspace_root=tmp_path),
        model="gpt-5.5",
        messages=[
            {"role": "assistant", "tool_calls": [{"id": "a"}, {"id": "b"}]},
            {"role": "tool", "tool_call_id": "a", "content": "A"},
            {"role": "user", "content": "imgA", ATTACHED_BY: "a"},
            {"role": "tool", "tool_call_id": "b", "content": "B"},
            {"role": "user", "content": "imgB", ATTACHED_BY: "b"},
        ],
    )
    assert _order(engine._outbound_messages()) == [
        "assistant:a,b",
        "tool:a",
        "tool:b",
        "attach-content:imgA",
        "attach-content:imgB",
    ]
    # Canonical history keeps its old shape; only the wire is normalized.
    assert engine.messages[2].get(ATTACHED_BY) == "a"
