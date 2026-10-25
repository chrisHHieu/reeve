"""The `<environment>` block put into the system prompt (`context/environment`): where the session
works, on what platform and day, and what state its git repository is in.
"""

from __future__ import annotations

import subprocess
import sys

from harness.context.environment import environment_context


def _repo(tmp_path):
    """A repository on `main` with one commit, "first commit"."""
    folder = tmp_path / "repo"
    folder.mkdir()

    def git(*args):
        subprocess.run(["git", "-C", str(folder), *args], capture_output=True, check=True)

    git("init", "-q", "-b", "main")
    git("config", "user.email", "t@t.io")
    git("config", "user.name", "T")
    (folder / "f.txt").write_text("1", encoding="utf-8")
    git("add", "-A")
    git("commit", "-qm", "first commit")
    return folder


def test_the_block_names_the_folder_the_platform_and_the_day(tmp_path):
    block = environment_context(tmp_path)
    assert str(tmp_path.resolve()) in block and sys.platform in block and "Today's date:" in block
    assert block.count("<environment>") == 1 and "</environment>" in block


def test_a_folder_that_is_no_repository_says_so(tmp_path):
    assert "not a git repository" in environment_context(tmp_path)


def test_a_clean_repository_gives_its_branch_and_last_commit(tmp_path):
    block = environment_context(_repo(tmp_path))
    assert "Git branch: main" in block and "Git status: clean" in block and "first commit" in block


def test_changed_files_are_counted_and_named(tmp_path):
    repo = _repo(tmp_path)
    (repo / "f.txt").write_text("2", encoding="utf-8")
    (repo / "new.txt").write_text("x", encoding="utf-8")
    block = environment_context(repo)
    assert "Git status (2 changed):" in block and "f.txt" in block and "new.txt" in block


class _NeverRuns:
    def complete(self, **request):  # pragma: no cover
        raise AssertionError("no turn is run")

    def capabilities(self, model):
        from harness.llm.providers import ModelCapabilities

        return ModelCapabilities()


def test_a_built_engine_has_the_block_in_its_system_prompt(tmp_path):
    from harness.compose import build_engine
    from harness.modes.agents import reeve_agent

    engine = build_engine(agent=reeve_agent(), workspace=tmp_path, provider=_NeverRuns())
    try:
        system = engine.messages[0]
        assert system["role"] == "system"
        assert "<environment>" in system["content"] and str(tmp_path.resolve()) in system["content"]
    finally:
        engine.executor.close()
