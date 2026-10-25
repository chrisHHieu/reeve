"""The server keeps its off-turn record (2026-09-25 review).

No logging was configured, so every `logger.info` in the package — memory extraction,
dreams, consolidation — was dropped at the root's WARNING default, and nobody could tell
whether a background pass had ever run.
"""

from __future__ import annotations

import logging

from harness.interface.server.run import configure_logging


def test_background_info_lands_in_a_file_and_warnings_still_reach_stderr(tmp_path, capsys):
    package = logging.getLogger("harness")
    saved = (package.level, list(package.handlers), getattr(package, "_reeve_configured", False))
    package._reeve_configured = False
    try:
        path = configure_logging(tmp_path)
        assert configure_logging(tmp_path) is None  # once per process
        logging.getLogger("harness.interface.server.manager.memory_jobs").info("auto memory dreamed for k")
        logging.getLogger("harness.integrations.lsp").warning("server went quiet")
        for handler in package.handlers:
            handler.flush()
        text = path.read_text(encoding="utf-8")
        assert "auto memory dreamed for k" in text and "server went quiet" in text
        err = capsys.readouterr().err
        assert "server went quiet" in err and "auto memory dreamed" not in err
    finally:
        for handler in package.handlers:
            if handler not in saved[1]:
                handler.close()
        package.handlers[:] = saved[1]
        package.setLevel(saved[0])
        package._reeve_configured = saved[2]
