"""Finding command-line tools, and installing pinned builds of them (`tools/toolchain`).

An app started from the Finder gets launchd's bare PATH, so every scanner brew or nvm installed is
out of sight and a security review loses checks without a word. A tool is looked for where such
installs put it; one that is installed for the person is a pinned version with a pinned digest, and
nothing that fails the digest is written.
"""

from __future__ import annotations

import hashlib
import io
import os
import stat
import tarfile
import zipfile
from pathlib import Path

import pytest

from harness.tools import toolchain
from harness.tools.toolchain.install import _member_bytes


def _serves(monkeypatch, payload: bytes):
    """Every download answers with `payload`."""

    class Download:
        def read(self):
            return payload

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: Download())


def _no_known_dirs(monkeypatch, path=""):
    monkeypatch.setenv("PATH", path)
    monkeypatch.setattr(toolchain.locate, "KNOWN_DIRS", ())


def _make_exe(path, body: str = "#!/bin/sh\necho hi\n"):
    """An executable at `path`. Windows finds executables by extension (PATHEXT) and `shutil.which`
    cannot see one without it, so there it gets `.exe`; callers compare against what is returned."""
    exe = path.with_name(path.name + ".exe") if os.name == "nt" else path
    exe.parent.mkdir(parents=True, exist_ok=True)
    exe.write_text(body)
    exe.chmod(exe.stat().st_mode | stat.S_IXUSR)
    return exe


def test_resolve_prefers_path(tmp_path, monkeypatch):
    found = _make_exe(tmp_path / "bin" / "semgrep")
    monkeypatch.setenv("PATH", str(tmp_path / "bin"))
    assert toolchain.resolve("semgrep") == str(found.resolve())


def test_resolve_finds_tools_launchd_path_cannot_see(tmp_path, monkeypatch):
    """The failure as it happened: a bare PATH, and the tool in a brew folder."""
    brew = tmp_path / "opt" / "homebrew" / "bin"
    gitleaks = _make_exe(brew / "gitleaks")
    monkeypatch.setenv("PATH", "/usr/bin:/bin")  # what a launch from the Finder is given
    monkeypatch.setattr(toolchain.locate, "KNOWN_DIRS", (str(brew),))
    assert toolchain.resolve("gitleaks") == str(gitleaks.resolve())


def test_resolve_returns_absolute_path(tmp_path, monkeypatch):
    # so a caller can run it without relying on PATH at all
    _make_exe(tmp_path / "bin" / "trivy")
    monkeypatch.setenv("PATH", str(tmp_path / "bin"))
    assert os.path.isabs(toolchain.resolve("trivy") or "")


def test_missing_reports_only_absent_tools(tmp_path, monkeypatch):
    _make_exe(tmp_path / "bin" / "gitleaks")
    _no_known_dirs(monkeypatch, str(tmp_path / "bin"))
    monkeypatch.setattr(toolchain.catalog, "MANAGED", {})
    assert toolchain.missing(["gitleaks", "semgrep"]) == ["semgrep"]


def test_unknown_tool_resolves_to_none(monkeypatch):
    _no_known_dirs(monkeypatch)
    assert toolchain.resolve("definitely-not-a-real-tool") is None


def test_registry_entries_are_pinned_and_digested():
    """Every managed download has a version and a whole SHA-256: an unpinned one would mean
    "download whatever is current", which is what is refused."""
    assert toolchain.MANAGED
    for name, tool in toolchain.MANAGED.items():
        assert tool.version and tool.version[0].isdigit(), name
        assert tool.summary, f"{name} needs a summary for the card"
        for platform, download in tool.downloads.items():
            where = f"{name}/{platform}"
            assert len(download.sha256) == 64 and int(download.sha256, 16) >= 0, where
            assert tool.version in download.url and download.url.startswith("https://"), where


def test_trivy_is_pinned_for_every_platform_we_ship():
    """tfsec is retired upstream into trivy; `trivy config` is the IaC scanner the cloud-posture
    bundle runs, so it is pinned wherever the app runs."""
    trivy = toolchain.MANAGED["trivy"]
    assert set(trivy.downloads) >= {"darwin_arm64", "darwin_amd64", "linux_amd64"}
    assert all(download.member == "trivy" for download in trivy.downloads.values())  # the releases are tarballs
    assert "tfsec" not in toolchain.MANAGED


def test_tectonic_is_pinned_for_every_platform_we_ship():
    """The LaTeX engine (2026-09-11). Windows is in the set on purpose: it is the first
    managed tool with a Windows build, and before it `_platform_key()` answered
    `windows_amd64` while no entry carried that key — so `install()` there only ever
    raised, on the platform the owner actually runs."""
    tool = toolchain.MANAGED["tectonic"]
    assert set(tool.downloads) >= {
        "windows_amd64",
        "darwin_arm64",
        "darwin_amd64",
        "linux_amd64",
    }
    # Verified against the archives themselves, not assumed: each one holds a single
    # binary at its root, and only Windows carries the .exe suffix.
    assert tool.downloads["windows_amd64"].member == "tectonic.exe"
    for key, dl in tool.downloads.items():
        if key != "windows_amd64":
            assert dl.member == "tectonic", key


def _zip_of(name: str, payload: bytes) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(name, payload)
    return buf.getvalue()


def _targz_of(name: str, payload: bytes) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        info = tarfile.TarInfo(name)
        info.size = len(payload)
        tf.addfile(info, io.BytesIO(payload))
    return buf.getvalue()


def test_a_release_archive_is_read_by_its_own_magic_not_its_filename():
    """tectonic ships Windows as .zip and everything else as .tar.gz, so the extractor has
    to handle both. It dispatches on the bytes rather than the URL suffix because by then
    the bytes are pinned by digest — what they ARE beats what the name claims."""
    payload = b"ELF-ish binary"
    assert _member_bytes(_zip_of("tectonic.exe", payload), "tectonic.exe", "t") == payload
    assert _member_bytes(_targz_of("tectonic", payload), "tectonic", "t") == payload


def test_a_member_that_is_not_in_the_archive_is_refused():
    """Same error either way — a pinned member that moved between releases must fail
    loudly at install time, not write a truncated file and call it a binary."""
    for blob in (_zip_of("other", b"x"), _targz_of("other", b"x")):
        with pytest.raises(ValueError, match="missing from"):
            _member_bytes(blob, "tectonic", "tectonic")


def test_windows_installs_from_a_zip_asset(tmp_path, monkeypatch):
    """The path that never existed before tectonic: a managed install on Windows, from a
    zip. Exercised end to end because the archive branch and the platform key had both
    only ever been used apart."""
    payload = b"MZ fake binary"
    blob = _zip_of("tectonic.exe", payload)

    monkeypatch.setattr(toolchain.paths, "managed_dir", lambda: tmp_path / "tools")
    monkeypatch.setattr(toolchain.paths, "platform_key", lambda: "windows_amd64")
    monkeypatch.setattr(
        toolchain.catalog,
        "MANAGED",
        {
            "tectonic": toolchain.ManagedTool(
                name="tectonic",
                version="0.17.0",
                summary="compiles LaTeX",
                downloads={
                    "windows_amd64": toolchain.Download(
                        url="https://example.invalid/tectonic.zip",
                        sha256=hashlib.sha256(blob).hexdigest(),
                        member="tectonic.exe",
                    )
                },
            )
        },
    )

    _serves(monkeypatch, blob)

    path = toolchain.install("tectonic")
    assert os.path.isfile(path)
    # The BINARY lands, not the archive around it.
    with open(path, "rb") as fh:
        assert fh.read() == payload


def test_describe_surfaces_what_the_user_is_approving(monkeypatch):
    monkeypatch.setattr(toolchain.paths, "platform_key", lambda: "darwin_arm64")
    card = toolchain.describe("gitleaks")
    assert card and card["version"] and card["sha256"] and card["url"]
    assert "secret" in card["summary"].lower()
    assert card["source"] == "github.com/gitleaks"  # who publishes it, readably


def test_install_refuses_a_tampered_download(tmp_path, monkeypatch):
    """What the pin is for: a download that does not match never reaches the disk."""
    monkeypatch.setattr(toolchain.paths, "managed_dir", lambda: tmp_path / "tools")
    monkeypatch.setattr(toolchain.paths, "platform_key", lambda: "darwin_arm64")
    _serves(monkeypatch, b"malicious payload")
    with pytest.raises(ValueError, match="checksum mismatch"):
        toolchain.install("gitleaks")
    assert not (tmp_path / "tools").exists() or not list((tmp_path / "tools").rglob("gitleaks"))


def test_install_writes_a_verified_binary(tmp_path, monkeypatch):
    payload = b"#!/bin/sh\necho scanned\n"
    monkeypatch.setattr(toolchain.paths, "managed_dir", lambda: tmp_path / "tools")
    monkeypatch.setattr(toolchain.paths, "platform_key", lambda: "darwin_arm64")
    pinned = toolchain.Download(url="https://example.invalid/osv-scanner", sha256=hashlib.sha256(payload).hexdigest())
    monkeypatch.setattr(toolchain.catalog, "MANAGED", {"osv-scanner": toolchain.ManagedTool(name="osv-scanner", version="2.5.0", summary="checks lockfiles", downloads={"darwin_arm64": pinned})})
    _serves(monkeypatch, payload)
    path = toolchain.install("osv-scanner")
    assert os.access(path, os.X_OK) and Path(path).read_bytes() == payload
    _no_known_dirs(monkeypatch)
    assert toolchain.resolve("osv-scanner") == path  # found afterwards with no PATH at all
    # linked in the stable bin folder too, so a shell with that folder on PATH runs it by name the
    # moment it lands; the link has the target's name, `.exe` on Windows
    linked = toolchain.bin_dir() / Path(path).name
    assert linked.exists() and linked.read_bytes() == payload
