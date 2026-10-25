"""A durable note that an irreversible call STARTED, so a restart never repeats it.

`TurnEngine.resume()` re-runs the trailing tool calls that have no result in the
persisted thread. That is right for a call which never ran, and it is how a suspended
approval finishes after a restart. It is wrong for a call that DID run and whose result
never reached disk — from the engine's side the two look identical, because the only
evidence it has is the absence of a result.

Reproduced before this module was written (two failing shapes, one control):

    single call, no approval   -> resume re-runs nothing        (nothing was persisted)
    batch: read then send      -> resume re-runs the send       SENT TWICE
    approval path              -> resume re-runs the send       SENT TWICE

The two failing shapes are the ones where a checkpoint landed BETWEEN the assistant
message and the call's own result: an earlier tool in the same batch wrote one, or the
approval card did. The window is not the microsecond after the effect — it spans the
whole time the outbound call is in flight, because the process can die while the request
is already at the far end.

The fix is not "know whether the email was sent". Nothing here can know that. It is to
know that we DO NOT know, and to stop pretending otherwise: record the intent before the
call, and on resume let a person decide instead of silently doing it again.

**The failure policy is the opposite of `tools/undo.py`, deliberately.** A snapshot that
fails must never block a write — a backup that failed is no reason to refuse the work. An
intent that fails to record MUST block: the note is the only thing standing between a
crash and a second live send, so running without it is running blind. The `pre_execute`
stage already fails closed, so raising is the whole implementation.

Scope is EXEC and EXTERNAL only:

  · READ has no effect to repeat.
  · EGRESS repeats the same bytes to the same place — a second GET is not a second event.
  · WRITE_LOCAL is covered by undo, and a repeated write lands the same content.
  · EXEC and EXTERNAL are the two where "again" is a different word: `terraform apply`,
    `npm publish`, a message, an invoice.
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Iterable, Optional

#: How many started-call notes are kept. They exist only to be read by the NEXT resume,
#: so this needs to cover one interrupted turn, not a history. Generous anyway: the
#: records are a few hundred bytes each.
DEFAULT_KEEP = 200


class IntentStore:
    """Call ids whose side effect may have happened, on disk, surviving the process.

    Records are never deleted on the hot path. Deleting one at the moment a result is
    produced would re-open the very window this closes — the result is not durable until
    the consumer checkpoints, and that moment belongs to the consumer, not to us. A note
    left behind after a successful call is harmless: `resume` only ever consults notes for
    calls that have NO result in the persisted thread. Pruning is by count, off-path.
    """

    def __init__(self, root: Path, *, keep: int = DEFAULT_KEEP) -> None:
        self.root = Path(root)
        self.keep = max(1, int(keep))
        self._lock = threading.Lock()
        self._index = self.root / "started.json"

    @classmethod
    def under(cls, roots: Iterable[Any], **kwargs: Any) -> Optional["IntentStore"]:
        """Beside the session's other machine-facing files, or nowhere.

        Not the scratch root, for the same reason `UndoStore` avoids it: a scratch dir is
        disposable, and a note that disappears when scratch is cleared is a note that was
        not there on the one restart it existed for.
        """
        writable = [r for r in roots or () if getattr(r, "writable", False)]
        if not writable:
            return None
        home = next(
            (r for r in writable if getattr(r, "label", "") != "scratch"), writable[0]
        )
        return cls(Path(home.path) / ".reeve" / "intents", **kwargs)

    # -- record -------------------------------------------------------------------

    def record(self, call_id: str, tool: str, arguments: Any = None) -> None:
        """Note that this call is about to run. RAISES if it cannot be made durable.

        The caller is a `pre_execute` listener, and that stage fails closed — so a raise
        here refuses the call, which is the intended behaviour: without the note, a crash
        during the call becomes a silent repeat.
        """
        if not call_id:
            return
        with self._lock:
            entries = self._read()
            entries[str(call_id)] = {
                "tool": str(tool or ""),
                "arguments": _plain(arguments),
                "at": time.time(),
            }
            if len(entries) > self.keep:
                # Oldest first. Only ever drops notes far older than one interrupted turn.
                stale = sorted(entries.items(), key=lambda kv: kv[1].get("at", 0.0))
                for key, _ in stale[: len(entries) - self.keep]:
                    entries.pop(key, None)
            self._write(entries)

    def pending(self, call_id: str) -> Optional[dict[str, Any]]:
        """The note for a call that started, or None. Never raises: a store that cannot
        be read tells us nothing, and "nothing" must not stop a resume from happening."""
        if not call_id:
            return None
        try:
            with self._lock:
                return self._read().get(str(call_id))
        except Exception:
            return None

    # -- storage ------------------------------------------------------------------

    def _read(self) -> dict[str, Any]:
        try:
            raw = json.loads(self._index.read_text(encoding="utf-8"))
        except Exception:
            return {}
        return raw if isinstance(raw, dict) else {}

    def _write(self, entries: dict[str, Any]) -> None:
        """Durable enough to be worth writing: flushed and fsynced, then swapped into
        place atomically. A half-written index would be worse than none — it would read as
        "no note" on exactly the restart the note exists for."""
        self.root.mkdir(parents=True, exist_ok=True)
        tmp = self._index.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(entries, handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, self._index)


def _plain(value: Any) -> Any:
    """A JSON-safe echo of the arguments, for the card a person will read on resume."""
    try:
        json.dumps(value)
        return value
    except Exception:
        return str(value)


def intent_listener(store: IntentStore, classify: Any) -> Any:
    """Pre-execute listener: note the call, or refuse it.

    `classify` is injected rather than imported so this module does not reach into the
    permission layer for one lookup — the same reason `snapshot_listener` takes
    `write_paths` as an argument.
    """
    from harness.safety.risk import RiskClass

    async def listener(execution: Any, nxt: Any) -> Any:
        if classify(execution.name) in (RiskClass.EXEC, RiskClass.EXTERNAL):
            # Deliberately unguarded: a failure here must reach `_run_pre`, which blocks.
            store.record(execution.call_id, execution.name, execution.arguments or {})
        return await nxt()

    return listener
