"""Figma and Canva tools reach their vendors' API roots.

The roots were defined next to the tools in the file the tools were later split out of, and the
split left them behind: every `figma_*` and `canva_*` call ended in a NameError that no test
looked at, because the lint rule that finds undefined names is switched off wherever a star
import hides them. This calls each tool once with the network stood in for and checks the URL.
"""

from __future__ import annotations

import pytest

from harness.integrations.connectors import calls as connector_calls
from harness.integrations.connectors.integration_tools import make_integration_tools
from harness.safety.secrets import SecretStore

CASES = [
    ("figma", "figma_get_file", {"file_key": "abc"}, "https://api.figma.com/v1/files/abc"),
    ("figma", "figma_get_comments", {"file_key": "abc"}, "https://api.figma.com/v1/files/abc/comments"),
    ("figma", "figma_export_images", {"file_key": "abc", "node_ids": "1:2"}, "https://api.figma.com/v1/images/abc"),
    ("canva", "canva_list_designs", {}, "https://api.canva.com/rest/v1/designs"),
    ("canva", "canva_get_design", {"design_id": "d1"}, "https://api.canva.com/rest/v1/designs/d1"),
    ("canva", "canva_export_design", {"design_id": "d1"}, "https://api.canva.com/rest/v1/exports"),
    ("canva", "canva_get_export", {"export_id": "e1"}, "https://api.canva.com/rest/v1/exports/e1"),
]


@pytest.mark.parametrize("connector,tool,arguments,url", CASES)
def test_the_tool_calls_its_vendor(tmp_path, monkeypatch, connector, tool, arguments, url):
    secrets = SecretStore(tmp_path / "s.json")
    secrets.put(f"{connector}:default", {"access_token": "t"})
    seen = []

    def fake_request(method, target, **kwargs):
        seen.append(target)
        return {"ok": True, "data": {}}

    monkeypatch.setattr(connector_calls, "request", fake_request)
    tools = {t.__name__: t for t in make_integration_tools(secrets)}
    result = tools[tool](**arguments)

    assert "NameError" not in str(result), result
    assert seen == [url]
