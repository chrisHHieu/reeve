"""Taking back a write the agent made (harness/tools/undo.py).

Approval asks a person to PREDICT: read `apply_patch`, imagine the result, decide. Someone
who does not write software cannot do that, so they approve everything and the gate
protects nobody. Undo asks them to RECOGNISE — look at what happened, say no — which
anyone can do. That is the whole argument for this file: it makes full capability safe
without removing any capability.

The prior content has to be captured BEFORE the tool runs, because afterwards it exists
nowhere: `provenance.SessionFiles` records that the agent touched a path and with what
origin, never what was there first. An earlier version of the plan claimed this came
almost free with the session log; it does not. `surfaceOp`-style shadowing hides
CONVERSATION nodes and restores no bytes on disk.
"""

from __future__ import annotations

import asyncio

import pytest

from harness.config.roots import RootDir
from harness.tools.pipeline import ToolExecution, ToolPipeline, ToolResult
from harness.tools.undo import UndoStore, snapshot_listener


def _store(tmp_path, **kwargs):
    return UndoStore(tmp_path / "undo", **kwargs)


# -- the capture ------------------------------------------------------------------


def test_a_file_is_kept_as_it_stood_before_the_write(tmp_path):
    target = tmp_path / "config.py"
    target.write_text("PORT = 8000", encoding="utf-8")
    store = _store(tmp_path)

    store.snapshot("call-1", [str(target)])
    target.write_text("PORT = 9999", encoding="utf-8")  # the agent's write

    assert store.restore("call-1")["ok"] is True
    assert target.read_text(encoding="utf-8") == "PORT = 8000"


def test_undoing_a_CREATE_deletes_the_file(tmp_path):
    """A file that did not exist is not an empty file. Writing zero bytes back would
    leave debris that looks like the user's own work — and this is the case a person is
    most likely to want undone, because a file that appeared is the visible one."""
    target = tmp_path / "new.py"
    store = _store(tmp_path)

    store.snapshot("call-1", [str(target)])  # nothing there yet
    target.write_text("print('hi')", encoding="utf-8")

    assert store.restore("call-1")["ok"] is True
    assert not target.exists()


def test_a_call_that_touched_several_files_comes_back_whole(tmp_path):
    a, b = tmp_path / "a.py", tmp_path / "b.py"
    a.write_text("A", encoding="utf-8")
    b.write_text("B", encoding="utf-8")
    store = _store(tmp_path)

    store.snapshot("patch-1", [str(a), str(b)])
    a.write_text("A2", encoding="utf-8")
    b.write_text("B2", encoding="utf-8")

    assert store.restore("patch-1")["restored"] == [str(a), str(b)]
    assert (a.read_text(), b.read_text()) == ("A", "B")


def test_an_undo_is_consumed_once_it_is_used(tmp_path):
    """Offering the same undo twice would put stale content over whatever came after."""
    target = tmp_path / "a.py"
    target.write_text("first", encoding="utf-8")
    store = _store(tmp_path)
    store.snapshot("call-1", [str(target)])

    target.write_text("second", encoding="utf-8")
    store.restore("call-1")
    target.write_text("third", encoding="utf-8")

    assert store.undoable("call-1") is False
    assert store.restore("call-1")["ok"] is False
    assert target.read_text(encoding="utf-8") == "third"


# -- what is deliberately NOT captured ---------------------------------------------


def test_a_relative_path_hangs_off_the_workspace(tmp_path):
    """THE case, not an edge case: a model writes `notes.txt`, never an absolute path.

    This test used to assert the opposite — that a relative path is refused — and it
    passed, so undo shipped doing nothing at all for ordinary writes. The reasoning was
    sound ("resolving against this process's cwd would snapshot the wrong file") and the
    conclusion was wrong, because the missing third option was to resolve against the
    workspace, which `build_engine` has always had. One real turn caught it; every test
    here had handed it `tmp_path / "..."`.
    """
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "config.py").write_text("original", encoding="utf-8")
    store = _store(tmp_path, base=workspace)

    taken = store.snapshot("call-1", ["config.py"])
    assert len(taken) == 1 and taken[0].existed is True
    (workspace / "config.py").write_text("clobbered", encoding="utf-8")

    assert store.undoable("call-1") is True
    store.restore("call-1")
    assert (workspace / "config.py").read_text(encoding="utf-8") == "original"


def test_without_a_workspace_a_relative_path_is_still_refused(tmp_path):
    """The fallback stays honest. With nothing to anchor to, resolving would mean this
    process's cwd — and a snapshot of the wrong file is worse than none."""
    store = _store(tmp_path)  # no base
    assert store.snapshot("call-1", ["config.py"]) == []
    assert store.undoable("call-1") is False


def test_a_file_too_large_to_copy_is_not_made_undoable(tmp_path):
    """A source file is kilobytes. Spending a hundred megabytes to make one write
    reversible is a trade nobody asked for — and the step says it is not undoable rather
    than pretending."""
    target = tmp_path / "big.bin"
    target.write_bytes(b"x" * 5000)
    store = _store(tmp_path, max_file_bytes=1000)

    assert store.snapshot("call-1", [str(target)]) == []
    assert store.undoable("call-1") is False


def test_a_directory_is_not_a_file(tmp_path):
    store = _store(tmp_path)
    (tmp_path / "sub").mkdir()
    assert store.snapshot("call-1", [str(tmp_path / "sub")]) == []


def test_a_path_that_could_never_exist_is_not_offered(tmp_path):
    """`a/b.txt/c.py` is not a path any tool can create, so recording it as an undoable
    CREATE would offer a deletion that does nothing when taken up. Caught by this test:
    `not exists()` is true for impossible paths as well as absent ones."""
    store = _store(tmp_path)
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("i am a file")

    assert store.snapshot("call-1", [str(blocker / "deep" / "x.py")]) == []
    assert store.undoable("call-1") is False


def test_a_create_under_a_missing_directory_still_counts(tmp_path):
    """Write tools make parent directories, so the path is genuinely creatable and
    genuinely worth being able to delete again."""
    store = _store(tmp_path)
    target = tmp_path / "new" / "deep" / "x.py"

    (shot,) = store.snapshot("call-1", [str(target)])
    assert shot.existed is False

    target.parent.mkdir(parents=True)
    target.write_text("created", encoding="utf-8")
    assert store.restore("call-1")["ok"] is True
    assert not target.exists()


# -- bounds and durability ---------------------------------------------------------


def test_only_the_recent_writes_stay_undoable(tmp_path):
    store = _store(tmp_path, keep=3)
    for i in range(6):
        target = tmp_path / f"f{i}.py"
        target.write_text(str(i), encoding="utf-8")
        store.snapshot(f"call-{i}", [str(target)])

    assert [store.undoable(f"call-{i}") for i in range(6)] == [
        False, False, False, True, True, True
    ]


def test_an_offer_is_withdrawn_once_its_copy_is_gone(tmp_path):
    """Pruning is bounded, so an index entry outlives its file. An offer to undo
    something that cannot be undone is worse than no offer."""
    target = tmp_path / "a.py"
    target.write_text("A", encoding="utf-8")
    store = _store(tmp_path)
    (shot,) = store.snapshot("call-1", [str(target)])

    assert store.undoable("call-1") is True
    from pathlib import Path

    Path(shot.saved_to).unlink()
    assert store.undoable("call-1") is False


def test_undo_survives_a_restart(tmp_path):
    """The index is written to disk. A user who closes the app and reopens it has not
    changed their mind about the file the agent overwrote."""
    target = tmp_path / "a.py"
    target.write_text("original", encoding="utf-8")
    _store(tmp_path).snapshot("call-1", [str(target)])
    target.write_text("changed", encoding="utf-8")

    reopened = _store(tmp_path)  # a fresh process would build it the same way
    assert reopened.undoable("call-1") is True
    assert reopened.restore("call-1")["ok"] is True
    assert target.read_text(encoding="utf-8") == "original"


def test_the_store_hides_itself_from_git(tmp_path):
    store = UndoStore(tmp_path / "ws" / ".reeve" / "undo")
    (tmp_path / "a.py").write_text("A", encoding="utf-8")
    store.snapshot("call-1", [str(tmp_path / "a.py")])
    assert (tmp_path / "ws" / ".reeve" / ".gitignore").read_text() == "*\n"


def test_the_store_avoids_scratch(tmp_path):
    """A spill is disposable; a before-image is the only copy of the user's file. Putting
    it on a scratch volume turns undo into a cross-device copy that can fail exactly when
    it matters."""
    roots = [
        RootDir(path=tmp_path / "scratch", writable=True, label="scratch"),
        RootDir(path=tmp_path / "project", writable=True),
    ]
    store = UndoStore.under(roots)
    assert (tmp_path / "project").resolve() in store.directory.parents


def test_a_session_with_nowhere_to_write_gets_no_store(tmp_path):
    assert UndoStore.under([RootDir(path=tmp_path, writable=False)]) is None


@pytest.mark.parametrize("bad", [{"keep": 0}, {"max_bytes": 0}, {"max_file_bytes": 0}])
def test_nonsense_bounds_are_refused(tmp_path, bad):
    with pytest.raises(ValueError):
        UndoStore(tmp_path, **bad)


# -- the pipeline listener ----------------------------------------------------------


def _run(pipeline, execution, result=ToolResult(value="done")):
    async def go():
        return await pipeline.dispatch(execution, lambda: _ready(result))

    return asyncio.run(go())


async def _ready(result):
    return result


def test_the_listener_captures_before_the_tool_runs(tmp_path):
    target = tmp_path / "a.py"
    target.write_text("before", encoding="utf-8")
    store = _store(tmp_path)
    pipeline = ToolPipeline()
    pipeline.on_pre_execute(
        snapshot_listener(store, lambda name, args: ([args["path"]], True))
    )

    async def write():
        target.write_text("after", encoding="utf-8")
        return ToolResult(value="ok")

    async def go():
        return await pipeline.dispatch(
            ToolExecution(name="write_file", arguments={"path": str(target)}, call_id="c1"),
            write,
        )

    asyncio.run(go())
    assert store.restore("c1")["ok"] is True
    assert target.read_text(encoding="utf-8") == "before"


def test_a_failing_snapshot_never_blocks_the_write(tmp_path):
    """`ToolPipeline._run_pre` fails CLOSED — a listener that raises blocks the call,
    because a gate that crashed cannot be read as consent. This listener is not a gate,
    and must not inherit that: a backup that failed is no reason to refuse the work."""
    store = _store(tmp_path)
    pipeline = ToolPipeline()

    def exploding(name, args):
        raise RuntimeError("locate blew up")

    pipeline.on_pre_execute(snapshot_listener(store, exploding))
    outcome = _run(
        pipeline, ToolExecution(name="write_file", arguments={}, call_id="c1")
    )
    assert outcome.ok is True
    assert outcome.value == "done"


def test_a_tool_with_no_locatable_path_is_left_alone(tmp_path):
    store = _store(tmp_path)
    pipeline = ToolPipeline()
    pipeline.on_pre_execute(snapshot_listener(store, lambda name, args: ([], False)))
    outcome = _run(pipeline, ToolExecution(name="run_shell", arguments={}, call_id="c1"))
    assert outcome.ok is True
    assert store.undoable("c1") is False


def test_the_store_names_every_call_it_can_still_take_back(tmp_path):
    """A reopened session's rows are rebuilt from the transcript, which never recorded
    `undoable` — so the Review panel said "Undo isn't available" for edits the turn's own
    receipt could still put back. The store answers for itself, from the disk."""
    a, b = tmp_path / "a.py", tmp_path / "b.py"
    a.write_text("A", encoding="utf-8")
    b.write_text("B", encoding="utf-8")
    store = _store(tmp_path)
    store.snapshot("call-1", [str(a)])
    store.snapshot("call-2", [str(b)])
    store.snapshot("call-3", [str(tmp_path / "missing-dir" / "x.py")])  # creatable: undo = delete

    reopened = _store(tmp_path)
    assert sorted(reopened.undoable_calls()) == ["call-1", "call-2", "call-3"]
    assert reopened.restore("call-1")["ok"] is True
    assert sorted(reopened.undoable_calls()) == ["call-2", "call-3"]
