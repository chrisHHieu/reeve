"""Gmail: "Never show agents" on every read path, and files on an outgoing message.

The filter used to guard search and one message read only. A thread read returned every message
in the thread, a thread search returned each thread's snippet (the newest message's opening words),
and a draft or an attachment could be fetched by id. What has to hold:

  - what is hidden is dropped from a list without a trace and answers a direct ask with the same
    404 a missing item gets;
  - a thread with SOME hidden messages loses only those, and a snippet that came from a hidden
    message is not shown.

Sending a local file is the one Gmail tool that carries data out of the machine, so:

  - only files inside the session's directories, never Reeve's own state, never in a remote
    session, and never more than Gmail will take;
  - a forward carries the original's attachments unless told not to, and says what it attached.
"""

from __future__ import annotations

import base64
import email
import json
import types

import httpx

from harness.integrations.connectors import gmail_accounts
from harness.integrations.connectors.integration_tools import make_integration_tools
from harness.safety.secrets import SecretStore


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _msg(mid, sender, labels=("INBOX",), extra=None):
    return {
        "id": mid,
        "threadId": "t1",
        "labelIds": list(labels),
        "snippet": f"snippet of {mid}",
        "payload": {
            "mimeType": "multipart/mixed",
            "headers": [
                {"name": "From", "value": sender},
                {"name": "To", "value": "me@example.com"},
                {"name": "Subject", "value": "Contract"},
                {"name": "Message-ID", "value": f"<{mid}@x>"},
            ],
            "parts": [
                {"mimeType": "text/plain", "filename": "", "body": {"data": _b64(b"Body of " + mid.encode())}},
                {"mimeType": "application/pdf", "filename": "deal.pdf", "body": {"attachmentId": "att-1", "size": 4}},
            ],
            **(extra or {}),
        },
    }


HIDDEN, SHOWN = _msg("m-hidden", "Ann <ann@acme.test>"), _msg("m-shown", "Bob <bob@ok.test>")


class _Gmail:
    def __init__(self):
        self.writes, self.reads = [], []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.split("/users/me/")[1]
        self.reads.append(path) if request.method == "GET" else None
        if request.method == "GET":
            if path == "threads":
                return httpx.Response(200, json={"threads": [
                    {"id": "t-mixed", "snippet": "from the hidden one"},
                    {"id": "t-hidden", "snippet": "secret words"},
                    {"id": "t-open", "snippet": "fine"},
                ]})
            if path == "threads/t-mixed":
                return httpx.Response(200, json={"id": "t-mixed", "messages": [SHOWN, HIDDEN]})
            if path == "threads/t-hidden":
                return httpx.Response(200, json={"id": "t-hidden", "messages": [HIDDEN]})
            if path == "threads/t-open":
                return httpx.Response(200, json={"id": "t-open", "messages": [SHOWN]})
            if path.startswith("drafts/"):
                return httpx.Response(200, json={"id": "d", "message": HIDDEN if path.endswith("d-hidden") else SHOWN})
            if path == "messages/m-hidden":
                return httpx.Response(200, json=HIDDEN)
            if path == "messages/m-shown":
                return httpx.Response(200, json=SHOWN)
            if path.startswith("messages/") and "/attachments/" in path:
                return httpx.Response(200, json={"data": _b64(b"%PDF")})
            if path == "labels":
                return httpx.Response(200, json={"labels": []})
        self.writes.append((request.method, path, json.loads(request.content) if request.content else None))
        return httpx.Response(200, json={"id": "sent", "threadId": "t1"})


def _setup(tmp_path, monkeypatch, *, filtered=True, remote=False):
    monkeypatch.setenv("REEVE_STATE_DIR", str(tmp_path / "state"))
    (tmp_path / "state").mkdir(exist_ok=True)
    secrets = SecretStore(tmp_path / "s.json")
    gmail_accounts.managed_connect_account(secrets, {"account": "me@example.com", "access_token": "at"})
    if filtered:
        gmail_accounts.set_filters(secrets, senders=["@acme.test"])
    server = _Gmail()
    real = httpx.Client

    def factory(*a, **kw):
        kw["transport"] = httpx.MockTransport(server)
        return real(*a, **kw)

    monkeypatch.setattr(httpx, "Client", factory)
    work = tmp_path / "work"
    work.mkdir(exist_ok=True)
    roots = [types.SimpleNamespace(path=work, writable=True)]
    tools = {t.__name__: t for t in make_integration_tools(secrets, roots=roots, remote=remote)}
    return tools, server, work


# ------------------------------------------------------------- hidden on every read path


def test_a_thread_loses_only_its_hidden_messages(tmp_path, monkeypatch):
    tools, _s, _w = _setup(tmp_path, monkeypatch)
    out = tools["gmail_get_thread"](thread_id="t-mixed")
    assert [m["id"] for m in out["data"]["messages"]] == ["m-shown"]
    assert out["_display"]["hidden_by_filters"] == 1


def test_a_thread_that_is_all_hidden_is_a_404(tmp_path, monkeypatch):
    tools, _s, _w = _setup(tmp_path, monkeypatch)
    assert tools["gmail_get_thread"](thread_id="t-hidden")["error"] == "HTTP 404"


def test_a_thread_listing_omits_hidden_threads_and_hidden_snippets(tmp_path, monkeypatch):
    tools, _s, _w = _setup(tmp_path, monkeypatch)
    out = tools["gmail_search_threads"](query="contract")
    threads = {t["id"]: t for t in out["data"]["threads"]}
    assert set(threads) == {"t-mixed", "t-open"}          # t-hidden is gone without a trace
    assert "snippet" in threads["t-open"]
    assert "snippet" not in threads["t-mixed"]            # newest message there is the hidden one
    assert "secret words" not in json.dumps(out)
    assert out["_display"]["hidden_by_filters"] == 1


def test_a_thread_that_cannot_be_checked_shows_no_snippet(tmp_path, monkeypatch):
    tools, server, _w = _setup(tmp_path, monkeypatch)
    real = server.__call__

    def flaky(request):
        if request.url.path.endswith("threads/t-open"):
            return httpx.Response(500, json={"error": "boom"})
        return real(request)

    monkeypatch.setattr(type(server), "__call__", lambda self, request: flaky(request))
    out = tools["gmail_search_threads"]()
    open_thread = [t for t in out["data"]["threads"] if t["id"] == "t-open"][0]
    assert "snippet" not in open_thread


def test_a_draft_attachments_and_downloads_of_a_hidden_message_are_404(tmp_path, monkeypatch):
    tools, server, _w = _setup(tmp_path, monkeypatch)
    assert tools["gmail_get_draft"](draft_id="d-hidden")["error"] == "HTTP 404"
    assert tools["gmail_list_attachments"](message_id="m-hidden")["error"] == "HTTP 404"
    out = tools["gmail_download_attachment"](message_id="m-hidden", attachment_id="att-1")
    assert out["error"] == "HTTP 404"
    assert not any("/attachments/" in r for r in server.reads)  # the bytes were never fetched


def test_nothing_is_lost_when_nothing_is_filtered(tmp_path, monkeypatch):
    tools, server, _w = _setup(tmp_path, monkeypatch, filtered=False)
    assert len(tools["gmail_get_thread"](thread_id="t-mixed")["data"]["messages"]) == 2
    assert len(tools["gmail_search_threads"]()["data"]["threads"]) == 3
    assert tools["gmail_list_attachments"](message_id="m-hidden")["count"] == 1
    # the only thread fetch is the one get_thread asked for: the search added none, there was nothing to check
    assert [r for r in server.reads if r.startswith("threads/")] == ["threads/t-mixed"]


# ------------------------------------------------------------------ files on the way out


def _mime(server):
    (_m, _p, body), = [w for w in server.writes if w[1] in ("messages/send", "drafts")]
    raw = (body.get("message") or body)["raw"]
    return email.message_from_bytes(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))


def _attached(parsed):
    return [(p.get_filename(), p.get_payload(decode=True)) for p in parsed.walk() if p.get_filename()]


def test_a_file_in_the_session_folder_goes_out_on_send_and_draft(tmp_path, monkeypatch):
    tools, server, work = _setup(tmp_path, monkeypatch, filtered=False)
    (work / "report.txt").write_bytes(b"quarterly")
    tools["gmail_send_email"](to="a@b.test", subject="S", body="B", attachments=[str(work / "report.txt")])
    assert _attached(_mime(server)) == [("report.txt", b"quarterly")]
    server.writes.clear()
    tools["gmail_create_draft"](to="a@b.test", subject="S", body="B", attachments=[str(work / "report.txt")])
    assert _attached(_mime(server)) == [("report.txt", b"quarterly")]


def test_a_file_outside_the_session_is_refused_and_nothing_is_sent(tmp_path, monkeypatch):
    tools, server, work = _setup(tmp_path, monkeypatch, filtered=False)
    (tmp_path / "elsewhere.txt").write_text("private")
    for path in (tmp_path / "elsewhere.txt", work / ".." / "elsewhere.txt"):
        out = tools["gmail_send_email"](to="a@b.test", subject="S", body="B", attachments=[str(path)])
        assert "outside the session" in out["error"]
    assert not server.writes


def test_reeve_own_state_is_never_mailed_even_inside_a_root(tmp_path, monkeypatch):
    tools, server, _w = _setup(tmp_path, monkeypatch, filtered=False)
    (tmp_path / "state" / "secrets.json").write_text("{}")
    # the state folder is inside the tmp root here, so only the protected-file rule can refuse it
    from harness.config.roots import RootDir

    wide = [RootDir(path=tmp_path, writable=True)]
    from harness.integrations.connectors.integration_tools import make_integration_tools as build

    secrets = SecretStore(tmp_path / "s.json")
    send = {t.__name__: t for t in build(secrets, roots=wide)}["gmail_send_email"]
    out = send(to="a@b.test", subject="S", body="B", attachments=[str(tmp_path / "state" / "secrets.json")])
    assert "Reeve's own state" in out["error"]
    assert not server.writes


def test_a_remote_session_refuses_attachments(tmp_path, monkeypatch):
    tools, server, work = _setup(tmp_path, monkeypatch, filtered=False, remote=True)
    (work / "a.txt").write_text("x")
    out = tools["gmail_send_email"](to="a@b.test", subject="S", body="B", attachments=[str(work / "a.txt")])
    assert "remote session" in out["error"]
    assert not server.writes


def test_too_much_is_refused_before_google_does(tmp_path, monkeypatch):
    tools, server, work = _setup(tmp_path, monkeypatch, filtered=False)
    (work / "big.bin").write_bytes(b"0" * (19 * 1024 * 1024))
    out = tools["gmail_send_email"](to="a@b.test", subject="S", body="B", attachments=[str(work / "big.bin")])
    assert "MB" in out["error"]
    assert not server.writes


def test_a_forward_carries_the_original_files_and_says_so(tmp_path, monkeypatch):
    tools, server, work = _setup(tmp_path, monkeypatch, filtered=False)
    (work / "extra.txt").write_bytes(b"mine")
    out = tools["gmail_forward"](message_id="m-shown", to="c@d.test", attachments=[str(work / "extra.txt")])
    assert _attached(_mime(server)) == [("deal.pdf", b"%PDF"), ("extra.txt", b"mine")]
    assert out["attached"] == ["deal.pdf", "extra.txt"]


def test_a_forward_can_leave_the_original_files_out(tmp_path, monkeypatch):
    tools, server, _w = _setup(tmp_path, monkeypatch, filtered=False)
    out = tools["gmail_forward"](message_id="m-shown", to="c@d.test", include_attachments=False)
    assert _attached(_mime(server)) == []
    assert out["attached"] == []
    assert not [r for r in server.reads if "/attachments/" in r]


def test_a_reply_can_attach_a_file(tmp_path, monkeypatch):
    tools, server, work = _setup(tmp_path, monkeypatch, filtered=False)
    (work / "answer.txt").write_bytes(b"here")
    tools["gmail_reply"](message_id="m-shown", body="see attached", attachments=[str(work / "answer.txt")])
    parsed = _mime(server)
    assert _attached(parsed) == [("answer.txt", b"here")]
    assert parsed["In-Reply-To"] == "<m-shown@x>"
