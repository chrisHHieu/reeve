"""Recovering the middle of a truncated output (harness/tools/spill.py).

`retain()` made the gap visible. That stopped the model reasoning as if the log were
complete, but it did not give it a way to look inside — the dropped middle was gone, and
the only route back was to run the command again. For a ten-minute build, noticing the
truncation cost ten more minutes.

The model already had an escape hatch it could never reach in time: redirect to a file
and grep it. Truncation is discovered AFTER the process exits, and by then nothing holds
the output. Spilling removes the ordering problem by writing at the moment of the cut.

Three properties are pinned throughout:

- **Nothing is written unless something was dropped.** A sink invoked on output that fit
  is a file nobody will ever open.
- **A spill failure is never a tool failure.** A full disk degrades to exactly the
  truncation that happened before this module existed.
- **The file lands under a root.** Reading outside the session's roots falls out of every
  session grant, so a spill parked elsewhere would raise an approval prompt on the way
  back in — a recovery path that asks permission is not a recovery path.
"""

from __future__ import annotations

import random

import pytest

from harness.config.roots import RootDir
from harness.tools.retention import retain
from harness.tools.spill import SpillStore


def test_a_planted_symlink_at_a_spill_name_is_refused(tmp_path):
    """The spill directory is predictable; a pre-planted link must not redirect the
    write. The exclusive create refuses it, and the store's
    contract turns that refusal into `None` — never a tool failure."""
    from harness.tools import spill as spill_module

    target = tmp_path / "victim.txt"
    target.write_text("original", encoding="utf-8")
    planted = tmp_path / "output-1.log"
    try:
        planted.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are not available on this machine")

    with pytest.raises(OSError):
        spill_module._write_exclusive(planted, "new content")
    assert target.read_text(encoding="utf-8") == "original"


def _lines(n, prefix="line"):
    return "\n".join(f"{prefix}{i}" for i in range(1, n + 1))


class _Recorder:
    """A sink that records rather than writes, so the retainer can be tested alone."""

    def __init__(self, locator="/tmp/spill/shell-1.log"):
        self.locator = locator
        self.calls: list[str] = []

    def __call__(self, text: str):
        self.calls.append(text)
        return self.locator


# -- the retainer's half of the contract -----------------------------------------


def test_output_that_fits_never_touches_the_sink():
    sink = _Recorder()
    assert retain("short", budget=100, spill=sink).text == "short"
    assert sink.calls == []


def test_the_sink_is_handed_the_complete_text_exactly_once():
    text = _lines(1000)
    sink = _Recorder()
    retain(text, budget=200, spill=sink)
    assert sink.calls == [text]  # the whole thing, not the gap: a fragment of a log
    # read back without its head or tail is barely more use than no log at all.


def test_the_marker_names_the_locator_so_the_model_reads_it_in_place():
    kept = retain(_lines(1000), budget=400, spill=_Recorder("/w/.reeve/out/s-1.log"))
    assert "/w/.reeve/out/s-1.log" in kept.text
    assert kept.spilled == "/w/.reeve/out/s-1.log"
    assert kept.text.startswith("line1") and kept.text.rstrip().endswith("line1000")


def test_the_budget_still_bounds_the_string_once_a_path_is_in_the_marker():
    for budget in (50, 120, 200, 400, 1000, 20_000):
        kept = retain(_lines(2000), budget=budget, spill=_Recorder("/x" * 30))
        assert len(kept.text) <= budget


def test_the_budget_holds_across_random_inputs_and_path_lengths():
    """The marker's length now depends on a caller-supplied string as well as on the
    gap, so the split's bound has to survive both moving at once."""
    random.seed(11)
    for _ in range(400):
        text = "".join(random.choice("ab\n é日") for _ in range(random.randint(0, 400)))
        budget = random.randint(0, 300)
        locator = "/p" * random.randint(1, 60)
        kept = retain(
            text,
            budget=budget,
            head_share=random.choice([0.0, 0.35, 0.7, 1.0]),
            spill=_Recorder(locator),
        )
        assert len(kept.text) <= budget


def test_a_path_that_would_crowd_out_the_content_leaves_the_marker():
    """The point of keeping both ends is that they can be read. A locator worth more
    than half the budget has eaten the thing it was meant to make recoverable — so it
    drops out of the text and travels in the result dict instead."""
    long_path = "/very/deep/" + "segment/" * 20 + "shell-1.log"
    kept = retain(_lines(1000), budget=200, spill=_Recorder(long_path))
    assert long_path not in kept.text
    assert kept.spilled == long_path  # still recoverable, just not from inside the text
    assert kept.text.startswith("line1")


def test_a_sink_that_fails_degrades_to_plain_truncation():
    def broken(_text):
        raise OSError("no space left on device")

    kept = retain(_lines(1000), budget=200, spill=broken)
    assert kept.truncated is True
    assert kept.spilled is None
    assert "complete output is at" not in kept.text
    assert len(kept.text) <= 200


def test_a_sink_that_declines_reports_no_locator():
    kept = retain(_lines(1000), budget=200, spill=lambda _t: None)
    assert kept.spilled is None
    assert "complete output is at" not in kept.text


# -- the store --------------------------------------------------------------------


def test_a_spill_round_trips_byte_for_byte(tmp_path):
    store = SpillStore(tmp_path / "out")
    text = "日本語\n" + _lines(500) + "\ntrailing é"
    path = store.save(text, label="shell")
    assert path is not None
    from pathlib import Path

    assert Path(path).read_text(encoding="utf-8") == text


def test_the_store_hides_itself_from_git(tmp_path):
    """It usually lands inside the user's own project. A tool that adds untracked files
    to someone's `git status` every time a build log runs long has made itself their
    problem."""
    store = SpillStore(tmp_path / "ws" / ".reeve" / "output")
    store.save("x", label="shell")
    assert (tmp_path / "ws" / ".reeve" / ".gitignore").read_text() == "*\n"


def test_a_real_git_status_stays_clean(tmp_path):
    """The ignore file is only worth writing if git actually honours it. Asserting the
    file exists proves nothing about the behaviour it was written for."""
    import shutil
    import subprocess

    if shutil.which("git") is None:
        pytest.skip("git not installed")

    repo = tmp_path / "repo"
    repo.mkdir()

    def git(*args):
        return subprocess.run(
            ["git", *args], cwd=repo, capture_output=True, text=True
        ).stdout

    git("init", "-q")
    (repo / "README.md").write_text("hello")
    git("add", "-A")
    git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init")
    assert git("status", "--porcelain") == ""

    store = SpillStore(repo / ".reeve" / "output")
    for _ in range(3):
        store.save("a very large build log\n" * 500, label="shell")

    assert git("status", "--porcelain") == ""
    assert ".reeve/.gitignore" in git(
        "check-ignore", "-v", ".reeve/output/shell-1.log"
    )


def test_files_are_named_after_the_tool_that_produced_them(tmp_path):
    store = SpillStore(tmp_path)
    assert store.save("a", label="shell").endswith("shell-1.log")
    assert store.save("b", label="page").endswith("page-2.log")
    # A label from an untrusted place cannot escape the directory.
    assert store.save("c", label="../../etc").endswith("etc-3.log")


def test_a_rebuilt_store_never_writes_over_a_file_the_transcript_cites(tmp_path):
    """The store is rebuilt with its engine (a restart, a reopened session) over the same
    directory, and the transcript still names the earlier files. Counting from zero put the
    next page at a path an old tool result cites: the locator then read as a different
    result (real store, 2026-10-04: 9 of 99 cited paths, 3 of 13 sessions)."""
    first = SpillStore(tmp_path)
    cited = first.save("the paper, whole", label="page")
    first.save("a build log", label="shell")

    rebuilt = SpillStore(tmp_path)  # what a restarted engine gets
    fresh = rebuilt.save("an unrelated page", label="page")

    assert fresh != cited
    assert fresh.endswith("page-3.log")  # after everything on disk, whatever its label
    with open(cited, encoding="utf-8") as handle:
        assert handle.read() == "the paper, whole"


def test_a_file_the_store_did_not_write_does_not_move_the_counter(tmp_path):
    (tmp_path / "notes.txt").write_text("x", encoding="utf-8")
    (tmp_path / "leftover.log").write_text("x", encoding="utf-8")
    assert SpillStore(tmp_path).save("a", label="shell").endswith("shell-1.log")


def test_the_store_keeps_only_the_recent_files(tmp_path):
    store = SpillStore(tmp_path, keep=3)
    for i in range(6):
        store.save(f"body {i}", label="shell")
    kept = sorted(p.name for p in tmp_path.glob("*.log"))
    assert len(kept) == 3
    assert "shell-6.log" in kept  # the newest always survives


def test_pruning_is_ordered_even_when_every_timestamp_is_identical(tmp_path):
    """Several spills inside one filesystem timestamp tick is ordinary — a loop of
    failing commands does it — and mtime alone would then evict in arbitrary order.
    Flattening every timestamp removes mtime as a signal entirely, so only the sequence
    number in the name can produce the right answer."""
    import os

    store = SpillStore(tmp_path, keep=3)
    for _ in range(8):
        store.save("body", label="shell")
    for path in tmp_path.glob("*.log"):
        os.utime(path, (1_700_000_000, 1_700_000_000))
    store.save("newest", label="shell")
    os.utime(tmp_path / "shell-9.log", (1_700_000_000, 1_700_000_000))

    assert sorted(p.name for p in tmp_path.glob("*.log")) == [
        "shell-7.log",
        "shell-8.log",
        "shell-9.log",
    ]


def test_the_store_stays_under_its_byte_budget(tmp_path):
    store = SpillStore(tmp_path, keep=50, max_bytes=400)
    for i in range(8):
        store.save("x" * 200, label="shell")
    total = sum(p.stat().st_size for p in tmp_path.glob("*.log"))
    assert total <= 400 + 200  # the file just written is never pruned


def test_a_store_that_cannot_write_returns_none_instead_of_raising(tmp_path):
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("i am a file")
    store = SpillStore(blocker / "output")
    assert store.save("anything", label="shell") is None


@pytest.mark.parametrize("bad", [{"keep": 0}, {"max_bytes": 0}])
def test_nonsense_bounds_are_refused_at_construction(tmp_path, bad):
    with pytest.raises(ValueError):
        SpillStore(tmp_path, **bad)


# -- where the store goes ---------------------------------------------------------


def test_the_store_prefers_the_sessions_scratch_over_the_users_project(tmp_path):
    """Scratch is already described to the model as the place for temporary files, and
    it is not the user's repository."""
    roots = [
        RootDir(path=tmp_path / "project", writable=True),
        RootDir(path=tmp_path / "scratch", writable=True, label="scratch"),
    ]
    store = SpillStore.under(roots)
    assert (tmp_path / "scratch").resolve() in store.directory.parents


def test_the_store_falls_back_to_the_first_writable_root(tmp_path):
    roots = [
        RootDir(path=tmp_path / "readonly", writable=False),
        RootDir(path=tmp_path / "project", writable=True),
    ]
    store = SpillStore.under(roots)
    assert (tmp_path / "project").resolve() in store.directory.parents


def test_a_session_with_nowhere_to_write_gets_no_store(tmp_path):
    """Not an error: spilling is an aid, and a read-only session raising on every long
    command would put the aid's failure in front of the user instead of the answer."""
    assert SpillStore.under([RootDir(path=tmp_path, writable=False)]) is None
    assert SpillStore.under([]) is None


# -- the call sites ----------------------------------------------------------------


def test_a_truncated_shell_run_leaves_a_readable_file(tmp_path):
    import sys

    from harness.tools.shell import LocalExecutor

    printer = (
        'foreach ($i in 1..800) { "line$i" }'
        if sys.platform == "win32"
        else "for i in $(seq 1 800); do echo line$i; done"
    )
    store = SpillStore(tmp_path / "spill")
    executor = LocalExecutor(
        cwd=tmp_path, max_output_chars=400, default_timeout=20, spill=store
    )
    try:
        result = executor.run(printer)
    finally:
        executor.close()

    assert result["truncated"] is True
    assert len(result["output"]) <= 400
    saved = result["full_output"]
    from pathlib import Path

    full = Path(saved).read_text(encoding="utf-8")
    # The whole run is there, including the middle the model was not shown.
    assert "line400" in full and "line1" in full and "line800" in full
    assert "line400" not in result["output"]


def test_a_shell_run_that_fits_writes_nothing(tmp_path):
    from harness.tools.shell import LocalExecutor

    store = SpillStore(tmp_path / "spill")
    executor = LocalExecutor(cwd=tmp_path, default_timeout=20, spill=store)
    try:
        result = executor.run("echo hi")
    finally:
        executor.close()
    assert "full_output" not in result
    assert not (tmp_path / "spill").exists()


def test_an_executor_without_a_store_behaves_exactly_as_before(tmp_path):
    from harness.tools.shell import LocalExecutor

    executor = LocalExecutor(cwd=tmp_path, max_output_chars=200, default_timeout=20)
    try:
        result = executor._result("cmd", 0, "out", timed_out=False, truncated=True)
    finally:
        executor.close()
    assert "full_output" not in result


def test_the_card_hands_over_the_path_not_just_the_news():
    """This used to append "(full log kept)" to the card's text — a fact the reader could
    not act on, since the path itself is cut by the 300-character result preview every
    surface is built from. The presentation carries the path now, so the row can offer to
    open it, and the wording disappears in favour of the thing it was describing."""
    from harness.tools.shell import RUN_SHELL_CONTRACT

    value = {"exit_code": 0, "output": "a", "truncated": True, "omitted_lines": 900}
    assert RUN_SHELL_CONTRACT.present_result({}, value).link is None

    value["full_output"] = "/w/.reeve/output/shell-1.log"
    shown = RUN_SHELL_CONTRACT.present_result({}, value)
    assert shown.link == "/w/.reeve/output/shell-1.log"
    assert shown.as_dict()["link"] == "/w/.reeve/output/shell-1.log"
    # The count stays in the text: it is what tells the reader the gap is worth opening.
    assert "900 omitted" in shown.detail
