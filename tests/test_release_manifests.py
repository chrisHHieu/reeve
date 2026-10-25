"""The release job's tag check and the merge of the two macOS update manifests
(`scripts/release_manifests.py`)."""

from __future__ import annotations

import pytest
import yaml

from scripts.release_manifests import check_tag, merge_mac_manifests


def test_a_tag_must_name_the_apps_version():
    check_tag("v1.2.3", "1.2.3")
    with pytest.raises(SystemExit, match="tag v1.2.4 != package.json version 1.2.3"):
        check_tag("v1.2.4", "1.2.3")


def test_the_two_architectures_manifests_become_one_that_lists_both(tmp_path):
    arm = {"version": "1.2.3", "files": [{"url": "Reeve-1.2.3-arm64-mac.zip"}], "path": "Reeve-1.2.3-arm64-mac.zip"}
    intel = {"version": "1.2.3", "files": [{"url": "Reeve-1.2.3-mac.zip"}], "path": "Reeve-1.2.3-mac.zip"}
    (tmp_path / "latest-mac-macos-arm64.yml").write_text(yaml.safe_dump(arm), encoding="utf-8")
    (tmp_path / "latest-mac-macos-x64.yml").write_text(yaml.safe_dump(intel), encoding="utf-8")
    merged = yaml.safe_load(merge_mac_manifests(tmp_path).read_text(encoding="utf-8"))
    assert [f["url"] for f in merged["files"]] == ["Reeve-1.2.3-arm64-mac.zip", "Reeve-1.2.3-mac.zip"]
    assert merged["path"] == "Reeve-1.2.3-arm64-mac.zip"  # the first part's other fields stand
    assert sorted(p.name for p in tmp_path.iterdir()) == ["latest-mac.yml"]  # the parts are gone


def test_a_release_without_a_macos_build_has_nothing_to_merge(tmp_path):
    assert merge_mac_manifests(tmp_path) is None and not list(tmp_path.iterdir())
