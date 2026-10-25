"""The command-line tools Reeve can install itself, each pinned by version and by SHA-256.

Only tools that a skill fundamentally is get listed here: the scanners behind the security bundles
and the LaTeX engine. Pinning keeps a security review reproducible, because it then does not depend on
whichever version the user's package manager shipped. The user's own toolchain (aws, kubectl,
terraform, gh, node) is never listed. Their installed, configured, credentialed copy is the point,
so it is only ever located.

Versions and digests come from the upstream release's own checksum manifest, and raising a version
means raising its digests in the same commit. Not every scanner belongs. semgrep is distributed as a
Python package, so the user's install is resolved and a copy is not half-managed. tfsec is absent on
purpose: it is deprecated upstream and `trivy config` replaces it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class Download:
    url: str
    sha256: str
    # Where the binary sits inside the archive, or None when the asset is the binary itself.
    member: Optional[str] = None


@dataclass(frozen=True)
class ManagedTool:
    name: str
    version: str
    downloads: dict[str, Download]  # platform key -> download
    summary: str


def _pins(base: str, member: Optional[str], assets: dict[str, tuple]) -> dict[str, Download]:
    """Downloads for each platform from `(file name, sha256)` pairs under one base URL. A third item
    on a pair overrides the archive member for that platform."""
    pins = {}
    for platform_key, (filename, digest, *own_member) in assets.items():
        pins[platform_key] = Download(base + filename, digest, own_member[0] if own_member else member)
    return pins


_GITLEAKS_BASE = "https://github.com/gitleaks/gitleaks/releases/download/v8.30.1/"
_TRIVY_BASE = "https://github.com/aquasecurity/trivy/releases/download/v0.74.0/"
_OSV_BASE = "https://github.com/google/osv-scanner/releases/download/v2.5.0/"
_TECTONIC_BASE = "https://github.com/tectonic-typesetting/tectonic/releases/download/tectonic%400.17.0/"

MANAGED: dict[str, ManagedTool] = {
    "gitleaks": ManagedTool(
        name="gitleaks",
        version="8.30.1",
        summary="scans git history and the working tree for committed secrets",
        downloads=_pins(
            _GITLEAKS_BASE,
            "gitleaks",
            {
                "darwin_arm64": (
                    "gitleaks_8.30.1_darwin_arm64.tar.gz",
                    "b40ab0ae55c505963e365f271a8d3846efbc170aa17f2607f13df610a9aeb6a5",
                ),
                "darwin_amd64": (
                    "gitleaks_8.30.1_darwin_x64.tar.gz",
                    "dfe101a4db2255fc85120ac7f3d25e4342c3c20cf749f2c20a18081af1952709",
                ),
                "linux_amd64": (
                    "gitleaks_8.30.1_linux_x64.tar.gz",
                    "551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb",
                ),
            },
        ),
    ),
    "trivy": ManagedTool(
        name="trivy",
        version="0.74.0",
        summary="scans IaC/config, container images, and filesystems for misconfigurations and vulnerabilities",
        downloads=_pins(
            _TRIVY_BASE,
            "trivy",
            {
                "darwin_arm64": (
                    "trivy_0.74.0_macOS-ARM64.tar.gz",
                    "1caada5e0e2091909357c7525d3aa76f4b660b13821bc143b190c7483e31cc11",
                ),
                "darwin_amd64": (
                    "trivy_0.74.0_macOS-64bit.tar.gz",
                    "472816f6888dda689d075c30254d4210b4d1035acf365aa72332f584c2f60485",
                ),
                "linux_amd64": (
                    "trivy_0.74.0_Linux-64bit.tar.gz",
                    "2ae6fe3ee734b7fdf11335663e18c75ea12dccc76062f09f164a3b0f8be4371a",
                ),
            },
        ),
    ),
    "osv-scanner": ManagedTool(
        name="osv-scanner",
        version="2.5.0",
        summary="checks dependency lockfiles against the OSV vulnerability database",
        downloads=_pins(
            _OSV_BASE,
            None,
            {
                "darwin_arm64": (
                    "osv-scanner_darwin_arm64",
                    "fff5a2e351b7f0a60001e87cbf862e82fb82e2792d368b533fec7a5865a73da2",
                ),
                "darwin_amd64": (
                    "osv-scanner_darwin_amd64",
                    "baef4f4a4ce2924a9241869c36d4bd9d6c04b632cae6637a0f6347ab9272eb16",
                ),
                "linux_amd64": (
                    "osv-scanner_linux_amd64",
                    "edcfc41d257db36148f065055655fe3fcfc434b0b423ea67468a84c207524e0c",
                ),
            },
        ),
    ),
    "tectonic": ManagedTool(
        name="tectonic",
        version="0.17.0",
        summary=(
            "compiles LaTeX to PDF — a self-contained TeX engine, so no TeX Live install. "
            "It fetches the packages a document actually uses from its own bundle on first "
            "compile, which means the first run of a new document needs the network"
        ),
        downloads=_pins(
            _TECTONIC_BASE,
            "tectonic",
            {
                "windows_amd64": (
                    "tectonic-0.17.0-x86_64-pc-windows-msvc.zip",
                    "f61ce51f0b0ade1015b7de7ef368541c5424e9756ecbd0d7af97d6d48030845f",
                    "tectonic.exe",
                ),
                "darwin_arm64": (
                    "tectonic-0.17.0-aarch64-apple-darwin.tar.gz",
                    "a3f1cac7c5678f01661a92212f58480ae3b0634115d880dbc59e2953ded45667",
                ),
                "darwin_amd64": (
                    "tectonic-0.17.0-x86_64-apple-darwin.tar.gz",
                    "7c90ef5b6ddb1eb1937e4337add5237b79338e4b9676459fa91187d24d6cdf80",
                ),
                "linux_amd64": (
                    "tectonic-0.17.0-x86_64-unknown-linux-gnu.tar.gz",
                    "1a715688baf591e650c8aeb160ae934e181685eecbb38b317de30b269ac5d606",
                ),
                "linux_arm64": (
                    "tectonic-0.17.0-aarch64-unknown-linux-musl.tar.gz",
                    "b10954a95404f3ab2328d2fa59a5ebab8e657f893fab096f98be8db7c0c979b8",
                ),
            },
        ),
    ),
}
