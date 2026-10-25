"""Skills: `load_skill`, `save_skill`, and the live skill menu."""

from __future__ import annotations

from pathlib import Path

from harness.extensions.skills import SkillLoader, save_skill_tool, skill_catalog_text, skill_tools

from ..session_helpers import loaded_skill_names, skill_dirs
from .base import FeatureContext


class Skills:
    id = "skills"

    def setup(self, ctx: FeatureContext) -> None:
        inputs = ctx.inputs
        # Agent folders come first so a person's global or workspace copy of the same name shadows
        # the bundle's, since later folders overwrite earlier ones in the loader.
        loader = SkillLoader([Path(d) for d in (inputs.extra_skill_dirs or [])] + skill_dirs(ctx.ws))
        ctx.provide("skill_loader", loader)
        # The per-session effective menu. The manager passes a callable, so
        # `load_skill` consults the live state per call: a Settings disable applies to running
        # sessions, and a skill created after this build is still loadable. The catalog itself is
        # injected per turn through the live context and not here, so the menu the model sees is live
        # as well, and skill changes apply from the next message with no new session. None keeps the
        # CLI and direct callers as they were.
        from harness.extensions.skills.store import record_skill_use

        skill_filter = inputs.skill_filter
        ctx.registry.register_all(skill_tools(loader, allowed=skill_filter, on_use=record_skill_use))
        # The worker-authors door: `save_skill` proposes installing a finished
        # skill, and `requires_approval` routes it through the standard approval card, so the
        # review-before-save rule holds with no special plumbing. Bundled files may only come from
        # this session's roots. A remote session has no local bundle source: its workspace is on the
        # other host and the installer reads files off this disk, so the allowed list is empty and the
        # tool refuses honestly and does not bundle a local spelling of a remote path.
        ctx.registry.register(
            save_skill_tool(
                allowed_dirs=[]
                if ctx.remote
                else ([root.path for root in (ctx.root_list or [])] or ([ctx.ws] if ctx.ws else []))
            )
        )

        def menu(engine) -> list[tuple[str, str]]:
            # The live skill menu is recomputed every turn like the roots list, so
            # a skill installed, enabled or disabled mid-session applies from the next message, with
            # no new session and no lost context.
            loader.rescan()
            allowed = skill_filter() if callable(skill_filter) else skill_filter
            parts: list[tuple[str, str]] = []
            text = skill_catalog_text(loader, allowed=allowed)
            if text:
                parts.append(("skills", text))
            # The disable countermand: instructions already loaded into this conversation keep
            # steering the model after the skill is turned off or deleted, since history cannot be
            # un-read. So a skill that is loaded and no longer available gets an explicit stop note,
            # worked out fresh each turn (enable it again and the note disappears; it is never saved).
            if engine is not None:
                available = set(loader.names()) if allowed is None else set(allowed)
                for name in sorted(loaded_skill_names(engine.messages) - available):
                    parts.append(
                        (
                            "countermand",
                            f'Note: the skill "{name}" has been disabled by the user — stop '
                            "following its instructions from here on.",
                        )
                    )
            return parts

        ctx.live(menu)
