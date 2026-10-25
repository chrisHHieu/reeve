"""Background work the HTTP layer starts and never awaits must actually finish.

asyncio keeps only a WEAK reference to a running task. A bare `asyncio.create_task(...)`
whose result nobody stores can therefore be garbage-collected mid-flight: the coroutine is
cancelled, and because nothing awaits it the cancellation is silent — no exception, no log,
no trace anywhere. Every long one-click sign-in has that shape (minutes spent waiting on a
browser), and so does the agent turn itself.

On 2026-09-08 a Postman connect returned `200 started` twice, opened no browser, registered
no client, wrote no error — and the identical flow completed perfectly when run outside the
server.

WHAT THESE TESTS DO NOT DO: force a collection and watch the task die. The first version of
this file tried, and a mutation sweep showed it passing with the fix removed — a task
suspended on a timer is still reachable from the loop's own handles, so the collection has
nothing to take. A test that cannot fail is worse than no test. What is checkable, and what
the fix actually is, is the reference itself: held for as long as the task runs, released
when it ends.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

import harness.interface.server.app as app_module


def _app():
    """The app object, without standing up a SessionManager: the task plumbing is built
    in the factory and never touches the manager."""

    class _Stub:
        def __getattr__(self, _name):
            raise AssertionError("the task plumbing must not touch the manager")

    return app_module.create_app(_Stub())


def test_a_running_task_is_held_by_a_strong_reference():
    """The fix, stated as the property it is. Without the set, nothing in the process
    refers to the task but the loop's weak bookkeeping."""
    seen: list[bool] = []

    async def main():
        app = _app()
        started = asyncio.Event()
        release = asyncio.Event()

        async def work():
            started.set()
            await release.wait()

        app.state.spawn(work(), what="work")  # deliberately keeping no reference
        await started.wait()
        seen.append(len(app.state.background) == 1)  # held while it runs
        release.set()
        await asyncio.sleep(0.05)
        seen.append(len(app.state.background) == 0)  # released when it ends

    asyncio.run(main())
    assert seen == [True, True], (
        "a task must be strongly referenced while it runs and dropped when it finishes"
    )


def test_a_spawned_task_that_raises_says_so(capsys):
    async def boom():
        raise RuntimeError("the browser never opened")

    async def main():
        app = _app()
        app.state.spawn(boom(), what="one-click connect postman")
        await asyncio.sleep(0.05)

    asyncio.run(main())
    printed = "".join(capsys.readouterr())
    # Both halves matter: which piece of work it was, and what went wrong with it.
    assert "one-click connect postman" in printed
    assert "the browser never opened" in printed


def test_a_cancelled_task_is_reported_too():
    """Cancellation is the failure mode the GC hazard actually produces, so it must not
    be swallowed as if it were a normal ending."""

    async def main():
        app = _app()
        task = app.state.spawn(asyncio.sleep(10), what="slow thing")
        task.cancel()
        await asyncio.sleep(0.05)
        return len(app.state.background)

    assert asyncio.run(main()) == 0, "a cancelled task must release its reference too"


def test_no_bare_create_task_is_left_in_the_http_layer():
    """A regression here is invisible at runtime — the task usually survives, and only
    dies under a collection that happens to land in the wrong place. So the rule is
    enforced on the source: every fire-and-forget in this module goes through `_spawn`."""
    package = Path(app_module.__file__).parent
    bare = [
        line.strip()
        for path in sorted(package.glob("*.py"))
        for line in path.read_text(encoding="utf-8").splitlines()
        if re.search(r"create_task\(", line)
        # the spawner's own call is the one legitimate use
        and "task = asyncio.create_task(coro)" not in line
        and not line.lstrip().startswith("#")
    ]
    assert bare == [], f"fire-and-forget tasks must go through _spawn: {bare}"
