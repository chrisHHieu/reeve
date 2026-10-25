"""`/share` / `/unshare` — over the person's configured endpoint, never a fake cloud.

There is no Reeve sharing service behind this sidecar (a deliberate difference from a
hosted cloud share). `GET` answers what the UI needs to render the
rows honestly: `configured`, `can_revoke`, and any stored share. `POST` uploads the
exported transcript through `harness/session/share.py`; `POST …/unshare` revokes it only when
the configuration supports that. Without an endpoint both mutations refuse with the
exact reason the disabled slash row carries.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI

from ..manager import SessionManager


def register_share(app: FastAPI, manager: SessionManager) -> None:
    @app.get("/v1/sessions/{session_id}/share")
    def session_share_status(session_id: str) -> dict[str, Any]:
        return manager.share_status(session_id)

    @app.post("/v1/sessions/{session_id}/share")
    def session_share(session_id: str) -> dict[str, Any]:
        return manager.share_session(session_id)

    @app.post("/v1/sessions/{session_id}/unshare")
    def session_unshare(session_id: str) -> dict[str, Any]:
        return manager.unshare_session(session_id)
