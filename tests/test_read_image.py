"""`read_file` shows a picture (it was the `read_image` tool), and one definition of a
user turn.

Images only ever travelled one way: a person attaches one, the model sees it. So an agent
could draw a chart and never check it, be told "the report looks wrong" and not see the
report, take a screenshot and learn nothing. For something meant to do the work in a
person's place that is a missing sense, not a missing convenience.

The bytes cannot ride inside the tool result — a tool result is a string — so the engine
attaches them as a user-role message. That message is NOT a person taking a turn, and
eight landmarks used to assume otherwise. The second half of this file is those landmarks.
"""

from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass
from pathlib import Path

from harness.context.compaction import extract_user_messages, pick_boundary
from harness.session.conversations import title_from
from harness.loop import TurnEngine
from harness.session.events import EventType
from harness.safety.permissions import Mode, PermissionEngine
from harness.llm.providers import AssistantTurn, ModelCapabilities, ProviderClient, ToolCall
from harness.tools import ToolRegistry
from harness.tools.files import file_tools
from harness.session.turns import ATTACHED_BY, attachment_message, is_user_turn

# The smallest valid PNG: one transparent pixel.
PIXEL = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk"
    "+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)


@dataclass
class _Meta:
    category: str = ""
    risk_level: str = "low"
    requires_approval: bool = False


class _Scripted(ProviderClient):
    def __init__(self, turns, *, vision: bool = False):
        self._turns = list(turns)
        self._vision = vision

    def complete(self, *, model, messages, tools=None, **settings):
        if self._turns:
            return self._turns.pop(0)
        return AssistantTurn(text="done", finish_reason="stop")

    def capabilities(self, model):
        return ModelCapabilities(vision=self._vision)


def _tool(tmp_path: Path):
    return file_tools(str(tmp_path))[0]


# -- the tool ----------------------------------------------------------------------


def test_it_hands_back_the_bytes_the_model_can_see(tmp_path):
    (tmp_path / "chart.png").write_bytes(PIXEL)
    out = _tool(tmp_path)(path="chart.png")

    assert out["path"] == "chart.png"
    part = out["_attach"][0]
    assert part["type"] == "image_url"
    assert part["image_url"]["url"].startswith("data:image/png;base64,")
    # Round-trips to the same file, so the model is looking at THIS picture.
    encoded = part["image_url"]["url"].split(",", 1)[1]
    assert base64.b64decode(encoded) == PIXEL


def test_a_binary_file_that_is_not_a_picture_is_not_shown(tmp_path):
    (tmp_path / "blob.bin").write_bytes(b"\x00\x01\x02" * 50)
    out = _tool(tmp_path)(path="blob.bin")
    assert "binary file" in out["error"] and "_attach" not in out


def test_an_image_outside_the_roots_is_refused(tmp_path):
    outside = tmp_path.parent / "elsewhere.png"
    outside.write_bytes(PIXEL)
    assert "outside the session's folders" in _tool(tmp_path)(path=str(outside))["error"]


def test_an_oversized_image_says_what_to_do_instead(tmp_path):
    from harness.tools import files as files_mod
    from harness.tools.files import images as _images  # noqa: F401

    big = tmp_path / "scan.png"
    big.write_bytes(b"x" * (files_mod.images.MAX_IMAGE_BYTES + 1))
    error = _tool(tmp_path)(path="scan.png")["error"]
    assert "resize or crop" in error


# -- the engine puts it where the model reads --------------------------------------


def _engine_that_looks(tmp_path, *, vision: bool = False):
    registry = ToolRegistry()
    registry.register(_tool(tmp_path), metadata=_Meta())
    return TurnEngine(
        provider=_Scripted(
            [
                AssistantTurn(
                    tool_calls=[ToolCall(id="c1", name="read_file", arguments={"path": "chart.png"})],
                    finish_reason="tool_calls",
                ),
                AssistantTurn(text="It is a single pixel.", finish_reason="stop"),
            ],
            vision=vision,
        ),
        registry=registry,
        permissions=PermissionEngine(workspace_root=tmp_path, mode=Mode.DANGER_FULL_ACCESS),
        model="m",
    )


def test_the_image_reaches_the_model_and_the_result_stays_a_string(tmp_path):
    (tmp_path / "chart.png").write_bytes(PIXEL)
    engine = _engine_that_looks(tmp_path)

    async def go():
        return [e async for e in engine.run("look at chart.png")]

    events = asyncio.run(go())
    assert [e for e in events if e.type == EventType.TOOL_FINISHED]

    result = [m for m in engine.messages if m.get("role") == "tool"][0]
    assert isinstance(result["content"], str)  # providers require this
    assert "_attach" not in result["content"]  # lifted off, not leaked into the text

    attached = [m for m in engine.messages if ATTACHED_BY in m]
    assert len(attached) == 1
    assert attached[0]["content"][0]["image_url"]["url"].startswith("data:image/png;base64,")
    assert attached[0][ATTACHED_BY] == "c1"


def _outbound_after_looking(tmp_path, *, vision: bool):
    (tmp_path / "chart.png").write_bytes(PIXEL)
    engine = _engine_that_looks(tmp_path, vision=vision)

    async def go():
        async for _ in engine.run("look at chart.png"):
            pass

    asyncio.run(go())
    return engine, engine._outbound_messages()


def _urls(messages):
    return [
        part["image_url"]["url"]
        for m in messages
        if isinstance(m.get("content"), list)
        for part in m["content"]
        if isinstance(part, dict) and part.get("type") == "image_url"
    ]


def test_a_vision_model_is_sent_the_actual_bytes(tmp_path):
    """Asserted on the DATA URL, not on "the content is a list". The placeholder below is
    also a list, so the weaker check passes whether or not the image ever arrives — this
    test was written that way first and passed for the wrong reason."""
    _engine, outbound = _outbound_after_looking(tmp_path, vision=True)
    urls = _urls(outbound)
    assert len(urls) == 1 and urls[0].startswith("data:image/png;base64,")
    assert base64.b64decode(urls[0].split(",", 1)[1]) == PIXEL


def test_a_model_without_vision_gets_the_placeholder_and_not_the_payload(tmp_path):
    """And the base64 must NOT be sent: it would be rejected, and charged for on the way."""
    _engine, outbound = _outbound_after_looking(tmp_path, vision=False)
    assert _urls(outbound) == []
    text = str(outbound)
    assert "not viewable by this model" in text
    assert "base64," not in text


def test_the_tag_never_reaches_the_provider(tmp_path):
    """Providers reject unknown keys — the same reason every other sidecar is stripped."""
    _engine, outbound = _outbound_after_looking(tmp_path, vision=True)
    assert not any(ATTACHED_BY in m for m in outbound)


# -- the landmarks, which is the part that would have broken silently --------------


def test_an_attached_image_is_not_a_person_taking_a_turn():
    real = {"role": "user", "content": "convert these"}
    attached = attachment_message("c1", [{"type": "image_url", "image_url": {"url": "data:x"}}])
    assert is_user_turn(real) is True
    assert is_user_turn(attached) is False
    assert is_user_turn({"role": "assistant", "content": "hi"}) is False


def test_durable_resume_still_finds_the_call_behind_an_attachment(tmp_path):
    """The sharpest one. `_unanswered_trailing_tool_calls` walks back and STOPS at a user
    message. An untagged attachment would end the walk early, so resume would decide
    there was nothing to finish — item 2.2 broken, silently, with no test failing."""
    engine = _engine_that_looks(tmp_path)
    engine.messages = [
        {"role": "user", "content": "look and then send it"},
        {
            "role": "assistant",
            "tool_calls": [
                {"id": "c1", "function": {"name": "read_image", "arguments": '{"path": "a.png"}'}},
                {"id": "c2", "function": {"name": "send_message", "arguments": "{}"}},
            ],
        },
        {"role": "tool", "tool_call_id": "c1", "content": "attached below"},
        attachment_message("c1", [{"type": "image_url", "image_url": {"url": "data:x"}}]),
    ]
    pending = engine._unanswered_trailing_tool_calls()
    assert [p.id for p in pending] == ["c2"]


def test_a_session_is_not_titled_after_a_picture():
    messages = [
        attachment_message("c1", [{"type": "image_url", "image_url": {"url": "data:x"}}]),
        {"role": "user", "content": "tidy up the invoices"},
    ]
    assert title_from(messages) == "tidy up the invoices"


def test_compaction_keeps_the_users_words_and_not_the_attachment():
    span = [
        {"role": "user", "content": "we settled on 240 per seat"},
        attachment_message("c1", [{"type": "image_url", "image_url": {"url": "data:x"}}]),
    ]
    kept = extract_user_messages(span)
    assert kept == ["we settled on 240 per seat"]


def test_compaction_never_cuts_at_an_attachment():
    """`pick_boundary` chooses among user AND assistant positions — it is not required to
    land on a user turn. What it must never do is cut AT an attachment, which would put
    an image on the far side of the boundary from the tool call that fetched it."""
    messages = [{"role": "system", "content": "s"}]
    for i in range(6):
        # Every user turn is followed by a picture, so an unfiltered candidate list would
        # have plenty of attachments to choose from.
        messages.append({"role": "user", "content": f"request {i} " + "word " * 400})
        messages.append(attachment_message(f"c{i}", [{"type": "image_url", "image_url": {"url": "data:x"}}]))
        messages.append({"role": "assistant", "content": "ok " + "word " * 400})

    for keep in (100, 200, 400, 800):
        boundary = pick_boundary(messages, keep_tokens=keep)
        if boundary is None:
            continue
        assert ATTACHED_BY not in messages[boundary], f"cut at an attachment (keep={keep})"
