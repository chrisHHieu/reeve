"""`/open` — the workspace file search behind the composer's file picker (the
`file.open`).

What must hold: the walk is confined to the session's roots (workspace, scratch, extra
roots — never a caller-supplied path, so traversal is not a reachable shape), noise
directories are pruned, a symlink out of the tree is refused, results are ranked and
capped, and the route is wired.
"""

from __future__ import annotations

import os
from pathlib import Path

from harness.llm.providers import AssistantTurn, ModelCapabilities, ProviderClient
from harness.interface.server.manager import SessionManager
from harness.session.sessions import SessionRecord


class Provider(ProviderClient):
    def complete(self, *, model, messages, tools=None, **settings):
        return AssistantTurn(text="ok", finish_reason="stop")

    def capabilities(self, model):
        return ModelCapabilities()


def _manager(tmp_path, monkeypatch) -> SessionManager:
    monkeypatch.setenv("REEVE_STATE_DIR", str(tmp_path / "state"))
    ws = tmp_path / "ws"
    ws.mkdir()
    return SessionManager(workspace=ws, provider=Provider())


def _session(mgr: SessionManager, sid: str, **kwargs) -> SessionRecord:
    record = SessionRecord(
        session_id=sid,
        workspace=str(mgr.default_workspace),
        model="gpt-5.5",
        mode="interactive",
        **kwargs,
    )
    mgr.session_store.save(record)
    return record


def test_lists_files_under_the_workspace(tmp_path, monkeypatch):
    mgr = _manager(tmp_path, monkeypatch)
    ws = Path(mgr.default_workspace)
    (ws / "notes.md").write_text("hi", encoding="utf-8")
    (ws / "src").mkdir()
    (ws / "src" / "app.py").write_text("print(1)", encoding="utf-8")
    _session(mgr, "s1")

    out = mgr.search_files("s1")

    assert out["ok"] is True and out["truncated"] is False
    assert [f["path"] for f in out["files"]] == ["notes.md", "src/app.py"]
    assert out["files"][1]["name"] == "app.py"
    assert out["files"][1]["abs_path"] == str(ws / "src" / "app.py")


def test_the_scratch_and_extra_roots_are_included(tmp_path, monkeypatch):
    mgr = _manager(tmp_path, monkeypatch)
    extra = tmp_path / "extra"
    extra.mkdir()
    (extra / "shared.md").write_text("x", encoding="utf-8")
    _session(mgr, "s2", extra_roots=[{"path": str(extra), "writable": False, "label": "extra"}])
    scratch = Path(mgr._provision_scratch("s2"))
    (scratch / "draft.md").write_text("x", encoding="utf-8")

    paths = {f["path"] for f in mgr.search_files("s2")["files"]}

    assert paths == {"draft.md", "shared.md"}


def test_noise_directories_are_pruned(tmp_path, monkeypatch):
    mgr = _manager(tmp_path, monkeypatch)
    ws = Path(mgr.default_workspace)
    (ws / "keep.md").write_text("x", encoding="utf-8")
    for noise in ("node_modules", ".git", "venv", "__pycache__", "dist", "target"):
        d = ws / noise
        d.mkdir()
        (d / "hidden.txt").write_text("x", encoding="utf-8")
    (ws / ".secret").write_text("x", encoding="utf-8")
    _session(mgr, "s3")

    out = mgr.search_files("s3")

    assert [f["path"] for f in out["files"]] == ["keep.md"]


def test_files_outside_the_roots_are_never_listed(tmp_path, monkeypatch):
    mgr = _manager(tmp_path, monkeypatch)
    (tmp_path / "outside.txt").write_text("x", encoding="utf-8")
    ws = Path(mgr.default_workspace)
    (ws / "inside.txt").write_text("x", encoding="utf-8")
    _session(mgr, "s4")

    out = mgr.search_files("s4")

    assert [f["path"] for f in out["files"]] == ["inside.txt"]
    # A traversal-shaped query cannot reach outside either: the query only filters
    # relative paths, and no joined path is ever built from it.
    assert mgr.search_files("s4", query="..")["files"] == []
    assert mgr.search_files("s4", query="outside")["files"] == []


def test_a_symlink_out_of_the_tree_is_refused(tmp_path, monkeypatch):
    mgr = _manager(tmp_path, monkeypatch)
    secret = tmp_path / "secret.txt"
    secret.write_text("x", encoding="utf-8")
    ws = Path(mgr.default_workspace)
    try:
        os.symlink(secret, ws / "leak.txt")
    except (OSError, NotImplementedError):
        return  # Windows without the privilege: nothing to assert
    (ws / "real.txt").write_text("x", encoding="utf-8")
    _session(mgr, "s5")

    paths = [f["path"] for f in mgr.search_files("s5")["files"]]

    assert "real.txt" in paths and "leak.txt" not in paths


def test_results_are_ranked_and_capped(tmp_path, monkeypatch):
    mgr = _manager(tmp_path, monkeypatch)
    ws = Path(mgr.default_workspace)
    (ws / "deep").mkdir()
    (ws / "alpha-dir").mkdir()
    for name in (
        "alpha.txt",
        "deep/alpha-notes.txt",
        "deep/alphabet.txt",
        "my-alpha-notes.txt",
        "alpha-dir/notes.txt",
    ):
        (ws / name).write_text("x", encoding="utf-8")
    _session(mgr, "s6")

    out = mgr.search_files("s6", query="alpha")

    # Name-prefix hits first (path order within the bucket), then name-substring, then
    # path-only — never filesystem order.
    assert [f["path"] for f in out["files"]] == [
        "alpha.txt",
        "deep/alpha-notes.txt",
        "deep/alphabet.txt",
        "my-alpha-notes.txt",
        "alpha-dir/notes.txt",
    ]

    capped = mgr.search_files("s6", limit=2)
    assert len(capped["files"]) == 2 and capped["truncated"] is True
    assert len(mgr.search_files("s6", limit=0)["files"]) == 1  # clamped, never empty-capped


def test_an_unknown_session_has_no_roots(tmp_path, monkeypatch):
    mgr = _manager(tmp_path, monkeypatch)
    out = mgr.search_files("nobody")
    assert out["ok"] is True and out["files"] == []


def test_the_route_is_wired(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from harness.interface.server import create_app

    mgr = _manager(tmp_path, monkeypatch)
    ws = Path(mgr.default_workspace)
    (ws / "report.md").write_text("x", encoding="utf-8")
    _session(mgr, "route-files")
    client = TestClient(create_app(mgr))

    resp = client.get("/v1/sessions/route-files/files", params={"q": "report"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert [f["path"] for f in body["files"]] == ["report.md"]
    assert body["truncated"] is False


def test_the_index_is_every_file_once_for_the_composer_to_filter(tmp_path, monkeypatch):
    """The composer's "@" asked for a search per keystroke, and each walked the whole tree
    again (owner, 2026-10-13: it was not live). The index is one walk the composer filters;
    it is pruned and confined exactly like the search."""
    from fastapi.testclient import TestClient

    from harness.interface.server import create_app

    mgr = _manager(tmp_path, monkeypatch)
    ws = Path(mgr.default_workspace)
    (ws / "docs").mkdir()
    (ws / "docs" / "API.md").write_text("x", encoding="utf-8")
    (ws / "README.md").write_text("x", encoding="utf-8")
    (ws / "node_modules").mkdir()
    (ws / "node_modules" / "dep.js").write_text("x", encoding="utf-8")
    (tmp_path / "outside.txt").write_text("x", encoding="utf-8")
    _session(mgr, "idx")

    out = mgr.file_index("idx")
    assert out == {"ok": True, "files": ["docs/API.md", "README.md"], "truncated": False}

    body = TestClient(create_app(mgr)).get("/v1/sessions/idx/files/index").json()
    assert body["files"] == ["docs/API.md", "README.md"]


# -- File-manager mutations (the GUI's New file / New folder / Rename / Move) --------------


def test_create_file_and_folder_under_the_workspace(tmp_path, monkeypatch):
    mgr = _manager(tmp_path, monkeypatch)
    ws = Path(mgr.default_workspace)
    (ws / "src").mkdir()
    _session(mgr, "e1")

    file_out = mgr.create_file_entry("e1", "src/new.md", "file")
    folder_out = mgr.create_file_entry("e1", "assets", "folder")

    assert file_out == {"ok": True, "path": "src/new.md"}
    assert folder_out == {"ok": True, "path": "assets"}
    assert (ws / "src" / "new.md").is_file()
    assert (ws / "assets").is_dir()
    # A second create never clobbers what is there.
    again = mgr.create_file_entry("e1", "src/new.md", "file")
    assert again["ok"] is False and "already exists" in again["error"]


def test_writes_stay_inside_the_writable_roots(tmp_path, monkeypatch):
    mgr = _manager(tmp_path, monkeypatch)
    outside = tmp_path / "outside.txt"
    outside.write_text("x", encoding="utf-8")
    read_only = tmp_path / "read-only"
    read_only.mkdir()
    _session(
        mgr,
        "e2",
        extra_roots=[{"path": str(read_only), "writable": False, "label": "ro"}],
    )

    assert mgr.create_file_entry("e2", "../outside.txt", "file")["ok"] is False
    assert mgr.create_file_entry("e2", "/etc/passwd", "file")["ok"] is False
    assert mgr.create_file_entry("e2", "C:/Windows/x.txt", "file")["ok"] is False
    assert mgr.create_file_entry("e2", "read-only/x.txt", "file")["ok"] is False
    assert mgr.create_file_entry("nobody", "x.txt", "file")["ok"] is False
    assert not (read_only / "x.txt").exists()
    assert outside.read_text(encoding="utf-8") == "x"


def test_credentials_and_the_git_store_are_never_touched(tmp_path, monkeypatch):
    mgr = _manager(tmp_path, monkeypatch)
    ws = Path(mgr.default_workspace)
    (ws / ".git").mkdir()
    _session(mgr, "e3")

    for path in (".env", ".env.local", ".git/config", "keys/id.pem", ".ssh/known_hosts"):
        assert mgr.create_file_entry("e3", path, "file")["ok"] is False
        assert mgr.rename_file_entry("e3", "notes.md", path)["ok"] is False


def test_rename_and_move_within_one_root(tmp_path, monkeypatch):
    mgr = _manager(tmp_path, monkeypatch)
    ws = Path(mgr.default_workspace)
    (ws / "notes.md").write_text("x", encoding="utf-8")
    (ws / "archive").mkdir()
    (ws / "archive" / "notes.md").write_text("old", encoding="utf-8")
    _session(mgr, "e4")

    renamed = mgr.rename_file_entry("e4", "notes.md", "summary.md")
    assert renamed == {"ok": True, "path": "summary.md"}
    assert (ws / "summary.md").is_file() and not (ws / "notes.md").exists()

    moved = mgr.move_file_entry("e4", "summary.md", "archive")
    assert moved == {"ok": True, "path": "archive/summary.md"}
    assert (ws / "archive" / "summary.md").is_file()

    # Collisions and non-folders are refused; a missing source is refused too.
    assert mgr.move_file_entry("e4", "archive/summary.md", "archive")["ok"] is True
    assert mgr.rename_file_entry("e4", "archive/summary.md", "notes.md")["ok"] is False
    assert mgr.move_file_entry("e4", "archive/summary.md", "archive/notes.md")["ok"] is False
    assert mgr.rename_file_entry("e4", "gone.md", "x.md")["ok"] is False


def test_a_move_cannot_cross_session_folders(tmp_path, monkeypatch):
    mgr = _manager(tmp_path, monkeypatch)
    ws = Path(mgr.default_workspace)
    (ws / "notes.md").write_text("x", encoding="utf-8")
    _session(mgr, "e5")
    scratch = Path(mgr._provision_scratch("e5"))
    (scratch / "inbox").mkdir()

    out = mgr.move_file_entry("e5", "notes.md", "inbox")

    # `inbox` resolves under the scratch root, not the file's workspace root.
    assert out["ok"] is False and "different session folders" in out["error"]
    assert (ws / "notes.md").is_file()


def test_writing_a_file_round_trips_with_its_mtime_token(tmp_path, monkeypatch):
    mgr = _manager(tmp_path, monkeypatch)
    ws = Path(mgr.default_workspace)
    (ws / "notes.md").write_bytes(b"one\n")
    _session(mgr, "w1")

    read = mgr.read_artifact("w1", "notes.md")
    assert read["mtime_ms"] > 0 and read["size"] == 4

    saved = mgr.write_file_entry("w1", "notes.md", "one\ntwo\n", read["mtime_ms"])

    assert saved["ok"] is True and saved["size"] == 8
    assert (ws / "notes.md").read_bytes() == b"one\ntwo\n"
    # The returned token is the new one, so the next save succeeds without a reload.
    again = mgr.write_file_entry("w1", "notes.md", "three\n", saved["mtime_ms"])
    assert again["ok"] is True


def test_a_save_keeps_the_files_line_endings_and_bom(tmp_path, monkeypatch):
    mgr = _manager(tmp_path, monkeypatch)
    ws = Path(mgr.default_workspace)
    (ws / "win.md").write_bytes(b"\xef\xbb\xbfone\r\ntwo\r\n")
    _session(mgr, "w4")

    read = mgr.read_artifact("w4", "win.md")
    assert read["content"] == "one\ntwo\n"

    saved = mgr.write_file_entry("w4", "win.md", "one\ntwo\nthree\n", read["mtime_ms"])

    assert saved["ok"] is True
    assert (ws / "win.md").read_bytes() == b"\xef\xbb\xbfone\r\ntwo\r\nthree\r\n"


def test_a_file_changed_on_disk_refuses_the_save_with_a_conflict(tmp_path, monkeypatch):
    mgr = _manager(tmp_path, monkeypatch)
    ws = Path(mgr.default_workspace)
    (ws / "notes.md").write_text("one\n", encoding="utf-8")
    _session(mgr, "w2")
    token = mgr.read_artifact("w2", "notes.md")["mtime_ms"]

    # Somebody else writes it after the read, a second later: a write inside the same millisecond
    # leaves the token as it was, and the test then saw no conflict (once, under load).
    (ws / "notes.md").write_text("someone else\n", encoding="utf-8")
    os.utime(ws / "notes.md", ns=((ws / "notes.md").stat().st_atime_ns, (token + 1000) * 1_000_000))

    out = mgr.write_file_entry("w2", "notes.md", "mine\n", token)

    assert out["ok"] is False and out["conflict"] is True
    assert (ws / "notes.md").read_text(encoding="utf-8") == "someone else\n"
    # Forcing the save (no token) is allowed — the surface asks before it gets here.
    assert mgr.write_file_entry("w2", "notes.md", "mine\n")["ok"] is True


def test_writes_are_guarded_like_the_other_mutations(tmp_path, monkeypatch):
    mgr = _manager(tmp_path, monkeypatch)
    ws = Path(mgr.default_workspace)
    (ws / "notes.md").write_text("x", encoding="utf-8")
    (ws / "src").mkdir()
    read_only = tmp_path / "ro"
    read_only.mkdir()
    (read_only / "locked.md").write_text("x", encoding="utf-8")
    _session(mgr, "w3", extra_roots=[{"path": str(read_only), "writable": False}])

    assert mgr.write_file_entry("w3", "src", "x")["ok"] is False  # a folder
    assert mgr.write_file_entry("w3", "../outside.md", "x")["ok"] is False
    assert mgr.write_file_entry("w3", ".env", "x")["ok"] is False
    assert mgr.write_file_entry("w3", "ro/locked.md", "x")["ok"] is False
    assert mgr.write_file_entry("w3", "notes.md", "x" * (2 * 1024 * 1024 + 1))["ok"] is False
    assert mgr.write_file_entry("nobody", "notes.md", "x")["ok"] is False
    assert (read_only / "locked.md").read_text(encoding="utf-8") == "x"


def test_the_mutation_routes_are_wired(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from harness.interface.server import create_app

    mgr = _manager(tmp_path, monkeypatch)
    ws = Path(mgr.default_workspace)
    (ws / "notes.md").write_text("x", encoding="utf-8")
    _session(mgr, "route-edit")
    client = TestClient(create_app(mgr))

    created = client.post(
        "/v1/sessions/route-edit/files/create", json={"path": "draft.md", "kind": "file"}
    ).json()
    written = client.post(
        "/v1/sessions/route-edit/files/write",
        json={"path": "draft.md", "content": "hello", "mtime_ms": created.get("mtime_ms")},
    ).json()
    renamed = client.post(
        "/v1/sessions/route-edit/files/rename", json={"path": "draft.md", "name": "final.md"}
    ).json()
    moved = client.post(
        "/v1/sessions/route-edit/files/move", json={"path": "final.md", "to": "."}
    ).json()

    assert created == {"ok": True, "path": "draft.md"}
    assert written["ok"] is True and written["size"] == 5
    assert renamed == {"ok": True, "path": "final.md"}
    assert moved == {"ok": True, "path": "final.md"}
    assert (ws / "final.md").read_text(encoding="utf-8") == "hello"


def _fake_trash(monkeypatch, tmp_path):
    """The system trash, stood in for by a folder: a test never touches the real Recycle Bin."""
    from harness.interface.server.manager import trash

    bin_ = tmp_path / "bin"
    bin_.mkdir()
    moved = []

    def to_bin(path):
        moved.append(path.name)
        path.rename(bin_ / path.name)
        return None

    monkeypatch.setattr(trash, "move_to_trash", to_bin)
    return bin_, moved


def test_delete_sends_a_file_or_folder_to_the_trash(tmp_path, monkeypatch):
    mgr = _manager(tmp_path, monkeypatch)
    ws = Path(mgr.default_workspace)
    (ws / "notes.md").write_text("x", encoding="utf-8")
    (ws / "old").mkdir()
    (ws / "old" / "a.txt").write_text("a", encoding="utf-8")
    _session(mgr, "d1")
    bin_, moved = _fake_trash(monkeypatch, tmp_path)

    assert mgr.delete_file_entry("d1", "notes.md") == {"ok": True, "path": "notes.md"}
    assert mgr.delete_file_entry("d1", "old") == {"ok": True, "path": "old"}
    assert not (ws / "notes.md").exists() and not (ws / "old").exists()
    assert (bin_ / "old" / "a.txt").read_text(encoding="utf-8") == "a"
    assert moved == ["notes.md", "old"]


def test_delete_is_guarded_like_the_other_mutations(tmp_path, monkeypatch):
    mgr = _manager(tmp_path, monkeypatch)
    ws = Path(mgr.default_workspace)
    (ws / ".env").write_text("SECRET=1", encoding="utf-8")
    (ws / ".git").mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("x", encoding="utf-8")
    read_only = tmp_path / "read-only"
    read_only.mkdir()
    (read_only / "keep.txt").write_text("k", encoding="utf-8")
    _session(mgr, "d2", extra_roots=[{"path": str(read_only), "writable": False, "label": "ro"}])
    _bin, moved = _fake_trash(monkeypatch, tmp_path)

    # The session's folder itself, credentials, the git store, a path that escapes, a read-only
    # folder and an unknown session: each refused, nothing moved.
    for path in (".", "", ".env", ".git", "../outside.txt", "read-only/keep.txt"):
        assert mgr.delete_file_entry("d2", path)["ok"] is False, path
    assert mgr.delete_file_entry("nobody", "x.txt")["ok"] is False
    assert moved == []
    assert (ws / ".env").exists() and outside.exists() and (read_only / "keep.txt").exists()


def test_the_delete_route_is_wired(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from harness.interface.server import create_app

    mgr = _manager(tmp_path, monkeypatch)
    ws = Path(mgr.default_workspace)
    (ws / "draft.md").write_text("x", encoding="utf-8")
    _session(mgr, "route-delete")
    _fake_trash(monkeypatch, tmp_path)
    client = TestClient(create_app(mgr))

    out = client.post("/v1/sessions/route-delete/files/delete", json={"path": "draft.md"}).json()

    assert out == {"ok": True, "path": "draft.md"}
    assert not (ws / "draft.md").exists()


def test_the_freedesktop_trash_keeps_what_restores_the_file(tmp_path, monkeypatch):
    from harness.interface.server.manager.trash import _trash_freedesktop

    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    (tmp_path / "work").mkdir()
    first = tmp_path / "work" / "notes.md"
    first.write_text("one", encoding="utf-8")
    assert _trash_freedesktop(first) is None
    second = tmp_path / "work" / "notes.md"
    second.write_text("two", encoding="utf-8")
    assert _trash_freedesktop(second) is None

    files = tmp_path / "data" / "Trash" / "files"
    # A second file of the same name does not overwrite the first.
    assert (files / "notes.md").read_text(encoding="utf-8") == "one"
    assert (files / "notes 2.md").read_text(encoding="utf-8") == "two"
    info = (tmp_path / "data" / "Trash" / "info" / "notes 2.md.trashinfo").read_text(encoding="utf-8")
    assert info.startswith("[Trash Info]\nPath=") and "notes.md" in info and "DeletionDate=" in info


def test_the_tree_s_full_paths_are_taken_inside_a_writable_folder(tmp_path, monkeypatch):
    """The file manager's tree names entries by their full path; a mutation takes it when it
    lies inside a writable session folder and judges it as that folder's relative path."""
    mgr = _manager(tmp_path, monkeypatch)
    ws = Path(mgr.default_workspace)
    (ws / "notes.md").write_text("x", encoding="utf-8")
    (ws / "archive").mkdir()
    (ws / ".env").write_text("SECRET=1", encoding="utf-8")
    read_only = tmp_path / "read-only"
    read_only.mkdir()
    (read_only / "keep.txt").write_text("k", encoding="utf-8")
    _session(mgr, "a1", extra_roots=[{"path": str(read_only), "writable": False, "label": "ro"}])
    _bin, moved = _fake_trash(monkeypatch, tmp_path)

    assert mgr.rename_file_entry("a1", str(ws / "notes.md"), "summary.md")["ok"] is True
    assert mgr.move_file_entry("a1", str(ws / "summary.md"), str(ws / "archive"))["ok"] is True
    assert (ws / "archive" / "summary.md").is_file()
    assert mgr.create_file_entry("a1", str(ws / "archive" / "draft.md"), "file")["ok"] is True
    assert mgr.delete_file_entry("a1", str(ws / "archive" / "draft.md"))["ok"] is True
    assert moved == ["draft.md"]

    # Everything the relative form refuses, the full form refuses too: the folder itself,
    # credentials, a read-only folder, a path outside every folder.
    for path in (str(ws), str(ws / ".env"), str(read_only / "keep.txt"), str(tmp_path / "elsewhere.md")):
        assert mgr.delete_file_entry("a1", path)["ok"] is False, path
        assert mgr.rename_file_entry("a1", path, "x.md")["ok"] is False, path
    assert mgr.move_file_entry("a1", str(ws), str(ws / "archive"))["ok"] is False
    assert moved == ["draft.md"]
    assert ws.is_dir() and (ws / ".env").exists() and (read_only / "keep.txt").exists()


# -- What a file is read as, and that a save keeps it -----------------------------------
#
# The Files tab knew one encoding. A Vietnamese Windows-1258 note or a UTF-16 log (what
# `cmd > out.txt` writes on Windows PowerShell 5.1) was "binary file cannot be previewed" to
# the person, while `read_file` read it for the model. Both now make the one decision in
# `tools/text_sniff.py`, and the save writes the same encoding back.


_VIETNAMESE_TEXT = (
    "Họp thứ Hai: chốt phạm vi và lịch bàn giao.\n"
    "Việc cần làm trước thứ Sáu: gửi bản đề xuất.\n"
)
# The same two lines in Windows-1258 as Windows writes it: most tone marks are a byte of their
# own after the letter (0xF2 dot below, 0xEC acute, 0xCC grave, 0xD2 hook, 0xDE tilde), which
# Python's own codec cannot produce from a composed "ọ".
_VIETNAMESE_1258 = (
    b"Ho\xf2p th\xfd\xec Hai: ch\xf4\xect pha\xf2m vi v\xe0 li\xf2ch b\xe0n giao.\r\n"
    b"Vi\xea\xf2c c\xe2\xccn l\xe0m tr\xfd\xf5\xecc th\xfd\xec S\xe1u: g\xfd\xd2i ba\xd2n \xf0\xea\xcc xu\xe2\xect.\r\n"
)


def test_a_file_that_is_not_utf8_is_read_as_what_it_is(tmp_path, monkeypatch):
    mgr = _manager(tmp_path, monkeypatch)
    ws = Path(mgr.default_workspace)
    _session(mgr, "s1")
    # Windows-1258 as Windows writes it: most tone marks are a byte of their own after the
    # letter (0xF2 dot below, 0xEC acute), which Python's own codec cannot produce from "ọ".
    (ws / "ghi-chu.txt").write_bytes(_VIETNAMESE_1258)
    (ws / "log.txt").write_bytes("build finished\r\n".encode("utf-16"))

    note = mgr.read_artifact("s1", "ghi-chu.txt")
    assert note["ok"] and note["encoding"] == "cp1258"
    # Composed letters, the form every other file, search and model uses.
    assert note["content"] == _VIETNAMESE_TEXT

    log = mgr.read_artifact("s1", "log.txt")
    assert log["ok"] and log["encoding"] == "utf-16"
    assert log["content"] == "build finished\n"  # no BOM in the text


def test_a_save_writes_the_encoding_the_file_had(tmp_path, monkeypatch):
    mgr = _manager(tmp_path, monkeypatch)
    ws = Path(mgr.default_workspace)
    _session(mgr, "s1")
    note = ws / "ghi-chu.txt"
    note.write_bytes(_VIETNAMESE_1258)
    log = ws / "log.txt"
    log.write_bytes("one\n".encode("utf-16"))

    edited = _VIETNAMESE_TEXT + "Đã chốt: thứ Ba.\n"
    assert mgr.write_file_entry("s1", "ghi-chu.txt", edited)["ok"]
    again = mgr.read_artifact("s1", "ghi-chu.txt")
    assert again["encoding"] == "cp1258" and again["content"] == edited
    assert note.read_bytes().startswith(_VIETNAMESE_1258)  # what was there is byte for byte the same
    assert b"\r\n" in note.read_bytes()  # its line endings too

    assert mgr.write_file_entry("s1", "log.txt", "one\ntwo\n")["ok"]
    assert log.read_bytes() == "one\ntwo\n".encode("utf-16")  # BOM and byte order kept


def test_a_character_the_files_encoding_cannot_hold_refuses_the_save(tmp_path, monkeypatch):
    mgr = _manager(tmp_path, monkeypatch)
    ws = Path(mgr.default_workspace)
    _session(mgr, "s1")
    note = ws / "ghi-chu.txt"
    before = b"Ho\xf2p th\xfd\xec Hai.\n"
    note.write_bytes(before)

    refused = mgr.write_file_entry("s1", "ghi-chu.txt", "Họp thứ Hai 日本.\n", encoding="cp1258")
    assert refused["ok"] is False and "cp1258" in refused["error"] and "'日'" in refused["error"]
    assert note.read_bytes() == before  # nothing written, nothing replaced with "?"


def test_a_short_note_is_guessed_wrong_and_the_person_can_name_its_encoding(tmp_path, monkeypatch):
    """Measured 2026-10-11: the detector names Windows-1258 from about 100 bytes on, and this
    15-byte note as something else. Naming it by hand reads it right, and the save keeps it."""
    mgr = _manager(tmp_path, monkeypatch)
    ws = Path(mgr.default_workspace)
    _session(mgr, "s1")
    note = ws / "ghi-chu.txt"
    note.write_bytes(b"Ho\xf2p th\xfd\xec Hai.\n")

    guessed = mgr.read_artifact("s1", "ghi-chu.txt")
    assert guessed["ok"] and "cp1258" in guessed["encodings"]
    assert guessed["content"] != "Họp thứ Hai.\n"

    named = mgr.read_artifact("s1", "ghi-chu.txt", "cp1258")
    assert named["encoding"] == "cp1258" and named["content"] == "Họp thứ Hai.\n"

    assert mgr.write_file_entry("s1", "ghi-chu.txt", "Họp thứ Ba.\n", encoding="cp1258")["ok"]
    assert note.read_bytes() == b"Ho\xf2p th\xfd\xec Ba.\n"
    # A name that is not on the list is not run as a codec: the read falls back to its guess.
    assert mgr.read_artifact("s1", "ghi-chu.txt", "rot13")["encoding"] != "rot13"


def test_more_pictures_and_sheets_open_and_what_cannot_says_why(tmp_path, monkeypatch):
    mgr = _manager(tmp_path, monkeypatch)
    ws = Path(mgr.default_workspace)
    _session(mgr, "s1")
    (ws / "icon.bmp").write_bytes(b"BM" + b"\x00" * 60)
    (ws / "book.xlsm").write_bytes(b"PK\x03\x04" + b"\x00" * 40)
    (ws / "backup.zip").write_bytes(b"PK\x03\x04" + b"\x00" * 400)

    picture = mgr.read_artifact("s1", "icon.bmp")
    assert picture["kind"] == "image" and picture["data_url"].startswith("data:image/bmp;base64,")
    assert mgr.read_artifact("s1", "book.xlsm")["kind"] == "sheet"

    archive = mgr.read_artifact("s1", "backup.zip")
    assert archive["ok"] is False
    assert archive["error"] == "This is not a text file, and there is no viewer here for .zip files."
