"""What a person attaches to a message (`llm/attachments`): how each kind becomes part of the user
message, what is left out, what is spilled to a file instead of sent whole, and that an image reaches
the provider exactly as it was attached and survives a save.

One test calls a real vision model and runs only when asked to (`REEVE_LIVE_VISION=1`, the key read
from the SecretStore); everything else uses no network.
"""

from __future__ import annotations

import base64
import os
import struct
import zlib

import pytest

from harness.llm.attachments import MAX_PDF_CHARS, build_user_content, content_to_text

PDF = "data:application/pdf;base64,JVBERi0xLjQ="


def _png(rgb: tuple[int, int, int], side: int = 32) -> bytes:
    """A solid square in one colour, written with the standard library."""

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    scanlines = (b"\x00" + bytes(rgb) * side) * side  # filter byte 0, then the row
    header = struct.pack(">IIBBBBB", side, side, 8, 2, 0, 0, 0)  # 8-bit RGB
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(scanlines, 9)) + chunk(b"IEND", b"")


def _data_url(mime: str, raw: bytes) -> str:
    return f"data:{mime};base64,{base64.b64encode(raw).decode()}"


def _image(rgb=(220, 30, 30)) -> dict:
    return {"kind": "image", "data_url": _data_url("image/png", _png(rgb))}


def _text(name: str, body: str) -> dict:
    return {"kind": "text", "name": name, "text": body}


class _Sink:
    """A spill target that keeps what it was given, or fails when told to."""

    def __init__(self, path="/w/.reeve/output/attachment-1.log", fails=False):
        self.path, self.fails, self.kept = path, fails, []

    def __call__(self, text):
        if self.fails:
            raise OSError("disk full")
        self.kept.append(text)
        return self.path


# -- each kind -----------------------------------------------------------------------------------


def test_with_nothing_attached_the_message_stays_a_string():
    assert build_user_content("hello", []) == "hello" and build_user_content("hello", None) == "hello"


def test_an_image_is_a_part_after_the_text():
    image = _image()
    assert build_user_content("what is this?", [image]) == [{"type": "text", "text": "what is this?"}, {"type": "image_url", "image_url": {"url": image["data_url"]}}]


def test_a_text_file_is_written_into_the_message_under_its_name():
    [part] = build_user_content("", [_text("notes.md", "# Title\nbody")])
    assert part["type"] == "text" and "notes.md" in part["text"] and "# Title" in part["text"]


def test_a_pdf_is_a_file_part():
    assert build_user_content("summarize this", [{"kind": "pdf", "name": "report.pdf", "data_url": PDF}]) == [
        {"type": "text", "text": "summarize this"},
        {"type": "file", "file": {"filename": "report.pdf", "file_data": PDF}},
    ]


@pytest.mark.parametrize(
    "unusable",
    [
        {"kind": "image", "data_url": "https://example.com/x.png"},  # not a data: URL
        {"kind": "image", "data_url": "data:image/png;base64," + "A" * 12_000_001},  # too big
        {"kind": "text", "text": ""},
        {"kind": "pdf", "name": "x.pdf", "data_url": "data:image/png;base64,zz"},  # not a pdf
        {"kind": "pdf", "name": "x.pdf"},
        {"kind": "pdf", "name": "big.pdf", "data_url": "data:application/pdf;base64," + "A" * MAX_PDF_CHARS},
    ],
)
def test_an_attachment_that_cannot_be_used_is_left_out(unusable):
    assert build_user_content("hi", [unusable]) == "hi"  # only the text is left, as a plain string


def test_in_flat_text_an_image_and_a_pdf_are_named_by_kind():
    assert content_to_text(build_user_content("look at this", [_image((0, 0, 0))])) == "look at this [image]"
    assert content_to_text("plain") == "plain"
    parts = [{"type": "text", "text": "see attached"}, {"type": "file", "file": {"filename": "report.pdf", "file_data": "data:application/pdf;base64,x"}}]
    assert content_to_text(parts) == "see attached [pdf]"
    assert content_to_text(parts, image_placeholder="") == "see attached"


# -- a large text file ---------------------------------------------------------------------------
#
# Inlined whole, a 200k-character file costs ~50k tokens on every later request until compaction.
# Over the inline budget the body goes to a file and the message carries its head and the path,
# the way a large tool result does.


def test_a_large_text_file_is_sent_as_its_head_and_where_the_rest_is():
    body = "\n".join(f"line {n} " + "x" * 40 for n in range(1, 1501))  # ~64k characters
    sink = _Sink()
    text = build_user_content("", [_text("big.md", body)], spill=sink)[0]["text"]
    assert text.startswith("[Attached file: big.md]") and body[:2000] in text and body[-200:] not in text
    assert sink.path in text and "read_file" in text  # where the rest is, and how to read it
    assert sink.kept == [body] and len(text) < 6_000


def test_with_no_sink_or_a_failing_one_a_large_file_is_inlined_whole():
    # An attachment never costs the turn that carried it.
    for body, spill in (("x" * 50_000, None), ("y" * 50_000, _Sink(fails=True))):
        assert body in build_user_content("", [_text("big.txt", body)], spill=spill)[0]["text"]


def test_a_small_text_file_is_never_spilled():
    sink = _Sink("/tmp/x")
    assert build_user_content("", [_text("note.md", "short and complete")], spill=sink)[0]["text"].endswith("short and complete")
    assert sink.kept == []


# -- any other file ------------------------------------------------------------------------------


def test_a_binary_file_is_saved_and_the_message_names_where(tmp_path):
    raw = b"\x89PNG\r\n\x1a\n" + b"\x00\xff\x01"  # not UTF-8
    saved = {}

    def save_file(name, data):
        saved.update(name=name, data=data)
        return str(tmp_path / name)

    attachment = {"kind": "file", "name": "archive.bin", "mime": "application/octet-stream", "data_url": _data_url("application/octet-stream", raw)}
    last = build_user_content("look", [attachment], save_file=save_file)[-1]
    assert saved == {"name": "archive.bin", "data": raw}
    assert last["type"] == "text" and "archive.bin" in last["text"] and str(tmp_path / "archive.bin") in last["text"]


def test_a_file_that_reads_as_text_is_sent_as_text():
    last = build_user_content("look", [{"kind": "file", "name": "rows.csv", "data_url": _data_url("text/csv", b"col_a,col_b\n1,2\n")}])[-1]
    assert last["type"] == "text" and "col_a" in last["text"]


def test_a_binary_file_with_nowhere_to_go_is_still_mentioned():
    attachment = {"kind": "file", "name": "x.bin", "data_url": _data_url("application/octet-stream", b"\x01\x02\x00\x03\xff\xfa")}
    assert "could not be saved" in build_user_content("look", [attachment])[-1]["text"]


# -- through a turn and a save -------------------------------------------------------------------


async def test_an_image_reaches_the_provider_exactly_as_attached():
    from harness.compose import build_engine
    from harness.llm.providers import AssistantTurn, ModelCapabilities, ProviderClient
    from harness.modes.agents.chat import chat_agent

    class Sees(ProviderClient):
        sent: list[dict] = []

        def complete(self, *, model, messages, tools=None, **settings):
            Sees.sent = [dict(m) for m in messages]
            return AssistantTurn(text="ok", finish_reason="stop")

        def capabilities(self, model):
            return ModelCapabilities(vision=True)

    image = _image()
    async for _ in build_engine(agent=chat_agent(), model="gpt-4o", provider=Sees()).run(build_user_content("describe the image", [image])):
        pass
    users = [m for m in Sees.sent if m.get("role") == "user"]
    assert users, "no user message reached the provider"
    parts = users[-1]["content"]
    assert isinstance(parts, list)
    images = [p for p in parts if p.get("type") == "image_url"]
    assert images and images[0]["image_url"]["url"] == image["data_url"]  # byte for byte


def test_a_message_with_parts_is_saved_whole_and_titled_from_its_text(tmp_path):
    from harness.session.conversations import ConversationStore, title_from
    from harness.session.sessions import SessionRecord

    image = _image((0, 128, 0))
    messages = [{"role": "user", "content": build_user_content("review this diagram", [image])}]
    store = ConversationStore(tmp_path)
    store.save(SessionRecord(session_id="s1", workspace=str(tmp_path), model="gpt-4o", mode="interactive", messages=messages, agent="chat"))
    assert store.load("s1").messages[0]["content"][1]["image_url"]["url"] == image["data_url"]
    assert title_from(messages) == "review this diagram"


@pytest.mark.skipif(os.environ.get("REEVE_LIVE_VISION") != "1", reason="calls a real OpenAI vision model; set REEVE_LIVE_VISION=1")
def test_a_real_vision_model_reads_the_colour_of_the_image():
    from harness.llm.providers import OpenAIProvider
    from harness.safety.secrets import SecretStore

    provider = OpenAIProvider(default_model="gpt-4o", secrets=SecretStore())
    question = build_user_content("What is the single dominant color of this image? Reply with one word.", [_image()])
    turn = provider.complete(model="gpt-4o", messages=[{"role": "user", "content": question}])
    assert "red" in (turn.text or "").lower(), f"model said: {turn.text!r}"
