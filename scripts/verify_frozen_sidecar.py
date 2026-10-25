"""Verify a BUILT sidecar bundle: it fits an oversized image (Pillow is in it), and its
`run_code` child imports the standard library a dev child imports.

`packaging/reeve-server.spec` collects PIL, and the bundle LOOKS correct either way:
PyInstaller stages Pillow's `.pyd` files into `_internal/` even when the module graph
excludes PIL, because those arrive through the explicit `binaries`/`datas` lists. An
`excludes` entry beats `hiddenimports`, so the pure-Python half can be missing from the
PYZ while a folder inspection still passes — and `harness/llm/images.py` then degrades to
"this build has no image encoder" for every image over a route's pixel ceiling.

So the check is behavioral: start the packaged exe, attach an image wider than the
Claude route's 8000 px ceiling, and read back the persisted user message. The turn
itself is never meant to reach a model: the route is one the local config holds no
credentials for, and the user message is built and persisted BEFORE the first provider
call, which is the only thing under test.

The child check runs one program twice, through the dev child (`python -m
harness.tools.code_child`) and through the frozen `reeve-code-child`, importing every module
`packaging/child_stdlib.py` names. The frozen child must import all that the dev one does; a
module that fails in both (`curses` on Windows has no `_curses`) is the platform, not the build.

    .venv\\Scripts\\python.exe scripts/verify_frozen_sidecar.py
    ... --exe packaging/dist/reeve-server/reeve-server.exe
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import io
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EXE = ROOT / "packaging" / "dist" / "reeve-server" / "reeve-server.exe"

# Over the 8000 px long-side ceiling of the Claude entry in IMAGE_LIMITS, and small
# enough to sail through every byte cap - the resize branch is the ONLY one this hits.
IMAGE_SIZE = (9000, 64)
CLAUDE_MODEL = "anthropic:claude-sonnet-4-5"
TOKEN = "frozen-sidecar-probe"


def check(results: list[bool], name: str, ok: bool, detail: str = "") -> None:
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f" - {detail}" if detail else ""))
    results.append(ok)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _get(port: int, path: str) -> dict:
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}", headers={"x-reeve-token": TOKEN}
    )
    with urllib.request.urlopen(req, timeout=30) as res:
        return json.loads(res.read().decode("utf-8"))


def oversized_png_data_url() -> str:
    from PIL import Image

    img = Image.new("RGB", IMAGE_SIZE, (245, 245, 245))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def text_of(content: object) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    return "\n".join(
        str(p.get("text", ""))
        for p in content
        if isinstance(p, dict) and p.get("type") == "text"
    )


def image_urls(content: object) -> list[str]:
    out: list[str] = []
    if not isinstance(content, list):
        return out
    for part in content:
        if isinstance(part, dict) and part.get("type") == "image_url":
            holder = part.get("image_url")
            if isinstance(holder, dict) and isinstance(holder.get("url"), str):
                out.append(holder["url"])
    return out


def wait_for_user_message(port: int, session_id: str, seconds: float = 90.0) -> dict:
    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            messages = _get(port, f"/v1/sessions/{session_id}/messages").get("messages", [])
        except Exception:
            messages = []
        for message in messages:
            if message.get("role") == "user" and message.get("content"):
                return message
        time.sleep(1.0)
    return {}


async def drive(port: int, workspace: Path, session_id: str, original: str) -> None:
    import websockets

    url = (
        f"ws://127.0.0.1:{port}/ws/session/{session_id}"
        f"?workspace={urllib.parse.quote(str(workspace))}&agent=reeve"
    )
    async with websockets.connect(url, subprotocols=["reeve", TOKEN]) as ws:
        await ws.send(
            json.dumps(
                {
                    "type": "user_message",
                    "text": "what is in this image?",
                    "model": CLAUDE_MODEL,
                    "attachments": [
                        {"kind": "image", "name": "wide.png", "data_url": original}
                    ],
                }
            )
        )
        # Frames arrive until the model call fails; the user message is already in the
        # store by then. Drain for a moment so a rejection would be visible.
        try:
            while True:
                frame = json.loads(await asyncio.wait_for(ws.recv(), timeout=8))
                if frame.get("type") in ("error", "notice"):
                    print(f"      ws {frame['type']}: {str(frame.get('data'))[:200]}")
        except (asyncio.TimeoutError, TimeoutError):
            pass
        except Exception:
            pass


def child_import_failures(argv: list[str], names: list[str]) -> list[str]:
    """The modules of `names` a `run_code` child started as `argv` cannot import, each with
    its error, read from the child's own done frame."""
    program = (
        "import importlib\n"
        "failed = []\n"
        f"for name in {json.dumps(names)}:\n"
        "    try:\n"
        "        importlib.import_module(name)\n"
        "    except BaseException as exc:\n"
        "        failed.append(f'{name}: {type(exc).__name__}: {exc}')\n"
        "return failed\n"
    )
    start = json.dumps({"code": program, "names": [], "signatures": {}}) + "\n"
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONPATH": str(ROOT)}
    done = subprocess.run(
        argv, input=start, capture_output=True, text=True, encoding="utf-8", env=env, timeout=300
    )
    for line in done.stdout.splitlines():
        try:
            frame = json.loads(line)
        except ValueError:
            continue
        if frame.get("t") == "done":
            if frame.get("error"):
                return [f"(the program failed) {frame['error'][-300:]}"]
            return [str(item) for item in frame.get("returned") or []]
    return [f"(no done frame) exit {done.returncode}: {done.stderr[-300:]}"]


def check_child_stdlib(results: list[bool], server_exe: Path) -> None:
    sys.path.insert(0, str(ROOT / "packaging"))
    from child_stdlib import top_level

    child = server_exe.with_name("reeve-code-child.exe" if sys.platform == "win32" else "reeve-code-child")
    if not child.exists():
        check(results, "the run_code child is in the bundle", False, str(child))
        return
    names = top_level()
    dev = child_import_failures([sys.executable, "-u", "-m", "harness.tools.code_child"], names)
    frozen = child_import_failures([str(child)], names)
    platform = {item.split(":", 1)[0] for item in dev}
    missing = [item for item in frozen if item.split(":", 1)[0] not in platform]
    check(
        results,
        f"the run_code child imports the standard library a dev child does ({len(names)} modules)",
        not missing,
        "; ".join(missing[:8]) or f"both fail only on: {sorted(platform) or 'nothing'}",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exe", type=Path, default=DEFAULT_EXE)
    parser.add_argument("--keep", action="store_true", help="leave the temp dirs behind")
    args = parser.parse_args()

    if not args.exe.exists():
        print(f"FAIL  sidecar not found at {args.exe} - run packaging/build_windows.ps1")
        return 1

    tmp = Path(tempfile.mkdtemp(prefix="reeve-frozen-"))
    workspace, state = tmp / "workspace", tmp / "state"
    workspace.mkdir()
    state.mkdir()
    port = free_port()
    session_id = uuid.uuid4().hex[:12]
    original = oversized_png_data_url()
    results: list[bool] = []
    check_child_stdlib(results, args.exe)

    env = {
        **os.environ,
        "REEVE_STATE_DIR": str(state),
        "REEVE_API_TOKEN": TOKEN,
    }
    proc = subprocess.Popen(
        [
            str(args.exe),
            "--port",
            str(port),
            "--cwd",
            str(workspace),
            "--model",
            CLAUDE_MODEL,
        ],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        deadline = time.time() + 90
        healthy = False
        while time.time() < deadline and proc.poll() is None:
            try:
                if _get(port, "/v1/health").get("status") == "ok":
                    healthy = True
                    break
            except Exception:
                time.sleep(0.5)
        check(results, "packaged sidecar starts and serves /v1/health", healthy)
        if not healthy:
            tail = proc.stdout.read(2000) if proc.stdout else ""
            print(tail[-1000:])
            return 1

        asyncio.run(drive(port, workspace, session_id, original))

        content = wait_for_user_message(port, session_id).get("content")
        text = text_of(content)
        urls = image_urls(content)
        check(results, "the image attachment was persisted with the user message", bool(content))
        check(
            results,
            "the image was RESIZED (Pillow is in the bundle)",
            "the image was resized to" in text,
            (text.splitlines()[-1][:160] if text else "no text part"),
        )
        check(
            results,
            "not refused as 'no image encoder'",
            "not sent" not in text and "no image encoder" not in text,
        )
        check(
            results,
            "the fitted bytes replaced the original image",
            bool(urls) and all(u != original for u in urls),
        )
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        if args.keep:
            print(f"      kept {tmp}")
        else:
            import shutil

            shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n{sum(results)}/{len(results)} checks passed")
    return 0 if results and all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
