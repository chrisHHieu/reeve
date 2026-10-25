"""The workflow journal: completed `agent()` calls, so an interrupted run can resume.

A workflow is a long fan-out, and a timeout, a Stop or a crash otherwise throws away
every child that had already finished. The journal is the run's own record of them: one
JSONL line per completed `agent()` call — its POSITION in the run, a fingerprint of the
exact argument object, and the tool result the script received. A later run of the same
script + args + meta.name in the same session, with `resume=true`, replays those results
(position AND fingerprint must both match) and executes only what is missing.

The file lives beside the session's event log, under `<conversations>/workflows/`, which
is what lets it survive a restart. A host with no durable log (CLI, unit tests) simply
has no journaling. Nothing here may raise: a journal that cannot be read or written costs
the resume, never the run.

Journaling is POSITIONAL, not content-addressed — like the determinism-restricted
runners this feature comes from. A script whose prompts depend on the clock fingerprints
to a miss and re-runs those calls, which is the correct answer.
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("harness.workflow.journal")


def fingerprint(arguments: Any) -> str:
    """The identity of one `agent()` call: its exact arguments, canonically encoded."""
    try:
        text = json.dumps(arguments, sort_keys=True, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return ""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def run_key(*, name: str, script: str, args: Any) -> str:
    """What makes two workflow calls "the same run" for resume purposes."""
    payload = json.dumps(
        {"name": name, "script": script, "args": args},
        sort_keys=True,
        ensure_ascii=False,
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


class WorkflowJournal:
    """One run's journal file, loaded lazily and appended one completed call at a time."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._entries: dict[int, dict[str, Any]] = {}
        self._loaded = False

    @classmethod
    def for_log(cls, log: Any, key: str) -> Optional["WorkflowJournal"]:
        """The journal that sits beside a session's event log, or None when there is no
        durable log to sit beside."""
        path = getattr(log, "path", None)
        if not isinstance(path, Path) or not path.name:
            return None
        return cls(path.parent / "workflows" / f"{path.stem}.{key}.jsonl")

    def reset(self) -> "WorkflowJournal":
        """A fresh run: a stale partial journal must not replay into it."""
        self._entries = {}
        self._loaded = True
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.unlink(missing_ok=True)
        except OSError:
            logger.debug("could not reset the workflow journal", exc_info=True)
        return self

    def load(self) -> "WorkflowJournal":
        if self._loaded:
            return self
        self._loaded = True
        try:
            with open(self.path, encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except ValueError:
                        continue  # a torn line is a lost entry, not a broken run
                    if isinstance(entry, dict) and isinstance(entry.get("i"), int):
                        self._entries[entry["i"]] = entry
        except OSError:
            logger.debug("could not read the workflow journal", exc_info=True)
        return self

    def get(self, index: int, fp: str) -> Any:
        """The cached result for the index-th call, when the arguments still match."""
        entry = self._entries.get(index)
        if not entry or entry.get("fp") != fp or "result" not in entry:
            return None
        return entry.get("result")

    def put(self, index: int, fp: str, result: Any) -> None:
        entry: dict[str, Any] = {"i": index, "fp": fp, "result": result}
        self._entries[index] = entry
        self._append(entry)

    def end(self, status: str) -> None:
        """The run's own bound, so a reader can tell a finished journal from a torn one."""
        self._append({"t": "end", "status": status})

    def _append(self, entry: dict[str, Any]) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except (OSError, TypeError, ValueError):
            logger.debug("could not append to the workflow journal", exc_info=True)
