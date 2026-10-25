"""The email connector (IMAP to read, SMTP to send): which servers an address reaches, the search an
IMAP server is sent, how a message's headers and body are read, and what each tool does.

No mailbox is touched: `_Imap` and `_Smtp` stand in for the servers and keep what they were asked.
"""

from __future__ import annotations

from email.message import EmailMessage

import pytest

from harness.config.roots import RootDir
from harness.integrations.connectors.mail import make_email_tools
from harness.integrations.connectors.mail.imap_query import build_search_criteria
from harness.integrations.connectors.mail.mime import decode_mime_header, extract_text_body
from harness.integrations.connectors.mail.servers import resolve_servers
from harness.safety.secrets import SecretStore

ADDRESS, PASSWORD = "user@gmail.com", "abcd efgh"
EMAIL_TOOLS = {"email_list_folders", "email_search", "email_read", "email_download_attachment", "email_send"}


class _Imap:
    """An IMAP server holding `messages` by uid; every command it is sent is kept."""

    def __init__(self, messages: dict[str, EmailMessage] | None = None):
        self.messages = messages or {}
        self.commands: list[tuple] = []
        self.logged_in = None

    def login(self, user, password):
        self.logged_in = (user, password)
        return "OK", [b"Logged in"]

    def select(self, folder, readonly=False):
        self.commands.append(("select", folder, readonly))
        return "OK", [b"42"]

    def list(self):
        return "OK", [b'(\\HasNoChildren) "/" "INBOX"', b'(\\HasNoChildren) "/" "[Gmail]/Sent Mail"', b'(\\Noselect \\HasChildren) "/" "[Gmail]"']

    def status(self, folder, what):
        return "OK", [folder.encode() + b" (MESSAGES 7)"]

    def uid(self, command, *args):
        self.commands.append(("uid", command) + args)
        if command == "SEARCH":
            return "OK", [b" ".join(uid.encode() for uid in sorted(self.messages, key=int))]
        if command != "FETCH":
            raise AssertionError(f"unexpected uid command {command}")
        uid, spec = args[0], args[1]
        uid = uid.decode() if isinstance(uid, bytes) else uid
        message = self.messages.get(uid)
        if message is None:
            return "OK", [None]
        raw = message.as_bytes()
        if "HEADER.FIELDS" in spec:
            names = spec.split("(")[2].rstrip(")]").split()
            raw = b"\r\n".join(f"{name}: {message.get(name)}".encode() for name in names if message.get(name)) + b"\r\n\r\n"
        meta = f"1 (UID {uid} FLAGS (\\Seen)".encode()
        if "BODYSTRUCTURE" in spec:
            meta += b' BODYSTRUCTURE ("ATTACHMENT" ("FILENAME" "x"))'
        return "OK", [(meta, raw), b")"]

    def logout(self):
        return "BYE", []


class _Smtp:
    def __init__(self):
        self.logged_in = None
        self.sent: list[EmailMessage] = []

    def login(self, user, password):
        self.logged_in = (user, password)

    def send_message(self, message):
        self.sent.append(message)

    def quit(self):
        pass


def _signed_in(tmp_path, **extra) -> SecretStore:
    secrets = SecretStore(tmp_path / "secrets.json")
    secrets.put("email:default", {"address": ADDRESS, "app_password": PASSWORD, **extra})
    return secrets


def _tools(secrets, *, imap=None, smtp=None, roots=None) -> dict:
    tools = make_email_tools(secrets, roots=roots, imap_factory=lambda host, port: imap or _Imap(), smtp_factory=lambda host, port: smtp or _Smtp())
    return {tool.__name__: tool for tool in tools}


def _report() -> EmailMessage:
    message = EmailMessage()
    message["From"] = "Ana <ana@example.com>"
    message["To"] = ADDRESS
    message["Subject"] = "Quarterly report"
    message["Date"] = "Mon, 09 Jun 2026 10:00:00 +0000"
    message["Message-ID"] = "<orig-123@example.com>"
    message.set_content("Plain body here.")
    message.add_alternative("<p>HTML body</p>", subtype="html")
    message.add_attachment(b"%PDF-fake", maintype="application", subtype="pdf", filename="report.pdf")
    return message


def _scratch(tmp_path):
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    return scratch


# -- servers -------------------------------------------------------------------------------------


def test_a_gmail_address_reaches_gmails_servers():
    servers, error = resolve_servers({"address": "x@gmail.com", "app_password": "p"})
    assert error == ""
    assert (servers.imap_host, servers.imap_port, servers.smtp_host, servers.smtp_port) == ("imap.gmail.com", 993, "smtp.gmail.com", 587)


def test_the_advanced_fields_override_the_providers_servers():
    servers, _ = resolve_servers({"address": "x@gmail.com", "imap_host": "mail.corp.io", "imap_port": "1993", "smtp_host": "smtp.corp.io", "smtp_port": "465"})
    assert (servers.imap_host, servers.imap_port, servers.smtp_host, servers.smtp_port) == ("mail.corp.io", 1993, "smtp.corp.io", 465)


def test_an_unknown_domain_asks_for_its_servers():
    servers, error = resolve_servers({"address": "x@unknown.example"})
    assert servers is None and "IMAP and SMTP host" in error


# -- the IMAP search -----------------------------------------------------------------------------


def test_no_filter_searches_everything():
    assert build_search_criteria() == (b"ALL", "")


def test_filters_are_quoted_dates_are_imaps_and_unread_comes_last():
    text = build_search_criteria(from_address='a "b" c@x.io', subject="hi", since="2026-01-05", before="2026-02-01", unread_only=True)[0].decode()
    assert 'FROM "a \\"b\\" c@x.io"' in text and 'SUBJECT "hi"' in text
    assert "SINCE 05-Jan-2026" in text and "BEFORE 01-Feb-2026" in text and text.endswith("UNSEEN")


def test_a_date_that_is_not_a_date_is_refused():
    criteria, error = build_search_criteria(since="last week")
    assert criteria is None and "YYYY-MM-DD" in error


def test_text_beyond_ascii_is_searched_as_utf8():
    criteria, _ = build_search_criteria(subject="日本語")
    assert criteria.startswith(b"CHARSET UTF-8 ") and "日本語".encode("utf-8") in criteria


# -- reading a message ---------------------------------------------------------------------------


def test_encoded_headers_are_decoded():
    assert decode_mime_header("=?utf-8?b?44GT44KT44Gr44Gh44Gv?=") == "こんにちは"
    assert decode_mime_header("plain") == "plain" and decode_mime_header(None) == ""


def test_the_plain_body_is_preferred_html_is_turned_to_text_and_a_long_body_is_cut():
    assert extract_text_body(_report()).strip() == "Plain body here."
    html = EmailMessage()
    html.set_content("<p>Hello <b>world</b></p>", subtype="html")
    assert extract_text_body(html) == "Hello world"
    long = EmailMessage()
    long.set_content("x" * 30_000)
    body = extract_text_body(long)
    assert body.endswith("…[truncated]") and len(body) < 30_000


# -- the tools -----------------------------------------------------------------------------------


@pytest.mark.parametrize(("tool", "arguments"), [("email_list_folders", {}), ("email_search", {}), ("email_read", {"uid": "1"})])
def test_each_tool_says_when_no_mailbox_is_connected(tmp_path, tool, arguments):
    assert "not connected" in _tools(SecretStore(tmp_path / "secrets.json"))[tool](**arguments)["error"]


def test_folders_that_cannot_be_opened_are_left_out(tmp_path):
    imap = _Imap()
    folders = _tools(_signed_in(tmp_path), imap=imap)["email_list_folders"]()["folders"]
    assert [f["name"] for f in folders] == ["INBOX", "[Gmail]/Sent Mail"] and all(f["messages"] == 7 for f in folders)
    assert imap.logged_in == (ADDRESS, PASSWORD)


def test_a_search_lists_the_newest_first_and_never_marks_anything_read(tmp_path):
    imap = _Imap({"1": _report(), "2": _report()})
    found = _tools(_signed_in(tmp_path), imap=imap)["email_search"](subject="report", max_results=5)
    assert found["ok"] and found["total_matches"] == 2 and [m["uid"] for m in found["messages"]] == ["2", "1"]
    assert found["messages"][0]["subject"] == "Quarterly report" and found["messages"][0]["has_attachments"] is True
    assert all(command[2] is True for command in imap.commands if command[0] == "select")  # read-only


def test_a_read_gives_the_body_and_lists_the_attachments(tmp_path):
    read = _tools(_signed_in(tmp_path), imap=_Imap({"7": _report()}))["email_read"](uid="7")
    assert read["body"].strip() == "Plain body here."
    assert read["attachments"] == [{"filename": "report.pdf", "content_type": "application/pdf", "size": 9}]


def test_an_attachment_is_saved_only_into_a_writable_folder_of_the_session(tmp_path):
    secrets = _signed_in(tmp_path)
    scratch = _scratch(tmp_path)
    nowhere = _tools(secrets, imap=_Imap({"7": _report()}))["email_download_attachment"](uid="7", filename="report.pdf")
    assert "no writable" in nowhere["error"]
    download = _tools(secrets, imap=_Imap({"7": _report()}), roots=[RootDir(path=scratch, writable=True)])["email_download_attachment"]
    saved = download(uid="7", filename="report.pdf")
    assert saved["ok"] and saved["path"] == str(scratch / "report.pdf")
    assert (scratch / "report.pdf").read_bytes() == b"%PDF-fake"
    assert "no attachment named" in download(uid="7", filename="nope.pdf")["error"]


def test_a_sent_message_is_from_the_person_and_needs_no_imap(tmp_path):
    smtp = _Smtp()
    assert _tools(_signed_in(tmp_path, display_name="Ana Smith"), smtp=smtp)["email_send"](to="ana@example.com", subject="Hi", body="Hello")["ok"]
    sent = smtp.sent[0]
    assert sent["From"] == f"Ana Smith <{ADDRESS}>" and sent["To"] == "ana@example.com"
    assert sent["Message-ID"].endswith("@gmail.com>") and smtp.logged_in == (ADDRESS, PASSWORD)


def test_a_reply_keeps_the_thread_and_the_subject(tmp_path):
    smtp = _Smtp()
    replied = _tools(_signed_in(tmp_path), imap=_Imap({"7": _report()}), smtp=smtp)["email_send"](to="ana@example.com", subject="", body="re!", reply_to_uid="7")
    assert replied["ok"] and replied["subject"] == "Re: Quarterly report"
    sent = smtp.sent[0]
    assert sent["In-Reply-To"] == "<orig-123@example.com>" and "<orig-123@example.com>" in sent["References"]
    assert sent["Subject"] == "Re: Quarterly report"


def test_an_attachment_from_outside_the_session_is_refused_and_nothing_is_sent(tmp_path):
    smtp = _Smtp()
    scratch = _scratch(tmp_path)
    (scratch / "ok.txt").write_text("fine")
    outside = tmp_path / "outside.txt"
    outside.write_text("nope")
    send = _tools(_signed_in(tmp_path), smtp=smtp, roots=[RootDir(path=scratch, writable=True)])["email_send"]
    refused = send(to="a@b.c", subject="s", body="b", attachments=[str(outside)])
    assert "outside the session" in refused["error"] and not smtp.sent
    assert send(to="a@b.c", subject="s", body="b", attachments=[str(scratch / "ok.txt")])["ok"]
    assert [part.get_filename() for part in smtp.sent[0].iter_attachments()] == ["ok.txt"]


def test_reading_runs_freely_and_saving_or_sending_asks(tmp_path):
    asks = {name: tool.__aisuite_tool_metadata__.requires_approval for name, tool in _tools(_signed_in(tmp_path)).items()}
    assert asks == {"email_list_folders": False, "email_search": False, "email_read": False, "email_download_attachment": True, "email_send": True}


def test_email_is_a_connector_with_an_app_password_and_its_tools_switch_one_by_one(tmp_path):
    from harness.integrations.connectors.catalog import TOOLS_BY_CONNECTOR, connector_for_tool
    from harness.integrations.connectors.descriptors import get_descriptor
    from harness.integrations.connectors.integration_tools import make_integration_tools

    descriptor = get_descriptor("email")
    assert descriptor is not None and descriptor.auth == "app_password"
    assert {tool.name for tool in TOOLS_BY_CONNECTOR["email"]} == EMAIL_TOOLS
    assert connector_for_tool("email_send") == "email"
    offered = make_integration_tools(_signed_in(tmp_path), enabled_connectors={"email"}, enabled_tools={"email_search", "email_send"})
    assert {tool.__name__ for tool in offered} == {"email_search", "email_send"}
