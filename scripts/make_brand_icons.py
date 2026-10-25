"""Draw the app's mark and write every raster that carries it.

The mark is two rounded squares set on a diagonal, one the person's and one the agent's. Where
they overlap the ink drops out: that hollow is the work they hold in common. It is pure
geometry, so this script draws it directly (Pillow, 8x supersampled) instead of rasterising an
SVG: no renderer, no font, the same pixels on every machine.

    .venv\\Scripts\\python.exe scripts\\make_brand_icons.py [--check]

Writes, under `surfaces/gui/`:
  assets/icon-master.png, assets/icon.png       the app icon (tile) at 1254 and 1024
  public/favicon.png                            64px tile
  electron/icons/icon.png, 32x32.png            the window icon and the Windows tray icon
  electron/icons/icon.ico, icon.icns            the Windows and macOS app icons (electron-builder)
  electron/icons/trayTemplate(@2x).png          22 and 44 px black+alpha template for the menu bar

`--check` draws everything and writes nothing (CI-safe smoke test). The in-app glyph is the path
printed by `--glyph`; it is pasted into `components/Icon.tsx`.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw

GUI = Path(__file__).resolve().parent.parent / "surfaces" / "gui"
SS = 8  # supersampling

# Harbor dark: the tile is the app's darkest chrome, the squares are paper and the dark accent.
TILE = (20, 22, 27, 255)
PAPER = (244, 241, 234, 255)
TEAL = (108, 199, 220, 255)

# The mark, in a 256-unit box centred on (128, 128): `s` is a square's side, `off` how far the
# second square is slid down and right of the first (also the arm thickness), `r` the corner
# radius. `SMALL` is the cut for 16-64px: thicker arms and a tighter radius, so the hollow
# does not close up.
FULL = dict(s=128.0, off=44.0, r=34.0)
SMALL = dict(s=124.0, off=52.0, r=28.0)


def draw_mark(img: Image.Image, cx: float, cy: float, span: float, colours, geo=FULL) -> None:
    """Draw the mark centred at (cx, cy), `span` px wide overall, onto `img`.

    `colours` is (person, agent). Each square keeps its own colour outside the overlap; the
    overlap itself is left as it was (the tile, or transparent for the tray template).
    """
    s, off, r = geo["s"], geo["off"], geo["r"]
    k = span / (s + off)
    side, radius, shift = round(s * k), r * k, round(off * k)
    w = side + shift
    first = Image.new("L", (w, w), 0)
    ImageDraw.Draw(first).rounded_rectangle([0, 0, side - 1, side - 1], radius=radius, fill=255)
    second = Image.new("L", (w, w), 0)
    ImageDraw.Draw(second).rounded_rectangle([shift, shift, w - 1, w - 1], radius=radius, fill=255)
    both = ImageChops.multiply(first, second)
    at = (round(cx - w / 2), round(cy - w / 2))
    for mask, colour in ((ImageChops.subtract(first, both), colours[0]), (ImageChops.subtract(second, both), colours[1])):
        img.paste(colour, at, mask)


def _rounded(size: int, radius: float, colour):
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    ImageDraw.Draw(img).rounded_rectangle([0, 0, size - 1, size - 1], radius=radius, fill=colour)
    return img


def tile(size: int, geo=FULL, fill: float = 0.5, margin: float = 0.0) -> Image.Image:
    """The app icon: a rounded dark tile with the mark in paper and teal."""
    big = size * SS
    inner = big * (1 - 2 * margin)
    img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    plate = _rounded(int(inner), inner * 0.225, TILE)
    img.alpha_composite(plate, (int(big * margin), int(big * margin)))
    draw_mark(img, big / 2, big / 2, inner * fill, (PAPER, TEAL), geo)
    return img.resize((size, size), Image.LANCZOS)


def tray(size: int = 44) -> Image.Image:
    """Black + alpha, for the menu bar to tint. Both squares the same: a template has no colour."""
    big = size * SS
    img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    draw_mark(img, big / 2, big / 2, big * 0.88, ((0, 0, 0, 255), (0, 0, 0, 255)), SMALL)
    return img.resize((size, size), Image.LANCZOS)


def _l_path(x: float, s: float, off: float, r: float, turn_about: float | None) -> str:
    """One square minus the overlap, an L-shape: the first square, or (turned 180 degrees about
    `turn_about`, the box centre) the second. Both squares start at (x, x). The overlap's near
    corners are the squares' own rounded ones, its far two are sharp."""

    def pt(a: float, b: float) -> str:
        if turn_about is not None:
            a, b = 2 * turn_about - a, 2 * turn_about - b
        return f"{a:.2f} {b:.2f}"

    x2 = x + off
    arc = f"A{r:.2f} {r:.2f} 0 0"
    return (
        f"M{pt(x + r, x)}"
        f"L{pt(x + s - r, x)}{arc} 1 {pt(x + s, x + r)}"
        f"L{pt(x + s, x2)}L{pt(x2 + r, x2)}{arc} 0 {pt(x2, x2 + r)}"
        f"L{pt(x2, x + s)}L{pt(x + r, x + s)}{arc} 1 {pt(x, x + s - r)}"
        f"L{pt(x, x + r)}{arc} 1 {pt(x + r, x)}Z"
    )


def glyph_path(geo=SMALL, box: float = 24.0, span: float = 20.0) -> tuple[str, str]:
    """The two squares as SVG path data in a `box`-unit square, for components/Icon.tsx.

    The agent's L is the person's turned 180 degrees about the centre, so one function draws both.
    """
    k = span / (geo["s"] + geo["off"])
    s, off, r = geo["s"] * k, geo["off"] * k, geo["r"] * k
    x = (box - (s + off)) / 2
    return _l_path(x, s, off, r, None), _l_path(x, s, off, r, box / 2)


def main(argv: list[str]) -> int:
    if "--glyph" in argv:
        a, b = glyph_path()
        print("person square:\n" + a + "\n\nagent square:\n" + b)
        return 0
    check = "--check" in argv
    out: dict[Path, Image.Image] = {}
    icons = GUI / "electron" / "icons"
    master = tile(1254, margin=0.0)
    out[GUI / "assets" / "icon-master.png"] = master
    out[GUI / "assets" / "icon.png"] = tile(1024)
    out[GUI / "public" / "favicon.png"] = tile(64, SMALL, fill=0.56)
    out[icons / "icon.png"] = tile(512)
    out[icons / "32x32.png"] = tile(32, SMALL, fill=0.56)
    # The menu bar's template: black and alpha, drawn at 44 px and shrunk for the 1x screen.
    out[icons / "trayTemplate@2x.png"] = tray(44)
    out[icons / "trayTemplate.png"] = tray(44).resize((22, 22), Image.Resampling.LANCZOS)
    for path, img in out.items():
        if not check:
            path.parent.mkdir(parents=True, exist_ok=True)
            img.save(path)
        print(("checked " if check else "wrote ") + str(path.relative_to(GUI)), img.size)
    ico = [tile(s, SMALL if s <= 64 else FULL, fill=0.56 if s <= 64 else 0.5) for s in (16, 24, 32, 48, 64, 128, 256)]
    if not check:
        ico[-1].save(icons / "icon.ico", format="ICO", sizes=[(s, s) for s in (16, 24, 32, 48, 64, 128, 256)], append_images=ico[:-1])
        tile(1024).save(icons / "icon.icns", format="ICNS")
    print("ico/icns", "skipped" if check else "written")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
