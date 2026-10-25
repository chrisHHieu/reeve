"""What each file tool declares about its output: how it reads, how it is shown, and how long it may
take.

`write_file` and its siblings come from `ai.toolkits.files(...)`, so there is no function here to
decorate. That is the shape most of the registry takes (40 connectors, MCP tools, aisuite toolkits),
and `attach_contract` is how a contract reaches a function this codebase did not write. The toolkit
builds fresh closures on each call, so annotating them changes nobody else's object, and
`apply_file_contracts` is called on the list the catalog has just built.
"""

from __future__ import annotations

from ..contract import READ_ONLY_CAPS, WORKSPACE_WRITE_CAPS, ToolContract, attach_contract
from .present import (
    _listfiles_present_call,
    _listfiles_present_result,
    _patch_present_call,
    _patch_present_result,
    _read_present_call,
    _read_present_result,
    _replace_present_call,
    _replace_present_result,
    _write_present_call,
    _write_present_result,
)
from .renders import _listfiles_render, _patch_render, _read_render, _replace_render
from .schemas import BORROWED_SCHEMAS

#: name -> contract, for tools built elsewhere. `apply_patch` and `apply_unified_diff`
#: share a presenter because they differ only in the patch syntax they accept.
#:
#: Every budget here sits far above what the operation actually costs and far below the
#: runtime ceiling (`tools/deadline.py`). These tools do blocking file I/O with no internal
#: deadline of their own — which is precisely the case the ceiling exists to catch. A
#: tighter number here is still worth having: it turns a stalled mount into a report in
#: seconds instead of letting the ceiling take twelve minutes to say the same thing.
BORROWED_CONTRACTS: dict[str, ToolContract] = {
    "write_file": ToolContract(
        present_call=_write_present_call,
        present_result=_write_present_result,
        timeout_ms=120_000,
        capabilities=WORKSPACE_WRITE_CAPS,
    ),
    "replace_in_file": ToolContract(
        render=_replace_render,
        present_call=_replace_present_call,
        present_result=_replace_present_result,
        timeout_ms=120_000,
        capabilities=WORKSPACE_WRITE_CAPS,
    ),
    "apply_patch": ToolContract(
        render=_patch_render,
        present_call=_patch_present_call,
        present_result=_patch_present_result,
        timeout_ms=180_000,
        capabilities=WORKSPACE_WRITE_CAPS,
    ),
    "apply_unified_diff": ToolContract(
        render=_patch_render,
        present_call=_patch_present_call,
        present_result=_patch_present_result,
        timeout_ms=180_000,
        capabilities=WORKSPACE_WRITE_CAPS,
    ),
    "list_files": ToolContract(
        render=_listfiles_render,
        present_call=_listfiles_present_call,
        present_result=_listfiles_present_result,
        timeout_ms=60_000,
        capabilities=READ_ONLY_CAPS,
    ),
}

#: `read_file` is a closure built per workspace, so its contract is attached where the
#: tool is, not in the borrowed table above.
READ_FILE_CONTRACT = ToolContract(
    render=_read_render,
    present_call=_read_present_call,
    present_result=_read_present_result,
    timeout_ms=60_000,
    capabilities=READ_ONLY_CAPS,
    # Windowed under `_MAX_TOTAL_CHARS` with the continuation named, so the aggregate
    # budget must never stub it (see `ToolContract.self_bounded`).
    self_bounded=True,
)



def apply_file_contracts(tools: list) -> list:
    """Annotate the aisuite file tools in `tools` in place, and return the same list."""
    for tool in tools:
        name = getattr(tool, "__name__", "")
        contract = BORROWED_CONTRACTS.get(name)
        if contract is not None:
            attach_contract(tool, contract)
        schema = BORROWED_SCHEMAS.get(name)
        if schema is not None:
            # The same mechanism `todo_write` uses: an explicit schema beats the one derived from
            # the signature and docstring (registry/registration.py).
            tool.__tool_schema__ = schema
    return tools
