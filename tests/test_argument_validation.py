"""A call whose arguments cannot bind to the tool is refused with the tool's real shape.

Found by the 2026-09-24 harness audit: ~14 calls in the real store reached the model as
raw Python — `make_web_fetch_tool.<locals>.web_fetch() got an unexpected keyword argument
'max_lines'` — a closure's qualified name and no word about what the tool DOES accept.
The check is the binding Python performs anyway (`inspect.Signature.bind`), done before
approval, so it cannot refuse a call the tool would have taken (no schema guesswork) and
no person is asked to approve a call that was going to fail.
"""

from __future__ import annotations

import asyncio

from harness.loop import TurnEngine
from harness.safety.permissions import PermissionEngine
from harness.llm.providers import AssistantTurn, ModelCapabilities, ProviderClient, ToolCall
from harness.tools import ToolRegistry

CALLED: list[dict] = []


def fetch(url: str, prompt: str = "") -> str:
    """Fetch a page."""
    CALLED.append({"url": url, "prompt": prompt})
    return "page"


def loose(**options) -> str:
    """Takes anything (the shape of an MCP tool)."""
    CALLED.append(options)
    return "ok"


class ScriptedProvider(ProviderClient):
    def __init__(self, turns):
        self._turns = list(turns)

    def complete(self, *, model, messages, tools=None, **settings):
        if not self._turns:
            return AssistantTurn(text="done", finish_reason="stop")
        return self._turns.pop(0)

    def capabilities(self, model):
        return ModelCapabilities()


def remote(**arguments) -> str:
    """A server's tool: forwards whatever it is given (how `mcp/tools.py` wraps one)."""
    CALLED.append(arguments)
    return "ok"


#: A server's own contract, as its `inputSchema` states it.
SERVER_SCHEMA = {
    "type": "object",
    "properties": {"workspaceId": {"type": "string"}, "content_id": {"type": "string"}, "limit": {"type": "integer"}},
    "required": ["workspaceId"],
}


def _run(tmp_path, name, arguments, server_schema=None):
    CALLED.clear()
    registry = ToolRegistry()
    registry.register(fetch)
    registry.register(
        remote,
        schema={
            "type": "function",
            "function": {"name": "remote", "description": "A server's tool.", "parameters": server_schema or SERVER_SCHEMA},
        },
    )
    # An explicit schema, as an MCP tool carries its server's: the signature is **kwargs.
    registry.register(
        loose,
        schema={
            "type": "function",
            "function": {
                "name": "loose",
                "description": "Takes anything.",
                "parameters": {"type": "object", "properties": {}},
            },
        },
    )
    asked: list = []

    async def approver(request):
        asked.append(request)
        from harness.loop import ApprovalOutcome

        return ApprovalOutcome.ONCE

    engine = TurnEngine(
        provider=ScriptedProvider(
            [
                AssistantTurn(
                    tool_calls=[ToolCall(id="c1", name=name, arguments=arguments)],
                    finish_reason="tool_calls",
                )
            ]
        ),
        registry=registry,
        permissions=PermissionEngine(workspace_root=tmp_path),
        model="x",
        approver=approver,
    )

    async def go():
        return [ev async for ev in engine.run("go")]

    asyncio.run(go())
    result = next(
        m for m in engine.messages if m.get("role") == "tool" and m.get("tool_call_id") == "c1"
    )
    return str(result.get("content")), asked


def test_an_unknown_argument_is_refused_with_the_accepted_ones(tmp_path):
    text, asked = _run(tmp_path, "fetch", {"url": "https://example.com", "max_lines": 5})
    assert CALLED == []  # the tool never ran
    assert asked == []  # and nobody was asked to approve a call that could not run
    assert "Invalid arguments for `fetch`" in text
    assert "max_lines" in text and "Accepted parameters: url, prompt" in text
    assert "<locals>" not in text and "TypeError" not in text


def test_a_missing_required_argument_is_named(tmp_path):
    text, _ = _run(tmp_path, "fetch", {"prompt": "summarize"})
    assert CALLED == []
    assert "missing required 'url'" in text


def test_a_call_that_binds_runs_and_a_kwargs_tool_with_no_contract_is_never_second_guessed(tmp_path):
    text, _ = _run(tmp_path, "fetch", {"url": "https://example.com"})
    assert CALLED == [{"url": "https://example.com", "prompt": ""}]
    _run(tmp_path, "loose", {"anything": 1, "at_all": 2})
    assert CALLED == [{"anything": 1, "at_all": 2}]


def test_a_wrong_name_is_answered_with_the_one_it_was_probably_meant_to_be(tmp_path):
    text, _ = _run(tmp_path, "fetch", {"url": "https://example.com", "promt": "summarize"})
    assert "unexpected 'promt' (did you mean 'prompt'?)" in text


def test_a_server_tool_missing_what_its_schema_requires_is_refused_before_it_is_sent(tmp_path):
    # The shape of the store's Atlassian failures: `workspace` for `workspaceId`, and
    # `contentId` / `content-id` for `content_id`, each answered by the server opaquely.
    text, asked = _run(tmp_path, "remote", {"workspace": "acme", "contentId": "7"})
    assert CALLED == [] and asked == []  # nothing sent, and nobody asked to approve it
    assert "unexpected 'workspace' (did you mean 'workspaceId'?), 'contentId' (did you mean 'content_id'?)" in text
    assert "missing required 'workspaceId'" in text
    assert "Accepted parameters: workspaceId, content_id, limit (required: workspaceId)." in text


def test_a_name_a_server_does_not_list_passes_unless_its_schema_is_closed(tmp_path):
    _run(tmp_path, "remote", {"workspaceId": "acme", "expand": "body"})
    assert CALLED == [{"workspaceId": "acme", "expand": "body"}]  # the server may well take it

    closed = {**SERVER_SCHEMA, "additionalProperties": False}
    text, _ = _run(tmp_path, "remote", {"workspaceId": "acme", "content-id": "7"}, server_schema=closed)
    assert CALLED == []
    assert "unexpected 'content-id' (did you mean 'content_id'?)" in text and "missing" not in text

    # A closed schema with no properties is a tool that takes nothing.
    text, _ = _run(tmp_path, "remote", {"limit": 5}, server_schema={"type": "object", "properties": {},
                                                                    "additionalProperties": False})
    assert CALLED == [] and "unexpected 'limit'" in text


def test_a_name_is_matched_across_case_and_separators_before_spelling(tmp_path):
    # Spelling alone finds nothing near `CONTENT-ID`; the same name written another way does.
    text, _ = _run(tmp_path, "remote", {"CONTENT-ID": "7"})
    assert "unexpected 'CONTENT-ID' (did you mean 'content_id'?)" in text
