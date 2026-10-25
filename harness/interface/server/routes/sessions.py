"""Route group moved out of harness/interface/server/app.py `create_app`, unchanged."""
from __future__ import annotations

from __future__ import annotations
from typing import Any, Optional
from fastapi.responses import JSONResponse


def register_sessions(app, manager):
        @app.get("/v1/sessions")
        def sessions(
            workspace: str | None = None, include_children: bool = False
        ) -> dict[str, Any]:
            # Roots only unless asked: child sessions spawned by `task` are
            # real but hidden; forks are roots and stay listed whatever this says.
            return {"sessions": manager.list_sessions(workspace, include_children=include_children)}
        @app.get("/v1/sessions/{session_id}")
        def session_detail(session_id: str) -> dict[str, Any]:
            # One session's identity + its environment binding:
            # `environment_id` null = local, else the profile id whose host owns the
            # workspace. The profile is returned as {id, name} only.
            return manager.session_summary(session_id)
        @app.get("/v1/sessions/{session_id}/messages")
        def session_messages(session_id: str) -> dict[str, Any]:
            # `revert` is session state, not a message: the canonical transcript
            # is complete and unchanged, and a surface draws the undo boundary from this
            # marker (null when nothing is staged). Same read the live `revert` frame pushes.
            return {
                "messages": manager.session_messages(session_id),
                "revert": manager.revert_marker(session_id),
            }
        @app.get("/v1/sessions/{session_id}/children")
        def session_children(session_id: str) -> dict[str, Any]:
            # Direct `kind="subagent"` children, each with the manager's live busy/idle
            # status (P3). Every roots listing
            # hides children, so this is the one route that names them: the
            # sidebar nests the on-path child and a reloaded window seeds its registry.
            return {"children": manager.children_view(session_id)}
        @app.get("/v1/sessions/{session_id}/metrics")
        def session_metrics(session_id: str) -> dict[str, Any]:
            # Token spend + cache split, latency, retry/failure rates and tool efficiency,
            # folded from the session event log (harness/observability/metrics.py).
            return manager.session_metrics(session_id)
        @app.get("/v1/sessions/{session_id}/plan")
        def session_plan(session_id: str) -> dict[str, Any]:
            # The session's working plan file (`update_plan`): path + text, so a surface can
            # show the plan the agent is maintaining without touching the workspace.
            return manager.session_plan(session_id)
        @app.get("/v1/sessions/{session_id}/goal")
        def session_goal(session_id: str) -> dict[str, Any]:
            # The session's goal (harness/loop/goals.py): null when none is set, else the
            # durable phase + process-local activation + rounds + blocker. The GoalBar's
            # reload source; live moves arrive over the session socket as `goal` /
            # `goal_activation` frames.
            return {"goal": manager.goal_for_session(session_id)}
        @app.post("/v1/sessions/{session_id}/goal")
        async def session_goal_action(session_id: str, body: dict) -> dict[str, Any]:
            # The HUMAN's controls (the GoalBar's pause/resume/edit/clear plus the
            # composer `/goal` command's set). Distinct from the model's `goal` tool: the
            # user may resume a goal the human paused, and may raise a cap and resume
            # after it was exhausted. `set`/`resume` are refused for a persona that has
            # not opted into autonomous rounds (`scheduling`). A pause on a running
            # session aborts the live round.
            body = body or {}
            return await manager.goal_action(
                session_id,
                str(body.get("action", "")),
                objective=body.get("objective"),
                max_rounds=body.get("max_rounds"),
                reason=str(body.get("reason", "")),
                token_budget=body.get("token_budget"),
            )
        @app.post("/v1/sessions/{session_id}/fork")
        def session_fork(session_id: str) -> dict[str, Any]:
            # Branch the conversation into a new session seeded with its history (a
            # fork): the parent is untouched, the child records its lineage, and the
            # session-scoped standing approvals do NOT come along.
            return manager.fork_session(session_id)

        @app.post("/v1/sessions/{session_id}/tasks/promote")
        def session_promote_task(session_id: str) -> dict[str, Any]:
            # P6: promote to background
            #: the session's running foreground `task`
            # children become background jobs; their waiting tool calls return the running
            # envelope and their completions are injected into this session. `{ok: false,
            # error}` when nothing runs — a no-op, not a 4xx.
            return manager.promote_subagent(session_id)

        @app.post("/v1/sessions/{session_id}/compact")
        async def session_compact_now(session_id: str) -> dict[str, Any]:
            # On-demand compaction (`/compact`): condense the history
            # now, even below the automatic threshold. Async because the summarizer call is
            # awaited on the server's loop; the manager refuses mid-turn and holds the
            # running mark across it.
            return await manager.session_compact_now(session_id)

        @app.patch("/v1/sessions/{session_id}")
        def session_patch(session_id: str, body: dict) -> Any:
            body = body or {}
            if "environment_id" in body:
                # Bind, switch, or clear the session's SSH environment. `environment_id: null` ("local") clears it. The remote
                # workspace must be an absolute POSIX path; the profile must exist.
                result = manager.set_session_environment(
                    session_id, body.get("environment_id"), body.get("workspace")
                )
                if not result.get("ok"):
                    return JSONResponse(
                        {
                            "ok": False,
                            "error": result.get("error")
                            or "could not bind the remote environment",
                        },
                        status_code=400,
                    )
                return result
            if "pinned" in body or "archived" in body:
                return manager.set_session_flags(
                    session_id,
                    pinned=bool(body["pinned"]) if "pinned" in body else None,
                    archived=bool(body["archived"]) if "archived" in body else None,
                )
            return manager.rename_session(session_id, str(body.get("title", "")))
        @app.delete("/v1/sessions/{session_id}")
        def session_delete(session_id: str) -> dict[str, Any]:
            return manager.delete_session(session_id)
        @app.get("/v1/sessions/{session_id}/roots")
        def session_roots(session_id: str) -> dict[str, Any]:
            return {"roots": manager.get_roots(session_id)}
        @app.get("/v1/sessions/{session_id}/changes")
        def session_changes(session_id: str, scope: str = "uncommitted") -> dict[str, Any]:
            if scope in ("", "uncommitted"):
                return manager.session_changes(session_id)
            return manager.scope_changes(session_id, scope)
        @app.get("/v1/sessions/{session_id}/changes/diff")
        def session_change_diff(session_id: str, path: str = "", scope: str = "uncommitted") -> dict[str, Any]:
            if scope in ("", "uncommitted"):
                return manager.session_change_diff(session_id, path)
            return manager.scope_change_diff(session_id, scope, path)
        # Review's Source Control actions: the PERSON's git writes from the GUI.
        @app.post("/v1/sessions/{session_id}/scm/stage")
        def session_scm_stage(session_id: str, body: dict) -> dict[str, Any]:
            return manager.scm_stage(session_id, (body or {}).get("paths"))
        @app.post("/v1/sessions/{session_id}/scm/unstage")
        def session_scm_unstage(session_id: str, body: dict) -> dict[str, Any]:
            return manager.scm_unstage(session_id, (body or {}).get("paths"))
        @app.post("/v1/sessions/{session_id}/scm/discard")
        def session_scm_discard(session_id: str, body: dict) -> dict[str, Any]:
            return manager.scm_discard(session_id, (body or {}).get("paths"))
        @app.post("/v1/sessions/{session_id}/scm/commit")
        def session_scm_commit(session_id: str, body: dict) -> dict[str, Any]:
            body = body or {}
            return manager.scm_commit(
                session_id, str(body.get("message", "")), all_changes=bool(body.get("all"))
            )
        @app.post("/v1/sessions/{session_id}/scm/pull")
        def session_scm_pull(session_id: str) -> dict[str, Any]:
            return manager.scm_pull(session_id)
        @app.get("/v1/sessions/{session_id}/scm/pr")
        def session_scm_pr(session_id: str) -> dict[str, Any]:
            return manager.scm_pull_request(session_id)
        @app.post("/v1/sessions/{session_id}/scm/pr")
        def session_scm_create_pr(session_id: str, body: dict) -> dict[str, Any]:
            b = body or {}
            return manager.scm_create_pull_request(
                session_id, str(b.get("title", "")), str(b.get("body", "")),
                base=str(b.get("base", "")), draft=bool(b.get("draft")),
            )
        @app.post("/v1/sessions/{session_id}/scm/push")
        def session_scm_push(session_id: str) -> dict[str, Any]:
            return manager.scm_push(session_id)
        @app.post("/v1/sessions/{session_id}/scm/suggest-message")
        async def session_scm_suggest_message(session_id: str, body: dict) -> dict[str, Any]:
            return await manager.scm_suggest_message(session_id, all_changes=bool((body or {}).get("all")))
        @app.post("/v1/sessions/{session_id}/scm/review")
        async def session_scm_review(session_id: str, body: dict) -> dict[str, Any]:
            return await manager.scm_review(session_id, str((body or {}).get("scope") or "uncommitted"))
        @app.post("/v1/sessions/{session_id}/scm/hunk")
        def session_scm_hunk(session_id: str, body: dict) -> dict[str, Any]:
            b = body or {}
            return manager.scm_hunk(
                session_id, str(b.get("path", "")), b.get("index"), str(b.get("header", "")), str(b.get("action", "")),
            )
        @app.get("/v1/sessions/{session_id}/scm/log")
        def session_scm_log(session_id: str, limit: int = 50) -> dict[str, Any]:
            return manager.scm_log(session_id, limit)
        @app.post("/v1/sessions/{session_id}/reveal")
        def session_reveal(session_id: str, body: dict) -> dict[str, Any]:
            """Show a workspace file in the OS file manager (local, non-executing)."""
            body = body or {}
            return manager.reveal_path(session_id, str(body.get("path", "")))
        @app.post("/v1/sessions/{session_id}/roots")
        def session_add_root(session_id: str, body: dict) -> dict[str, Any]:
            body = body or {}
            return manager.add_root(
                session_id, str(body.get("path", "")), bool(body.get("writable", False))
            )
        @app.delete("/v1/sessions/{session_id}/roots")
        def session_remove_root(session_id: str, path: str) -> dict[str, Any]:
            return manager.remove_root(session_id, path)
        @app.get("/v1/sessions/{session_id}/artifacts")
        def session_artifacts(session_id: str) -> dict[str, Any]:
            return {"artifacts": manager.list_artifacts(session_id)}

        @app.get("/v1/sessions/{session_id}/files/index")
        def session_file_index(session_id: str) -> dict[str, Any]:
            return manager.file_index(session_id)  # one walk; the composer's "@" filters it as typed
        @app.get("/v1/sessions/{session_id}/files")
        def session_files(session_id: str, q: str = "", limit: int = 50) -> dict[str, Any]:
            """`/open`'s search, confined to the session's roots, noise folders pruned and a
            symlink out of a root refused (`file_search.py`); ranked and capped."""
            return manager.search_files(session_id, query=q, limit=limit)
        @app.get("/v1/sessions/{session_id}/artifacts/read")
        def session_artifact_read(session_id: str, path: str, encoding: str = "") -> dict[str, Any]:
            # `encoding`: the person named what a text file is saved as (file_text.OFFERED).
            return manager.read_artifact(session_id, path, encoding or None)
        @app.post("/v1/sessions/{session_id}/artifacts/reveal")
        def session_artifact_reveal(session_id: str, body: dict) -> dict[str, Any]:
            body = body or {}
            return manager.reveal_artifact(
                session_id, str(body.get("path", "")), str(body.get("mode", "reveal"))
            )
        @app.post("/v1/sessions/{session_id}/files/create")
        def session_file_create(session_id: str, body: dict) -> dict[str, Any]:
            """The file manager's New file / New folder (the toolbar).

            Confined to a session's WRITABLE roots; the path is relative to its root and a
            relative path that escapes is refused, as are credentials and `.git`.
            """
            body = body or {}
            return manager.create_file_entry(
                session_id,
                str(body.get("path", "")),
                str(body.get("kind", "file")),
            )
        @app.post("/v1/sessions/{session_id}/files/write")
        def session_file_write(session_id: str, body: dict) -> dict[str, Any]:
            """Save an edited text file (the file manager's Edit mode).

            `mtime_ms` is the token the read returned; a file that changed on disk since
            then is refused with `conflict` instead of being overwritten.
            """
            body = body or {}
            return manager.write_file_entry(
                session_id,
                str(body.get("path", "")),
                body.get("content"),
                body.get("mtime_ms"),
                # The encoding the file was shown in, when the person named one.
                str(body.get("encoding") or "") or None,
            )
        @app.post("/v1/sessions/{session_id}/files/rename")
        def session_file_rename(session_id: str, body: dict) -> dict[str, Any]:
            body = body or {}
            return manager.rename_file_entry(
                session_id, str(body.get("path", "")), str(body.get("name", ""))
            )
        @app.post("/v1/sessions/{session_id}/files/delete")
        def session_file_delete(session_id: str, body: dict) -> dict[str, Any]:
            """The file manager's Delete: to the system's trash, never a permanent delete."""
            body = body or {}
            return manager.delete_file_entry(session_id, str(body.get("path", "")))
        @app.post("/v1/sessions/{session_id}/files/move")
        def session_file_move(session_id: str, body: dict) -> dict[str, Any]:
            body = body or {}
            return manager.move_file_entry(
                session_id, str(body.get("path", "")), str(body.get("to", ""))
            )
        @app.get("/v1/sessions/{session_id}/jobs/output")
        def session_job_output(
            session_id: str, id: str = "", tail: str = ""
        ) -> dict[str, Any]:
            """The tail of ONE background job's output, for the panel that polls it.

            The panel's ANCHOR: read this once when a job is opened (or re-anchored), then
            append the `job_output` frames that arrive after `POST …/jobs/observe` turned
            them on. Reading it does not observe anything — a client that only polls this
            route gets no frames, and one that observes gets the backlog here plus the live
            tail on its socket.

            WHY it is not part of the list: the panel polls the job it is showing on a timer,
            and this text must come from the NON-consuming read — the model's
            `shell_task_output` owns the incremental cursor into the same buffer, so a panel
            read that took bytes would leave the model's next look at the job empty.

            `tail` is the number of characters off the END of everything the job has read:
            optional, default 4000, clamped to 0..20000 (unparseable or out-of-range values
            clamp, so a bad query string answers instead of 500ing). `truncated` is true when
            the job has produced more than came back, and `total_chars` is all it has produced.
            An unknown or unnamed job, an unknown session, one with no engine here, and a
            remote-bound session are `{ok: false, error}` sentences — no session log is written.
            """
            return manager.background_job_output(session_id, id, tail)
        @app.get("/v1/sessions/{session_id}/jobs")
        def session_jobs(session_id: str) -> dict[str, Any]:
            """The shell jobs this session's live executor holds, newest first.

            Read of in-memory state for the background-jobs panel: the command running now
            (its elapsed time comes from `started_at`) and the ones that finished recently
            with their exit codes. Empty when nothing runs here — no session log is written.
            """
            return manager.background_jobs(session_id)
        @app.post("/v1/sessions/{session_id}/jobs/observe")
        def session_job_observe(session_id: str, body: dict) -> dict[str, Any]:
            """Say whether this client is WATCHING one background job, by its `bg-N` id.

            `{"id": "bg-3", "on": true}` turns the `job_output` frames for that job on,
            `on: false` turns them off; the answer carries `observing`, how many clients hold
            the job now. It is REFERENCE-COUNTED, so two windows watching one job do not
            cancel each other out, and a count that reaches zero cannot go negative.
            Observing is not reading: the backlog still comes from `GET …/jobs/output` (the
            anchor), which is unchanged and does not observe on anybody's behalf because
            that is the client's decision, and the job's buffer stays readable for the model
            either way. A job that has already finished is an honest `{ok: false, error}`
            sentence ("Job bg-3 has finished; …") — its count was dropped when it settled, and
            there is nothing to stream. The job must exist first: a client observes a row it
            read off the list.
            """
            body = body or {}
            return manager.observe_background_job(
                session_id, str(body.get("id", "")), body.get("on", True)
            )
        @app.post("/v1/sessions/{session_id}/jobs/kill")
        def session_job_kill(session_id: str, body: dict) -> dict[str, Any]:
            """Stop one background job (the panel's Stop control), by its `bg-N` id.

            A job that had already finished comes back as a no-op with
            `already_finished: true`, not as an error — the panel asked to stop something
            that is over. Reuses the executor's own cross-platform kill.
            """
            body = body or {}
            return manager.kill_background_job(session_id, str(body.get("id", "")))
        @app.get("/v1/sessions/{session_id}/undoable")
        def session_undoable(session_id: str) -> dict[str, Any]:
            # Which calls can still be undone (Review ▸ Recorded of a reopened session).
            return manager.undoable_calls(session_id)
        @app.post("/v1/sessions/{session_id}/undo")
        def session_undo(session_id: str, body: dict) -> dict[str, Any]:
            """Put back the files one tool call changed (harness/tools/undo.py).

            Keyed by the call id the TOOL_FINISHED event carries, so the surface names a step
            the user actually watched happen rather than a path they had to type.
            """
            return manager.undo_tool_call(session_id, str((body or {}).get("call_id", "")))

        @app.post("/v1/sessions/{session_id}/revert")
        async def session_revert(session_id: str, body: Optional[dict] = None) -> dict[str, Any]:
            """Message-level undo.

            Stages a marker at a user message (`message_id` names a canonical index;
            omitted = the last visible user message). Interrupts a running turn first,
            like `/undo`. Nothing is deleted: the marker truncates the model's
            view, and the returned `message` is what the composer pulls back into the
            draft. A live viewer gets the `revert` frame so its boundary moves without a
            reload.
            """
            body = body or {}
            result = manager.revert_session(
                session_id, message_id=body.get("message_id")
            )
            if result.get("ok"):
                await manager.broadcast_session(
                    session_id,
                    {
                        "type": "revert",
                        "data": {
                            "action": "stage",
                            "revert": result.get("revert"),
                            "message": result.get("message"),
                        },
                    },
                )
            return result

        @app.post("/v1/sessions/{session_id}/unrevert")
        async def session_unrevert(session_id: str) -> dict[str, Any]:
            """Message-level redo.

            Walks the boundary forward to the next user message, or clears the marker
            when there is none (`/redo`).
            Refused mid-turn. The `revert` frame carries the new marker (null when
            cleared) so every open view redraws together.
            """
            result = manager.unrevert_session(session_id)
            if result.get("ok"):
                await manager.broadcast_session(
                    session_id,
                    {
                        "type": "revert",
                        "data": {
                            "action": "clear" if result.get("cleared") else "stage",
                            "revert": result.get("revert"),
                            "message": result.get("message"),
                        },
                    },
                )
            return result
