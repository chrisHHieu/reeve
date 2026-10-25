"""Review does not draw a screenshot as a diff.

Found from a laggy GUI: an untracked 330 KB PNG came back from `/changes/diff` as 143,000 replacement
characters in 2,700 "lines", three of them in one workspace, drawn as additions and read again every time
the change list was read. What has to hold:

  - a binary untracked file is facts only (`binary`, `size`), never content, in the working tree and in the
    `unstaged` scope alike;
  - a long text file is a preview that ends on a whole line, says it was cut and how big it is;
  - an ordinary text file is exactly what it was, with universal newlines;
  - the change LIST carries what Review needs to keep a heavy card closed (`binary`, `size`, the line count as
    additions) and a `revision` that moves when the file does, so a diff is read again when its file moved and
    not whenever the list was read;
  - looking at untracked files is bounded: a folder of thousands is listed as before.
"""

from __future__ import annotations

import os
import subprocess

from harness.interface.server import SessionManager
from harness.interface.server.manager import untracked
from harness.session.sessions import SessionRecord

PNG = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + bytes(range(256)) * 40  # NULs and 10 KB of noise


def _git(root, *args):
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True)


def _repo(tmp_path):
    tmp_path.mkdir(parents=True, exist_ok=True)
    _git(tmp_path, "init")
    _git(tmp_path, "config", "user.email", "t@example.com")
    _git(tmp_path, "config", "user.name", "Tester")
    (tmp_path / "tracked.txt").write_text("one\n", encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-m", "init")
    return tmp_path


def _manager(tmp_path, workspace):
    manager = SessionManager(workspace=tmp_path, provider=object())
    manager.session_store.save(SessionRecord(session_id="s1", workspace=str(workspace), model="m", mode="interactive"))
    return manager


def test_a_binary_untracked_file_is_facts_and_never_content(tmp_path):
    repo = _repo(tmp_path / "repo")
    (repo / "shot.png").write_bytes(PNG)
    manager = _manager(tmp_path / "state", repo)

    diff = manager.session_change_diff("s1", "shot.png")
    assert diff == {"ok": True, "path": "shot.png", "untracked": True, "binary": True, "size": len(PNG)}
    assert "content" not in diff
    scoped = manager.scope_change_diff("s1", "unstaged", "shot.png")
    assert scoped["binary"] is True and scoped["size"] == len(PNG) and "content" not in scoped


def test_an_ordinary_text_file_is_unchanged_with_universal_newlines(tmp_path):
    repo = _repo(tmp_path / "repo")
    (repo / "fresh.txt").write_bytes(b"one\r\ntwo\r\nthree")
    diff = _manager(tmp_path / "state", repo).session_change_diff("s1", "fresh.txt")
    assert diff["untracked"] is True and diff["content"] == "one\ntwo\nthree"
    assert "binary" not in diff and "truncated" not in diff


def test_a_long_text_file_is_a_preview_that_ends_on_a_whole_line(tmp_path, monkeypatch):
    monkeypatch.setattr(untracked, "MAX_CONTENT_BYTES", 105)  # not a multiple of a line: the cut lands mid-line
    repo = _repo(tmp_path / "repo")
    (repo / "long.log").write_bytes("".join(f"line {i:04d}\n" for i in range(200)).encode())  # LF, whatever the OS
    diff = _manager(tmp_path / "state", repo).session_change_diff("s1", "long.log")
    assert diff["truncated"] is True and diff["size"] == 200 * 10
    assert diff["content"].endswith("\n") and len(diff["content"]) == 100  # ten whole lines, the half line is gone
    assert all(line.startswith("line ") and len(line) == 9 for line in diff["content"].splitlines())


def test_the_list_says_enough_to_keep_a_heavy_card_closed(tmp_path):
    repo = _repo(tmp_path / "repo")
    (repo / "shot.png").write_bytes(PNG)
    (repo / "notes.txt").write_bytes(b"a\nb\nc\n")
    files = {f["path"]: f for f in _manager(tmp_path / "state", repo).session_changes("s1")["files"]}
    assert files["shot.png"]["binary"] is True and files["shot.png"]["size"] == len(PNG) and files["shot.png"]["add"] == 0
    assert "binary" not in files["notes.txt"]
    assert files["notes.txt"]["add"] == 3 and files["notes.txt"]["size"] == 6  # additions are its lines


def test_a_long_untracked_text_file_is_not_mistaken_for_an_empty_change(tmp_path, monkeypatch):
    monkeypatch.setattr(untracked, "MAX_CONTENT_BYTES", 100)
    repo = _repo(tmp_path / "repo")
    (repo / "long.log").write_text("x" * 4000, encoding="utf-8")
    entry = {f["path"]: f for f in _manager(tmp_path / "state", repo).session_changes("s1")["files"]}["long.log"]
    assert entry["add"] == 4000 // 40


def test_the_revision_moves_when_the_file_does_and_not_otherwise(tmp_path):
    repo = _repo(tmp_path / "repo")
    (repo / "notes.txt").write_text("one\n", encoding="utf-8")
    manager = _manager(tmp_path / "state", repo)
    first = {f["path"]: f for f in manager.session_changes("s1")["files"]}
    again = {f["path"]: f for f in manager.session_changes("s1")["files"]}
    assert first["notes.txt"]["revision"] == again["notes.txt"]["revision"]  # a re-read of an unchanged tree

    path = repo / "notes.txt"
    path.write_text("one\ntwo\n", encoding="utf-8")
    os.utime(path, ns=(2_000_000_000_000_000_000, 2_000_000_000_000_000_000))
    moved = {f["path"]: f for f in manager.session_changes("s1")["files"]}
    assert moved["notes.txt"]["revision"] != first["notes.txt"]["revision"]


def test_a_tracked_file_has_a_revision_and_a_deleted_one_does_not(tmp_path):
    repo = _repo(tmp_path / "repo")
    (repo / "tracked.txt").write_text("one\ntwo\n", encoding="utf-8")
    (repo / "gone.txt").write_text("x\n", encoding="utf-8")
    _git(repo, "add", "gone.txt")
    _git(repo, "commit", "-m", "add gone")
    (repo / "gone.txt").unlink()
    files = {f["path"]: f for f in _manager(tmp_path / "state", repo).session_changes("s1")["files"]}
    assert files["tracked.txt"]["revision"] and "binary" not in files["tracked.txt"]
    assert "revision" not in files["gone.txt"] and "size" not in files["gone.txt"]


def test_only_so_many_untracked_files_are_looked_at(tmp_path, monkeypatch):
    monkeypatch.setattr(untracked, "MAX_INSPECTED", 1)
    monkeypatch.setattr("harness.interface.server.manager.reviews.MAX_INSPECTED", 1)
    repo = _repo(tmp_path / "repo")
    for name in ("a.txt", "b.txt", "c.txt"):
        (repo / name).write_text("x\n", encoding="utf-8")
    files = _manager(tmp_path / "state", repo).session_changes("s1")["files"]
    looked = [f for f in files if "revision" in f]
    assert len(looked) == 1 and len(files) == 3  # the rest are listed exactly as before
