"""Audio and video attachments: sent where model AND route can take them, named everywhere else.

Two facts decide, and neither is a provider name:

  - the MODEL takes the kind: `capabilities(...).audio/.video`, from the provider's catalogue
    (models.dev, OpenRouter) or the curated matrix. An unlisted model is not guessed at;
  - the ROUTE can carry the file: `ProviderClient.media_carry`, what the adapter can encode.
    Gemini takes any audio/video MIME type inline; the chat-completions wire takes WAV/MP3 audio and
    MP4/MPEG/MOV/WebM video (which also covers OpenRouter and compatible servers); Anthropic, the
    Responses wire and Bedrock take none.

The failure to design out is silence: a recording that reaches a model as nothing reads as "the user
attached nothing". And the decision is per outbound call, never written to the history, so switching
model mid-session re-decides.
"""

from __future__ import annotations

import base64

from harness.llm import media_support
from harness.llm import pdf_support
from harness.llm.attachments import build_user_content, content_to_text
from harness.context.compaction.tokens import PDF_TOKEN_ESTIMATE, estimate_tokens
from harness.llm.providers import catalog as catalog_module
from harness.llm.providers.claude import AnthropicProvider
from harness.llm.providers.base import ModelCapabilities
from harness.llm.providers.capabilities import capabilities_for
from harness.llm.providers.gemini import GeminiProvider
from harness.llm.providers.gemini.convert import _user_parts
from harness.llm.providers.chat import OpenAIProvider
from harness.llm.providers.chat.media import carry_media as _carry_media
from harness.llm.providers.opencode_go import OpenCodeGoProvider
from harness.llm.providers.vertex import VertexProvider

HEARS = ModelCapabilities(vision=True, pdf=True, audio=True, video=True)
DEAF = ModelCapabilities(vision=True, pdf=True)
ANY = lambda mime: True  # noqa: E731  a route that carries everything
NONE = lambda mime: False  # noqa: E731


def _part(name="call.mp3", mime="audio/mpeg", body=b"ID3\x04\x00fake", saved="C:/s/call.mp3"):
    url = f"data:{mime};base64,{base64.b64encode(body).decode()}"
    part = {"type": "file", "file": {"filename": name, "file_data": url}}
    if saved:
        part["saved_at"] = saved
    return part


def _pdf():
    return {"type": "file", "file": {"filename": "a.pdf", "file_data": "data:application/pdf;base64,JVBERi0="}}


def _user(*parts):
    return {"role": "user", "content": [{"type": "text", "text": "listen"}, *parts]}


def _texts(message):
    return " ".join(p["text"] for p in message["content"] if p["type"] == "text")


# --------------------------------------------------------------- the decision


def test_a_recording_is_sent_when_the_model_and_the_route_both_take_it():
    out = media_support.adapt_messages([_user(_part())], HEARS, ANY)
    assert out[0]["content"][1]["type"] == "file"


def test_a_model_that_does_not_take_audio_gets_a_sentence_with_the_path():
    out = media_support.adapt_messages(
        [_user(_part(), _part("clip.mp4", "video/mp4", saved="C:/s/clip.mp4"))], DEAF, ANY
    )
    text = _texts(out[0])
    assert "Attached audio: call.mp3" in text and "C:/s/call.mp3" in text and "does not take audio" in text
    assert "Attached video: clip.mp4" in text and "does not take video" in text
    assert not [p for p in out[0]["content"] if p["type"] == "file"]


def test_a_route_with_no_way_to_carry_it_says_why():
    # the model hears, but it is reached over an API that has no audio input (Anthropic Messages,
    # the Responses wire, Bedrock): named, with the reason, never sent to fail
    out = media_support.adapt_messages([_user(_part("memo.m4a", "audio/mp4"))], HEARS, NONE)
    text = _texts(out[0])
    assert "takes audio, but the connection it is reached over has no way to carry it" in text
    assert "memo.m4a" in text and "C:/s/call.mp3" in text


def test_no_route_answer_means_nothing_is_sent():
    out = media_support.adapt_messages([_user(_part())], HEARS, None)
    assert "Attached audio" in _texts(out[0])


def test_a_recording_with_no_saved_copy_still_says_it_was_there():
    out = media_support.adapt_messages([_user(_part(saved=None))], DEAF, ANY)
    assert "Attached audio: call.mp3" in out[0]["content"][1]["text"]


def test_the_history_is_never_rewritten():
    history = [_user(_part())]
    media_support.adapt_messages(history, DEAF, NONE)
    assert history[0]["content"][1]["type"] == "file"


def test_only_the_newest_recordings_stay_inline():
    history = [_user(_part(f"r{i}.mp3", saved=f"C:/s/r{i}.mp3")) for i in range(4)]
    out = media_support.adapt_messages(history, HEARS, ANY)
    assert [m["content"][1]["type"] for m in out] == ["text", "text", "file", "file"]
    assert "earlier in the conversation" in out[0]["content"][1]["text"] and "C:/s/r0.mp3" in out[0]["content"][1]["text"]


def test_a_pdf_and_a_recording_are_not_mistaken_for_each_other():
    message = _user(_pdf(), _part())
    adapted = pdf_support.adapt_content(message["content"], ModelCapabilities(pdf=False))
    assert any(p.get("type") == "file" and media_support.media_mime(p) for p in adapted)
    out = media_support.adapt_messages([message], DEAF, NONE)
    assert out[0]["content"][1] == _pdf()


# --------------------------------------------- what the model takes: facts, not names


def test_the_curated_gemini_rows_take_audio_and_video_and_others_do_not():
    for model in ("gemini:gemini-3.6-flash", "vertex:gemini/gemini-3.7-flash"):
        caps = capabilities_for(model)
        assert caps.audio and caps.video, model
    for model in ("gpt-5.5", "anthropic:claude-fable-5"):
        caps = capabilities_for(model)
        assert not caps.audio and not caps.video, model


def test_an_unlisted_model_is_not_guessed_at_from_its_name():
    caps = capabilities_for("gemini:gemini-9-not-in-any-list")
    assert not caps.audio and not caps.video


def test_a_catalogue_that_lists_audio_makes_any_model_hear(monkeypatch):
    """models.dev states input modalities per model; that is the fact, whoever serves it."""

    class Dev:
        def get(self, model):
            return {
                "qwen:omni-x": {"input": ["text", "image", "audio"]},
                "together:some-vlm": {"input": ["text", "image", "video"]},
                "zai:plain": {"input": ["text"]},
            }.get(model)

    monkeypatch.setattr(catalog_module, "models_dev", lambda: Dev())
    assert capabilities_for("qwen:omni-x").audio and not capabilities_for("qwen:omni-x").video
    assert capabilities_for("together:some-vlm").video and not capabilities_for("together:some-vlm").audio
    assert not capabilities_for("zai:plain").audio


def test_the_picker_lists_audio_and_video_only_where_the_app_can_send_them(monkeypatch):
    """A catalogue describes the MODEL. Bedrock, Ark (Responses wire), stock OpenAI (Responses wire),
    Claude on Vertex and OpenCode Go's Messages wire have no way to carry the part here, so listing
    "audio" for them would be a promise the app breaks. Each provider's builder says whether its client
    can carry media; the list the picker shows and the sender acts on leaves the input out otherwise."""
    from harness.llm.providers.modalities import input_modalities

    every = {"input": ["text", "image", "audio", "video"]}

    class Dev:
        def get(self, model):
            if model == "opencode-go:via-messages":
                return {**every, "wire": "messages"}
            if model == "opencode-go:via-chat":
                return {**every, "wire": "chat"}
            return every if model.split(":", 1)[0] in {
                "qwen", "bedrock", "ark", "openai", "vertex", "openrouter", "gemini", "opencode-go",
            } else None

    monkeypatch.setattr(catalog_module, "models_dev", lambda: Dev())

    def listed(model):
        return [m for m in input_modalities(model) if m in ("audio", "video")]

    for model in (
        "qwen:omni-x", "openrouter:any/model", "gemini:gemini-x", "opencode-go:via-chat",
        "vertex:gemini-3.7-flash", "vertex:gemini/gemini-3.7-flash",
    ):
        assert listed(model) == ["audio", "video"], model
    for model in (
        "bedrock:amazon.nova-pro-v1:0", "ark:doubao-x", "openai:gpt-audio-x",
        "opencode-go:via-messages", "vertex:claude/claude-sonnet-4-6",
    ):
        assert listed(model) == [], model
    assert not capabilities_for("bedrock:amazon.nova-pro-v1:0").video  # and the sender agrees


def test_openrouter_modalities_feed_audio_and_video_but_not_the_pdf_decision(monkeypatch):
    class Router:
        def get(self, model):
            # OpenRouter's own words: "file" is its name for documents, not a statement about PDFs
            return {"vision": True, "media": ["audio"]} if model == "openrouter:x/listener" else None

    monkeypatch.setattr(catalog_module, "catalog", lambda: Router())
    caps = capabilities_for("openrouter:x/listener")
    assert caps.audio and not caps.video
    assert caps.pdf == capabilities_for("openrouter:x/unlisted").pdf


# ----------------------------------------------------------- what the route carries


def test_the_picker_shows_exactly_what_the_sender_acts_on(monkeypatch):
    """The model picker's "Inputs" row and the decision to send a recording read one list. They were
    two lookups that could disagree: a Gemini row the matrix vouches for showed "text, image" until
    models.dev was cached, yet was sent audio."""
    from harness.llm.providers.matrix import model_details

    class Dev:
        def get(self, model):
            return {"opencode-go:mimo": {"input": ["text", "image", "audio", "video"]}}.get(model)

    monkeypatch.setattr(catalog_module, "models_dev", lambda: Dev())
    for model in (
        "opencode-go:mimo", "gemini:gemini-3.6-flash", "vertex:gemini/gemini-3.7-flash", "gpt-5.5",
        "anthropic:claude-fable-5", "zai:glm-5.2", "gemini:gemini-9-not-in-any-list",
        "bedrock:amazon.nova-pro-v1:0",
    ):
        shown = model_details(model)["inputs"]
        caps = capabilities_for(model)
        assert ("audio" in shown) == caps.audio, model
        assert ("video" in shown) == caps.video, model
    assert "audio" in model_details("gemini:gemini-3.6-flash")["inputs"]  # listed even with no catalogue cache


def test_each_route_declares_what_it_can_put_on_the_wire():
    gemini = GeminiProvider.__new__(GeminiProvider)
    assert gemini.media_carry("gemini-3.6-flash", "audio/aac") and gemini.media_carry("gemini-3.6-flash", "video/avi")
    assert not gemini.media_carry("gemini-3.6-flash", "application/pdf")

    # the chat wire spells any audio/video the model lists; the picker's "Inputs" is the gate, so an
    # iPhone's M4A or a FLAC is sent, not refused for a format list of ours
    chat = OpenAIProvider.__new__(OpenAIProvider)
    for mime in ("audio/wav", "audio/mpeg", "audio/mp4", "audio/flac", "video/mp4", "video/x-msvideo"):
        assert chat.media_carry("gpt-audio-x", mime), mime
    assert not chat.media_carry("gpt-audio-x", "application/pdf")

    assert not AnthropicProvider.__new__(AnthropicProvider).media_carry("claude-x", "audio/mpeg")

    vertex = VertexProvider.__new__(VertexProvider)
    assert vertex.media_carry("vertex:gemini/gemini-3.7-flash", "video/mp4")
    assert vertex.media_carry("gemini/gemini-3.7-flash", "audio/wav")
    assert not vertex.media_carry("vertex:claude/claude-sonnet-4-6", "audio/wav")


def test_a_gateway_that_serves_several_wires_answers_for_the_wire_of_the_model(monkeypatch):
    """OpenCode Go serves MiMo (which lists audio and video) over chat completions and some families
    over Anthropic Messages. It used to inherit the default, so MiMo was deaf whatever its catalogue said."""
    gateway = OpenCodeGoProvider(api_key="k", base_url="https://gateway.example/v1")
    monkeypatch.setattr(
        gateway,
        "_wire_for",
        lambda model: {"claude-like": "messages", "gemini-4-argon-contributor": "gemini"}.get(model, "chat"),
    )
    assert gateway.media_carry("mimo-v2.6-pro", "audio/mpeg") and gateway.media_carry("mimo-v2.6-pro", "video/mp4")
    assert gateway.media_carry("mimo-v2.6-pro", "audio/flac")
    assert gateway.media_carry("gemini-4-argon-contributor", "audio/mpeg")
    assert not gateway.media_carry("claude-like", "audio/mpeg")


def test_the_chat_wire_converts_a_recording_to_its_own_parts():
    messages = [
        _user(_part(), _part("clip.mp4", "video/mp4", body=b"\x00\x00\x00\x18ftypmp42"), _pdf())
    ]
    parts = _carry_media(messages)[0]["content"]
    assert parts[1] == {
        "type": "input_audio",
        "input_audio": {"data": base64.b64encode(b"ID3\x04\x00fake").decode(), "format": "mp3"},
    }
    m4a = _carry_media([_user(_part("memo.m4a", "audio/mp4"), _part("x.flac", "audio/x-flac"))])[0]["content"]
    assert [p["input_audio"]["format"] for p in m4a[1:]] == ["m4a", "flac"]
    assert parts[2]["type"] == "video_url" and parts[2]["video_url"]["url"].startswith("data:video/mp4;base64,")
    assert parts[3] == _pdf()  # a PDF shares the `file` part and is left alone
    assert "saved_at" not in parts[1] and "saved_at" not in parts[2]


def test_gemini_receives_audio_and_video_as_inline_data():
    parts = _user_parts([_part(), _part("clip.mp4", "video/mp4", body=b"\x00\x00\x00\x18ftypmp42")])
    assert parts[0]["inline_data"]["mime_type"] == "audio/mpeg"
    assert parts[1]["inline_data"]["mime_type"] == "video/mp4"
    assert base64.b64decode(parts[0]["inline_data"]["data"]) == b"ID3\x04\x00fake"


# ------------------------------------------------------------- attaching and pricing


def test_an_uploaded_recording_becomes_a_media_part_with_its_saved_path(tmp_path):
    raw = b"RIFF....WAVEfmt "
    url = f"data:audio/wav;base64,{base64.b64encode(raw).decode()}"
    content = build_user_content(
        "transcribe",
        [{"kind": "file", "name": "memo.wav", "mime": "audio/wav", "data_url": url}],
        save_file=lambda name, data: str(tmp_path / name),
    )
    part = content[-1]
    assert part["type"] == "file" and media_support.media_mime(part) == "audio/wav"
    assert part["saved_at"] == str(tmp_path / "memo.wav")
    assert content_to_text(content) == "transcribe [audio]"


def test_a_recording_costs_less_than_a_document_and_is_capped():
    small = estimate_tokens([_user(_part(body=b"x" * 200_000))])
    huge = estimate_tokens([_user(_part(body=b"x" * 15_000_000))])
    pdf = estimate_tokens([_user(_pdf())])
    assert small < pdf + PDF_TOKEN_ESTIMATE
    assert huge <= 61_000  # capped: one recording is never priced as a whole conversation
    assert huge > small
