"""`compare_design`: the app as built, held against the locked design (step 2).

The model builds a design into the app, starts the app (it runs its dev server like any command),
and calls this with the address of the route. The harness opens that address in its own browser at
the widths the design was locked at, measures it with the lock's own script, and says what differs
from the lock (`compare.py`). The design side is the lock's measurement, so the app is held to the
version the person locked, not to whatever the page holds now; a page never locked is measured as it
is, and the result says so.

Only the app running on this machine is opened: an address on another host is the open web, which
this browser does not go to on the model's word.
"""

from __future__ import annotations

import base64
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

import aisuite as ai

from harness.config.roots import allowed_dirs, containing_root

from .. import cards
from ..contract import READ_ONLY_CAPS, ToolContract, ToolPresentation, attach_contract, clip
from ..files.access import escape_refusal
from .browser import Render, render_app
from .check import DEFAULT_WIDTHS
from .compare import Comparison, compare
from .handoff import measure_for_lock
from .handoff_js import HANDOFF_JS
from .locks import kept_ids, page_sha, read_lock
from .measure import MEASURE_JS
from .pages import is_design_file

#: The app's side: the check's measure, the lock's, and the words on the page.
COMPARE_JS = (
    f"Object.assign({MEASURE_JS.strip()}, {{ handoff: {HANDOFF_JS.strip()},"
    " body_text: (document.body.innerText || '').slice(0, 60000) })"
)
#: The app loads at three widths, and an unlocked design at three more.
COMPARE_TIMEOUT_MS = 120_000
_LOOPBACK = frozenset({"localhost", "127.0.0.1", "::1", "[::1]", "0.0.0.0"})

_SCHEMA = {
    "type": "function",
    "function": {
        "name": "compare_design",
        "description": (
            "After building a design into the app: open the running app at `url` in Reeve's own browser "
            "at the widths the design was locked at, and compare it with the locked design `page`: parts "
            "missing, in another order or another number of columns, design colors, fonts and type sizes "
            "the app does not use, words not shown, and the app running off the window. Start the app "
            "first (its dev server); only an address on this machine is opened. Fix what it reports, "
            "then call it again before saying the work is done."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "The route in the running app, e.g. http://localhost:5173/pricing.",
                },
                "page": {
                    "type": "string",
                    "description": "The design page it was built from, e.g. design/pricing.html.",
                },
            },
            "required": ["url", "page"],
        },
    },
}


def on_this_machine(url: str) -> bool:
    """An http(s) address whose host is this machine."""
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    host = (parsed.hostname or "").lower()
    return parsed.scheme in ("http", "https") and (host in _LOOPBACK or host.endswith(".localhost"))


def _when(ms: Optional[int]) -> str:
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ms / 1000)) if ms else ""


@dataclass
class CompareRun:
    """One compare: the result, the app's render (its shots), and what was said of the lock."""

    result: Comparison
    render: Render
    locked_at: Optional[int] = None
    note: str = ""
    error: str = ""


def run_compare(target: Path, shown: str, address: str, roots: list, *, browser: Optional[Path] = None) -> CompareRun:
    """Compare the app at `address` (already checked to be on this machine) with the design page
    `target`: its lock when it has one, else the page as it is. Differences the person kept as the
    app has them (`locks.kept_ids`) are said as kept and not counted. Shared by `compare_design`
    and the review tab (`design_lock.py`)."""
    lock = read_lock(target)
    note = ""
    if lock and lock.get("system"):
        widths = [int(width) for width in lock.get("widths") or DEFAULT_WIDTHS]
        design_system = list(lock["system"])
        inventory = (lock.get("inventory") or {}).get(str(min(widths))) or []
        locked_at = int(lock.get("locked_at") or 0) or None
        if page_sha(target) != lock.get("sha256"):
            note = (
                f"\n- To check: {shown} has changed since it was locked; this compares with the locked"
                " version, the one the app is built from."
            )
    else:
        locked = measure_for_lock(target, shown=shown, roots=roots, browser=browser)
        if locked.error:
            return CompareRun(result=Comparison(url=address, page=shown, widths=[]), render=Render(), error=f"{shown} could not be opened: {locked.error}")
        widths = list(locked.report.widths)
        design_system = locked.system
        narrow = min(locked.report.measured_widths, key=lambda m: int(m.get("width") or 0), default={})
        inventory = list(narrow.get("inventory") or [])
        locked_at = None
    order = sorted(widths, reverse=True)
    render = render_app(address, order, COMPARE_JS, shots=True, browser=browser)
    if render.error:
        result = Comparison(url=address, page=shown, widths=order, locked_at=locked_at, error=render.error)
    else:
        result = compare(
            url=address,
            page=shown,
            design_system=design_system,
            design_inventory=inventory,
            app_measured=[dict(width.measured) for width in render.widths],
            widths=order,
            locked_at=locked_at,
            accepted=kept_ids(target),
        )
    return CompareRun(result=result, render=render, locked_at=locked_at, note=note)


def compare_tool(workspace: str, roots: Optional[list] = None, *, browser: Optional[Path] = None):
    root = Path(workspace).resolve()

    def compare_design(url: Any = None, page: Any = None) -> dict[str, Any]:
        address = str(url or "").strip()
        shown = str(page or "").strip()
        if not address or not shown:
            return {"error": "compare_design needs `url` (the running app's route) and `page` (the design page)"}
        if not on_this_machine(address):
            return {
                "error": f"{address} is not on this machine: compare_design opens only the app running here"
                " (http://localhost:<port>/…). Start its dev server and pass that address."
            }
        allowed = allowed_dirs(root, roots)
        target = (root / shown).resolve()
        if containing_root(target, allowed) is None:
            return {"error": escape_refusal(allowed)}
        if not target.is_file() or not is_design_file(target):
            return {"error": f"Cannot compare with {shown}: it is not a design page under design/."}

        run = run_compare(target, shown, address, allowed, browser=browser)
        if run.error:
            return {"error": run.error}
        result, render, locked_at, note = run.result, run.render, run.locked_at, run.note
        widths = result.widths
        text = result.text(_when(locked_at)) + note
        out: dict[str, Any] = {
            "report": text,
            "_facts": result.facts(),
            "_card_meta": {
                "design_compare": {
                    "url": address,
                    "page": shown,
                    "summary": text.splitlines()[0].split(": ", 1)[-1].rstrip("."),
                    "lines": [line[2:] for line in text.splitlines()[1:] if line.startswith("- ")],
                    "issues": result.issues,
                    "locked": locked_at is not None,
                    "findings": result.findings,
                }
            },
        }
        if result.fix and not render.error:
            parts: list[dict[str, Any]] = []
            for width in render.widths:
                if width.png and width.width in (max(widths), min(widths)):
                    parts.append({"type": "text", "text": f"The app at {width.width} px"})
                    parts.append(
                        {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(width.png).decode("ascii")}}
                    )
            if parts:
                out["_attach"] = parts
        return out

    compare_design.__name__ = "compare_design"
    compare_design.__doc__ = _SCHEMA["function"]["description"]
    compare_design.__aisuite_tool_metadata__ = ai.ToolMetadata(  # type: ignore[attr-defined]
        name="compare_design", category="design", risk_level="low", capabilities=["design"], requires_approval=False
    )
    compare_design.__tool_schema__ = _SCHEMA  # type: ignore[attr-defined]
    attach_contract(compare_design, COMPARE_CONTRACT)
    return compare_design


# -- contract -----------------------------------------------------------------------------------


def _error_of(value: Any) -> str:
    return str(value.get("error") or "").strip() if isinstance(value, dict) else ""


def _render(arguments: dict[str, Any], value: Any) -> str:
    if _error_of(value):
        return f"Error: {_error_of(value)}"
    return str(value.get("report") or "") if isinstance(value, dict) else str(value)


def _facts(arguments: dict[str, Any], value: Any) -> dict[str, Any]:
    facts = value.get("_facts") if isinstance(value, dict) else None
    return dict(facts) if isinstance(facts, dict) else {}


def _call(arguments: dict[str, Any]) -> ToolPresentation:
    page = str((arguments or {}).get("page") or "").replace("\\", "/").rsplit("/", 1)[-1]
    summary = f"Compare the app with {page}" if page else "Compare the app with the design"
    return ToolPresentation(summary=summary, kind="read", card=cards.generic_call(summary, kind="read"))


def _result(arguments: dict[str, Any], value: Any, card_meta: Any = None) -> ToolPresentation:
    if _error_of(value):
        return ToolPresentation(summary=clip(_error_of(value)), kind="read")
    meta = card_meta.get("design_compare") if isinstance(card_meta, dict) else None
    if not isinstance(meta, dict):
        return ToolPresentation(summary="Compared the app with the design", kind="read")
    lines = [str(line) for line in meta.get("lines") or [] if isinstance(line, str)]
    return ToolPresentation(
        summary="Compared with the design: " + str(meta.get("summary") or ""),
        kind="read",
        card=cards.generic_result(content=lines) if lines else None,
    )


COMPARE_CONTRACT = ToolContract(
    render=_render,
    present_call=_call,
    present_result=_result,
    facts=_facts,
    timeout_ms=COMPARE_TIMEOUT_MS,
    capabilities=READ_ONLY_CAPS,
)
