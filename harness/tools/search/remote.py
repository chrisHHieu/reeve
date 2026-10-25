"""`grep` over a session's remote world, using POSIX `grep -rn -E` there (phase 2b)."""

from __future__ import annotations

from typing import Any, Optional

from ..grep_engines import CONTENT, COUNT, FILES

ENGINE = "remote-grep"


def remote_grep(
    backend: Any,
    pattern: str,
    path: str,
    glob: Optional[str],
    limit: int,
    mode: str = CONTENT,
    *,
    before: int = 0,
    after: int = 0,
    offset: int = 0,
    ignore_case: bool = False,
    multiline: bool = False,
    types: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Run the pattern remotely. Hits come back in the same `{file, line, text}` shape as a local
    search, and the result names the remote engine so a card can say where it searched.

    The remote side is POSIX `grep`, which has no multiline search: that is refused in words,
    never answered with an empty result that would read as "not found". Context lines are left
    out and the result says so."""
    if multiline:
        return {
            "error": (
                "multiline search is not available on a remote session (the host searches with "
                "POSIX grep); search for one line of it, then read_file around the match"
            )
        }
    try:
        # One past the page: asking for one more is the only way to know there were more.
        hits = backend.search_files(
            pattern,
            path or ".",
            pattern=glob or "*",
            max_results=offset + limit + 1,
            regex=True,
            ignore_case=ignore_case,
            includes=list(types),
        )
    except PermissionError as exc:
        return {"error": str(exc)}
    except ValueError as exc:
        return {"error": str(exc)}
    except OSError as exc:
        return {"error": f"grep failed: {exc}"}
    found = [
        {
            "file": str(hit.get("path") or ""),
            "line": int(hit.get("line") or 0),
            "text": str(hit.get("text") or "")[:300],
        }
        for hit in hits
    ]
    truncated = len(found) > offset + limit
    matches = found[offset : offset + limit]
    note = (
        {"note": "context lines are not available on a remote session; read_file around a match"}
        if (before or after) and mode == CONTENT
        else {}
    )
    if mode == FILES:
        # Derived from the capped hits, because the remote search has no files-only pass.
        files = list(dict.fromkeys(m["file"] for m in matches))
        return {
            "engine": ENGINE, "mode": mode, "count": len(files), "files": files,
            "truncated": truncated, "offset": offset,
        }
    if mode == COUNT:
        per_file: dict[str, int] = {}
        for match in matches:
            per_file[match["file"]] = per_file.get(match["file"], 0) + 1
        counts = [{"file": name, "count": number} for name, number in per_file.items()]
        return {
            "engine": ENGINE, "mode": mode, "count": len(matches), "counts": counts,
            "truncated": truncated, "offset": offset,
        }
    return {
        "engine": ENGINE,
        "mode": CONTENT,
        "count": len(matches),
        "matches": matches,
        "truncated": truncated,
        "offset": offset,
        **note,
    }
