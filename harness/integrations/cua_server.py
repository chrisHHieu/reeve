"""Reeve Computer Use — an MCP server over the desktop.

The shape is deliberate: READ the screen as an accessibility
tree (`get_app_state`), act on elements or pixels (`click`/`type`/`key`), and fall back
to vision (`screenshot`/`zoom`) only when the tree cannot answer. No bundled skill
carries the workflow (a fresh install ships zero skills) — each tool's own description
is the contract, with stable tool names.

Windows is the ONLY platform with the accessibility tree (UI Automation through the
`uiautomation` package); everywhere pixels come from `mss`, pointer and keyboard from
`pyautogui`, and the clipboard from `pyperclip`, so `screenshot`/`zoom`/`click`/`type`/
`key` work on macOS and Linux too (the OS still has to grant screen/input permissions).
A dependency missing — or a UIA-only tool on a non-Windows host — is a clear setup error
from that tool, never a server that dies, so arming it never breaks a session.

Run as a stdio MCP server:  python cua_server.py
"""

from __future__ import annotations

import os
import sys

# Launched by absolute script path, this file's own directory (`harness/integrations/`) lands at
# sys.path[0] and shadows the pip `mcp` SDK with Reeve's `harness/extensions/mcp` package —
# drop it before anything imports. Harmless when run as a module.
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:] = [p for p in sys.path if os.path.abspath(p or os.getcwd()) != _HERE]

import io
import threading
import time
from typing import Any, Optional

from mcp.server.fastmcp import FastMCP, Image
from harness.platform.spawn import spawn

mcp = FastMCP("computer-use")

# --- optional platform deps -----------------------------------------------------
try:  # Windows UI Automation
    import uiautomation as uia  # type: ignore

    _HAS_UIA = sys.platform == "win32"
    try:
        # uiautomation logs search timeouts to `@AutomationLog.txt` in the CURRENT
        # directory — the user's workspace, or this repository (one was committed by
        # accident). Reeve's own logs folder is where a diagnostic belongs, found through
        # `state_dir` like every other path: a folder spelled out here once created `Reeve\logs`
        # ahead of the state move, and the move then left the real store unread (2026-10-07).
        from harness.safety.secrets import state_dir

        _log_dir = os.path.join(state_dir(), "logs")
        os.makedirs(_log_dir, exist_ok=True)
        uia.Logger.SetLogFile(os.path.join(_log_dir, "uiautomation.log"))
    except Exception:  # a log path must never cost the desktop tools
        pass
except Exception:  # pragma: no cover - non-Windows dev machines
    uia = None  # type: ignore
    _HAS_UIA = False

try:
    import mss  # type: ignore

    _HAS_MSS = True
except Exception:  # pragma: no cover
    mss = None  # type: ignore
    _HAS_MSS = False

try:
    import pyautogui  # type: ignore

    pyautogui.FAILSAFE = False  # corner-kill interferes with scripted desktop work
    pyautogui.PAUSE = 0.05
    _HAS_GUI = True
except Exception:  # pragma: no cover
    pyautogui = None  # type: ignore
    _HAS_GUI = False

try:
    import pyperclip  # type: ignore

    _HAS_CLIP = True
except Exception:  # pragma: no cover
    pyperclip = None  # type: ignore
    _HAS_CLIP = False

#: The chord that pastes on this OS. `type_text` rides the clipboard so Unicode
#: (Vietnamese, emoji) survives pyautogui's key mapping; macOS pastes with Command.
_PASTE_CHORD = ("command", "v") if sys.platform == "darwin" else ("ctrl", "v")


def _need(flag: bool, what: str, pip_pkg: str) -> Optional[dict[str, Any]]:
    if flag:
        return None
    return {
        "error": f"{what} is not available on this machine — install it with `pip install {pip_pkg}`.",
        "os": sys.platform,
    }


# --- shared, mutated state ------------------------------------------------------
_LOCK = threading.Lock()
_LAST_SHOT: dict[str, Any] = {}  # {"png": bytes, "left": x, "top": y, "width": w, "height": h}
_LAST_TREE: dict[str, Any] = {}  # {"items": [...], "title": str, "pid": int, "controls": {index: ctrl}, "generation": int, "foreground": bool | None}
_GENERATION = 0  # bumped by every get_app_state, so a stale index can be refused
_STOPPED = False

_MAX_TREE_DEPTH = 14
_MAX_TREE_NODES = 220


def _stopped() -> Optional[dict[str, Any]]:
    if _STOPPED:
        return {"error": "Computer use was stopped for this session. Re-arm or restart to continue."}
    return None


# --- screenshot -----------------------------------------------------------------
@mcp.tool()
def screenshot() -> Any:
    """Capture the whole virtual screen as an image. Call this when the accessibility
    tree cannot answer, or to verify a visual result (a chart rendered, a dialog shown).
    The capture is cached: `zoom` crops THIS image."""
    guard = _need(_HAS_MSS, "Screen capture", "mss")
    if guard:
        return guard
    with _LOCK:
        with mss.mss() as sct:
            shot = sct.grab(sct.monitors[0])  # monitors[0] = the whole virtual screen
            png = _png_bytes(shot)
            _LAST_SHOT.clear()
            _LAST_SHOT.update(
                {"png": png, "left": shot.left, "top": shot.top, "width": shot.width, "height": shot.height}
            )
    return Image(data=_LAST_SHOT["png"], format="png")


def _png_bytes(shot: Any) -> bytes:
    from PIL import Image as PILImage  # mss ships with Pillow? No — Pillow is a Playwright-free dep

    img = PILImage.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


@mcp.tool()
def zoom(x0: int, y0: int, x1: int, y1: int) -> Any:
    """Crop a region out of the LAST `screenshot` — a close-up for small or ambiguous
    targets. Coordinates are the same global screen points the screenshot was taken in."""
    if not _LAST_SHOT:
        return {"error": "no screenshot yet — call screenshot() first"}
    left, top = _LAST_SHOT["left"], _LAST_SHOT["top"]
    x0, y0, x1, y1 = int(x0), int(y0), int(x1), int(y1)
    if x1 <= x0 or y1 <= y0:
        return {"error": "empty crop — x1 must be greater than x0, y1 greater than y0"}
    from PIL import Image as PILImage

    img = PILImage.open(io.BytesIO(_LAST_SHOT["png"]))
    box = (
        max(0, x0 - left),
        max(0, y0 - top),
        min(img.width, x1 - left),
        min(img.height, y1 - top),
    )
    if box[2] <= box[0] or box[3] <= box[1]:
        return {"error": "crop falls outside the last screenshot"}
    buf = io.BytesIO()
    img.crop(box).save(buf, format="PNG")
    return Image(data=buf.getvalue(), format="png")


# --- accessibility tree ---------------------------------------------------------
def _rect_box(rect: Any) -> Optional[list[int]]:
    """UIA bounding rectangles arrive as objects with .left/.top/.right/.bottom."""
    try:
        return [int(rect.left), int(rect.top), int(rect.right), int(rect.bottom)]
    except Exception:
        return None


def _walk(ctrl: Any, depth: int, out: list[dict[str, Any]], controls: Optional[dict[int, Any]] = None) -> None:
    if depth > _MAX_TREE_DEPTH or len(out) >= _MAX_TREE_NODES:
        return
    try:
        name = (ctrl.Name or "").strip()
        ctype = ctrl.ControlTypeName
        if name or ctype in ("ButtonControl", "DocumentControl", "EditControl", "ComboBoxControl",
                             "ListItemControl", "MenuItemControl", "TabItemControl", "CheckBoxControl",
                             "RadioButtonControl", "HyperlinkControl", "TreeItemControl"):
            out.append(
                {
                    "index": len(out),
                    "type": ctype.replace("Control", ""),
                    "name": name[:120],
                    "rect": _rect_box(ctrl.BoundingRectangle),
                    "enabled": bool(ctrl.IsEnabled),
                    "password": bool(getattr(ctrl, "IsPassword", False)),
                }
            )
            if controls is not None:
                controls[len(out) - 1] = ctrl
        for child in ctrl.GetChildren():
            _walk(child, depth + 1, out, controls)
            if len(out) >= _MAX_TREE_NODES:
                # One marker, not one per ancestor unwinding past the cap.
                if out[-1].get("type") != "truncated":
                    out.append({"index": len(out), "type": "truncated", "name": f"… tree cut at {_MAX_TREE_NODES} nodes", "rect": None, "enabled": False})
                return
    except Exception:
        return  # a dead or protected node is skipped, never fatal


@mcp.tool()
def get_app_state(app: str = "", window_title: str = "", handle: int = 0) -> Any:
    """Read an application as an accessibility tree: one line per interactive element
    with an `index`, type, name and screen rect. Click/type by `index` afterwards, or by
    coordinates from a screenshot. `app` matches the process or window title loosely;
    empty = the FOREGROUND window. `handle` (from a previous read) re-reads that exact
    window even after its title changed. Prefer this over screenshots — it is cheaper
    and works on background windows."""
    guard = _need(_HAS_UIA, "UI Automation (Windows UIA)", "uiautomation")
    if guard:
        return guard
    with _LOCK:
        target = None
        if handle:
            target = uia.ControlFromHandle(int(handle))
        elif window_title:
            target = uia.WindowControl(searchDepth=1, Name=window_title) or None
            if target is not None and not target.Exists(0, 0):
                # The window may carry a suffix the caller did not name ("uia" vs
                # "uia - File Explorer"): fall back to a top-level WINDOW whose name
                # contains the title — never a descendant control that merely carries
                # the same name (probe 2026-09-22: that matched a nav-pane item inside
                # another Explorer window, so a coordinate click could have landed in
                # the wrong window).
                target = uia.GetRootControl().Control(
                    searchDepth=6,
                    SubName=window_title,
                    ControlType=uia.ControlType.WindowControl,
                )
        elif app:
            target = uia.GetRootControl().Control(
                searchDepth=8, SubName=app, ControlType=uia.ControlType.WindowControl
            )
        else:
            target = uia.GetFocusedControl()
            # The focused control is often a leaf; climb to the owning window.
            while target is not None and target.ControlTypeName != "WindowControl":
                parent = target.GetParentControl()
                if parent is None:
                    break
                target = parent
        # A search that finds nothing must say so now: the lazy control otherwise
        # searches again on first use, with uiautomation's 10 s default, twice (live
        # 2026-09-25: a caller re-reading a window whose title had changed spent 20 s).
        if target is None or not target.Exists(1, 0.2):
            return {"error": f"no window matched app={app!r} window_title={window_title!r} handle={handle}"}
        items: list[dict[str, Any]] = []
        controls: dict[int, Any] = {}
        _walk(target, 0, items, controls)
        global _GENERATION
        _GENERATION += 1
        _LAST_TREE.clear()
        _LAST_TREE.update(
            {
                "items": items,
                "controls": controls,
                "title": target.Name or "",
                "pid": getattr(target, "ProcessId", 0),
                "generation": _GENERATION,
            }
        )
    # Whether this window is in the FOREGROUND, so a coordinate click resolved from
    # this tree cannot land on whatever is covering it. None when the platform cannot say — or when the search
    # landed on a control with no window of its own (handle 0), which is not a window
    # this tool could safely click in anyway.
    window_handle = 0
    try:
        top = target.GetTopLevelControl() or target
        window_handle = int(top.NativeWindowHandle)
        front = int(uia.GetForegroundWindow() or 0)
        foreground: Optional[bool] = (front == window_handle) if (front and window_handle) else None
    except Exception:
        foreground = None
    with _LOCK:
        # `click` reads it from here. It used to be returned and never stored, so the
        # coordinate fallback's foreground refusal could not fire (found 2026-09-25;
        # the tests seeded the key by hand and never saw the gap).
        if _LAST_TREE.get("generation") == _GENERATION:
            _LAST_TREE["foreground"] = foreground
    return {
        "title": _LAST_TREE["title"],
        "pid": _LAST_TREE["pid"],
        "rect": _rect_box(target.BoundingRectangle),
        "foreground": foreground,
        "handle": window_handle,
        "generation": _LAST_TREE["generation"],
        "elements": _LAST_TREE["items"],
    }


def _element_rect(index: int) -> Optional[list[int]]:
    for it in _LAST_TREE.get("items", []):
        if it.get("index") == index and it.get("rect"):
            return it["rect"]
    return None


def _invoke_element(index: int) -> Optional[str]:
    """The element's own accessibility action, or None when it has none.

    Invoke/Select run through UIA, so they work on a background window and steal no
    focus, cursor or clipboard — the agent-desktop pattern. Buttons and links
    invoke; list/tab/tree items select (what a single click does). Any COM failure
    falls through to the coordinate path.
    """
    ctrl = _LAST_TREE.get("controls", {}).get(index)
    if ctrl is None:
        return None
    try:
        kind = str(getattr(ctrl, "ControlTypeName", "") or "")
        if kind not in ("ButtonControl", "MenuItemControl", "HyperlinkControl",
                        "ListItemControl", "TabItemControl", "TreeItemControl",
                        "CheckBoxControl", "RadioButtonControl"):
            return None
        selection_first = kind in ("ListItemControl", "TabItemControl", "TreeItemControl", "CheckBoxControl", "RadioButtonControl")
        orders = (
            (uia.PatternId.SelectionItemPattern, uia.PatternId.InvokePattern)
            if selection_first
            else (uia.PatternId.InvokePattern, uia.PatternId.SelectionItemPattern)
        )
        for pattern_id in orders:
            pattern = ctrl.GetPattern(pattern_id)
            if pattern is None:
                continue
            if pattern_id == uia.PatternId.InvokePattern:
                if pattern.Invoke():
                    return "invoke"
            elif pattern.Select():
                return "select"
        legacy = ctrl.GetPattern(uia.PatternId.LegacyIAccessiblePattern)
        if legacy is not None and legacy.DefaultAction:
            if legacy.DoDefaultAction():
                return "legacy"
    except Exception:
        return None
    return None


# --- pointer & keyboard ---------------------------------------------------------
@mcp.tool()
def click(x: int = -1, y: int = -1, index: int = -1, button: str = "left", clicks: int = 1,
          generation: int = -1) -> Any:
    """Click once (or `clicks` times) with `button` at screen coordinates, or at the
    center of element `index` from the last `get_app_state`. One of x/y or index is
    required; index wins when both are given.

    An element click first tries the element's own accessibility action (invoke or
    select): it works on a background window and steals no focus, cursor or clipboard.
    Only when the element has no such action does it fall back to screen coordinates,
    and then only while that window is in the foreground — a coordinate click resolved
    from a covered window lands on whatever covers it. Pass the `generation` the read
    reported, and a stale index is refused instead of clicking the wrong element."""
    if stopped := _stopped():
        return stopped
    guard = _need(_HAS_GUI, "Mouse/keyboard control", "pyautogui")
    if guard:
        return guard
    if index >= 0:
        with _LOCK:
            expected = _LAST_TREE.get("generation")
            if generation >= 0 and expected is not None and generation != expected:
                return {
                    "error": (
                        f"the last get_app_state changed since it was read "
                        f"(generation {generation} -> {expected}) — read the app again"
                    )
                }
            if button == "left" and int(clicks) == 1:
                via = _invoke_element(index)
                if via:
                    return {"ok": True, "via": via, "index": index}
            rect = _element_rect(index)
            foreground = _LAST_TREE.get("foreground")
        if not rect:
            return {"error": f"element {index} is not in the last get_app_state — read the app again"}
        if foreground is False:
            return {
                "error": (
                    f"element {index} has no accessibility action and its window is not "
                    "in the foreground — bring it up first"
                )
            }
        # `rect` is [left, top, right, bottom] (`_rect_box`), so the centre is the
        # midpoint — `left + right // 2` put the click off the element entirely.
        x, y = (rect[0] + rect[2]) // 2, (rect[1] + rect[3]) // 2
    if x < 0 or y < 0:
        return {"error": "give x/y or an element index"}
    with _LOCK:
        pyautogui.click(int(x), int(y), clicks=int(clicks), button=button)
    return {"ok": True, "via": "coordinates", "x": int(x), "y": int(y), "button": button, "clicks": int(clicks)}


@mcp.tool()
def double_click(x: int, y: int) -> Any:
    """Double-click at screen coordinates (select a word, open a file)."""
    return click(x=x, y=y, button="left", clicks=2)


@mcp.tool()
def right_click(x: int, y: int) -> Any:
    """Right-click at screen coordinates — opens a context menu."""
    return click(x=x, y=y, button="right", clicks=1)


def _control_value(ctrl: Any) -> Optional[str]:
    """A control's current text, when it exposes one (ValuePattern, then legacy)."""
    try:
        pattern = ctrl.GetPattern(uia.PatternId.ValuePattern)
        if pattern is not None and isinstance(pattern.Value, str):
            return pattern.Value
    except Exception:
        pass
    try:
        pattern = ctrl.GetPattern(uia.PatternId.LegacyIAccessiblePattern)
        if pattern is not None and isinstance(pattern.Value, str):
            return pattern.Value
    except Exception:
        pass
    return None


@mcp.tool()
def type_text(text: str) -> Any:
    """Type text into the FOCUSED control. Unicode-safe (Vietnamese, emoji): the text
    rides the clipboard and a paste chord (Ctrl+V, Cmd+V on macOS), then the clipboard
    is restored. Focus the right field first (click it).

    Refuses a password field outright. After typing it reads the focused control back
    and reports `verified` (True/False, null when the control exposes no value): the
    deterministic half of the post-fill check, no model needed. `field` is the
    control's label, for the caller's own judgement."""
    if stopped := _stopped():
        return stopped
    guard = _need(_HAS_GUI and _HAS_CLIP, "Text entry", "pyautogui pyperclip")
    if guard:
        return guard
    with _LOCK:
        focused = None
        if _HAS_UIA and uia is not None:
            try:
                focused = uia.GetFocusedControl()
                if focused is not None and bool(getattr(focused, "IsPassword", False)):
                    # The clipboard is the wrong road into a credential field: the
                    # value would sit in the paste buffer and the history. Refuse and
                    # let the person type it (typesafe-computer-use's fill: false).
                    return {
                        "error": (
                            "the focused control is a password field — Reeve never "
                            "pastes secrets; type it yourself"
                        )
                    }
            except Exception:
                pass  # a focus probe that fails must not block ordinary typing
        previous = ""
        try:
            previous = pyperclip.paste()
        except Exception:
            pass
        pyperclip.copy(text)
        pyautogui.hotkey(*_PASTE_CHORD)
        time.sleep(0.08)
        try:
            pyperclip.copy(previous)
        except Exception:
            pass
        field = ""
        verified: Optional[bool] = None
        if focused is not None:
            try:
                field = str(focused.Name or "").strip()[:80]
                value = _control_value(focused)
                if value is not None:
                    wanted = text.strip()
                    verified = bool(wanted) and (wanted in value or value.strip() in wanted)
            except Exception:
                verified = None
    return {"ok": True, "typed": len(text), "field": field, "verified": verified}


@mcp.tool()
def key(combo: str) -> Any:
    """Press a key or chord: "enter", "esc", "tab", "ctrl+s", "ctrl+shift+esc",
    "alt+F4". Chords are one combination — never a sequence of independent presses."""
    if stopped := _stopped():
        return stopped
    guard = _need(_HAS_GUI, "Keyboard control", "pyautogui")
    if guard:
        return guard
    parts = [p.strip() for p in combo.split("+") if p.strip()]
    with _LOCK:
        if len(parts) == 1:
            pyautogui.press(parts[0])
        else:
            pyautogui.hotkey(*parts)
    return {"ok": True, "pressed": combo}


@mcp.tool()
def scroll(x: int, y: int, dy: int = -3) -> Any:
    """Scroll at screen coordinates. `dy` is notch-steps: negative scrolls down (the
    natural wheel direction), positive up."""
    if stopped := _stopped():
        return stopped
    guard = _need(_HAS_GUI, "Scroll", "pyautogui")
    if guard:
        return guard
    with _LOCK:
        pyautogui.moveTo(int(x), int(y))
        pyautogui.scroll(int(dy))
    return {"ok": True, "x": int(x), "y": int(y), "dy": int(dy)}


@mcp.tool()
def drag(x1: int, y1: int, x2: int, y2: int, duration_ms: int = 400) -> Any:
    """Press at (x1,y1), move, release at (x2,y2) — a real drag for sliders, moving
    windows, selection rectangles."""
    if stopped := _stopped():
        return stopped
    guard = _need(_HAS_GUI, "Drag", "pyautogui")
    if guard:
        return guard
    with _LOCK:
        pyautogui.moveTo(int(x1), int(y1))
        pyautogui.mouseDown()
        pyautogui.moveTo(int(x2), int(y2), duration=max(0.05, duration_ms / 1000))
        pyautogui.mouseUp()
    return {"ok": True, "from": [x1, y1], "to": [x2, y2]}


# --- windows & apps -------------------------------------------------------------
@mcp.tool()
def list_apps() -> Any:
    """List visible top-level windows: title + pid. The addressable surface for
    `get_app_state` and `open_application`."""
    guard = _need(_HAS_UIA, "Window enumeration (Windows UIA)", "uiautomation")
    if guard:
        return guard
    apps = []
    for win in uia.GetRootControl().GetChildren():
        try:
            if win.ControlTypeName == "WindowControl" and win.Name:
                apps.append({"title": win.Name[:100], "pid": win.ProcessId, "class": win.ClassName or ""})
        except Exception:
            continue
    return {"apps": apps}


@mcp.tool()
def open_application(target: str) -> Any:
    """Launch an app by name or open a file/URL with its default handler ("notepad",
    "C:\\path\\app.exe", "https://…"). Returns after the launch — give the app a moment,
    then `list_apps` / `get_app_state`."""
    import subprocess

    if stopped := _stopped():
        return stopped
    if not target.strip():
        return {"error": "target is required"}
    try:
        if target.lower().startswith(("http://", "https://")):
            import webbrowser

            webbrowser.open(target)
        elif os.path.isabs(target) and os.path.exists(target):
            if sys.platform == "win32":
                os.startfile(target)  # noqa: S606 - approval-gated upstream
            elif sys.platform == "darwin":
                spawn(["open", target])  # noqa: S603,S607 - the OS opener
            else:
                spawn(["xdg-open", target])  # noqa: S603,S607
        else:
            spawn([target], shell=True)  # noqa: S606 - by design; approval-gated
        return {"ok": True, "launched": target}
    except Exception as exc:
        return {"error": f"could not launch {target!r}: {exc}"}


@mcp.tool()
def clipboard_read() -> Any:
    """Read the system clipboard as text."""
    guard = _need(_HAS_CLIP, "Clipboard", "pyperclip")
    if guard:
        return guard
    return {"text": pyperclip.paste()}


@mcp.tool()
def clipboard_write(text: str) -> Any:
    """Write text to the system clipboard (stage a long string, then Ctrl+V it with `key`)."""
    guard = _need(_HAS_CLIP, "Clipboard", "pyperclip")
    if guard:
        return guard
    pyperclip.copy(text)
    return {"ok": True, "bytes": len(text)}


@mcp.tool()
def wait(seconds: float = 1.0) -> Any:
    """Pause between actions — an app is launching, an animation is running. 0.1–30s."""
    time.sleep(max(0.1, min(30.0, float(seconds))))
    return {"ok": True, "waited": seconds}


@mcp.tool()
def stop(reason: str = "") -> Any:
    """Stop computer control for this session: every later action tool refuses. Call
    this when the task is done or the user redirected you."""
    global _STOPPED
    _STOPPED = True
    return {"ok": True, "stopped": True, "reason": reason}


if __name__ == "__main__":
    mcp.run()  # stdio transport
