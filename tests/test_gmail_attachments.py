"""Gmail attachments — the pair Google's own MCP catalogue does not offer.

`gmail_get_message` already returns each part's filename, mime type, size and
`attachmentId`; it never returns the bytes, which Gmail serves from a separate endpoint.
So before these, an agent could see that a PDF was attached and had no way to read it.

Three of these guard properties that are invisible until they bite:

  - the MIME walk, because Gmail nests parts arbitrarily deep and an attachment sits at no
    fixed level — a shallow reader finds nothing and reports "no attachments";
  - base64URL padding, because Gmail strips it and the standard decoder then throws on
    exactly the attachments whose length is not a multiple of four;
  - the filename, because it is chosen by whoever SENT the mail. `../../../evil.pdf` must
    land in the session folder, not three directories above it.
"""

from __future__ import annotations

import base64
import types
from pathlib import Path

import httpx
import pytest

from harness.integrations.connectors.integration_tools import make_integration_tools
from harness.integrations.connectors.mail.mime import safe_filename as _gmail_safe_filename
from harness.safety.secrets import SecretStore


def _root(tmp_path, writable=True):
    folder = tmp_path / "session"
    folder.mkdir(exist_ok=True)
    return types.SimpleNamespace(path=folder, writable=writable)


def _connected(tmp_path):
    """A gmail account the tools will resolve, with a token that never expires."""
    from harness.integrations.connectors import gmail_accounts

    secrets = SecretStore(tmp_path / "s.json")
    gmail_accounts.managed_connect_account(
        secrets, {"account": "me@example.com", "access_token": "at"}
    )
    return secrets


def _tools(secrets, tmp_path, handler, monkeypatch, writable=True):
    real = httpx.Client

    def factory(*a, **kw):
        kw["transport"] = httpx.MockTransport(handler)
        return real(*a, **kw)

    monkeypatch.setattr(httpx, "Client", factory)
    built = make_integration_tools(secrets, roots=[_root(tmp_path, writable)])
    return {t.__name__: t for t in built}


# ------------------------------------------------------------------ the MIME walk

#: multipart/mixed wrapping multipart/alternative — the ordinary shape of a mail with an
#: attachment, and the reason a one-level scan finds nothing.
NESTED = {
    "mimeType": "multipart/mixed",
    "parts": [
        {
            "mimeType": "multipart/alternative",
            "filename": "",
            "parts": [
                {"mimeType": "text/plain", "filename": "", "body": {"size": 10}},
                {"mimeType": "text/html", "filename": "", "body": {"size": 20}},
            ],
        },
        {
            "mimeType": "application/pdf",
            "filename": "report.pdf",
            "body": {"size": 70691, "attachmentId": "att-1"},
        },
        {
            # An inline image: it has a filename but its bytes are already here, and there
            # is no id to fetch. Listing it would hand the agent an id it cannot use.
            "mimeType": "image/png",
            "filename": "logo.png",
            "body": {"size": 120, "data": "aGk="},
        },
    ],
}


def test_the_walk_finds_a_deeply_nested_attachment_and_skips_inline_parts(
    tmp_path, monkeypatch
):
    secrets = _connected(tmp_path)

    def handler(request):
        return httpx.Response(200, json={"id": "m1", "payload": NESTED})

    tools = _tools(secrets, tmp_path, handler, monkeypatch)
    result = tools["gmail_list_attachments"](message_id="m1")

    assert result["ok"] is True
    assert result["count"] == 1, result["attachments"]
    only = result["attachments"][0]
    assert only == {
        "filename": "report.pdf",
        "mime_type": "application/pdf",
        "size": 70691,
        "attachment_id": "att-1",
    }


def test_a_message_with_no_attachments_says_so_rather_than_failing(tmp_path, monkeypatch):
    secrets = _connected(tmp_path)
    payload = {"mimeType": "text/plain", "filename": "", "body": {"size": 5}}

    def handler(request):
        return httpx.Response(200, json={"id": "m1", "payload": payload})

    tools = _tools(secrets, tmp_path, handler, monkeypatch)
    result = tools["gmail_list_attachments"](message_id="m1")
    assert result["ok"] is True and result["count"] == 0


# ------------------------------------------------------------------ downloading

#: 5 bytes, so base64 leaves two '=' of padding — which Gmail strips.
_BYTES = b"%PDF-"
_UNPADDED = base64.urlsafe_b64encode(_BYTES).decode().rstrip("=")


def test_base64url_without_padding_is_decoded(tmp_path, monkeypatch):
    """Gmail strips the padding. `urlsafe_b64decode` throws on that unless it is put
    back, and it throws only for lengths that are not a multiple of four — so this fails
    on *some* attachments and not others, which is the worst way to find out."""
    assert not _UNPADDED.endswith("=")
    secrets = _connected(tmp_path)

    def handler(request):
        return httpx.Response(200, json={"size": len(_BYTES), "data": _UNPADDED})

    tools = _tools(secrets, tmp_path, handler, monkeypatch)
    result = tools["gmail_download_attachment"](
        message_id="m1", attachment_id="att-1", filename="report.pdf"
    )
    assert result["ok"] is True, result
    saved = Path(result["path"])
    assert saved.read_bytes() == _BYTES
    assert result["size"] == len(_BYTES)


def test_a_second_download_does_not_overwrite_the_first(tmp_path, monkeypatch):
    secrets = _connected(tmp_path)

    def handler(request):
        return httpx.Response(200, json={"size": len(_BYTES), "data": _UNPADDED})

    tools = _tools(secrets, tmp_path, handler, monkeypatch)
    first = tools["gmail_download_attachment"](
        message_id="m1", attachment_id="a", filename="report.pdf"
    )
    second = tools["gmail_download_attachment"](
        message_id="m1", attachment_id="a", filename="report.pdf"
    )
    assert first["path"] != second["path"]
    assert Path(first["path"]).exists() and Path(second["path"]).exists()
    assert Path(second["path"]).name == "report-1.pdf"


@pytest.mark.parametrize(
    "sent",
    [
        "../../../evil.pdf",
        r"..\..\..\evil.pdf",
        "/etc/passwd",
        r"C:\Windows\System32\evil.dll",
    ],
)
def test_a_sender_chosen_filename_cannot_escape_the_session_folder(
    tmp_path, monkeypatch, sent
):
    """The filename comes from whoever sent the mail — it is attacker-controlled input
    that reaches a filesystem write."""
    secrets = _connected(tmp_path)

    def handler(request):
        return httpx.Response(200, json={"size": len(_BYTES), "data": _UNPADDED})

    root = _root(tmp_path)
    real = httpx.Client
    monkeypatch.setattr(
        httpx,
        "Client",
        lambda *a, **kw: real(*a, **{**kw, "transport": httpx.MockTransport(handler)}),
    )
    tools = {
        t.__name__: t for t in make_integration_tools(secrets, roots=[root])
    }
    result = tools["gmail_download_attachment"](
        message_id="m1", attachment_id="a", filename=sent
    )
    assert result["ok"] is True, result
    written = Path(result["path"]).resolve()
    assert written.parent == root.path.resolve(), f"escaped to {written}"


def test_a_nameless_attachment_still_gets_a_file(tmp_path, monkeypatch):
    secrets = _connected(tmp_path)

    def handler(request):
        return httpx.Response(200, json={"size": len(_BYTES), "data": _UNPADDED})

    tools = _tools(secrets, tmp_path, handler, monkeypatch)
    result = tools["gmail_download_attachment"](message_id="m1", attachment_id="abcdef123456")
    assert result["ok"] is True
    assert Path(result["path"]).name.startswith("attachment-")


def test_no_writable_session_folder_is_refused_before_the_download(tmp_path, monkeypatch):
    secrets = _connected(tmp_path)
    called = {"n": 0}

    def handler(request):  # pragma: no cover - reaching this IS the failure
        called["n"] += 1
        return httpx.Response(200, json={"data": _UNPADDED})

    tools = _tools(secrets, tmp_path, handler, monkeypatch, writable=False)
    result = tools["gmail_download_attachment"](message_id="m1", attachment_id="a")
    assert "error" in result and "writable" in result["error"]
    assert called["n"] == 0, "fetched the bytes with nowhere to put them"


# ------------------------------------------------------------------ the helper itself


@pytest.mark.parametrize(
    "given,expected",
    [
        ("report.pdf", "report.pdf"),
        ("../../evil.sh", "evil.sh"),
        (r"a\b\c.txt", "c.txt"),
        ('bad<>:"|?*.txt', "bad_______.txt"),
        ("...", "attachment"),
        ("", "attachment"),
    ],
)
def test_safe_filename_rules(given, expected):
    assert _gmail_safe_filename(given) == expected
