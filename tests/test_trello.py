"""Trello connector: what each tool sends to Trello, what it makes of the answer, the
guards on files, and the two ways in (one click and a pasted key and token).

Trello is never called: `calls.request` is replaced by a fake board that answers by method and
path, and the tests assert on what would have left the process. Invented boards and people.
"""

from __future__ import annotations

import asyncio
import types
from datetime import datetime, timedelta, timezone

import pytest

from harness.integrations.connectors import calls as connector_calls
from harness.integrations.connectors.integration_tools import make_integration_tools
from harness.integrations.connectors.lifecycle.connect import connect_connector
from harness.safety.secrets import SecretStore

KEY, TOKEN = "k" * 32, "t" * 64


def _soon(days: float) -> str:
    return (datetime.now(timezone.utc) + timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S.000Z")


BOARD = {
    "id": "b1", "name": "Ra mắt app", "url": "https://trello.com/b/AbC123/ra-mat-app", "desc": "", "closed": False,
    "lists": [{"id": "l-todo", "name": "Cần làm"}, {"id": "l-done", "name": "Xong"}],
    "labels": [{"id": "lab-red", "name": "Gấp", "color": "red"}, {"id": "lab-x", "name": "", "color": "blue"}],
    "members": [{"id": "m-an", "username": "an", "fullName": "An Nguyễn"}],
    "customFields": [
        {"id": "cf-pri", "name": "Ưu tiên", "type": "list", "options": [{"id": "o-hi", "value": {"text": "Cao"}}]},
        {"id": "cf-ok", "name": "Đã duyệt", "type": "checkbox"},
        {"id": "cf-note", "name": "Ghi chú", "type": "text"},
    ],
}
CARDS = [
    {"id": "c1", "name": "Viết release note", "idList": "l-todo", "due": _soon(2), "labels": [{"id": "lab-red", "name": "Gấp"}], "idMembers": ["m-an"]},
    {"id": "c2", "name": "Chụp ảnh màn hình", "idList": "l-todo", "due": _soon(-1), "labels": []},
    {"id": "c3", "name": "Đăng bài", "idList": "l-todo", "due": _soon(20), "labels": []},
    {"id": "c4", "name": "Đã làm", "idList": "l-done", "due": _soon(1), "dueComplete": True, "labels": []},
]
CARD = {
    "id": "c1", "name": "Viết release note", "idList": "l-todo", "idBoard": "b1", "desc": "Bản 1.2", "shortUrl": "https://trello.com/c/Xy9",
    "list": {"name": "Cần làm"}, "board": {"name": "Ra mắt app"}, "labels": [{"id": "lab-red", "name": "Gấp"}],
    "members": [{"id": "m-an", "username": "an"}],
    "checklists": [{"id": "cl1", "name": "Việc", "checkItems": [
        {"id": "i2", "name": "Gửi duyệt", "state": "incomplete", "pos": 2},
        {"id": "i1", "name": "Viết nháp", "state": "complete", "pos": 1},
    ]}],
    "attachments": [
        {"id": "a-file", "name": "anh.png", "isUpload": True, "bytes": 1200, "url": "https://trello.com/1/cards/c1/attachments/a-file/download/anh.png"},
        {"id": "a-link", "name": "Figma", "isUpload": False, "url": "https://www.figma.com/file/x"},
    ],
    "actions": [{"id": "act1", "date": "2026-10-19T08:00:00.000Z", "memberCreator": {"username": "an"}, "data": {"text": "Xong nháp rồi"}}],
    "customFieldItems": [{"idCustomField": "cf-pri", "idValue": "o-hi"}, {"idCustomField": "cf-ok", "value": {"checked": "true"}}],
}


@pytest.fixture
def trello(tmp_path, monkeypatch):
    """Trello connected (one account), with every call recorded and answered by the fake board."""
    monkeypatch.setenv("REEVE_STATE_DIR", str(tmp_path / "state"))
    secrets = SecretStore(tmp_path / "secrets.json")
    assert connect_connector(secrets, "trello", {"api_key": KEY, "token": TOKEN}, validate=False)["ok"]
    sent: list[dict] = []
    answers = {
        ("GET", "/members/me"): {"id": "m-me", "username": "toi", "fullName": "Tôi"},
        ("GET", "/boards/AbC123"): BOARD,
        ("GET", "/boards/b1/cards"): CARDS,
        ("GET", "/boards/b1/customFields"): BOARD["customFields"],
        ("GET", "/cards/c1"): CARD,
        ("GET", "/cards/c1/attachments/a-file"): CARD["attachments"][0],
        ("GET", "/cards/c1/attachments/a-link"): CARD["attachments"][1],
        ("POST", "/cards"): {"id": "c9", "name": "Mới", "idList": "l-todo"},
    }

    def fake_request(method, url, *, headers=None, params=None, json=None, auth=None):
        path = url.replace("https://api.trello.com/1", "")
        sent.append({"method": method, "url": url, "path": path, "headers": headers or {}, "params": params, "json": json})
        return {"ok": True, "data": answers.get((method, path), {"id": "x"})}

    monkeypatch.setattr(connector_calls, "request", fake_request)
    folder = tmp_path / "session"
    folder.mkdir()
    root = types.SimpleNamespace(path=folder, writable=True)
    tools = {t.__name__: t for t in make_integration_tools(secrets, roots=[root])}
    return types.SimpleNamespace(tools=tools, sent=sent, secrets=secrets, folder=folder, root=root)


def test_not_connected_says_so(tmp_path, monkeypatch):
    monkeypatch.setenv("REEVE_STATE_DIR", str(tmp_path / "state"))
    tools = {t.__name__: t for t in make_integration_tools(SecretStore(tmp_path / "s.json"))}
    assert "not connected" in tools["trello_list_boards"]()["error"]


def test_the_token_rides_in_the_header_and_never_in_a_url(trello):
    trello.tools["trello_whoami"]()
    call = trello.sent[-1]
    assert call["headers"]["Authorization"] == f'OAuth oauth_consumer_key="{KEY}", oauth_token="{TOKEN}"'
    assert TOKEN not in call["url"] and TOKEN not in str(call["params"])


def test_a_board_read_names_every_list_label_member_and_field_with_its_id(trello):
    board = trello.tools["trello_get_board"]("https://trello.com/b/AbC123/ra-mat-app")
    assert trello.sent[-1]["path"] == "/boards/AbC123"  # the URL's short link, which Trello takes as an id
    assert board["lists"] == [{"id": "l-todo", "name": "Cần làm"}, {"id": "l-done", "name": "Xong"}]
    assert {"id": "lab-x", "name": "", "color": "blue"} in board["labels"]
    assert board["members"][0] == {"id": "m-an", "username": "an", "name": "An Nguyễn"}
    assert board["custom_fields"][0]["options"] == [{"id": "o-hi", "text": "Cao"}]


def test_cards_due_soon_include_the_overdue_and_leave_out_the_done(trello):
    cards = trello.tools["trello_list_cards"](board_id="b1", due_within_days=7)["cards"]
    assert [c["id"] for c in cards] == ["c2", "c1"]  # overdue first, then by due date; c4 is done
    assert [c["id"] for c in trello.tools["trello_list_cards"](board_id="b1", label="gấp")["cards"]] == ["c1"]
    assert [c["id"] for c in trello.tools["trello_list_cards"](board_id="b1", member_id="m-an")["cards"]] == ["c1"]
    assert "error" in trello.tools["trello_list_cards"]()


def test_a_card_reads_whole(trello):
    card = trello.tools["trello_get_card"]("https://trello.com/c/c1/viet")
    assert card["list"] == {"id": "l-todo", "name": "Cần làm"} and card["desc"] == "Bản 1.2"
    assert [i["name"] for i in card["checklists"][0]["items"]] == ["Viết nháp", "Gửi duyệt"]  # in board order
    assert card["checklists"][0]["items"][0]["done"] is True
    assert card["attachments"] == [
        {"id": "a-file", "name": "anh.png", "kind": "file", "bytes": 1200},
        {"id": "a-link", "name": "Figma", "kind": "link", "url": "https://www.figma.com/file/x"},
    ]
    assert card["comments"] == [{"id": "act1", "by": "an", "date": "2026-10-19T08:00:00.000Z", "text": "Xong nháp rồi"}]
    assert card["custom_fields"] == {"Ưu tiên": "Cao", "Đã duyệt": True}


def test_an_update_sends_only_what_changes_and_labels_one_at_a_time(trello):
    result = trello.tools["trello_update_card"]("c1", due="", done=True, list_id="l-done", add_label_ids=["lab-red"], remove_member_ids=["m-an"])
    put, add, remove = trello.sent[0], trello.sent[1], trello.sent[2]
    assert put["method"] == "PUT" and put["json"] == {"due": None, "dueComplete": True, "idList": "l-done"}  # "" clears the date
    assert (add["method"], add["path"], add["json"]) == ("POST", "/cards/c1/idLabels", {"value": "lab-red"})
    assert (remove["method"], remove["path"]) == ("DELETE", "/cards/c1/idMembers/m-an")
    assert "added label lab-red" in result["changed"]
    assert "error" in trello.tools["trello_update_card"]("c1")


def test_a_copied_card_keeps_everything_from_its_source(trello):
    trello.tools["trello_create_card"]("l-todo", copy_from_card_id="https://trello.com/c/Src1/x")
    body = trello.sent[-1]["json"]
    assert body["idCardSource"] == "Src1" and body["keepFromSource"] == "all" and "name" not in body
    assert "error" in trello.tools["trello_create_card"]("l-todo")  # neither a name nor a source


def test_a_custom_field_is_set_in_the_shape_its_type_needs(trello):
    trello.tools["trello_set_custom_field"]("c1", "cf-pri", option_id="o-hi")
    trello.tools["trello_set_custom_field"]("c1", "cf-ok", value="yes")
    trello.tools["trello_set_custom_field"]("c1", "cf-note", value="")
    puts = [c for c in trello.sent if c["method"] == "PUT"]
    assert [c["json"] for c in puts] == [{"idValue": "o-hi"}, {"value": {"checked": "true"}}, {"value": ""}]
    assert puts[0]["path"] == "/cards/c1/customField/cf-pri/item"
    assert "no custom field" in trello.tools["trello_set_custom_field"]("c1", "cf-nope", value="x")["error"]


def test_only_a_file_in_the_session_folder_is_attached(trello, tmp_path, monkeypatch):
    from harness.integrations.connectors.tool_builders import trello_cards

    uploaded = []
    monkeypatch.setattr(trello_cards, "api_upload", lambda endpoint, profile, file, name, cover: uploaded.append((endpoint, file.name, name)) or {"ok": True, "data": {"id": "att", "name": name}})
    outside = tmp_path / "secret.txt"
    outside.write_text("không gửi", encoding="utf-8")
    assert "outside the session" in trello.tools["trello_attach"]("c1", path=str(outside))["error"]
    assert "either path" in trello.tools["trello_attach"]("c1", path="a", url="b")["error"]
    inside = trello.folder / "bao-cao.pdf"
    inside.write_bytes(b"%PDF")
    assert trello.tools["trello_attach"]("c1", path=str(inside))["attachment"] == {"id": "att", "name": "bao-cao.pdf"}
    assert uploaded == [("https://api.trello.com/1/cards/c1/attachments", "bao-cao.pdf", "bao-cao.pdf")]
    remote = {t.__name__: t for t in make_integration_tools(trello.secrets, roots=[trello.root], remote=True)}
    assert "remote session" in remote["trello_attach"]("c1", path=str(inside))["error"]


def test_a_file_attachment_is_saved_in_the_session_folder_and_a_link_is_not(trello, monkeypatch):
    from harness.integrations.connectors.tool_builders import trello as trello_reads

    fetched = []
    monkeypatch.setattr(trello_reads, "api_download", lambda url, profile: fetched.append(url) or (b"PNG", ""))
    first = trello.tools["trello_download_attachment"]("c1", "a-file")
    second = trello.tools["trello_download_attachment"]("c1", "a-file")
    assert first["path"].endswith("anh.png") and second["path"].endswith("anh-1.png")
    assert (trello.folder / "anh.png").read_bytes() == b"PNG"
    assert fetched == [CARD["attachments"][0]["url"]] * 2
    assert "a link" in trello.tools["trello_download_attachment"]("c1", "a-link")["error"]


def test_delete_names_what_goes_and_needs_the_parent_where_trello_does(trello):
    assert trello.tools["trello_delete"]("comment", "act1")["deleted"] == "comment"
    assert (trello.sent[-1]["method"], trello.sent[-1]["path"]) == ("DELETE", "/actions/act1")
    assert "needs card_id" in trello.tools["trello_delete"]("attachment", "a-file")["error"]
    assert "what is one of" in trello.tools["trello_delete"]("board", "b1")["error"]


def test_a_second_account_is_its_own_and_a_call_can_name_it(trello):
    from harness.integrations.connectors import accounts

    accounts.add_account(trello.secrets, "trello", "khach", {"type": "token", "enabled": True, "api_key": KEY, "token": "w" * 64, "account": "khach"})
    trello.tools["trello_whoami"](account="khach")
    assert 'oauth_token="' + "w" * 64 in trello.sent[-1]["headers"]["Authorization"]


def test_reads_never_ask_and_every_change_does(trello):
    from harness.integrations.connectors import connector_list

    entry = next(c for c in connector_list(trello.secrets) if c["name"] == "trello")
    kinds = {t["name"]: t["kind"] for t in entry["tools"]}
    reads = {n for n, k in kinds.items() if k == "read"}
    assert reads == {"trello_whoami", "trello_list_boards", "trello_get_board", "trello_list_cards",
                     "trello_get_card", "trello_search", "trello_activity"}
    assert len(kinds) == 18


# -- the two ways in -------------------------------------------------------------------------


def test_one_click_is_offered_only_when_a_key_is_set(tmp_path, monkeypatch):
    from harness.integrations.connectors import connector_list

    from harness.integrations.connectors.descriptors import get_descriptor

    monkeypatch.setenv("REEVE_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.delenv("REEVE_TRELLO_API_KEY", raising=False)
    row = lambda: next(c for c in connector_list(SecretStore(tmp_path / "s.json")) if c["name"] == "trello")  # noqa: E731
    assert not get_descriptor("trello").oauth_client_id, "no Power-Up key ships in the source"
    assert row()["one_click"] is False and [f["key"] for f in row()["fields"]] == ["api_key", "token"]
    monkeypatch.setenv("REEVE_TRELLO_API_KEY", KEY)
    assert row()["one_click"] is True


def test_the_landing_post_resolves_only_its_own_sign_in():
    from harness.integrations.connectors.trello_auth import TokenGrant

    async def scenario():
        grant = TokenGrant()
        assert grant.deliver(TOKEN, "anything") is False  # nothing waiting

        async def press_allow():
            await asyncio.sleep(0)
            assert grant.deliver(TOKEN, "forged") is False  # another site cannot know the state
            assert grant.deliver(TOKEN, grant.state) is True

        import webbrowser

        opened = []
        original = webbrowser.open
        webbrowser.open = lambda url: opened.append(url) or True
        try:
            token, _ = await asyncio.gather(grant.run("https://trello.com/1/authorize?key=k"), press_allow())
        finally:
            webbrowser.open = original
        return token, opened, grant.state

    token, opened, state_after = asyncio.run(scenario())
    assert token == TOKEN and opened == ["https://trello.com/1/authorize?key=k"] and state_after is None


def test_the_authorize_page_asks_for_a_token_that_comes_back_in_the_fragment():
    from urllib.parse import parse_qs, urlsplit

    from harness.integrations.connectors.trello_auth import authorize_url

    query = parse_qs(urlsplit(authorize_url(KEY, "http://127.0.0.1:8765/connectors/trello/callback")).query)
    assert query["key"] == [KEY] and query["callback_method"] == ["fragment"] and query["expiration"] == ["never"]
    assert query["scope"] == ["read,write,account"] and query["return_url"] == ["http://127.0.0.1:8765/connectors/trello/callback"]


def test_one_click_stores_the_account_the_same_way_a_pasted_token_does(tmp_path, monkeypatch):
    from harness.integrations.connectors import accounts
    from harness.integrations.connectors.descriptor.model import ValidationResult
    from harness.integrations.connectors.descriptors import get_descriptor
    from harness.integrations.connectors.trello_auth import GRANT
    from harness.interface.server.manager import SessionManager

    monkeypatch.setenv("REEVE_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("REEVE_TRELLO_API_KEY", KEY)
    monkeypatch.setattr(get_descriptor("trello"), "validate", lambda creds: ValidationResult(True, identity="toi"))

    async def allow(url):
        await asyncio.sleep(0)
        GRANT.deliver(TOKEN, GRANT.state)
        return TOKEN

    import webbrowser

    monkeypatch.setattr(webbrowser, "open", lambda url: True)
    manager = SessionManager(data_dir=tmp_path / "data")

    async def scenario():
        return (await asyncio.gather(manager.connect_token_grant("trello"), allow("")))[0]

    result = asyncio.run(scenario())
    assert result["ok"] and result["account"] == "toi"
    [(account_id, profile)] = accounts.list_accounts(manager.secrets, "trello")
    assert account_id == "toi" and profile["api_key"] == KEY and profile["token"] == TOKEN
    assert "trello" not in manager._mcp_authorizing and "trello" not in manager._mcp_errors


def test_one_click_without_a_key_says_to_use_manual(tmp_path, monkeypatch):
    from harness.interface.server.manager import SessionManager

    from harness.integrations.connectors.descriptors import get_descriptor

    monkeypatch.setenv("REEVE_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.delenv("REEVE_TRELLO_API_KEY", raising=False)
    monkeypatch.setattr(get_descriptor("trello"), "oauth_client_id", "")
    manager = SessionManager(data_dir=tmp_path / "data")
    result = asyncio.run(manager.connect_token_grant("trello"))
    assert not result["ok"] and "Manual" in result["error"] and "Manual" in manager._mcp_errors["trello"]


class _Resp:
    def __init__(self, status, payload=None):
        self.status_code, self._payload = status, payload

    def json(self):
        return self._payload


def test_the_check_names_both_the_key_and_the_token_and_returns_the_username(monkeypatch):
    import httpx

    from harness.integrations.connectors.descriptor.validators import validate_trello

    seen = {}
    monkeypatch.setattr(httpx, "get", lambda url, **kw: seen.update(url=url, **kw) or _Resp(401))
    refused = validate_trello({"api_key": KEY, "token": "bad"})
    assert not refused.ok and "key or the token" in refused.error
    assert "bad" not in str(seen.get("params")) and 'oauth_token="bad"' in seen["headers"]["Authorization"]
    monkeypatch.setattr(httpx, "get", lambda url, **kw: _Resp(200, {"id": "m1", "username": "toi"}))
    assert validate_trello({"api_key": KEY, "token": TOKEN}).identity == "toi"


def test_the_landing_answers_the_browser_without_the_sidecar_token_and_carries_the_state(tmp_path, monkeypatch):
    """Trello's redirect comes from the person's browser, which cannot carry the sidecar token; the
    page it lands on holds the waiting sign-in's state, and a post without that state is refused."""
    from fastapi.testclient import TestClient

    from harness.integrations.connectors.trello_auth import CALLBACK_PATH, GRANT
    from harness.interface.server.app import create_app
    from harness.interface.server.manager import SessionManager

    monkeypatch.setenv("REEVE_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("REEVE_API_TOKEN", "sidecar-secret")
    client = TestClient(create_app(SessionManager(data_dir=tmp_path / "data")))
    assert client.get("/v1/connectors").status_code == 401  # everything else still needs it

    monkeypatch.setattr(GRANT, "state", "st4te-for-this-flow")
    page = client.get(CALLBACK_PATH)
    assert page.status_code == 200 and '"st4te-for-this-flow"' in page.text and "location.hash" in page.text
    assert page.headers["cache-control"] == "no-store"
    assert client.post(CALLBACK_PATH, json={"token": TOKEN, "state": "forged"}).json() == {"ok": False}
