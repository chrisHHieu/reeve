"""ReviewAssistMixin — Review's two model calls, made when the person presses a button.

- **Write it for me** (`scm_suggest_message`): a commit message for what the commit would
  hold (the index, or everything), in the style of the branch's recent subjects.
- **Review code** (`scm_review`): findings on a scope's diff, each anchored to a line the
  diff shows, for the GUI to draw under that line.

Both are off-turn calls on the session's own provider and model, metered like titles and
memory work under `__commit_message__` / `__review__`, so their tokens are
counted and never folded into a session's turns. Neither can change anything: the message
lands in the commit box for the person to edit, the findings are notes the person fixes,
sends on, or dismisses. The diff is capped before it is sent, and a review says when it
was.
"""
from __future__ import annotations

import logging

import asyncio
import json
import re

from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("harness.manager")


#: The most diff text one call sends. A commit message needs the gist; a review needs the
#: lines, and says it covered only part of the change when the cap cut it.
MESSAGE_DIFF_CHARS = 24_000
REVIEW_DIFF_CHARS = 80_000
#: Findings a review returns at most: a person reads them one by one.
REVIEW_MAX_FINDINGS = 12
_SEVERITIES = ("bug", "risk", "nit")

_MESSAGE_PROMPT = (
    "You write git commit messages. Given a change as a unified diff and the repository's "
    "recent commit subjects, reply with ONLY the commit message: a subject line of at most "
    "72 characters in the imperative mood that matches the style of the recent subjects "
    "(their prefix convention, if any, and their language); then, only if the change needs "
    "it, a blank line and a short body saying why, wrapped at 72 characters. No quotes, no "
    "code fences, no preamble."
)

_REVIEW_PROMPT = (
    "You review a code change before it is committed. Read the unified diff and report only "
    "what is worth the author's time: logic errors, crashes, wrong edge cases, data loss, "
    "security holes, broken error handling, a test that cannot fail. Skip style and "
    "formatting unless it hides a bug. Reply with ONLY a JSON array, no prose and no code "
    "fence. Each element: {\"path\": the file path as the diff names it, \"line\": a line "
    "number on the NEW side that the diff shows (added or context), \"severity\": \"bug\" | "
    "\"risk\" | \"nit\", \"body\": one or two sentences that say what is wrong and what to "
    f"do}}. At most {REVIEW_MAX_FINDINGS}. Reply [] when nothing is worth raising."
)

_FILE = re.compile(r"^\+\+\+ (?:b/)?(.+)$")
_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")


def diff_lines(patch: str) -> dict[str, set[int]]:
    """path → the new-side line numbers the diff shows (added or context): the only lines a
    finding may point at, since the GUI can only draw a note under a line it shows."""
    shown: dict[str, set[int]] = {}
    path: Optional[str] = None
    line = 0
    for raw in patch.splitlines():
        if raw.startswith("diff --git "):
            path = None
            continue
        match = _FILE.match(raw)
        if match:
            path = None if match.group(1) == "/dev/null" else match.group(1).strip()
            continue
        hunk = _HUNK.match(raw)
        if hunk:
            line = int(hunk.group(1))
            continue
        if path is None or not line:
            continue
        if raw.startswith("+") or raw.startswith(" "):
            shown.setdefault(path, set()).add(line)
            line += 1
    return shown


def parse_findings(text: str, shown: dict[str, set[int]]) -> list[dict[str, Any]]:
    """The model's JSON array, kept only where it points at a file and a line the diff
    shows. A finding the GUI could not place is dropped rather than misplaced."""
    start, end = text.find("["), text.rfind("]")
    if start < 0 or end <= start:
        return []
    try:
        raw = json.loads(text[start : end + 1])
    except ValueError:
        return []
    findings: list[dict[str, Any]] = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        path = str(item.get("path") or "").strip().removeprefix("b/").removeprefix("./")
        try:
            line = int(item.get("line"))
        except (TypeError, ValueError):
            continue
        body = " ".join(str(item.get("body") or "").split())
        if not body or line not in shown.get(path, set()):
            continue
        severity = str(item.get("severity") or "risk").lower()
        findings.append({
            "path": path,
            "line": line,
            "severity": severity if severity in _SEVERITIES else "risk",
            "body": body[:600],
        })
        if len(findings) >= REVIEW_MAX_FINDINGS:
            break
    return findings


class ReviewAssistMixin:
    def _scope_patch(self, root: Path, scope: str) -> Optional[str]:
        """The unified diff of a scope, untracked files included as additions where the scope
        holds them (Uncommitted, Unstaged). None for a scope that cannot be read."""
        if scope in ("", "uncommitted"):
            argv = ["-c", "core.quotepath=off", "diff", "--no-color", "--no-ext-diff", "--no-renames", "HEAD"]
            if self._git(root, "rev-parse", "--verify", "--quiet", "HEAD") is None:
                argv = argv[:-1] + ["--cached"]
        else:
            spec = self._scope_command(root, scope)
            if spec is None or "error" in spec:
                return None
            if spec.get("same"):
                return ""
            argv = spec["argv"]
        patch = self._git(root, *argv) or ""
        if scope in ("", "uncommitted", "unstaged"):
            fresh = (self._git(root, "-c", "core.quotepath=off", "ls-files", "--others", "--exclude-standard") or "")
            for rel in fresh.splitlines()[:200]:
                patch += self._untracked_patch(root, rel)
        return patch

    @staticmethod
    def _untracked_patch(root: Path, rel: str) -> str:
        """A new file as git would diff it once added: every line an addition."""
        try:
            text = (root / rel).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return ""
        lines = text.splitlines()
        if not lines:
            return ""
        body = "\n".join(f"+{line}" for line in lines)
        return (
            f"diff --git a/{rel} b/{rel}\nnew file mode 100644\n--- /dev/null\n+++ b/{rel}\n"
            f"@@ -0,0 +1,{len(lines)} @@\n{body}\n"
        )

    async def _assist_call(self, session_id: str, purpose: str, system: str, user: str, **settings: Any) -> Any:
        engine = self.get_engine(session_id)
        if engine is None:
            raise RuntimeError("no model for this session")
        provider = self._off_turn_provider(engine.provider, purpose)
        return await asyncio.to_thread(
            provider.complete,
            model=engine.model,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            **settings,
        )

    async def scm_suggest_message(self, session_id: str, *, all_changes: bool = False) -> dict[str, Any]:
        """A commit message for the staged change (or, with `all_changes`, everything)."""
        root = self._scm_root(session_id)
        if root is None:
            return {"ok": False, "error": "not a git repository"}
        patch = self._scope_patch(root, "uncommitted" if all_changes else "staged") or ""
        if not patch.strip():
            return {"ok": False, "error": "nothing to describe: no changes in what would be committed"}
        subjects = (self._git(root, "log", "-n8", "--pretty=format:%s") or "").strip()
        clipped = patch[:MESSAGE_DIFF_CHARS]
        user = (
            f"Recent commit subjects:\n{subjects or '(none yet)'}\n\n"
            f"The change{' (cut short)' if len(patch) > len(clipped) else ''}:\n{clipped}"
        )
        try:
            turn = await self._assist_call(
                session_id, "commit_message", _MESSAGE_PROMPT, user,
                temperature=0.2, max_tokens=400, reasoning_effort="none",
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("commit message failed for %s", session_id, exc_info=True)
            return {"ok": False, "error": f"the model did not answer: {exc}"[:300]}
        text = (getattr(turn, "text", None) or "").strip().strip("`").strip()
        if not text:
            return {"ok": False, "error": "the model returned no message"}
        return {"ok": True, "message": text}

    async def scm_review(self, session_id: str, scope: str) -> dict[str, Any]:
        """Findings on a scope's diff, each on a line the diff shows."""
        root = self._scm_root(session_id)
        if root is None:
            return {"ok": False, "error": "not a git repository"}
        patch = self._scope_patch(root, scope)
        if patch is None:
            return {"ok": False, "error": "this view cannot be reviewed"}
        if not patch.strip():
            return {"ok": True, "findings": [], "truncated": False}
        clipped = patch[:REVIEW_DIFF_CHARS]
        truncated = len(patch) > len(clipped)
        if truncated:
            # End on a whole file: a hunk cut in half would anchor findings to lines that
            # are not what the file says.
            cut = clipped.rfind("\ndiff --git ")
            clipped = clipped[: cut + 1] if cut > 0 else clipped
        try:
            turn = await self._assist_call(session_id, "review", _REVIEW_PROMPT, clipped, max_tokens=4_000)
        except Exception as exc:  # noqa: BLE001
            logger.debug("review failed for %s", session_id, exc_info=True)
            return {"ok": False, "error": f"the model did not answer: {exc}"[:300]}
        findings = parse_findings(getattr(turn, "text", None) or "", diff_lines(clipped))
        return {"ok": True, "findings": findings, "truncated": truncated}
