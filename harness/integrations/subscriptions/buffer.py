"""The last few messages seen on each channel."""

from __future__ import annotations

import json
from collections import deque
from pathlib import Path
from typing import Optional


class ChannelBuffer:
    """Filled as channel messages arrive, so a subscribed agent can catch up on what it missed and
    the channel picker can offer channels the bot has already seen. With a `state_path` it is saved
    on every message, because a list of suggestions that empties on each restart is no use; the
    traffic is human-paced, so that is cheap."""

    def __init__(self, cap: int = 50, state_path: Optional[Path] = None) -> None:
        self._cap = cap
        self._path = Path(state_path) if state_path else None
        self._by_channel: dict[str, deque] = {}
        self._names: dict[str, str] = {}  # channel address -> display name
        if self._path is not None and self._path.exists():
            self._restore()

    def _restore(self) -> None:
        try:
            saved = json.loads(self._path.read_text())
            # The file is {"messages": ..., "names": ...}; the first version saved only the messages.
            messages = saved.get("messages", saved) if isinstance(saved, dict) else {}
            self._names = dict(saved.get("names") or {}) if isinstance(saved, dict) else {}
            for channel, held in messages.items():
                if isinstance(held, list):
                    self._by_channel[channel] = deque(held[-self._cap :], maxlen=self._cap)
        except (OSError, ValueError, AttributeError):
            pass  # a damaged file must not stop startup

    def record(self, channel: str, who: str, text: str, name: Optional[str] = None) -> None:
        self._by_channel.setdefault(channel, deque(maxlen=self._cap)).append({"from": who, "text": text})
        if name:
            self._names[channel] = name
        self._save()

    def _save(self) -> None:
        if self._path is None:
            return
        payload = {"messages": {c: list(m) for c, m in self._by_channel.items()}, "names": self._names}
        try:
            scratch = self._path.with_suffix(".tmp")
            scratch.write_text(json.dumps(payload))
            scratch.replace(self._path)
        except OSError:
            pass  # best effort: the copy in memory stays the one that counts

    def recent(self, channel: str, n: int = 10) -> list[dict]:
        held = list(self._by_channel.get(channel, ()))
        return held[-max(1, min(n, self._cap)) :]

    def name_for(self, channel: str) -> Optional[str]:
        """The display name a message carried for this channel, if any did."""
        return self._names.get(channel)

    def channels(self) -> list[dict]:
        """The channels seen so far, for the picker's recently-seen list."""
        seen = []
        for channel, held in self._by_channel.items():
            last = held[-1] if held else {}
            seen.append(
                {
                    "channel": channel,
                    "name": self._names.get(channel),
                    "last_from": last.get("from"),
                    "last_text": last.get("text"),
                }
            )
        return seen
