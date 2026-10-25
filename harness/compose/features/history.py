"""The session's own past: `search_history`, and the recap of this project's recent sessions."""

from __future__ import annotations

from .base import PROMPT_RECAP, FeatureContext


class History:
    id = "history"

    def setup(self, ctx: FeatureContext) -> None:
        inputs = ctx.inputs
        store, session_id = inputs.conversation_store, inputs.session_id
        if store is None or not session_id:
            return
        # Compaction is a projection: the canonical messages are still on disk and the model just had
        # no way to ask (item 2.5). This is `tools/spill.py` applied to compaction and not to tool
        # output: keep no more, and make what fell out retrievable.
        from harness.tools.history import HistorySearch, history_tools

        ctx.registry.register_all(
            history_tools(
                HistorySearch(
                    store,
                    session_id=session_id,
                    workspace=str(ctx.ws) if ctx.ws else (ctx.remote_root if ctx.remote else ""),
                )
            ),
            session_bound=True,
        )
        if not ctx.ws:
            return

        # Cross-session recall (P3b): the tail of this project's most recent sessions, so a new
        # conversation opens knowing where things stood and does not ask the person to explain it
        # again. It is session-stable like the memories, so it rides the same cached prefix, and a
        # project with no history renders nothing.
        def recap() -> str:
            from harness.context.memory.recap import render_recent_work

            return render_recent_work(store, session_id=session_id, workspace=str(ctx.ws))

        ctx.prompt(PROMPT_RECAP, recap)
