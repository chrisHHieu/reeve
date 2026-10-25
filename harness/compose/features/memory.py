"""Auto memory as files: the prompt section that teaches it, its index, and the `auto_memory` tool
where the session has no local folder for it. What may be written is the core's memory door,
which this feature does not own."""

from __future__ import annotations

from harness.context.memory import auto_memory_tools
from harness.context.memory.admission import Admission
from harness.context.memory.prompt import memory_section

from .base import PROMPT_MEMORY, FeatureContext


def _joined(*blocks: str) -> str:
    return "\n\n".join(block for block in blocks if block)


class Memory:
    id = "memory"

    def setup(self, ctx: FeatureContext) -> None:
        memory_root, auto_memory = ctx.memory_root, ctx.inputs.auto_memory
        registry = ctx.registry
        if memory_root is not None:
            # The way to delete a memory is named only where the session holds it. `apply_patch` is
            # offered to some model families and not others, and 4 of the 11 sessions after
            # 2026-10-04 were told to use it without having it. The section is rendered
            # once the tool list is final, so it asks the registry the model will see.
            def local() -> str:
                section = memory_section(memory_root, tools=registry.names())
                if auto_memory is None:
                    return section
                # Only the MEMORY.md index is injected, because it is session-stable and rides the
                # cached prefix; topic files cost context only when read. The file tools are the
                # memory tools, so nothing dedicated goes on the wire.
                return _joined(
                    section, auto_memory.inject_block(via_files=True), auto_memory.progress_block(via_files=True)
                )

            ctx.prompt(PROMPT_MEMORY, local)
            return
        if auto_memory is None:
            return

        # No local folder: the same instruction, through the `auto_memory` tool.
        ctx.prompt(
            PROMPT_MEMORY,
            lambda: _joined(
                memory_section(auto_memory.root, via_tool=True), auto_memory.inject_block(), auto_memory.progress_block()
            ),
        )
        enabled = ctx.inputs.auto_memory_enabled

        def saving_enabled() -> bool:
            return bool(enabled()) if enabled is not None else True

        # The gate the file tools' door applies. A session on this path has no local
        # root, so a note cannot name a file of it.
        witnesses = ctx.witnesses
        auto_memory.admit = Admission(
            auto_memory.root,
            anchor=lambda raw: None,
            stamp=witnesses.stamp,
            unsettled=witnesses.unsettled,
            external=witnesses.external,
        ).admit
        registry.register_all(auto_memory_tools(auto_memory, saving_enabled=saving_enabled))
