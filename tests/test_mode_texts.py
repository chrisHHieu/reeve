"""What the model is told about the session's permissions matches what the engine does.

Every preset gets one line of the live context saying
where the person stands, and a refused call's result says what to do next. The checks read the
engine's verdicts and the context the model receives, not the wording, so the sentences can be
rewritten without a test going red while what they claim stays true.
"""

from __future__ import annotations

from harness.compose.live_context import make_context_provider
from harness.compose.prompts import PERMISSIONS_CONTEXT, PLAN_MODE_CONTEXT
from harness.config.roots import RootDir, render_context
from harness.modes.agents import reeve_agent
from harness.safety.permissions import Mode, PermissionEngine


def _said(text: str) -> str:
    return " ".join(text.split())


def _verdict(tmp_path, mode: Mode, tool: str, arguments: dict, *, plan: bool = False):
    return PermissionEngine(workspace_root=tmp_path, mode=mode, plan=plan).evaluate(tool, arguments)


def _mode_line(engine: PermissionEngine) -> str:
    provider, parts = make_context_provider(
        permissions=engine, roots_context=None, background_status=None, engine_of=lambda: None
    )
    provider()
    return parts.get("mode", "")


def test_every_preset_tells_the_model_where_it_stands_and_a_switch_changes_it(tmp_path):
    """Workspace-write and full access said nothing, so a model in full access was never told that
    no approval stands between a call and its effect. The line follows the engine turn by turn,
    which is how a switch reaches the model without a notice of its own."""
    engine = PermissionEngine(workspace_root=tmp_path, mode=Mode.WORKSPACE_WRITE)
    seen = {}
    for mode in (Mode.READ_ONLY, Mode.WORKSPACE_WRITE, Mode.DANGER_FULL_ACCESS):
        engine.mode = mode
        seen[mode] = _mode_line(engine)
        assert seen[mode] == PERMISSIONS_CONTEXT[mode.value]
    assert len(set(seen.values())) == 3
    engine.plan = True
    assert _mode_line(engine) == PLAN_MODE_CONTEXT


def test_read_only_reads_run_and_a_refused_change_says_what_to_do(tmp_path):
    """The read-only line no longer lists commands or forbids in advance: a read runs, and a change
    is refused by the engine with a result that says what to do instead. "read-only mode is
    read-only" said neither."""
    assert _verdict(tmp_path, Mode.READ_ONLY, "read_file", {"path": "a.txt"}).allowed
    assert _verdict(tmp_path, Mode.READ_ONLY, "run_shell", {"command": "git status"}).allowed
    for tool, args in (("write_file", {"path": "a.txt", "content": "x"}), ("run_shell", {"command": "npm install"})):
        refused = _verdict(tmp_path, Mode.READ_ONLY, tool, args)
        assert not refused.allowed and not refused.needs_user
        assert "instead" in refused.reason  # what to do, not only which mode refused
    planned = _verdict(tmp_path, Mode.WORKSPACE_WRITE, "write_file", {"path": "a.txt", "content": "x"}, plan=True)
    assert not planned.allowed and "put this change in the plan" in planned.reason


def test_workspace_write_asks_for_a_change_the_web_and_another_folder(tmp_path):
    assert _verdict(tmp_path, Mode.WORKSPACE_WRITE, "read_file", {"path": "a.txt"}).allowed
    outside = tmp_path.parent / "elsewhere.txt"
    for tool, args in (
        ("write_file", {"path": "a.txt", "content": "x"}),
        ("run_shell", {"command": "npm install"}),
        ("web_fetch", {"url": "https://example.com"}),
        ("read_file", {"path": str(outside)}),
    ):
        decision = _verdict(tmp_path, Mode.WORKSPACE_WRITE, tool, args)
        assert not decision.allowed and decision.needs_user, tool


def test_full_access_asks_nobody_even_outside_the_folders(tmp_path):
    outside = tmp_path.parent / "elsewhere.txt"
    for tool, args in (
        ("write_file", {"path": str(outside), "content": "x"}),
        ("write_file", {"path": ".git/hooks/pre-commit", "content": "x"}),
        ("run_shell", {"command": "npm install"}),
        ("web_fetch", {"url": "https://example.com"}),
    ):
        assert _verdict(tmp_path, Mode.DANGER_FULL_ACCESS, tool, args).allowed, tool


def test_the_folder_list_leaves_who_is_asked_to_the_permissions_line(tmp_path):
    """The list said "a write anywhere else needs approval" in every preset, which full access
    contradicts. One place says who is asked."""
    text = render_context([RootDir(path=tmp_path / "ws", writable=True), RootDir(path=tmp_path / "t", reached=True)])
    assert "approval" not in text


def test_the_agent_answers_a_question_and_does_a_request():
    """Full access asks nobody, so it is the instructions, not an approval card, that keep a
    question from turning into edits: 44 of 456 question turns wrote a file before 2026-10-12. The
    rule is told by what the user wants, with no list of words to match."""
    instructions = _said(reeve_agent().prompt_for())
    assert "A question gets an answer; a request gets done." in instructions
    assert "how, why, whether" not in instructions
    assert "On a request, never end a turn on a plan" in instructions
