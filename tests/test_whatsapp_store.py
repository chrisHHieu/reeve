"""The WhatsApp store: what a linked device keeps, and what the read tools ask of it.

Invented people and messages throughout; nothing here touches WhatsApp.
"""

from __future__ import annotations

import pytest

from harness.integrations.whatsapp.records import Chat, Contact, Message
from harness.integrations.whatsapp.store import Store

AN = "84901111111@s.whatsapp.net"
BINH = "84902222222@s.whatsapp.net"
TEAM = "1203630@g.us"
ME = "84909999999@s.whatsapp.net"


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "wa" / "messages.db")
    yield s
    s.close()


def _m(chat, id, ts, text="", *, sender=None, me=False, kind="text", target="", name="", media=None):
    return Message(
        chat=chat, id=id, sender=ME if me else (sender or chat), from_me=me, ts=ts, kind=kind,
        text=text, target_id=target, sender_name=name, media=media,
    )


def test_a_message_seen_twice_is_kept_once_and_chats_come_newest_first(store):
    assert store.add([_m(AN, "a1", 100, "Chiều nay họp không?", name="An")]) == 1
    assert store.add([_m(AN, "a1", 100, "Chiều nay họp không?", name="An")]) == 0  # history + live
    store.add([_m(BINH, "b1", 200, "ok")])
    assert [c["chat"] for c in store.chats()] == [BINH, AN]
    assert store.chats()[1]["last"]["text"] == "Chiều nay họp không?"


def test_a_chat_is_named_by_the_saved_name_then_its_own_then_the_one_they_set(store):
    store.add([_m(AN, "a1", 100, "hi", name="an.nguyen")])
    assert store.name_of(AN) == "an.nguyen"
    store.put_contact(Contact(jid=AN, name="An Nguyễn"))
    assert store.name_of(AN) == "An Nguyễn"
    assert store.name_of(BINH) == "+84902222222"  # nobody named them: the number
    store.put_chat(Chat(jid=TEAM, name="Nhóm dự án"))
    assert store.name_of(TEAM) == "Nhóm dự án"


def test_search_ignores_accents_and_needs_every_word(store):
    store.add([
        _m(AN, "a1", 100, "Mai đi uống cà phê được không?"),
        _m(AN, "a2", 110, "Được nhé"),
        _m(BINH, "b1", 120, "cà phê sữa"),
    ])
    assert [r["id"] for r in store.search("duoc")] == ["a2", "a1"]
    assert [r["id"] for r in store.search("ca phe duoc")] == ["a1"]
    assert [r["id"] for r in store.search("ca phe", chat=BINH)] == ["b1"]
    assert store.search("ca phe")[0]["chat_name"] == "+84902222222"
    assert store.search("") == []


def test_an_edit_a_revoke_and_a_reaction_change_the_message_they_name(store):
    store.add([_m(AN, "a1", 100, "Hẹn 3h"), _m(AN, "a2", 110, "nhầm")])
    store.add([
        _m(AN, "e1", 120, "Hẹn 4h", kind="edit", target="a1"),
        _m(AN, "r1", 130, kind="revoke", target="a2"),
        _m(AN, "x1", 140, "👍", kind="reaction", target="a1", me=True),
    ])
    first, second = store.messages(AN)
    assert first["text"] == "Hẹn 4h" and first["edited"] and first["reactions"] == ["👍 me"]
    assert second == {"id": "a2", "ts": 110, "from": "+84901111111", "kind": "text", "deleted": True}
    assert [r["id"] for r in store.search("4h")] == ["a1"]
    # An empty reaction takes the earlier one back.
    store.add([_m(AN, "x2", 150, "", kind="reaction", target="a1", me=True)])
    assert "reactions" not in store.messages(AN)[0]


def test_a_known_person_is_a_saved_contact_or_one_who_wrote(store):
    store.add([_m(AN, "a1", 100, "hi")])
    store.add([_m(BINH, "b1", 100, "hello?", me=True)])  # only I wrote: not known yet
    store.put_contact(Contact(jid="84903333333@s.whatsapp.net", name="Chị Ba"))
    assert store.known(AN)
    assert not store.known(BINH)
    assert store.known("84903333333@s.whatsapp.net")
    assert not store.known("84904444444@s.whatsapp.net")


def test_a_chat_is_found_by_jid_number_or_name(store):
    store.put_contact(Contact(jid=AN, name="An Nguyễn"))
    store.put_contact(Contact(jid=BINH, name="Bình Trần"))
    store.put_contact(Contact(jid="84905555555@s.whatsapp.net", name="Anh Tuấn"))
    assert store.resolve(AN) == (AN, [])
    assert store.resolve("+84 901 111 111") == (AN, [])
    assert store.resolve("0901 111 111", country_code="84") == (AN, [])
    assert store.resolve("binh") == (BINH, [])  # accents ignored
    jid, candidates = store.resolve("an")  # "An Nguyễn" and "Anh Tuấn" both hold it
    assert jid is None and len(candidates) == 2
    assert store.resolve("an nguyen") == (AN, [])  # an exact name wins
    assert store.resolve("nobody") == (None, [])


def test_reading_a_chat_is_bounded_by_count_and_time(store):
    store.add([_m(AN, f"a{i}", 100 + i, f"tin {i}") for i in range(10)])
    assert [m["id"] for m in store.messages(AN, limit=3)] == ["a7", "a8", "a9"]
    assert [m["id"] for m in store.messages(AN, before=103)] == ["a0", "a1", "a2"]
    assert [m["id"] for m in store.around(AN, "a5", span=2)] == ["a3", "a4", "a5", "a6", "a7"]


def test_a_group_message_names_its_sender_and_media_says_what_it_is(store):
    store.put_chat(Chat(jid=TEAM, name="Nhóm dự án", unread=2))
    store.add([_m(TEAM, "g1", 100, "bản vẽ", sender=AN, name="An",
                  kind="document", media={"mimetype": "application/pdf", "file_name": "ban-ve.pdf", "proto": "x"})])
    [shown] = store.messages(TEAM)
    assert shown["from"] == "An" and shown["kind"] == "document"
    assert shown["media"] == {"mimetype": "application/pdf", "file_name": "ban-ve.pdf"}  # no proto
    assert store.chats(unread_only=True)[0]["unread"] == 2
    assert [r["id"] for r in store.search("ban ve")] == ["g1"]  # the file name is searchable


def test_contacts_are_listed_by_name_and_found_by_number(store):
    store.put_contact(Contact(jid=BINH, name="Bình"))
    store.put_contact(Contact(jid=AN, name="An"))
    assert [c["name"] for c in store.contacts()] == ["An", "Bình"]
    assert store.contacts("0222")[0]["jid"] == BINH


def test_messages_older_than_the_keep_choice_go_with_their_reactions_and_names_stay(store):
    from harness.integrations.whatsapp.store import DAY_S

    now = 1_000 * DAY_S
    store.put_contact(Contact(jid=AN, name="An"))
    store.add([
        _m(AN, "old", now - 100 * DAY_S, "hẹn tuần trước"),
        _m(AN, "new", now - 2 * DAY_S, "mai gặp"),
        _m(AN, "r1", now - DAY_S, "👍", kind="reaction", target="old"),
    ])
    assert store.keep_for(None, now=now) == 0  # "keep all" drops nothing
    assert store.keep_for(90, now=now) == 1
    assert [m["id"] for m in store.messages(AN)] == ["new"]
    assert store.stats()["messages"] == 1 and store.stats()["oldest"] == now - 2 * DAY_S
    with store._lock:
        assert store._db.execute("SELECT COUNT(*) FROM reactions").fetchone()[0] == 0
    assert store.resolve("An")[0] == AN  # a name survives for a send and a search by name


def test_clearing_drops_every_message_and_gives_the_space_back(store):
    store.add([_m(AN, f"m{i}", 100 + i, "nội dung khá dài " * 40) for i in range(400)])
    before = store.stats()
    assert before["messages"] == 400 and before["chats"] == 1 and before["bytes"] > 0
    assert store.forget_messages() == 400
    after = store.stats()
    assert after["messages"] == 0 and after["oldest"] is None
    assert after["bytes"] < before["bytes"]


def test_the_keep_choice_defaults_to_ninety_days_and_takes_only_the_choices_offered(tmp_path):
    from harness.integrations.whatsapp.store import keep_days, set_keep_days

    assert keep_days(tmp_path) == 90
    set_keep_days(tmp_path, None)
    assert keep_days(tmp_path) is None
    set_keep_days(tmp_path, 30)
    assert keep_days(tmp_path) == 30
    with pytest.raises(ValueError):
        set_keep_days(tmp_path, 7)
    (tmp_path / "keep.json").write_text("not json")
    assert keep_days(tmp_path) == 90
