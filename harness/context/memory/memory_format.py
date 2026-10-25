"""The on-disk shape of one auto-memory file.

    ---
    name: no-db-mocks
    description: Integration tests must hit a real database, not mocks
    metadata:
      type: feedback
      modified: 2026-09-24T10:00:00+00:00
    ---
    Integration tests hit a real database.
    **Why:** … **How to apply:** … Related: [[testing-strategy]]

`description` is what recall and a reader judge relevance by without opening the file;
`name` is the slug a `[[name]]` link points at. The model writes these files itself with
the ordinary file tools, so this module READS whatever shape arrives — the
nested one, the flat `type:` one every earlier file used, or a file carrying both
blocks (the duplicated-frontmatter files the old write path produced) — and WRITES one.

`upgrade_dir` moves a directory written in the earlier shape to this one, once, with a
backup: it is the migration, idempotent behind a marker file.
"""

from __future__ import annotations

import re
import shutil
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

#: Keys that live under `metadata:` in the rendered file. Everything else is top level.
_NESTED = ("type", "modified")
_TOP = ("name", "description")

_BLOCK_RE = re.compile(r"\A\s*---\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)", re.S)

#: The format this module writes; `upgrade_dir` stamps it into `FORMAT_MARKER`.
FORMAT_VERSION = "cc-1"
FORMAT_MARKER = ".format"

#: A `description` is one line a person (or a recall pass) reads instead of the file.
DESCRIPTION_MAX_CHARS = 150

#: The earlier body convention for relations: `**Liên quan:** [A](a.md) · [B](b.md)`.
_RELATION_LINK_RE = re.compile(r"\[[^\]]*\]\(([^)\s#]+\.md)(?:#[^)]*)?\)")


def split(text: str) -> tuple[dict[str, str], str]:
    """`(meta, body)`. Every LEADING frontmatter block is consumed and merged — the first
    value for a key wins — so a file that grew a second block is read as one."""
    meta: dict[str, str] = {}
    rest = text or ""
    while True:
        match = _BLOCK_RE.match(rest)
        if not match:
            break
        for key, value in _parse_block(match.group(1)).items():
            if value and not meta.get(key):
                meta[key] = value
        rest = rest[match.end():]
    return meta, rest


def _parse_block(block: str) -> dict[str, str]:
    """Flat `key: value` lines plus one level of indented keys under `metadata:`."""
    out: dict[str, str] = {}
    in_metadata = False
    for raw in block.splitlines():
        if not raw.strip() or ":" not in raw:
            continue
        indented = raw[:1] in (" ", "\t")
        key, value = raw.split(":", 1)
        key, value = key.strip(), value.strip()
        if not indented:
            in_metadata = key == "metadata" and not value
            if in_metadata:
                continue
        elif not in_metadata:
            continue
        out[key] = _unquote(value)
    return out


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def render(meta: dict[str, str]) -> str:
    """The frontmatter block: name/description on top, the rest
    under `metadata:`. Empty values are left out."""
    lines = ["---"]
    for key in _TOP:
        value = _one_line(meta.get(key, ""))
        if value:
            lines.append(f"{key}: {value}")
    nested = [k for k in _NESTED if _one_line(meta.get(k, ""))]
    nested += [k for k in meta if k not in _TOP and k not in _NESTED and _one_line(meta[k])]
    if nested:
        lines.append("metadata:")
        lines += [f"  {k}: {_one_line(meta[k])}" for k in nested]
    lines.append("---")
    return "\n".join(lines) + "\n"


def _one_line(value: object) -> str:
    return " ".join(str(value or "").split())


def fold(text: str) -> str:
    """Lowercase and strip what Vietnamese orthography adds to Latin letters.

    NFD decomposition strips the tone and breve marks; `đ` has no decomposition — it is a
    letter of its own — so it maps explicitly, because a user typing `duoc` means `được`
    every single time.
    """
    decomposed = unicodedata.normalize("NFD", text.lower())
    stripped = "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")
    return stripped.replace("đ", "d")


def slug(text: str, *, max_words: int = 6) -> str:
    """A kebab-case file stem, readable for Vietnamese: diacritics fold to their base
    letters (`nguyên tắc tích` → `nguyen-tac-tich`) instead of being dropped mid-word
    (the earlier slug produced `nguy-n-t-c-t-ch`)."""
    folded = unicodedata.normalize("NFKD", (text or "").replace("đ", "d").replace("Đ", "D"))
    ascii_text = folded.encode("ascii", "ignore").decode("ascii").lower()
    words = re.findall(r"[a-z0-9]+", ascii_text)
    return "-".join(words[:max_words]) or "note"


def describe(body: str) -> str:
    """A one-line description from a body: its first heading or first line of text."""
    for line in (body or "").splitlines():
        text = line.strip().lstrip("#").strip().strip("*").strip()
        if text and not text.startswith("---"):
            return _clip(text)
    return ""


def _clip(text: str) -> str:
    text = _one_line(text)
    if len(text) <= DESCRIPTION_MAX_CHARS:
        return text
    return text[: DESCRIPTION_MAX_CHARS - 1].rstrip() + "…"


def now_iso() -> str:
    return datetime.now(tz=timezone.utc).replace(microsecond=0).isoformat()


# -- migration --------------------------------------------------------------------


def _index_hooks(index_text: str) -> dict[str, str]:
    """`file.md` → the hook after the dash on its index line, when it has one."""
    hooks: dict[str, str] = {}
    for line in (index_text or "").splitlines():
        match = re.match(r"\s*-\s*\[([^\]]*)\]\(([^)\s]+)\)\s*(?:[—–-]\s*(.*))?$", line)
        if match:
            hooks[match.group(2).rsplit("/", 1)[-1]] = (match.group(3) or match.group(1)).strip()
    return hooks


#: A frontmatter block stranded MID-body: the old append mode concatenated a whole file,
#: header and all, onto an existing one (4 of 488 real files). Only blocks whose every
#: line is a known frontmatter key qualify, so a markdown rule (`---`) is never touched.
_INNER_BLOCK_RE = re.compile(
    r"^---[ \t]*\r?\n(?:(?:type|modified|name|description)[ \t]*:[^\n]*\r?\n)+---[ \t]*(?:\r?\n|\Z)",
    re.M,
)


def _strip_inner_blocks(body: str) -> str:
    return _INNER_BLOCK_RE.sub("\n", body)


def _wiki_links(body: str, known: set[str]) -> str:
    """The earlier `[Title](file.md)` relation links, as `[[name]]` when the target is a
    memory file of this directory; any other link is left as written."""

    def swap(match: re.Match) -> str:
        target = match.group(1).rsplit("/", 1)[-1]
        return f"[[{Path(target).stem}]]" if target in known else match.group(0)

    return _RELATION_LINK_RE.sub(swap, body)


def upgrade_dir(root: Path, *, reserved: tuple[str, ...] = ()) -> dict[str, int]:
    """Bring every memory file under `root` to FORMAT_VERSION, once.

    Idempotent behind `root/.format`. The whole directory's markdown is copied to
    `.history/pre-<version>/` first, so the migration can be undone by hand. Files are
    never renamed (index lines and links point at their names); `name` is the stem.
    `reserved` names (the index, the progress log) have no frontmatter and are skipped.
    """
    root = Path(root)
    marker = root / FORMAT_MARKER
    if not root.is_dir():
        return {"upgraded": 0}
    try:
        if marker.read_text(encoding="utf-8").strip() == FORMAT_VERSION:
            return {"upgraded": 0}
    except OSError:
        pass
    files = [p for p in sorted(root.glob("*.md")) if p.is_file()]
    backup = root / ".history" / f"pre-{FORMAT_VERSION}"
    backup.mkdir(parents=True, exist_ok=True)
    for path in files:
        target = backup / path.name
        if not target.exists():
            shutil.copy2(path, target)
    index_name = reserved[0] if reserved else "MEMORY.md"
    try:
        hooks = _index_hooks((root / index_name).read_text(encoding="utf-8", errors="replace"))
    except OSError:
        hooks = {}
    known = {p.name for p in files}
    upgraded = 0
    for path in files:
        if path.name in reserved:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        meta, body = split(text)
        body = _wiki_links(_strip_inner_blocks(body).lstrip("\n"), known)
        meta.setdefault("name", path.stem)
        if not meta.get("description"):
            meta["description"] = _clip(hooks.get(path.name, "")) or describe(body)
        rendered = render(meta) + body
        if rendered != text:
            path.write_text(rendered, encoding="utf-8", newline="\n")
            upgraded += 1
    marker.write_text(FORMAT_VERSION + "\n", encoding="utf-8")
    return {"upgraded": upgraded}


#: The state file of the removed dream pass, and what it wrote beside it.
DREAM_STATE = ".dream.json"
DREAM_LEFT = ("PROPOSALS.md",)
RETIRED_DIR = "retired"


def retire_removed(root: Path) -> list[str]:
    """Move what a removed background pass left in a memory directory to
    `.history/retired/`, and return the names moved.

    The dream pass was removed, which left its files where they were. A model that lists
    its memory directory reads them as a mechanism that still runs: in session 14715fae-61f
    the agent told its owner that a background pass "scans the transcript and
    proposes lessons", from `.dream.json` and `PROPOSALS.md` on disk. Five project
    directories in the store held both. Only a directory holding the pass's own state file
    is touched: a `PROPOSALS.md` with no `.dream.json` beside it is someone else's. Moved,
    never deleted; idempotent, since nothing is left to move the second time."""
    root = Path(root)
    if not (root / DREAM_STATE).is_file():
        return []
    aside = root / ".history" / RETIRED_DIR
    moved = []
    for name in (DREAM_STATE, *DREAM_LEFT):
        source = root / name
        if not source.is_file():
            continue
        try:
            aside.mkdir(parents=True, exist_ok=True)
            target, n = aside / name, 2
            while target.exists():
                target, n = aside / f"{Path(name).stem}-{n}{Path(name).suffix}", n + 1
            source.replace(target)
            moved.append(name)
        except OSError:
            continue  # left where it is; the next start tries again
    return moved


def type_of(meta: dict[str, str]) -> str:
    return (meta.get("type") or "").strip().lower()


def unique_name(root: Path, stem: str) -> str:
    """`stem.md`, or `stem-2.md`, `stem-3.md`… when taken."""
    candidate = f"{stem}.md"
    n = 2
    while (Path(root) / candidate).exists():
        candidate = f"{stem}-{n}.md"
        n += 1
    return candidate


def first_value(meta: dict[str, str], *keys: str) -> Optional[str]:
    for key in keys:
        if meta.get(key):
            return meta[key]
    return None
