"""External subagent providers — delegate to the Codex or Claude Code CLI.

An external child runs as a real CLI process: its own agent loop, its own sandbox and
approvals, its own authentication (the user's `codex login` / `claude login`). Only its
final answer comes back; intermediate tool traffic stays in the CLI's own transcript.
These are the out-of-process counterparts of the in-process `task` children,
without a bundled runtime (the CLI must
be installed and signed in on the machine running the sidecar).

The prompt rides STDIN (no argv length/quoting limits), the process runs in the parent's
workspace, and a stop event from the session's Stop path kills the whole process tree.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any, Callable, Optional

from harness.platform.win_job import ProcessJob
from harness.platform.spawn import spawn, run as spawn_run

#: Provider id → the executable it needs on PATH.
PROVIDER_BINARIES = {"codex": "codex", "claude-code": "claude"}

DEFAULT_TIMEOUT = 900.0
#: Report ceiling; the task envelope then rides the normal output budget.
MAX_REPORT_CHARS = 60_000
_ERR_TAIL_CHARS = 4_000


def available(provider: str) -> Optional[str]:
    """Absolute path to `provider`'s CLI, or None when it is not installed."""
    binary = PROVIDER_BINARIES.get(str(provider or ""))
    return shutil.which(binary) if binary else None


#: npm package that installs each CLI (the "Install" action's command).
INSTALL_PACKAGES = {"codex": "@openai/codex", "claude-code": "@anthropic-ai/claude-code"}
#: Credential markers, best-effort: the CLI's own auth file or its API-key env var.
_CRED_FILES = {
    "codex": (os.path.join("~", ".codex", "auth.json"),),
    "claude-code": (
        os.path.join("~", ".claude", ".credentials.json"),
        os.path.join("~", ".claude.json"),
    ),
}
_KEY_ENVS = {
    "codex": ("CODEX_API_KEY", "OPENAI_API_KEY"),
    "claude-code": ("CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY"),
}


def auth_status(provider: str) -> dict[str, Any]:
    """Best-effort signed-in probe for the badge. Never claims more than a marker."""
    for env in _KEY_ENVS.get(provider, ()):
        if os.environ.get(env):
            return {"signed_in": True, "detail": f"{env} is set"}
    for raw in _CRED_FILES.get(provider, ()):
        path = Path(raw).expanduser()
        if path.is_file():
            return {"signed_in": True, "detail": f"credentials at {path}"}
    return {"signed_in": False, "detail": "not signed in — use Sign in"}


def preflight(provider: str) -> Optional[str]:
    """A cheap "can this CLI actually run" check before spawning, or None when it can.

    Best-effort and never raises: only a POSITIVE not-signed-in finding returns a
    message, so an unexpected probe failure never blocks a delegation the CLI might
    still serve. Used by the manager before a child session is created (and by
    `run_external` for callers with no manager), so a lapsed sign-in fails in a second
    with a sentence instead of half a minute with an exit code.
    """
    if provider == "claude-code":
        if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"):
            return None
        try:
            payload = json.loads(
                Path(CLAUDE_CREDENTIALS).expanduser().read_text(encoding="utf-8")
            )
            oauth = payload.get("claudeAiOauth") or {}
        except Exception:
            return (
                "claude-code is not signed in — use Sign in in Settings ▸ Subagents "
                "(or run `claude setup-token`)"
            )
        if not (oauth.get("accessToken") or oauth.get("refreshToken")):
            return "claude-code is not signed in — use Sign in in Settings ▸ Subagents"
        # An expired ACCESS token is normal (the CLI refreshes it on run); only an
        # expired REFRESH token means the sign-in is gone for good.
        expires = int(oauth.get("refreshTokenExpiresAt") or 0)
        if expires and expires < time.time() * 1000:
            return "the claude-code sign-in has expired — sign in again in Settings ▸ Subagents"
        return None
    if provider == "codex":
        if os.environ.get("OPENAI_API_KEY") or os.environ.get("CODEX_API_KEY"):
            return None
        path = available("codex")
        if path is None:
            return "the codex CLI is not installed — use Install CLI in Settings ▸ Subagents"
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
        try:
            proc = spawn_run(
                [path, "login", "status"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=MODELS_TIMEOUT,
                creationflags=creationflags,
            )
        except Exception:
            return None
        if proc.returncode == 0:
            return None
        detail = " ".join(((proc.stderr or "") + " " + (proc.stdout or "")).split())[:200]
        return (
            "codex is not signed in — run `codex login` (or Sign in in Settings ▸ "
            f"Subagents){': ' + detail if detail else ''}"
        )
    return None


def login_argv(provider: str) -> Optional[list[str]]:
    """The CLI's own browser sign-in command, or None for an unknown provider."""
    path = available(provider)
    if path is None:
        return None
    if provider == "codex":
        return [path, "login"]
    if provider == "claude-code":
        return [path, "setup-token"]
    return None


def install_argv(provider: str) -> Optional[list[str]]:
    """`npm install -g <package>` for the provider's CLI, or None when npm is absent."""
    package = INSTALL_PACKAGES.get(provider)
    npm = shutil.which("npm") or shutil.which("npm.cmd")
    if package is None or npm is None:
        return None
    return [npm, "install", "-g", package]


# --- the account's own model list ------------------------------------------------------
#
# The CLI has no non-interactive `models` command, but its account list is reachable:
# the CLI talks to
# api.anthropic.com with the OAuth token it stores in the credential file, and the same
# token answers `GET /v1/models`. Codex is the same idea with an OpenAI API key.

#: Claude Code's credential file (the OAuth access token the CLI itself uses).
CLAUDE_CREDENTIALS = "~/.claude/.credentials.json"
MODELS_TIMEOUT = 10.0
#: HTTP seam for tests — monkeypatch to avoid the network.
_urlopen = urllib.request.urlopen


def _codex_cli_models() -> Optional[list[dict[str, str]]]:
    """The Codex CLI's own model catalog (`codex debug models`), visible rows only.

    A debug command, but it is the CLI's own source of truth, runs in under a second and
    needs no API key (so it works for the ChatGPT sign-in too). None when the CLI is
    absent or the command fails; the caller then falls back to the OpenAI API."""
    path = available("codex")
    if path is None:
        return None
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
    try:
        proc = spawn_run(
            [path, "debug", "models"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=MODELS_TIMEOUT,
            creationflags=creationflags,
        )
    except Exception:
        return None
    if proc.returncode != 0:
        return None
    try:
        payload = json.loads(proc.stdout or "")
    except Exception:
        return None
    rows = payload.get("models") if isinstance(payload, dict) else None
    models: list[dict[str, str]] = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        slug = str(row.get("slug") or "")
        if not slug or str(row.get("visibility") or "") == "hide":
            continue
        models.append({"id": slug, "name": str(row.get("display_name") or slug)})
    return models or None


def _claude_token() -> tuple[str, str, int]:
    """`(kind, token, expires_at_ms)`. `kind` is `"api_key"` or `"bearer"`; the API key
    env wins over the stored OAuth token, the CLI's own precedence."""
    key = os.environ.get("ANTHROPIC_API_KEY")
    if key:
        return "api_key", key, 0
    path = Path(CLAUDE_CREDENTIALS).expanduser()
    payload = json.loads(path.read_text(encoding="utf-8"))
    oauth = payload.get("claudeAiOauth") or {}
    return "bearer", str(oauth.get("accessToken") or ""), int(oauth.get("expiresAt") or 0)


def list_models(provider: str) -> dict[str, Any]:
    """The models the CLI's account can use, from the provider's own API.

    Claude: `GET https://api.anthropic.com/v1/models` with the CLI's OAuth token (or an
    `ANTHROPIC_API_KEY`), the same list Claude Code's `/model` shows. Codex: the CLI's own
    catalog (`codex debug models`, visible rows; no key needed — works for the ChatGPT
    sign-in), falling back to the OpenAI model list when an API key is set. Never raises;
    failures carry a message for the GUI fallback.
    """
    if provider == "claude-code":
        try:
            kind, token, expires = _claude_token()
        except Exception:
            return {
                "ok": False,
                "error": "no Claude credentials found — use Sign in first",
            }
        if not token:
            return {
                "ok": False,
                "error": "no Claude credentials found — use Sign in first",
            }
        if kind == "bearer" and expires and expires < time.time() * 1000:
            return {
                "ok": False,
                "error": "the Claude token has expired — open Claude Code once to refresh it",
            }
        headers = {"anthropic-version": "2023-06-01"}
        if kind == "api_key":
            headers["x-api-key"] = token
        else:
            headers["Authorization"] = f"Bearer {token}"
            headers["anthropic-beta"] = "oauth-2025-04-20"
        url = "https://api.anthropic.com/v1/models?limit=100"
        source = "api key" if kind == "api_key" else "Claude account"
    elif provider == "codex":
        cli_models = _codex_cli_models()
        if cli_models:
            return {"ok": True, "models": cli_models, "source": "Codex CLI catalog"}
        key = os.environ.get("OPENAI_API_KEY") or os.environ.get("CODEX_API_KEY")
        if not key:
            return {
                "ok": False,
                "error": "the Codex CLI catalog is unavailable and no OPENAI_API_KEY is set",
            }
        headers = {"Authorization": f"Bearer {key}"}
        url = "https://api.openai.com/v1/models"
        source = "OpenAI API key"
    else:
        return {"ok": False, "error": f"unknown provider: {provider}"}

    try:
        request = urllib.request.Request(url, headers=headers)
        with _urlopen(request, timeout=MODELS_TIMEOUT) as response:
            payload = json.load(response)
    except Exception as exc:  # HTTPError, URLError, timeout, bad JSON
        return {"ok": False, "error": f"could not read the model list: {exc}"}

    rows = payload.get("data") if isinstance(payload, dict) else None
    models: list[dict[str, str]] = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        model_id = str(row.get("id") or "")
        if not model_id:
            continue
        if provider == "codex" and not model_id.startswith(("gpt-", "o1", "o3", "o4", "codex")):
            continue
        models.append({"id": model_id, "name": str(row.get("display_name") or model_id)})
    return {"ok": True, "models": models, "source": source}


def build_argv(
    provider: str,
    path: str,
    *,
    workspace: str,
    model: str = "",
    permission_mode: str = "",
) -> list[str]:
    """The non-interactive invocation for one provider.

    Both providers run with full trust, because a piped subprocess can never answer an
    approval prompt: `codex exec` gets `--sandbox danger-full-access` with
    `approval_policy=never`, and `claude -p` gets `--permission-mode bypassPermissions`
    (its user hooks still apply). `permission_mode` (agent `permission_mode`) overrides
    either — e.g. `workspace-write`/`acceptEdits` to re-confine a delegation.
    """
    if provider == "codex":
        argv = [path, "exec", "--json", "--cd", workspace or os.getcwd()]
        argv += ["--sandbox", permission_mode or "danger-full-access"]
        argv += ["-c", "approval_policy=never", "--skip-git-repo-check"]
        if model:
            argv += ["--model", model]
        return argv
    if provider == "claude-code":
        # `stream-json` + partial messages is what makes the child's view LIVE: each
        # content-block delta is one JSON line on stdout, which `run_external` forwards
        # through `on_event` as it arrives. The final `result` line still carries the
        # answer and usage.
        argv = [
            path,
            "-p",
            "--output-format",
            "stream-json",
            "--include-partial-messages",
            "--verbose",
        ]
        argv += ["--permission-mode", permission_mode or "bypassPermissions"]
        if model:
            argv += ["--model", model]
        return argv
    raise ValueError(f"unknown external provider: {provider}")


def parse_report(provider: str, stdout: str) -> tuple[str, int]:
    """`(final text, tokens)` from the CLI's stdout. Never raises — an unparseable stream
    degrades to the raw text, so a run that produced words is not reported empty."""
    if provider == "claude-code":
        # `--output-format stream-json`: one JSON object per line; the terminal `result`
        # event is the answer, and a run killed before it leaves `assistant` messages to
        # fall back on. A single-object `{"result": …}` body (older flag shape) still
        # parses through the same loop.
        text = ""
        tokens = 0
        for line in stdout.splitlines():
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if not isinstance(event, dict):
                continue
            if event.get("type") == "result":
                text = str(event.get("result") or "")
                usage = event.get("usage") or {}
                tokens = int(usage.get("input_tokens") or 0) + int(
                    usage.get("output_tokens") or 0
                )
            elif event.get("type") == "assistant":
                message = event.get("message") or {}
                for block in message.get("content") or []:
                    if isinstance(block, dict) and block.get("type") == "text":
                        text = str(block.get("text") or "")
                usage = message.get("usage") or {}
                if usage:
                    tokens = int(usage.get("input_tokens") or 0) + int(
                        usage.get("output_tokens") or 0
                    )
        if text:
            return text, tokens
        return stdout.strip(), 0
    # `codex exec --json`: JSONL events; the last agent_message item is the answer.
    text = ""
    tokens = 0
    for line in stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        if event.get("type") == "item.completed":
            item = event.get("item") or {}
            if item.get("type") == "agent_message" and item.get("text"):
                text = str(item["text"])
        elif event.get("type") == "turn.completed":
            usage = event.get("usage") or {}
            tokens = int(usage.get("input_tokens") or 0) + int(
                usage.get("output_tokens") or 0
            )
    return (text or stdout).strip(), tokens


def progress_text(provider: str, event: dict[str, Any], current: str) -> str:
    """Fold one streamed CLI event into the child's live progress text.

    Best-effort and provider-shaped: Claude's `stream-json` deltas append assistant text
    and mark tool starts; Codex's JSONL events append the agent's messages and the shell
    commands it ran. Returns `current` unchanged for events carrying nothing visible —
    the child view polls the persisted transcript, so every change here is one frame of
    "the subagent is working, here is what it is doing".
    """
    if provider == "claude-code":
        stream = event.get("event") or {}
        kind = stream.get("type")
        if kind == "content_block_delta":
            delta = stream.get("delta") or {}
            if delta.get("type") == "text_delta":
                return current + str(delta.get("text") or "")
        if kind == "content_block_start":
            block = stream.get("content_block") or {}
            if block.get("type") == "tool_use":
                return f"{current}\n\n→ {block.get('name')} …\n"
        return current
    if event.get("type") == "item.completed":
        item = event.get("item") or {}
        if item.get("type") == "agent_message" and item.get("text"):
            return current + str(item["text"])
        if item.get("type") == "command_execution" and item.get("command"):
            return f"{current}\n\n$ {item['command']}\n"
    return current


def _kill_tree(proc: subprocess.Popen, job: Optional[ProcessJob] = None) -> None:
    """Kill the CLI and everything it spawned. A CLI agent leaves children behind.

    The job object, when one attached, is the structural kill: it reaches a descendant
    that re-parented (taskkill /T cannot). taskkill and the process group stay as the
    fallback for the case it did not attach.
    """
    try:
        if job is not None:
            job.terminate()
        if sys.platform == "win32":
            spawn_run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                capture_output=True,
                check=False,
            )
        else:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def run_external(
    provider: str,
    *,
    prompt: str,
    workspace: Optional[str] = None,
    model: str = "",
    permission_mode: str = "",
    timeout: float = DEFAULT_TIMEOUT,
    stop_event: Optional[threading.Event] = None,
    on_started: Optional[Callable[[list[str]], None]] = None,
    #: Per-streamed-event observer (`on_event(parsed_json_line)`), called on the reader
    #: thread as the CLI emits. Best-effort: a raising observer never fails the run.
    on_event: Optional[Callable[[dict[str, Any]], None]] = None,
) -> dict[str, Any]:
    """Run one external child to completion (blocking; call on a worker thread).

    Returns the manager's child-result shape: `{status, report, tokens, error?}` where
    `status` is `"completed"`, `"interrupted"`, or `"error: …"`. Never raises.
    """
    path = available(provider)
    if path is None:
        return {
            "status": "error: the CLI is not installed",
            "report": "",
            "tokens": 0,
            "error": (
                f"the {provider} CLI is not on PATH on the machine running the sidecar "
                "— install it and sign in, or pick an in-process agent type"
            ),
        }
    ws = workspace if workspace and Path(workspace).is_dir() else None
    problem = preflight(provider)
    if problem:
        return {"status": f"error: {problem}", "report": "", "tokens": 0, "error": problem}
    argv = build_argv(
        provider,
        path,
        workspace=ws or os.getcwd(),
        model=model,
        permission_mode=permission_mode,
    )
    errfile = tempfile.TemporaryFile(mode="w+", encoding="utf-8", errors="replace")
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
    try:
        proc = spawn(
            argv,
            cwd=ws,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=errfile,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=creationflags,
            start_new_session=sys.platform != "win32",
        )
        job = ProcessJob.attach(proc.pid)
    except OSError as exc:
        errfile.close()
        return {
            "status": f"error: could not start {provider}",
            "report": "",
            "tokens": 0,
            "error": str(exc),
        }
    if on_started is not None:
        try:
            on_started(argv)
        except Exception:
            pass

    chunks: list[str] = []

    def _read() -> None:
        try:
            assert proc.stdout is not None
            while True:
                # Line-based so a streamed event reaches `on_event` the moment the CLI
                # flushes it — this is what makes the child view live.
                line = proc.stdout.readline()
                if not line:
                    break
                chunks.append(line)
                if on_event is None:
                    continue
                stripped = line.strip()
                if not stripped.startswith("{"):
                    continue
                try:
                    event = json.loads(stripped)
                except ValueError:
                    continue
                if not isinstance(event, dict):
                    continue
                try:
                    on_event(event)
                except Exception:
                    pass
        except Exception:
            pass

    reader = threading.Thread(target=_read, name=f"external-{provider}", daemon=True)
    reader.start()
    try:
        assert proc.stdin is not None
        proc.stdin.write(prompt)
        proc.stdin.close()
    except Exception:
        pass

    deadline = time.monotonic() + max(5.0, float(timeout))
    stopped = False
    timed_out = False
    while proc.poll() is None:
        if stop_event is not None and stop_event.is_set():
            stopped = True
            break
        if time.monotonic() > deadline:
            timed_out = True
            break
        time.sleep(0.2)
    if stopped or timed_out:
        _kill_tree(proc, job)
    try:
        proc.wait(timeout=10)
    except Exception:
        _kill_tree(proc, job)
    reader.join(timeout=5)
    stdout = "".join(chunks)
    try:
        errfile.seek(0)
        stderr = errfile.read()[:_ERR_TAIL_CHARS]
    except Exception:
        stderr = ""
    errfile.close()

    if stopped:
        return {"status": "interrupted", "report": "", "tokens": 0, "error": "stopped"}
    if timed_out:
        return {
            "status": f"error: timed out after {int(timeout)}s",
            "report": "",
            "tokens": 0,
            "error": f"the external agent did not finish within {int(timeout)}s",
        }
    if proc.returncode != 0:
        tail = " ".join(stderr.split())[-600:]
        return {
            "status": f"error: {provider} exited {proc.returncode}",
            "report": "",
            "tokens": 0,
            "error": tail or f"exit code {proc.returncode}",
        }
    text, tokens = parse_report(provider, stdout)
    if len(text) > MAX_REPORT_CHARS:
        text = text[:MAX_REPORT_CHARS] + "\n…[truncated]"
    return {
        "status": "completed",
        "report": text or "(the external agent returned no output)",
        "tokens": tokens,
    }
