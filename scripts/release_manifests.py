"""The release job's last checks before a draft GitHub Release is made.

    python scripts/release_manifests.py <tag> <dist-folder>

1. The tag must be `v` + the app's version (surfaces/gui/package.json). A tag that differs would
   leave every installed app seeing an update that never goes away, so the job stops instead.
2. Each macOS runner wrote its own `latest-mac-<slug>.yml`; electron-updater reads one
   `latest-mac.yml` listing both architectures' files and picks its own. They are merged into it,
   and the parts are removed.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def app_version() -> str:
    return json.loads((ROOT / "surfaces" / "gui" / "package.json").read_text(encoding="utf-8"))["version"]


def check_tag(tag: str, version: str) -> None:
    if tag.removeprefix("v") != version:
        raise SystemExit(f"::error::tag {tag} != package.json version {version} — bump surfaces/gui/package.json before tagging")


def merge_mac_manifests(dist: Path) -> Path | None:
    """One `latest-mac.yml` from every `latest-mac-*.yml` in `dist`: the first part's fields, with
    every part's `files`. None when there were no parts (a release with no macOS build)."""
    import yaml

    parts = sorted(dist.glob("latest-mac-*.yml"))
    if not parts:
        return None
    merged = yaml.safe_load(parts[0].read_text(encoding="utf-8"))
    for part in parts[1:]:
        merged["files"].extend(yaml.safe_load(part.read_text(encoding="utf-8"))["files"])
    target = dist / "latest-mac.yml"
    target.write_text(yaml.safe_dump(merged, sort_keys=False), encoding="utf-8")
    for part in parts:
        part.unlink()
    return target


def main(argv: list[str]) -> int:
    tag, dist = argv[0], Path(argv[1])
    check_tag(tag, app_version())
    merged = merge_mac_manifests(dist)
    print(f"tag {tag} matches the app; " + (f"wrote {merged}" if merged else "no macOS manifests to merge"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
