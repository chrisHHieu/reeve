"""The mark is drawn in one place (scripts/make_brand_icons.py). These pin that its outputs agree:
the in-app glyph in Icon.tsx is the script's own path, the tray template is black + alpha with a
hollow where the squares overlap, and the app icon carries both squares in their own colours."""

from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _pixels(img):
    raw = img.tobytes()
    return [tuple(raw[i : i + 4]) for i in range(0, len(raw), 4)]
_spec = importlib.util.spec_from_file_location("make_brand_icons", ROOT / "scripts" / "make_brand_icons.py")
brand = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(brand)  # type: ignore[union-attr]


def test_the_in_app_glyph_is_the_scripts_own_path():
    person, agent = brand.glyph_path()
    source = (ROOT / "surfaces" / "gui" / "src" / "components" / "Icon.tsx").read_text(encoding="utf-8")
    assert person in source and agent in source, "Icon.tsx drifted from scripts/make_brand_icons.py --glyph"


def test_the_tray_template_is_black_and_alpha_with_a_hollow_between_the_squares():
    img = brand.tray(44)
    assert img.size == (44, 44)
    pixels = _pixels(img)
    assert {p[:3] for p in pixels if p[3] > 0} == {(0, 0, 0)}  # a template has no colour
    alphas = [[img.getpixel((x, y))[3] for x in range(44)] for y in range(44)]
    # across the middle row there are two filled runs with the hollow between them
    row = [a > 128 for a in alphas[22]]
    runs = sum(1 for i in range(1, 44) if row[i] and not row[i - 1]) + (1 if row[0] else 0)
    assert runs == 2


def test_the_app_icon_carries_both_squares_in_their_own_colours():
    img = brand.tile(256)
    colours = {p[:3] for p in _pixels(img) if p[3] == 255}
    assert brand.PAPER[:3] in colours and brand.TEAL[:3] in colours and brand.TILE[:3] in colours


def test_the_bundled_tray_files_match_what_the_script_draws():
    from PIL import Image

    icons = ROOT / "surfaces" / "gui" / "electron" / "icons"
    big = Image.open(icons / "trayTemplate@2x.png").convert("RGBA")
    small = Image.open(icons / "trayTemplate.png").convert("RGBA")
    assert big.tobytes() == brand.tray(44).tobytes(), "run scripts/make_brand_icons.py"
    assert small.tobytes() == brand.tray(44).resize((22, 22), Image.Resampling.LANCZOS).tobytes(), "run scripts/make_brand_icons.py"
