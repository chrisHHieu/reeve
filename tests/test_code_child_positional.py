"""A program may call a tool positionally, or with one arguments dict.

Found by the live discovery A/B (deepseek-v4.1-flash, 2026-09-24): a program wrote
`tools.mcp__wealthlog__get_accounts({})` — the shape `tool_call` and every JSON-schema
tool use — and got `call() takes 0 positional arguments but 1 was given`, raised BEFORE the
signature hint could attach. It took five more programs to find the keyword form. The
proxy now accepts the two shapes a model writes (one arguments dict; values in the
signature's parameter order) and refuses anything else WITH the signature.
"""

from __future__ import annotations

import pytest

from harness.tools.code_child import _Tools

SIGNATURES = {
    "read_file": "read_file(path: str, start_line: int | None = None, max_lines: int | None = None)\nRead a file.",
    "get_accounts": "get_accounts(include_closed: bool | None = None)\nList accounts.",
    "configure": "configure(options: dict)\nTakes one dict parameter.",
}


def _tools():
    sent: list[tuple[str, dict]] = []
    tools = _Tools(lambda name, args: sent.append((name, args)) or "ok", set(SIGNATURES), SIGNATURES)
    return tools, sent


def test_one_arguments_dict_is_the_arguments():
    tools, sent = _tools()
    tools.get_accounts({})
    tools.read_file({"path": "a.py", "max_lines": 5})
    assert sent == [("get_accounts", {}), ("read_file", {"path": "a.py", "max_lines": 5})]


def test_positional_values_follow_the_signature_order():
    tools, sent = _tools()
    tools.read_file("a.py")
    tools.read_file("a.py", 10, max_lines=5)
    assert sent == [
        ("read_file", {"path": "a.py"}),
        ("read_file", {"path": "a.py", "start_line": 10, "max_lines": 5}),
    ]


def test_a_dict_that_is_not_an_arguments_object_is_the_first_parameter():
    tools, sent = _tools()
    tools.configure({"retries": 3})  # `retries` is not a parameter name: it is the value
    assert sent == [("configure", {"options": {"retries": 3}})]


def test_too_many_values_or_a_duplicate_is_refused_with_the_signature():
    tools, sent = _tools()
    with pytest.raises(TypeError) as extra:
        tools.get_accounts(True, False)
    assert "tools.get_accounts(include_closed" in str(extra.value)
    with pytest.raises(TypeError) as twice:
        tools.read_file("a.py", path="b.py")
    assert "path" in str(twice.value) and "tools.read_file(" in str(twice.value)
    assert sent == []  # nothing reached the host


def test_positional_calls_work_through_the_real_child_process():
    """The same shapes, end to end: a real child, the private channel, the host's answer."""
    from harness.tools.code_mode import DONE, CodeProgram

    program = CodeProgram(
        "print(tools.get_accounts({})['args'], tools.read_file('a.py', 3)['args'])",
        names=frozenset({"get_accounts", "read_file"}),
        signatures=SIGNATURES,
    )
    program.start()
    seen = []
    while True:
        request = program.next_request(timeout=10)
        if request is DONE:
            break
        if request is None:
            continue
        seen.append((request.name, request.arguments))
        request.answer({"args": request.arguments})
    out = program.result()
    assert seen == [("get_accounts", {}), ("read_file", {"path": "a.py", "start_line": 3})]
    assert "{} {'path': 'a.py', 'start_line': 3}" in out["output"], out


def test_a_program_that_awaits_its_tool_calls_runs():
    # Session b3a8491e-9db (2026-09-28): `r = await tools.mcp__wealthlog__get_accounts(...)`
    # — the async style other code-mode harnesses use — died on "'await' outside async
    # function", and two more programs went into asyncio before the sync form was found.
    from harness.tools.code_child import run_program

    tools, sent = _tools()
    returned, error = run_program(
        'r = await tools.get_accounts(include_closed=True)\nreturn r + "!"', tools
    )
    assert error == "" and returned == "ok!"
    assert sent == [("get_accounts", {"include_closed": True})]

    # gather works, and describe stays a plain call inside an awaiting program.
    returned, error = run_program(
        "import asyncio\n"
        'a, b = await asyncio.gather(tools.read_file("a.py"), tools.read_file("b.py"))\n'
        'return [a, b, tools.describe("configure").startswith("tools.configure")]',
        tools,
    )
    assert error == "" and returned == ["ok", "ok", True]


def test_a_program_without_await_is_untouched_and_a_real_syntax_error_still_reports():
    from harness.tools.code_child import run_program

    tools, sent = _tools()
    returned, error = run_program('return tools.get_accounts({})', tools)
    assert error == "" and returned == "ok"

    _, error = run_program("return (", tools)
    assert "SyntaxError" in error
