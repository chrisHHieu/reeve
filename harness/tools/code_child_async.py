"""A `run_code` program that awaits its tool calls (code_child.py's second shape).

Many models write `r = await tools.x(...)` — the async style of other code-mode harnesses.
The bridge is synchronous, so that program died on "'await' outside async function":
session b3a8491e-9db (2026-09-28) spent three runs on it before finding the plain call. A
program that awaits now compiles as a coroutine and runs with an awaitable view of the same
bridge; a program that does not is compiled and run exactly as before.
"""

from __future__ import annotations

from typing import Any

#: The compile errors that mean "this program awaits" rather than "this program is wrong".
_ASYNC_ONLY = (
    "'await' outside",
    "'async for' outside",
    "'async with' outside",
    "asynchronous comprehension outside",
)


def compile_program(body: str) -> tuple[Any, bool]:
    """`(code object defining __program__(tools), is_async)` for an indented body."""
    try:
        return compile("def __program__(tools):\n" + body, "<run_code>", "exec"), False
    except SyntaxError as exc:
        if not any(marker in str(exc.msg or "") for marker in _ASYNC_ONLY):
            raise
    return compile("async def __program__(tools):\n" + body, "<run_code>", "exec"), True


def run_async(program: Any, tools: Any) -> Any:
    """Run an async `__program__` to completion against an awaitable view of `tools`."""
    import asyncio

    return asyncio.run(program(AwaitableTools(tools)))


class AwaitableTools:
    """`tools` for a program that awaits: each tool call is a coroutine that makes the same
    call on a worker thread. `describe` stays a plain function: it is a local lookup, and a
    program that forgets to await it should still get text back.

    The call runs on a thread so that awaited calls really overlap. Each one
    blocks until the host answers its id, and the child routes replies by id. Before, the
    coroutine made the blocking call itself, so `asyncio.gather` over ten reads made ten
    calls one after another. The host decides what may actually run together: a write
    still runs alone."""

    def __init__(self, tools: Any) -> None:
        self._tools = tools

    def __dir__(self) -> list:
        return dir(self._tools)

    def describe(self, name: str) -> str:
        return self._tools.describe(name)

    def __getattr__(self, name: str):
        call = getattr(self._tools, name)

        async def awaitable(*values: Any, **arguments: Any) -> Any:
            import asyncio

            return await asyncio.to_thread(call, *values, **arguments)

        awaitable.__name__ = name
        return awaitable
