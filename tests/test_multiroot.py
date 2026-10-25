"""A session's folders: the workspace or scratch folder it starts in, the folders added to it read-only
or read-write, and the three layers that read that one list (the file tools, the permission engine,
and the context the model is told).

The folders are temp directories; the manager's model answers nothing.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import time
from pathlib import Path

import aisuite as ai
import pytest

from harness.config.roots import RootDir, normalize_roots, render_context
from harness.llm.providers import AssistantTurn
from harness.loop import TurnEngine
from harness.safety.permissions import Mode, PermissionEngine
from harness.tools import ToolRegistry


def _three_folders(tmp_path):
    scratch, ro, rw = tmp_path / "scratch", tmp_path / "ro", tmp_path / "rw"
    for folder in (scratch, ro, rw):
        folder.mkdir()
    (ro / "data.txt").write_text("secret", encoding="utf-8")
    return scratch, ro, rw


def _file_tools(roots):
    return {tool.__name__: tool for tool in ai.toolkits.files(roots=roots)}


def _write_metadata(scratch):
    registry = ToolRegistry()
    registry.register_all(ai.toolkits.files(roots=[{"path": str(scratch), "writable": True}]))
    return registry.get("write_file").metadata


def _engine_over(messages=None, **fields):
    return TurnEngine(provider=object(), registry=ToolRegistry(), permissions=PermissionEngine(workspace_root="/tmp"), model="x", messages=messages, **fields)


def _manager(tmp_path):
    from harness.interface.server import SessionManager
    from harness.llm.providers import ModelCapabilities, ProviderClient

    class _Silent(ProviderClient):
        def complete(self, *, model, messages, tools=None, **settings):
            return AssistantTurn(text="", finish_reason="stop")

        def capabilities(self, model):
            return ModelCapabilities()

    manager = SessionManager(data_dir=tmp_path / "data", provider=_Silent())
    manager._prefs["scratch_base"] = str(tmp_path / "scratchbase")
    return manager


def _repo(tmp_path, name="repo"):
    folder = tmp_path / name
    folder.mkdir()
    (folder / "README.md").write_text("hello", encoding="utf-8")
    return folder


def _one_turn(engine) -> None:
    """A turn, as a person's message starts one: what makes the session's scratch folder."""

    async def go():
        async for _ in engine.run("hi"):
            pass

    asyncio.run(go())


def _paths(manager, session_id):
    return [Path(row["path"]) for row in manager.get_roots(session_id)]


# -- the file tools ------------------------------------------------------------------------------


def test_the_file_tools_read_every_folder_and_write_only_the_writable_ones(tmp_path):
    scratch, ro, rw = _three_folders(tmp_path)
    tools = _file_tools([{"path": str(scratch), "writable": True}, {"path": str(ro), "writable": False}, {"path": str(rw), "writable": True}])
    assert tools["write_file"]("note.txt", "hi") == "note.txt"  # a relative path is in the first folder
    assert (scratch / "note.txt").read_text() == "hi"
    assert tools["read_file"](str(ro / "data.txt")) == "secret"
    tools["write_file"](str(rw / "added.txt"), "x")
    assert (rw / "added.txt").read_text() == "x"
    with pytest.raises(PermissionError):
        tools["write_file"](str(ro / "nope.txt"), "x")
    with pytest.raises(PermissionError):
        tools["read_file"]("/etc/hosts")


def test_a_folder_added_to_the_shared_list_is_seen_by_the_tools_at_once(tmp_path):
    scratch, ro, _ = _three_folders(tmp_path)
    shared = normalize_roots([RootDir(path=scratch, writable=True)])
    tools = _file_tools(shared)
    with pytest.raises(PermissionError):
        tools["read_file"](str(ro / "data.txt"))
    shared.append(RootDir(path=ro, writable=False))
    assert tools["read_file"](str(ro / "data.txt")) == "secret"


# -- the permission engine -----------------------------------------------------------------------


def test_a_write_into_a_read_only_folder_is_refused_and_one_into_the_scratch_asks(tmp_path):
    """The read-only mark is the person's: refused outright, with no card."""
    scratch, ro, _ = _three_folders(tmp_path)
    metadata = _write_metadata(scratch)
    engine = PermissionEngine(workspace_root=scratch, roots=[{"path": str(scratch), "writable": True}, {"path": str(ro), "writable": False}])
    refused = engine.evaluate("write_file", {"path": str(ro / "x.txt"), "content": "x"}, metadata)
    assert not refused.allowed and not refused.needs_user
    asked = engine.evaluate("write_file", {"path": "x.txt", "content": "x"}, metadata)
    assert not asked.allowed and asked.needs_user


def test_the_read_only_mark_outranks_the_preset_but_a_nested_root_reopens_it(tmp_path):
    """Two reach questions are apart. A path outside EVERY root is an ordinary
    ask the preset decides; an explicitly non-writable root is the person's constraint and
    denies even in full access — unless an inner folder was granted writable again, which
    wins the way a nested mount would."""
    scratch, ro, _ = _three_folders(tmp_path)
    inner = ro / "inner"
    inner.mkdir()
    metadata = _write_metadata(scratch)
    roots = [{"path": str(scratch), "writable": True}, {"path": str(ro), "writable": False}]
    engine = PermissionEngine(workspace_root=scratch, mode=Mode.DANGER_FULL_ACCESS, roots=list(roots))
    denied = engine.evaluate("write_file", {"path": str(ro / "x.txt"), "content": "x"}, metadata)
    assert not denied.allowed and not denied.needs_user and "read-only folder" in denied.reason
    # Outside every root is NOT this case: full access runs it.
    assert engine.evaluate("write_file", {"path": str(tmp_path / "elsewhere.txt"), "content": "x"}, metadata).allowed
    # The nested writable root wins over the enclosing read-only mark.
    nested = PermissionEngine(workspace_root=scratch, mode=Mode.DANGER_FULL_ACCESS, roots=[*roots, {"path": str(inner), "writable": True}])
    assert nested.evaluate("write_file", {"path": str(inner / "ok.txt"), "content": "x"}, metadata).allowed
    assert not nested.evaluate("write_file", {"path": str(ro / "x.txt"), "content": "x"}, metadata).allowed


# -- what the model is told ----------------------------------------------------------------------


def test_the_folders_are_told_with_their_access_and_the_first_one_marked(tmp_path):
    scratch, ro, _ = _three_folders(tmp_path)
    text = render_context(normalize_roots([RootDir(path=scratch, writable=True), RootDir(path=ro, writable=False)]))
    assert "read-write" in text and "read-only" in text and "primary" in text.lower()
    assert str(scratch.resolve()) in text


def test_the_live_block_is_written_once_onto_the_turns_message_and_only_into_what_is_sent():
    """Fixed at the turn's start and not decided again at each render, which keeps the prefix cacheable."""
    engine = _engine_over(
        messages=[{"role": "system", "content": "sys"}, {"role": "user", "content": "hello"}, {"role": "assistant", "content": "hi"}, {"role": "user", "content": "do it"}],
        context_provider=lambda: "DIRS",
    )
    assert engine._outbound_messages()[-1]["content"] == "do it"  # a render alone writes nothing
    engine._stamp_live_context(engine.messages[-1])
    sent = engine._outbound_messages()
    assert engine.messages[-1]["content"] == "do it"
    assert sent[-1]["content"] == "do it\n\n<system-context>\nDIRS\n</system-context>"
    assert sent[1]["content"] == "hello"


def test_without_a_context_provider_nothing_is_added():
    engine = _engine_over(messages=[{"role": "user", "content": "hello"}])
    sent = engine._outbound_messages()
    assert sent == engine.messages and sent[-1]["content"] == "hello"  # a copy, equal in value


def test_a_folder_inside_a_writable_root_is_listed_by_its_real_access(tmp_path):
    repo, inner, ro, ro_inner, rw_in_ro = (tmp_path / "repo", tmp_path / "repo" / "frontend", tmp_path / "ro", tmp_path / "ro" / "sub", tmp_path / "ro" / "open")
    for folder in (inner, ro_inner, rw_in_ro):
        folder.mkdir(parents=True)
    text = render_context(normalize_roots([
        {"path": str(repo), "writable": True},
        {"path": str(inner), "writable": False},
        {"path": str(ro), "writable": False},
        {"path": str(ro_inner), "writable": False},
        {"path": str(rw_in_ro), "writable": True},
    ]))
    assert str(inner.resolve()) not in text  # writable through repo: nothing to add
    assert str(ro_inner.resolve()) not in text  # read-only like its parent
    assert f"{ro.resolve()} [read-only]" in text
    assert f"{rw_in_ro.resolve()} [read-write]" in text  # re-opens a read-only folder


def test_a_reached_folder_is_told_to_the_model_as_outside_not_read_only(tmp_path):
    # Who is asked about a write there is the permissions line's to say: "writes need approval"
    # was false in full access, which writes outside the folders unasked.
    text = render_context([
        RootDir(path=tmp_path / "ws", writable=True),
        RootDir(path=tmp_path / "taken", writable=False, reached=True),
        RootDir(path=tmp_path / "kept", writable=False),
    ])
    assert f"{(tmp_path / 'taken').resolve()} [read; a write here is outside these directories]" in text
    assert f"{(tmp_path / 'kept').resolve()} [read-only]" in text


# -- adding and removing folders -----------------------------------------------------------------


def test_added_folders_reach_the_live_engine_and_the_writable_one_survives_a_restart(tmp_path):
    manager = _manager(tmp_path)
    ro, rw = tmp_path / "shared_ro", tmp_path / "shared_rw"
    ro.mkdir()
    rw.mkdir()
    sid = "sessC"
    engine = manager.get_engine(sid, agent="reeve")
    [scratch] = manager.get_roots(sid)
    assert scratch["primary"] and scratch["writable"]

    manager.add_root(sid, str(ro), writable=False)
    manager.add_root(sid, str(rw), writable=True)
    # The person's folders; the harness's own memory folder rides beside them, never
    # listed to them and never saved as theirs. It is the one folder of its kind.
    assert {r.path for r in engine.roots if not getattr(r, "managed", False)} == {Path(scratch["path"]).resolve(), ro.resolve(), rw.resolve()}
    managed = [r for r in engine.roots if getattr(r, "managed", False)]
    assert sorted(r.label for r in managed) == ["memory"]
    for memory in managed:
        assert str(memory.path) not in {r["path"] for r in manager.get_roots(sid)}
        assert str(memory.path) not in {r["path"] for r in manager._extra_roots_of(engine, sid)}
    listed = {r["path"]: r["writable"] for r in manager.get_roots(sid)}
    assert listed[str(ro.resolve())] is False and listed[str(rw.resolve())] is True
    boundary = manager.get_engine(sid).permissions.boundary
    assert boundary.under_writable_root(str(rw / "x.txt")) and not boundary.under_writable_root(str(ro / "x.txt"))

    assert not manager.remove_root(sid, scratch["path"])["ok"]  # the first folder stays
    manager.remove_root(sid, str(ro))
    assert ro.resolve() not in {r.path for r in engine.roots}
    manager.save(sid, engine)
    reopened = {r["path"]: r for r in _manager(tmp_path).get_roots(sid)}
    assert reopened[str(rw.resolve())]["writable"] is True and str(ro.resolve()) not in reopened


def test_a_folder_added_before_the_first_message_is_kept(tmp_path):
    """The start panel adds a folder before there is a record or an engine; a minimal record is
    made so the grant is not lost."""
    manager = _manager(tmp_path)
    shared = tmp_path / "shared"
    shared.mkdir()
    sid = "fresh-session"
    assert manager.session_store.load(sid) is None
    added = manager.add_root(sid, str(shared), writable=False)
    assert added["ok"] is True and str(shared.resolve()) in {r["path"] for r in added["roots"]}
    reopened = _manager(tmp_path)
    assert str(shared.resolve()) in {r["path"] for r in reopened.get_roots(sid)}
    record = reopened.session_store.load(sid)
    assert record is not None and record.agent == "reeve"


def test_a_granted_folder_is_readable_and_searchable_at_once(tmp_path):
    """A folder granted after the engine was built is visible to the file tools on the
    very next call — the same live roots list the shell's REEVE_ROOTS reads.

    Session 435e0be4: a folder granted mid-session (D:\\Project\\reeve) was read by `run_shell`
    could read it in the same turn, while `read_file` still said "path escapes the
    session's directories" and `grep` said "path escapes the workspace". The file tools
    had copied the roots list at build time, so the grant never reached them.
    """
    manager = _manager(tmp_path)
    repo = _repo(tmp_path)
    granted = tmp_path / "granted"
    granted.mkdir()
    (granted / "note.txt").write_text("hello from the grant", encoding="utf-8")
    sid = "sessGrantedFiles"
    engine = manager.get_engine(sid, agent="reeve", workspace=str(repo))
    # Before the grant the file is out of scope, and the refusal NAMES the valid dirs.
    before = engine.registry.execute("read_file", {"path": str(granted / "note.txt")})
    assert "outside the session's folders" in before["error"] and str(repo.resolve()) in before["error"]
    manager.add_root(sid, str(granted), writable=False)
    assert "hello from the grant" in engine.registry.execute("read_file", {"path": str(granted / "note.txt")})["content"]
    found = engine.registry.execute("grep", {"pattern": "hello", "path": str(granted)})
    assert "error" not in found and found["count"] >= 1


def test_a_reached_folder_is_kept_as_reached_folds_its_inner_folders_and_a_persons_add_owns_it(tmp_path):
    """The session's own add (`reached`) persists as such, only widens, and swallows the
    folders it took earlier inside the same project; the person adding the folder makes it theirs."""
    manager = _manager(tmp_path)
    project = tmp_path / "wealthlog"
    (project / "frontend").mkdir(parents=True)
    (project / "backend").mkdir()
    sid = "sessR"
    engine = manager.get_engine(sid, agent="reeve")
    for folder in (project / "frontend", project / "backend", project):
        manager.add_root(sid, str(folder), writable=False, reached=True)
    rows = [r for r in manager.get_roots(sid) if not r["primary"]]
    assert [(Path(r["path"]), r["writable"], r.get("reached")) for r in rows] == [(project.resolve(), False, True)]
    manager.add_root(sid, str(project), writable=True, reached=True)
    manager.add_root(sid, str(project), writable=False, reached=True)  # a later read never narrows it
    manager.save(sid, engine)
    cold = {r["path"]: r for r in _manager(tmp_path).get_roots(sid)}
    assert cold[str(project.resolve())]["writable"] is True and cold[str(project.resolve())]["reached"] is True
    manager.add_root(sid, str(project), writable=False)
    [mine] = [r for r in engine.roots if r.path == project.resolve()]
    assert not mine.writable and not mine.reached
    assert engine.permissions.boundary.in_read_only_root(str(project / "a.txt"))


def test_a_grant_inside_the_sessions_own_project_says_nothing_about_that_project(tmp_path):
    """Session b3a8491e-9db was told "“wealthlog” already has project memory — bind it by
    name or start a session there" inside its own wealthlog session. The pointer is for
    ANOTHER project's memory; for the one the session is already on it is noise."""
    from harness.session.projects import project_key

    def git_repo(name):
        folder = tmp_path / name
        (folder / "backend").mkdir(parents=True)
        (folder / "frontend").mkdir()
        subprocess.run(["git", "init", "-q"], cwd=folder, check=True, capture_output=True)
        return folder

    manager = _manager(tmp_path)
    own, other = git_repo("wealth"), git_repo("other")
    for repo in (own, other):
        manager._auto_memory_for(project_key(repo)).save_memory(title="F", description="f", note_type="project", body="f")
    sid = "sessOwnProject"
    assert manager.get_engine(sid, agent="reeve", workspace=str(own / "backend")) is not None
    assert manager.add_root(sid, str(own / "frontend"), writable=True)["notice"] is None
    notice = manager.add_root(sid, str(other), writable=False)["notice"]
    assert notice and "other" in notice


# -- the scratch folder --------------------------------------------------------------------------


def test_a_session_on_a_project_has_the_project_first_and_its_scratch_after(tmp_path):
    manager = _manager(tmp_path)
    repo = _repo(tmp_path)
    sid = "sessGated1"
    engine = manager.get_engine(sid, agent="reeve", workspace=str(repo))
    project, scratch_row = manager.get_roots(sid)
    scratch = manager.scratch_base() / sid
    assert project["primary"] and Path(project["path"]) == repo.resolve()
    assert Path(scratch_row["path"]) == scratch.resolve() and scratch_row["writable"] is True
    # Named and not made until the first message (2026-10-18), and not made is not "not found".
    assert not scratch.exists() and scratch_row["exists"] is True
    _one_turn(engine)
    assert scratch.is_dir()
    boundary = engine.permissions.boundary
    assert boundary.under_writable_root(str(repo / "x.txt")) and boundary.under_writable_root(str(scratch / "x.txt"))
    assert not boundary.under_writable_root(str(tmp_path / "elsewhere.txt"))


def test_a_session_without_a_project_has_its_scratch_once_and_first(tmp_path):
    manager = _manager(tmp_path)
    sid = "sessOrphan1"
    assert manager.get_engine(sid, agent="reeve") is not None
    [row] = manager.get_roots(sid)
    assert row["primary"] and row["writable"] and Path(row["path"]) == (manager.scratch_base() / sid).resolve()


def test_the_files_a_session_made_are_its_scratchs_not_its_projects(tmp_path):
    manager = _manager(tmp_path)
    repo = _repo(tmp_path)
    sid = "sessGated2"
    _one_turn(manager.get_engine(sid, agent="reeve", workspace=str(repo)))
    (manager.scratch_base() / sid / "report.html").write_text("<h1>posture</h1>", encoding="utf-8")
    names = {artifact["name"] for artifact in manager.list_artifacts(sid)}
    assert "report.html" in names and "README.md" not in names


def test_a_file_link_opens_from_the_project_or_the_scratch_and_nowhere_else(tmp_path):
    manager = _manager(tmp_path)
    repo = _repo(tmp_path)
    sid = "sessGated3"
    engine = manager.get_engine(sid, agent="reeve", workspace=str(repo))
    _one_turn(engine)
    manager.save(sid, engine)  # links are read against the saved record, as after a turn
    scratch = manager.scratch_base() / sid
    (scratch / "report.html").write_text("<h1>ok</h1>", encoding="utf-8")
    assert manager.read_artifact(sid, "README.md")["ok"] is True
    assert manager.read_artifact(sid, "report.html")["ok"] is True  # only in the scratch
    assert manager.read_artifact(sid, str(scratch / "report.html"))["ok"] is True  # as the context names it
    outside = tmp_path / "outside.txt"
    outside.write_text("x", encoding="utf-8")
    assert manager.read_artifact(sid, str(outside))["ok"] is False


def test_saving_and_rebuilding_never_adds_the_scratch_again_as_a_folder(tmp_path):
    """It used to be saved as an added folder, and each rebuild listed it once more."""
    manager = _manager(tmp_path)
    repo = _repo(tmp_path)
    sid = "sessGated5"
    manager.save(sid, manager.get_engine(sid, agent="reeve", workspace=str(repo)))
    second = _manager(tmp_path)
    second.save(sid, second.get_engine(sid))
    third = _manager(tmp_path)
    assert _paths(third, sid) == [repo.resolve(), (third.scratch_base() / sid).resolve()]


def test_a_conversation_opened_and_never_sent_leaves_no_folder(tmp_path):
    """The GUI builds an engine the moment a conversation opens. Building it named the scratch
    folder and made it, so every conversation opened and never sent left one behind (879 folders
    with no session in one person's scratch area, 2026-10-18). The first turn makes it."""
    manager = _manager(tmp_path)
    sid = "sessDraft1"
    engine = manager.get_engine(sid, agent="reeve")
    [row] = manager.get_roots(sid)
    scratch = manager.scratch_base() / sid
    assert Path(row["path"]) == scratch.resolve() and row["exists"] is True and not scratch.exists()
    _one_turn(engine)
    assert scratch.is_dir()


def test_the_startup_sweep_removes_only_empty_folders_no_conversation_owns(tmp_path):
    from harness.session.sessions import SessionRecord

    manager = _manager(tmp_path)
    base = manager.scratch_base()
    base.mkdir(parents=True, exist_ok=True)
    two_days_ago = time.time() - 2 * 86_400

    def folder(name: str, *, old: bool = True, file: bool = False) -> Path:
        path = base / name
        path.mkdir()
        if file:
            (path / "notes.md").write_text("kept", encoding="utf-8")
        if old:
            os.utime(path, (two_days_ago, two_days_ago))
        return path

    unused = folder("aaaa11112222")
    kept = [folder("bbbb11112222", file=True), folder("cccc11112222", old=False), folder("my folder")]
    owned = folder("dddd11112222")
    manager.session_store.save(SessionRecord(session_id="dddd11112222", workspace=str(owned), model="m", mode="workspace-write", messages=[]))
    live = folder("eeee11112222")
    manager.get_engine("eeee11112222", agent="reeve")
    assert manager.sweep_unused_scratch() == 1
    assert not unused.exists()
    for still in (*kept, owned, live):
        assert still.is_dir(), still.name


def test_deleting_a_session_on_a_real_folder_removes_its_scratch_beside_it(tmp_path):
    manager = _manager(tmp_path)
    repo = _repo(tmp_path)
    sid = "sessGated9"
    engine = manager.get_engine(sid, agent="reeve", workspace=str(repo))
    _one_turn(engine)
    manager.save(sid, engine)
    scratch = manager.scratch_base() / sid
    (scratch / "report.html").write_text("<h1>x</h1>", encoding="utf-8")
    assert manager.delete_session(sid)["ok"]
    assert not scratch.exists() and (repo / "README.md").is_file()  # the person's folder is never touched


# -- giving a scratch session a project ----------------------------------------------------------


def test_a_scratch_session_given_a_project_puts_it_first_and_keeps_the_scratch(tmp_path):
    manager = _manager(tmp_path)
    repo = _repo(tmp_path, "project")
    sid = "sessPromo1"
    engine = manager.get_engine(sid, agent="reeve")
    scratch = (manager.scratch_base() / sid).resolve()
    assert manager.promote_workspace(sid, str(repo))["ok"]
    assert [r.path for r in engine.roots][:2] == [repo.resolve(), scratch]
    assert engine.roots[0].writable and engine.roots[0].label == "workspace"
    assert Path(manager.session_store.load(sid).workspace).resolve() == repo.resolve()
    # Rebuilt after the turn (marking it idle drops the engine), it is a project session.
    manager.mark_idle(sid)
    assert manager.get_engine(sid) is not engine
    rows = manager.get_roots(sid)
    assert [Path(r["path"]) for r in rows] == [repo.resolve(), scratch]
    assert rows[0]["primary"] and rows[0]["label"] == "workspace"


@pytest.mark.parametrize("starts_on_a_project", [True, False])
def test_a_session_that_already_has_a_project_is_not_given_another(tmp_path, starts_on_a_project):
    manager = _manager(tmp_path)
    first, second = _repo(tmp_path, "first"), _repo(tmp_path, "second")
    sid = "sessPromo"
    if starts_on_a_project:
        assert manager.get_engine(sid, agent="reeve", workspace=str(first)) is not None
    else:
        assert manager.get_engine(sid, agent="reeve") is not None
        assert manager.promote_workspace(sid, str(first))["ok"]
    refused = manager.promote_workspace(sid, str(second))
    assert not refused["ok"] and "already has a workspace" in refused["error"]


# -- the workspace is the primary root, not the shell's cwd ------------------------


def _session_in_a_repo(tmp_path, sid):
    """A manager session opened on a repo with backend/ and frontend/, whose shell has
    then `cd`-ed into backend/ — what shell.py does with the cwd each command reports."""
    manager = _manager(tmp_path)
    repo = _repo(tmp_path, "wealth")
    (repo / "backend").mkdir()
    (repo / "frontend").mkdir()
    engine = manager.get_engine(sid, agent="reeve", workspace=str(repo))
    engine.executor.cwd = str((repo / "backend").resolve())
    return manager, engine, repo


def test_a_cd_in_the_shell_does_not_move_the_session(tmp_path):
    """Session b3a8491e-9db was opened on wealthlog; after `cd backend` the save wrote
    backend as the workspace, and the rebuilt engine put frontend/ outside it."""
    manager, engine, repo = _session_in_a_repo(tmp_path, "sessCd1")
    manager.save("sessCd1", engine)
    assert Path(manager.session_store.load("sessCd1").workspace) == repo.resolve()
    manager._engines.pop("sessCd1")
    rebuilt = manager.get_engine("sessCd1", agent="reeve")
    assert rebuilt.permissions.boundary.under_writable_root(str(repo / "frontend" / "x.tsx"))


def test_the_workspace_chip_shows_the_session_folder_not_the_shell_cwd(tmp_path):
    from fastapi.testclient import TestClient

    from harness.interface.server import create_app

    manager, _engine, repo = _session_in_a_repo(tmp_path, "sessCd2")
    with TestClient(create_app(manager)).websocket_connect("/ws/session/sessCd2?agent=reeve") as ws:
        ready = ws.receive_json()
    assert ready["type"] == "ready" and Path(ready["data"]["workspace"]) == repo.resolve()


def test_the_ready_frame_says_where_the_session_compacts(tmp_path):
    """The context gauge has its real denominator from the moment a session opens, not
    only after the next answer (the per-message `compact_at`)."""
    from fastapi.testclient import TestClient

    from harness.interface.server import create_app

    manager, engine, _repo_dir = _session_in_a_repo(tmp_path, "sessCompactAt")
    with TestClient(create_app(manager)).websocket_connect("/ws/session/sessCompactAt?agent=reeve") as ws:
        ready = ws.receive_json()
    assert ready["type"] == "ready"
    assert ready["data"]["compact_at"] == engine._history_trigger_tokens() > 0
