"""The preview door serves what a person opened to look at, and nothing else.

A page the session wrote runs with its own folder behind it (its stylesheet, its script, the
next page a link names) at an address that is not the API's. These tests pin what that address
refuses: a key it did not issue, a path out of the folder, a dotfile, a type that is not a
page's, and the API itself.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from harness.interface.server import create_app
from harness.interface.server.manager import SessionManager
from harness.llm.providers import AssistantTurn, ModelCapabilities, ProviderClient
from harness.interface.server.preview_door import PreviewDoor, page_policy
from harness.session.sessions import SessionRecord


@pytest.fixture
def site(tmp_path) -> Path:
    root = (tmp_path / "ws").resolve()
    (root / "site" / "img").mkdir(parents=True)
    (root / "site" / "index.html").write_text(
        "<!doctype html><html><head><title>Home</title><link rel=stylesheet href=style.css></head>"
        "<body><a href=pricing.html>Pricing</a></body></html>",
        encoding="utf-8",
    )
    (root / "site" / "pricing.html").write_text("<h1>Pricing</h1>", encoding="utf-8")
    (root / "site" / "style.css").write_text("body{margin:0}", encoding="utf-8")
    (root / "site" / "app.js").write_text("window.ready = 1;", encoding="utf-8")
    (root / "site" / "img" / "dot.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)
    (root / "site" / ".env").write_text("TOKEN=not-a-real-one", encoding="utf-8")
    (root / "site" / "notes.py").write_text("x = 1\n", encoding="utf-8")
    (root / "clip.mp4").write_bytes(bytes(range(256)) * 8)
    (tmp_path / "outside.html").write_text("<h1>outside</h1>", encoding="utf-8")
    return root


def _door(root: Path, **grant) -> tuple[TestClient, str]:
    door = PreviewDoor()
    key = door.grant(root, **grant)
    return TestClient(door.app(), base_url="http://127.0.0.1"), key


def test_a_page_is_served_with_the_files_beside_it(site):
    client, key = _door(site)
    page = client.get(f"/s/{key}/site/index.html")
    assert page.status_code == 200 and page.headers["content-type"].startswith("text/html")
    assert "<title>Home</title>" in page.text
    assert client.get(f"/s/{key}/site/style.css").headers["content-type"].startswith("text/css")
    assert client.get(f"/s/{key}/site/app.js").headers["content-type"].startswith("text/javascript")
    assert client.get(f"/s/{key}/site/pricing.html").text.endswith("<h1>Pricing</h1>")
    assert client.get(f"/s/{key}/site/img/dot.png").headers["content-type"] == "image/png"
    # A folder answers with its index page, at an address that ends in a slash.
    assert "<title>Home</title>" in client.get(f"/s/{key}/site/").text
    assert client.get(f"/s/{key}/site", follow_redirects=False).headers["location"] == f"/s/{key}/site/"


def test_what_the_door_refuses(site):
    client, key = _door(site)
    refused = [
        "/s/not-a-key/site/index.html",  # a key it did not issue
        f"/s/{key}/site/.env",  # a dotfile
        f"/s/{key}/site/notes.py",  # a type that is not a page's
        f"/s/{key}/../outside.html",  # out of the folder
        f"/s/{key}/site/%2e%2e/%2e%2e/outside.html",
        f"/s/{key}/site/missing.html",
    ]
    for path in refused:
        assert client.get(path).status_code == 404, path
    assert client.post(f"/s/{key}/site/index.html").status_code == 405


@pytest.mark.skipif(os.name == "nt", reason="making a symlink needs a privilege on Windows")
def test_a_link_that_points_out_of_the_folder_is_out_of_the_folder(site, tmp_path):
    (site / "site" / "leak.html").symlink_to(tmp_path / "outside.html")
    client, key = _door(site)
    assert client.get(f"/s/{key}/site/leak.html").status_code == 404


def test_a_page_cannot_reach_the_internet_until_the_person_says_so(site):
    client, key = _door(site)
    policy = client.get(f"/s/{key}/site/index.html").headers["content-security-policy"]
    # Everything is this address or inline: no other host is named anywhere in it.
    assert "default-src http://127.0.0.1 'unsafe-inline'" in policy
    assert "connect-src http://127.0.0.1;" in policy and "form-action http://127.0.0.1;" in policy
    assert "frame-src http://127.0.0.1;" in policy and "object-src 'none'" in policy
    assert "*" not in policy.split("frame-ancestors")[0]
    # The stylesheet and the script are sent with the same policy as the page.
    assert client.get(f"/s/{key}/site/app.js").headers["content-security-policy"] == policy

    open_client, open_key = _door(site, internet=True)
    opened = open_client.get(f"/s/{open_key}/site/index.html").headers["content-security-policy"]
    assert "default-src" not in opened and opened.startswith("frame-ancestors ")
    assert page_policy("http://127.0.0.1:5", False) != page_policy("http://127.0.0.1:5", True)


def test_a_page_tells_the_app_where_it_is_and_what_was_blocked(site):
    client, key = _door(site)
    text = client.get(f"/s/{key}/site/index.html").text
    head = text.index("<head>") + len("<head>")
    # The bridge is the first thing in the head, so it hears a violation the page's own
    # first stylesheet causes.
    assert text[head:].startswith("<script>(function(){")
    assert "securitypolicyviolation" in text and "reevePreview" in text
    # A page with no head still gets it, and a stylesheet never does.
    assert client.get(f"/s/{key}/site/pricing.html").text.startswith("<script>(function(){")
    assert "reevePreview" not in client.get(f"/s/{key}/site/style.css").text


def test_a_page_is_read_in_the_encoding_it_names_and_as_utf8_when_it_names_none(site):
    """A header beats a `<meta charset>`. The door names UTF-8 only for a page that says
    nothing, so a page saved in another encoding that says so is not turned into noise."""
    (site / "site" / "legacy.html").write_bytes(
        b'<html><head><meta charset="windows-1258"></head><body>Ho\xf2p</body></html>'
    )
    (site / "site" / "bare.html").write_bytes("<h1>Họp · thứ Hai</h1>".encode("utf-8"))
    client, key = _door(site)
    assert client.get(f"/s/{key}/site/legacy.html").headers["content-type"] == "text/html"
    assert client.get(f"/s/{key}/site/bare.html").headers["content-type"] == "text/html; charset=utf-8"
    assert client.get(f"/s/{key}/site/index.html").headers["content-type"] == "text/html; charset=utf-8"


def test_a_single_file_grant_serves_that_file_alone_and_in_ranges(site):
    client, key = _door(site, only="clip.mp4")
    whole = client.get(f"/s/{key}/clip.mp4")
    assert whole.status_code == 200 and whole.headers["content-type"] == "video/mp4"
    part = client.get(f"/s/{key}/clip.mp4", headers={"Range": "bytes=10-19"})
    assert part.status_code == 206 and part.content == bytes(range(10, 20))
    assert part.headers["content-range"] == f"bytes 10-19/{256 * 8}"
    assert client.get(f"/s/{key}/site/index.html").status_code == 404


def test_only_the_apps_own_window_may_read_bytes_from_the_door(site):
    client, key = _door(site)
    ours = client.get(f"/s/{key}/site/app.js", headers={"Origin": "http://localhost:1420"})
    assert ours.headers["access-control-allow-origin"] == "http://localhost:1420"
    theirs = client.get(f"/s/{key}/site/app.js", headers={"Origin": "https://example.com"})
    assert "access-control-allow-origin" not in theirs.headers


def test_old_grants_are_dropped_and_a_revoked_key_stops_answering(site):
    door = PreviewDoor()
    first = door.grant(site)
    keys = [door.grant(site) for _ in range(64)]
    client = TestClient(door.app(), base_url="http://127.0.0.1")
    assert client.get(f"/s/{first}/site/index.html").status_code == 404
    assert client.get(f"/s/{keys[-1]}/site/index.html").status_code == 200
    door.revoke(keys[-1])
    assert client.get(f"/s/{keys[-1]}/site/index.html").status_code == 404


def test_the_listener_answers_on_its_own_port_and_only_by_its_own_name(site):
    import http.client

    door = PreviewDoor()
    try:
        key = door.grant(site)
        url = door.url(key, "site/index.html")
        assert url.startswith(door.origin + "/s/") and door.listening == door.origin
        port = int(door.origin.rsplit(":", 1)[1])

        def ask(host: str) -> http.client.HTTPResponse:
            connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            connection.putrequest("GET", f"/s/{key}/site/index.html", skip_host=True)
            connection.putheader("Host", host)
            connection.endheaders()
            return connection.getresponse()

        answered = ask(f"127.0.0.1:{port}")
        assert answered.status == 200
        assert f"default-src {door.origin} " in answered.getheader("Content-Security-Policy")
        # A name pointed at 127.0.0.1 by a page elsewhere reaches nothing.
        assert ask("evil.example").status == 404
    finally:
        door.close()
    assert door.listening is None


# -- through the API: a grant belongs to a session, and a previewed page never uses the API --


class Provider(ProviderClient):
    def complete(self, *, model, messages, tools=None, **settings):
        return AssistantTurn(text="ok", finish_reason="stop")

    def capabilities(self, model):
        return ModelCapabilities()


def _manager(site: Path, tmp_path, monkeypatch) -> SessionManager:
    """A manager with one session, `s1`, whose folder is the site."""
    monkeypatch.setenv("REEVE_STATE_DIR", str(tmp_path / "state"))
    mgr = SessionManager(workspace=site, provider=Provider())
    mgr.session_store.save(
        SessionRecord(session_id="s1", workspace=str(site), model="gpt-5.5", mode="interactive")
    )
    return mgr


def test_a_grant_is_made_for_a_file_of_the_session_and_nothing_outside_it(site, tmp_path, monkeypatch):
    mgr = _manager(site, tmp_path, monkeypatch)
    try:
        granted = mgr.preview_grant("s1", "site/index.html")
        assert granted["ok"] and granted["internet"] is False
        assert granted["url"] == f"{granted['origin']}/s/{granted['key']}/site/index.html"
        assert mgr.preview_grant("s1", "site/index.html", internet=True)["internet"] is True

        assert mgr.preview_grant("s1", "../outside.html")["ok"] is False
        assert mgr.preview_grant("s1", "site/notes.py")["ok"] is False  # not a page's type
        assert mgr.preview_grant("s1", "site/missing.html")["ok"] is False

        client = TestClient(create_app(mgr))
        single = client.post("/v1/sessions/s1/preview/grant", json={"path": "clip.mp4", "single": True}).json()
        assert single["ok"] and single["url"].endswith("/clip.mp4")
        # "internet" is a person's yes: anything but a real true is a no.
        loose = client.post("/v1/sessions/s1/preview/grant", json={"path": "site/index.html", "internet": "true"})
        assert loose.json()["internet"] is False
    finally:
        mgr.preview_door.close()


def test_a_previewed_page_cannot_use_the_reeve_api(site, tmp_path, monkeypatch):
    monkeypatch.delenv("REEVE_API_TOKEN", raising=False)
    mgr = _manager(site, tmp_path, monkeypatch)
    client = TestClient(create_app(mgr))  # no token: a dev server, where the Origin is the only bar
    try:
        assert client.get("/v1/health").status_code == 200
        origin = mgr.preview_grant("s1", "site/index.html")["origin"]
        refused = client.get("/v1/sessions", headers={"Origin": origin})
        assert refused.status_code == 403 and "previewed page" in refused.json()["error"]
        # The app's own window is still let in.
        assert client.get("/v1/sessions", headers={"Origin": "http://localhost:1420"}).status_code == 200
    finally:
        mgr.preview_door.close()
