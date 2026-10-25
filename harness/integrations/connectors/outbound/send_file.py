"""The `send_file` tool: put a file from the session into a connected chat.

It is a separate tool from `send_message` on purpose: a standing grant for `send_message` (the
pre-approval of a mention thread, say) must never stretch to cover uploading a file.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Optional

import aisuite as ai

from harness.safety.secrets import SecretStore

from ..failures import actionable
from ..messaging.senders import DEFAULT_FILE_SENDERS, FileSender
from .addressing import bot_token, looks_like_slack_name, parse_or_coerce, resolve_slack_channel

_SCHEMA = {
    "type": "function",
    "function": {
        "name": "send_file",
        "description": (
            "Upload a file from the session's workspace into a connected chat (Slack). "
            "`target` is the same handle send_message uses. Slack shows its own previews "
            "for pdf/csv/images — send the actual file, not a screenshot of it. For .html "
            "artifacts (which Slack can't preview) set as_screenshot=true to send a "
            "rendered PNG instead. This is a DISTINCT permission from send_message: it "
            "asks for approval even in threads where text replies are pre-approved."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "target": {
                    "type": "string",
                    "description": "Destination handle 'platform:chat_id[:thread]', e.g. 'slack:C0123:171234.5678'.",
                },
                "path": {
                    "type": "string",
                    "description": "The file to send — workspace-relative, or absolute within an allowed folder.",
                },
                "title": {"type": "string", "description": "Display title (defaults to the filename)."},
                "comment": {"type": "string", "description": "Short message posted with the file."},
                "as_screenshot": {
                    "type": "boolean",
                    "description": "HTML only: render the page headless and send a PNG preview instead of the raw file.",
                },
            },
            "required": ["target", "path"],
        },
    },
}

#: A sanity ceiling, well under Slack's own limit.
MAX_FILE_BYTES = 50 * 1024 * 1024

_HTML_SUFFIXES = (".html", ".htm")


def resolve_within(path: str, bases: list[Path]) -> Optional[Path]:
    """`path` resolved (a relative one is tried against each base), if it exists and lies inside one
    of the allowed bases; None otherwise."""
    given = Path(path).expanduser()
    candidates = [given] if given.is_absolute() else [base / given for base in bases]
    for candidate in candidates:
        try:
            resolved = candidate.resolve(strict=True)
        except OSError:
            continue
        if any(resolved.is_relative_to(base.resolve()) for base in bases):
            return resolved
    return None


def render_html_png(path: Path) -> bytes:
    """A local HTML file rendered headless to a 1280x800 PNG with the Playwright Chromium that
    ships for the browser connector."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": 1280, "height": 800})
            page.goto(path.as_uri())
            page.wait_for_timeout(500)  # let scripts (charts, tables) paint
            return page.screenshot(full_page=False)
        finally:
            browser.close()


def _refused(message: str) -> dict[str, str]:
    return {"error": message}


def make_send_file_tool(
    secrets: SecretStore,
    *,
    workspace: Optional[Path] = None,
    roots: Optional[list] = None,
    remote: bool = False,
    file_senders: Optional[dict[str, FileSender]] = None,
    render_html: Optional[Callable[[Path], bytes]] = None,
) -> Callable[..., Any]:
    """`send_file`. Targets are read, and the token found, the way `send_message` does it.

    With `remote=True` (an SSH-bound session) the tool stays listed but every call is refused: the
    file base would be this computer's disk while the deliverable is on the remote host, so a send
    that succeeded would upload the wrong machine's file.
    """
    senders = DEFAULT_FILE_SENDERS if file_senders is None else file_senders
    render = render_html or render_html_png
    bases = [Path(root.path) for root in (roots or []) if getattr(root, "path", None)]
    if workspace is not None:
        bases.append(Path(workspace))

    def payload_of(resolved: Path, as_screenshot: bool):
        """`(filename, bytes, None)` for what is to be sent, or `(None, None, the refusal)`."""
        if not as_screenshot:
            if resolved.stat().st_size > MAX_FILE_BYTES:
                return None, None, _refused(
                    "that file is over 50 MB, which is more than chat platforms "
                    "accept — share a link to it instead."
                )
            return resolved.name, resolved.read_bytes(), None
        if resolved.suffix.lower() not in _HTML_SUFFIXES:
            return None, None, _refused(
                "only .html files can be sent as a picture — send this one "
                "as a file instead (drop as_screenshot)."
            )
        try:
            return resolved.stem + ".png", render(resolved), None
        except Exception as exc:
            return None, None, _refused(f"could not render the page: {exc}")

    def send_file(
        target: str,
        path: str,
        title: Optional[str] = None,
        comment: Optional[str] = None,
        as_screenshot: bool = False,
    ) -> dict[str, Any]:
        if remote:
            return _refused(
                "send_file is not available in a remote session — the file "
                "would be read from this computer, not from the remote host. Run a "
                "local session to send a local file."
            )
        try:
            platform, chat_id, thread_id = parse_or_coerce(target)
        except ValueError as exc:
            return _refused(str(exc))
        sender = senders.get(platform)
        if sender is None:
            known = ", ".join(sorted(senders)) or "none"
            return _refused(
                f"files can't be sent on {platform} yet — {known} can receive them. "
                "Send a message with a link instead."
            )
        if platform == "slack" and looks_like_slack_name(chat_id):
            chat_id, why = resolve_slack_channel(secrets, chat_id)
            if why:
                return _refused(why)
        if not bases:
            return _refused(
                "this session has no folder to read files from — grant one in "
                "the app first, then try again."
            )
        resolved = resolve_within(path, bases)
        if resolved is None or not resolved.is_file():
            return _refused(
                f"no file at {path!r} that this session can read — check the "
                "path, or grant the folder it lives in."
            )
        token = bot_token(secrets, platform)
        if not token:
            return _refused(f"no bot token for {platform} — connect it first")

        filename, data, refusal = payload_of(resolved, as_screenshot)
        if refusal:
            return refusal
        result = sender(token, chat_id, thread_id, filename, data, title, comment)
        if result.ok:
            return {"ok": True, "file_id": result.message_id, "target": target, "filename": filename}
        return _refused(actionable(result.error or "file send failed", platform=platform))

    send_file.__name__ = "send_file"
    send_file.__doc__ = _SCHEMA["function"]["description"]
    send_file.__aisuite_tool_metadata__ = ai.ToolMetadata(
        name="send_file",
        category="messaging",
        risk_level="medium",
        capabilities=["messaging", "files"],
        requires_approval=True,
    )
    send_file.__tool_schema__ = _SCHEMA
    return send_file
