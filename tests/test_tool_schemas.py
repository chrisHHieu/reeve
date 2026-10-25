"""The tool schema overrides (tools/files.py, tool-layer audit).

The write tools faced the model with one-line descriptions; the new
descriptions follow a fixed rhetoric — capability, operational contract, failure modes
WITH remedies, when-not-to-use — and reeve now writes the same for the tools the
model uses most. What these tests pin:

  · the overrides ATTACH (via the same `_BORROWED_SCHEMAS` mechanism list_files used)
    and the registry sends them;
  · the parameter names still match the toolkit's signatures, so a call the model
    makes against the described schema binds to the real function — behavior is
    unchanged end-to-end (write → replace → read back through the registry);
  · the descriptions carry the house rhetoric (failure remedies, routing, contracts).
"""

from __future__ import annotations

import aisuite as ai

from harness.tools.files import apply_file_contracts
from harness.tools.files.schemas import BORROWED_SCHEMAS as _BORROWED_SCHEMAS
from harness.tools.registry import ToolRegistry


def _registered():
    registry = ToolRegistry()
    tools = apply_file_contracts(
        [t for t in ai.toolkits.files(root=".", allow_write=True)]
    )
    registry.register_all(tools)
    return registry


def test_the_five_schema_overrides_are_registered():
    assert set(_BORROWED_SCHEMAS) == {
        "write_file",
        "replace_in_file",
        "apply_patch",
        "apply_unified_diff",
        "list_files",
    }
    registry = _registered()
    schemas = {s["function"]["name"]: s for s in registry.schemas()}
    for name, override in _BORROWED_SCHEMAS.items():
        assert schemas[name] is override or schemas[name] == override, name


def test_parameter_names_match_the_toolkit_signatures():
    """A schema the model follows must bind to the function that runs. Each override's
    required/optional split matches the real signature exactly."""
    registry = _registered()
    specs = {name: registry.get(name) for name in _BORROWED_SCHEMAS}
    expected = {
        "write_file": {"path": True, "content": True, "overwrite": False},
        "replace_in_file": {
            "path": True,
            "old": True,
            "new": True,
            "expected_replacements": False,
        },
        "apply_patch": {"patch": True},
        "apply_unified_diff": {"diff": True},
    }
    for name, params in expected.items():
        schema = specs[name].schema["function"]
        props = schema["parameters"]["properties"]
        assert set(props) == set(params), name
        declared_required = set(schema["parameters"].get("required", []))
        for param, required in params.items():
            assert (param in declared_required) == required, (name, param)


def test_descriptions_carry_the_house_rhetoric():
    registry = _registered()
    schemas = {s["function"]["name"]: s["function"]["description"] for s in registry.schemas()}
    # Failure remedies, verbatim facts the model can act on:
    assert "Expected 1 replacement(s), found" in schemas["replace_in_file"]
    assert "File already exists" in schemas["write_file"]
    assert "*** Begin Patch" in schemas["apply_patch"]
    assert "/dev/null" in schemas["apply_unified_diff"]
    # Routing (when NOT to use / what to prefer):
    assert "replace_in_file" in schemas["write_file"]
    assert "write_file" in schemas["replace_in_file"]
    assert "NOT echoed back" in schemas["write_file"]


def test_write_then_replace_still_round_trips(tmp_path):
    """End-to-end through the registered tools — the same composition the catalog uses
    (toolkit minus replaced names, plus our reader): the override changed only what the
    model reads, not what the functions do."""
    from harness.tools.files import file_tools

    ws = tmp_path / "ws"
    ws.mkdir()
    replaced = {"search_files", "read_file", "read_file_lines"}
    registry = ToolRegistry()
    toolkit = apply_file_contracts(
        [t for t in ai.toolkits.files(root=str(ws), allow_write=True)]
    )
    registry.register_all([t for t in toolkit if getattr(t, "__name__", "") not in replaced])
    registry.register_all(file_tools(str(ws)))

    write = registry.get("write_file").func
    written = write(path="a/b.txt", content="alpha beta")
    assert written

    replace = registry.get("replace_in_file").func
    outcome = replace(path="a/b.txt", old="beta", new="gamma")
    assert outcome["replacements"] == 1

    read = registry.get("read_file").func
    assert "alpha gamma" in read(path="a/b.txt")["content"]


def test_read_and_search_descriptions_upgraded():
    registry = ToolRegistry()
    from harness.tools.files import file_tools
    from harness.tools.search import search_tools

    registry.register_all(file_tools("."))
    registry.register_all(search_tools("."))
    schemas = {s["function"]["name"]: s["function"]["description"] for s in registry.schemas()}
    # read_file routes away from shell and teaches read-before-edit:
    assert "shell cat" in schemas["read_file"]
    assert "BEFORE editing" in schemas["read_file"]
    # grep routes to read_file for context and names the syntax family:
    assert "read_file" in schemas["grep"]
    assert "Ripgrep" in schemas["grep"] or "ripgrep" in schemas["grep"].lower()
    # run_shell carries the PowerShell fact our own audit turned into an error path:
    from harness.tools.shell import _RUN_SHELL_SCHEMA

    assert "PowerShell 5.1" in _RUN_SHELL_SCHEMA["function"]["description"]
    assert "NON-ZERO exit is data" in _RUN_SHELL_SCHEMA["function"]["description"]
