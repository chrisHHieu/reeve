"""Fit an image to the route that will receive it.

Providers enforce their own image limits, and they enforce them as a 400 in the middle of
a turn — after the person attached, after the model round-trips were paid for. Anthropic
(and Claude on Bedrock) rejects anything over ~5 MB or 8000 px on a side; DeepSeek —
directly or behind a gateway — anything over 32 MiB or 8,192 px on a side; OpenAI and
Gemini accept far more. An image is resolved into a route-chosen request version
(`packages/attachment`: normalize, then derive dimensions from the route's pixel and byte
targets); this is that rule at the granularity this harness needs.

Two commitments:

**Never send bytes the provider is known to reject.** Over the limit, the image is
downscaled and re-encoded with Pillow when the build has it. Without an encoder the
image is refused with a sentence rather than sent to fail.

**A refused image is TEXT, not an omission.** The model must know an image was attached
and why it cannot see it, or it answers confidently about a picture it never saw. That
is the same rule `_outbound_messages` already follows for models without vision.

Fitting runs on the two choke points where image parts are born — user attachments
(`attachments.build_user_content`) and tool-produced attachments (`read_image`'s
`_attach`, lifted in `engine._record_result`) — so a provider 400 from an oversized image
is not reachable from either path.
"""

from __future__ import annotations

import base64
import io
import re
from typing import Any, Optional

#: `(max decoded bytes, max pixels on the longest side)` for single-vendor provider
#: prefixes. The table is a LAST resort: a provider instance that declares its own limits
#: wins (see `limits_for`), the Claude-name heuristic covers multi-vendor routes and
#: no-instance callers, and only then does the prefix decide. Anthropic's published
#: limits are 5 MB / 8000 px, so the entry fits under both with room for the base64
#: envelope. Multi-vendor prefixes (`bedrock` serves Claude, Nova, Llama, Mistral) are
#: deliberately absent — one entry for them would be wrong for one of the vendors, and
#: the heuristic plus the adapter declaration cover them. An unknown provider gets the
#: widest entry rather than a guess at a limit.
IMAGE_LIMITS: dict[str, tuple[int, Optional[int]]] = {
    "anthropic": (4_500_000, 8_000),
    # DeepSeek's published image limits: 32 MiB per inline image and 8,192 px per side
    # (dropping to 4,096 px at 15+ images in one request). The bytes are kept under with
    # room; the pixel ceiling rides SOFT_SIDE below like Anthropic's does.
    "deepseek": (33_000_000, 8_192),
    "openai": (19_000_000, None),
    "google": (19_000_000, None),
    "vertex": (19_000_000, None),
    "ollama": (19_000_000, None),
}
DEFAULT_LIMITS: tuple[int, Optional[int]] = (19_000_000, None)

#: The formats every provider reads. Anthropic accepts exactly these four, and they are the
#: intersection the others share; anything else (HEIC from a phone, BMP, TIFF, ICO, AVIF) is
#: converted to PNG rather than sent to fail with a 400 mid-turn.
SUPPORTED_MIMES = ("image/jpeg", "image/png", "image/gif", "image/webp")

#: Long side an image is brought down to on a route that HAS a pixel ceiling. Anthropic tightens
#: the limit to 2000 px for every image in a request once it carries more than 20, counting the
#: ones resent from earlier turns, so a session of Retina screenshots would start failing on the
#: 21st; its own guidance is to keep each image under 2000 px. Only routes with a ceiling get it
#: (the others take far more and have no such rule), and 2000 is under both the 2576 px the newest
#: Claude models read and the 1568 px the rest do, so no fidelity the model could use is lost.
SOFT_SIDE = 2000

_DATA_URL = re.compile(
    r"^data:(image/[a-z0-9.+-]+);base64,(.+)$", re.IGNORECASE | re.DOTALL
)

#: Resize steps before giving up. Six 0.82 steps take a 4×-too-large image under the
#: limit; past that the image is pathological and the refusal is the honest answer.
_MAX_ATTEMPTS = 6
_TARGET_SCALE = 0.82
_JPEG_QUALITY = 82

#: None until the first probe; the probe is one import attempt against `sys.modules`.
_PIL_AVAILABLE: Optional[bool] = None


def pillow_available() -> bool:
    """Whether this build can resize images. Cached: the probe is an import, not I/O."""
    global _PIL_AVAILABLE
    if _PIL_AVAILABLE is None:
        try:
            from PIL import Image  # noqa: F401

            _PIL_AVAILABLE = True
        except Exception:
            _PIL_AVAILABLE = False
    return _PIL_AVAILABLE


def provider_prefix(model: str) -> str:
    return (model or "").split(":", 1)[0].strip().lower()


def is_claude_route(model: str) -> bool:
    """Whether the model id names a Claude route, wherever it is served from.

    `vertex` and `bedrock` each serve Claude *and* models with far wider image limits
    (Gemini, Nova, Llama), so the provider prefix alone cannot decide. The name can —
    and it also catches a Claude model behind a generic OpenAI-compatible gateway.
    """
    bare = (model or "").split(":", 1)[-1].lower()
    return "claude" in bare or "anthropic" in bare


def is_deepseek_route(model: str) -> bool:
    """Whether the model id names a DeepSeek route, wherever it is served from.

    DeepSeek's image rules follow the model, not the route, and the gateway prefix
    (`opencode-go:deepseek-v4.1-flash`) hides them from the prefix table — the same
    gap `is_claude_route` closes for Claude behind a gateway. Found 2026-10-16,
    session `50a81a82-280`: a tool attached a valid 1000×11899 PNG mid-turn, and
    every request after it was refused with a 400 naming image *formats* while the
    rule actually violated was DeepSeek's documented 8,192 px per-side maximum.
    """
    bare = (model or "").split(":", 1)[-1].lower()
    return "deepseek" in bare


def limits_for(
    model: str, *, provider: Any = None
) -> tuple[int, Optional[int]]:
    """The route's `(max decoded bytes, max long side)`.

    The provider's own declaration wins when it has one (the adapter owns
    its route's targets, so two models behind one provider can differ). The Claude-name
    heuristic is the safety net for callers with no provider instance and for gateways
    that serve Claude under a different prefix. The prefix table is the last resort, and
    an unknown route gets the widest entry rather than a guess at a limit.
    """
    if provider is not None:
        declared = getattr(provider, "image_limits", None)
        if callable(declared):
            try:
                limits = declared(model)
            except Exception:
                limits = None
            if (
                isinstance(limits, tuple)
                and len(limits) == 2
                and isinstance(limits[0], int)
                and limits[0] > 0
                and (limits[1] is None or isinstance(limits[1], int))
            ):
                return limits
    if is_claude_route(model):
        return IMAGE_LIMITS["anthropic"]
    if is_deepseek_route(model):
        return IMAGE_LIMITS["deepseek"]
    return IMAGE_LIMITS.get(provider_prefix(model), DEFAULT_LIMITS)


def _dimensions(raw: bytes) -> Optional[tuple[int, int]]:
    """(width, height) from the header alone, or None when it cannot be read."""
    try:
        from PIL import Image

        with Image.open(io.BytesIO(raw)) as probe:
            return probe.size
    except Exception:
        return None


def fit_image_url(
    url: str, *, model: str, provider: Any = None
) -> tuple[Optional[str], str]:
    """`(a data URL that fits the route, note)`.

    Unchanged and `""` for a URL that already fits, or that is not a data URL (a remote
    URL is not ours to resize). When it does not fit, the returned URL is downscaled and
    re-encoded and the note says what changed; when this build cannot re-encode, the URL
    is None and the note says why — callers replace it with a text placeholder instead of
    sending an image the provider will reject.
    """
    match = _DATA_URL.match(url or "")
    if not match:
        return url, ""
    mime, payload = match.group(1).lower(), match.group(2)
    max_bytes, max_side = limits_for(model, provider=provider)
    if max_side is not None:
        max_side = min(max_side, SOFT_SIDE)
    try:
        raw = base64.b64decode(payload, validate=False)
    except Exception:
        return None, "the image data could not be decoded"
    converted = ""
    if mime not in SUPPORTED_MIMES:
        result = _convert(raw) if pillow_available() else None
        if result is None:
            return None, (
                f"{mime.split('/')[-1].upper()} is not an image format models read, and this "
                "build could not convert it — save it as PNG or JPEG"
            )
        converted = f"converted from {mime.split('/')[-1].upper()} to PNG"
        mime, raw = result
    if len(raw) <= max_bytes:
        # Bytes fit. The pixel ceiling is the only other way in — and checking it needs
        # Pillow, so a route that declares no pixel ceiling (or a build without PIL)
        # sends the image as-is.
        size = _dimensions(raw) if (max_side is not None and pillow_available()) else None
        if size is None or max(size) <= max_side:
            if not converted:
                return url, ""
            return f"data:{mime};base64," + base64.b64encode(raw).decode("ascii"), f"the image was {converted}"
    if not pillow_available():
        return None, (
            f"the image is {len(raw) // 1024} KB and this model accepts at most "
            f"{max_bytes // 1024} KB, and this build has no image encoder to resize it "
            "— resize or crop it first"
        )
    fitted = _downscale(raw, mime=mime, max_bytes=max_bytes, max_side=max_side)
    if fitted is None:
        return None, (
            f"the image could not be resized under this model's "
            f"{max_bytes // 1024} KB limit — resize or crop it first"
        )
    out_mime, data, width, height = fitted
    note = (
        f"the image was resized to {width}×{height} "
        f"({out_mime.split('/')[-1].upper()}) to fit this model's limits"
    )
    return f"data:{out_mime};base64," + base64.b64encode(data).decode("ascii"), note


def _convert(raw: bytes) -> Optional[tuple[str, bytes]]:
    """The first frame of any image Pillow can open, as PNG (lossless), or None."""
    from PIL import Image

    try:
        img = Image.open(io.BytesIO(raw))
        img.load()
    except Exception:
        return None
    if img.mode not in ("RGB", "RGBA", "L"):
        img = img.convert("RGBA" if "A" in img.getbands() else "RGB")
    return _encode(img, "PNG")


def _downscale(
    raw: bytes, *, mime: str, max_bytes: int, max_side: Optional[int]
) -> Optional[tuple[str, bytes, int, int]]:
    from PIL import Image

    try:
        img = Image.open(io.BytesIO(raw))
        img.load()
    except Exception:
        return None
    if img.mode not in ("RGB", "RGBA", "L"):
        img = img.convert("RGBA" if "A" in img.getbands() else "RGB")
    if max_side is not None:
        img.thumbnail((max_side, max_side), Image.LANCZOS)
    # Lossless first (screenshots and diagrams stay legible); JPEG is the escape for a
    # photographic image whose PNG is still over the byte cap after downscaling.
    fmt = "PNG" if mime in ("image/png", "image/gif", "image/webp") else "JPEG"
    for attempt in range(_MAX_ATTEMPTS):
        out_mime, data = _encode(img, fmt)
        if len(data) <= max_bytes:
            return out_mime, data, img.width, img.height
        if fmt == "PNG":
            fmt = "JPEG"
            continue
        if attempt + 1 < _MAX_ATTEMPTS:
            img = img.resize(
                (
                    max(1, int(img.width * _TARGET_SCALE)),
                    max(1, int(img.height * _TARGET_SCALE)),
                ),
                Image.LANCZOS,
            )
    return None


def _encode(img: Any, fmt: str) -> tuple[str, bytes]:
    buf = io.BytesIO()
    if fmt == "JPEG":
        if img.mode == "RGBA":
            img = img.convert("RGB")
        img.save(buf, format="JPEG", quality=_JPEG_QUALITY, optimize=True)
        return "image/jpeg", buf.getvalue()
    img.save(buf, format="PNG", optimize=True)
    return "image/png", buf.getvalue()


def fit_attach_parts(
    parts: list[Any], *, model: str, provider: Any = None
) -> tuple[list[Any], list[str]]:
    """Fit every image part in one attachment list; return `(parts, notes)`.

    A part that cannot fit becomes a text placeholder in place (the model must know an
    image was attached and why it cannot see it). A part that was resized keeps its
    position and gains a short note after it, so exact pixels are never silently
    replaced by approximate ones.
    """
    out: list[Any] = []
    notes: list[str] = []
    for part in parts:
        if isinstance(part, dict) and part.get("type") == "image_url":
            holder = part.get("image_url")
            url = holder.get("url") if isinstance(holder, dict) else None
            if isinstance(url, str) and url.startswith("data:"):
                fitted, note = fit_image_url(url, model=model, provider=provider)
                if fitted is None:
                    notes.append(note)
                    out.append(
                        {"type": "text", "text": f"[image attachment — not sent: {note}]"}
                    )
                    continue
                if note:
                    notes.append(note)
                    out.append(
                        {
                            "type": "image_url",
                            "image_url": {**holder, "url": fitted},
                        }
                    )
                    out.append({"type": "text", "text": f"[{note}]"})
                    continue
        out.append(part)
    return out, notes


__all__ = [
    "DEFAULT_LIMITS",
    "IMAGE_LIMITS",
    "fit_attach_parts",
    "fit_image_url",
    "is_claude_route",
    "is_deepseek_route",
    "limits_for",
    "pillow_available",
    "provider_prefix",
]
