"""Pictures of a page in the harness's browser: the screenshots a check hands the
model when something is out of bounds, and the small one each version of a design keeps."""

from __future__ import annotations

import base64
from typing import Any

#: How tall a screenshot may be, in CSS pixels. A long page is shot down to here; the model reads
#: the top of it, where a first draft's problems are, and the facts cover the rest.
SHOT_MAX_HEIGHT = 2400
#: Screenshots are scaled so they are at most this wide, which is about what a model reads well.
SHOT_MAX_WIDTH = 720
#: The window's height while measuring.
VIEW_HEIGHT = 900


async def shoot(cdp: Any, session: str, width: int) -> bytes:
    metrics = await cdp.send("Page.getLayoutMetrics", session=session)
    size = metrics.get("cssContentSize") or metrics.get("contentSize") or {}
    height = max(VIEW_HEIGHT, min(int(size.get("height") or VIEW_HEIGHT), SHOT_MAX_HEIGHT))
    scale = min(1.0, SHOT_MAX_WIDTH / float(width))
    shot = await cdp.send(
        "Page.captureScreenshot",
        {
            "format": "png",
            "captureBeyondViewport": True,
            "clip": {"x": 0, "y": 0, "width": width, "height": height, "scale": scale},
        },
        session=session,
    )
    return base64.b64decode(shot.get("data") or "")


#: The width of a version's thumbnail, and the height of page it shows.
THUMB_WIDTH = 240
_THUMB_PAGE_HEIGHT = 1200


async def thumb(cdp: Any, session: str, width: int) -> bytes:
    """The top of the page, small and compressed: a few kilobytes a version."""
    metrics = await cdp.send("Page.getLayoutMetrics", session=session)
    size = metrics.get("cssContentSize") or metrics.get("contentSize") or {}
    height = max(VIEW_HEIGHT, min(int(size.get("height") or VIEW_HEIGHT), _THUMB_PAGE_HEIGHT))
    shot = await cdp.send(
        "Page.captureScreenshot",
        {
            "format": "jpeg",
            "quality": 60,
            "captureBeyondViewport": True,
            "clip": {"x": 0, "y": 0, "width": width, "height": height, "scale": THUMB_WIDTH / float(width)},
        },
        session=session,
    )
    return base64.b64decode(shot.get("data") or "")


#: The largest area a marked region is shot at, in CSS pixels each way.
REGION_MAX = 2400


async def region(cdp: Any, session: str, clip: dict) -> bytes:
    """One area of the page at its own size (a mark the person drew in the preview),
    bounded so a mark the size of the page stays a picture a model can read."""
    x = max(0, int(clip.get("x") or 0))
    y = max(0, int(clip.get("y") or 0))
    w = max(1, min(int(clip.get("w") or 1), REGION_MAX))
    h = max(1, min(int(clip.get("h") or 1), REGION_MAX))
    shot = await cdp.send(
        "Page.captureScreenshot",
        {"format": "png", "captureBeyondViewport": True, "clip": {"x": x, "y": y, "width": w, "height": h, "scale": 1}},
        session=session,
    )
    return base64.b64decode(shot.get("data") or "")


#: The tallest page a "page" or "pdf" capture keeps, in CSS pixels; a longer one is cut here.
CAPTURE_MAX_HEIGHT = 16000


async def capture(send: Any, kind: str, width: int, screen: dict) -> bytes:
    """The page as loaded at `width`, in the form `kind` names (see `browser.Render.capture`).
    `screen` is the emulated media a PDF is printed under: the page's screen styles."""
    if kind == "html":
        result = await send(
            "Runtime.evaluate",
            {"expression": "'<!doctype html>\\n' + document.documentElement.outerHTML", "returnByValue": True},
        )
        return str((result.get("result") or {}).get("value") or "").encode("utf-8")
    metrics = await send("Page.getLayoutMetrics")
    size = metrics.get("cssContentSize") or metrics.get("contentSize") or {}
    tall = max(VIEW_HEIGHT, min(int(size.get("height") or VIEW_HEIGHT), CAPTURE_MAX_HEIGHT))
    if kind == "pdf":
        # One page as wide as the screen and as tall as the page, in its screen styles: what the
        # person sees in the preview, not the page's print sheet.
        await send("Emulation.setEmulatedMedia", screen)
        pdf = await send(
            "Page.printToPDF",
            {
                "printBackground": True,
                "paperWidth": width / 96.0,
                "paperHeight": tall / 96.0,
                "marginTop": 0,
                "marginBottom": 0,
                "marginLeft": 0,
                "marginRight": 0,
                "preferCSSPageSize": False,
            },
        )
        return base64.b64decode(pdf.get("data") or "")
    high = VIEW_HEIGHT if kind == "view" else tall
    shot = await send(
        "Page.captureScreenshot",
        {"format": "png", "captureBeyondViewport": True, "clip": {"x": 0, "y": 0, "width": width, "height": high, "scale": 1}},
    )
    return base64.b64decode(shot.get("data") or "")
