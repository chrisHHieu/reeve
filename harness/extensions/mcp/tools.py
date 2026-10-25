"""An MCP server's tools, made into functions the tool registry can hold.

The registry calls tools synchronously, and the engine already runs each call on a worker thread;
the MCP session, though, lives on the server's event loop. So each tool becomes a plain function
that hands its call to that loop (`run_coroutine_threadsafe`) and waits. It carries the schema the
server declared (`inputSchema`, sent to the model as is), metadata the permission engine reads
(category `mcp`, approval as configured) and a contract for how a call reads on a card.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
from concurrent.futures import TimeoutError as FuturesTimeoutError
from dataclasses import replace
from typing import Any, Awaitable, Callable, Optional

import aisuite as ai

from harness.tools.contract import ToolCapabilities, ToolContract, ToolPresentation, attach_contract

from .client import CALL_TIMEOUT
from .config import MCPServerDef

CallAsync = Callable[[str, dict[str, Any]], Awaitable[Any]]

#: The registry group of every MCP tool. Servers are added, removed, switched off and signed in
#: again while conversations are open; an open engine's tools follow by syncing this one group
#: (`ToolRegistry.sync_group`), never by being built again.
MCP_GROUP = "mcp"

_NOT_IN_A_NAME = re.compile(r"[^a-zA-Z0-9_-]")
_NAME_LIMIT = 64  # what providers accept for a function name
_KEPT_BEFORE_HASH = 55
_HEADLINE_CHARS = 100
_DESCRIPTION_CHARS = 1024


def tool_name(server: str, tool: str) -> str:
    """`mcp__<server>__<tool>`, with anything outside `[A-Za-z0-9_-]` made `_`.

    A name over 64 characters keeps its first 55 and ends `_` plus 8 hex characters of the whole
    name's sha256. Cutting it short alone gave two tools of one long-named server the same name,
    and the second quietly took the first's place; plugin servers (`plugin_<plugin>_<server>`)
    make long names common (design §5.5). These names are stored in grants and logs,
    so the rule does not change."""
    full = f"mcp__{_NOT_IN_A_NAME.sub('_', server)}__{_NOT_IN_A_NAME.sub('_', tool)}"
    if len(full) <= _NAME_LIMIT:
        return full
    return f"{full[:_KEPT_BEFORE_HASH]}_{hashlib.sha256(full.encode('utf-8')).hexdigest()[:8]}"


def _headline(description: str, remote: str, server: str) -> str:
    """What the approval card says the call does: the first sentence of the tool's own
    description, cut to one line. The server's author is the only one who knows what a
    third-party tool does; without their sentence the card can only name the tool."""
    first = re.split(r"(?<=[.!?])\s", description.strip(), maxsplit=1)[0].strip()
    if not first:
        return f"Run {remote} on {server}"
    return first if len(first) <= _HEADLINE_CHARS else first[: _HEADLINE_CHARS - 1].rstrip() + "…"


def _contract(description: str, remote: str, server: str) -> ToolContract:
    """How a call reads, and how long it may take; no `kind`, on purpose.

    `kind` is what lets a surface tell the person an action stays on their computer, and an MCP
    server may be a bridge to local files or a write to someone else's tracker. Unset, the card
    admits it does not know; set by guess, it would reassure someone about to consent, wrongly.
    """
    headline = _headline(description, remote, server)
    return ToolContract(
        present_call=lambda arguments: ToolPresentation(summary=headline),
        # The last of three limits. `MCPManager.call` cancels at `CALL_TIMEOUT`, the thread in
        # `build_callables` gives up ten seconds after, and this trips only when both have
        # stuck. The one that can really stop the request goes first. A server nobody here
        # wrote gets a budget of its own instead of the runtime's ceiling.
        timeout_ms=int(CALL_TIMEOUT * 1000) + 60_000,
    )


def _schema(name: str, mcp_tool: Any) -> dict[str, Any]:
    parameters = getattr(mcp_tool, "inputSchema", None) or {"type": "object", "properties": {}}
    description = (getattr(mcp_tool, "description", None) or "")[:_DESCRIPTION_CHARS]
    return {"type": "function", "function": {"name": name, "description": description, "parameters": parameters}}


def _offered(mcp_tools: list[Any], server: MCPServerDef) -> list[Any]:
    """The server's tools less what its config leaves out (`include_tools`, `exclude_tools`)."""
    only = None if server.include_tools is None else set(server.include_tools)
    never = set(server.exclude_tools or ())
    return [tool for tool in mcp_tools if (only is None or tool.name in only) and tool.name not in never]


def _annotated_capabilities(mcp_tool: Any) -> Optional[ToolCapabilities]:
    """The tool's MCP annotations (`readOnlyHint`, `destructiveHint`, `idempotentHint`) as
    capabilities, or None when it declares none, which leaves the engine to judge.

    They are the server's word about its own tool, and are used for one thing: a read-only,
    idempotent tool that destroys nothing may run beside other calls. They never lift an
    approval: a server calling itself read-only does not turn a person's check into an auto-run,
    so `needs_approval` stays with the server's config."""
    annotations = getattr(mcp_tool, "annotations", None)
    hint = lambda name: bool(getattr(annotations, name, False))  # noqa: E731
    read_only, destructive, idempotent = hint("readOnlyHint"), hint("destructiveHint"), hint("idempotentHint")
    if annotations is None or not (read_only or destructive or idempotent):
        return None
    return ToolCapabilities(
        read_only=read_only,
        destructive=destructive,
        concurrent_safe=read_only and idempotent,
        side_effect_scope="none" if read_only else "network",
    )


def _needs_approval(server: MCPServerDef, mcp_tool: Any) -> bool:
    """Whether a call waits for a person. As the server is configured, except for a server whose
    descriptor gates by hints: there a tool marked `readOnlyHint` runs and every other one asks,
    a tool that says nothing included, since nobody has said it is safe to run unwatched."""
    if server.approval_by_hints:
        return not bool(getattr(getattr(mcp_tool, "annotations", None), "readOnlyHint", False))
    return server.requires_approval


def _bridge(server: str, remote: str, call_async: CallAsync, loop: asyncio.AbstractEventLoop, timeout: float) -> Callable[..., Any]:
    """A function that runs `remote` on the server loop and waits for it, from a worker thread."""

    def call(_remote: str = remote, **arguments: Any) -> Any:
        pending = asyncio.run_coroutine_threadsafe(call_async(_remote, arguments), loop)
        try:
            return pending.result(timeout)
        except FuturesTimeoutError:
            # On 3.11 this is the builtin TimeoutError, which `MCPManager.call` also raises when
            # it cancels at its own deadline. A finished future is that case, the tool's own
            # answer, and goes up untouched: reading it as this layer's timeout once turned the
            # true "the request was cancelled" into the wrong story (seen 2026-09-17).
            if pending.done():
                raise
            # Still running: the manager's deadline did not fire in its ten seconds, so its loop
            # is stuck. Waiting longer stops nothing, so ask for the cancel here, and say this
            # was the thread giving up: the cancel happens only once that loop moves again.
            pending.cancel()
            raise TimeoutError(
                f"MCP tool {server}/{_remote} did not answer within {timeout:g}s — the thread-side "
                "backstop fired (the cancelling deadline did not); cancellation was requested"
            ) from None

    return call


def build_callables(
    server: MCPServerDef,
    mcp_tools: list[Any],
    call_async: CallAsync,
    loop: asyncio.AbstractEventLoop,
    *,
    timeout: float = CALL_TIMEOUT + 10.0,
) -> list[Callable[..., Any]]:
    """One registry function for each tool `server` offers."""
    made: list[Callable[..., Any]] = []
    for mcp_tool in _offered(mcp_tools, server):
        name, remote = tool_name(server.name, mcp_tool.name), mcp_tool.name
        description = getattr(mcp_tool, "description", None) or ""
        call = _bridge(server.name, remote, call_async, loop, timeout)
        # Set by hand rather than through `ai.tool`, which would try to read a schema off the
        # `**arguments` signature; the registry reads these attributes directly.
        call.__name__ = name
        call.__doc__ = description or f"MCP tool {remote} from {server.name}"
        call.__aisuite_tool_metadata__ = ai.ToolMetadata(
            name=name,
            category="mcp",
            risk_level="medium",
            capabilities=[server.name],
            requires_approval=_needs_approval(server, mcp_tool),
        )
        call.__tool_schema__ = _schema(name, mcp_tool)
        call.__tool_group__ = MCP_GROUP
        # The one kind of tool with no upper bound on how many there are, so the one deferred:
        # with discovery on, its schema leaves the request until `tool_search` loads it.
        call.__tool_defer__ = True
        # The card is built from the tool's own description, not `__doc__`, whose fallback
        # ("MCP tool x from y") is jargon to a person and would hide the plainer one in `_contract`.
        attach_contract(call, replace(_contract(description, remote, server.name), capabilities=_annotated_capabilities(mcp_tool)))
        made.append(call)
    return made
