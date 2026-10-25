"""`run_code` — the agent writes a short program instead of calling tools one at a time.

Measured on seven real sessions in this install: 364 tool calls, of which `run_shell` 113,
`replace_in_file` 64, `write_file` 56, `read_file` 33. Every one of those is a separate
model round-trip, and every result lands in the context window whole and stays there. A
loop over twelve files costs twelve round-trips and twelve file contents; the same loop
written as a program costs one round-trip and whatever the program chooses to print.

Code Mode. The load-bearing part
is not that code runs — `run_shell` already runs code — it is that the program can call the
AGENT'S OWN TOOLS. The workaround the agent invented for itself (write a script, then
`run_shell` it) can reach the operating system and nothing else: not `grep`, not
`web_search`, not a connector.

**Every sub-call is a real tool call.** It goes through the same authorization, the same
approval card, the same pipeline listeners and the same audit as a call the model made
directly. That is the whole safety argument, and it is why the broker lives in the engine
rather than here: a program that wanted to write a file would raise exactly the card it
raises today. `run_code` itself is classified EXEC — the program body is arbitrary Python,
which is the same thing `run_shell` already accepts, behind the same gate.

**The program runs in its OWN process** (`code_child.py`). The first version used `exec()`
inside reeve, and the argument above was incomplete because of it: `run_shell` gets a
separate process, so it can wreck the machine but cannot reach into the thing watching it,
while an in-process program could `import harness.safety.permissions` and switch the approval gate
off for the rest of the session — the one kind of damage that also removes the evidence.
A boundary the shell had and this did not was the wrong way round.

Every frame the child sends is validated AND REBUILT here before anything reads it, the way
The host treats every inbound frame as hostile: a forged field cannot ride
along on a frame that otherwise looks right. What is deliberately NOT included is the
lossless-number machinery — `hasUnsafeIntegerToken`, `BigInt` digit encoding, `-0` and
non-finite rejection. Those exist because JavaScript numbers are doubles and a large integer
rounds silently. Python has arbitrary-precision integers and `json` round-trips them, so
porting it would be defending against a failure this language does not have.

**Only the outer result enters the conversation.** Sub-calls are recorded as
`tool/code-dispatch` events so the log shows every one, and are deliberately NOT
appended to the message list — putting them there would spend the context this exists to
save.
"""

from __future__ import annotations

import collections
import json
import os
import queue
import shlex
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from . import cards
from .contract import ToolContract, ToolPresentation, attach_contract, clip
from harness.platform.spawn import spawn

#: A frame longer than this is not one this protocol produced. Bounded because the child is
#: model-controlled and an unbounded line is an unbounded allocation in the host.
MAX_FRAME_BYTES = 4 * 1024 * 1024

#: Tools a program may never call. The interactive ones are handled by the engine's own
#: branches rather than the dispatch path (they ask a human out-of-band and that answer IS
#: the consent), so a program calling one would reach a code path that does not exist for
#: it. `run_code` is excluded from itself: a program that can start a program is a stack
#: nobody bounded.
UNAVAILABLE = frozenset(
    {
        "run_code",
        "ask_user",
        "request_tool",
        "propose_plan",
        "update_plan",
    }
)

#: How long a whole program may run. Generous, because the point is to replace many calls
#: with one; bounded, because a program that never returns would hold the turn open.
DEFAULT_TIMEOUT = 180.0

#: Output kept from one program. Beyond this it is not a summary any more, and the agent
#: was asked to curate.
MAX_OUTPUT_CHARS = 20_000

#: A bound on sub-calls, so a runaway loop costs a bounded number of real tool calls rather
#: than every one it can reach before the deadline.
MAX_SUB_CALLS = 200


class _Done:
    """Returned by `next_request` when the program has FINISHED.

    A distinct object rather than `None`, because `None` already means "nothing yet, the
    wait expired" and a caller cannot act on both with one value: reading a timeout as the
    end abandons a program that was merely slow, and reading the end as a timeout waits out
    the budget on a program that already returned. The second is what happened — the test
    suite spent three minutes idling on it.
    """

    def __repr__(self) -> str:  # pragma: no cover - diagnostics only
        return "<program finished>"


DONE = _Done()


@dataclass
class SubCall:
    """One tool call the program asked for, and the way its answer gets back to it."""

    name: str
    arguments: dict
    wire_id: int = 0
    #: Set by `CodeProgram` when it hands the request out; `answer` writes through it.
    _send: Any = None

    def answer(self, value: Any, *, ok: bool = True) -> None:
        if self._send is not None:
            self._send(
                {"t": "reply", "id": self.wire_id, "ok": bool(ok), "value": value}
            )


def _validated_call(frame: dict) -> Optional[dict]:
    """A `call` frame rebuilt from scratch, or None when it is not one.

    Rebuilt rather than passed through: the child is model-controlled, so a frame that
    looks right must not be able to smuggle extra fields into whatever reads it next. Only
    these three values survive, and each has to be the right type.
    """
    if frame.get("t") != "call":
        return None
    call_id = frame.get("id")
    name = frame.get("name")
    args = frame.get("args")
    if not isinstance(call_id, int) or isinstance(call_id, bool):
        return None
    if not isinstance(name, str) or not name:
        return None
    if not isinstance(args, dict):
        return None
    return {"id": call_id, "name": name, "args": {str(k): v for k, v in args.items()}}


def _validated_done(frame: dict) -> Optional[dict]:
    """A `done` frame rebuilt the same way."""
    if frame.get("t") != "done":
        return None
    output = frame.get("output")
    error = frame.get("error")
    return {
        "output": output if isinstance(output, str) else "",
        "returned": frame.get("returned"),
        "error": error if isinstance(error, str) else "",
    }


def _validated_note(frame: dict) -> Optional[dict]:
    """A workflow narration frame (`phase`/`log`), rebuilt like every other frame."""
    if frame.get("t") != "note":
        return None
    kind = frame.get("kind")
    text = frame.get("text")
    if kind not in ("phase", "log") or not isinstance(text, str) or not text.strip():
        return None
    return {"kind": kind, "text": text[:2000]}


def child_command() -> list[str]:
    """The argv that runs the code-child protocol, resolved for THIS substrate.

    Three cases, in override order:

    - ``REEVE_CODE_CHILD`` — a full command line (shlex-split) for dev against
      another interpreter. Wins over everything.
    - Frozen (the PyInstaller desktop sidecar): ``sys.executable`` is
      ``reeve-server.exe``, whose argparse rejects ``-u -m`` and dies before the
      program's first line (measured in production — session 3c33bb70's stderr tail).
      The child is the sibling ``reeve-code-child[.exe]`` built for exactly this
      (packaging/reeve-server.spec), same JSON-lines protocol, no interpreter flags.
    - Everything else (dev, pytest, pip install): ``sys.executable`` IS a python.
    """
    override = os.environ.get("REEVE_CODE_CHILD", "").strip()
    if override:
        return shlex.split(override)
    if getattr(sys, "frozen", False):
        exe = Path(sys.executable).with_name(
            "reeve-code-child.exe" if sys.platform == "win32" else "reeve-code-child"
        )
        if not exe.exists():
            raise RuntimeError(
                "run_code is unavailable in this installation: the code-child runtime "
                f"({exe.name}) was not found next to the server binary. Reinstall or "
                "upgrade the app — or set REEVE_CODE_CHILD to a python that can run "
                "'-m harness.tools.code_child'."
            )
        return [str(exe)]
    return [sys.executable, "-u", "-m", "harness.tools.code_child"]


def child_env(argv: list[str]) -> dict[str, str]:
    """The child's environment. It runs in the session's workspace, which `-m` would put first on
    `sys.path`: a project with its own `json.py`, `queue.py` or `harness/` would then be imported in
    place of what the child needs. So a python child resolves its imports without the working
    directory (`PYTHONSAFEPATH`, 3.11+) and finds this package where it is loaded from, the same
    whichever folder it starts in. The frozen child and an override are left as they are."""
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    if argv[:1] != [sys.executable] or getattr(sys, "frozen", False):
        return env
    env["PYTHONSAFEPATH"] = "1"
    package_root = str(Path(__file__).resolve().parents[2])
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = package_root + (os.pathsep + existing if existing else "")
    return env


class CodeProgram:
    """One model-written program, running in its own process, brokering its tool calls.

    The API is the one the engine already drives — `start`, `next_request`, `answer` on the
    request, `result` — so moving the program out of this process changed nothing above it.
    """

    def __init__(
        self,
        code: str,
        *,
        names: frozenset,
        signatures: Optional[dict[str, str]] = None,
        timeout: Optional[float] = None,
        max_sub_calls: Optional[int] = None,
        notes: bool = False,
        prelude: Optional[str] = None,
        restricted: bool = False,
        cwd: Optional[str] = None,
    ) -> None:
        self.code = code
        # Where the program runs: the session's workspace, as `run_shell`. It was the server's own
        # working directory, so a program that wrote `open("out.csv", "w")` put the file wherever
        # the server was started (a probe's `_inspect.py` landed in this repo's root, 2026-10-09)
        # and a relative read found nothing the person could see. None: no local workspace.
        self.cwd = cwd if cwd and Path(cwd).is_dir() else os.getcwd()
        # Resolved HERE, not as a default argument. A default is bound when the function is
        # defined, so `DEFAULT_TIMEOUT` would be frozen at import and a caller changing it
        # would change nothing — which is precisely what happened: a test that believed it
        # was proving a 1-second deadline sat through the real 180-second one and passed.
        self.timeout = DEFAULT_TIMEOUT if timeout is None else timeout
        self.max_sub_calls = MAX_SUB_CALLS if max_sub_calls is None else max_sub_calls
        self._names = frozenset(n for n in names if n not in UNAVAILABLE)
        # The signatures ride the private channel to the child, never the prompt: the
        # program asks for one with `tools.describe(name)` when it needs it.
        self._signatures = {
            str(name): str(text)
            for name, text in (signatures or {}).items()
            if name in self._names
        }
        self._requests: "queue.Queue[Optional[SubCall]]" = queue.Queue()
        self._proc: Optional[subprocess.Popen] = None
        self._reader: Optional[threading.Thread] = None
        self._stderr_reader: Optional[threading.Thread] = None
        self._done: dict = {}
        self._calls_made = 0
        self._overrun = False
        self._lock = threading.Lock()
        # The child's stderr, as a bounded tail. Python-level tracebacks travel the
        # protocol (code_child catches them and sends a done frame), so stderr normally
        # says nothing — but a NATIVE death (a C extension hard-crash, a broken install)
        # kills the process before any frame, and the traceback Windows prints goes
        # here. The runtime captures both pipes as a "backstop" for exactly
        # this; DEVNULL, the old arrangement, discarded the only evidence a death
        # without a frame ever leaves.
        self._stderr_tail: "collections.deque[str]" = collections.deque(maxlen=80)
        #: Workflow narration (`phase`/`log`): the child sends `note` frames, the engine
        #: drains them into the turn's event stream. A plain `run_code` program has no
        #: prelude and the start frame tells the child notes are off.
        self._notes_enabled = bool(notes)
        self._notes: "queue.Queue[dict]" = queue.Queue()
        #: A workflow's trusted helper block, executed before the body, and whether the
        #: body runs with the restricted builtins (no open/import/eval/...).
        self._prelude = str(prelude or "")
        self._restricted = bool(restricted)

    # -- lifecycle -----------------------------------------------------------------

    def start(self) -> None:
        argv = child_command()
        self._proc = spawn(
            argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=self.cwd,
            env=child_env(argv),
        )
        self._write(
            {
                "code": self.code,
                "names": sorted(self._names),
                "signatures": self._signatures,
                "notes": self._notes_enabled,
                "prelude": self._prelude,
                "restricted": self._restricted,
            }
        )
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()
        self._stderr_reader = threading.Thread(target=self._drain_stderr, daemon=True)
        self._stderr_reader.start()

    def _drain_stderr(self) -> None:
        """Keep the last pages of the child's stderr. A dying process can write more
        than any tail needs; `maxlen` bounds both the memory and the report."""
        proc = self._proc
        try:
            assert proc is not None and proc.stderr is not None
            for raw in proc.stderr:
                self._stderr_tail.append(raw.decode("utf-8", errors="replace"))
        except (OSError, ValueError, AssertionError):
            return

    def _stderr_note(self) -> str:
        """The stderr tail as one reportable string, or "" when the child said nothing."""
        text = "".join(self._stderr_tail).strip()
        if not text:
            return ""
        return f"\n[child stderr tail]\n{text[-2000:]}"

    def _write(self, frame: dict) -> None:
        proc = self._proc
        if proc is None or proc.stdin is None:
            return
        try:
            proc.stdin.write((json.dumps(frame, default=str) + "\n").encode("utf-8"))
            proc.stdin.flush()
        except (OSError, ValueError):
            pass  # the child is gone; the reader will see EOF and end the run

    def _read_loop(self) -> None:
        """Turn the child's frames into requests. Every frame is validated and rebuilt."""
        proc = self._proc
        try:
            assert proc is not None and proc.stdout is not None
            for raw in proc.stdout:
                if len(raw) > MAX_FRAME_BYTES:
                    continue
                try:
                    frame = json.loads(raw.decode("utf-8", errors="replace"))
                except ValueError:
                    continue
                if not isinstance(frame, dict):
                    continue
                done = _validated_done(frame)
                if done is not None:
                    self._done = done
                    return
                note = _validated_note(frame)
                if note is not None:
                    self._notes.put(note)
                    continue
                call = _validated_call(frame)
                if call is None:
                    continue
                with self._lock:
                    self._calls_made += 1
                    over = self._calls_made > self.max_sub_calls
                    self._overrun = self._overrun or over
                if over:
                    self._write(
                        {
                            "t": "reply",
                            "id": call["id"],
                            "ok": False,
                            "value": (
                                f"this program has already made {self.max_sub_calls} "
                                "tool calls, which is the limit"
                            ),
                        }
                    )
                    continue
                self._requests.put(
                    SubCall(name=call["name"], arguments=call["args"], wire_id=call["id"])
                )
        except (OSError, ValueError, AssertionError):
            return
        finally:
            self._requests.put(None)  # the child is finished; stop the broker loop

    def stop(self) -> None:
        """End the program. Unlike the in-process version this really does end it — the
        child is a process, and a process can be killed."""
        proc = self._proc
        if proc is None:
            return
        try:
            proc.kill()
        except OSError:
            pass

    # -- the engine's side ----------------------------------------------------------

    def drain_notes(self) -> list[dict]:
        """Whatever workflow narration arrived since the last drain (never blocks)."""
        out: list[dict] = []
        while True:
            try:
                out.append(self._notes.get_nowait())
            except queue.Empty:
                return out

    def next_request(self, timeout: float):
        """The next tool call the program wants, `DONE` when it has finished, or `None`
        when the wait expired with neither.

        Three answers rather than two, because a caller cannot act on "finished" and
        "nothing yet" with one value: reading a timeout as the end abandons a program that
        was merely slow, and reading the end as a timeout waits out the whole budget on a
        program that already returned.

        Blocking, and meant to be awaited through `asyncio.to_thread` so the event loop
        stays free to serve the approval card this may be waiting for.
        """
        try:
            item = self._requests.get(timeout=timeout)
        except queue.Empty:
            return None
        if item is None:
            return DONE
        item._send = self._write  # the reply goes back down the same pipe
        return item

    @property
    def finished(self) -> bool:
        proc = self._proc
        return proc is None or proc.poll() is not None

    def result(self, *, timed_out: bool = False) -> dict:
        """What the model reads. Printed output first, because that is what it curated."""
        # The stderr drainer races the death report: the process can be dead while its
        # last bytes still sit in the pipe (the pipe is drained before finalizing
        # for the same reason). A short bounded join, not a guess.
        if self._stderr_reader is not None:
            self._stderr_reader.join(timeout=1.0)
        done = self._done
        text = str(done.get("output") or "")
        if len(text) > MAX_OUTPUT_CHARS:
            dropped = len(text) - MAX_OUTPUT_CHARS
            text = text[:MAX_OUTPUT_CHARS] + f"\n…[{dropped:,} more characters]"
        with self._lock:
            made = min(self._calls_made, self.max_sub_calls)
            overrun = self._overrun
        out: dict[str, Any] = {"output": text, "tool_calls": made}
        returned = done.get("returned")
        if returned is not None:
            out["returned"] = returned
        error = str(done.get("error") or "")
        if error:
            out["error"] = error
        if overrun and not error:
            out["error"] = (
                f"this program tried to make more than {self.max_sub_calls} tool calls, "
                "which is the limit"
            )
        if timed_out:
            out["error"] = (
                f"the program was still running after {self.timeout:g}s and was stopped; "
                "anything it printed before that is above"
            )
        elif not done and not error:
            # No `done` frame and no deadline: the child died without saying anything —
            # killed, crashed in a native import, or exited on a forged frame it could
            # not recover from. The OS's exit code is the one fact that separates those
            # cases, and the stderr tail is usually the ONLY evidence a native death
            # leaves (the traceback never reaches the protocol when the process dies
            # under it).
            proc = self._proc
            code = proc.poll() if proc is not None else None
            where = f" (exit code {code})" if code is not None else ""
            note = self._stderr_note()
            out["error"] = (
                f"the program ended without returning a result{where}"
                + (f"{note}" if note else "")
            )
        return out


def _plain(value: Any) -> Any:
    """A returned value the message list can hold. Anything exotic becomes its repr —
    a program returning a live object is describing it, not handing it over."""
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    return repr(value)


# -- the model-facing tool -------------------------------------------------------------


def run_code_schema() -> dict:
    """The tool schema. It names no tools.

    The description once listed every tool a program may call, so a program would not
    reach for one that is not there and fail halfway. On a loaded install that was 306
    names, a quarter of the tool list on every request, and a second copy of what the
    request's own tool list and `tool_search`'s index already say. The names a program
    uses are checked before it starts instead (`tools/code_names.py`).

    What the program's Python holds is said up front. The sentence it replaced told the
    model to switch to run_shell "if a run dies with no output", but both import failures
    on the session store (pandas, playwright) printed a full traceback. In the desktop app
    the child carries the whole standard library and no third-party package
    (`packaging/child_stdlib.py`; before it, 48 of 195 modules were missing, sqlite3 among
    them, and `scripts/verify_frozen_sidecar.py` now proves the set).
    """
    return {
        "type": "function",
        "function": {
            "name": "run_code",
            "description": (
                "Run a short Python program that calls your own tools, instead of calling "
                "them one at a time. Use it when the same kind of work repeats — reading "
                "or checking many files, filtering a big result down to what matters. "
                "Call a tool as `tools.name(argument=value)`; it returns the tool's result "
                "as a direct call shows it, with engine-only keys such as `_card_meta` "
                "stripped (a file never arrives twice). A result is a dict, a list or text "
                "depending on the tool, and reads both ways: a dict or list also answers "
                "string methods (`.splitlines()`, `.strip()`) and `.text` with its JSON, and "
                "text also answers `.get(key)` / `['key']` when it is a JSON object. A dict "
                "carrying an `error` key means the tool reported the call as failed — check "
                "it; a call that is REFUSED or raises raises RuntimeError inside your "
                "program, so wrap it in try/except when you want to carry on. To run "
                "independent calls together, await them: `a, b = await asyncio.gather("
                "tools.x(...), tools.y(...))` (top-level await works; reads and searches "
                "overlap, a write or shell call still runs alone and in order). "
                "`print(...)` and a top-level "
                "`return` are your output, and ONLY your output enters the conversation — "
                "so summarise rather than dumping. Each tool call inside still asks for "
                "approval exactly as it would outside. The program runs in Reeve's own "
                "Python, not the `python` that run_shell finds: the standard library is "
                "there (json, re, csv, sqlite3, statistics, datetime, asyncio), "
                "third-party packages (pandas, numpy, playwright, plotting libraries) are "
                "not. For those, write the script to a file and run it with run_shell. A "
                "program may "
                "call any tool you can call directly, and any deferred tool, by the same "
                "name (`dir(tools)` lists them); the tools that ask the user a question "
                "are not callable from a program. A program naming a tool that does not "
                "exist is refused before it starts, so nothing runs halfway."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {
                        "type": "string",
                        "description": "The program body (top-level `return` works).",
                    },
                    "description": {
                        "type": "string",
                        "description": "One short line: what this program does.",
                    },
                },
                "required": ["code", "description"],
            },
        },
    }


#: JSON Schema type → the Python hint a program author reads.
_TYPE_NAMES = {
    "string": "str",
    "integer": "int",
    "number": "float",
    "boolean": "bool",
    "array": "list",
    "object": "dict",
}


def _param_type(spec: Any) -> str:
    """The Python hint for one JSON Schema parameter, `Any` when it is not knowable."""
    if not isinstance(spec, dict):
        return "Any"
    kind = spec.get("type")
    return _TYPE_NAMES.get(str(kind), "Any") if not isinstance(kind, list) else "Any"


def tool_signatures(schemas: list[dict]) -> dict[str, str]:
    """One compact `name(args)` + description per callable tool, for `tools.describe`.

    These travel to the program over the child's private channel, NOT in the prompt: a
    program asks for the few signatures it needs without a model round-trip.
    """
    out: dict[str, str] = {}
    for schema in schemas:
        fn = schema.get("function") if isinstance(schema, dict) else None
        if not isinstance(fn, dict):
            continue
        name = fn.get("name")
        if not isinstance(name, str) or not name or name in UNAVAILABLE or name == "run_code":
            continue
        params = fn.get("parameters") if isinstance(fn.get("parameters"), dict) else {}
        properties = params.get("properties") if isinstance(params.get("properties"), dict) else {}
        required = set(params.get("required") or [])
        parts = [
            f"{param}: {_param_type(spec)}"
            if param in required
            else f"{param}: {_param_type(spec)} | None = None"
            for param, spec in properties.items()
        ]
        description = str(fn.get("description") or "").strip().split("\n", 1)[0]
        out[name] = f"{name}({', '.join(parts)})\n{description}"
    return out


def run_code_tool() -> object:
    """The schema carrier. The engine intercepts `run_code` (it needs the sub-call
    round-trip through authorization), so this body runs only where no engine does — a
    headless surface, a scripted test — and says so rather than pretending to run code.
    """
    from aisuite.agents import ToolMetadata, tool

    def run_code(code: str, description: str = "") -> dict:
        return {
            "error": "run_code needs the turn engine; it is not available in this surface"
        }

    wrapped = tool(
        run_code,
        metadata=ToolMetadata(
            category="execute",
            # High, and it must be: the program body is arbitrary Python. The same thing
            # `run_shell` accepts, behind the same gate — `risk.EXEC_TOOLS` names it.
            risk_level="high",
            capabilities=["code_mode"],
            description="Run a short Python program that calls your own tools.",
        ),
    )
    wrapped.__tool_schema__ = run_code_schema()
    attach_contract(wrapped, RUN_CODE_CONTRACT)
    return wrapped


# -- what a person sees (tools/contract.py) -------------------------------------
# The CALL card carries the program itself, so the row can show the code that is about to
# run, which is what a person is being asked to consent to. The RESULT card carries what it
# printed and returned (`_code_present_result`); what it called is the sub-call rows.


def _code_present_call(arguments: dict[str, Any]) -> ToolPresentation:
    described = clip(arguments.get("description"), 160)
    code = arguments.get("code")
    text = code if isinstance(code, str) else ""
    summary = described or "Run a program"
    lines = len(text.splitlines())
    return ToolPresentation(
        summary=summary,
        detail=f"{lines} line{'' if lines == 1 else 's'}" if text else "",
        kind="execute",
        card=cards.generic_call(summary, kind="execute", raw_input=text or None),
    )


def _code_render(arguments: dict[str, Any], value: Any) -> str:
    """What the program printed, then its return value, then a status line.

    The JSON form wraps the program's whole output in `{"output","tool_calls",…}`; the
    output (and any `return`) is what the call was for, and the status line carries the
    sub-call count and the failure text the engine attaches.
    """
    if not isinstance(value, dict):
        return json.dumps(value, default=str)
    lines: list[str] = []
    output = value.get("output")
    if isinstance(output, str) and output:
        lines.append(output)
    if value.get("returned") is not None:
        lines.append(f"returned: {json.dumps(value['returned'], default=str, ensure_ascii=False)}")
    notes: list[str] = []
    calls = value.get("tool_calls")
    if isinstance(calls, int):
        notes.append(f"{calls} tool call{'' if calls == 1 else 's'}")
    error = value.get("error")
    if isinstance(error, str) and error:
        notes.append(error)
    if notes:
        lines.append("[" + "; ".join(notes) + "]")
    return "\n".join(lines) if lines else "(no output)"


def _code_present_result(arguments: dict[str, Any], value: Any) -> ToolPresentation:
    """The program's printed output and return value, as a terminal card.

    The note above once held: no result card, because "the durable result text renders
    underneath". The GUI never rendered it, so a program's output — the reason it ran —
    showed nowhere (GUI tools review, B4). The sub-call rows still say what the program
    CALLED; this card says what it PRINTED and returned.
    """
    described = clip(arguments.get("description"), 160) or "Run a program"
    if not isinstance(value, dict):
        return ToolPresentation(summary=described, kind="execute")
    parts: list[str] = []
    output = value.get("output")
    if isinstance(output, str) and output:
        parts.append(output.rstrip("\n"))
    if value.get("returned") is not None:
        parts.append(f"returned: {json.dumps(value['returned'], default=str, ensure_ascii=False)}")
    error = value.get("error")
    if isinstance(error, str) and error:
        parts.append(error)
    return ToolPresentation(
        summary=described,
        kind="execute",
        card=cards.terminal_result(
            output="\n".join(parts) if parts else "(no output)",
            exit_code=1 if isinstance(error, str) and error else 0,
        ),
    )


def _code_facts(arguments: dict[str, Any], value: Any) -> dict[str, Any]:
    """What the log records about a program: refused before it started for
    naming a tool that is not there (and how many), failed, or returned; and the tool
    calls it made. `harness_review` counts `unknown-tool-programs` from this."""
    if not isinstance(value, dict):
        return {}
    unknown = value.get("unknown_tools")
    if isinstance(unknown, list) and unknown:
        return {"outcome": "unknown_tool", "unknown": len(unknown)}
    calls = value.get("tool_calls") if isinstance(value.get("tool_calls"), int) else None
    return {"outcome": "error" if value.get("error") else "ok", "calls": calls}


RUN_CODE_CONTRACT = ToolContract(
    render=_code_render,
    present_call=_code_present_call,
    present_result=_code_present_result,
    facts=_code_facts,
)
