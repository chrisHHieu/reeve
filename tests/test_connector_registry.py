"""What each connector descriptor gives the app to draw it with: a brand colour
and a logo id, with a grey for one that sets none, and the descriptors of connectors that are not
built yet, which can be listed and never connected.
"""

from __future__ import annotations

import re

import pytest

from harness.integrations.connectors import connector_list
from harness.integrations.connectors.descriptors import ConnectorDescriptor, get_descriptor, list_descriptors
from harness.safety.secrets import SecretStore

_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
_NOT_BUILT = ("datadog", "salesforce", "pagerduty")  # github is a real connector here


@pytest.fixture
def listed(tmp_path):
    return {c["name"]: c for c in connector_list(SecretStore(tmp_path / "secrets.json"))}


def test_every_descriptor_and_every_listed_row_has_a_hex_colour_and_a_logo_id(listed):
    for descriptor in list_descriptors():
        assert _HEX.match(descriptor.brand_color), f"{descriptor.name}: {descriptor.brand_color!r}"
        assert isinstance(descriptor.logo, str)
    for row in listed.values():
        assert _HEX.match(row["brand_color"]) and isinstance(row.get("logo"), str), row


@pytest.mark.parametrize(
    ("name", "colour"),
    [("slack", "#611f69"), ("telegram", "#229ed9"), ("github", "#1f2328"), ("datadog", "#632ca6"), ("salesforce", "#00a1e0"), ("pagerduty", "#06ac38")],
)
def test_a_connector_wears_its_own_brand_and_its_name_as_logo(listed, name, colour):
    descriptor = get_descriptor(name)
    assert descriptor is not None and (descriptor.brand_color, descriptor.logo) == (colour, name)
    assert listed[name]["brand_color"] == colour


def test_email_has_its_logo_and_a_descriptor_that_sets_nothing_is_grey():
    assert get_descriptor("email").logo == "email"
    bare = ConnectorDescriptor(name="zzz_unknown", title="Unknown", icon="?", blurb="", auth="none", two_way=False, fields=[], instructions=[])
    assert (bare.brand_color, bare.logo) == ("#6b7280", "")


@pytest.mark.parametrize("name", _NOT_BUILT)
def test_a_connector_not_built_yet_is_listed_but_can_never_be_connected(listed, name):
    descriptor = get_descriptor(name)
    assert descriptor.available is False and descriptor.validate is None
    assert not any(field.required for field in descriptor.fields)  # nothing to fill in, no way to connect
    row = listed[name]
    assert (row["available"], row["connected"], row["enabled"], row["fields"]) == (False, False, False, [])


def test_github_is_a_real_connector(listed):
    assert listed["github"]["available"] is True and get_descriptor("github").logo == "github"
