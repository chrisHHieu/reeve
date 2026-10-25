"""Listing a workspace's artifacts never goes into the operating system's application data.

On macOS 14 and later, just walking ~/Library/Application Support (other apps' containers) sets off
the App Data protection, and the person is shown "Reeve would like to access data from other apps".
The artifacts panel is listed again after every turn, so a workspace at home brought that prompt
with nobody asking for anything. The folders are cut off as the walk meets them: rglob went in first
and filtered after, which was the bug.
"""

import os

from harness.interface.server.manager import SessionManager
from harness.tools.search.ignore import OS_DATA_DIRS


def _home(tmp_path):
    """A home folder with another app's data under Library, a dependency, and one real report."""
    home = tmp_path / "home"
    other_app = home / "Library" / "Application Support" / "SomeOtherApp"
    other_app.mkdir(parents=True)
    (other_app / "secrets.json").write_text("{}")
    (home / "Library" / "notes.md").write_text("# private")
    (home / "node_modules" / "pkg").mkdir(parents=True)
    (home / "node_modules" / "pkg" / "readme.md").write_text("# dep")
    (home / "report.md").write_text("# real artifact")
    return home


def test_the_walk_never_enters_library_or_node_modules(tmp_path, monkeypatch):
    entered: list[str] = []
    walk = os.walk

    def watched(top, *a, **k):
        for folder, folders, files in walk(top, *a, **k):
            entered.append(folder)
            yield folder, folders, files

    monkeypatch.setattr("harness.interface.server.manager.artifacts.os.walk", watched)
    listed = [a["name"] for a in SessionManager(data_dir=tmp_path / "data", workspace=str(_home(tmp_path))).list_artifacts("s1")]
    assert "report.md" in listed
    assert "notes.md" not in listed and "secrets.json" not in listed
    # not just left out: never entered, which is what sets the prompt off
    assert not any("Library" in folder for folder in entered), entered
    assert not any("node_modules" in folder for folder in entered)


def test_the_cut_off_folders_cover_macos_and_windows():
    assert {"Library", "AppData", "Application Data"} <= OS_DATA_DIRS
