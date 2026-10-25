"""`reeve exec` — one task, no interface.

A harness should be drivable by a script, as `claude -p` and `codex exec` are,
`pi -p`. Reeve could only be driven through its server or by someone sitting at the TUI, so it
could not be called from CI, from a cron line, from another agent, or by an outside benchmark: a
harness that only a person can start can never be part of a loop (lecture 13).

This is the product's own engine, built the way the product builds it (`SessionManager.get_engine`:
the session tools, the log, the memory, MCP), run for one prompt and stopped. Nothing here is a
second code path; it is a surface, like the TUI and the GUI.

  - The final answer goes to **stdout**, progress to **stderr**, so `reeve exec "…" > answer.md`
    works. `--json` writes one JSON event per line to stdout instead (the same events the WebSocket
    carries, minus the streamed deltas, which are display-only) and ends with a `result` line.
  - Nobody answers. A call that needs approval is **denied** (fail closed) unless its tool was named
    with `--allow-tool`; `ask_user`, folder requests and plans are unavailable, exactly as on any
    headless surface. `--mode danger-full-access` is the explicit way to say "this box is disposable".
  - The session is saved like any other, so the GUI shows it and `--resume ID` continues it.

Exit codes (the turn's own vocabulary, `harness/session/turns.py`):
  0  completed      — finished, and there was nothing unproven about it
  1  failed         — the model route failed, the run was interrupted, or a bad setup
  2  stopped        — a rail ended it first: step or token budget, repetition, a check loop
  3  not proven     — it said done, but changed code and ran nothing, or the last check is red,
                      or the answer was cut off
  64 usage          — the command line itself was wrong
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from pathlib import Path
from typing import Any, Optional, TextIO

EXIT_OK, EXIT_FAILED, EXIT_STOPPED, EXIT_UNPROVEN, EXIT_USAGE = 0, 1, 2, 3, 64

#: Display-only events: the finished message carries the same text, so a machine reader skips them.
_DELTAS = frozenset({"assistant_delta", "reasoning_delta", "tool_call_delta", "tool_output_delta"})

MODES = ("read-only", "workspace-write", "danger-full-access")


class _Parser(argparse.ArgumentParser):
    def error(self, message: str):  # argparse exits 2, which here means "stopped by a rail"
        self.print_usage(sys.stderr)
        print(f"reeve exec: {message}", file=sys.stderr)
        raise SystemExit(EXIT_USAGE)


def build_parser(default_model: str = "") -> argparse.ArgumentParser:
    parser = _Parser(
        prog="reeve exec",
        description="Run one task with the Reeve engine and print the result. No interface, no questions.",
    )
    parser.add_argument("prompt", nargs="?", default=None, help='the task; "-" or nothing reads it from stdin')
    parser.add_argument("--cwd", default=".", help="workspace directory")
    parser.add_argument("--model", default=default_model or None, help="model id, e.g. gemini:gemini-3.6-flash")
    parser.add_argument("--mode", default="workspace-write", choices=MODES, help="permission preset")
    parser.add_argument("--allow-tool", action="append", default=[], metavar="TOOL",
                        help="approve this tool when it asks (repeatable); everything else that asks is denied")
    parser.add_argument("--max-steps", type=int, default=None, help="stop after this many model steps")
    parser.add_argument("--token-budget", type=int, default=None, help="stop once the turn has used this many tokens")
    parser.add_argument("--resume", default=None, metavar="SESSION", help="continue a saved session")
    parser.add_argument("--json", action="store_true", dest="as_json", help="one JSON event per line on stdout")
    parser.add_argument("--output-last-message", default=None, metavar="FILE", help="also write the final answer here")
    parser.add_argument("--quiet", action="store_true", help="no progress on stderr")
    parser.add_argument("--no-mcp", action="store_true", help="do not start MCP servers")
    return parser


def _read_prompt(args: argparse.Namespace, stdin: TextIO) -> Optional[str]:
    if args.prompt not in (None, "-"):
        return args.prompt
    if args.prompt is None and stdin.isatty():
        return None
    text = stdin.read().strip()
    return text or None


def _short(value: Any, limit: int = 90) -> str:
    text = " ".join(str(value).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _progress(event_type: str, data: dict[str, Any]) -> Optional[str]:
    """One stderr line for the events a person watching a log wants, or None."""
    if event_type == "tool_proposed":
        args = data.get("arguments") if isinstance(data.get("arguments"), dict) else {}
        subject = next((args[k] for k in ("command", "path", "pattern", "query", "url") if args.get(k)), "")
        return f"• {data.get('name', 'tool')} {_short(subject)}".rstrip()
    if event_type == "tool_finished" and data.get("status") not in (None, "ok"):
        return f"  ↳ {data.get('status')}: {_short(data.get('reason') or '')}".rstrip(": ")
    if event_type == "compacted":
        return "• context compacted"
    if event_type == "retrying":
        return f"• retrying ({data.get('attempt')}/{data.get('of')})"
    if event_type == "model_fallback":
        return f"• switched model: {data.get('from')} → {data.get('to')}"
    if event_type == "error":
        return f"error: {_short(data.get('error') or data, 240)}"
    return None


def exit_code(end: Optional[dict[str, Any]], *, failed: bool) -> int:
    """The exit status for how the turn ended. `end` is the `turn_end` payload, if there was one."""
    if failed or end is None:
        return EXIT_FAILED
    if end.get("status") != "completed":
        return EXIT_STOPPED
    if end.get("unverified") or end.get("check_failed") or end.get("truncated"):
        return EXIT_UNPROVEN
    return EXIT_OK


async def run_exec(
    args: argparse.Namespace,
    prompt: str,
    *,
    provider: Any = None,
    out: TextIO = sys.stdout,
    err: TextIO = sys.stderr,
    state_dir: Optional[Path] = None,
) -> int:
    from harness.config.config import load_config
    from harness.loop import ApprovalOutcome
    from harness.safety.permissions import Mode
    from harness.safety.secrets import SecretStore, state_dir as default_state_dir
    from .server.manager import SessionManager
    from .server.manager.helpers import last_assistant_text

    workspace = Path(args.cwd).expanduser().resolve()
    if not workspace.is_dir():
        print(f"reeve exec: not a directory: {workspace}", file=err)
        return EXIT_USAGE
    cfg = load_config()
    model = args.model or cfg.model
    if provider is None:
        from harness.llm.providers.router import ProviderRouter

        provider = ProviderRouter(SecretStore())

    allowed = set(args.allow_tool or [])

    async def approver(request: Any) -> Any:
        name = getattr(request, "tool_name", "")
        if name in allowed:
            return ApprovalOutcome.ONCE
        if not args.quiet and not args.as_json:
            print(f"  ↳ denied (needs approval; use --allow-tool {name}): {name}", file=err)
        return ApprovalOutcome.DENY

    manager = SessionManager(
        workspace=workspace, data_dir=state_dir or default_state_dir(), model=model,
        mode=Mode(args.mode), provider=provider,
    )
    session_id = args.resume or uuid.uuid4().hex[:12]
    engine = manager.get_engine(session_id, workspace=str(workspace), approver=approver)
    if engine is None:
        print(f"reeve exec: could not start session {session_id}", file=err)
        return EXIT_FAILED
    # Nobody answers a headless run: leave the asks unwired so each tool says "not available here" and
    # the model goes on, instead of parking in an Inbox no one is watching.
    for asker in ("question_asker", "plan_approver", "tool_requester"):
        setattr(engine, asker, None)
    engine.set_mode(Mode(args.mode), reason="exec")
    if args.max_steps:
        engine.max_iterations = args.max_steps
    if args.token_budget:
        engine.token_budget = args.token_budget

    end: Optional[dict[str, Any]] = None
    failed = False
    from harness.interface.tui.transcript import remember_visual

    shown: dict[str, Any] = {}  # the visuals the run showed, by name, for the answer's text
    try:
        if not args.no_mcp:
            try:
                await manager.arm_mcp(session_id, workspace=str(workspace), agent="reeve")
            except Exception as exc:  # a server that will not start must not cost the task
                if not args.quiet and not args.as_json:
                    print(f"• mcp unavailable: {_short(exc)}", file=err)
        async for event in engine.run(prompt):
            kind = event.type.value
            if kind == "turn_end":
                end = dict(event.data)
            elif kind == "tool_finished":
                remember_visual(event.data, shown)
            elif kind in ("error", "interrupted"):
                failed = True
            if args.as_json:
                if kind not in _DELTAS:
                    print(json.dumps({"type": kind, "data": event.data}, ensure_ascii=False, default=str), file=out)
            elif not args.quiet:
                line = _progress(kind, event.data)
                if line:
                    print(line, file=err)
    finally:
        try:
            manager.save(session_id, engine)
        except Exception as exc:
            print(f"reeve exec: could not save the session: {_short(exc)}", file=err)
        try:
            await manager.aclose()
        except Exception:
            pass

    from harness.tools.visual import plain_visual_markers

    # stdout draws no visual: a placing line becomes the visual written as text.
    text = plain_visual_markers(last_assistant_text(engine.messages) or "", shown)
    code = exit_code(end, failed=failed)
    if args.output_last_message:
        Path(args.output_last_message).write_text(text, encoding="utf-8")
    if args.as_json:
        print(json.dumps({"type": "result", "session_id": session_id, "exit_code": code,
                          "status": (end or {}).get("status", "failed"), "text": text}, ensure_ascii=False), file=out)
    else:
        if text:
            print(text, file=out)
        if not args.quiet:
            print(f"session {session_id}", file=err)
    return code


def main(argv: Optional[list[str]] = None, *, stdin: Optional[TextIO] = None, provider: Any = None,
         out: TextIO = sys.stdout, err: TextIO = sys.stderr, state_dir: Optional[Path] = None) -> int:
    from harness.config.config import load_config

    args = build_parser(load_config().model).parse_args(argv)
    # A Windows console is cp1252 and a pipe is whatever the locale says; the answer is whatever the
    # model wrote. Printing `đạt` to either crashed the run AFTER the work was done and saved.
    for stream in (out, err):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass
    prompt = _read_prompt(args, stdin or sys.stdin)
    if not prompt:
        print("reeve exec: no task given (pass it as an argument, or pipe it in)", file=err)
        return EXIT_USAGE
    return asyncio.run(run_exec(args, prompt, provider=provider, out=out, err=err, state_dir=state_dir))


if __name__ == "__main__":
    raise SystemExit(main())
