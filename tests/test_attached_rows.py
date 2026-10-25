"""A message keeps what was attached, as the transcript draws it.

The rows ride beside the content in `_attached` and on TURN_START: kind, name, size, lines for text,
and the path of the copy kept in the session's attachments folder. The provider never sees them,
and the copy the model is told about for a binary file is the same file the row names.
"""

from __future__ import annotations

import base64
from pathlib import Path

from fastapi.testclient import TestClient

from harness.interface.server import SessionManager, create_app
from harness.interface.server.app.attached import KeptFiles, attached_rows
from harness.llm.providers import AssistantTurn, ModelCapabilities, ProviderClient
from harness.llm.wire import wire_message


class ScriptedProvider(ProviderClient):
    def __init__(self, turns=None):
        self._turns = list(turns or [])
        self.seen: list[list[dict]] = []

    def complete(self, *, model, messages, tools=None, **settings):
        self.seen.append(messages)
        return self._turns.pop(0)

    def capabilities(self, model):
        return ModelCapabilities()


def _url(mime: str, data: bytes) -> str:
    return f"data:{mime};base64," + base64.b64encode(data).decode()


PDF = b"%PDF-1.4 invented test bytes"
BINARY = bytes(range(256)) * 4  # not UTF-8: saved and named, never inlined


def test_rows_say_kind_name_size_and_lines_and_keep_a_copy_of_each_file(tmp_path):
    saved: list[tuple[str, bytes]] = []

    def save(name, data):
        saved.append((name, data))
        target = tmp_path / name
        target.write_bytes(data)
        return str(target)

    rows = attached_rows(
        [
            {"kind": "image", "name": "shot.png", "data_url": _url("image/png", b"\x89PNG fake")},
            {"kind": "pdf", "name": "Q3-report.pdf", "mime": "application/pdf", "data_url": _url("application/pdf", PDF)},
            {"kind": "text", "name": "sales.csv", "text": "month,total\njan,3\nfeb,4\n"},
        ],
        KeptFiles(save),
    )
    assert [r["name"] for r in rows] == ["shot.png", "Q3-report.pdf", "sales.csv"]
    assert rows[0] == {"kind": "image", "name": "shot.png", "size": len(b"\x89PNG fake")}  # a picture is drawn from content
    assert rows[1]["size"] == len(PDF) and rows[1]["mime"] == "application/pdf"
    assert Path(rows[1]["path"]).read_bytes() == PDF
    assert rows[2]["lines"] == 3 and rows[2]["size"] == len("month,total\njan,3\nfeb,4\n")
    assert [name for name, _ in saved] == ["Q3-report.pdf", "sales.csv"]


def test_a_file_that_cannot_be_saved_keeps_its_row_without_a_path():
    rows = attached_rows([{"kind": "pdf", "name": "a.pdf", "data_url": _url("application/pdf", PDF)}], KeptFiles(lambda n, d: None))
    assert rows == [{"kind": "pdf", "name": "a.pdf", "size": len(PDF)}]
    assert attached_rows([{"kind": "file", "name": "x.bin", "data_url": "not a data url"}], None) == [
        {"kind": "file", "name": "x.bin"}
    ]


def test_a_message_carries_its_rows_and_the_provider_never_sees_them(tmp_path):
    provider = ScriptedProvider([AssistantTurn(text="done")])
    manager = SessionManager(workspace=tmp_path, provider=provider)
    client = TestClient(create_app(manager))
    with client.websocket_connect("/ws/session/s1?agent=chat") as ws:
        assert ws.receive_json()["type"] == "ready"
        ws.send_json(
            {
                "type": "user_message",
                "text": "what is in these",
                "attachments": [
                    {"kind": "pdf", "name": "Q3-report.pdf", "data_url": _url("application/pdf", PDF)},
                    {"kind": "file", "name": "blob.bin", "mime": "application/octet-stream", "data_url": _url("application/octet-stream", BINARY)},
                ],
            }
        )
        events = []
        while True:
            evt = ws.receive_json()
            events.append(evt)
            if evt["type"] == "turn_done":
                break
    start = next(e for e in events if e["type"] == "turn_start")
    rows = start["data"]["attached"]
    assert [(r["kind"], r["name"], r["size"]) for r in rows] == [("pdf", "Q3-report.pdf", len(PDF)), ("file", "blob.bin", len(BINARY))]
    assert Path(rows[0]["path"]).read_bytes() == PDF
    # The binary was saved once: the line the model reads names the same path as the row.
    blob = Path(rows[1]["path"])
    assert blob.read_bytes() == BINARY and len(list(blob.parent.iterdir())) == 2
    stored = client.get("/v1/sessions/s1/messages").json()["messages"]
    user = next(m for m in stored if m["role"] == "user")
    assert user["_attached"] == rows
    # The line the model reads for the binary names the same copy.
    said = " ".join(part.get("text", "") for part in user["content"] if part.get("type") == "text")
    assert f"saved at {blob}" in said
    # The rows are the transcript's: the request a provider is sent carries none of them.
    assert "_attached" not in wire_message(user)
