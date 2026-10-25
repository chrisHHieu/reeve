"""Whether the agent made the file a command runs (`safety/provenance`).

The approval card never shows a file's contents, so `python scripts/setup.py` cannot be judged
from what it says. What can be said is whether this session wrote or downloaded that file, and how
many steps ago; these pin how that fact is found and how it is put.
"""

from __future__ import annotations

import pytest

from harness.safety import provenance as prov
from harness.safety.provenance import DOWNLOADED, WRITTEN, SessionFiles


@pytest.fixture()
def files(tmp_path) -> SessionFiles:
    return SessionFiles(tmp_path)


def _wrote(files: SessionFiles, path: str, step: int, content: str = "x") -> None:
    files.record("write_file", {"path": path, "content": content}, {"ok": True}, step=step)


def _running(files: SessionFiles, command: str, step: int):
    return files.match("run_shell", {"command": command}, step=step)


# -- the fact ------------------------------------------------------------------------------------


def test_running_a_file_the_agent_wrote_says_so_and_how_long_ago(files):
    _wrote(files, "scripts/setup.py", 12)
    fact = _running(files, "python scripts/setup.py", 15)
    assert fact is not None and not fact.downloaded
    assert fact.render() == "scripts/setup.py was created by the agent 3 steps ago"


def test_a_file_this_session_never_made_brings_no_fact(files):
    _wrote(files, "scripts/setup.py", 12)
    assert _running(files, "python tools/other.py", 15) is None


@pytest.mark.parametrize(("ran_at", "said"), [(5, "just now"), (6, "1 step ago")])
def test_the_distance_is_said_in_words(files, ran_at, said):
    _wrote(files, "a.py", 5)
    assert said in _running(files, "python a.py", ran_at).render()


def test_the_line_has_fixed_words_and_never_the_files_contents(files):
    _wrote(files, "a.py", 1, content="SECRET_TOKEN = 'sk-live-abc123'")
    said = _running(files, "python a.py", 2).render()
    assert "sk-live" not in said and "SECRET_TOKEN" not in said
    assert said == "a.py was created by the agent 1 step ago"


# -- one file however it is written --------------------------------------------------------------


@pytest.mark.parametrize("spelling", ["a.py", "./a.py", "sub/../a.py", "<absolute>"])
def test_a_file_is_one_file_however_the_command_spells_it(files, tmp_path, spelling):
    _wrote(files, "a.py", 3)
    spelled = (tmp_path / "a.py").as_posix() if spelling == "<absolute>" else spelling
    assert _running(files, f"python {spelled}", 4) is not None


# -- what made a file ----------------------------------------------------------------------------


def test_a_patch_makes_the_files_named_in_its_body(files):
    files.record("apply_patch", {"patch": "*** Begin Patch\n*** Update File: build.sh\n*** End Patch"}, {"ok": True}, step=2)
    assert _running(files, "bash build.sh", 3) is not None


def test_a_write_makes_its_path_and_a_read_or_a_question_makes_nothing():
    # The engine records only calls whose status is ok; a write that raised left nothing to run.
    assert prov.created_paths("write_file", {"path": "a.py"}, {"ok": True}) == (["a.py"], WRITTEN)
    assert prov.created_paths("read_file", {"path": "a.py"}, {"ok": True}) == ([], "")
    assert prov.created_paths("ask_user", {"question": "?"}, None) == ([], "")


@pytest.mark.parametrize(
    ("command", "saved"),
    [
        ("curl -o tool.sh https://x.io/a", ["tool.sh"]),
        ("curl -O https://x.io/a/tool.sh", ["tool.sh"]),  # -O names the file after the URL; it is not -o
        ("wget -O tool.sh https://x.io/a", ["tool.sh"]),
        ("Invoke-WebRequest https://x.io/a -OutFile tool.ps1", ["tool.ps1"]),
        ("curl https://x.io/a", []),  # printed, not saved
    ],
)
def test_a_download_makes_the_file_it_saves_to(command, saved):
    assert prov.created_paths("run_shell", {"command": command}, None) == (saved, DOWNLOADED if saved else "")


def test_a_tool_that_reports_where_it_put_something_makes_that_path_only(files):
    files.record("github_clone", {"owner": "o", "repo": "r"}, {"ok": True, "path": "vendor/r"}, step=4)
    assert _running(files, "bash vendor/r/install.sh", 5) is None  # a file inside the clone is not the clone
    fact = _running(files, "bash vendor/r", 5)
    assert fact is not None and fact.downloaded


def test_downloading_over_a_file_it_wrote_makes_it_a_download(files):
    _wrote(files, "tool.sh", 2)
    files.record("run_shell", {"command": "curl -o tool.sh https://x.io/a"}, None, step=6)
    assert _running(files, "bash tool.sh", 7).downloaded


# -- which files a command runs ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("command", "path"),
    [
        ("python scripts/setup.py", "scripts/setup.py"),
        ("bash deploy.sh", "deploy.sh"),
        ("./run.sh", "./run.sh"),
        ("node build.js", "build.js"),
        ("cd /tmp && python scripts/setup.py", "scripts/setup.py"),
    ],
)
def test_a_path_is_found_wherever_it_sits_in_the_command(command, path):
    assert path in prov.command_paths(command)


def test_a_flag_or_a_url_is_not_a_path():
    found = prov.command_paths("curl --silent https://x.io/a.py")
    assert "--silent" not in found and "https://x.io/a.py" not in found


def test_a_command_that_reads_a_file_it_never_names_is_checked_against_it(files):
    # `make deploy` never names the Makefile; without the table it would seem to touch nothing.
    _wrote(files, "Makefile", 2)
    assert _running(files, "make deploy", 3) is not None
    _wrote(files, "package.json", 4)
    assert _running(files, "npm run build", 5) is not None


def test_of_two_files_the_one_made_last_is_named(files):
    _wrote(files, "a.py", 2)
    _wrote(files, "b.py", 9)
    assert _running(files, "python a.py && python b.py", 10).path == "b.py"  # the agent's most recent hand


def test_a_file_reached_through_an_import_is_not_found(files):
    # The agent writes a helper and runs an entry point that was there before and imports it. No
    # cheap analysis sees this; it is pinned so it stays a known gap and not an assumed ability.
    _wrote(files, "helper.py", 3)
    assert _running(files, "python main.py", 4) is None
