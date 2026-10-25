"""`send_file`: a finished file sent into the chat, with an approval of its own.

The platform's upload is replaced by a recorder, so nothing is sent. What a call may reach is the
workspace and the roots; a platform that takes no files and a missing token are said plainly; a
screenshot is for HTML only. A grant to send messages in a thread never covers sending a file there.
"""

from pathlib import Path

import pytest

from harness.config.roots import RootDir
from harness.integrations.connectors.messaging import SendResult
from harness.integrations.connectors.outbound import make_send_file_tool, make_send_message_tool
from harness.safety.permissions import Mode, PermissionEngine
from harness.safety.secrets import SecretStore


def _secrets(tmp_path, token="xoxb-1") -> SecretStore:
    secrets = SecretStore(tmp_path / "secrets.json")
    if token:
        secrets.put("slack:default", {"bot_token": token, "enabled": True})
    return secrets


@pytest.fixture
def workspace(tmp_path):
    folder = tmp_path / "ws"
    folder.mkdir()
    return folder


class _Uploads(list):
    """Slack's upload, kept instead of sent."""

    def senders(self):
        def upload(token, chat_id, thread_id, filename, data, title, comment):
            self.append({"token": token, "chat_id": chat_id, "thread_id": thread_id, "filename": filename, "data": data, "title": title, "comment": comment})
            return SendResult(True, message_id="F123")

        return {"slack": upload}


def _tool(tmp_path, workspace, uploads=None, **options):
    return make_send_file_tool(_secrets(tmp_path), workspace=workspace, file_senders=(uploads if uploads is not None else _Uploads()).senders(), **options)


def test_a_file_in_the_workspace_is_sent_to_the_thread_named(tmp_path, workspace):
    (workspace / "report.pdf").write_bytes(b"%PDF-fake")
    uploads = _Uploads()
    sent = _tool(tmp_path, workspace, uploads)("slack:C9:1700.1", "report.pdf", comment="here you go")
    assert sent == {"ok": True, "file_id": "F123", "target": "slack:C9:1700.1", "filename": "report.pdf"}
    [upload] = uploads
    assert (upload["chat_id"], upload["thread_id"]) == ("C9", "1700.1")
    assert (upload["data"], upload["comment"]) == (b"%PDF-fake", "here you go")


def test_a_file_outside_the_workspace_and_the_roots_is_refused(tmp_path, workspace):
    outside = tmp_path / "elsewhere.txt"
    outside.write_text("secret")
    send = _tool(tmp_path, workspace)
    for path in (str(outside), "../elsewhere.txt"):  # by its absolute path, or by climbing out
        assert "error" in send("slack:C9", path)


def test_a_root_can_be_sent_from_too(tmp_path, workspace):
    shared = tmp_path / "shared"
    shared.mkdir()
    (shared / "data.csv").write_text("a,b\n1,2\n")
    uploads = _Uploads()
    sent = _tool(tmp_path, workspace, uploads, roots=[RootDir(path=shared)])("slack:C9", str(shared / "data.csv"))
    assert sent["ok"] and uploads[0]["filename"] == "data.csv"


def test_a_platform_that_takes_no_files_and_a_missing_token_are_said_plainly(tmp_path, workspace):
    (workspace / "a.txt").write_text("x")
    # Item 4.4: it names the platforms that can take a file and what to do instead.
    refused = _tool(tmp_path, workspace)("telegram:123", "a.txt")["error"]
    assert "telegram" in refused and "slack" in refused and "link" in refused
    no_token = make_send_file_tool(_secrets(tmp_path / "nt", token=None), workspace=workspace, file_senders=_Uploads().senders())
    assert "no bot token" in no_token("slack:C9", "a.txt")["error"]


def test_a_screenshot_is_of_html_only_and_goes_as_a_png(tmp_path, workspace):
    (workspace / "dash.html").write_text("<h1>hi</h1>")
    (workspace / "notes.md").write_text("# hi")
    uploads = _Uploads()
    send = _tool(tmp_path, workspace, uploads, render_html=lambda page: b"PNG-bytes-for-" + Path(page).name.encode())
    refused = send("slack:C9", "notes.md", as_screenshot=True)["error"]
    assert ".html" in refused and "as a file instead" in refused  # and the way on
    shot = send("slack:C9", "dash.html", as_screenshot=True)
    assert shot["ok"] and shot["filename"] == "dash.png"
    assert uploads[-1]["data"] == b"PNG-bytes-for-dash.html"


def test_thread_send_message_grant_never_covers_send_file(tmp_path):
    """The grant for a mention's thread approves send_message to that target beforehand; the
    same target on send_file still asks, since the standing rules are kept per tool."""
    engine = PermissionEngine(workspace_root=tmp_path, mode=Mode.WORKSPACE_WRITE)
    thread = "slack:T1/C9:1700.1"
    engine.task_rules.setdefault("send_message", set()).add(thread)
    message = make_send_message_tool(_secrets(tmp_path)).__aisuite_tool_metadata__
    upload = make_send_file_tool(_secrets(tmp_path), workspace=tmp_path).__aisuite_tool_metadata__
    sent = engine.evaluate("send_message", {"target": thread, "text": "hi"}, message)
    assert sent.allowed and "standing rule" in sent.reason
    asked = engine.evaluate("send_file", {"target": thread, "path": "report.pdf"}, upload)
    assert not asked.allowed and asked.needs_user
