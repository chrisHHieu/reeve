"""Read-only/plan mode does not advertise write tools.

The boundary guard refuses `write_file` in read-only mode anyway, so sending its schema
spent tokens and bought a refusal round trip. Argument-dependent tools stay: `run_shell`
can still run `git status`, and hiding it would break legitimate read-only work.

The registry is stubbed rather than built: this pins the FILTER, not the builtin
catalogue (which `test_catalog.py` owns).
"""

from __future__ import annotations

from harness.loop import TurnEngine
from harness.safety.permissions import Mode, PermissionEngine
from harness.llm.providers import ModelCapabilities, ProviderClient
from harness.tools import ToolRegistry

_SCHEMAS = [
    {"type": "function", "function": {"name": "write_file", "parameters": {}}},
    {"type": "function", "function": {"name": "replace_in_file", "parameters": {}}},
    {"type": "function", "function": {"name": "read_file", "parameters": {}}},
    {"type": "function", "function": {"name": "run_shell", "parameters": {}}},
]


class _Idle(ProviderClient):
    def stream(self, **kwargs):
        raise AssertionError("no request is made here")

    def complete(self, **kwargs):
        raise AssertionError("no request is made here")

    def capabilities(self, model):
        return ModelCapabilities()


def _engine(tmp_path, mode):
    engine = TurnEngine(
        provider=_Idle(),
        registry=ToolRegistry(),
        permissions=PermissionEngine(workspace_root=tmp_path, mode=mode),
        model="gpt-5.5",
    )
    engine.registry.schemas = lambda: list(_SCHEMAS)
    engine.registry.schemas_by_group = lambda: [(None, s) for s in _SCHEMAS]
    return engine


def _names(engine) -> set:
    return {schema["function"]["name"] for schema in engine._model_tools()}


def test_read_only_hides_write_tools_but_keeps_argument_dependent_ones(tmp_path):
    names = _names(_engine(tmp_path, Mode.READ_ONLY))

    assert "write_file" not in names
    assert "replace_in_file" not in names
    assert "read_file" in names
    assert "run_shell" in names


def test_the_plan_overlay_hides_them_too(tmp_path):
    engine = _engine(tmp_path, Mode.WORKSPACE_WRITE)
    engine.permissions.plan = True

    assert "write_file" not in _names(engine)


def test_workspace_write_advertises_everything(tmp_path):
    names = _names(_engine(tmp_path, Mode.WORKSPACE_WRITE))

    assert {"write_file", "replace_in_file", "read_file", "run_shell"} <= names


def test_the_context_estimate_agrees_with_the_advertised_list(tmp_path):
    """The panel prices the request that is actually sent, so a hidden tool must not
    count toward the estimate either."""
    messages = [{"role": "user", "content": "hi"}]

    full = _engine(tmp_path, Mode.WORKSPACE_WRITE)
    read_only = _engine(tmp_path, Mode.READ_ONLY)

    assert (
        read_only._context_breakdown(messages, tools_active=True)["system_tools"]
        < full._context_breakdown(messages, tools_active=True)["system_tools"]
    )
