"""How long a command is waited for, and the environment every command starts with."""

from __future__ import annotations

# Long enough for an install, a build or a test run by default. The cap stays at ten minutes on
# purpose. The backstop ceiling in `tools/deadline.py` (900 s) has to stay above every deadline a
# tool enforces on itself, and `tests/test_tool_deadline.py` pins that order. Expiry is not
# destructive anyway: the command becomes a background task and the session is woken when it ends,
# so a longer foreground wait gains the person nothing.
DEFAULT_TIMEOUT = 120.0
MAX_TIMEOUT = 600.0


def clamp_timeout(seconds: float) -> float:
    """A wait the model asked for, normalised and bounded.

    The schema says seconds, but a model with a habit of `timeoutMs` passes milliseconds. A value
    that only fits under the cap once divided by 1000 is read as milliseconds. Without this, a
    `300000` meant ten minutes and not five."""
    value = float(seconds)
    if value > MAX_TIMEOUT and value / 1000.0 <= MAX_TIMEOUT:
        value /= 1000.0
    return max(1.0, min(value, MAX_TIMEOUT))


# Defaults that discourage a command from blocking on a prompt.
NONINTERACTIVE_ENV = {
    "GIT_TERMINAL_PROMPT": "0",
    "DEBIAN_FRONTEND": "noninteractive",
    "PYTHONUNBUFFERED": "1",
    "PIP_NO_INPUT": "1",
    # Without this a child Python encodes stdout with the locale codec, and `print("ầ")` dies with
    # UnicodeEncodeError before the command does anything (four sessions).
    "PYTHONIOENCODING": "utf-8",
    # PYTHONIOENCODING stops at stdio. `open()` and `read_text()` with no encoding still used cp1252,
    # so a script reading a UTF-8 file died with UnicodeDecodeError (one session, after the stdio
    # fix). UTF-8 mode becomes Python 3.15's default (PEP 686).
    "PYTHONUTF8": "1",
}
