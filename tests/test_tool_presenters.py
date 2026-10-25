"""The first three tools migrated to the output contract — one per risk class.

`run_shell` (EXEC), `send_message` (EXTERNAL) and `write_file` (WRITE_LOCAL) were picked
because they exercise three different shapes of the problem, not because they were the
easiest three:

- `run_shell` already carried a `description` argument whose stated purpose was to let
  an approval prompt show intent. Nothing read it. The contract is what finally spends
  it, so this test pins that wiring.
- `send_message` is the irreversible case the intent-level card exists for: the raw
  arguments say `target="slack:C0123"`, which nobody outside engineering can judge.
- `write_file` belongs to aisuite, not to this codebase. It is the shape MOST of the
  registry takes — 40 connectors, MCP tools, toolkit tools — so it proves a contract can
  reach a function we did not author.

Two properties are asserted throughout: a presenter reports what happened in the world
rather than what the runtime did, and no presenter performs I/O (they are handed values
and must be replayable from them alone).
"""

from __future__ import annotations

import tempfile

import pytest

import harness.modes.agents  # noqa: F401  — imported first to break a pre-existing cycle
from harness.modes.agents.base import AgentContext
from harness.modes.catalog import expand
from harness.integrations.connectors.outbound import SEND_MESSAGE_CONTRACT, make_send_message_tool
from harness.safety.secrets import SecretStore
from harness.tools import ToolRegistry
from harness.tools.contract import contract_of
from harness.tools.shell import RUN_SHELL_CONTRACT, SHELL_TASK_KILL_CONTRACT, SHELL_TASK_OUTPUT_CONTRACT, shell_tools


# -- run_shell (EXEC) -----------------------------------------------------------


def _call(args):
    return RUN_SHELL_CONTRACT.present_call(args).as_dict()


def _result(value):
    return RUN_SHELL_CONTRACT.present_result({}, value).as_dict()


def test_run_shell_card_leads_with_the_models_stated_intent():
    shown = _call({"command": "pytest -q", "description": "Run the test suite"})
    assert shown["summary"] == "Run the test suite"
    # The mechanism stays one rung down: hiding it from a reader who CAN judge it would
    # be its own dishonesty.
    assert shown["detail"] == "pytest -q"
    assert shown["kind"] == "execute"


def test_run_shell_card_still_says_something_without_a_description():
    assert _call({"command": "ls"})["summary"] == "Run a terminal command"


def test_run_shell_card_flags_a_background_run():
    shown = _call({"command": "npm run build", "run_in_background": True})
    assert "background" in shown["summary"]


def test_run_shell_card_bounds_a_huge_command():
    shown = _call({"command": "echo " + "x" * 5000})
    assert len(shown["detail"]) <= 160


@pytest.mark.parametrize(
    "value,expected",
    [
        ({"exit_code": 0, "output": "a\nb"}, "Command succeeded"),
        ({"exit_code": 2, "output": ""}, "Command failed (exit 2)"),
        ({"exit_code": None, "output": "", "timed_out": True}, "Command still running"),
        # The exit marker never parsed — say so instead of implying success.
        ({"exit_code": None, "output": "x"}, "Command finished, exit status unknown"),
        ({"task_id": "bg-1", "status": "running"}, "Started in the background as bg-1"),
        ({"error": "failed to start background task: x"}, "Command could not start"),
    ],
)
def test_run_shell_result_names_the_outcome(value, expected):
    assert _result(value)["summary"] == expected


def test_run_shell_result_counts_lines_and_flags_truncation():
    plain = _result({"exit_code": 0, "output": "a\nb\nc"})
    assert plain["detail"] == "3 lines of output"
    # Once retention reports HOW MUCH went missing, the card says so — "(kept the
    # tail)" described a mechanism, this describes a quantity the reader can act on.
    counted = _result(
        {
            "command": "pytest -q",
            "exit_code": 0,
            "output": "a",
            "truncated": True,
            "omitted_lines": 1203,
        }
    )
    assert counted["detail"] == "1 line of output, 1,203 omitted"
    # The CARD carries the count too, not just the row's detail string: the notice is drawn
    # under the output, where the reader who scrolled to the end is standing.
    assert counted["card"]["truncated"] is True
    assert counted["card"]["omitted_lines"] == 1203
    # A truncation with no count still says something rather than claiming completeness.
    uncounted = _result(
        {"command": "pytest -q", "exit_code": 0, "output": "a", "truncated": True}
    )
    assert uncounted["detail"] == "1 line of output (shortened)"
    assert "omitted_lines" not in uncounted["card"]


def test_run_shell_declares_a_render_but_no_timeout():
    """`timeout_ms` stays None on purpose: the tool governs its own deadline through
    `timeout_seconds`, and a second budget here would silently compete with the first.
    The render IS declared: without it the result dict is JSON-encoded, which charges
    the wrapper keys and escapes the whole stdout on every (often short) command."""
    assert RUN_SHELL_CONTRACT.render is not None
    assert RUN_SHELL_CONTRACT.timeout_ms is None


def test_shell_family_contracts_reach_the_registry():
    class _Executor:
        cwd = "."

    registry = ToolRegistry()
    registry.register_all(shell_tools(_Executor()))
    assert registry.contract("run_shell") is RUN_SHELL_CONTRACT
    # The background helpers are migrated too: their dicts carry retained output that
    # would otherwise be JSON-escaped.
    assert registry.contract("shell_task_output") is SHELL_TASK_OUTPUT_CONTRACT
    assert registry.contract("shell_task_kill") is SHELL_TASK_KILL_CONTRACT


# -- send_message (EXTERNAL) ----------------------------------------------------


def _send_call(args):
    return SEND_MESSAGE_CONTRACT.present_call(args).as_dict()


def test_send_card_names_the_destination_and_previews_the_text():
    shown = _send_call({"target": "slack:C0123", "text": "the report is ready"})
    assert shown["summary"] == "Send a message to C0123 on Slack"
    assert shown["detail"] == "the report is ready"
    assert shown["kind"] == "send"


def test_send_card_handles_a_bare_channel_name():
    assert "#general" in _send_call({"target": "#general", "text": "hi"})["summary"]


def test_send_card_calls_out_a_thread_reply():
    shown = _send_call({"target": "slack:C0123:1712.55", "text": "re"})
    assert shown["summary"].endswith("in a thread")


def test_send_card_survives_an_unparseable_target():
    """A malformed target must still produce a card — the person approving it is
    exactly who needs to see that it looks wrong."""
    assert _send_call({"target": "", "text": "hi"})["summary"]


def test_send_result_distinguishes_sent_from_refused():
    ok = SEND_MESSAGE_CONTRACT.present_result({}, {"ok": True, "target": "slack:C1"})
    assert ok.summary == "Message sent to slack:C1"
    bad = SEND_MESSAGE_CONTRACT.present_result({}, {"error": "no bot token for slack"})
    assert bad.summary == "Message was not sent"
    assert "no bot token" in bad.detail


def test_send_message_factory_attaches_the_contract():
    tool = make_send_message_tool(SecretStore())
    assert contract_of(tool) is SEND_MESSAGE_CONTRACT


# -- write_file (WRITE_LOCAL, a function this codebase does not own) ------------


def _registry_with_files():
    registry = ToolRegistry()
    registry.register_all(
        expand(["code_files"], AgentContext(workspace=tempfile.mkdtemp()))
    )
    return registry


def test_write_file_contract_reaches_a_borrowed_aisuite_tool():
    registry = _registry_with_files()
    assert registry.contract("write_file") is not None
    # `read_image` is gone: `read_file` shows a picture, and its contract renders
    # the sentence the picture rides beside.
    assert registry.contract("read_image") is None
    from harness.tools.files.renders import _read_render

    shown = {"path": "a.png", "bytes": 3, "shown": "a.png is attached below — look at it before answering."}
    assert _read_render({"path": "a.png"}, shown) == shown["shown"]


def test_the_whole_mutation_family_carries_a_contract():
    """`write_file`, `replace_in_file` and `apply_patch` are the ways the session offers to
    change a file and one question for a reader — so all are migrated, and a new one added
    to the toolkit without a contract is a row that silently loses its card.
    (`apply_unified_diff` is no longer offered — 2026-09-24 audit; its contract stays for
    the world that still implements it.)"""
    registry = _registry_with_files()
    assert registry.get("apply_unified_diff") is None
    for name in (
        "write_file",
        "replace_in_file",
        "apply_patch",
        "list_files",
        "read_file",
    ):
        assert registry.contract(name) is not None, name


def test_write_card_reports_the_path_and_the_size():
    contract = _registry_with_files().contract("write_file")
    shown = contract.present_call(
        {"path": "docs/report.md", "content": "x" * 3000}
    ).as_dict()
    assert shown["summary"] == "Write docs/report.md"
    assert shown["detail"] == "2.9 KB"
    assert shown["kind"] == "write"


def test_write_card_says_create_only_when_the_arguments_settle_it():
    """Whether the file already exists is a filesystem question, and a presenter may not
    ask one — purity is what lets the same call render the same card on replay. Only
    `overwrite=False` settles it from the arguments alone."""
    contract = _registry_with_files().contract("write_file")
    assert contract.present_call({"path": "n.txt", "content": "hi"}).summary.startswith(
        "Write"
    )
    assert contract.present_call(
        {"path": "n.txt", "content": "hi", "overwrite": False}
    ).summary.startswith("Create")


def test_write_result_reports_the_path_aisuite_returned():
    contract = _registry_with_files().contract("write_file")
    assert (
        contract.present_result({"path": "a.md"}, "docs/a.md").summary
        == "Wrote docs/a.md"
    )
    # A backend that returns nothing useful falls back to the requested path.
    assert contract.present_result({"path": "a.md"}, "").summary == "Wrote a.md"


# -- the property that must hold for all of them --------------------------------


def test_no_presenter_touches_the_filesystem():
    """Presenters run on the turn's hot path and their output is replayed from the log.
    One that read a file would make the same call render a different card later.

    The spies RECORD and delegate rather than raising, and the patch is undone before
    the assertion runs: a raising spy still installed while pytest builds a traceback
    takes the whole session down with it.
    """
    import builtins
    import pathlib

    contract = _registry_with_files().contract("write_file")  # built before patching
    seen: list[str] = []
    real = (builtins.open, pathlib.Path.exists, pathlib.Path.stat)

    def _spy(name, func):
        def wrapper(*args, **kwargs):
            seen.append(name)
            return func(*args, **kwargs)

        return wrapper

    builtins.open = _spy("open", real[0])
    pathlib.Path.exists = _spy("Path.exists", real[1])
    pathlib.Path.stat = _spy("Path.stat", real[2])
    try:
        contract.present_call({"path": "a.txt", "content": "hi"})
        contract.present_result({"path": "a.txt"}, "a.txt")
        RUN_SHELL_CONTRACT.present_call({"command": "ls", "description": "list"})
        RUN_SHELL_CONTRACT.present_result({}, {"exit_code": 0, "output": ""})
        SEND_MESSAGE_CONTRACT.present_call({"target": "slack:C1", "text": "hi"})
        SEND_MESSAGE_CONTRACT.present_result({}, {"ok": True, "target": "slack:C1"})
    finally:
        builtins.open, pathlib.Path.exists, pathlib.Path.stat = real

    assert seen == [], f"a presenter performed I/O: {seen}"
