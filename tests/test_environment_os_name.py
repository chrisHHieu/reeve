"""The environment block names Windows 11 as Windows 11 (2026-09-24 audit: it said 10)."""

from __future__ import annotations

from harness.context import environment


def _windows(monkeypatch, release: str, version: str) -> None:
    monkeypatch.setattr(environment._platform, "mac_ver", lambda: ("", ("", "", ""), ""))
    monkeypatch.setattr(environment._platform, "system", lambda: "Windows")
    monkeypatch.setattr(environment._platform, "release", lambda: release)
    monkeypatch.setattr(environment._platform, "version", lambda: version)


def test_windows_11_is_told_apart_from_windows_10_by_build(monkeypatch):
    _windows(monkeypatch, "10", "10.0.26200")
    assert environment.os_name() == "Windows 11 (build 26200)"
    _windows(monkeypatch, "10", "10.0.19045")
    assert environment.os_name() == "Windows 10 (build 19045)"


def test_other_systems_keep_system_and_release(monkeypatch):
    monkeypatch.setattr(environment._platform, "mac_ver", lambda: ("", ("", "", ""), ""))
    monkeypatch.setattr(environment._platform, "system", lambda: "Linux")
    monkeypatch.setattr(environment._platform, "release", lambda: "6.8.0")
    assert environment.os_name() == "Linux 6.8.0"
