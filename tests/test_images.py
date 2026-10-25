"""Route-aware image admission (harness/llm/images.py).

The failure this closes: a user attaches a 6 MB screenshot, Anthropic rejects it with a
400 in the middle of a turn. The image is now fitted to the route before it is sent —
downscaled with Pillow when the build has it, refused with a readable sentence when it
does not — and a refused image leaves a TEXT placeholder behind, so the model never
answers about a picture it did not see.
"""

from __future__ import annotations

import base64
import io
import os

import pytest

from harness.llm import images


def _png(width: int, height: int, *, noise: bool = False) -> bytes:
    PIL = pytest.importorskip("PIL.Image")
    if noise:
        # Real entropy: a repeating pattern compresses too well to exercise the byte path.
        img = PIL.frombytes("RGB", (width, height), os.urandom(width * height * 3))
    else:
        img = PIL.new("RGB", (width, height), (200, 30, 30))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _data_url(raw: bytes, mime: str = "image/png") -> str:
    return f"data:{mime};base64," + base64.b64encode(raw).decode("ascii")


def _decoded(url: str) -> bytes:
    return base64.b64decode(url.split(",", 1)[1])


def _dims(url: str) -> tuple[int, int]:
    from PIL import Image

    with Image.open(io.BytesIO(_decoded(url))) as img:
        return img.size


def test_limits_are_per_provider_route():
    assert images.limits_for("anthropic:claude-sonnet-4") == (4_500_000, 8_000)
    assert images.limits_for("bedrock:anthropic.claude") == (4_500_000, 8_000)
    assert images.limits_for("deepseek:deepseek-flash") == (33_000_000, 8_192)
    assert images.limits_for("openai:gpt-5.5") == (19_000_000, None)
    # A bare model id is the OpenAI-compatible default route: the widest entry, never a
    # guess at a limit.
    assert images.limits_for("gpt-5.5") == images.DEFAULT_LIMITS
    assert images.limits_for("") == images.DEFAULT_LIMITS


def test_a_deepseek_name_is_tight_wherever_it_is_served_from():
    """DeepSeek's image rules follow the model, not the route, so the gateway prefix
    (`opencode-go`) hides them from the prefix table — the same gap the Claude-name
    heuristic closes. Found 2026-10-16, session `50a81a82-280`: a valid 1000×11899
    PNG attached mid-turn on `opencode-go:deepseek-v4.1-flash` 400'd on every request
    after it ("unsupported image", naming formats; DeepSeek's documented max is
    8,192 px a side)."""
    for model in (
        "opencode-go:deepseek-v4.1-flash",
        "openrouter:deepseek/deepseek-v4.1-flash",
        "my-gateway:deepseek-v4.1-flash",
        "deepseek-v4.1-flash",  # a bare id: the name still says whose rules apply
    ):
        assert images.limits_for(model) == (33_000_000, 8_192), model
        assert images.is_deepseek_route(model)
    # And the non-DeepSeek routes keep the wide entry.
    assert images.limits_for("opencode-go:glm-5.3") == images.DEFAULT_LIMITS
    assert images.limits_for("vertex:gemini-2.5-pro") == images.DEFAULT_LIMITS
    assert images.limits_for("bedrock:amazon.nova-pro-v1:0") == images.DEFAULT_LIMITS


def test_a_claude_name_is_tight_wherever_it_is_served_from():
    """`vertex` and `bedrock` serve Gemini/Nova AND Claude under one provider, so the
    prefix table alone is wrong for one of the two — the name heuristic is the safety
    net for callers with no provider instance (and for a gateway proxying Claude)."""
    for model in (
        "vertex:claude-sonnet-4@20250514",
        "bedrock:claude/anthropic.claude-sonnet-4-6-v1:0",
        "my-gateway:anthropic/claude-3-5",
    ):
        assert images.limits_for(model) == (4_500_000, 8_000), model
        assert images.is_claude_route(model)
    # And the non-Claude routes keep the wide entry.
    assert images.limits_for("vertex:gemini-2.5-pro") == images.DEFAULT_LIMITS
    assert images.limits_for("bedrock:amazon.nova-pro-v1:0") == images.DEFAULT_LIMITS


class _DeclaringProvider:
    """A provider that declares its own route limits."""

    def __init__(self, limits):
        self._limits = limits

    def image_limits(self, model):
        return self._limits


def test_a_providers_own_declaration_wins_over_the_table():
    provider = _DeclaringProvider((2_000, 1_000))
    assert images.limits_for("anything:at-all", provider=provider) == (2_000, 1_000)
    # A provider whose declaration is malformed is ignored, not trusted.
    for bad in ((None, None), (0, None), "wide", (1, 2, 3)):
        assert (
            images.limits_for("gpt-5.5", provider=_DeclaringProvider(bad))
            == images.DEFAULT_LIMITS
        )
    # A provider that raises falls through to the fallbacks rather than failing the fit.
    class Exploding:
        def image_limits(self, model):
            raise RuntimeError("boom")

    assert images.limits_for("anthropic:claude-x", provider=Exploding()) == (
        4_500_000,
        8_000,
    )


def test_the_real_adapters_declare_per_model():
    from harness.llm.providers.claude import AnthropicProvider
    from harness.llm.providers.bedrock import BedrockProvider
    from harness.llm.providers.vertex import VertexProvider

    # Anthropic's own route is Claude-only: every model it serves is tight.
    anthropic = AnthropicProvider.__new__(AnthropicProvider)
    assert anthropic.image_limits("claude-sonnet-4") == (4_500_000, 8_000)
    assert anthropic.image_limits("whatever-they-name-next") == (4_500_000, 8_000)

    # Bedrock and Vertex serve several vendors: the declaration is per MODEL.
    for cls in (BedrockProvider, VertexProvider):
        provider = cls.__new__(cls)  # the declaration needs no constructed state
        assert provider.image_limits("claude-sonnet-4") == (4_500_000, 8_000), cls
        assert provider.image_limits("gemini-2.5-pro") == images.DEFAULT_LIMITS, cls
        assert provider.image_limits("amazon.nova-pro-v1:0") == images.DEFAULT_LIMITS, cls


def test_an_image_that_fits_is_returned_untouched():
    url = _data_url(_png(64, 64))
    fitted, note = images.fit_image_url(url, model="anthropic:claude-sonnet-4")
    assert fitted == url and note == ""


def test_a_remote_url_is_not_ours_to_resize():
    url = "https://example.com/a.png"
    assert images.fit_image_url(url, model="anthropic:claude-sonnet-4") == (url, "")


def test_a_pixel_oversized_image_is_downscaled():
    url = _data_url(_png(9_000, 150))
    fitted, note = images.fit_image_url(url, model="anthropic:claude-sonnet-4")

    assert fitted is not None and fitted != url
    width, height = _dims(fitted)
    # 2000, not the 8000 Anthropic rejects at: past 20 images in a request it tightens every
    # image to 2000 px, and history resends the earlier ones
    assert width <= 2_000 and height <= 2_000
    assert "resized" in note and "2000" in note


def test_a_full_page_screenshot_is_downscaled_for_a_deepseek_route():
    """The real failure this closes (2026-10-16, session `50a81a82-280`): a tool
    attached a 1000×11899 PNG mid-turn and every request after it on
    `opencode-go:deepseek-v4.1-flash` was refused with a 400 that names image
    formats — the PNG was valid; the rule violated was DeepSeek's 8,192 px
    per-side maximum. The fit downscales it before it is sent."""
    url = _data_url(_png(1_000, 11_899))
    fitted, note = images.fit_image_url(url, model="opencode-go:deepseek-v4.1-flash")

    assert fitted is not None and fitted != url
    width, height = _dims(fitted)
    # The soft side keeps it under the 8,192 px hard line with the same margin
    # every other ceiled route gets.
    assert width <= 2_000 and height <= 2_000
    assert "resized" in note and "2000" in note


def test_a_byte_oversized_image_is_re_encoded_until_it_fits(monkeypatch):
    monkeypatch.setitem(images.IMAGE_LIMITS, "anthropic", (50_000, None))
    url = _data_url(_png(500, 500, noise=True))

    fitted, note = images.fit_image_url(url, model="anthropic:claude-sonnet-4")

    assert fitted is not None
    raw = _decoded(fitted)
    assert len(raw) <= 50_000
    assert fitted.startswith("data:image/jpeg;base64,")  # PNG gave way to JPEG
    assert "resized" in note


def test_without_an_encoder_an_oversized_image_is_refused_with_a_reason(monkeypatch):
    monkeypatch.setattr(images, "_PIL_AVAILABLE", False)
    monkeypatch.setitem(images.IMAGE_LIMITS, "anthropic", (100, None))
    url = _data_url(_png(64, 64))

    fitted, note = images.fit_image_url(url, model="anthropic:claude-sonnet-4")

    assert fitted is None
    assert "no image encoder" in note and "resize or crop" in note


def test_fit_attach_parts_replaces_a_refused_image_with_text(monkeypatch):
    monkeypatch.setattr(images, "_PIL_AVAILABLE", False)
    monkeypatch.setitem(images.IMAGE_LIMITS, "anthropic", (100, None))
    parts = [
        {"type": "text", "text": "look at this"},
        {"type": "image_url", "image_url": {"url": _data_url(_png(64, 64))}},
    ]

    out, notes = images.fit_attach_parts(parts, model="anthropic:claude-sonnet-4")

    assert out[0] == parts[0]
    assert out[1]["type"] == "text" and "not sent" in out[1]["text"]
    assert not any(p.get("type") == "image_url" for p in out)
    assert notes and "no image encoder" in notes[0]


def test_fit_attach_parts_keeps_a_resized_image_and_says_so():
    parts = [{"type": "image_url", "image_url": {"url": _data_url(_png(9_000, 150))}}]

    out, notes = images.fit_attach_parts(parts, model="anthropic:claude-sonnet-4")

    assert out[0]["type"] == "image_url"  # position preserved
    assert out[0]["image_url"]["url"] != parts[0]["image_url"]["url"]
    assert out[1]["type"] == "text" and "resized" in out[1]["text"]
    assert notes and "resized" in notes[0]


def test_build_user_content_fits_images_for_the_session_model():
    from harness.llm.attachments import build_user_content

    content = build_user_content(
        "what is wrong here?",
        [{"kind": "image", "data_url": _data_url(_png(9_000, 150))}],
        model="anthropic:claude-sonnet-4",
    )
    assert isinstance(content, list)
    kinds = [p["type"] for p in content]
    assert kinds == ["text", "image_url", "text"]  # text + fitted image + note
    assert _dims(content[1]["image_url"]["url"])[0] <= 8_000


def test_read_image_attachments_are_fitted_through_the_engine(monkeypatch):
    """The `_attach` lift in `_record_result` is the other birth point for image parts."""
    from harness.loop import TurnEngine
    from harness.safety.permissions import PermissionEngine
    from harness.llm.providers import ModelCapabilities, ProviderClient, ToolCall
    from harness.llm.providers.base import AssistantTurn
    from harness.tools import ToolRegistry

    class Provider(ProviderClient):
        def complete(self, *, model, messages, tools=None, **settings):
            return AssistantTurn(text="ok", finish_reason="stop")

        def capabilities(self, model):
            return ModelCapabilities(vision=True)

        def image_limits(self, model):
            # A route whose declaration is tighter than any table entry (as a real
            # adapter's would be): the declaration must win.
            return (100, None)

    monkeypatch.setattr(images, "_PIL_AVAILABLE", False)
    engine = TurnEngine(
        provider=Provider(),
        registry=ToolRegistry(),
        permissions=PermissionEngine(workspace_root="."),
        model="anthropic:claude-sonnet-4",
        messages=[{"role": "system", "content": "s"}],
    )
    call = ToolCall(id="c1", name="read_image", arguments={"path": "a.png"})
    engine._record_result(
        call,
        {
            "shown": "a.png is attached below",
            "_attach": [
                {
                    "type": "image_url",
                    "image_url": {"url": _data_url(_png(64, 64))},
                }
            ],
        },
        "ok",
    )

    attachment = engine.messages[-1]
    assert attachment.get("role") == "user"  # the attachment message
    assert attachment["content"][0]["type"] == "text"
    assert "not sent" in attachment["content"][0]["text"]


def test_the_engine_route_sees_the_deepseek_ceiling_through_the_router():
    """The engine only asks through the router, and the router asks the client. A base
    adapter that answered the widest entry hid the name heuristics, so the DeepSeek fix
    above never reached a real request (session `50a81a82-280`, 2026-10-16)."""
    from harness.llm.providers.opencode_go import OpenCodeGoProvider

    gateway = OpenCodeGoProvider.__new__(OpenCodeGoProvider)  # the declaration needs no state
    assert gateway.image_limits("deepseek-v4.1-flash") == (33_000_000, 8_192)
    assert gateway.image_limits("opencode-go:deepseek-v4.1-flash") == (33_000_000, 8_192)
    assert images.limits_for("opencode-go:deepseek-v4.1-flash", provider=gateway) == (
        33_000_000,
        8_192,
    )
    # A Claude model behind a gateway gets Claude's limits on the same path.
    assert gateway.image_limits("claude-sonnet-4") == (4_500_000, 8_000)
    assert gateway.image_limits("glm-5.3") == images.DEFAULT_LIMITS


def _vision_engine(model, provider_limits=None):
    from harness.loop import TurnEngine
    from harness.safety.permissions import PermissionEngine
    from harness.llm.providers import ModelCapabilities, ProviderClient
    from harness.llm.providers.base import AssistantTurn
    from harness.tools import ToolRegistry

    class Provider(ProviderClient):
        def complete(self, *, model, messages, tools=None, **settings):
            return AssistantTurn(text="ok", finish_reason="stop")

        def capabilities(self, model):
            return ModelCapabilities(vision=True)

    return TurnEngine(
        provider=Provider(),
        registry=ToolRegistry(),
        permissions=PermissionEngine(workspace_root="."),
        model=model,
        messages=[{"role": "system", "content": "s"}],
    )


def test_an_oversized_image_already_in_the_history_is_fitted_on_send():
    """The session that broke kept failing: the 1000×11899 screenshot was saved in its
    history and resent every request. Fitting again outbound heals it (and a switch onto
    a tighter model); the saved history keeps the original."""
    tall = _data_url(_png(1_000, 11_899))
    engine = _vision_engine("opencode-go:deepseek-v4.1-flash")
    engine.messages.append(
        {"role": "user", "content": [{"type": "text", "text": "page"}, {"type": "image_url", "image_url": {"url": tall}}]}
    )

    first = engine._outbound_messages()
    sent = first[-1]["content"]
    assert [p["type"] for p in sent] == ["text", "image_url", "text"]
    assert max(_dims(sent[1]["image_url"]["url"])) <= 2_000
    assert "resized" in sent[2]["text"]
    # Outbound only: the transcript is untouched.
    assert engine.messages[-1]["content"][1]["image_url"]["url"] == tall
    # The same bytes every request, so a prefix cache keeps holding.
    assert engine._outbound_messages()[-1]["content"] == sent

    # A route with no pixel ceiling sends the original as it is.
    wide = _vision_engine("openai:gpt-5.5")
    wide.messages.append(engine.messages[-1])
    assert wide._outbound_messages()[-1]["content"][1]["image_url"]["url"] == tall


def test_an_image_that_fits_leaves_the_outbound_message_as_it_was():
    engine = _vision_engine("opencode-go:deepseek-v4.1-flash")
    message = {"role": "user", "content": [{"type": "image_url", "image_url": {"url": _data_url(_png(64, 64))}}]}
    engine.messages.append(message)
    out = engine._outbound_messages()
    assert out[-1]["content"] == message["content"]
