"""The WhatsApp tools' own rules, driven through a fake link: reads that say whose
words they carry, and sends that go only to known people, spaced, with files from the calling
session's folder; the daemon that serves them; the session server that forwards to it. No
WhatsApp anywhere."""

from __future__ import annotations

import asyncio

import pytest

from harness.integrations.whatsapp.daemon import TOOLS, dispatch, handler_for
from harness.integrations.whatsapp.records import Contact, Message
from harness.integrations.whatsapp.server import READ_ONLY, build_server
from harness.integrations.whatsapp.store import Store
from harness.integrations.whatsapp.tools import PEOPLE_NOTICE, Pacer, Tools, country_code

AN = "84901111111@s.whatsapp.net"
STRANGER = "84907777777@s.whatsapp.net"
ME = "84909999999@s.whatsapp.net"


class FakeLink:
    def __init__(self, store, paired=True):
        self.store, self.paired, self.sent = store, paired, []

    def status(self):
        return {"paired": self.paired, "connected": self.paired, "qr": "secret-qr"}

    def me(self):
        return ME

    def send_text(self, chat, text, *, reply_to=""):
        self.sent.append(("text", chat, text, reply_to))
        return {"sent": True, "id": "S1", "chat": chat}

    def send_file(self, chat, path, *, caption="", voice=False, reply_to=""):
        self.sent.append(("file", chat, path.name, caption))
        return {"sent": True, "id": "S2", "chat": chat}

    def react(self, chat, message_id, emoji):
        self.sent.append(("react", chat, message_id, emoji))
        return {"reacted": True}

    def mark_read(self, chat, ids):
        return {"read": len(ids)}

    def download(self, chat, message_id, folder):
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / "anh.jpg"
        target.write_bytes(b"jpg")
        return target


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


@pytest.fixture
def setup(tmp_path):
    store = Store(tmp_path / "state" / "messages.db")
    store.add([Message(chat=AN, id="a1", sender=AN, from_me=False, ts=100, text="Mai gặp nhé", sender_name="An")])
    store.add([Message(chat=STRANGER, id="s1", sender=ME, from_me=True, ts=100, text="hello")])
    store.put_contact(Contact(jid=AN, name="An Nguyễn"))
    workspace = tmp_path / "ws"
    workspace.mkdir()
    clock = Clock()
    link = FakeLink(store)
    yield Tools(link, Pacer(clock=clock)), link, workspace, clock
    store.close()


def test_reads_carry_the_notice_that_the_words_are_other_peoples(setup):
    tools, *_ = setup
    read = tools.read("an nguyen")
    assert read["notice"] == PEOPLE_NOTICE and read["chat"] == AN
    assert read["messages"][0]["text"] == "Mai gặp nhé"
    assert tools.search("gap")["notice"] == PEOPLE_NOTICE
    assert tools.chats()["notice"] == PEOPLE_NOTICE


def test_nothing_reads_or_sends_until_the_account_is_linked(setup):
    tools, link, *_ = setup
    link.paired = False
    for result in (tools.chats(), tools.read(AN), tools.send(AN, "hi"), tools.contacts()):
        assert "not linked" in result["error"]
    assert link.sent == []
    assert "qr" not in tools.status()  # the pairing code is the person's, never the model's


def test_a_send_goes_only_to_someone_the_person_already_talks_to(setup):
    tools, link, *_ = setup
    refused = tools.send(STRANGER, "chào")  # only I ever wrote there
    assert "never written" in refused["error"] and link.sent == []
    refused = tools.send("+84 906 000 000", "chào")  # a cold number
    assert "never written" in refused["error"]
    assert tools.send("an", "ok, 9h nhé", reply_to="a1")["sent"]
    assert link.sent == [("text", AN, "ok, 9h nhé", "a1")]


def test_sends_are_spaced_and_bounded_per_minute(setup):
    tools, link, _, clock = setup
    assert tools.send(AN, "một")["sent"]
    assert "wait" in tools.send(AN, "hai")["error"]
    clock.now += 3.1
    assert tools.send(AN, "hai")["sent"]
    for _ in range(10):
        clock.now += 3.1
        tools.send(AN, "...")
    clock.now += 3.1
    assert "this minute" in tools.send(AN, "thêm")["error"]
    clock.now += 61
    assert tools.send(AN, "sau")["sent"]


def test_a_file_must_lie_in_the_calling_sessions_folder(setup, tmp_path):
    tools, link, workspace, _ = setup
    (workspace / "bao-gia.pdf").write_bytes(b"%PDF")
    outside = tmp_path / "id_rsa"
    outside.write_text("key")
    ws = str(workspace)
    assert "only those can be sent" in tools.send(AN, file="../id_rsa", workspace=ws)["error"]
    assert "only those can be sent" in tools.send(AN, file=str(outside), workspace=ws)["error"]
    assert "only those can be sent" in tools.send(AN, file="bao-gia.pdf")["error"]  # no folder named
    assert tools.send(AN, "báo giá", file="bao-gia.pdf", workspace=ws)["sent"]
    assert link.sent[-1] == ("file", AN, "bao-gia.pdf", "báo giá")


def test_an_ambiguous_name_asks_which(setup):
    tools, link, *_ = setup
    link.store.put_contact(Contact(jid="84905555555@s.whatsapp.net", name="Anh Tuấn"))
    result = tools.send("an", "hi")
    assert "more than one" in result["error"] and len(result["candidates"]) == 2


def test_a_download_lands_in_the_calling_sessions_folder(setup):
    tools, _, workspace, _ = setup
    saved = tools.download(AN, "a1", workspace=str(workspace))
    assert (workspace / saved["saved"]).read_bytes() == b"jpg" and saved["bytes"] == 3


def test_a_local_number_takes_the_accounts_country_code():
    assert country_code(ME) == "84"
    assert country_code("15551234567@s.whatsapp.net") == "1"
    assert country_code("") == ""


def test_reads_are_marked_read_only_and_sends_are_not():
    pytest.importorskip("mcp")
    calls = []
    server = build_server(lambda tool, args: calls.append((tool, args)) or {"ok": True})
    listed = asyncio.run(server.list_tools())
    read_only = {t.name for t in listed if t.annotations and t.annotations.readOnlyHint}
    assert read_only == set(READ_ONLY)
    assert {t.name for t in listed} - read_only == {"whatsapp_send", "whatsapp_react", "whatsapp_mark_read"}
    asyncio.run(server.call_tool("whatsapp_send", {"chat": "An", "text": "hi"}))
    assert calls == [("send", {"chat": "An", "text": "hi", "file": "", "reply_to": "", "voice": False})]


def test_the_daemon_dispatches_only_its_tools_and_gives_files_the_callers_folder(setup):
    tools, link, workspace, _ = setup
    assert "unknown" in dispatch(tools, "logout", {}, str(workspace))["error"]
    assert "bad arguments" in dispatch(tools, "read", {"nope": 1}, "")["error"]
    (workspace / "a.txt").write_text("x")
    assert dispatch(tools, "send", {"chat": AN, "file": "a.txt"}, str(workspace))["sent"]
    assert set(TOOLS) == {"status", "chats", "read", "search", "contacts", "download", "send", "react", "mark_read"}


def test_the_daemon_answers_only_with_its_token(setup):
    import json
    import threading
    import urllib.error
    import urllib.request
    from http.server import ThreadingHTTPServer

    tools, *_ = setup
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler_for("t0k", {"GET /status": lambda _p: tools.status()}))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_address[1]}/status"
    try:
        with pytest.raises(urllib.error.HTTPError) as refused:
            urllib.request.urlopen(urllib.request.Request(url, headers={"Authorization": "Bearer wrong"}))
        assert refused.value.code == 401
        with urllib.request.urlopen(urllib.request.Request(url, headers={"Authorization": "Bearer t0k"})) as ok:
            assert json.loads(ok.read())["paired"] is True
    finally:
        server.shutdown()
