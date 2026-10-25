"""DesignReviewMixin: the app beside its locked design, for the person (step 3).

The review tab shows the design and the running app as two whole-page pictures at one width, both
drawn by Reeve's own browser (the same engine, so what differs is the page and not the renderer),
with the differences `compare_design` finds listed beside them. For each difference the person asks
the agent to fix it (a message, in the GUI) or keeps it as the app has it; keeping one is recorded
on the lock by the difference's id, and a later compare says it as kept and does not count it. "Looks
good" records that the app was looked at and called done, with the app's tokens the design uses
and their values, so a later write that moves one is said (`drift.py`, step 5).

Only the app on this machine is opened, as for the tool.
"""

from __future__ import annotations

import base64
from typing import Any

from harness.tools.design.browser import render_app, render_page
from harness.tools.design.check import MAX_WIDTH, MIN_WIDTH
from harness.tools.design.compare_tool import on_this_machine, run_compare
from harness.tools.design.app_read import read_app
from harness.tools.design.drift import design_colors, reviewed_tokens
from harness.tools.design.locks import keep_difference, lock_state, mark_reviewed, read_lock


def _png(data: bytes) -> str:
    return "data:image/png;base64," + base64.b64encode(data).decode("ascii")


class DesignReviewMixin:
    def design_review(self, session_id: str, path: str, url: str, width: Any, compare: bool = True) -> dict[str, Any]:
        """The design and the app at `url` as pictures at `width`, and, with `compare`, the differences."""
        target, shown, error = self._lock_target(session_id, path)
        if target is None:
            return {"ok": False, "error": error}
        if not on_this_machine(url):
            return {"ok": False, "error": "only the app running on this machine is opened (http://localhost:…)"}
        try:
            chosen = int(width)
        except (TypeError, ValueError):
            return {"ok": False, "error": "the width is a number"}
        if not MIN_WIDTH <= chosen <= MAX_WIDTH:
            return {"ok": False, "error": "that width is out of bounds"}
        out: dict[str, Any] = {"ok": True, "page": shown, "url": url, "width": chosen}
        if compare:
            run = run_compare(target, shown, url, self._session_folders(session_id))
            if run.error:
                return {"ok": False, "error": run.error}
            if run.result.error:
                return {"ok": False, "error": f"the app did not open: {run.result.error}"}
            out.update(
                findings=run.result.findings,
                widths=run.result.widths,
                issues=run.result.issues,
                locked=run.locked_at is not None,
                changed_since_lock=bool(run.note),
            )
        design = render_page(target, [chosen], "0", shots=False, capture="page", timeout_s=45.0)
        app = render_app(url, [chosen], "0", shots=False, capture="page", timeout_s=45.0)
        out["design_png"] = _png(design.capture) if design.capture else None
        out["app_png"] = _png(app.capture) if app.capture else None
        if app.error:
            out["app_error"] = app.error
        out["state"] = lock_state(target)
        return out

    def design_review_keep(self, session_id: str, path: str, ident: str, text: str) -> dict[str, Any]:
        """Keep one difference as the app has it."""
        target, _, error = self._lock_target(session_id, path)
        if target is None:
            return {"ok": False, "error": error}
        if not ident or len(ident) > 200:
            return {"ok": False, "error": "which difference?"}
        if keep_difference(target, ident, text) is None:
            return {"ok": False, "error": "lock the design first: a kept difference is recorded on its lock"}
        return {"ok": True, "state": lock_state(target)}

    def design_review_done(self, session_id: str, path: str, url: str, issues: Any) -> dict[str, Any]:
        """The person looked at the app beside the design and called it done."""
        target, _, error = self._lock_target(session_id, path)
        if target is None:
            return {"ok": False, "error": error}
        if not on_this_machine(url):
            return {"ok": False, "error": "only the app running on this machine is reviewed"}
        try:
            open_issues = max(0, int(issues or 0))
        except (TypeError, ValueError):
            open_issues = 0
        lock = read_lock(target)
        folders = self._session_folders(session_id)
        tokens = reviewed_tokens(read_app(folders[0]), design_colors(lock)) if lock and folders else {}
        if mark_reviewed(target, url, open_issues, tokens=tokens) is None:
            return {"ok": False, "error": "lock the design first: a review is recorded on its lock"}
        return {"ok": True, "state": lock_state(target)}
