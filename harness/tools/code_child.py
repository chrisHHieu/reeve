"""The child process a `run_code` program runs in.

Started as `python -u -m harness.tools.code_child`. It reads the program source, runs it,
and brokers every tool call back to reeve over a private channel.

**Why a child at all.** The first version ran the program with `exec()` inside reeve.
That put model-written code in the same process as the permission engine and the API keys
held in memory — so a program could `import harness.safety.permissions` and switch the approval
gate off for the rest of the session. `run_shell` cannot do that: it gets its own process,
so it can wreck the machine but cannot reach inside the thing watching it. A boundary that
`run_shell` has and `run_code` did not was the wrong way round.

**The channel.** The Python runtime gets a dedicated fd 3 so the program's own stdout
stays free (`packages/code-runtime/code-runtime-python`). Same idea, different mechanism,
because fd 3 is a POSIX arrangement and this has to run on Windows: at startup the real
stdout is duplicated to a private descriptor and fd 1 is pointed at the null device. The
protocol writes to the private copy; anything the program prints — including a direct
`os.write(1, ...)` — goes nowhere near it.

**Frames are not trusted in the other direction either.** The host validates and rebuilds
everything this sends (see `code_mode.py`). Nothing here assumes it is believed.
"""

from __future__ import annotations

import io
import json
import os
import queue
import sys
import threading
import traceback
from typing import Any

# Stdlib-only, like this module: the program's process must not load the host's policy code.
from .code_child_args import _bind_positional
from .code_child_async import compile_program, run_async
from .code_child_values import wrap
from .code_child_sandbox import _restricted_builtins

#: Every frame is one JSON object on one line. A line longer than this is not a frame this
#: protocol produced, and the host drops it rather than trying to parse a partial one.
MAX_FRAME_BYTES = 4 * 1024 * 1024


def _private_channel() -> int:
    """Take the real stdout for the protocol and blind fd 1.

    Both halves matter. Duplicating gives the protocol a descriptor the program cannot
    reach by name; pointing fd 1 at the null device means even a program that writes to the
    raw descriptor cannot inject a frame. What it prints through `print` is captured
    separately and returned as its output.
    """
    protocol = os.dup(1)
    null = os.open(os.devnull, os.O_WRONLY)
    os.dup2(null, 1)
    os.close(null)
    return protocol


#: Frames leave over one descriptor; concurrent requests would otherwise interleave
#: their bytes on POSIX pipes (writes over PIPE_BUF are not guaranteed atomic).
_SEND_LOCK = threading.Lock()


def _send(fd: int, frame: dict) -> None:
    line = json.dumps(frame, default=str, ensure_ascii=False) + "\n"
    with _SEND_LOCK:
        os.write(fd, line.encode("utf-8"))


class _HostGone(Exception):
    """The host closed the channel: there is nobody left to answer a call."""


def _receive() -> dict:
    """One reply frame from the host. EOF means the host gave up on us."""
    line = sys.stdin.readline()
    if not line:
        raise _HostGone
    return json.loads(line)


#: Phrases that mark an error as "the call did not match the tool's signature" rather than
#: the tool refusing the work. Only those get the signature appended.
_SIGNATURE_TROUBLE = (
    "required positional argument",
    "unexpected keyword argument",
    "positional argument",
)


def _signature_hint(name: str, exc: Exception, signatures: dict) -> Exception:
    """The same error, with the tool's signature appended when the shape was the problem.

    The SDK lists tool NAMES and the model asks for detail with `tools.describe`; when it
    guesses instead, the failure is a bare `missing 1 required positional argument`. The
    signature is already in this process, so attaching it turns a dead end into a fixable
    one at no prompt cost.
    """
    hint = signatures.get(name)
    text = str(exc)
    if hint and any(marker in text for marker in _SIGNATURE_TROUBLE):
        return RuntimeError(
            f"{text}\nSignature: tools.{hint}\nCall tools.describe(name) before using a tool."
        )
    return exc


class _Tools:
    """`tools` inside the program: attribute access posts a call and blocks on the answer."""

    def __init__(self, send: Any, names: set, signatures: dict, note: Any = None) -> None:
        self._send = send
        self._names = names
        self._signatures = signatures
        #: Children that carry the workflow prelude have a narration channel; a plain
        #: `run_code` program does not (the start frame says so).
        self._wf_send_note = note

    def _wf_note(self, kind: str, text: str) -> None:
        if self._wf_send_note is not None:
            self._wf_send_note(str(kind or "log"), str(text))

    def __dir__(self) -> list:
        return sorted(self._names)

    def describe(self, name: str) -> str:
        """A tool's arguments and one-line description.

        A LOCAL lookup: the signatures arrived on the private channel with the program,
        so asking costs no model round-trip and no tool call. This is what lets the
        prompt name no tools at all.
        """
        text = self._signatures.get(name)
        if text is None:
            available = ", ".join(sorted(self._signatures)) or "(none)"
            return f"no tool named {name!r}. Available: {available}"
        return f"tools.{text}"

    def __getattr__(self, name: str):
        if name.startswith("_"):
            raise AttributeError(name)
        if name not in self._names:
            available = ", ".join(sorted(self._names)) or "(none)"
            raise AttributeError(
                f"no tool named {name!r}. Available: {available}. "
                "Call tools.describe(name) for a tool's arguments."
            )

        def call(*values: Any, **arguments: Any) -> Any:
            if values:
                arguments = _bind_positional(name, values, arguments, self._signatures)
            try:
                return self._send(name, arguments)
            except Exception as exc:
                raise _signature_hint(name, exc, self._signatures) from exc

        call.__name__ = name
        return call


def _exit_error(exc: SystemExit) -> str:
    """What the model reads when its own program called exit()."""
    code = exc.code if isinstance(exc.code, int) else 0
    return (
        f"the program called exit()/sys.exit() (code {code!r}); a run_code program must not "
        "exit — end the program naturally, and `return` or `print` the value you want back"
    )


def _failure_text(value: Any) -> str:
    """The host's error envelope as a message the model can act on.

    A refused sub-call comes back as `{"error": ..., "error_type": ...}`, and `str()` of
    that dict reached the model as a Python repr — `{'error': "...", 'error_type':
    'PermissionError'}`, backslashes escaped — which is noise to read and impossible to
    match on. Measured in a real session: every sub-call failure read that way.
    """
    if isinstance(value, dict):
        text = value.get("error")
        if isinstance(text, str) and text:
            kind = value.get("error_type")
            # `RuntimeError` is the host's generic wrapper for a policy refusal whose text is
            # already a sentence; naming the wrapper would only add noise.
            if isinstance(kind, str) and kind and kind != "RuntimeError":
                return f"{kind}: {text}"
            return text
    return str(value)


def run_program(
    code: str,
    tools: "_Tools",
    *,
    prelude: str = "",
    restricted: bool = False,
) -> tuple[Any, str]:
    """Run the model's program and return `(returned, error_text)`.

    An optional PRELUDE runs first, in its own namespace with full builtins (it is ours,
    and its imports are the helpers' — `threading`, `json`); the model-written body then
    runs in a namespace seeded with the helpers, and — for a workflow — with the
    restricted builtins above. `SystemExit` raised by the PROGRAM is reported, not
    propagated: letting it through killed the child without a done frame, and the model
    then saw only "the program ended without returning a result" — a dead end it could
    not act on. `_HostGone` still propagates, because the host closed the channel and
    there is nobody to answer.
    """
    try:
        namespace: dict = {}
        if prelude:
            # ONE dict for globals and locals: with two, `def`s land in locals while the
            # functions' `__globals__` is the (nearly empty) globals — every helper then
            # NameErrors on its own siblings.
            helpers: dict = {}
            exec(  # noqa: S102 - the prelude is ours, not model text
                compile(prelude, "<workflow-prelude>", "exec"), helpers
            )
            helpers["tools"] = tools
            namespace.update(helpers)
        if restricted:
            namespace["__builtins__"] = _restricted_builtins()
        else:
            # `await asyncio.gather(...)` is how a program runs calls together;
            # models reach for it without importing it.
            import asyncio

            namespace.setdefault("asyncio", asyncio)
        # A program that awaits its tool calls runs as a coroutine (code_child_async.py).
        program, asynchronous = compile_program(_indent(code or "pass"))
        exec(program, namespace)  # noqa: S102
        if asynchronous:
            return run_async(namespace["__program__"], tools), ""
        return namespace["__program__"](tools), ""
    except _HostGone:
        raise
    except SystemExit as exc:
        return None, _exit_error(exc)
    except BaseException:
        # The traceback is the useful part: the model wrote this code, and handing it back
        # verbatim is what lets the next attempt be right.
        return None, traceback.format_exc(limit=6)


def main() -> None:
    protocol = _private_channel()
    start = _receive()
    code = str(start.get("code") or "")
    prelude = str(start.get("prelude") or "")
    restricted = bool(start.get("restricted"))
    names = {str(n) for n in start.get("names") or []}
    signatures = {str(k): str(v) for k, v in (start.get("signatures") or {}).items()}
    notes = bool(start.get("notes"))

    captured = io.StringIO()
    # The program's own `print` — and anything else that reaches `sys.stdout`, since fd 1
    # is now the null device and this is the only writable stdout left.
    sys.stdout = captured
    sys.__stdout__ = captured  # type: ignore[misc]

    next_id = [0]
    # Replies are routed BY ID so a program may have several calls in flight at once
    # (the workflow fan-out). One reader thread owns stdin and hands each reply to the
    # request that is waiting on its id; the old shape — send, then read "the next"
    # reply — made concurrency impossible and would have silently paired the wrong
    # answer with the wrong call.
    pending: dict[int, "queue.Queue[dict]"] = {}
    pending_lock = threading.Lock()

    def _route(reply: dict) -> None:
        call_id = reply.get("id")
        with pending_lock:
            slot = pending.get(call_id) if isinstance(call_id, int) else None
        if slot is not None:
            slot.put(reply)

    def _host_gone() -> None:
        with pending_lock:
            slots = list(pending.values())
        for slot in slots:
            slot.put({"t": "gone"})

    def _reader() -> None:
        while True:
            try:
                reply = _receive()
            except _HostGone:
                _host_gone()
                return
            except Exception:
                _host_gone()
                return
            _route(reply)

    reader = threading.Thread(target=_reader, name="code-child-reader", daemon=True)
    reader.start()

    def request(name: str, arguments: dict) -> Any:
        with pending_lock:
            next_id[0] += 1
            call_id = next_id[0]
            slot: "queue.Queue[dict]" = queue.Queue(maxsize=1)
            pending[call_id] = slot
        _send(
            protocol,
            {"t": "call", "id": call_id, "name": name, "args": arguments},
        )
        reply = slot.get()
        with pending_lock:
            pending.pop(call_id, None)
        if reply.get("t") == "gone":
            raise _HostGone
        if not reply.get("ok"):
            # Raised inside the program so the model can catch it: a refused write does not
            # have to end a run that had already done useful work.
            raise RuntimeError(_failure_text(reply.get("value")))
        # Typed for both readings (code_child_values.py): a dict that also reads as text,
        # a text that also reads as a JSON object.
        return wrap(reply.get("value"))

    def _note(kind: str, text: str) -> None:
        _send(
            protocol,
            {"t": "note", "kind": str(kind or "log"), "text": str(text)[:2000]},
        )

    returned: Any = None
    error = ""
    try:
        returned, error = run_program(
            code,
            _Tools(request, names, signatures, note=_note if notes else None),
            prelude=prelude,
            restricted=restricted,
        )
    except _HostGone:
        raise SystemExit(0) from None

    _send(
        protocol,
        {
            "t": "done",
            "output": captured.getvalue(),
            "returned": _plain(returned),
            "error": error,
        },
    )


def _indent(code: str) -> str:
    return "\n".join("    " + line for line in code.splitlines()) or "    pass"


def _plain(value: Any) -> Any:
    """A value the wire can carry. Anything exotic becomes its repr — a program returning a
    live object is describing it, not handing it over."""
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    return repr(value)


if __name__ == "__main__":
    main()
