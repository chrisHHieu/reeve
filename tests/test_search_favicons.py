"""A search result's site mark: fetched once by the search that found it, kept here.

What must hold: only a host not kept yet is fetched, and once; only a small raster image is kept
(an SVG or an oversized body is not); a host whose icon failed is not asked again for a while; the
model's text never carries a favicon address; the card says which rows have a mark; and the local
route hands out only what is kept, by a plain host.
"""

from __future__ import annotations

import pytest

from harness.integrations.web import favicons
from harness.integrations.web.providers import SearchResult, WebSearchProvider
from harness.integrations.web.search_present import _search_present_result, _web_search_render
from harness.integrations.web.tool import make_web_search_tool

PNG = b"\x89PNG\r\n\x1a\n" + b"x" * 100


class _Stream:
    def __init__(self, status, media, body):
        self.status_code = status
        self.headers = {"content-type": media}
        self.extensions = {}
        self._body = body

    def close(self):
        pass

    def iter_bytes(self):
        for i in range(0, len(self._body), 1024):
            yield self._body[i : i + 1024]


@pytest.fixture
def icons(tmp_path, monkeypatch):
    monkeypatch.setenv("REEVE_STATE_DIR", str(tmp_path))
    served: dict[str, tuple[int, str, bytes]] = {}
    asked: list[str] = []

    def open_(url):
        asked.append(url)
        return _Stream(*served.get(url, (404, "text/html", b"")))

    monkeypatch.setattr(favicons, "_open", open_)
    return served, asked


def test_a_new_host_is_fetched_once_and_kept(icons):
    served, asked = icons
    served["https://a.example/favicon.ico"] = (200, "image/png", PNG)
    pairs = [("https://www.a.example/page", "https://a.example/favicon.ico")]
    assert favicons.prefetch(pairs) == {"a.example"}
    assert favicons.prefetch(pairs) == {"a.example"}  # kept: not asked again
    assert asked == ["https://a.example/favicon.ico"]
    assert favicons.cached("a.example").read_bytes() == PNG


def test_a_subdomain_without_an_icon_wears_its_sites(icons):
    served, asked = icons
    served["https://celeris.example/favicon.ico"] = (200, "image/png", PNG)
    assert favicons.prefetch([("https://docs.celeris.example/models", None)]) == {"docs.celeris.example"}
    assert asked == ["https://docs.celeris.example/favicon.ico", "https://celeris.example/favicon.ico"]
    assert favicons.cached("docs.celeris.example").read_bytes() == PNG


def test_only_a_small_raster_image_is_kept_and_a_miss_is_remembered(icons):
    served, asked = icons
    served["https://svg.example/i.svg"] = (200, "image/svg+xml", b"<svg/>")
    served["https://big.example/i.png"] = (200, "image/png", PNG * 3000)
    served["https://page.example/i"] = (200, "text/html", b"<html>")
    pairs = [
        ("https://svg.example/", "https://svg.example/i.svg"),
        ("https://big.example/", "https://big.example/i.png"),
        ("https://page.example/", "https://page.example/i"),
    ]
    assert favicons.prefetch(pairs) == set()
    assert all(favicons.cached(h) is None for h in ("svg.example", "big.example", "page.example"))
    asked.clear()
    favicons.prefetch(pairs)
    assert asked == []  # a failed host waits a week before it is asked again


def test_an_icon_address_on_this_machines_network_is_refused_before_any_connection(tmp_path, monkeypatch):
    """The address is a third party's data: it passes the same guard as `web_fetch`."""
    monkeypatch.setenv("REEVE_STATE_DIR", str(tmp_path))
    for icon in ("http://127.0.0.1/favicon.png", "http://169.254.169.254/latest", "http://192.168.1.1/i.png"):
        with pytest.raises(PermissionError):  # the guard, not a failed connection
            favicons._open(icon)
        assert favicons.prefetch([("https://public.example/", icon)]) == set()
        assert favicons.cached("public.example") is None
        (tmp_path / "favicons" / "public.example.miss").unlink()


def test_a_plain_host_only():
    assert favicons.host_of("https://WWW.Example.com/x") == "example.com"
    assert favicons.host_of("file:///etc/passwd") is None
    assert favicons.cached("../secrets.json") is None


class _Provider(WebSearchProvider):
    name = "exa"

    def search(self, query, max_results=5, options=None):
        return [
            SearchResult(title="A", url="https://a.example/1", snippet="s", favicon="https://a.example/favicon.ico"),
            SearchResult(title="B", url="https://b.example/2", snippet="s"),
        ]


def test_the_card_knows_which_rows_have_a_mark_and_the_model_never_sees_one(icons):
    served, _ = icons
    served["https://a.example/favicon.ico"] = (200, "image/png", PNG)
    value = make_web_search_tool(provider=_Provider())(query="q")
    assert [r.get("icon") for r in value["results"]] == [True, None]
    text = _web_search_render({"query": "q"}, value)
    assert "favicon" not in text and "icon" not in text
    card = _search_present_result({"query": "q"}, value).card
    assert [s.get("icon") for s in card["sources"]] == [True, None]


def test_the_route_hands_out_only_what_is_kept(icons, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from harness.interface.server.app import create_app
    from harness.interface.server.manager import SessionManager

    served, _ = icons
    served["https://a.example/favicon.ico"] = (200, "image/x-icon", PNG)
    favicons.prefetch([("https://a.example/", "https://a.example/favicon.ico")])
    client = TestClient(create_app(SessionManager(data_dir=tmp_path / "data")))
    got = client.get("/v1/favicons/a.example")
    assert got.status_code == 200 and got.content == PNG
    assert got.headers["content-type"] == "image/x-icon" and got.headers["x-content-type-options"] == "nosniff"
    assert client.get("/v1/favicons/b.example").status_code == 404
