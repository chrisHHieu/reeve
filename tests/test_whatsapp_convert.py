"""WhatsApp's protobufs as records, built from the real message types with invented
content. Skipped where the optional `whatsapp` extra is not installed."""

from __future__ import annotations

import base64

import pytest

E2E = pytest.importorskip("neonize.proto.waE2E.WAWebProtobufsE2E_pb2")
HISTORY = pytest.importorskip("neonize.proto.waHistorySync.WAWebProtobufsHistorySync_pb2")
NEONIZE = pytest.importorskip("neonize.proto.Neonize_pb2")
WEB = pytest.importorskip("neonize.proto.waWeb.WAWebProtobufsWeb_pb2")
COMMON = pytest.importorskip("neonize.proto.waCommon.WACommon_pb2")

from harness.integrations.whatsapp.convert import describe, from_event, from_history, media_message  # noqa: E402

AN = "84901111111"


def test_plain_and_extended_text_and_a_reply():
    assert describe(E2E.Message(conversation="chào")) == {"kind": "text", "text": "chào"}
    reply = E2E.Message(extendedTextMessage=E2E.ExtendedTextMessage(
        text="ok nhé", contextInfo=E2E.ContextInfo(stanzaID="Q1", mentionedJID=[f"{AN}@s.whatsapp.net"])
    ))
    assert describe(reply) == {"kind": "text", "text": "ok nhé", "target_id": "Q1", "mentions": [f"{AN}@s.whatsapp.net"]}


def test_a_wrapped_message_is_unwrapped():
    wrapped = E2E.Message(ephemeralMessage=E2E.FutureProofMessage(message=E2E.Message(conversation="biến mất")))
    assert describe(wrapped)["text"] == "biến mất"


def test_media_keeps_what_a_download_needs_and_drops_the_thumbnail():
    image = E2E.Message(imageMessage=E2E.ImageMessage(
        mimetype="image/jpeg", caption="bản vẽ", fileLength=1234, JPEGThumbnail=b"x" * 5000, directPath="/v/t62/abc",
    ))
    said = describe(image)
    assert said["kind"] == "image" and said["text"] == "bản vẽ"
    assert said["media"]["mimetype"] == "image/jpeg" and said["media"]["size"] == 1234
    rebuilt = media_message(said["media"], E2E.Message)
    assert rebuilt.imageMessage.directPath == "/v/t62/abc" and not rebuilt.imageMessage.JPEGThumbnail
    assert len(base64.b64decode(said["media"]["proto"])) < 200


def test_a_voice_note_is_told_from_an_audio_file():
    voice = describe(E2E.Message(audioMessage=E2E.AudioMessage(mimetype="audio/ogg; codecs=opus", seconds=7, PTT=True)))
    audio = describe(E2E.Message(audioMessage=E2E.AudioMessage(mimetype="audio/mpeg", seconds=200)))
    assert (voice["kind"], voice["media"]["seconds"]) == ("voice", 7)
    assert audio["kind"] == "audio"


def test_an_edit_a_revoke_and_a_reaction_name_their_target():
    key = COMMON.MessageKey(ID="T1")
    revoke = E2E.Message(protocolMessage=E2E.ProtocolMessage(key=key, type=0))
    edit = E2E.Message(protocolMessage=E2E.ProtocolMessage(key=key, type=14, editedMessage=E2E.Message(conversation="sửa")))
    reaction = E2E.Message(reactionMessage=E2E.ReactionMessage(key=key, text="❤️"))
    assert describe(revoke) == {"kind": "revoke", "target_id": "T1"}
    assert describe(edit) == {"kind": "edit", "target_id": "T1", "text": "sửa"}
    assert describe(reaction) == {"kind": "reaction", "target_id": "T1", "text": "❤️"}
    other = E2E.Message(protocolMessage=E2E.ProtocolMessage(type=5))  # a history-sync notice
    assert describe(other) == {"kind": "system"}


def test_a_live_message_event_becomes_a_record():
    event = NEONIZE.Message(
        Info=NEONIZE.MessageInfo(
            MessageSource=NEONIZE.MessageSource(
                Chat=NEONIZE.JID(User=AN, Server="s.whatsapp.net"),
                Sender=NEONIZE.JID(User=AN, Device=3, Server="s.whatsapp.net"),
                IsFromMe=False,
            ),
            ID="M1", Timestamp=1_760_000_000, Pushname="An",
        ),
        Message=E2E.Message(conversation="tối nay rảnh không"),
    )
    record = from_event(event)
    assert (record.chat, record.sender, record.id, record.ts) == (f"{AN}@s.whatsapp.net", f"{AN}@s.whatsapp.net", "M1", 1_760_000_000)
    assert record.sender_name == "An" and record.text == "tối nay rảnh không" and not record.from_me


def test_a_history_chunk_brings_chats_messages_and_names():
    data = HISTORY.HistorySync(
        syncType=0,
        conversations=[HISTORY.Conversation(
            ID=f"{AN}@s.whatsapp.net", name="An", unreadCount=2, pinned=1, conversationTimestamp=1_760_000_100,
            messages=[
                HISTORY.HistorySyncMsg(message=WEB.WebMessageInfo(
                    key=COMMON.MessageKey(remoteJID=f"{AN}@s.whatsapp.net", fromMe=False, ID="H1"),
                    message=E2E.Message(conversation="lâu rồi không gặp"), messageTimestamp=1_760_000_000,
                )),
                HISTORY.HistorySyncMsg(message=WEB.WebMessageInfo(
                    key=COMMON.MessageKey(remoteJID=f"{AN}@s.whatsapp.net", fromMe=True, ID="H2"),
                    message=E2E.Message(conversation="ừ"), messageTimestamp=1_760_000_050,
                )),
            ],
        )],
        pushnames=[HISTORY.Pushname(ID=f"{AN}@s.whatsapp.net", pushname="An")],
    )
    chats, messages, contacts = from_history(data)
    assert chats[0].jid == f"{AN}@s.whatsapp.net" and chats[0].unread == 2 and chats[0].pinned
    assert [(m.id, m.from_me, m.sender) for m in messages] == [("H1", False, f"{AN}@s.whatsapp.net"), ("H2", True, "")]
    assert contacts[0].push_name == "An"
