"""Design: `design_guide`, `check_design`, `compare_design`, and the check after a design page is
written."""

from __future__ import annotations

from pathlib import Path

from harness.config.roots import allowed_dirs
from harness.tools.design import check_page, design_tools, find_browser
from harness.tools.design.compare_tool import compare_tool
from harness.tools.design.drift import find_drift

from .base import FeatureContext


class Design:
    id = "design"

    def setup(self, ctx: FeatureContext) -> None:
        # Local only, like `present`: the guide reads this machine's files and the check runs this
        # machine's browser, while a remote session's pages live on the other host.
        if ctx.ws is None or ctx.remote:
            return
        ws = ctx.ws
        roots = ctx.root_list or None
        # The browser is looked for once, when the session is built. With none on the machine the
        # model is not handed `check_design`, a tool that could only half work, and the check after a
        # write still reads the page's source and says the page was not opened.
        browser = find_browser()
        guide, check = design_tools(str(ws), roots=roots, browser=browser)
        ctx.registry.register(guide)
        if browser is not None:
            ctx.registry.register(check)
            ctx.registry.register(compare_tool(str(ws), roots=roots, browser=browser))

        # The last inventory of each page this session checked, so a write is told what it changed
        # (`tools/design/versions.py`). Kept for the engine's life: a new session starts fresh.
        seen: dict[str, list] = {}

        def checker(path: Path, shown: str, asked: tuple[str, ...] = ()):
            key = str(path.resolve())
            report = check_page(
                path, shown=shown, roots=allowed_dirs(ws, roots), browser=browser, previous=seen.get(key), asked=asked
            )
            if report.inventory:
                seen[key] = report.inventory
            return report

        # The engine checks a design page after each write that touches it (`loop/design_checks.py`).
        ctx.on_engine_ready(lambda engine: setattr(engine, "design_checker", checker))
        # After any write, a theme token a reviewed design uses that moved is said.
        ctx.on_engine_ready(lambda engine: setattr(engine, "design_drift", lambda paths: find_drift(Path(str(ws)), paths)))
