"""What a `run_code` program knows of the tools it may call, and how its failures read.

The tool list goes on the wire as function definitions; `run_code` names none of them (ADR
0119). A program reads a tool's arguments from the signatures that ride its child's private
channel (`tools.describe`, a local lookup), and its failures come back as sentences.
"""

from __future__ import annotations

from harness.tools.code_mode import tool_signatures
from harness.tools.registry import ToolRegistry


def _schema(name, *, description="does a thing", properties=None, required=()):
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties or {},
                "required": list(required),
            },
        },
    }


def _registry(*names):
    reg = ToolRegistry()
    for name in names:

        def fn(**kwargs):
            return {}

        fn.__name__ = name
        fn.__tool_schema__ = _schema(name)
        reg.register(fn)
    return reg


def test_tool_signatures_carry_arguments_and_the_description():
    sigs = tool_signatures(
        [
            _schema(
                "read_file",
                description="Read a file from disk.",
                properties={"path": {"type": "string"}},
                required=("path",),
            ),
            _schema(
                "list_files",
                description="List files.",
                properties={"path": {"type": "string"}, "limit": {"type": "integer"}},
            ),
            _schema("ask_user", description="out of band"),
        ]
    )
    assert sigs["read_file"] == "read_file(path: str)\nRead a file from disk."
    assert sigs["list_files"].startswith(
        "list_files(path: str | None = None, limit: int | None = None)"
    )
    assert "ask_user" not in sigs


def test_tools_describe_is_a_local_lookup_in_the_child():
    from harness.tools.code_child import _Tools

    tools = _Tools(
        lambda name, args: None,
        {"read_file"},
        {"read_file": "read_file(path: str)\nRead a file from disk."},
    )
    assert tools.describe("read_file").startswith("tools.read_file(path: str)")
    assert "no tool named" in tools.describe("nope")
    try:
        tools.nope  # noqa: B018
    except AttributeError as exc:
        assert "tools.describe" in str(exc)
    else:  # pragma: no cover - the raise is the point
        raise AssertionError("an unknown tool did not raise")


def test_a_signature_mistake_comes_back_with_the_signature():
    """The failure the model cannot act on is a bare "missing 1 required positional
    argument"; the child already holds the signature, so it appends it — no prompt cost."""
    from harness.tools.code_child import _Tools

    def send(name, args):
        raise RuntimeError(
            "memory_tools.<locals>.memory_read() missing 1 required positional argument: 'memory_ids'"
        )

    tools = _Tools(
        send, {"memory_read"}, {"memory_read": "memory_read(memory_ids: list)\nRead memories."}
    )
    try:
        tools.memory_read()  # noqa: B018
    except RuntimeError as exc:
        assert "Signature: tools.memory_read(memory_ids: list)" in str(exc)
        assert "tools.describe" in str(exc)
    else:  # pragma: no cover - the raise is the point
        raise AssertionError("the enriched error was not raised")

    def refuse(name, args):
        raise RuntimeError("file already exists and you have not read it in this session")

    other = _Tools(refuse, {"write_file"}, {"write_file": "write_file(path: str)\nWrite."})
    try:
        other.write_file()
    except RuntimeError as exc:
        assert "Signature:" not in str(exc)  # a refusal is not a shape problem
    else:  # pragma: no cover
        raise AssertionError("the refusal was not raised")


def test_a_sub_call_failure_reads_as_a_message_not_a_dict_repr():
    """The host answers a refused sub-call with an error envelope; `str()` of that dict
    reached the model as a Python repr with escaped backslashes — measured in a real session,
    where every sub-call failure read that way."""
    from harness.tools.code_child import _failure_text

    assert (
        _failure_text({"error": "missing 1 required positional argument", "error_type": "TypeError"})
        == "TypeError: missing 1 required positional argument"
    )
    # A policy refusal is already a sentence; naming the generic wrapper adds noise.
    assert (
        _failure_text({"error": "tool call blocked: read it first", "error_type": "RuntimeError"})
        == "tool call blocked: read it first"
    )
    assert _failure_text("plain text") == "plain text"
    assert _failure_text(None) == "None"


def test_run_program_reports_exit_instead_of_dying_silently():
    from harness.tools.code_child import _Tools, run_program

    tools = _Tools(lambda name, args: None, set(), {})
    returned, error = run_program("import sys\nsys.exit(3)", tools)
    assert returned is None
    assert "exit()" in error and "3" in error

    returned, error = run_program("return 42", tools)
    assert returned == 42 and error == ""

    _, error = run_program("1 / 0", tools)
    assert "ZeroDivisionError" in error


def test_a_silent_child_death_reports_the_exit_code():
    from harness.tools.code_mode import CodeProgram

    class _DeadProc:
        def poll(self):
            return 3

    program = CodeProgram("pass", names=frozenset())
    program._proc = _DeadProc()
    assert "exit code 3" in program.result()["error"]


def test_every_tool_is_on_the_wire_and_callable_directly(tmp_path):
    """One presentation: every registered tool is a function definition the model may call by
    name, `run_code` among them."""
    from harness.loop import TurnEngine
    from harness.safety.permissions import PermissionEngine

    reg = _registry("read_file", "run_code", "ask_user")
    assert {s["function"]["name"] for s in reg.schemas()} == {"read_file", "run_code", "ask_user"}
    assert reg.directly_callable() == frozenset({"read_file", "run_code", "ask_user"})
    engine = TurnEngine(
        provider=object(), registry=reg, permissions=PermissionEngine(workspace_root=tmp_path), model="x"
    )
    assert engine._direct_call_refusal("read_file") is None
    assert engine._direct_call_refusal("run_code") is None


def test_build_engine_offers_run_code_beside_the_tools_it_can_call(tmp_path):
    from harness.compose import build_engine
    from harness.modes.agents import reeve_agent

    engine = build_engine(agent=reeve_agent(), workspace=tmp_path, provider=object())
    names = {s["function"]["name"] for s in engine.registry.schemas()}
    assert "run_code" in names and "read_file" in names
    assert "Tools callable from a `run_code` program" not in engine.messages[0]["content"]

