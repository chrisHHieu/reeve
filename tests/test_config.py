"""Loading the config (`config/config`): the defaults, then the person's global file, then the
workspace's own, where a workspace may change how the agent works and never what it is allowed.
"""

from __future__ import annotations

import os

from harness.config.config import load_config


def _files(tmp_path, global_text: str, workspace_text: str = ""):
    """The global file, and a workspace with `.reeve/config.toml` when there is text for it."""
    global_file = tmp_path / "global.toml"
    global_file.write_text(global_text)
    workspace = tmp_path / "ws"
    (workspace / ".reeve").mkdir(parents=True)
    if workspace_text:
        (workspace / ".reeve" / "config.toml").write_text(workspace_text)
    return global_file, workspace


class _NeverRuns:
    def complete(self, **k):  # pragma: no cover
        raise NotImplementedError

    def capabilities(self, m):  # pragma: no cover
        raise NotImplementedError


def _built(tmp_path, **options):
    from harness.compose import build_engine
    from harness.modes.agents import reeve_agent

    return build_engine(agent=reeve_agent(), workspace=tmp_path, provider=_NeverRuns(), **options)


def test_defaults_when_no_files(tmp_path):
    config = load_config(global_path=tmp_path / "nope.toml")
    assert (config.model, config.mode) == ("gpt-5.6-sol", "workspace-write")
    assert config.max_iterations == 0  # no cap on steps
    assert config.allowed_commands == []
    assert (config.subagent_depth, config.default_agent) == (1, "")


def test_a_table_of_a_removed_mechanism_is_ignored(tmp_path):
    """A `[decision]` table left in a config file from before the decision
    layer was removed must not fail the load, and must not surface as a setting."""
    g = tmp_path / "global.toml"
    g.write_text(
        '[decision]\nenabled = "on"\n\n[decision.slots]\nstop_check = "on"\n\n'
        "[delegation]\nworktree_isolation = true\n"
    )
    cfg = load_config(global_path=g)
    assert not hasattr(cfg, "decision")
    assert cfg.delegation == {"worktree_isolation": True}


def test_subagent_depth_and_default_agent_round_trip(tmp_path):
    g = tmp_path / "global.toml"
    g.write_text('subagent_depth = 2\ndefault_agent = " reviewer "\n')
    cfg = load_config(global_path=g)
    assert cfg.subagent_depth == 2
    assert cfg.default_agent == "reviewer"

    # `0` is the documented "disable subagents" value, not an unset.
    g.write_text("subagent_depth = 0\n")
    assert load_config(global_path=g).subagent_depth == 0

    # A typo degrades to the default (1) instead of raising mid-run.
    g.write_text('subagent_depth = "two"\n')
    assert load_config(global_path=g).subagent_depth == 1

    # A negative value clamps to the disabled end of the scale, not below it.
    g.write_text("subagent_depth = -3\n")
    assert load_config(global_path=g).subagent_depth == 0


def test_context_editing_is_global_only(tmp_path):
    """Provider-level over a cached client: a workspace layer could never take effect,
    so it must not appear to."""
    g = tmp_path / "global.toml"
    g.write_text("[context_editing]\ntrigger_input_tokens = 50000\n", encoding="utf-8")
    ws = tmp_path / "ws"
    (ws / ".reeve").mkdir(parents=True)
    (ws / ".reeve" / "config.toml").write_text(
        "[context_editing]\nenabled = false\n", encoding="utf-8"
    )

    cfg = load_config(ws, global_path=g)

    assert cfg.context_editing == {"trigger_input_tokens": 50000}


def test_goal_max_rounds_default_global_and_workspace_precedence(tmp_path):
    """`[goal] max_rounds`: one table, global < workspace."""
    from harness.loop.goals import DEFAULT_MAX_ROUNDS

    assert load_config(global_path=tmp_path / "nope.toml").goal_max_rounds == DEFAULT_MAX_ROUNDS

    g = tmp_path / "global.toml"
    g.write_text("[goal]\nmax_rounds = 40\n")
    assert load_config(global_path=g).goal_max_rounds == 40

    ws = tmp_path / "ws"
    (ws / ".reeve").mkdir(parents=True)
    (ws / ".reeve" / "config.toml").write_text("[goal]\nmax_rounds = 7\n")
    assert load_config(ws, global_path=g).goal_max_rounds == 7  # workspace wins
    # Without a workspace value the global one survives, like every other key.
    (ws / ".reeve" / "config.toml").write_text("[goal]\n")
    assert load_config(ws, global_path=g).goal_max_rounds == 40


def test_goal_max_rounds_invalid_negative_logs_and_falls_back(tmp_path, caplog):
    """An unusable config value must not kill the load: invalid/negative degrades to
    the default with a log, and a value past the safe-integer ceiling clamps."""
    from harness.loop.goals import DEFAULT_MAX_ROUNDS, MAX_MAX_ROUNDS

    g = tmp_path / "global.toml"
    with caplog.at_level("WARNING", logger="harness.config.config"):
        g.write_text('[goal]\nmax_rounds = "many"\n')
        assert load_config(global_path=g).goal_max_rounds == DEFAULT_MAX_ROUNDS
        assert "max_rounds" in caplog.text

        caplog.clear()
        g.write_text("[goal]\nmax_rounds = -3\n")
        assert load_config(global_path=g).goal_max_rounds == DEFAULT_MAX_ROUNDS
        assert "must be positive" in caplog.text

    # A huge value clamps to the safe-integer ceiling, not to the default.
    g.write_text(f"[goal]\nmax_rounds = {2**60}\n")
    assert load_config(global_path=g).goal_max_rounds == MAX_MAX_ROUNDS


def test_global_and_workspace_override(tmp_path):
    global_file, workspace = _files(tmp_path, 'model = "gpt-4o"\nmax_iterations = 20\nport = 9000\n', 'max_iterations = 30\nmode = "plan"\n')
    config = load_config(workspace, global_path=global_file)
    assert (config.model, config.port) == ("gpt-4o", 9000)  # the global file's
    assert (config.max_iterations, config.mode) == (30, "plan")  # the workspace's, over the global


def test_workspace_cannot_grant_its_own_permissions(tmp_path):
    global_file, workspace = _files(tmp_path, 'allowed_commands = ["git status"]\nauto_allow = ["write_file"]\n', 'allowed_commands = ["python3"]\nauto_allow = ["run_shell"]\n')
    config = load_config(workspace, global_path=global_file)
    assert (config.allowed_commands, config.auto_allow) == (["git status"], ["write_file"])


def test_trusted_workspace_adds_its_command_allowances_only(tmp_path):
    global_file, workspace = _files(tmp_path, 'allowed_commands = ["git status"]\nauto_allow = ["write_file"]\n', 'allowed_commands = ["pytest", "git status"]\nauto_allow = ["run_shell"]\n')
    config = load_config(workspace, global_path=global_file, workspace_trusted=True)
    assert config.allowed_commands == ["git status", "pytest"]  # added to, once each
    assert config.auto_allow == ["write_file"]  # never from the workspace


def test_workspace_trust_is_canonical_and_user_owned(tmp_path):
    from harness.safety.workspace_trust import WorkspaceTrustStore

    project = tmp_path / "project"
    project.mkdir()
    link = tmp_path / "link-to-project"
    link.symlink_to(project, target_is_directory=True)
    trust = WorkspaceTrustStore(tmp_path / "state" / "workspace_trust.json")
    canonical = str(project.resolve())
    assert trust.set_trusted(link, True) == canonical  # trusted by way of the link, kept under the real path
    assert trust.is_trusted(project) and trust.list() == [canonical]
    if os.name != "nt":
        # Windows has no mode bits; there the store is kept private by living in the user's profile
        assert (trust.path.stat().st_mode & 0o777) == 0o600
    trust.set_trusted(project, False)
    assert not trust.is_trusted(link)
    trust.path.write_text("[]")  # emptied by hand
    assert trust.list() == []


def test_build_engine_honors_explicit_empty_command_allowlist(tmp_path):
    from harness.config.config import global_config_path

    global_config_path().parent.mkdir(parents=True)
    global_config_path().write_text('allowed_commands = ["pytest"]\n')
    engine = _built(tmp_path, allowed_commands=[])  # an empty list passed is a list, not "use the file's"
    try:
        verdict = engine.permissions.evaluate("run_shell", {"command": "pytest -q"}, None)
        assert not verdict.allowed and verdict.needs_user
    finally:
        engine.executor.close()


def test_build_engine_respects_max_iterations(tmp_path):
    (tmp_path / ".reeve").mkdir()
    (tmp_path / ".reeve" / "config.toml").write_text("max_iterations = 3\n")
    engine = _built(tmp_path)
    try:
        assert engine.max_iterations == 3
    finally:
        engine.executor.close()


def test_no_step_cap_by_default(tmp_path):
    """The default: built-ins are uncapped, so a turn runs until the model stops.
    `0` in config means unlimited, not 'stop at the first step'."""
    from harness.compose import build_engine
    from harness.modes.agents import reeve_agent

    class _Stub:
        def complete(self, **k):  # pragma: no cover
            raise NotImplementedError

        def capabilities(self, m):  # pragma: no cover
            raise NotImplementedError

    engine = build_engine(agent=reeve_agent(), workspace=tmp_path, provider=_Stub())
    try:
        assert engine.max_iterations is None
        assert engine._iteration_cap_reached(10_000) is False
    finally:
        engine.executor.close()


def test_agent_steps_win_over_the_global_fallback(tmp_path):
    (tmp_path / ".reeve").mkdir()
    (tmp_path / ".reeve" / "config.toml").write_text("max_iterations = 9\n")

    from harness.compose import build_engine
    from harness.modes.agents.base import Agent

    class _Stub:
        def complete(self, **k):  # pragma: no cover
            raise NotImplementedError

        def capabilities(self, m):  # pragma: no cover
            raise NotImplementedError

    agent = Agent(name="capped", title="Capped", system_prompt="x", steps=4)
    engine = build_engine(agent=agent, workspace=tmp_path, provider=_Stub())
    try:
        assert engine.max_iterations == 4  # agent.steps beats the global 9
        assert engine._iteration_cap_reached(4) is True
    finally:
        engine.executor.close()
