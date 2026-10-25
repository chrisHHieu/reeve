"""What every test shares: a state folder, scratch base and home of its own, nothing process-wide
carried over from the test before, and `fake_slack`.

`fake_slack` starts FakeSlack in this process on a free port and sets `SLACK_API_URL` to it, so the
real `SlackAdapter` and `slack_bolt` run from end to end with no network, no tokens and no Slack
app (`harness.testing.fake_slack`, `platform/docs/FAKE-SLACK-SPEC.md`).
"""

from __future__ import annotations

import os
import tempfile

import pytest
import pytest_asyncio

from harness.testing.fake_slack import FakeSlack


def pytest_configure(config):
    """Point the state folder somewhere disposable before any test module is imported. The
    per-test fixture below only applies once a test runs, and a module that resolves
    `state_dir()` at import (the desktop-control server does, for its log file) would otherwise
    resolve the developer's real folder during collection, and could move it."""
    os.environ["REEVE_STATE_DIR"] = tempfile.mkdtemp(prefix="reeve-pytest-state-")


@pytest.fixture(autouse=True)
def _own_state_folder(tmp_path, monkeypatch):
    """A state folder and a SecretStore for each test alone. A SessionManager built without one
    read the developer's own state, their cloud sign-in included, and a test's new session sent real
    telemetry (seen 2026-07-03 as bursts in a telemetry table)."""
    monkeypatch.setenv("REEVE_STATE_DIR", str(tmp_path / "reeve-state"))
    # every session gets a scratch folder; without this they would be made under the real ~/Reeve
    monkeypatch.setenv("REEVE_SCRATCH_BASE", str(tmp_path / "reeve-scratch"))
    monkeypatch.delenv("REEVE_API_TOKEN", raising=False)
    # The Metrics page's self-review starts a child process on a stale result; a test that reads
    # the page must not start one (tests/test_metrics_review.py drives it directly).
    monkeypatch.setenv("REEVE_SELF_REVIEW", "off")


@pytest.fixture(autouse=True)
def _no_compat_skills(monkeypatch, tmp_path):
    """P1-2 compat roots read `~/.agents`, `~/.claude`, `~/.config/opencode`. Tests must
    never discover the developer's real machine skills — point the home anchor at an empty
    temp dir; tests that exercise compat roots patch `home_dir` themselves."""
    monkeypatch.setattr(
        "harness.extensions.skills.roots.home_dir", lambda: tmp_path / "no-compat-home"
    )


@pytest.fixture(autouse=True)
def _no_armed_goals():
    """Goal activation is process-local by design (`goals._ACTIVATION`), so a test that
    arms a goal would leave it armed for every later test: the startup-disarm test then
    counted goals armed by another file. Each test starts with nothing armed."""
    from harness.loop.goals import GoalStore

    GoalStore.disarm_all()
    yield
    GoalStore.disarm_all()


@pytest.fixture(autouse=True)
def _os_default_shell(monkeypatch):
    """The Settings shell is process-wide (`shell_kinds.set_preferred`): a test that picks bash
    would leave every later test's worlds on bash, and the developer's own default must not reach
    the suite either."""
    from harness.tools import shell_kinds

    monkeypatch.delenv("REEVE_DEFAULT_SHELL", raising=False)
    shell_kinds.set_preferred("")
    yield
    shell_kinds.set_preferred("")


@pytest.fixture(autouse=True)
def _no_held_secrets():
    """The secrets a process holds are process-wide (`config/known_secrets.py`): a value one test
    stored must not be redacted out of the next test's log."""
    from harness.config import known_secrets

    known_secrets.forget_all()
    yield
    known_secrets.forget_all()


@pytest.fixture(autouse=True)
def _no_session_plugins(monkeypatch):
    """A developer's `REEVE_PLUGIN_DIRS` must not load plugins into the suite."""
    monkeypatch.delenv("REEVE_PLUGIN_DIRS", raising=False)


@pytest.fixture(autouse=True)
def _no_builtin_mcp(monkeypatch):
    """Tests assert exact MCP server sets and arming behaviour — the shipped servers
    (browser via npx, computer-use via the venv python) would spawn real processes in
    every arming test. Built-ins come back only where a test patches them in."""
    monkeypatch.setattr("harness.extensions.mcp.config.builtin_mcp_servers", lambda *a, **k: {})


@pytest_asyncio.fixture
async def fake_slack(monkeypatch):
    """FakeSlack, started, with `SLACK_API_URL` pointing at it until the test ends."""
    slack = FakeSlack()
    await slack.start()
    monkeypatch.setenv("SLACK_API_URL", slack.api_url)
    try:
        yield slack
    finally:
        await slack.stop()
