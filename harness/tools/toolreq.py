"""`request_tool`: the agent asks the person for a command-line tool it needs and does not have.

A sibling of `ask_user`. The turn engine catches the call and raises TOOL_REQUESTED, and the person
answers outside the turn: install the pinned build, or go on without it, knowingly. The function
here carries the schema, and answers for a surface where nobody can be asked.

It was made for one failure. Without gitleaks, a security review left out its scan of git history
for secrets and said nothing: the check did not fail, it disappeared from the report. A missing
tool must become a choice the person sees, never a hole nobody notices.
"""

from __future__ import annotations

import json

from aisuite.agents import ToolMetadata, tool

from .contract import ToolContract, attach_contract


def _request_tool_render(arguments: dict, value: object) -> str:
    """One line: which pinned tool was installed, or why not."""
    if not isinstance(value, dict):
        return json.dumps(value, default=str)
    name = str(value.get("name") or arguments.get("name") or "the tool")
    if value.get("installed"):
        version = str(value.get("version") or "").strip()
        return f"installed {name} ({version})" if version else f"installed {name}"
    why = str(value.get("error") or value.get("reason") or "").strip()
    return f"Error: {why}" if why else f"{name} was not installed"


def request_tool_tool() -> object:
    def request_tool(name: str, reason: str) -> dict:
        """Ask the person to install a tool from the PINNED catalog that you need and that is not
        on this machine. The catalog is small and closed, today `gitleaks`, `trivy` and
        `osv-scanner`, each installed at a fixed version whose checksum is verified.

        Any other missing command (semgrep, jq, kubectl, …) is NOT for this tool: install it with
        the shell yourself (brew, pip, …), which asks for approval like any command, or do without.

        `reason` is ONE sentence: the check that needs the tool. What the person is shown already
        says what the install is (version, publisher, checksum) and what declining means; do not
        repeat any of it in `reason`.

        Call this INSTEAD of dropping a check without a word. If the person declines, use a
        fallback (reading the git history yourself, say, where gitleaks would have scanned it) and
        say plainly in your report which checks were weaker and why.
        """
        return {"installed": False, "error": "tool requests aren't available in this surface"}

    metadata = ToolMetadata(
        category="system",
        risk_level="low",
        capabilities=["request_tool"],
        description="Ask the person to install a command-line tool that is missing, instead of quietly skipping the check that needs it.",
    )
    wrapped = tool(request_tool, metadata=metadata)
    attach_contract(wrapped, REQUEST_TOOL_CONTRACT)
    return wrapped


REQUEST_TOOL_CONTRACT = ToolContract(render=_request_tool_render)
