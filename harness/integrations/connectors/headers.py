"""Request headers and base URLs for the vendors the connectors call."""

from __future__ import annotations

from typing import Any


def bearer_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Accept": "application/json"}


#: Google's APIs take the same headers as any bearer-token API; the name keeps the call sites
#: readable and gives the 401 renewal one obvious place to look for them.
google_headers = bearer_headers


def graph_headers(token: str) -> dict[str, str]:
    return {**bearer_headers(token), "Content-Type": "application/json"}


def github_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def basic_auth(email: str, token: str) -> tuple[str, str]:
    return (email, token)


def atlassian_base(profile: dict[str, Any]) -> str:
    return str(profile.get("base_url", "")).rstrip("/")


def redmine_base(profile: dict[str, Any]) -> str:
    return str(profile.get("base_url", "")).rstrip("/")


def redmine_headers(profile: dict[str, Any]) -> dict[str, str]:
    return {"X-Redmine-API-Key": str(profile.get("api_key", "")), "Content-Type": "application/json"}
