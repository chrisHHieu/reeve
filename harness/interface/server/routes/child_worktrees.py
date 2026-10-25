"""An isolated child's own copy: what it holds, and the person's two answers.

`session_id` is the CHILD's. The calls are git work, so they run off the event loop.
"""
from __future__ import annotations

import asyncio
from typing import Any


def register_child_worktrees(app, manager):
        @app.get("/v1/sessions/{session_id}/worktree")
        async def session_worktree(session_id: str) -> dict[str, Any]:
            return await asyncio.to_thread(manager.child_worktree_view, session_id)

        @app.post("/v1/sessions/{session_id}/worktree/apply")
        async def session_worktree_apply(session_id: str) -> dict[str, Any]:
            return await asyncio.to_thread(manager.apply_child_worktree, session_id)

        @app.post("/v1/sessions/{session_id}/worktree/discard")
        async def session_worktree_discard(session_id: str) -> dict[str, Any]:
            return await asyncio.to_thread(manager.discard_child_worktree, session_id)
