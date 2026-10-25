"""Which lines of a file have been seen — the small data structure behind the guard.

Split out of `observed.py` when the coverage fix pushed it one line past the architecture
ceiling. It is a pure value object (merge, contains, is-full) with no policy in
it, which is exactly the kind of thing that earns its own module rather than a comment
trimmed to satisfy a counter.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Coverage:
    """Which lines of a file the agent has actually seen, and how many there are.

    A per-call boolean was the first version of this and it produced FALSE refusals in the
    field (session 1030c218-40a): the model read `CLAUDE.md` as 1-120 then 121-287 — the
    whole file, in two calls — and the edit was refused because neither call alone covered
    it; it read four windows of `docs/MCP.md` including the one it was editing, and was
    refused because the file as a whole was not covered.

    Ranges are merged as they arrive, so contiguous windows add up to a full read and an
    edit can be judged by WHERE it lands. `full` marks an observation that covered
    everything by definition — a successful write.
    """

    ranges: list[tuple[int, int]] = field(default_factory=list)
    total: int = 0
    full: bool = False

    def add(self, start: int, end: int) -> None:
        """Record a window, merging anything overlapping or adjacent."""
        if end < start:
            return
        merged = [*self.ranges, (max(1, start), max(1, end))]
        merged.sort()
        out: list[tuple[int, int]] = []
        for lo, hi in merged:
            if out and lo <= out[-1][1] + 1:
                out[-1] = (out[-1][0], max(out[-1][1], hi))
            else:
                out.append((lo, hi))
        self.ranges = out

    def covers(self, start: int, end: int) -> bool:
        """Whether one recorded window contains `start..end`."""
        if self.full:
            return True
        return any(lo <= start and end <= hi for lo, hi in self.ranges)

    def is_full(self) -> bool:
        return self.full or (bool(self.total) and self.covers(1, self.total))
