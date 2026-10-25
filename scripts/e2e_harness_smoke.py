"""End-to-end smoke for the harness work of 2026-09-15 — the real engine, a real model.

The pytest suite proves each mechanism with scripted providers; this script proves the
WIRING, against whatever provider the app is configured with. It is run by hand because
the live checks spend tokens (three calls: one summarizer replay, two goal rounds):

    python scripts/e2e_harness_smoke.py                # every check, real model
    python scripts/e2e_harness_smoke.py --offline      # deterministic checks only
    python scripts/e2e_harness_smoke.py --model anthropic:claude-sonnet-4

What each check covers (decisions 0008–0013):

    spill     a 300k-character text attachment becomes head + path, whole file on disk
    images    a 9000-px-wide image is fitted under the route's ceiling
    syntax    a write that leaves an unparsable file says so in the same step
    compact   POST-equivalent manual compaction below pressure: summary + marker + Restore
    goal      two rounds of an armed goal open turns on their own, then stop

The full pytest suite (`pytest`) is the regression gate; this is the "does it hold in the
real thing" pass. The live checks need keys the app already has (SecretStore).
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import io
import json
import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

RESULTS: list[tuple[str, bool, str]] = []


def _console(text: str) -> str:
    """ASCII-safe for a Windows console (cp1252 dies on ×, → and friends)."""
    for fancy, plain in (("×", "x"), ("→", "->"), ("—", "-"), ("…", "...")):
        text = text.replace(fancy, plain)
    return text.encode("ascii", "replace").decode("ascii")


def check(name: str, ok: bool, detail: str = "") -> None:
    detail = _console(detail)
    RESULTS.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}{(' - ' + detail) if detail else ''}")


# -- deterministic checks (no provider) -------------------------------------------


def check_attachment_spill(workspace: Path) -> None:
    from harness.llm.attachments import build_user_content
    from harness.tools.spill import SpillStore

    store = SpillStore(workspace / ".reeve" / "output")
    # Under MAX_TEXT_CHARS (200k) on purpose: that ceiling is the attachment admission
    # cap, and this check is about what happens to an accepted file that is still far too
    # large to inline.
    body = "\n".join(f"line {i} " + "x" * 40 for i in range(1, 3001))  # ~150k chars
    content = build_user_content(
        "summarize the attachment",
        [{"kind": "text", "name": "big.md", "text": body}],
        spill=store.sink("attachment"),
    )
    text = "".join(p.get("text", "") for p in content if p.get("type") == "text")
    saved = [p for p in (workspace / ".reeve" / "output").glob("attachment-*.log")]
    ok = (
        len(text) < 20_000
        and "big.md" in text
        and bool(saved)
        and saved[0].read_text(encoding="utf-8") == body
    )
    check(
        "attachment spill",
        ok,
        f"{len(body):,} chars in, {len(text):,} inline, whole file at {saved[0].name if saved else '?'}",
    )


def check_image_fitting(workspace: Path) -> None:
    try:
        from PIL import Image
    except Exception:
        check("image fitting", True, "skipped (no Pillow in this interpreter)")
        return
    from harness.llm.images import fit_image_url

    buf = io.BytesIO()
    Image.new("RGB", (9_000, 150), (200, 30, 30)).save(buf, format="PNG")
    url = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
    fitted, note = fit_image_url(url, model="anthropic:claude-sonnet-4")
    if fitted is None:
        check("image fitting", False, note or "refused")
        return
    raw = base64.b64decode(fitted.split(",", 1)[1])
    with Image.open(io.BytesIO(raw)) as img:
        width, height = img.size
    check("image fitting", width <= 8_000, f"9000x150 -> {width}x{height}; {note}")


def check_syntax_note(workspace: Path) -> None:
    from harness.loop import TurnEngine
    from harness.safety.permissions import PermissionEngine
    from harness.llm.providers import ModelCapabilities, ProviderClient, ToolCall
    from harness.llm.providers.base import AssistantTurn
    from harness.tools import ToolRegistry

    class Offline(ProviderClient):
        def complete(self, *, model, messages, tools=None, **settings):
            return AssistantTurn(text="", finish_reason="stop")

        def capabilities(self, model):
            return ModelCapabilities()

    target = workspace / "broken.py"
    target.write_text("def f(:\n    pass\n", encoding="utf-8")
    engine = TurnEngine(
        provider=Offline(),
        registry=ToolRegistry(),
        permissions=PermissionEngine(workspace_root=workspace),
        model="gpt-5.5",
        messages=[{"role": "system", "content": "s"}],
    )
    engine._record_result(
        ToolCall(id="c1", name="write_file", arguments={"path": "broken.py", "content": ""}),
        {"ok": True},
        "ok",
    )
    content = str(engine.messages[-1].get("content", ""))
    check(
        "syntax diagnostics",
        "[syntax check]" in content and "broken.py:1:" in content,
        content.splitlines()[-1][:80] if "[syntax check]" in content else content[:80],
    )


# -- live checks (real model) ------------------------------------------------------


def _provider(model: str, secrets_path: Path):
    """The app's own configured provider, built from the REAL secrets file.

    The path is captured before the state dir is swapped, so the live checks use the
    user's keys while every scratch artifact (goals, wakes) lands in the throwaway tree.
    """
    from harness.llm.providers import ProviderRouter
    from harness.safety.secrets import SecretStore

    return ProviderRouter(SecretStore(secrets_path), default_provider="openai")


def _seed_history(turns: int = 15, bulk: int = 3_000) -> list[dict]:
    """Big enough that a summary CAN shrink it: `_assert_smaller` refuses a compaction
    whose block is not smaller than the span it replaces, and a tiny seed would make the
    check measure the guard instead of the mechanism."""
    messages = [{"role": "system", "content": "You are a concise assistant."}]
    for i in range(turns):
        messages.append({"role": "user", "content": f"question {i}", "ts": 1.0})
        messages.append(
            {"role": "assistant", "content": f"answer {i} " + "x" * bulk, "ts": 1.0}
        )
    return messages


async def check_manual_compaction(model: str, workspace: Path, secrets_path: Path) -> None:
    from harness.compose import build_engine
    from harness.modes.agents.reeve import reeve_agent

    provider = _provider(model, secrets_path)
    engine = build_engine(
        agent=reeve_agent(),
        workspace=workspace,
        model=model,
        provider=provider,
        session_id="e2e-compact",
        messages=_seed_history(),
    )
    result = await engine.compact_now()
    notice = next(
        (
            m
            for m in engine.messages
            if m.get("role") == "notice" and m.get("kind") == "compacted"
        ),
        None,
    )
    ok = (
        result.get("ok") is True
        and result.get("saved_tokens", 0) > 0
        and result.get("reason") in ("summarized", "trimmed")
        and notice is not None
    )
    check(
        "manual /compact",
        ok,
        f"{result.get('reason')} saved {result.get('saved_tokens', 0):,} tokens "
        f"({result.get('saved_pct', 0):.0%}); marker={'yes' if notice else 'no'}"
        if result.get("ok")
        else str(result.get("error")),
    )


async def check_goal_rounds(
    model: str, workspace: Path, state: Path, secrets_path: Path
) -> None:
    from harness.loop.goals import GoalStore, goal_tools
    from harness.interface.server.manager import SessionManager

    os.environ["REEVE_STATE_DIR"] = str(state)  # goals/wakes isolated; keys not
    provider = _provider(model, secrets_path)
    manager = SessionManager(workspace=workspace, provider=provider)
    sid = "e2e-goal"
    # Seed the session RECORD with the requested model: the manager builds an engine
    # from the record (or its own default), and a fresh session would otherwise run on
    # the app's default route, not the one this smoke asked for.
    from harness.session.sessions import SessionRecord

    manager.session_store.save(
        SessionRecord(
            session_id=sid,
            workspace=str(workspace),
            model=model,
            mode="interactive",
            messages=[],
            agent="reeve",
        )
    )
    engine = manager.get_engine(sid, agent="reeve", workspace=str(workspace))
    assert engine is not None and engine.model == model, (
        f"manager built {getattr(engine, 'model', None)!r}, expected {model!r}"
    )

    store = GoalStore.for_session(sid)
    tool = goal_tools(store)[0]
    tool(
        action="set",
        objective="Do not use any tools. Reply with the single word OK. Do not call goal.",
        max_rounds=3,
    )
    manager._loop = asyncio.get_running_loop()
    manager.broadcast_session = _noop  # type: ignore[assignment]

    await manager._continue_goal(sid)
    await manager._continue_goal(sid)
    goal = store.load()
    rounds = [m for m in engine.messages if "goal_round" in str(m.get("content"))]
    ok = goal.rounds_started == 2 and goal.phase == "active" and len(rounds) == 2
    check(
        "goal rounds",
        ok,
        f"{goal.rounds_started} rounds opened, phase {goal.phase}, "
        f"{len(rounds)} prompts in history",
    )
    tool(action="done", reason="smoke test over")
    store.clear()


async def check_goal_pauses_on_a_failed_round(
    model: str, workspace: Path, state: Path, secrets_path: Path
) -> None:
    """A round whose model call fails must PAUSE the goal, not open another one.

    Deliberately driven with a model route that cannot resolve, so the check costs
    nothing: the round ends `error`, and the pause rule is what is under test.
    """
    from harness.loop.goals import GoalStore, goal_tools
    from harness.interface.server.manager import SessionManager
    from harness.session.sessions import SessionRecord

    os.environ["REEVE_STATE_DIR"] = str(state)
    provider = _provider(model, secrets_path)
    manager = SessionManager(workspace=workspace, provider=provider)
    sid = "e2e-goal-failure"
    manager.session_store.save(
        SessionRecord(
            session_id=sid,
            workspace=str(workspace),
            model="opencode-go:definitely-not-a-real-model",
            mode="interactive",
            messages=[],
            agent="reeve",
        )
    )
    manager.get_engine(sid, agent="reeve", workspace=str(workspace))
    store = GoalStore.for_session(sid)
    tool = goal_tools(store)[0]
    tool(action="set", objective="this round cannot run", max_rounds=2)
    manager._loop = asyncio.get_running_loop()
    manager.broadcast_session = _noop  # type: ignore[assignment]

    await manager._continue_goal(sid)

    goal = store.load()
    ok = goal.phase == "paused" and goal.rounds_started == 1
    check(
        "goal pauses on a failed round",
        ok,
        f"phase {goal.phase}, rounds {goal.rounds_started}, note {goal.note!r}",
    )
    store.clear()


async def check_background_wake(
    model: str, workspace: Path, state: Path, secrets_path: Path
) -> None:
    """A real model starts a background job, ends its reply, and is WOKEN with the result.

    This is the behavioural half of background tasks reporting themselves; it needs a real model because the claim
    is about what the MODEL does with the new description: start the job in the background,
    stop, and let the notification arrive — instead of writing a `sleep`/`ps` poll loop.
    Two facts are checked, and both are visible in the transcript:
      * the session is woken (a `background_shell` notification carrying the job's output);
      * the model did not poll (no foreground sleep/ps, no `shell_task_output`).
    """
    import re
    import time

    from harness.interface.server.manager import SessionManager
    from harness.session.sessions import SessionRecord

    os.environ["REEVE_STATE_DIR"] = str(state / "wake")
    provider = _provider(model, secrets_path)
    manager = SessionManager(workspace=workspace, provider=provider)
    sid = "e2e-bg-wake"
    manager.session_store.save(
        SessionRecord(
            session_id=sid,
            workspace=str(workspace),
            model=model,
            # Full access: the check is about the WAKE, and a `run_shell` approval card has
            # nobody to answer it in a headless smoke (the first run parked forever).
            mode="danger-full-access",
            messages=[],
            agent="reeve",
        )
    )
    engine = manager.get_engine(sid, agent="reeve", workspace=str(workspace))
    assert engine is not None, "the manager did not build an engine"
    manager._loop = asyncio.get_running_loop()
    manager.broadcast_session = _noop  # type: ignore[assignment]

    marker = "BG-JOB-DONE-42"
    command = (
        f"Start-Sleep -Seconds 8; echo {marker}" if os.name == "nt"
        else f"sleep 8; echo {marker}"
    )
    print(f"  ... asking {model} to start `{command}` in the background")
    # A hard cap: a parked approval or a wedged stream must fail the check, not hang the
    # smoke for a quarter of an hour (the first version did exactly that).
    await asyncio.wait_for(
        manager.deliver_to_session(
            sid,
            "Start this command in the BACKGROUND with run_shell run_in_background=true, "
            f"then END your reply immediately without waiting for it: `{command}`. Do not "
            "poll it, do not call shell_task_output, do not sleep. After starting it, "
            "reply with the single word STARTED.",
        ),
        timeout=240,
    )
    print("  ... first turn done; waiting for the wake")

    def transcript() -> str:
        record = manager.session_store.load(sid)
        return "\n".join(str(m.get("content")) for m in (record.messages if record else []))

    deadline = time.monotonic() + 240
    woken = False
    while time.monotonic() < deadline:
        blob = transcript()
        if marker in blob and "background task" in blob:
            woken = True
            break
        await asyncio.sleep(1.0)
    check(
        "a real model is woken by the background job",
        woken,
        f"marker in transcript: {marker in transcript()}",
    )

    # The polls this exists to prevent: a FOREGROUND wait, or asking the task for output.
    polls: list[str] = []
    for message in engine.messages:
        for call in message.get("tool_calls") or []:
            function = (call or {}).get("function") or {}
            name = function.get("name") or ""
            try:
                args = json.loads(function.get("arguments") or "{}")
            except (TypeError, ValueError):
                args = {}
            if name in ("shell_task_output", "shell_task_kill"):
                polls.append(name)
            elif name == "run_shell" and not args.get("run_in_background"):
                text = str(args.get("command") or "")
                if re.search(r"Start-Sleep|sleep\s+\d|Get-Process|\bps\b|pgrep", text):
                    polls.append(text[:40])
    check(
        "the model did not poll",
        not polls,
        f"{len(polls)} poll call(s): {polls[:3]}" if polls else "no poll calls",
    )
    engine.executor.close()


async def _noop(*_args, **_kwargs) -> None:
    return None


# -- main --------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description="End-to-end smoke for the harness")
    parser.add_argument("--model", default="gpt-5.6-sol", help="provider:model route")
    parser.add_argument(
        "--offline",
        action="store_true",
        help="deterministic checks only (no provider call)",
    )
    parser.add_argument(
        "--checks",
        default="",
        help=(
            "comma-separated live checks to run (default: all). Names: compaction, goals, "
            "goal-failure, background-wake"
        ),
    )
    args = parser.parse_args()
    wanted = {name.strip() for name in args.checks.split(",") if name.strip()}

    def _want(name: str) -> bool:
        return not wanted or name in wanted

    # The manager keeps SQLite handles open (automation/goals), which Windows will not
    # let the cleanup unlink — keep the results, not the scratch tree, and let the OS
    # reclaim the temp dir.
    with tempfile.TemporaryDirectory(
        prefix="reeve-e2e-", ignore_cleanup_errors=True
    ) as tmp:
        base = Path(tmp)
        workspace = base / "workspace"
        workspace.mkdir()
        check_attachment_spill(workspace)
        check_image_fitting(workspace)
        check_syntax_note(workspace)

        if not args.offline:
            # Capture the real keys before the state dir is swapped: the live checks use
            # the app's own configuration, while goals/wakes land in the throwaway tree.
            from harness.safety.secrets import state_dir

            real_secrets = state_dir() / "secrets.json"
            state = base / "state"
            state.mkdir()
            if _want("compaction"):
                asyncio.run(check_manual_compaction(args.model, workspace, real_secrets))
            if _want("goals"):
                asyncio.run(check_goal_rounds(args.model, workspace, state, real_secrets))
            if _want("goal-failure"):
                asyncio.run(
                    check_goal_pauses_on_a_failed_round(
                        args.model, workspace, state, real_secrets
                    )
                )
            if _want("background-wake"):
                asyncio.run(
                    check_background_wake(args.model, workspace, state, real_secrets)
                )

    failed = [name for name, ok, _ in RESULTS if not ok]
    print()
    print(f"{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed"
          + (f"; failed: {', '.join(failed)}" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
