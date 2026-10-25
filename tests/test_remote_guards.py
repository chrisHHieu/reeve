"""Local-path features that refuse on a remote session.

Every guard here exists to avoid one class of fake: a tool that reads or writes THIS
machine's disk while the session's world is an SSH host. The refusal text is the point —
"not available in a remote session", not an empty result that reads as "nothing there".
No test spawns ssh: the store's runner seam holds a `FakeSsh`.
"""

from __future__ import annotations

from harness.integrations.connectors.mail import make_email_tools
from harness.integrations.connectors.tool_builders.github import github_tools
from harness.integrations.connectors.tool_builders.gmail_labels import gmail_labels_tools
from harness.integrations.connectors.outbound import make_send_file_tool
from harness.orchestration.remote_environments import RemoteEnvironmentStore
from harness.safety.secrets import SecretStore
from harness.interface.server import SessionManager
from harness.session.sessions import SessionRecord
from harness.testing.fake_ssh import FakeSsh

PROFILE = {"name": "Build server", "host": "dev.example.com", "user": "dev"}
REMOTE = "/home/dev/project"


def _secrets(tmp_path) -> SecretStore:
    return SecretStore(tmp_path / "secrets.json")


def _managed(tmp_path):
    manager = SessionManager(workspace=tmp_path)
    store = RemoteEnvironmentStore(tmp_path / "remote-environments.json", runner=FakeSsh())
    manager.remote_environments = store
    profile = store.create(PROFILE)
    assert manager.set_session_environment("sess-r", profile["id"], REMOTE)["ok"]
    return manager, store, profile


def _by_name(tools) -> dict:
    return {t.__name__: t for t in tools}


# -- the messaging and connector file paths ---------------------------------------------


def test_send_file_refuses_on_a_remote_session(tmp_path):
    tool = make_send_file_tool(_secrets(tmp_path), remote=True)
    out = tool("slack:C9:1", "/home/dev/report.pdf")
    assert "not available in a remote session" in out["error"]
    assert "remote host" in out["error"]


def test_email_attachments_refuse_on_a_remote_session(tmp_path):
    tools = _by_name(make_email_tools(_secrets(tmp_path), remote=True))
    out = tools["email_send"](
        "a@b.c", "subject", "body", attachments=["/home/dev/report.pdf"]
    )
    assert "not available in a remote session" in out["error"]
    download = tools["email_download_attachment"]("1", "report.pdf")
    assert "not available in a remote session" in download["error"]


def test_gmail_attachment_download_refuses_on_a_remote_session(tmp_path):
    tools = _by_name(gmail_labels_tools(_secrets(tmp_path), remote=True))
    out = tools["gmail_download_attachment"]("msg-1", "att-1")
    assert "not available in a remote session" in out["error"]


def test_github_clone_refuses_on_a_remote_session(tmp_path):
    tools = _by_name(github_tools(_secrets(tmp_path), remote=True))
    out = tools["github_clone"]("acme", "site")
    assert "not available in a remote session" in out["error"]


def test_the_local_paths_still_work_when_not_remote(tmp_path):
    """The guard is the flag, not a blanket removal: local builds are untouched."""
    tool = make_send_file_tool(_secrets(tmp_path), remote=False)
    out = tool("slack:C9:1", "/home/dev/report.pdf")
    assert "not available in a remote session" not in out["error"]
    # (No roots and no such file — it refuses for the ordinary local reason.)
    assert "this session has no folder" in out["error"] or "no file at" in out["error"]

    tools = _by_name(gmail_labels_tools(_secrets(tmp_path), remote=False))
    assert "not available in a remote session" not in tools["gmail_download_attachment"](
        "msg-1", "att-1"
    ).get("error", "")


# -- the manager's local-disk read models -----------------------------------------------


def test_file_search_refuses_instead_of_listing_local_directories(tmp_path):
    manager, _store, _profile = _managed(tmp_path)
    out = manager.search_files("sess-r", query="notes")
    assert out["ok"] is False
    assert "remote host" in out["error"]
    assert out["files"] == [] and out["truncated"] is False


def test_artifact_reads_refuse_instead_of_resolving_the_remote_path_locally(tmp_path):
    manager, _store, _profile = _managed(tmp_path)
    out = manager.read_artifact("sess-r", "notes.txt")
    assert out["ok"] is False
    assert "remote host" in out["error"]


def test_artifacts_do_not_list_a_leftover_local_scratch(tmp_path):
    """A remote session must not show this machine's scratch as its artifacts."""
    manager, _store, _profile = _managed(tmp_path)
    scratch = manager.scratch_base() / "sess-r"
    scratch.mkdir(parents=True, exist_ok=True)
    (scratch / "report.html").write_text("<html></html>", encoding="utf-8")

    assert manager.list_artifacts("sess-r") == []
    assert manager._artifact_scan_root("sess-r") is None


def test_local_sessions_keep_the_local_read_models(tmp_path):
    manager = SessionManager(workspace=tmp_path)
    manager.session_store.save(
        SessionRecord(
            session_id="local-1",
            workspace=str(tmp_path),
            model="m",
            mode="interactive",
        )
    )
    (tmp_path / "notes.txt").write_text("local", encoding="utf-8")
    out = manager.search_files("local-1", query="notes")
    assert out["ok"] is True
    assert [f["name"] for f in out["files"]] == ["notes.txt"]
