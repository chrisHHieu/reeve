"""Drive `fields` shapes — the two are not interchangeable, and mixing them is silent.

Google's Drive API wants a DIFFERENT `fields` shape per endpoint:

    files.list  ->  "files(id,name,...)"   a projection over the collection
    files.get   ->  "id,name,..."          a projection over one resource

Send the second to `files.list` and Google answers `400 Invalid field selection id`.

This is not hypothetical. Both tool groups are closures built inside ONE function, and a
second `_DRIVE_FIELDS = ...` there does not shadow locally — it rebinds the single
variable every earlier closure reads at call time. Adding the newer Drive tools with a
constant of the same name silently repointed `drive_search_files` and `drive_list_folder`
at the wrong shape. They 400'd against real Google while the new tools worked, and no test
noticed, because nothing exercised Drive's request shape.

So these assert the shape on the wire, per endpoint, with no live Google needed.
"""

from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from harness.integrations.connectors import accounts as _accounts
from harness.integrations.connectors.integration_tools import make_integration_tools
from harness.safety.secrets import SecretStore


def _connected(tmp_path):
    secrets = SecretStore(tmp_path / "s.json")
    _accounts.add_account(
        secrets,
        "google_drive",
        "me@example.com",
        {"access_token": "at", "type": "oauth", "enabled": True},
    )
    return secrets


def _capture(secrets, monkeypatch):
    """Run tools against a mock that records every request URL."""
    seen: list[httpx.Request] = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"files": [], "id": "x", "permissions": []})

    real = httpx.Client
    monkeypatch.setattr(
        httpx,
        "Client",
        lambda *a, **kw: real(*a, **{**kw, "transport": httpx.MockTransport(handler)}),
    )
    tools = {t.__name__: t for t in make_integration_tools(secrets)}
    return tools, seen


def _fields(request: httpx.Request) -> str:
    values = parse_qs(urlsplit(str(request.url)).query).get("fields")
    return values[0] if values else ""


LIST_TOOLS = [
    ("drive_search_files", {"query": "x"}),
    ("drive_list_folder", {}),
    ("drive_recent_files", {}),
]

ITEM_TOOLS = [
    ("drive_file_metadata", {"file_id": "f1"}),
    ("drive_copy_file", {"file_id": "f1"}),
]


@pytest.mark.parametrize("name,kwargs", LIST_TOOLS)
def test_collection_endpoints_ask_for_the_files_wrapped_shape(
    tmp_path, monkeypatch, name, kwargs
):
    tools, seen = _capture(_connected(tmp_path), monkeypatch)
    tools[name](**kwargs)

    assert seen, f"{name} made no request"
    fields = _fields(seen[-1])
    assert fields.startswith("files("), (
        f"{name} sent fields={fields!r} to a files.list endpoint; Google answers "
        f"'400 Invalid field selection id' for the bare shape"
    )
    assert fields.endswith(")")


@pytest.mark.parametrize("name,kwargs", ITEM_TOOLS)
def test_single_resource_endpoints_ask_for_the_bare_shape(
    tmp_path, monkeypatch, name, kwargs
):
    tools, seen = _capture(_connected(tmp_path), monkeypatch)
    tools[name](**kwargs)

    assert seen, f"{name} made no request"
    fields = _fields(seen[-1])
    assert fields and not fields.startswith("files("), (
        f"{name} sent the collection shape {fields!r} to a single-resource endpoint"
    )
    assert "id" in fields.split(",")


def test_the_two_drive_field_constants_stay_distinct():
    """The bug was two constants sharing one name in one closure.

    Reading the source is the only way to catch that: the values are indistinguishable
    once bound, and whichever assignment runs last wins for EVERY tool.
    """
    import inspect

    from harness.integrations.connectors.tool_builders import drive as _drive

    source = inspect.getsource(_drive.drive_tools)
    assignments = [
        line.strip()
        for line in source.splitlines()
        if line.strip().startswith("_DRIVE_FIELDS =")
    ]
    assert len(assignments) == 1, (
        f"_DRIVE_FIELDS is assigned {len(assignments)} times in one closure; every Drive "
        f"tool reads the LAST one at call time: {assignments}"
    )
