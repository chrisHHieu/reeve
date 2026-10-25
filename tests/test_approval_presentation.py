"""What a person reads on the approval card, and where it comes from.

The card was never a raw args dump — it already humanized the title, named the scope, and
previewed the command. The problem was WHERE that knowledge lived: a `switch` on tool
names in the frontend, which can only speak about the names hardcoded in it. Every MCP
tool, every connector, every persona toolkit fell to `Use <raw_tool_name>` with a
`key=value` line under it.

Two consequences, and the second is the serious one:

1. The tool that knows most about a call — the one that declared it — had no way to say
   anything, even though `ToolContract.present_call` already existed and the engine
   already put its output on the event. Nothing read it.
2. The scope note's default was "stays on this computer". That is a PROMISE, and it was
   being made about tools the frontend had never heard of. A third-party MCP server
   filing a ticket in someone else's tracker was described to the user as staying on
   their machine, on the one surface where that claim decides consent.

The parked Inbox card had the same problem twice over: it re-derived everything from
tool+arguments because that was all the server put in the item.
"""

from __future__ import annotations

import asyncio
import types

import pytest

from harness.extensions.mcp.config import MCPServerDef
from harness.extensions.mcp.tools import _headline, build_callables
from harness.tools import ToolRegistry
from harness.tools.contract import contract_of


# -- an MCP tool can finally say what it does ------------------------------------


class _Tool:
    def __init__(self, name, description, schema=None):
        self.name = name
        self.description = description
        self.inputSchema = schema or {"type": "object", "properties": {}}


def _mcp_tools(*tools):
    server = MCPServerDef(name="tracker", transport="stdio", command="x")
    loop = asyncio.new_event_loop()
    try:
        return build_callables(server, list(tools), lambda *a, **k: None, loop)
    finally:
        loop.close()


def test_the_server_authors_own_sentence_becomes_the_card_headline():
    """Nothing in this process knows what a third-party tool does. The description the
    server author wrote is the only honest thing to put on the card, and it was being
    thrown away."""
    (tool,) = _mcp_tools(
        _Tool("create_issue", "Create a new issue in a project. Requires a project key.")
    )
    shown = contract_of(tool).present_call({"project": "OPS"})
    assert shown.summary == "Create a new issue in a project."


def test_a_tool_with_no_description_still_gets_a_sentence():
    (tool,) = _mcp_tools(_Tool("create_issue", ""))
    assert contract_of(tool).present_call({}).summary == "Run create_issue on tracker"


def test_a_paragraph_is_cut_to_one_line():
    long = "A" * 300
    (tool,) = _mcp_tools(_Tool("t", long))
    summary = contract_of(tool).present_call({}).summary
    assert len(summary) == 100 and summary.endswith("…")


def test_an_mcp_tool_declares_no_kind_on_purpose():
    """`kind` is what lets a surface state that an action is local or outward-bound. A
    third-party server could be a filesystem bridge or someone else's issue tracker, so
    the honest declaration is none at all — which is what makes the card admit it."""
    (tool,) = _mcp_tools(_Tool("create_issue", "File a ticket."))
    assert contract_of(tool).present_call({}).kind == "other"


def test_the_contract_reaches_the_registry_through_the_mcp_wrapper():
    registry = ToolRegistry()
    registry.register_all(_mcp_tools(_Tool("create_issue", "File a ticket.")))
    name = next(n for n in registry.names())
    assert registry.contract(name) is not None


@pytest.mark.parametrize(
    "description,expected",
    [
        ("One. Two.", "One."),
        ("No trailing period", "No trailing period"),
        ("   Padded.   ", "Padded."),
        ("Ends with a question? And more.", "Ends with a question?"),
    ],
)
def test_the_headline_takes_exactly_the_first_sentence(description, expected):
    assert _headline(description, "t", "s") == expected


# -- the parked card gets what the live card gets ---------------------------------


def test_the_parked_approval_carries_the_category_and_the_presentation():
    """The Inbox card re-derived everything from tool+arguments, because that was all the
    server put in the item — so it disagreed with the live card about the same decision.
    Both facts it was missing are ones no amount of staring at the arguments can supply.
    """
    from harness.loop import PermissionRequest
    from harness.interface.server.manager import SessionManager

    request = PermissionRequest(
        tool_name="mcp__tracker__create_issue",
        arguments={"project": "OPS"},
        metadata=types.SimpleNamespace(category="mcp"),
        reason="requires approval",
        presentation={"summary": "File a ticket", "kind": "other"},
    )
    data = SessionManager.approval_prompt_data(
        types.SimpleNamespace(task_store=types.SimpleNamespace(task_for_run_session=lambda _s: None)),
        "s1",
        request,
    )
    assert data["tool"] == "mcp__tracker__create_issue"
    assert data["category"] == "mcp"
    assert data["presentation"]["summary"] == "File a ticket"


def test_a_tool_without_a_contract_parks_no_presentation():
    """Absent, not empty: the card falls back to its own per-tool switch, and an empty
    object would make it render a blank headline instead."""
    from harness.loop import PermissionRequest
    from harness.interface.server.manager import SessionManager

    request = PermissionRequest(
        tool_name="write_file",
        arguments={"path": "a.txt"},
        metadata=types.SimpleNamespace(category=""),
        reason="requires approval",
    )
    data = SessionManager.approval_prompt_data(
        types.SimpleNamespace(task_store=types.SimpleNamespace(task_for_run_session=lambda _s: None)),
        "s1",
        request,
    )
    assert "presentation" not in data
    assert "category" not in data


def test_the_engine_computes_the_presentation_once_for_both_consumers():
    """The live event and the parked request must show the same sentence. They do
    because it is computed once — a second derivation is a second chance to disagree.

    Since cards landed there is a THIRD consumer: the propose event, because a running
    row draws the command and its working directory before anything has run. So the
    single computation moved out of `_authorize` to the caller, which serves all three
    from one object rather than three surfaces that could drift apart.
    """
    import inspect

    from harness import loop as engine_module

    caller = inspect.getsource(engine_module.TurnEngine._screen_call)
    assert caller.count("safe_present_call(") == 1
    assert "self._authorize(tool_call, shown)" in caller
    assert '"presentation": shown.as_dict()' in caller

    authorize = inspect.getsource(engine_module.TurnEngine._authorize)
    # Computed by the caller and handed in: a second lookup here would be the drift this
    # test exists to prevent.
    assert "safe_present_call(" not in authorize
    assert "call_shown: Optional[ToolPresentation] = None" in authorize
    assert "presentation=call_shown.as_dict() if call_shown else None" in authorize
    card = inspect.getsource(engine_module.TurnEngine._approval_card)
    assert "safe_present_call(" not in card
    assert '"presentation": call_shown.as_dict()' in card
