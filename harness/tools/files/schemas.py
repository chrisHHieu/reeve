"""What the model is told about the file tools. The wording is model-facing, so change it only with
evidence from a run."""

from __future__ import annotations

from .window import DEFAULT_MAX_LINES

READ_SCHEMA = {
    "type": "function",
    "function": {
        "name": "read_file",
        "description": (
            "Read a text file and get its content back verbatim, headed by a metadata "
            "line naming the file and the line window (no line-number prefixes on the "
            "lines). This — not shell cat/type — is how to inspect a file. Large files "
            "are windowed: the metadata line says where to resume with start_line, and "
            "`grep` gives exact line numbers when you need to reference path:line. Read "
            "a file here BEFORE editing it: the edit tools match exact current text, "
            "and a fragment copied from memory rather than a recent read is the usual "
            "cause of a failed edit. A PDF reads as its extracted text with [page N] "
            "markers (a scanned page has no text layer and says so); a .docx, .xlsx or "
            ".pptx as its text; a notebook as its cells, each with its number, id and "
            "outputs; an image (png, jpg, gif, webp) is SHOWN to you, attached beside the "
            "result: look at a chart or screenshot you produced to check it. Read-only."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": (
                        "File path, relative to the workspace (or absolute inside a "
                        "session root)."
                    ),
                },
                "start_line": {
                    "type": "integer",
                    "description": (
                        "First line to show, 1-based (default 1). Pass the previous "
                        "result's continuation value to page through a long file."
                    ),
                },
                "max_lines": {
                    "type": "integer",
                    "description": (
                        f"How many lines to show (default {DEFAULT_MAX_LINES}). The "
                        "result also stops at a total-size budget, so a file of very "
                        "long lines returns fewer — the metadata line says which lines "
                        "were shown and the start_line to continue from. Long lines are "
                        "clipped either way."
                    ),
                },
            },
            "required": ["path"],
        },
    },
}

# -- Schemas for the borrowed write tools ------------------------------
#
# The toolkit's own descriptions are one line ("Write a UTF-8 text file under the configured
# root"), and the model sees that line for the tools it uses most. These follow a fixed order:
# (1) the capability in one sentence, (2) the operational contract, meaning what comes back and
# what the model will see, (3) the failure modes with their remedies, (4) when not to use it and
# what to prefer.
# Parameter names are locked to the toolkit's signatures — the schema override replaces
# the model-facing description only, never the behavior.


WRITE_FILE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "write_file",
        "description": (
            "Create a new text file or fully replace an existing one under the workspace. "
            "The result names the path written — the file's content is NOT echoed back, "
            "so keep your own copy of what you sent. Creates parent directories. "
            "Prefer replace_in_file for changing a fragment of an existing file: a full "
            "rewrite risks silently dropping content you have not read recently. "
            "overwrite=false turns this into create-only: an existing file fails with "
            "'File already exists' instead of being replaced."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": (
                        "File path, relative to the workspace (or absolute inside a "
                        "session root)."
                    ),
                },
                "content": {
                    "type": "string",
                    "description": (
                        "The ENTIRE new content of the file — this replaces whatever was "
                        "there. An empty string creates an empty file."
                    ),
                },
                "overwrite": {
                    "type": "boolean",
                    "description": (
                        "Replace the file if it exists (default true). false = create "
                        "only: the call fails rather than overwrite."
                    ),
                },
            },
            "required": ["path", "content"],
        },
    },
}


REPLACE_IN_FILE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "replace_in_file",
        "description": (
            "Edit an existing file by replacing an EXACT text fragment — the surgical "
            "way to change code or prose you have already read. The call counts matches "
            "first: unless the file contains exactly `expected_replacements` occurrences "
            "of `old`, it fails and tells you how many it found (e.g. 'Expected 1 "
            "replacement(s), found 3') — include more surrounding lines in `old` to make "
            "the match unique, then retry. `new` may be empty to delete the fragment. "
            "Fails if the file does not exist — create it with write_file instead. "
            "Reading the file first is not enforced, but a match copied from memory "
            "rather than from a recent read is the usual cause of a count mismatch."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "File to edit, relative to the workspace.",
                },
                "old": {
                    "type": "string",
                    "description": (
                        "The exact text to replace — copied verbatim from a recent read, "
                        "including indentation. Must appear exactly "
                        "`expected_replacements` times."
                    ),
                },
                "new": {
                    "type": "string",
                    "description": (
                        "The replacement text (same indentation conventions). Empty "
                        "string deletes `old`."
                    ),
                },
                "expected_replacements": {
                    "type": "integer",
                    "description": (
                        "How many occurrences of `old` must exist (default 1). The call "
                        "fails unless the file contains exactly that many."
                    ),
                },
            },
            "required": ["path", "old", "new"],
        },
    },
}


APPLY_PATCH_SCHEMA = {
    "type": "function",
    "function": {
        "name": "apply_patch",
        "description": (
            "Apply a Codex-style multi-file patch envelope — for changes spanning "
            "several files in one call. The patch MUST start with '*** Begin Patch' and "
            "end with '*** End Patch', with sections like '*** Add File: <path>', "
            "'*** Update File: <path>' (followed by @@ hunks of -old/+new lines), "
            "'*** Delete File: <path>', and '*** Move to: <path>' inside an Update "
            "section. Paths are relative to the workspace. The result reports changed, "
            "added, and deleted files; a malformed envelope or a hunk whose context "
            "lines do not match the file fails with the offending detail — fix the "
            "patch, do not fall back to write_file for the same change."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "patch": {
                    "type": "string",
                    "description": "The full patch envelope, Begin/End lines included.",
                },
            },
            "required": ["patch"],
        },
    },
}


APPLY_UNIFIED_DIFF_SCHEMA = {
    "type": "function",
    "function": {
        "name": "apply_unified_diff",
        "description": (
            "Apply a unified diff (git-format `--- a/<path>\\n+++ b/<path>` with @@ "
            "hunks) under the workspace — the format `diff -u` and `git diff` produce. "
            "Use it when you HAVE a diff in hand (from a tool, a user, or another "
            "agent); to edit a file you have read, replace_in_file is simpler. "
            "'/dev/null' as the old path creates the file; as the new path deletes it. "
            "The result reports the files changed and hunks applied; a hunk whose "
            "context does not match fails with that file named — regenerate the diff "
            "against the file's current content."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "diff": {
                    "type": "string",
                    "description": "The unified diff text.",
                },
            },
            "required": ["diff"],
        },
    },
}


BORROWED_SCHEMAS: dict[str, dict] = {
    "write_file": WRITE_FILE_SCHEMA,
    "replace_in_file": REPLACE_IN_FILE_SCHEMA,
    "apply_patch": APPLY_PATCH_SCHEMA,
    "apply_unified_diff": APPLY_UNIFIED_DIFF_SCHEMA,
}


#: `list_files` already takes `pattern` and `recursive` — it has always been able to glob.
#: Its aisuite docstring is one line, "List files under the configured root", and that line
#: is the whole description the model sees, so the capability was there and unreachable:
#: the model globs by shelling out to `find`, or not at all. This is not a new power, it is
#: the existing one written down.
LIST_FILES_SCHEMA = {
    "type": "function",
    "function": {
        "name": "list_files",
        "description": (
            "List files under the workspace, optionally matching a glob pattern. Use this "
            "to find files by NAME or extension (`*.py`, `*.pdf`, `quarterly_*.csv`) — it "
            "is faster than shelling out, and `grep` is the tool for searching file "
            "CONTENTS. .gitignore-aware (skips node_modules, build dirs, .git); most "
            "recently modified first; says when more files exist than it shows. Read-only."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Directory to list, relative to the workspace. Defaults to the whole workspace.",
                },
                "pattern": {
                    "type": "string",
                    "description": "Glob matched against the file NAME, e.g. '*.py', 'invoice_*', or '*.csv'. Defaults to every file.",
                },
                "recursive": {
                    "type": "boolean",
                    "description": "Descend into subdirectories. Defaults to true.",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Cap on paths returned (default 100).",
                },
            },
        },
    },
}

BORROWED_SCHEMAS.update({"list_files": LIST_FILES_SCHEMA})
