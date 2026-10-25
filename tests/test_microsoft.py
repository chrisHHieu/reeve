"""Microsoft 365 connectors: the browser sign-in and its renewal, and what the Outlook and
OneDrive tools send to Microsoft Graph and make of the answers.

Microsoft is never called: `calls.request` and the module seams (`api_fetch`, `api_upload`, httpx)
are replaced, and the tests assert on what would have left the process. Invented people and files.
"""

from __future__ import annotations

import asyncio
import time
import types

import pytest

from harness.integrations.connectors import accounts, calls as connector_calls, microsoft_auth
from harness.integrations.connectors.integration_tools import make_integration_tools
from harness.safety.secrets import SecretStore

CLIENT = "00000000-1111-2222-3333-444444444444"
GRAPH = "https://graph.microsoft.com/v1.0"


def _signed_in(secrets, connector="outlook", email="an@contoso.com", **extra):
    profile = {"type": "oauth", "enabled": True, "account": email, "access_token": "at-1", "refresh_token": "rt-1",
               "ms_client_id": CLIENT, "expires_at": int(time.time()) + 3600, "scope": "Mail.ReadWrite offline_access", **extra}
    accounts.add_account(secrets, connector, email, profile)
    return profile


@pytest.fixture
def graph(tmp_path, monkeypatch):
    """Outlook and OneDrive signed in, every Graph call recorded and answered from `answers`."""
    monkeypatch.setenv("REEVE_STATE_DIR", str(tmp_path / "state"))
    secrets = SecretStore(tmp_path / "secrets.json")
    _signed_in(secrets, "outlook")
    _signed_in(secrets, "onedrive")
    _signed_in(secrets, "teams")
    sent: list[dict] = []
    answers: dict = {}

    def fake_request(method, url, *, headers=None, params=None, json=None, auth=None):
        path = url.replace(GRAPH, "")
        sent.append({"method": method, "path": path, "headers": headers or {}, "params": params or {}, "json": json})
        answer = answers.get((method, path), {"id": "x"})
        return answer if isinstance(answer, dict) and ("ok" in answer or "error" in answer) else {"ok": True, "data": answer}

    monkeypatch.setattr(connector_calls, "request", fake_request)
    folder = tmp_path / "session"
    folder.mkdir()
    root = types.SimpleNamespace(path=folder, writable=True)
    tools = {t.__name__: t for t in make_integration_tools(secrets, roots=[root])}
    return types.SimpleNamespace(tools=tools, sent=sent, answers=answers, secrets=secrets, folder=folder, root=root, tmp=tmp_path)


# -- signing in and staying signed in ----------------------------------------------------------


def test_one_click_asks_for_a_refresh_token_and_keeps_the_account_by_its_email(tmp_path, monkeypatch):
    from harness.extensions.mcp import pkce_auth
    from harness.interface.server.manager import SessionManager

    monkeypatch.setenv("REEVE_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("REEVE_MICROSOFT_CLIENT_ID", CLIENT)
    asked = {}

    async def fake_authorize(**kw):
        asked.update(kw)
        return {"access_token": "at-new", "refresh_token": "rt-new", "expires_in": 3600, "scope": kw["scope"]}

    monkeypatch.setattr(pkce_auth, "authorize_raw", fake_authorize)
    monkeypatch.setattr(microsoft_auth, "account_email", lambda token: "an@contoso.com")
    manager = SessionManager(data_dir=tmp_path / "data")
    result = asyncio.run(manager.connect_pkce_connector("outlook"))

    assert result["ok"], result
    scopes = asked["scope"].split()
    assert {"offline_access", "User.Read", "Mail.ReadWrite", "Mail.Send", "Calendars.ReadWrite"} <= set(scopes)
    assert "Files.ReadWrite.All" not in scopes  # Outlook never asks for files
    assert asked["client_id"] == CLIENT and asked["auth_endpoint"].startswith("https://login.microsoftonline.com/common/")
    assert asked["extra_params"] == {"prompt": "select_account"}
    [(account_id, profile)] = accounts.list_accounts(manager.secrets, "outlook")
    assert account_id == "an@contoso.com" and profile["refresh_token"] == "rt-new" and profile["ms_client_id"] == CLIENT
    assert profile["expires_at"] > time.time() + 3000


def test_one_click_is_offered_only_when_a_client_id_is_set(tmp_path, monkeypatch):
    from harness.integrations.connectors import connector_list

    from harness.integrations.connectors.descriptors import get_descriptor

    monkeypatch.setenv("REEVE_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.delenv("REEVE_MICROSOFT_CLIENT_ID", raising=False)
    rows = lambda: {c["name"]: c for c in connector_list(SecretStore(tmp_path / "s.json"))}  # noqa: E731
    assert not get_descriptor("outlook").oauth_client_id, "no Entra client id ships in the source"
    assert rows()["outlook"]["one_click"] is False and rows()["onedrive"]["one_click"] is False
    monkeypatch.setenv("REEVE_MICROSOFT_CLIENT_ID", CLIENT)
    assert rows()["outlook"]["one_click"] is True and rows()["onedrive"]["one_click"] is True


class _Resp:
    def __init__(self, status, payload):
        self.status_code, self._payload = status, payload

    def json(self):
        return self._payload


def test_an_aged_token_is_renewed_before_the_call_and_the_rotated_refresh_token_is_kept(graph, monkeypatch):
    import httpx

    key = "outlook:account:an@contoso.com"
    graph.secrets.put(key, {**graph.secrets.get(key), "expires_at": int(time.time()) - 10})
    posted = {}
    monkeypatch.setattr(httpx, "post", lambda url, data=None, **kw: posted.update(url=url, **data) or _Resp(200, {
        "access_token": "at-2", "refresh_token": "rt-2", "expires_in": 3600}))
    graph.tools["outlook_list_folders"]()
    assert posted["url"] == microsoft_auth.TOKEN and posted["refresh_token"] == "rt-1" and posted["client_id"] == CLIENT
    assert graph.sent[-1]["headers"]["Authorization"] == "Bearer at-2"
    stored = graph.secrets.get(key)
    assert stored["refresh_token"] == "rt-2" and stored["access_token"] == "at-2"


def test_a_401_renews_the_token_this_process_issued_and_only_that_one(graph, monkeypatch):
    import httpx

    key = "outlook:account:an@contoso.com"
    microsoft_auth.note_issued(graph.secrets, key, graph.secrets.get(key))
    monkeypatch.setattr(httpx, "post", lambda url, data=None, **kw: _Resp(200, {"access_token": "at-3", "expires_in": 3600}))
    assert microsoft_auth.renew_after_401("someone-elses-token") is None
    assert microsoft_auth.renew_after_401("at-1") == "at-3"
    assert graph.secrets.get(key)["refresh_token"] == "rt-1"  # no rotation in the answer: the old one stays


def test_a_pasted_token_is_left_alone(graph, monkeypatch):
    import httpx

    accounts.add_account(graph.secrets, "outlook", "pasted@contoso.com", {"type": "oauth", "enabled": True, "access_token": "pasted", "account": "pasted@contoso.com"})
    monkeypatch.setattr(httpx, "post", lambda *a, **k: pytest.fail("a pasted token has nothing to renew with"))
    graph.tools["outlook_list_folders"](account="pasted@contoso.com")
    assert graph.sent[-1]["headers"]["Authorization"] == "Bearer pasted"


# -- Outlook -----------------------------------------------------------------------------------


def test_a_search_ranks_by_text_and_a_listing_filters_and_sorts(graph):
    graph.tools["outlook_search_messages"](query="hợp đồng")
    params = graph.sent[-1]["params"]
    assert params["$search"] == '"hợp đồng"' and "$orderby" not in params and "$filter" not in params
    graph.tools["outlook_search_messages"](folder="inbox", unread_only=True, from_address="o'neil@contoso.com", since="2026-10-01T00:00:00Z")
    call = graph.sent[-1]
    assert call["path"] == "/me/mailFolders/inbox/messages"
    assert call["params"]["$filter"] == "isRead eq false and from/emailAddress/address eq 'o''neil@contoso.com' and receivedDateTime ge 2026-10-01T00:00:00Z"
    assert call["params"]["$orderby"] == "receivedDateTime desc"


def test_a_message_reads_as_text_with_its_real_attachments(graph):
    graph.answers[("GET", "/me/messages/m1")] = {"subject": "Báo giá", "from": {"emailAddress": {"name": "Bình", "address": "binh@contoso.com"}},
                                                  "body": {"content": "Gửi anh báo giá."}, "hasAttachments": True, "isRead": False,
                                                  "toRecipients": [{"emailAddress": {"address": "an@contoso.com"}}]}
    graph.answers[("GET", "/me/messages/m1/attachments")] = {"value": [
        {"id": "a1", "name": "bao-gia.pdf", "size": 2000, "contentType": "application/pdf", "isInline": False},
        {"id": "a2", "name": "logo.png", "isInline": True}]}
    message = graph.tools["outlook_read_message"]("m1")
    assert graph.sent[0]["headers"]["Prefer"] == 'outlook.body-content-type="text"'
    assert message["from"] == "Bình <binh@contoso.com>" and message["body"] == "Gửi anh báo giá." and message["unread"] is True
    assert message["attachment_list"] == [{"id": "a1", "name": "bao-gia.pdf", "bytes": 2000, "type": "application/pdf"}]


def test_mail_goes_out_with_only_session_files_and_within_the_limit(graph, monkeypatch):
    from harness.integrations.connectors.tool_builders import outlook_mail

    outside = graph.tmp / "luong.xlsx"
    outside.write_bytes(b"x")
    assert "outside the session" in graph.tools["outlook_send_mail"](["binh@contoso.com"], "Lương", "…", attachments=[str(outside)])["error"]
    report = graph.folder / "bao-cao.txt"
    report.write_bytes(b"so lieu")
    graph.tools["outlook_send_mail"]("binh@contoso.com; chi@contoso.com", "Báo cáo", "Gửi chị.", attachments=[str(report)])
    message = graph.sent[-1]["json"]["message"]
    assert [r["emailAddress"]["address"] for r in message["toRecipients"]] == ["binh@contoso.com", "chi@contoso.com"]
    assert message["attachments"][0]["name"] == "bao-cao.txt" and message["attachments"][0]["contentBytes"] == "c28gbGlldQ=="
    monkeypatch.setattr(outlook_mail, "MAX_ATTACH", 3)
    tools = {t.__name__: t for t in make_integration_tools(graph.secrets, roots=[graph.root])}
    assert "MB" in tools["outlook_send_mail"](["b@contoso.com"], "s", "b", attachments=[str(report)])["error"]
    remote = {t.__name__: t for t in make_integration_tools(graph.secrets, roots=[graph.root], remote=True)}
    assert "remote session" in remote["outlook_send_mail"](["b@contoso.com"], "s", "b", attachments=[str(report)])["error"]


def test_a_reply_is_a_draft_made_from_the_message_then_sent(graph):
    graph.answers[("POST", "/me/messages/m1/createReplyAll")] = {"id": "d9"}
    result = graph.tools["outlook_reply"]("m1", "Em đồng ý.", reply_all=True)
    assert [(c["method"], c["path"]) for c in graph.sent] == [("POST", "/me/messages/m1/createReplyAll"), ("POST", "/me/messages/d9/send")]
    assert graph.sent[0]["json"] == {"comment": "Em đồng ý."} and result["reply_all"] is True


def test_filing_changes_then_moves_each_message_and_names_the_ones_that_failed(graph):
    graph.answers[("PATCH", "/me/messages/bad")] = {"error": "HTTP 404"}
    result = graph.tools["outlook_organize"](["m1", "bad"], move_to="archive", read=True)
    assert [(c["method"], c["path"]) for c in graph.sent] == [
        ("PATCH", "/me/messages/m1"), ("POST", "/me/messages/m1/move"), ("PATCH", "/me/messages/bad")]
    assert graph.sent[0]["json"] == {"isRead": True} and graph.sent[1]["json"] == {"destinationId": "archive"}
    assert result["done"] == 1 and result["failed"][0]["id"] == "bad" and result["ok"] is False


def test_events_read_as_rows_and_free_times_ask_for_the_meeting_length(graph):
    graph.answers[("GET", "/me/calendarView")] = {"value": [{"id": "e1", "subject": "Họp tuần", "start": {"dateTime": "2026-10-21T02:00:00"},
                                                             "end": {"dateTime": "2026-10-21T03:00:00"}, "attendees": [{}, {}],
                                                             "onlineMeeting": {"joinUrl": "https://teams.example/j"}, "responseStatus": {"response": "accepted"}}]}
    events = graph.tools["outlook_list_events"]()["events"]
    assert events == [{"id": "e1", "subject": "Họp tuần", "start": "2026-10-21T02:00:00", "end": "2026-10-21T03:00:00",
                       "attendees": 2, "join_url": "https://teams.example/j", "my_response": "accepted"}]
    graph.tools["outlook_find_times"](["binh@contoso.com"], duration_minutes=45)
    assert graph.sent[-1]["path"] == "/me/findMeetingTimes" and graph.sent[-1]["json"]["meetingDuration"] == "PT45M"


# -- OneDrive ----------------------------------------------------------------------------------


def test_an_item_is_named_by_its_path_its_id_or_its_drive(graph):
    from harness.integrations.connectors.tool_builders.onedrive import item_path

    assert item_path("/Báo cáo/q3.xlsx") == "/me/drive/root:/B%C3%A1o%20c%C3%A1o/q3.xlsx:"
    assert item_path("/") == "/me/drive/root" and item_path("01ABC!12") == "/me/drive/items/01ABC%2112"
    assert item_path("01XYZ", drive_id="b!drive") == "/drives/b%21drive/items/01XYZ"


def test_opening_a_file_saves_it_where_the_reader_reads_and_a_folder_is_refused(graph, monkeypatch):
    from harness.integrations.connectors.tool_builders import onedrive

    graph.answers[("GET", "/me/drive/root:/q3.docx:")] = {"id": "f1", "name": "q3.docx", "size": 10, "file": {}, "@microsoft.graph.downloadUrl": "https://dl.example/f1"}
    graph.answers[("GET", "/me/drive/root:/Reports:")] = {"id": "d1", "name": "Reports", "folder": {"childCount": 2}}
    fetched = []
    monkeypatch.setattr(onedrive, "api_fetch", lambda url: fetched.append(url) or (b"DOCX", ""))
    saved = graph.tools["onedrive_download"]("/q3.docx")
    assert saved["path"].endswith("q3.docx") and (graph.folder / "q3.docx").read_bytes() == b"DOCX" and fetched == ["https://dl.example/f1"]
    assert "a folder" in graph.tools["onedrive_download"]("/Reports")["error"]


def test_sharing_says_who_can_open_it(graph):
    graph.answers[("POST", "/me/drive/items/f1/createLink")] = {"link": {"webUrl": "https://1drv.ms/x"}}
    assert graph.tools["onedrive_share"]("f1", who="anyone")["url"] == "https://1drv.ms/x"
    assert graph.sent[-1]["json"] == {"type": "view", "scope": "anonymous"}
    graph.tools["onedrive_share"]("f1", access="edit", who="people", emails=["binh@contoso.com"])
    assert graph.sent[-1]["path"] == "/me/drive/items/f1/invite" and graph.sent[-1]["json"]["roles"] == ["write"]
    assert "emails" in graph.tools["onedrive_share"]("f1", who="people")["error"]


def test_a_large_upload_goes_in_chunks_graph_accepts(tmp_path, monkeypatch):
    import httpx

    from harness.integrations.connectors.tool_builders import onedrive

    assert onedrive.CHUNK % (320 * 1024) == 0  # Graph's rule for every chunk but the last
    monkeypatch.setattr(onedrive, "CHUNK", 2 * 320 * 1024)
    big = tmp_path / "video.mp4"
    big.write_bytes(b"v" * (onedrive.SIMPLE_UPLOAD + 1))
    puts = []

    class Client:
        def __init__(self, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, url, headers=None, json=None):
            assert url.endswith("/createUploadSession") and headers["Authorization"] == "Bearer tok"
            return types.SimpleNamespace(status_code=200, json=lambda: {"uploadUrl": "https://up.example/s"}, text="")

        def put(self, url, content=None, headers=None, params=None):
            puts.append((url, headers, len(content)))
            return types.SimpleNamespace(status_code=201, json=lambda: {"id": "f9", "name": "video.mp4"}, text="")

    monkeypatch.setattr(httpx, "Client", Client)
    result = onedrive.api_upload("tok", f"{GRAPH}/me/drive/root:/video.mp4:", big, "rename")
    size, chunk = big.stat().st_size, onedrive.CHUNK
    expected = [f"bytes {start}-{min(start + chunk, size) - 1}/{size}" for start in range(0, size, chunk)]
    assert result["ok"] and [h["Content-Range"] for _, h, _ in puts] == expected and len(expected) == 7
    assert all("Authorization" not in h for _, h, _ in puts)  # the session URL is the credential


def test_reads_never_ask_and_every_change_does(graph):
    from harness.integrations.connectors import connector_list

    rows = {c["name"]: c for c in connector_list(graph.secrets)}
    reads = {t["name"] for name in ("outlook", "onedrive", "teams") for t in rows[name]["tools"] if t["kind"] == "read"}
    assert reads == {"outlook_whoami", "outlook_search_messages", "outlook_read_message", "outlook_read_thread",
                     "outlook_list_folders", "outlook_find_people", "outlook_list_events", "outlook_find_times",
                     "onedrive_search", "onedrive_list", "onedrive_download",
                     "teams_list_chats", "teams_read_chat", "teams_list_teams", "teams_list_channels", "teams_find_people"}
    assert len(rows["outlook"]["tools"]) == 18 and len(rows["onedrive"]["tools"]) == 8 and len(rows["teams"]["tools"]) == 8


def test_the_one_request_door_renews_a_microsoft_token_on_a_401(graph, monkeypatch):
    import httpx

    key = "outlook:account:an@contoso.com"
    microsoft_auth.note_issued(graph.secrets, key, graph.secrets.get(key))
    monkeypatch.setattr(httpx, "post", lambda url, data=None, **kw: _Resp(200, {"access_token": "at-4", "expires_in": 3600}))
    again = []
    client = types.SimpleNamespace(request=lambda method, url, **kw: again.append(kw["headers"]["Authorization"]) or "second answer")
    refused = types.SimpleNamespace(status_code=401)
    answer = connector_calls._send_again_if_token_died(
        refused, client, "GET", f"{GRAPH}/me", headers={"Authorization": "Bearer at-1"}, params=None, json=None, auth=None)
    assert answer == "second answer" and again == ["Bearer at-4"]


# -- Teams -------------------------------------------------------------------------------------


def test_teams_never_asks_for_the_scope_only_an_admin_can_grant(tmp_path, monkeypatch):
    """One admin-only scope in the sign-in stops it for everyone whose admin has not granted it."""
    from harness.integrations.connectors.descriptors import get_descriptor

    scopes = get_descriptor("teams").oauth_scope.split()
    assert "ChannelMessage.Read.All" not in scopes and {"Chat.ReadWrite", "ChannelMessage.Send"} <= set(scopes)
    assert "Mail.ReadWrite" not in scopes  # Teams asks only for its own


def test_chats_list_by_last_activity_and_read_as_text_oldest_first(graph):
    graph.answers[("GET", "/me/chats")] = {"value": [
        {"id": "c1", "chatType": "oneOnOne", "members": [{"displayName": "An"}, {"displayName": "Bình"}],
         "lastMessagePreview": {"from": {"user": {"displayName": "Bình"}}, "createdDateTime": "2026-10-20T08:00:00Z",
                                "body": {"contentType": "html", "content": "<p>Mai <b>họp</b> nhé</p>"}}},
        {"id": "c2", "chatType": "group", "topic": "Dự án Alpha", "members": []}]}
    chats = graph.tools["teams_list_chats"]()["chats"]
    assert graph.sent[-1]["params"]["$orderby"] == "lastMessagePreview/createdDateTime desc"
    assert chats[0] == {"id": "c1", "title": "An, Bình", "kind": "oneOnOne",
                        "last": {"from": "Bình", "date": "2026-10-20T08:00:00Z", "text": "Mai họp nhé"}}
    assert [c["id"] for c in graph.tools["teams_list_chats"](query="alpha")["chats"]] == ["c2"]
    graph.answers[("GET", "/chats/c1/messages")] = {"value": [
        {"id": "m2", "createdDateTime": "2026-10-20T08:01:00Z", "from": {"user": {"displayName": "An"}}, "body": {"contentType": "text", "content": "Ok"}},
        {"id": "sys", "messageType": "systemEventMessage", "createdDateTime": "2026-10-20T07:00:00Z"},
        {"id": "m1", "createdDateTime": "2026-10-20T08:00:00Z", "from": {"user": {"displayName": "Bình"}}, "body": {"contentType": "html", "content": "<div>Mai họp</div>"}}]}
    assert [(m["id"], m["text"]) for m in graph.tools["teams_read_chat"]("c1")["messages"]] == [("m1", "Mai họp"), ("m2", "Ok")]


def test_writing_to_people_opens_their_chat_with_me_in_it_then_sends(graph):
    graph.answers[("GET", "/me")] = {"id": "me-1"}
    graph.answers[("POST", "/chats")] = {"id": "c9"}
    result = graph.tools["teams_send_chat"]("Chào chị", to=["binh@contoso.com"])
    create, send = graph.sent[-2], graph.sent[-1]
    assert create["json"]["chatType"] == "oneOnOne"
    assert [m["user@odata.bind"] for m in create["json"]["members"]] == [f"{GRAPH}/users('me-1')", f"{GRAPH}/users('binh%40contoso.com')"]
    assert (send["path"], send["json"]) == ("/chats/c9/messages", {"body": {"contentType": "text", "content": "Chào chị"}})
    assert result["chat_id"] == "c9"
    graph.tools["teams_send_chat"]("Cả nhà ơi", to=["a@contoso.com", "b@contoso.com"], topic="Họp")
    assert graph.sent[-2]["json"]["chatType"] == "group" and graph.sent[-2]["json"]["topic"] == "Họp"


def test_a_channel_reply_goes_into_the_thread_and_a_reaction_names_its_place(graph):
    graph.tools["teams_post_channel"]("t1", "ch1", "Đã xong", reply_to="p1")
    assert graph.sent[-1]["path"] == "/teams/t1/channels/ch1/messages/p1/replies"
    graph.tools["teams_post_channel"]("t1", "ch1", "Thông báo", subject="Lịch nghỉ")
    assert graph.sent[-1]["json"]["subject"] == "Lịch nghỉ"
    graph.tools["teams_react"]("m1", "heart", chat_id="c1")
    assert (graph.sent[-1]["path"], graph.sent[-1]["json"]) == ("/chats/c1/messages/m1/setReaction", {"reactionType": "heart"})
    assert "chat_id" in graph.tools["teams_react"]("m1")["error"]
    assert "one of" in graph.tools["teams_react"]("m1", "fire", chat_id="c1")["error"]


def test_people_search_asks_for_the_count_header_graph_needs(graph):
    graph.tools["teams_find_people"]("Bình")
    call = graph.sent[-1]
    assert call["headers"]["ConsistencyLevel"] == "eventual" and call["params"]["$search"] == '"displayName:Bình" OR "mail:Bình"'


def test_a_chat_message_keeps_its_inline_marks_on_one_line():
    from harness.integrations.connectors.tool_builders.teams import chat_text

    assert chat_text("<p>Mai <b>họp</b> lúc <a href='x'>9h</a>&nbsp;nhé</p><p>Nhớ mang laptop</p>") == "Mai họp lúc 9h nhé\nNhớ mang laptop"
    assert chat_text("Dòng 1<br>Dòng 2<br/><at id='0'>Bình</at> ơi") == "Dòng 1\nDòng 2\nBình ơi"
    assert chat_text("<b>bold</b> <bdi>x</bdi>") == "bold x"  # <bdi> is not <br>
