"""Installing a pinned tool.

Nothing here downloads on its own. `install` runs only after the user has approved it through
`request_tool`, because fetching an executable is a supply-chain decision: it is pinned by version,
checked against a SHA-256 and never implicit.
"""

from __future__ import annotations

import hashlib
import io
import shutil
import stat
import tarfile
import tempfile
import urllib.request
import zipfile
from pathlib import Path

from . import catalog
from . import paths
from .paths import bin_dir, managed_path
from .locate import is_runnable


def _verify(blob: bytes, expected: str) -> None:
    actual = hashlib.sha256(blob).hexdigest()
    if actual != expected:
        raise ValueError(f"checksum mismatch: expected {expected}, got {actual} — refusing to install")


def _member_bytes(blob: bytes, member: str, tool: str) -> bytes:
    """One file out of a release archive, read according to the archive's own magic bytes.

    The URL's extension is not trusted here. By this point the bytes are pinned by digest, so what
    they are outranks what the file name says. There is a zip branch because tectonic ships Windows
    as `.zip` where every other platform is `.tar.gz`. It was added for tectonic, the first managed
    tool with a Windows build. Before it `platform_key()` returned `windows_amd64` and no entry
    carried that key, so `install()` on Windows could only raise."""
    if blob[:4] == b"PK\x03\x04":
        with zipfile.ZipFile(io.BytesIO(blob)) as archive:
            try:
                return archive.read(member)
            except KeyError:
                raise ValueError(f"{member} missing from {tool} archive") from None
    with tarfile.open(fileobj=io.BytesIO(blob)) as archive:
        try:
            extracted = archive.extractfile(member)
        except KeyError:
            # `extractfile` raises for a name that is absent and returns None only for a member
            # that exists but is not a regular file, such as a directory or a link. Checking for
            # None alone let a pin that went stale between releases surface as a bare KeyError
            # and not as the message that says which member moved.
            raise ValueError(f"{member} missing from {tool} archive") from None
        if extracted is None:
            raise ValueError(f"{member} in {tool} archive is not a regular file")
        return extracted.read()


def install(name: str, *, timeout: int = 120) -> str:
    """Install a pinned tool and return its absolute path.

    The download is checked against the pinned digest before anything reaches its final location,
    so a tampered or truncated artifact never becomes an executable on disk."""
    tool = catalog.MANAGED.get(name)
    if not tool:
        raise KeyError(f"{name} is not a managed tool")
    download = tool.downloads.get(paths.platform_key())
    if not download:
        raise KeyError(f"{name} has no pinned build for {paths.platform_key()}")

    target = managed_path(tool)
    if is_runnable(target):
        return str(target)

    with urllib.request.urlopen(download.url, timeout=timeout) as response:  # noqa: S310 - pinned URL
        blob = response.read()
    _verify(blob, download.sha256)

    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as scratch:
        payload = _member_bytes(blob, download.member, name) if download.member else blob
        staged = Path(scratch) / "binary"
        staged.write_bytes(payload)
        staged.chmod(staged.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        shutil.move(str(staged), str(target))

    _link_into_bin(target)
    return str(target)


def _link_into_bin(target: Path) -> None:
    """Expose the versioned binary under the stable bin folder, by a name that suits PATH."""
    link = bin_dir() / target.name
    link.parent.mkdir(parents=True, exist_ok=True)
    try:
        if link.is_symlink() or link.exists():
            link.unlink()
        link.symlink_to(target)
    except OSError:
        # A filesystem without symlinks, as on some Windows setups: a copy does the same job.
        shutil.copy2(target, link)
