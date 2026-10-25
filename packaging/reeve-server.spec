# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the bundled `reeve-server` (desktop sidecar).

One-DIR bundle (exe + `_internal/` support folder) shipped as the desktop app's `extraResources`.
It used to be a onefile binary in the externalBin slot, but onefile self-extracts its whole
archive to a temp dir on EVERY launch — 6-7s of "Starting…" splash (measured; the
actual Python import is ~0.5s). The wrinkles handled here:
  - aisuite is a regular pip dependency (git-pinned in pyproject.toml); collect harness +
    aisuite submodules from the venv.
  - uvicorn loads its protocol/lifespan impls dynamically → collect_all.
  - certifi's CA bundle must ship for TLS (OpenAI, web search, Telegram/Slack).
  - messaging extras (slack_bolt, telegram) are optional; collected if importable.

Cross-platform: paths are derived from this spec's own location (SPECPATH), never hardcoded,
so the same spec builds native binaries on macOS, Windows, and Linux. On Windows PyInstaller
appends `.exe` to `name`. The binary is built as a normal console app on every OS — a windowed
(console=False) build leaves sys.stdout/stderr as None, which breaks uvicorn's startup logging
and hangs the server. To avoid a console window flashing in the desktop app, the Electron shell
spawns this sidecar with `windowsHide` (CREATE_NO_WINDOW; surfaces/gui/electron/sidecar.ts), which
hides the window while keeping stdio intact.
"""

import importlib.util
import os
import sys

from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_submodules

# SPECPATH is injected by PyInstaller and points at this file's directory
# (<repo>/packaging). Derive everything else from it — no hardcoded paths.
PACKAGING = SPECPATH
ROOT = os.path.dirname(PACKAGING)

IS_WINDOWS = sys.platform == "win32"

# The desktop-control sidecar (`reeve-cua`) freezes only when its stack is in the
# build venv — Windows plus the `computer-use` extra (uiautomation/mss/pyautogui/
# pyperclip). find_spec, not import: importing uiautomation just to probe it spins up
# COM machinery in the build process. Without the extra the build succeeds but ships
# no CUA exe, and builtin_mcp_servers() omits the server instead of arming a dead one.
CUA = IS_WINDOWS and importlib.util.find_spec("uiautomation") is not None

# Experimental (use-at-your-own-risk) connectors are excluded from official builds: the code
# is stripped, not just disabled. Self-builders opt in with REEVE_EXPERIMENTAL=1; the
# loader in harness/integrations/connectors/descriptors.py treats the missing package as a no-op.
INCLUDE_EXPERIMENTAL = os.environ.get("REEVE_EXPERIMENTAL") == "1"

hiddenimports = []
datas = []
binaries = []

for pkg in ("harness", "aisuite", "mcp", "ddgs", "croniter", "docstring_parser"):
    hiddenimports += collect_submodules(pkg)

# Builtin personas ship as DATA, not code: personas/builtin/<id>/manifest.md plus their
# skills/<name>/SKILL.md. collect_submodules only takes .py files, so without this the
# packaged sidecar starts with NO builtin agents — the picker comes up empty and every
# persona-scoped skill silently disappears. (pyproject's package-data covers pip installs;
# PyInstaller needs its own instruction.) Keep this even if the persona set changes — it
# collects whatever non-.py files the package carries.
datas += collect_data_files("harness")

# Zero bundled feature skills (owner decision 2026-09-17): the old
# `skills-builtin` folder (computer-use / control-browser / web-gui-tester) is
# deleted and must not reach the installer even if a stale build tree still carries it.
# Persona manifests + their own skills/ dirs are the only skill data that ships.
datas = [d for d in datas if not any("skills-builtin" in str(p).replace("\\", "/") for p in d)]

# The Metrics page's self-review (harness/observability/self_review.py) runs the repository's own
# review and predictions scripts over the logs and reads the ledger for the days a change shipped.
# They are not part of the `harness` package, so a frozen build carries copies under reeve_review/.
# A tree without the ledger (the public one) builds without it, and its review judges no claims.
for rel in ("scripts/harness_review.py", "scripts/harness_predictions.py", "docs/decisions/predictions.json"):
    if rel.endswith(".py") or os.path.isfile(os.path.join(ROOT, rel)):
        datas.append((os.path.join(ROOT, rel), "reeve_review"))

if not INCLUDE_EXPERIMENTAL:
    hiddenimports = [
        m for m in hiddenimports if not m.startswith("harness.integrations.connectors.experimental")
    ]

# `websockets` powers the managed Slack relay client (relay_client.py). It is
# lazy-imported inside a function, so PyInstaller's static analysis misses it —
# collect it explicitly or the packaged relay adapter fails to open its socket.
# `pypdf`/`pypdfium2` are lazy-imported the same way (pdf_support.py) — and pypdfium2
# carries the libpdfium binary, which collect_all is what actually stages.
# `PIL` (harness/llm/images.py) is the same shape: imported inside the downscale helper so
# an oversized image can be fitted instead of refused. Without this the packaged app
# still RUNS (the code degrades to a refusal sentence) but the capability silently
# vanishes — the failure mode PyInstaller lazy-imports always have.
for pkg in ("uvicorn", "certifi", "anyio", "websockets", "pypdf", "pypdfium2", "PIL"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

# Windows has no system tz database; tzdata ships the zoneinfo files the scheduler needs.
# winpty (pywinpty) carries the ConPTY native pieces the terminal panel spawns shells with;
# it is imported inside pty_session.py, so PyInstaller's static analysis misses it.
if IS_WINDOWS:
    try:
        d, b, h = collect_all("tzdata")
        datas += d
        binaries += b
        hiddenimports += h
    except Exception:
        pass
    try:
        d, b, h = collect_all("winpty")
        datas += d
        binaries += b
        hiddenimports += h
    except Exception:
        pass

# [bedrock] extra — boto3 is lazy-imported (bedrock/converse.py) so static analysis
# misses it, and botocore's service-model JSON data dir only ships via collect_all.
for pkg in ("boto3", "botocore"):
    try:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hiddenimports += h
    except Exception:
        pass

# [whatsapp] extra: WhatsApp (personal). neonize carries its Go runtime as a DLL beside
# its modules and is imported inside link.py, so static analysis would miss both; segno draws the
# pairing QR code. Collected when the build venv has them.
for pkg in ("neonize", "segno"):
    try:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hiddenimports += h
    except Exception:
        pass

for pkg in ("slack_bolt", "telegram"):  # [messaging] extra — optional
    try:
        hiddenimports += collect_submodules(pkg)
    except Exception:
        pass

a = Analysis(
    [os.path.join(PACKAGING, "server_entry.py")],
    pathex=[ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    # No "PIL" here. Pillow is a SERVER capability (harness/llm/images.py fits oversized
    # images instead of refusing them), and an `excludes` entry empties it out of the
    # PYZ while the collect_all below still stages Pillow's sources AND its .pyd files
    # into _internal/ — two instructions that disagree. The on-disk copy happens to save
    # the capability today (frozen imports fall through to _internal, which is on
    # sys.path), so the contradiction is invisible until that fallback changes. Collect
    # it; do not exclude it. scripts/verify_frozen_sidecar.py is the behavioral check.
    excludes=["tkinter", "matplotlib", "PyQt5", "PySide6"]
    + ([] if INCLUDE_EXPERIMENTAL else ["harness.integrations.connectors.experimental"]),
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="reeve-server",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    # Console on every OS: a windowed build nulls stdout/stderr and hangs uvicorn. The desktop
    # shell hides the window on Windows via CREATE_NO_WINDOW when spawning the sidecar.
    console=True,
    # target_arch left unset → PyInstaller builds for the host architecture.
)
exes = [exe]
extra_binaries = a.binaries
extra_datas = a.datas
if CUA:
    # Second exe over the same COLLECT: shares _internal with the server, carries only
    # its own entry graph (harness.integrations.cua_server + the desktop stack). No PIL exclusion
    # here — the screenshot/zoom tools need Pillow, same as the server's image fitting.
    # Each EXE carries its own PYZ, so the server's graph is unaffected by this one.
    # Same console rule as the server: stdio is the protocol channel.
    b = Analysis(
        [os.path.join(PACKAGING, "cua_entry.py")],
        pathex=[ROOT],
        binaries=binaries,
        datas=datas,
        hiddenimports=["harness.integrations.cua_server"],
        hookspath=[],
        runtime_hooks=[],
        excludes=["tkinter", "matplotlib", "PyQt5", "PySide6"]
        + ([] if INCLUDE_EXPERIMENTAL else ["harness.integrations.connectors.experimental"]),
        noarchive=False,
    )
    pyz_b = PYZ(b.pure)
    cua_exe = EXE(
        pyz_b,
        b.scripts,
        [],
        exclude_binaries=True,
        name="reeve-cua",
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        console=True,
    )
    exes.append(cua_exe)
    extra_binaries += b.binaries
    extra_datas += b.datas

# Third exe over the same COLLECT: the code-mode child protocol (run_code). Inside the
# frozen server, sys.executable is reeve-server.exe and `python -m` is impossible —
# the server's own argparse rejects `-u -m` and the child dies before its first line
# (measured in production, session 3c33bb70's stderr tail). CodeProgram.child_command()
# targets this sibling when frozen. Same console rule: stdio is the protocol channel.
#
# A program imports what the model writes, and an archive holds only what the analysis
# reaches from the entry, so the child is given the whole standard library by name
# (`packaging/child_stdlib.py` says what is left out and why; the frozen check proves it).
sys.path.insert(0, PACKAGING)
from child_stdlib import is_test_module, top_level  # noqa: E402

child_stdlib = []
for name in top_level():
    spec_ = importlib.util.find_spec(name)
    if spec_ is not None and spec_.submodule_search_locations:
        child_stdlib += collect_submodules(name, filter=lambda sub: not is_test_module(sub))
    else:
        child_stdlib.append(name)
child = Analysis(
    [os.path.join(PACKAGING, "code_child_entry.py")],
    pathex=[ROOT],
    binaries=binaries,
    datas=datas,
    # Its sibling modules are imported statically by code_child; named anyway so the
    # frozen child never depends on the analyser following a relative import.
    hiddenimports=[
        "harness.tools.code_child",
        "harness.tools.code_child_args",
        "harness.tools.code_child_async",
        "harness.tools.code_child_values",
        *child_stdlib,
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "PIL", "PyQt5", "PySide6"]
    + ([] if INCLUDE_EXPERIMENTAL else ["harness.integrations.connectors.experimental"]),
    noarchive=False,
)
pyz_c = PYZ(child.pure)
code_child_exe = EXE(
    pyz_c,
    child.scripts,
    [],
    exclude_binaries=True,
    name="reeve-code-child",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
)
exes.append(code_child_exe)
extra_binaries += child.binaries
extra_datas += child.datas

# Onedir: dist/reeve-server/{reeve-server[.exe], _internal/}. The build scripts stage
# this whole folder into surfaces/gui/binaries/sidecar/ for electron-builder's `extraResources`.
coll = COLLECT(
    *exes,
    extra_binaries,
    extra_datas,
    strip=False,
    upx=False,
    name="reeve-server",
)
