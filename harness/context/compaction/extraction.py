"""What is taken from the span mechanically, with no model involved, so it cannot be hallucinated."""

from __future__ import annotations

import json
from typing import Any, Iterator

from harness.safety.boundary.tool_paths import write_paths
from harness.safety.risk import RiskClass, classify
from harness.session.turns import is_user_turn
from harness.tools.checks import shown_command

from .tokens import _USER_MESSAGE_CLIP, _USER_MESSAGES_MAX, redact_secrets


def iter_tool_calls(span: list[dict[str, Any]]) -> Iterator[tuple[str, dict, Any]]:
    """`(name, arguments, result_content)` for every tool call in the span, in order. The engine uses
    it too, to learn which files a kept tail still shows (`view_cut.py`)."""
    # Each result belongs to the newest call before it with its id (`call_pairs`), keyed by the call
    # object. An id repeats across a session (Gemini's `call_0`), and a table keyed by id kept the
    # last call's result.
    from harness.loop.call_pairs import answered_calls

    results = {
        id(call): message.get("content")
        for _index, message, call in answered_calls(span)
        if message.get("role") == "tool" and call is not None
    }
    for message in span:
        if message.get("role") != "assistant":
            continue
        for call in message.get("tool_calls") or []:
            function = call.get("function") or {}
            try:
                arguments = json.loads(function.get("arguments") or "{}")
            except (ValueError, TypeError):
                arguments = {}
            yield str(function.get("name") or ""), arguments, results.get(id(call))


def result_status(result: Any) -> str:
    """`ok`, `error` or `exit N` for a shell result that reports one, else an empty string."""
    if not isinstance(result, str):
        return ""
    try:
        parsed = json.loads(result)
    except (ValueError, TypeError):
        return ""
    if not isinstance(parsed, dict):
        return ""
    if parsed.get("error"):
        return "error"
    if "exit_code" in parsed:
        code = parsed.get("exit_code")
        return "ok" if code in (0, "0") else f"exit {code}"
    return ""


def _recent_first(items: list[str], limit: int) -> list[str]:
    seen: list[str] = []
    for item in reversed(items):
        if item not in seen:
            seen.append(item)
        if len(seen) >= limit:
            break
    return seen


def extract_working_state(span: list[dict[str, Any]]) -> str:
    """The mechanical block code appends to the summary, from the span's tool-call records: files
    written, recent commands with their exit status, what was done off the machine, and the tools used.

    What a call did is read from its risk class, not from the words in its name: a name
    containing "create" or "save" was counted as a file write, so `create_issue` with a path argument
    went in the files list, and a patch, which names no `path`, was missing from it."""
    files: list[str] = []
    touched: list[str] = []
    commands: list[str] = []
    outside: list[str] = []
    tools: list[str] = []
    for name, arguments, result in iter_tool_calls(span):
        if name and name not in tools:
            tools.append(name)
        risk = classify(name)
        path = arguments.get("path") or arguments.get("file_path")
        written_here = write_paths(name, arguments)[0] if risk is RiskClass.WRITE_LOCAL else []
        # Every path any tool touched, in order: the raw material for the resume hint below. Writes
        # also go in their own, larger list, and reads appear only here.
        for touched_path in written_here or ([str(path)] if path else []):
            touched.append(str(touched_path))
        files.extend(str(p) for p in written_here)
        if name == "run_shell" and arguments.get("command"):
            status = result_status(result)
            line = shown_command(str(arguments["command"]), 160)
            commands.append(line + (f"  [{status}]" if status else ""))
        if risk is RiskClass.EXTERNAL:
            location = arguments.get("url") or arguments.get("path") or arguments.get("title")
            outside.append(f"{name}: {location}" if location else name)

    lines = ["## Working state (extracted mechanically from tool records)"]
    written = _recent_first(files, 20)
    if written:
        lines.append("Files written/edited (most recent first):")
        lines += [f"- {path}" for path in written]
    if commands[-10:]:
        lines.append("Recent shell commands:")
        lines += [f"- {command}" for command in commands[-10:]]
    done_outside = _recent_first(outside, 10)
    if done_outside:
        lines.append("Done outside this computer (most recent first):")
        lines += [f"- {item}" for item in done_outside]
    # The resume hint: the handful of paths the span's end was nearest, reads included. Stale memory
    # of a file is exactly what this block warns against, so it points at what to read again first.
    # Paths already shown as writes are skipped, and this section carries the rest.
    recent_touched = [path for path in _recent_first(touched, 5) if path not in set(written)]
    if recent_touched:
        lines.append("Last files touched (most recent first — re-read before trusting remembered contents):")
        lines += [f"- {path}" for path in recent_touched]
    if tools:
        lines.append("Tools used in the summarized span: " + ", ".join(sorted(tools)))
    if len(lines) <= 1:
        return ""
    # Shell commands are quoted verbatim here, and commands carry credentials.
    return redact_secrets("\n".join(lines))


def text_of(content: Any) -> str:
    """A message's text, whether plain or content parts (an image becomes a placeholder)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                parts.append(str(part.get("text", "")))
            elif isinstance(part, dict) and part.get("type") == "image_url":
                parts.append("[image]")
        return "\n".join(parts)
    return "" if content is None else str(content)


def both_ends(text: str, clip: int) -> str:
    """`text` within `clip` characters, keeping its two ends.

    The head alone lost whatever a long message ended with, and a long message is usually a paste
    with the instruction on one side of it. In the store, 158 of 1,620 user messages ran past the clip
    (2026-10-05), and a constraint stated after the paste ("…and do not touch the auth module") is on
    no disk for the model to read back."""
    if len(text) <= clip:
        return text
    mark = f" […{len(text) - clip:,} characters cut…] "
    room = max(2, clip - len(mark))
    head = room * 2 // 3
    return text[:head] + mark + text[-(room - head):]


def extract_user_messages(span: list[dict[str, Any]], *, clip: int = _USER_MESSAGE_CLIP) -> list[str]:
    """Every user message in the span, in order, trimmed of pasted bulk. They are kept mechanically.
    The summarizer is asked to list them as well, but the person's words are the ground truth of
    intent and must not depend on a model remembering to include them."""
    found: list[str] = []
    for message in span:
        # An image the agent asked to look at is not the person's words, and this list is the ground
        # truth of intent (see harness/session/turns.py).
        if not is_user_turn(message):
            continue
        text = " ".join(text_of(message.get("content")).split())
        if not text:
            continue
        # The words are kept verbatim minus anything shaped like a credential. The list rides every
        # later request, and a key pasted into the opening message would otherwise outlive every
        # other copy of itself.
        found.append(both_ends(redact_secrets(text), clip))
    return found


def cap_user_messages(
    messages: list[str], *, prior_dropped: int, limit: int = _USER_MESSAGES_MAX
) -> tuple[list[str], int]:
    """The first message plus the newest `limit - 1`, and the running total dropped.

    Newest-N alone was the rule while the limit was a fixed 40, where it almost never bit. Sizing the
    block against the window (`block_limits`) made small limits real, and on a small limit newest-N
    drops the opening message, the one that says what the session is for. That is what this list
    exists to guarantee: the summary is asked for the intent too, but the point of extracting the
    person's words mechanically is not to depend on a model remembering them.

    So it keeps the two ends, for the reason every other bound in this codebase does. The middle is
    what the summary is for."""
    if len(messages) <= limit:
        return messages, prior_dropped
    dropped = prior_dropped + (len(messages) - limit)
    if limit <= 1:
        return messages[:1], dropped
    return [messages[0], *messages[-(limit - 1) :]], dropped
