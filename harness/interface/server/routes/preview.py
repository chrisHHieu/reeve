"""Routes of the preview door: asking for an address for a file, and handing it back.

They are on the API, behind the sidecar token. The door itself is a separate listener
(`preview_door.py`) that these routes only hand keys to.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI


def register_preview(app: FastAPI, manager) -> None:
    @app.post("/v1/sessions/{session_id}/preview/grant")
    def session_preview_grant(session_id: str, body: dict) -> dict[str, Any]:
        """An address at the preview door for one of the session's files.

        `internet` is the person's own answer to "let this page use the internet?"; `single`
        grants that one file alone (a media element, a document's bytes).
        """
        body = body or {}
        return manager.preview_grant(
            session_id,
            str(body.get("path", "")),
            internet=body.get("internet") is True,
            single=body.get("single") is True,
        )

    @app.post("/v1/preview/revoke")
    def preview_revoke(body: dict) -> dict[str, Any]:
        return manager.preview_revoke(str((body or {}).get("key", "")))

    @app.get("/v1/sessions/{session_id}/design/versions")
    def session_design_versions(session_id: str, path: str = "") -> dict[str, Any]:
        """The versions of a design page, as the checks after its writes recorded them."""
        return manager.design_versions(session_id, path)

    @app.get("/v1/sessions/{session_id}/design/files")
    def session_design_files(session_id: str) -> dict[str, Any]:
        """The session's design pages with their last checks, and its DESIGN.md."""
        return manager.design_files(session_id)

    @app.post("/v1/sessions/{session_id}/design/shot")
    def session_design_shot(session_id: str, body: dict) -> dict[str, Any]:
        """A picture of one area of a design page at a width: a mark the person drew."""
        body = body or {}
        area = body.get("area") if isinstance(body.get("area"), dict) else {}
        return manager.design_shot(session_id, str(body.get("path", "")), body.get("width") or 0, area)

    @app.get("/v1/sessions/{session_id}/design/lock")
    def session_design_lock_state(session_id: str, path: str = "") -> dict[str, Any]:
        """Whether a design page is locked, and whether it changed since."""
        return manager.design_lock_state(session_id, path)

    @app.post("/v1/sessions/{session_id}/design/lock")
    def session_design_lock(session_id: str, body: dict) -> dict[str, Any]:
        """Measure a design page for its lock (`preview: true`, nothing written), or lock it: the
        harness writes the lock and the handoff the app is built from."""
        body = body or {}
        path = str(body.get("path", ""))
        if body.get("preview") is True:
            return manager.design_lock_preview(session_id, path)
        return manager.design_lock(session_id, path)

    @app.post("/v1/sessions/{session_id}/design/review")
    def session_design_review(session_id: str, body: dict) -> dict[str, Any]:
        """The design and the running app as pictures at a width, with what differs (`compare`;
        leave it out to change only the width)."""
        body = body or {}
        return manager.design_review(
            session_id,
            str(body.get("path", "")),
            str(body.get("url", "")),
            body.get("width") or 1440,
            compare=body.get("compare") is not False,
        )

    @app.post("/v1/sessions/{session_id}/design/review/keep")
    def session_design_review_keep(session_id: str, body: dict) -> dict[str, Any]:
        """Keep one difference between the app and its design as the app has it."""
        body = body or {}
        return manager.design_review_keep(session_id, str(body.get("path", "")), str(body.get("id", "")), str(body.get("text", "")))

    @app.post("/v1/sessions/{session_id}/design/review/done")
    def session_design_review_done(session_id: str, body: dict) -> dict[str, Any]:
        """The person looked at the app beside its design and called it done."""
        body = body or {}
        return manager.design_review_done(session_id, str(body.get("path", "")), str(body.get("url", "")), body.get("issues"))

    @app.post("/v1/sessions/{session_id}/design/export")
    def session_design_export(session_id: str, body: dict) -> dict[str, Any]:
        """A design page as a PDF, a picture, a zip or one HTML file, saved to Downloads, or a
        picture or the HTML handed back for the clipboard (`deliver: "data"`)."""
        body = body or {}
        return manager.design_export(
            session_id,
            str(body.get("path", "")),
            str(body.get("kind", "")),
            body.get("width") or 1440,
            internet=body.get("internet") is True,
            deliver="data" if body.get("deliver") == "data" else "file",
        )

    @app.post("/v1/downloads/reveal")
    def downloads_reveal(body: dict) -> dict[str, Any]:
        """Show a file Reeve saved to Downloads in its folder."""
        return manager.reveal_download(str((body or {}).get("path", "")))

    @app.get("/v1/editors")
    def editors() -> dict[str, Any]:
        """The editors on this machine a file can be opened in."""
        return manager.design_editors()

    @app.post("/v1/sessions/{session_id}/open-in")
    def session_open_in(session_id: str, body: dict) -> dict[str, Any]:
        """Open one of the session's files in an editor."""
        body = body or {}
        return manager.open_in_editor(session_id, str(body.get("path", "")), str(body.get("editor", "")))

    @app.post("/v1/browser/capture")
    def browser_capture(body: dict) -> dict[str, Any]:
        """The Browser tab's page as Reeve's browser loads it: a picture, its look, or its HTML."""
        body = body or {}
        return manager.browser_capture(str(body.get("url", "")), str(body.get("kind", "")), body.get("width") or 1280)
