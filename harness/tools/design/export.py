"""A design page taken out of Reeve: as one HTML file, as a zip, and where it is saved.

A design is an HTML file with the local files it uses beside it (a stylesheet, a script, pictures,
fonts). To hand it to someone it is either one file with each of those written into it (a style
inlined, a picture as a `data:` address), or a zip of the page with them. What counts as a file it
uses is what the page names in the places a browser loads from: a stylesheet or icon link, a
script's source, a picture's source or source set, a video's poster, an SVG image, and every `url()`
and `@import` in its styles and stylesheets. A link to another page is not one: it stays a link.

Only the kinds of file a page loads are taken (styles, scripts, pictures, fonts, media, JSON), only
under the page's root however a path is spelled, never a hidden one (a dot file or one in a dot
folder), and none larger than `MAX_FILE` or past `MAX_TOTAL` altogether: a page that names
`../../.env` or `../notes.txt` gets nothing for it. Addresses on the web stay as they are.

What is saved goes to the person's Downloads folder, as a browser saves a download, under a name
that does not overwrite one already there.
"""

from __future__ import annotations

import base64
import io
import mimetypes
import os
import re
import sys
import zipfile
from pathlib import Path
from typing import Iterator, Optional
from urllib.parse import unquote

from .tokens import find_design_md

MAX_FILE = 10 * 1024 * 1024
MAX_TOTAL = 60 * 1024 * 1024
#: The kinds of file a page loads, by suffix: the only ones an export takes.
WEB_SUFFIXES = frozenset(
    ".css .js .mjs .json .png .jpg .jpeg .gif .webp .avif .svg .ico .bmp .woff .woff2 .ttf .otf .eot "
    ".mp4 .webm .ogg .mp3 .wav .m4a .vtt .html .htm".split()
)

_TAG = re.compile(r"<(?P<name>[a-zA-Z][\w:-]*)(?P<attrs>(?:[^>\"']|\"[^\"]*\"|'[^']*')*)>", re.S)
_ATTR = re.compile(r"(?P<lead>\s)(?P<key>[\w:-]+)(?P<eq>\s*=\s*)(?P<q>[\"'])(?P<value>.*?)(?P=q)", re.S)
_CSS_URL = re.compile(r"url\(\s*(?P<q>[\"']?)(?P<value>[^\"')]+)(?P=q)\s*\)", re.I)
_CSS_IMPORT = re.compile(r"@import\s+(?:url\(\s*)?(?P<q>[\"'])(?P<value>[^\"']+)(?P=q)\s*\)?\s*(?P<media>[^;]*);", re.I)
_STYLE_BLOCK = re.compile(r"(<style\b[^>]*>)(.*?)(</style\s*>)", re.I | re.S)
_SCRIPT_SRC = re.compile(r"<script\b(?P<attrs>[^>]*?)\bsrc\s*=\s*(?P<q>[\"'])(?P<value>[^\"']+)(?P=q)(?P<rest>[^>]*)>\s*</script\s*>", re.I | re.S)
_LINK = re.compile(r"<link\b[^>]*>", re.I | re.S)

#: The attributes that load a file, by tag (`*` for any tag).
_LOADS = {
    "img": ("src", "srcset"),
    "source": ("src", "srcset"),
    "video": ("src", "poster"),
    "audio": ("src",),
    "track": ("src",),
    "input": ("src",),
    "image": ("href", "xlink:href"),
    "use": ("href", "xlink:href"),
    "script": ("src",),
    "iframe": ("src",),
    "embed": ("src",),
    "object": ("data",),
}
_LINK_RELS = ("stylesheet", "icon", "apple-touch-icon", "preload", "manifest", "mask-icon")


def _local(value: str) -> Optional[str]:
    """The path part of a reference that names a local file, or None for a web address, a
    `data:` address, an anchor, or an empty one."""
    value = value.strip()
    if not value or value.startswith(("#", "//")) or re.match(r"^[a-zA-Z][\w+.-]*:", value):
        return None
    value = value.split("#", 1)[0].split("?", 1)[0]
    return unquote(value) or None


def _resolve(base: Path, reference: str, root: Path) -> Optional[Path]:
    """`reference` from a file in `base`, when it is a file under `root`."""
    local = _local(reference)
    if local is None:
        return None
    try:
        target = (base / local).resolve()
        if not target.is_relative_to(root) or not target.is_file():
            return None
        inside = target.relative_to(root).parts
    except (OSError, ValueError):
        return None
    if target.suffix.lower() not in WEB_SUFFIXES or any(part.startswith(".") for part in inside):
        return None
    return target


def _srcset(value: str) -> list[str]:
    return [part.strip().split()[0] for part in value.split(",") if part.strip()]


def _page_references(html: str) -> Iterator[str]:
    """Every reference in the page that loads a file, as written."""
    for tag in _TAG.finditer(html):
        name = tag.group("name").lower()
        attrs = {m.group("key").lower(): m.group("value") for m in _ATTR.finditer(" " + tag.group("attrs"))}
        if name == "link":
            rels = (attrs.get("rel") or "").lower().split()
            if any(rel in _LINK_RELS for rel in rels) and attrs.get("href"):
                yield attrs["href"]
        for key in _LOADS.get(name, ()):
            if key in attrs:
                yield from (_srcset(attrs[key]) if key == "srcset" else [attrs[key]])
        if "style" in attrs:
            yield from (m.group("value") for m in _CSS_URL.finditer(attrs["style"]))
    for block in _STYLE_BLOCK.finditer(html):
        yield from _css_references(block.group(2))


def _css_references(css: str) -> Iterator[str]:
    yield from (m.group("value") for m in _CSS_IMPORT.finditer(css))
    yield from (m.group("value") for m in _CSS_URL.finditer(css))


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def local_files(page: Path, root: Path) -> list[Path]:
    """The files under `root` that `page` uses, its stylesheets' files included, page first."""
    root = root.resolve()
    page = page.resolve()
    found: list[Path] = [page]
    seen = {page}
    total = 0
    queue: list[tuple[Path, Iterator[str]]] = [(page, _page_references(_read(page)))]
    while queue:
        holder, references = queue.pop(0)
        for reference in references:
            target = _resolve(holder.parent, reference, root)
            if target is None or target in seen:
                continue
            size = target.stat().st_size
            if size > MAX_FILE or total + size > MAX_TOTAL:
                continue
            seen.add(target)
            found.append(target)
            total += size
            if target.suffix.lower() == ".css":
                queue.append((target, _css_references(_read(target))))
    return found


def _data_url(path: Path) -> str:
    kind = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    if path.suffix.lower() == ".svg":
        kind = "image/svg+xml"
    return f"data:{kind};base64," + base64.b64encode(path.read_bytes()).decode("ascii")


class _Budget:
    def __init__(self) -> None:
        self.left = MAX_TOTAL

    def take(self, path: Path) -> bool:
        size = path.stat().st_size
        if size > MAX_FILE or size > self.left:
            return False
        self.left -= size
        return True


def _inline_css(css: str, base: Path, root: Path, budget: _Budget, depth: int = 0) -> str:
    def imported(match: re.Match) -> str:
        target = _resolve(base, match.group("value"), root)
        if target is None or depth > 4 or not budget.take(target):
            return match.group(0)
        body = _inline_css(_read(target), target.parent, root, budget, depth + 1)
        media = match.group("media").strip()
        return f"@media {media} {{\n{body}\n}}" if media else body

    def url(match: re.Match) -> str:
        target = _resolve(base, match.group("value"), root)
        if target is None or not budget.take(target):
            return match.group(0)
        return f'url("{_data_url(target)}")'

    return _CSS_URL.sub(url, _CSS_IMPORT.sub(imported, css))


def standalone_html(page: Path, root: Path) -> str:
    """`page` as one file: its local stylesheets and scripts written in, every local file it loads
    as a `data:` address. A link to another page and anything on the web stay as written."""
    root = root.resolve()
    page = page.resolve()
    base = page.parent
    budget = _Budget()
    html = _read(page)

    def link(match: re.Match) -> str:
        tag = match.group(0)
        attrs = {m.group("key").lower(): m.group("value") for m in _ATTR.finditer(" " + tag[5:])}
        rels = (attrs.get("rel") or "").lower().split()
        target = _resolve(base, attrs.get("href", ""), root)
        if target is None or not budget.take(target):
            return tag
        if "stylesheet" in rels:
            media = attrs.get("media")
            open_tag = f'<style media="{media}">' if media else "<style>"
            return open_tag + _inline_css(_read(target), target.parent, root, budget) + "</style>"
        if any(rel in _LINK_RELS for rel in rels):
            return tag.replace(attrs["href"], _data_url(target), 1)
        return tag

    def script(match: re.Match) -> str:
        target = _resolve(base, match.group("value"), root)
        if target is None or not budget.take(target):
            return match.group(0)
        code = re.sub(r"</script", r"<\\/script", _read(target), flags=re.I)
        return f"<script{match.group('attrs')}{match.group('rest')}>{code}</script>"

    def style_block(match: re.Match) -> str:
        return match.group(1) + _inline_css(match.group(2), base, root, budget) + match.group(3)

    def tag(match: re.Match) -> str:
        name = match.group("name").lower()
        keys = _LOADS.get(name, ())
        if name == "script":
            return match.group(0)

        def attr(found: re.Match) -> str:
            key = found.group("key").lower()
            value = found.group("value")
            if key == "style":
                value = _inline_css(value, base, root, budget)
            elif key == "srcset" and key in keys:
                parts = []
                for part in value.split(","):
                    bits = part.strip().split(None, 1)
                    if not bits:
                        continue
                    target = _resolve(base, bits[0], root)
                    if target is not None and budget.take(target):
                        bits[0] = _data_url(target)
                    parts.append(" ".join(bits))
                value = ", ".join(parts)
            elif key in keys:
                target = _resolve(base, value, root)
                if target is not None and budget.take(target):
                    value = _data_url(target)
            else:
                return found.group(0)
            return f"{found.group('lead')}{found.group('key')}{found.group('eq')}{found.group('q')}{value}{found.group('q')}"

        return f"<{match.group('name')}{_ATTR.sub(attr, match.group('attrs'))}>"

    html = _LINK.sub(link, html)
    html = _SCRIPT_SRC.sub(script, html)
    html = _STYLE_BLOCK.sub(style_block, html)
    return _TAG.sub(tag, html)


def zip_bytes(page: Path, root: Path, roots: Optional[list[Path]] = None) -> bytes:
    """`page`, the files it uses and the DESIGN.md it follows, in one zip, with their paths as
    they are beside each other (so the page opens from the unzipped folder)."""
    files = local_files(page, root)
    design_md = find_design_md(page, roots or [root])
    if design_md is not None and design_md.resolve() not in files:
        files.append(design_md.resolve())
    common = Path(os.path.commonpath([str(path.parent) for path in files]))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            archive.write(path, path.relative_to(common).as_posix())
    return out.getvalue()


def downloads_dir() -> Path:
    """The person's Downloads folder (where Windows has moved it, if it has), else their home."""
    if sys.platform == "win32":
        try:
            import winreg

            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders"
            ) as key:
                value, _ = winreg.QueryValueEx(key, "{374DE290-123F-4565-9164-39C4925E467B}")
            folder = Path(os.path.expandvars(str(value)))
            if folder.is_dir():
                return folder
        except OSError:
            pass
    folder = Path.home() / "Downloads"
    return folder if folder.is_dir() else Path.home()


def save_download(name: str, data: bytes, folder: Optional[Path] = None) -> Path:
    """Write `data` as `name` in `folder` (Downloads), as `name (2)` and on when it is taken."""
    folder = folder or downloads_dir()
    clean = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .") or "design"
    stem, dot, ext = clean.rpartition(".")
    if not dot:
        stem, ext = clean, ""
    target = folder / clean
    n = 2
    while target.exists():
        target = folder / (f"{stem} ({n}).{ext}" if ext else f"{stem} ({n})")
        n += 1
    target.write_bytes(data)
    return target
