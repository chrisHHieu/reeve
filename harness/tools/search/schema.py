"""What the model is told about `grep`. The wording is model-facing, so change it only with evidence
from a run."""

from __future__ import annotations

from ..grep_engines import MAX_CONTEXT, MAX_MATCH_LINES, MODES, TYPES

SCHEMA = {
    "type": "function",
    "function": {
        "name": "grep",
        "description": (
            "Search file CONTENTS across the workspace and get matching lines back as "
            "file:line:text. .gitignore-aware (skips node_modules, build dirs, .git) and "
            "reads only matches, so it is the first move for locating a word, phrase or "
            "pattern in any text file, code or prose. Patterns are ripgrep regular expressions "
            "(Rust syntax: \\d, alternation with |, classes like \\w). Each match names "
            "its file and line — follow one with read_file (starting at that line) for "
            "surrounding context. Another folder on this machine (a sibling project) is "
            "searched the same way: give its path. Results come in pages: when a page "
            "says more exist, `offset` reads the next one. Read-only."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": (
                        "Ripgrep regular expression. Match on structure, not just "
                        "names: e.g. 'def \\w+_test', 'TODO|FIXME', or "
                        "'invoice #?\\d+'."
                    ),
                },
                "path": {
                    "type": "string",
                    "description": (
                        "Subdirectory (or single file) to search (default: whole "
                        "workspace). Narrowing a huge workspace starts here."
                    ),
                },
                "glob": {
                    "type": "string",
                    "description": (
                        "Filename glob to filter which files are searched, e.g. "
                        "'*.py' or '*.{ts,tsx}'."
                    ),
                },
                "type": {
                    "type": "string",
                    "enum": sorted(TYPES),
                    "description": (
                        "A file type instead of a glob: every extension of that language "
                        "('ts' is .ts, .tsx, .mts and .cts). Combines with `glob`."
                    ),
                },
                "ignore_case": {
                    "type": "boolean",
                    "description": "Match regardless of letter case (default false).",
                },
                "multiline": {
                    "type": "boolean",
                    "description": (
                        "Let the pattern span lines: `.` and `\\s` cross line breaks, so "
                        "'@app\\.route.*\\n\\s*def' finds a decorator and the function under "
                        f"it. A match shows up to {MAX_MATCH_LINES} of its lines."
                    ),
                },
                "max_results": {
                    "type": "integer",
                    "description": (
                        "Matches (or files) per page (default 100, max 1000). A broad "
                        "pattern that fills pages usually means the pattern or path should "
                        "be narrowed, not the page made bigger."
                    ),
                },
                "offset": {
                    "type": "integer",
                    "description": (
                        "Results to skip: the next page of a search that said more exist "
                        "(the result names the offset to use). Default 0."
                    ),
                },
                "output_mode": {
                    "type": "string",
                    "enum": list(MODES),
                    "description": (
                        "'content' (default): matching lines. 'files_with_matches': only "
                        "the paths, newest first — the cheapest way to find WHERE "
                        "something lives before reading. 'count': matches per file."
                    ),
                },
                "context": {
                    "type": "integer",
                    "description": (
                        f"Lines of context before and after each match (content mode, "
                        f"0-{MAX_CONTEXT}). Context lines print as `N-`, matches as `N:`."
                    ),
                },
                "before": {
                    "type": "integer",
                    "description": f"Lines before each match only (0-{MAX_CONTEXT}); overrides `context` on that side.",
                },
                "after": {
                    "type": "integer",
                    "description": f"Lines after each match only (0-{MAX_CONTEXT}); overrides `context` on that side.",
                },
            },
            "required": ["pattern"],
        },
    },
}
