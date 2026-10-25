"""Gmail reply, forward, and the drafts that get sent.

`gmail_send_email` starts a new conversation, so "answer that email" used to be a fresh message
the recipient's client could not file under the original. What has to hold, and is invisible
until it does not:

  - a reply goes to the person the original came from without the model naming anyone, and
    carries the thread and the `In-Reply-To` / `References` headers that make it a reply;
  - "reply all" never mails the mailbox owner, and never repeats an address;
  - a message hidden by "Never show agents" cannot be answered (same 404 as a missing one);
  - a header value cannot smuggle in a line break;
  - a send reports who it went to, because the approval card cannot show a worked-out recipient.
"""

from __future__ import annotations

import base64
import email
import json

import httpx

from harness.integrations.connectors import gmail_accounts
from harness.integrations.connectors.integration_tools import make_integration_tools
from harness.safety.secrets import SecretStore


def _b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")


def _original(**headers) -> dict:
    base = {
        "From": "Ann <ann@acme.test>",
        "To": "me@example.com, Bob <bob@acme.test>",
        "Cc": "Cy <cy@acme.test>",
        "Subject": "Quarterly numbers",
        "Message-ID": "<orig-1@acme.test>",
        "References": "<root@acme.test>",
        "Date": "Mon, 1 Jan 2026 09:00:00 +0000",
    }
    base.update(headers)
    return {
        "id": "m1",
        "threadId": "t1",
        "labelIds": ["INBOX"],
        "payload": {
            "mimeType": "text/plain",
            "headers": [{"name": k, "value": v} for k, v in base.items()],
            "body": {"data": _b64("Numbers are attached.")},
        },
    }


class _Gmail:
    """A Gmail that serves one message and records every write."""

    def __init__(self, message):
        self.message, self.writes = message, []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "GET" and path.endswith("/messages/m1"):
            return httpx.Response(200, json=self.message)
        if request.method == "GET" and path.endswith("/labels"):
            return httpx.Response(200, json={"labels": [{"id": "Label_1", "name": "Private"}]})
        if request.method == "GET" and "/drafts/" in path:
            return httpx.Response(200, json={"id": "d1", "message": {"id": "dm", "threadId": "t9"}})
        body = json.loads(request.content) if request.content else None
        self.writes.append((request.method, path.split("/users/me/")[1], body))
        return httpx.Response(200, json={"id": "sent-1", "threadId": "t1"})


def _tools(tmp_path, monkeypatch, message=None):
    secrets = SecretStore(tmp_path / "s.json")
    gmail_accounts.managed_connect_account(secrets, {"account": "me@example.com", "access_token": "at"})
    server = _Gmail(message or _original())
    real = httpx.Client

    def factory(*a, **kw):
        kw["transport"] = httpx.MockTransport(server)
        return real(*a, **kw)

    monkeypatch.setattr(httpx, "Client", factory)
    return secrets, {t.__name__: t for t in make_integration_tools(secrets)}, server


def _sent(server):
    (method, path, body), = [w for w in server.writes if w[1] in ("messages/send", "drafts")]
    message = body if path == "messages/send" else body["message"]
    raw = message["raw"]
    parsed = email.message_from_bytes(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))
    return parsed, message


def test_a_reply_goes_to_the_sender_in_the_same_thread(tmp_path, monkeypatch):
    _s, tools, server = _tools(tmp_path, monkeypatch)
    out = tools["gmail_reply"](message_id="m1", body="Thanks, got it.")

    parsed, message = _sent(server)
    assert parsed["To"] == "Ann <ann@acme.test>"
    assert parsed["Cc"] is None
    assert parsed["Subject"] == "Re: Quarterly numbers"
    assert parsed["In-Reply-To"] == "<orig-1@acme.test>"
    assert parsed["References"] == "<root@acme.test> <orig-1@acme.test>"
    assert message["threadId"] == "t1"
    assert out["sent_to"] == ["Ann <ann@acme.test>"]


def test_a_reply_prefers_reply_to_and_does_not_double_the_re(tmp_path, monkeypatch):
    original = _original(**{"Reply-To": "list@acme.test", "Subject": "RE: Quarterly numbers"})
    _s, tools, server = _tools(tmp_path, monkeypatch, original)
    tools["gmail_reply"](message_id="m1", body="ok")
    parsed, _ = _sent(server)
    assert parsed["To"] == "list@acme.test"
    assert parsed["Subject"] == "RE: Quarterly numbers"


def test_reply_all_includes_everyone_but_me_once(tmp_path, monkeypatch):
    _s, tools, server = _tools(tmp_path, monkeypatch)
    out = tools["gmail_reply"](message_id="m1", body="ok", reply_all=True, cc="cy@acme.test, dee@acme.test")
    parsed, _ = _sent(server)
    assert parsed["To"] == "Ann <ann@acme.test>, Bob <bob@acme.test>"
    assert parsed["Cc"] == "Cy <cy@acme.test>, dee@acme.test"
    assert "me@example.com" not in (parsed["To"] + parsed["Cc"])
    assert out["sent_cc"] == ["Cy <cy@acme.test>", "dee@acme.test"]


def test_answering_my_own_sent_mail_goes_to_who_i_wrote_to(tmp_path, monkeypatch):
    original = _original(From="me@example.com", To="Ann <ann@acme.test>")
    _s, tools, server = _tools(tmp_path, monkeypatch, original)
    tools["gmail_reply"](message_id="m1", body="following up")
    parsed, _ = _sent(server)
    assert parsed["To"] == "Ann <ann@acme.test>"


def test_a_message_hidden_from_agents_cannot_be_answered(tmp_path, monkeypatch):
    secrets, tools, server = _tools(tmp_path, monkeypatch)
    gmail_accounts.set_filters(secrets, senders=["@acme.test"])
    out = tools["gmail_reply"](message_id="m1", body="hi")
    assert out["error"] == "HTTP 404"
    assert not [w for w in server.writes if w[1] in ("messages/send", "drafts")]


def test_a_reply_draft_is_a_draft_in_the_thread_and_sends_nothing(tmp_path, monkeypatch):
    _s, tools, server = _tools(tmp_path, monkeypatch)
    tools["gmail_create_reply_draft"](message_id="m1", body="draft")
    parsed, message = _sent(server)
    assert [w[1] for w in server.writes] == ["drafts"]
    assert message["threadId"] == "t1" and parsed["In-Reply-To"] == "<orig-1@acme.test>"


def test_a_line_break_in_a_recipient_is_refused(tmp_path, monkeypatch):
    _s, tools, server = _tools(tmp_path, monkeypatch)
    out = tools["gmail_forward"](message_id="m1", to="x@y.test\nBcc: evil@z.test")
    assert "error" in out
    assert not server.writes


def test_forward_quotes_the_original_in_a_new_thread(tmp_path, monkeypatch):
    _s, tools, server = _tools(tmp_path, monkeypatch)
    out = tools["gmail_forward"](message_id="m1", to="Eve <eve@acme.test>", note="FYI")
    parsed, message = _sent(server)
    text = parsed.get_payload(decode=True).decode()
    assert parsed["Subject"] == "Fwd: Quarterly numbers"
    assert text.startswith("FYI") and "Forwarded message" in text and "Numbers are attached." in text
    assert "From: Ann <ann@acme.test>" in text
    assert "threadId" not in message and parsed["In-Reply-To"] is None
    assert out["sent_to"] == ["Eve <eve@acme.test>"]


def test_a_reply_needs_someone_to_reply_to(tmp_path, monkeypatch):
    original = _original(From="", **{"Reply-To": ""})
    _s, tools, server = _tools(tmp_path, monkeypatch, original)
    assert "no recipient" in tools["gmail_reply"](message_id="m1", body="hi")["error"]


def test_drafts_send_update_delete(tmp_path, monkeypatch):
    _s, tools, server = _tools(tmp_path, monkeypatch)
    tools["gmail_send_draft"](draft_id="d1")
    tools["gmail_update_draft"](draft_id="d1", to="a@b.test", subject="S", body="B", bcc="hid@b.test")
    tools["gmail_delete_draft"](draft_id="d1")
    assert server.writes[0] == ("POST", "drafts/send", {"id": "d1"})
    method, path, body = server.writes[1]
    assert (method, path) == ("PUT", "drafts/d1")
    assert body["message"]["threadId"] == "t9"  # a reply draft stays in its conversation
    raw = body["message"]["raw"]
    assert email.message_from_bytes(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))["Bcc"] == "hid@b.test"
    assert server.writes[2][:2] == ("DELETE", "drafts/d1")


def test_labels_and_bulk_modify(tmp_path, monkeypatch):
    _s, tools, server = _tools(tmp_path, monkeypatch)
    tools["gmail_update_label"](label_id="Label_1", name="Clients")
    tools["gmail_batch_modify"](message_ids="a, b", remove_label_ids="INBOX,UNREAD")
    assert server.writes[0] == ("PATCH", "labels/Label_1", {"name": "Clients"})
    assert server.writes[1] == ("POST", "messages/batchModify", {"ids": ["a", "b"], "removeLabelIds": ["INBOX", "UNREAD"]})
    assert "error" in tools["gmail_batch_modify"](message_ids="a")
    assert "error" in tools["gmail_batch_modify"](message_ids="", add_label_ids="X")


def test_send_and_draft_accept_a_bcc(tmp_path, monkeypatch):
    _s, tools, server = _tools(tmp_path, monkeypatch)
    tools["gmail_send_email"](to="a@b.test", subject="S", body="B", bcc="quiet@b.test")
    tools["gmail_create_draft"](to="a@b.test", subject="S", body="B", bcc="quiet@b.test")
    for _m, _p, body in server.writes:
        message = body if "raw" in body else body["message"]
        parsed = email.message_from_bytes(base64.urlsafe_b64decode(message["raw"] + "=" * (-len(message["raw"]) % 4)))
        assert parsed["Bcc"] == "quiet@b.test"


def test_everything_that_sends_or_destroys_asks_first(tmp_path, monkeypatch):
    _s, tools, _server = _tools(tmp_path, monkeypatch)
    for name in ("gmail_reply", "gmail_forward", "gmail_send_draft", "gmail_delete_draft", "gmail_delete_label"):
        assert tools[name].__aisuite_tool_metadata__.requires_approval is True, name
    assert tools["gmail_create_reply_draft"].__aisuite_tool_metadata__.requires_approval is True  # a write asks


def test_every_new_tool_is_in_the_catalog_and_the_catalog_has_no_ghost(tmp_path, monkeypatch):
    from harness.integrations.connectors.catalog import TOOL_DEFS

    _s, tools, _server = _tools(tmp_path, monkeypatch)
    built = {n for n in tools if n.startswith("gmail_")}
    listed = {d.name for d in TOOL_DEFS if d.connector == "gmail"}
    assert built == listed
