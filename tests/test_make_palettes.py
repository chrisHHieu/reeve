"""The generated palettes: the files match their table, and every text colour is legible."""

from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "make_palettes.py"


def _module():
    spec = importlib.util.spec_from_file_location("make_palettes", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_written_files_are_what_the_table_makes():
    done = subprocess.run([sys.executable, str(SCRIPT), "--check"], capture_output=True, text=True)
    assert done.returncode == 0, done.stdout + done.stderr


def test_every_colour_read_as_text_clears_aa_where_it_is_read():
    module = _module()
    css, _ts = module.render()
    for palette in module.PALETTES:
        for face in ("light", "dark"):
            selector = (
                f'html[data-theme="dark"][data-palette="{palette["id"]}"]'
                if face == "dark"
                else f'html[data-palette="{palette["id"]}"]'
            )
            start = css.index(selector + " {")
            body = css[start : css.index("\n}", start)]
            tokens = dict(re.findall(r"--([\w-]+): (#[0-9a-f]{6});", body))
            where = {
                "ink": "canvas",
                "muted": "canvas",
                "accent": "panel",
                "ok": "panel",
                "warn-ink": "panel",
                "danger": "panel",
                "syntax-comment": "md-code-bg",
                "syntax-string": "md-code-bg",
                "syntax-keyword": "md-code-bg",
                "syntax-type": "md-code-bg",
                "syntax-constant": "md-code-bg",
                "md-code-fg": "md-code-bg",
                "diff-add-ink": "diff-add-bg",
                "diff-del-ink": "diff-del-bg",
            }
            for text, surface in where.items():
                ratio = module.contrast(tokens[text], tokens[surface])
                assert ratio >= 4.5, f"{palette['id']} {face}: {text} on {surface} is {ratio:.2f}:1"


def test_a_pastel_is_darkened_not_greyed():
    module = _module()
    # Moving toward black keeps a hue; moving toward a grey ink is what made Latte's green muddy.
    moved, step = module.legible("#40a02b", "#000000", ["#e6e9ef"])
    assert step > 0 and module.contrast(moved, "#e6e9ef") >= 4.5
    r, g, b = module.rgb(moved)
    assert g > r and g > b
