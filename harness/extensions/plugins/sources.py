"""Fetching a plugin (or a marketplace) into a staging folder (design §7.2, §9.8).

Every source lands as plain files under a folder this module creates: no symlink survives
(one inside the tree is copied as a file, one pointing out is refused), and the hard
limits below refuse an oversized tree rather than cutting it. `git` and `npm` run through
`harness.platform.spawn` with list argv — never a shell — and a timeout. An `archive` is
downloaded by the caller's `download(url) -> bytes`, which on the server is the
SSRF-guarded fetch (`web.guard.get_checked`); this package does not reach the network
itself. The `command` source (run the marketplace's own program on this machine) is
refused.
"""

from __future__ import annotations

import hashlib
import io
import os
import shutil
import stat
import tarfile
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Optional

MAX_BYTES = 200 * 1024 * 1024
MAX_FILES = 20_000
MAX_DEPTH = 32
INSTALL_TIMEOUT_SECONDS = 300.0

Downloader = Callable[[str], bytes]


class SourceError(ValueError):
    """A source that cannot be fetched, said in words a person can act on."""


@dataclass(frozen=True)
class Fetched:
    """A fetched tree and what identifies it (for the version and the consent screen)."""

    path: Path
    kind: str
    sha: str = ""  # a git commit, or an archive's sha256
    detail: str = ""  # the URL / repo / package, verbatim, for the consent screen


# -- the one safe copy ----------------------------------------------------------------------------


def copy_tree(src: Path, dest: Path) -> None:
    """Copy `src` into `dest` under the limits, symlinks resolved inside `src` only."""
    src = Path(src)
    base = src.resolve()
    files = 0
    total = 0
    for current, dirs, names in os.walk(src, followlinks=False):
        rel_dir = Path(current).relative_to(src)
        if len(rel_dir.parts) > MAX_DEPTH:
            raise SourceError(f"folders nest deeper than {MAX_DEPTH} levels")
        dirs[:] = [d for d in dirs if d != ".git"]
        for name in [*dirs, *names]:
            path = Path(current) / name
            if path.is_symlink():
                target = path.resolve()
                if target != base and base not in target.parents:
                    raise SourceError(f"{path.relative_to(src).as_posix()} points outside the plugin")
        for name in names:
            path = Path(current) / name
            real = path.resolve() if path.is_symlink() else path
            try:
                info = real.stat()
            except OSError as exc:
                raise SourceError(f"{path.relative_to(src).as_posix()}: {exc}") from exc
            if not stat.S_ISREG(info.st_mode):
                raise SourceError(f"{path.relative_to(src).as_posix()} is not a regular file")
            files += 1
            total += info.st_size
            if files > MAX_FILES:
                raise SourceError(f"more than {MAX_FILES} files")
            if total > MAX_BYTES:
                raise SourceError(f"larger than {MAX_BYTES // (1024 * 1024)} MB")
            target = Path(dest) / rel_dir / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(real, target)
        for name in dirs:
            (Path(dest) / rel_dir / name).mkdir(parents=True, exist_ok=True)


def computed_hash(root: Path) -> str:
    """`sha256:` over every file's relative path and bytes, sorted — the skill installer's
    algorithm (`skills-lock.json`), so a hand-edited install is noticed (design §6.2)."""
    digest = hashlib.sha256()
    files = sorted(
        (p for p in Path(root).rglob("*") if p.is_file() and ".git" not in p.relative_to(root).parts),
        key=lambda p: p.relative_to(root).as_posix(),
    )
    for path in files:
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return "sha256:" + digest.hexdigest()


# -- sources ---------------------------------------------------------------------------------------


def fetch(
    source: Any,
    dest: Path,
    *,
    marketplace_root: Optional[Path] = None,
    download: Optional[Downloader] = None,
) -> Fetched:
    """Fetch `source` into the empty folder `dest` (which this creates)."""
    dest = Path(dest)
    if isinstance(source, str):
        if source.startswith("./") or source == ".":
            return _relative(source, dest, marketplace_root)
        path = Path(source).expanduser()
        if path.is_dir():
            dest.mkdir(parents=True, exist_ok=True)
            copy_tree(path, dest)
            return Fetched(dest, "directory", detail=str(path.resolve()))
        raise SourceError(f"{source!r} is neither a folder nor a ./relative path in a marketplace")
    if not isinstance(source, dict):
        raise SourceError("a plugin source is a path or an object with `source`")
    kind = str(source.get("source") or "")
    if kind == "github":
        repo = str(source.get("repo") or "")
        if not repo or repo.count("/") != 1:
            raise SourceError("a github source needs `repo`: owner/name")
        return _git(f"https://github.com/{repo}.git", dest, source, detail=repo)
    if kind in ("url", "git"):
        url = str(source.get("url") or "")
        if not url:
            raise SourceError("a url source needs `url`")
        return _git(url, dest, source, detail=url)
    if kind == "git-subdir":
        return _git_subdir(source, dest)
    if kind == "archive":
        return _archive(source, dest, download)
    if kind == "npm":
        return _npm(source, dest)
    if kind == "command":
        raise SourceError(
            "a `command` source runs the marketplace's own program on this machine; Reeve "
            "does not install plugins that way"
        )
    raise SourceError(f"unknown source type {kind!r}")


def _relative(source: str, dest: Path, marketplace_root: Optional[Path]) -> Fetched:
    if marketplace_root is None:
        raise SourceError(f"{source} is relative, and there is no marketplace to resolve it in")
    if ".." in PurePosixPath(source).parts:
        raise SourceError(f"{source} leaves the marketplace")
    path = (Path(marketplace_root) / source).resolve()
    if not path.is_dir() or (path != Path(marketplace_root).resolve() and Path(marketplace_root).resolve() not in path.parents):
        raise SourceError(f"{source} is not a folder in the marketplace")
    dest.mkdir(parents=True, exist_ok=True)
    copy_tree(path, dest)
    return Fetched(dest, "relative", detail=source)


def _run(argv: list[str], cwd: Path) -> str:
    from harness.platform.spawn import run

    try:
        result = run(argv, cwd=str(cwd), capture_output=True, encoding="utf-8", errors="replace", timeout=INSTALL_TIMEOUT_SECONDS)
    except FileNotFoundError as exc:
        raise SourceError(f"{argv[0]} is not installed on this machine") from exc
    except Exception as exc:  # a timeout, a spawn failure
        raise SourceError(f"{argv[0]} failed: {exc}") from exc
    if result.returncode != 0:
        tail = (result.stderr or result.stdout or "").strip().splitlines()[-1:] or [""]
        raise SourceError(f"{' '.join(argv[:2])} failed: {tail[0]}")
    return result.stdout or ""


def _git(url: str, dest: Path, source: dict[str, Any], *, detail: str) -> Fetched:
    """A shallow clone at `ref`, pinned to `sha` when given; the tree is copied out so no
    `.git` directory (hooks, config) travels with the plugin."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    work = dest.parent / f".{dest.name}.git"
    shutil.rmtree(work, ignore_errors=True)
    ref = str(source.get("ref") or "")
    sha = str(source.get("sha") or "")
    argv = ["git", "clone", "--quiet", "--depth", "1"]
    if ref:
        argv += ["--branch", ref]
    _run([*argv, "--", url, str(work)], dest.parent)
    if sha:
        _run(["git", "-C", str(work), "fetch", "--quiet", "--depth", "1", "origin", sha], dest.parent)
        _run(["git", "-C", str(work), "checkout", "--quiet", sha], dest.parent)
    head = _run(["git", "-C", str(work), "rev-parse", "HEAD"], dest.parent).strip()
    subdir = str(source.get("path") or "")
    tree = work / subdir if subdir else work
    if subdir and (".." in PurePosixPath(subdir).parts or not tree.is_dir()):
        shutil.rmtree(work, ignore_errors=True)
        raise SourceError(f"{subdir} is not a folder in {detail}")
    try:
        dest.mkdir(parents=True, exist_ok=True)
        copy_tree(tree, dest)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return Fetched(dest, "git", sha=head, detail=detail + (f" ({subdir})" if subdir else ""))


def _git_subdir(source: dict[str, Any], dest: Path) -> Fetched:
    url = str(source.get("url") or "")
    if url and "://" not in url and url.count("/") == 1:
        url = f"https://github.com/{url}.git"
    if not url or not source.get("path"):
        raise SourceError("a git-subdir source needs `url` and `path`")
    fetched = _git(url, dest, source, detail=str(source.get("url")))
    return Fetched(fetched.path, "git-subdir", sha=fetched.sha, detail=fetched.detail)


def _archive(source: dict[str, Any], dest: Path, download: Optional[Downloader]) -> Fetched:
    url = str(source.get("url") or "")
    if not url.startswith("https://"):
        raise SourceError("an archive source needs an https:// `url`")
    if download is None:
        raise SourceError("downloading is not available here")
    data = download(url)
    if len(data) > MAX_BYTES:
        raise SourceError(f"the archive is larger than {MAX_BYTES // (1024 * 1024)} MB")
    sha = hashlib.sha256(data).hexdigest()
    expected = str(source.get("sha256") or "").lower().removeprefix("sha256:")
    if expected and expected != sha:
        raise SourceError("the archive's sha256 does not match the marketplace's")
    dest.mkdir(parents=True, exist_ok=True)
    extract_zip(data, dest)
    return Fetched(dest, "archive", sha=sha, detail=url)


def _entry_path(name: str) -> PurePosixPath:
    path = PurePosixPath(name.replace("\\", "/"))
    if path.is_absolute() or ".." in path.parts or (path.parts and ":" in path.parts[0]):
        raise SourceError(f"{name} leaves the plugin folder")
    if len(path.parts) > MAX_DEPTH:
        raise SourceError(f"folders nest deeper than {MAX_DEPTH} levels")
    return path


def extract_zip(data: bytes, dest: Path) -> None:
    """A zip, extracted with every guard: no absolute or `..` path (zip-slip), no symlink,
    no oversized total, no more than MAX_FILES entries."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise SourceError("the archive is not a zip file") from exc
    infos = archive.infolist()
    if len(infos) > MAX_FILES:
        raise SourceError(f"more than {MAX_FILES} files")
    if sum(i.file_size for i in infos) > MAX_BYTES:
        raise SourceError(f"larger than {MAX_BYTES // (1024 * 1024)} MB unpacked")
    for info in infos:
        path = _entry_path(info.filename)
        if stat.S_ISLNK(info.external_attr >> 16):
            raise SourceError(f"{info.filename} is a symlink")
        target = Path(dest) / Path(*path.parts) if path.parts else Path(dest)
        if info.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        with archive.open(info) as source, open(target, "wb") as out:
            shutil.copyfileobj(source, out)
    _single_top_folder(dest)


def _single_top_folder(dest: Path) -> None:
    """An archive of `plugin/…` unpacks to the plugin itself, not a folder holding it."""
    entries = [p for p in Path(dest).iterdir()]
    if len(entries) == 1 and entries[0].is_dir() and not (Path(dest) / ".claude-plugin").exists():
        inner = entries[0]
        for child in list(inner.iterdir()):
            shutil.move(str(child), str(Path(dest) / child.name))
        inner.rmdir()


def _npm(source: dict[str, Any], dest: Path) -> Fetched:
    """`npm pack`, then the tarball unpacked with the same guards — never `npm install`,
    so no lifecycle script of the package runs."""
    package = str(source.get("package") or "")
    if not package:
        raise SourceError("an npm source needs `package`")
    spec = f"{package}@{source['version']}" if source.get("version") else package
    work = dest.parent / f".{dest.name}.npm"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    try:
        out = _run(["npm", "pack", "--silent", "--ignore-scripts", spec], work).strip().splitlines()
        tarball = work / (out[-1] if out else "")
        if not tarball.is_file():
            raise SourceError(f"npm pack produced no tarball for {spec}")
        data = tarball.read_bytes()
        dest.mkdir(parents=True, exist_ok=True)
        extract_tar(data, dest)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return Fetched(dest, "npm", sha=hashlib.sha256(data).hexdigest(), detail=spec)


def extract_tar(data: bytes, dest: Path) -> None:
    try:
        archive = tarfile.open(fileobj=io.BytesIO(data), mode="r:*")
    except tarfile.TarError as exc:
        raise SourceError("the package is not a tarball") from exc
    members = archive.getmembers()
    if len(members) > MAX_FILES:
        raise SourceError(f"more than {MAX_FILES} files")
    if sum(m.size for m in members) > MAX_BYTES:
        raise SourceError(f"larger than {MAX_BYTES // (1024 * 1024)} MB unpacked")
    for member in members:
        path = _entry_path(member.name)
        if not (member.isfile() or member.isdir()):
            raise SourceError(f"{member.name} is not a regular file")
        target = Path(dest) / Path(*path.parts) if path.parts else Path(dest)
        if member.isdir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        handle = archive.extractfile(member)
        if handle is None:
            continue
        with handle, open(target, "wb") as out:
            shutil.copyfileobj(handle, out)
    _single_top_folder(dest)
