"""Credential redaction — the one authoritative table of secret shapes.

A leaf module on purpose: the compaction block and the memory store both need it, and
a memory -> compaction import created a cycle (memory -> compaction -> tools -> memory).
High-precision shapes only — a provider-key prefix or a labeled assignment cannot be
confused with prose, while bare hex strings (a git sha, a session id) can, and a
redactor that mangles text trains nobody to trust it.
"""

from __future__ import annotations

import re
from typing import Any


# -- secret redaction ----------------------------------------------------------
#
# The compacted block is the part of the session that outlives its turns — it rides
# every later request, gets persisted with the session state, and outlives the tool
# result that once held the credential in full. A secret that survives into the summary
# therefore gains a LONGER life than it had in place, in a form the user is least likely
# to be re-reading carefully. Compaction text is redacted; the summarizer prompt also
# asks (and a prompt is judgement — this net is what makes the ask a guarantee).
#
# High-precision shapes only: a provider-key prefix or a labeled assignment cannot be
# confused with prose, while bare hex strings (a git sha, a session id) can — and a
# redactor that mangles text trains nobody to trust it.

_REDACTED = "[REDACTED]"

_SECRET_PATTERNS: list[tuple[Any, str]] = [
    # Provider-key shapes: the prefix is the identifier, the length is the tell.
    (re.compile(r"\bsk-(?:ant|proj|or|sv)[A-Za-z0-9_-]{16,}\b"), _REDACTED),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"), _REDACTED),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b"), _REDACTED),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"), _REDACTED),
    (re.compile(r"\bglpat-[A-Za-z0-9_-]{20,}\b"), _REDACTED),
    (re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"), _REDACTED),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), _REDACTED),
    (re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"), _REDACTED),
    (re.compile(r"\bhf_[A-Za-z0-9]{30,}\b"), _REDACTED),
    (re.compile(r"\bnpm_[A-Za-z0-9]{30,}\b"), _REDACTED),
    # JWT: three base64url segments.
    (re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,}\b"), _REDACTED),
    # Authorization headers: keep the label — "what kind of credential" is useful
    # context; the value is not.
    (re.compile(r"(?i)\b(authorization\s*[=:]\s*bearer\s+)[A-Za-z0-9._~+/=-]{8,}"), r"\1" + _REDACTED),
    # Labeled assignments: API_KEY=..., password: ... — the name survives, the value
    # does not. Requires 8+ charset chars so prose values ("none", "correct") pass.
    (
        re.compile(
            r"(?i)\b((?:api[_-]?key|apikey|secret|access[_-]?token|auth[_-]?token|"
            r"refresh[_-]?token|password|passwd|client[_-]?secret)\s*[=:]\s*)"
            r"[A-Za-z0-9._~+/=-]{8,}"
        ),
        r"\1" + _REDACTED,
    ),
    # PEM private key blocks, whole.
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.DOTALL), _REDACTED),
]


def redact_secrets(text: str) -> str:
    """Replace credential-shaped substrings with `[REDACTED]`, keeping labels.

    Applied to everything the compaction block carries — the LLM summary, the
    mechanically extracted state, and the verbatim user-message list — because all of
    it is re-sent on every later request. Idempotent by construction: `[REDACTED]`
    matches nothing.
    """
    if not text:
        return text
    for pattern, repl in _SECRET_PATTERNS:
        text = pattern.sub(repl, text)
    return text
