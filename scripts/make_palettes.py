"""Write the generated colour palettes: `surfaces/gui/src/palettes.css` and `palettes.generated.ts`.

The eight hand-tuned palettes live in `styles.css` (Harbor as `:root`, the rest as blocks). The
ones here are written from a short table of each palette's own colours, and every other token is
derived by the rules the comment above the palette blocks in `styles.css` states, so a generated
palette defines the same 66 tokens a hand-tuned one does and no component changes with it.

One rule is added for these: a colour that is read as text is darkened (light faces) or lightened
(dark faces), 5% toward black or white at a time, until it clears WCAG AA (4.5:1) on the surface
it is read on. Toward black or white and not toward the ink: a pastel moved toward a grey ink went
muddy. A palette taken from an editor theme keeps its hues, and Reeve keeps its promise that every
role is legible. Each block's comment names what was moved and how far.

Every palette, the hand-tuned ones included, also gets the terminal's 16 ANSI colours
(`--ansi-*`). The hand-tuned ones keep the mapping the terminal used before (its syntax roles),
the generated ones carry their theme's own terminal table.

    python scripts/make_palettes.py           # write both files
    python scripts/make_palettes.py --check   # fail when a file is out of date
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CSS_OUT = ROOT / "surfaces" / "gui" / "src" / "palettes.css"
TS_OUT = ROOT / "surfaces" / "gui" / "src" / "palettes.generated.ts"

ANSI = (
    "black", "red", "green", "yellow", "blue", "magenta", "cyan", "white",
    "bright-black", "bright-red", "bright-green", "bright-yellow",
    "bright-blue", "bright-magenta", "bright-cyan", "bright-white",
)

# Each mode: surfaces, text, accent and status, the six tuned syntax roles, the code surface,
# and the 16 ANSI colours in the order above. `solid`/`on_solid` (the user's bubble) default to
# paper/ink, the rule every derived palette follows; Reeve sets its own.
PALETTES: list[dict] = [
    {
        "id": "reeve",
        "name": "Reeve",
        "intent": "Reeve's own: cool parchment neutrals with a plum cast and one plum accent for every action.",
        "light": dict(
            paper="#ece8ef", panel="#fffeff", canvas="#faf9fb", chrome="#f2f0f4", chrome_hover="#e6e1eb",
            ink="#1e1a24", muted="#5e5768", faint="#8f8899",
            accent="#74358c", accent_soft="#f1e7f5",
            ok="#1f7a46", ok_soft="#e4f3e8", warn="#8a5a00", warn_soft="#f7efdc", danger="#b3342f", danger_soft="#f9e9e6",
            solid="#1e1a24", on_solid="#faf9fb",
            comment="#6f6878", string="#2f7a3a", keyword="#74358c", variable="#1e1a24", type="#2457a6", constant="#9a5a00",
            code_bg="#f6f4f8",
            ansi=("#1e1a24", "#b3342f", "#2f7a3a", "#8a5a00", "#2457a6", "#74358c", "#1f6f7a", "#8f8899",
                  "#5e5768", "#d0453f", "#3a9248", "#a87000", "#3a6fc4", "#9150ad", "#2a8a96", "#5e5768"),
        ),
        "dark": dict(
            paper="#28242f", panel="#1e1b24", canvas="#17151c", chrome="#110f15", chrome_hover="#28242f",
            ink="#ece8f2", muted="#a9a2b4", faint="#746d7f",
            accent="#cf9fe6", accent_soft="#2e2238",
            ok="#7bd190", ok_soft="#172a1e", warn="#e8b45a", warn_soft="#322a15", danger="#f09a90", danger_soft="#3a2120",
            solid="#ece8f2", on_solid="#17151c",
            comment="#8a8395", string="#9fd59a", keyword="#cf9fe6", variable="#ece8f2", type="#8db8f2", constant="#e8b45a",
            code_bg="#1e1b24",
            ansi=("#28242f", "#f09a90", "#9fd59a", "#e8b45a", "#8db8f2", "#cf9fe6", "#7fd0d8", "#ece8f2",
                  "#746d7f", "#f5b0a8", "#b5e3b0", "#f0c97e", "#a9cbf6", "#dcb6ee", "#9fdde3", "#ffffff"),
        ),
    },
    {
        "id": "catppuccin",
        "name": "Catppuccin",
        "intent": "Latte by day, Mocha by night: soft pastels on lavender-grey, mauve for action.",
        "light": dict(
            paper="#dce0e8", panel="#fbfcfd", canvas="#eff1f5", chrome="#e6e9ef", chrome_hover="#dce0e8",
            ink="#4c4f69", muted="#6c6f85", faint="#8c8fa1",
            accent="#8839ef", accent_soft="#ece3fb",
            ok="#40a02b", ok_soft="#e3f1df", warn="#df8e1d", warn_soft="#f8ecd9", danger="#d20f39", danger_soft="#f8dfe4",
            comment="#7c7f93", string="#40a02b", keyword="#8839ef", variable="#4c4f69", type="#df8e1d", constant="#fe640b",
            code_bg="#e6e9ef",
            ansi=("#5c5f77", "#d20f39", "#40a02b", "#df8e1d", "#1e66f5", "#ea76cb", "#179299", "#acb0be",
                  "#6c6f85", "#d20f39", "#40a02b", "#df8e1d", "#1e66f5", "#ea76cb", "#179299", "#bcc0cc"),
        ),
        "dark": dict(
            paper="#313244", panel="#262637", canvas="#1e1e2e", chrome="#181825", chrome_hover="#313244",
            ink="#cdd6f4", muted="#a6adc8", faint="#6c7086",
            accent="#cba6f7", accent_soft="#3a2f52",
            ok="#a6e3a1", ok_soft="#22332a", warn="#f9e2af", warn_soft="#3a3428", danger="#f38ba8", danger_soft="#3e2633",
            comment="#9399b2", string="#a6e3a1", keyword="#cba6f7", variable="#cdd6f4", type="#f9e2af", constant="#fab387",
            code_bg="#181825",
            ansi=("#45475a", "#f38ba8", "#a6e3a1", "#f9e2af", "#89b4fa", "#f5c2e7", "#94e2d5", "#bac2de",
                  "#585b70", "#f38ba8", "#a6e3a1", "#f9e2af", "#89b4fa", "#f5c2e7", "#94e2d5", "#a6adc8"),
        ),
    },
    {
        "id": "rose-pine",
        "name": "Rosé Pine",
        "intent": "Dawn and Main: muted, low-glare surfaces with pine and iris.",
        "light": dict(
            paper="#efe5dc", panel="#fffaf3", canvas="#faf4ed", chrome="#f2e9e1", chrome_hover="#e9dfd5",
            ink="#575279", muted="#797593", faint="#9893a5",
            accent="#286983", accent_soft="#e2eef0",
            ok="#3e7a5a", ok_soft="#e3efe6", warn="#ea9d34", warn_soft="#f8ead6", danger="#b4637a", danger_soft="#f6e3e5",
            comment="#9893a5", string="#ea9d34", keyword="#286983", variable="#575279", type="#56949f", constant="#d7827e",
            code_bg="#f4ede8",
            ansi=("#f2e9e1", "#b4637a", "#286983", "#ea9d34", "#56949f", "#907aa9", "#d7827e", "#575279",
                  "#9893a5", "#b4637a", "#286983", "#ea9d34", "#56949f", "#907aa9", "#d7827e", "#575279"),
        ),
        "dark": dict(
            paper="#26233a", panel="#1f1d2e", canvas="#191724", chrome="#16141f", chrome_hover="#26233a",
            ink="#e0def4", muted="#908caa", faint="#6e6a86",
            accent="#c4a7e7", accent_soft="#2f2840",
            ok="#95d4ad", ok_soft="#1f2e29", warn="#f6c177", warn_soft="#352c22", danger="#eb6f92", danger_soft="#3a2030",
            comment="#6e6a86", string="#f6c177", keyword="#31748f", variable="#e0def4", type="#9ccfd8", constant="#ebbcba",
            code_bg="#1f1d2e",
            ansi=("#26233a", "#eb6f92", "#31748f", "#f6c177", "#9ccfd8", "#c4a7e7", "#ebbcba", "#e0def4",
                  "#6e6a86", "#eb6f92", "#31748f", "#f6c177", "#9ccfd8", "#c4a7e7", "#ebbcba", "#e0def4"),
        ),
    },
    {
        "id": "nord",
        "name": "Nord",
        "intent": "Arctic blue-greys and frost accents; the calmest of the set.",
        "light": dict(
            paper="#d8dee9", panel="#f5f7fa", canvas="#eceff4", chrome="#e5e9f0", chrome_hover="#d8dee9",
            ink="#2e3440", muted="#4c566a", faint="#7b88a1",
            accent="#5e81ac", accent_soft="#dfe7f1",
            ok="#a3be8c", ok_soft="#e3ecdc", warn="#ebcb8b", warn_soft="#f4ecd9", danger="#bf616a", danger_soft="#f1dfe1",
            comment="#616e88", string="#a3be8c", keyword="#5e81ac", variable="#2e3440", type="#8fbcbb", constant="#b48ead",
            code_bg="#e5e9f0",
            ansi=("#3b4252", "#bf616a", "#a3be8c", "#ebcb8b", "#81a1c1", "#b48ead", "#88c0d0", "#e5e9f0",
                  "#4c566a", "#bf616a", "#a3be8c", "#ebcb8b", "#81a1c1", "#b48ead", "#8fbcbb", "#eceff4"),
        ),
        "dark": dict(
            paper="#434c5e", panel="#3b4252", canvas="#2e3440", chrome="#272c36", chrome_hover="#3b4252",
            ink="#eceff4", muted="#aab4c4", faint="#7b88a1",
            accent="#88c0d0", accent_soft="#36475a",
            ok="#a3be8c", ok_soft="#39443a", warn="#ebcb8b", warn_soft="#4a4636", danger="#bf616a", danger_soft="#4a3640",
            comment="#616e88", string="#a3be8c", keyword="#81a1c1", variable="#d8dee9", type="#8fbcbb", constant="#b48ead",
            code_bg="#353b48",
            ansi=("#3b4252", "#bf616a", "#a3be8c", "#ebcb8b", "#81a1c1", "#b48ead", "#88c0d0", "#e5e9f0",
                  "#4c566a", "#bf616a", "#a3be8c", "#ebcb8b", "#81a1c1", "#b48ead", "#8fbcbb", "#eceff4"),
        ),
    },
    {
        "id": "gruvbox",
        "name": "Gruvbox",
        "intent": "Retro warm contrast: cream by day, charcoal and orange at night.",
        "light": dict(
            paper="#ebdbb2", panel="#f9f5d7", canvas="#fbf1c7", chrome="#f2e5bc", chrome_hover="#ebdbb2",
            ink="#3c3836", muted="#665c54", faint="#928374",
            accent="#af3a03", accent_soft="#f6dfc4",
            ok="#79740e", ok_soft="#eaeac0", warn="#b57614", warn_soft="#f5e4bd", danger="#9d0006", danger_soft="#f6d8c8",
            comment="#928374", string="#79740e", keyword="#9d0006", variable="#3c3836", type="#b57614", constant="#8f3f71",
            code_bg="#f2e5bc",
            ansi=("#fbf1c7", "#cc241d", "#98971a", "#d79921", "#458588", "#b16286", "#689d6a", "#7c6f64",
                  "#928374", "#9d0006", "#79740e", "#b57614", "#076678", "#8f3f71", "#427b58", "#3c3836"),
        ),
        "dark": dict(
            paper="#3c3836", panel="#32302f", canvas="#282828", chrome="#1d2021", chrome_hover="#3c3836",
            ink="#ebdbb2", muted="#a89984", faint="#7c6f64",
            accent="#fe8019", accent_soft="#45311f",
            ok="#b8bb26", ok_soft="#33351c", warn="#fabd2f", warn_soft="#403520", danger="#fb4934", danger_soft="#43231f",
            comment="#928374", string="#b8bb26", keyword="#fb4934", variable="#ebdbb2", type="#fabd2f", constant="#d3869b",
            code_bg="#32302f",
            ansi=("#282828", "#cc241d", "#98971a", "#d79921", "#458588", "#b16286", "#689d6a", "#a89984",
                  "#928374", "#fb4934", "#b8bb26", "#fabd2f", "#83a598", "#d3869b", "#8ec07c", "#ebdbb2"),
        ),
    },
    {
        "id": "solarized",
        "name": "Solarized",
        "intent": "The same accents on both faces, built for long reading.",
        "light": dict(
            paper="#e8e1cc", panel="#fffbee", canvas="#fdf6e3", chrome="#eee8d5", chrome_hover="#e4ddc8",
            ink="#2f4a52", muted="#586e75", faint="#93a1a1",
            accent="#268bd2", accent_soft="#e0ecf0",
            ok="#859900", ok_soft="#eceed2", warn="#b58900", warn_soft="#f5ebcf", danger="#dc322f", danger_soft="#f9e2d8",
            comment="#93a1a1", string="#2aa198", keyword="#859900", variable="#2f4a52", type="#b58900", constant="#d33682",
            code_bg="#eee8d5",
            ansi=("#073642", "#dc322f", "#859900", "#b58900", "#268bd2", "#d33682", "#2aa198", "#eee8d5",
                  "#002b36", "#cb4b16", "#586e75", "#657b83", "#839496", "#6c71c4", "#93a1a1", "#fdf6e3"),
        ),
        "dark": dict(
            paper="#0a404d", panel="#073642", canvas="#002b36", chrome="#00212b", chrome_hover="#073642",
            ink="#d0d6d4", muted="#93a1a1", faint="#657b83",
            accent="#268bd2", accent_soft="#0c3b52",
            ok="#859900", ok_soft="#16391f", warn="#b58900", warn_soft="#2a3a24", danger="#dc322f", danger_soft="#2f2a2c",
            comment="#586e75", string="#2aa198", keyword="#859900", variable="#93a1a1", type="#b58900", constant="#d33682",
            code_bg="#073642",
            ansi=("#073642", "#dc322f", "#859900", "#b58900", "#268bd2", "#d33682", "#2aa198", "#eee8d5",
                  "#002b36", "#cb4b16", "#586e75", "#657b83", "#839496", "#6c71c4", "#93a1a1", "#fdf6e3"),
        ),
    },
    {
        "id": "tokyo-night",
        "name": "Tokyo Night",
        "intent": "Day and Night: neon-city blues on deep navy.",
        "light": dict(
            paper="#c9cad0", panel="#ebecf0", canvas="#e1e2e7", chrome="#d5d6db", chrome_hover="#c9cad0",
            ink="#1f2a5c", muted="#4a5487", faint="#8990b3",
            accent="#2e7de9", accent_soft="#d3e0f5",
            ok="#587539", ok_soft="#dde6d3", warn="#8c6c3e", warn_soft="#ece3d3", danger="#f52a65", danger_soft="#f6d9e1",
            comment="#848cb5", string="#587539", keyword="#9854f1", variable="#3760bf", type="#188092", constant="#b15c00",
            code_bg="#d5d6db",
            ansi=("#e9e9ed", "#f52a65", "#587539", "#8c6c3e", "#2e7de9", "#9854f1", "#007197", "#6172b0",
                  "#a1a6c5", "#f52a65", "#587539", "#8c6c3e", "#2e7de9", "#9854f1", "#007197", "#3760bf"),
        ),
        "dark": dict(
            paper="#292e42", panel="#1f2335", canvas="#1a1b26", chrome="#16161e", chrome_hover="#292e42",
            ink="#c0caf5", muted="#a9b1d6", faint="#565f89",
            accent="#7aa2f7", accent_soft="#283457",
            ok="#9ece6a", ok_soft="#263020", warn="#e0af68", warn_soft="#3b3020", danger="#f7768e", danger_soft="#3d2230",
            comment="#565f89", string="#9ece6a", keyword="#bb9af7", variable="#c0caf5", type="#2ac3de", constant="#ff9e64",
            code_bg="#1f2335",
            ansi=("#15161e", "#f7768e", "#9ece6a", "#e0af68", "#7aa2f7", "#bb9af7", "#7dcfff", "#a9b1d6",
                  "#414868", "#f7768e", "#9ece6a", "#e0af68", "#7aa2f7", "#bb9af7", "#7dcfff", "#c0caf5"),
        ),
    },
    {
        "id": "github",
        "name": "GitHub",
        "intent": "Primer's light and dark: what a pull request looks like.",
        "light": dict(
            paper="#eff2f5", panel="#ffffff", canvas="#ffffff", chrome="#f6f8fa", chrome_hover="#eaeef2",
            ink="#1f2328", muted="#59636e", faint="#818b98",
            accent="#0969da", accent_soft="#ddf4ff",
            ok="#1a7f37", ok_soft="#dafbe1", warn="#9a6700", warn_soft="#fff8c5", danger="#d1242f", danger_soft="#ffebe9",
            comment="#59636e", string="#0a3069", keyword="#cf222e", variable="#1f2328", type="#8250df", constant="#0550ae",
            code_bg="#f6f8fa",
            ansi=("#24292f", "#cf222e", "#116329", "#4d2d00", "#0969da", "#8250df", "#1b7c83", "#6e7781",
                  "#57606a", "#a40e26", "#1a7f37", "#633c01", "#218bff", "#a475f9", "#3192aa", "#8c959f"),
        ),
        "dark": dict(
            paper="#21262d", panel="#151b23", canvas="#0d1117", chrome="#010409", chrome_hover="#21262d",
            ink="#e6edf3", muted="#9198a1", faint="#656c76",
            accent="#4493f8", accent_soft="#12233d",
            ok="#3fb950", ok_soft="#12261e", warn="#d29922", warn_soft="#272115", danger="#f85149", danger_soft="#2d1517",
            comment="#9198a1", string="#a5d6ff", keyword="#ff7b72", variable="#e6edf3", type="#d2a8ff", constant="#79c0ff",
            code_bg="#151b23",
            ansi=("#484f58", "#ff7b72", "#3fb950", "#d29922", "#58a6ff", "#bc8cff", "#39c5cf", "#b1bac4",
                  "#6e7681", "#ffa198", "#56d364", "#e3b341", "#79c0ff", "#d2a8ff", "#56d4dd", "#ffffff"),
        ),
    },
]


# -- colour arithmetic ------------------------------------------------------------------------


def rgb(hex_: str) -> tuple[int, int, int]:
    h = hex_.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def hexed(c: tuple[float, float, float]) -> str:
    return "#" + "".join(f"{max(0, min(255, round(v))):02x}" for v in c)


def mix(a: str, b: str, t: float) -> str:
    """`a` moved toward `b` by `t` (0..1)."""
    ra, rb = rgb(a), rgb(b)
    return hexed(tuple(x + (y - x) * t for x, y in zip(ra, rb)))


def over(color: str, alpha: float, surface: str) -> str:
    """`color` at `alpha` painted over `surface`."""
    return mix(surface, color, alpha)


def luminance(hex_: str) -> float:
    def lin(c: float) -> float:
        c /= 255
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = rgb(hex_)
    return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)


def contrast(a: str, b: str) -> float:
    hi, lo = sorted((luminance(a), luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def rgba(hex_: str, alpha: float) -> str:
    r, g, b = rgb(hex_)
    return f"rgba({r}, {g}, {b}, {alpha:g})"


def legible(color: str, toward: str, surfaces: list[str], target: float = 4.5) -> tuple[str, int]:
    """`color`, moved toward `toward` (black or white) 5% at a time until it clears `target` on
    every surface, and how far it moved in percent."""
    for step in range(0, 101, 5):
        moved = mix(color, toward, step / 100)
        if all(contrast(moved, s) >= target for s in surfaces):
            return moved, step
    return toward, 100


# -- one block --------------------------------------------------------------------------------


def block(p: dict, mode: str) -> tuple[str, dict]:
    v = dict(p[mode])
    dark = mode == "dark"
    ink, panel, canvas, code_bg = v["ink"], v["panel"], v["canvas"], v["code_bg"]
    toward = "#ffffff" if dark else "#000000"
    moved: list[str] = []

    def fix(key: str, surfaces: list[str]) -> str:
        value, step = legible(v[key], toward, surfaces)
        if step:
            moved.append(f"{key.replace('_', '-')} {step}%")
        return value

    muted = fix("muted", [canvas])
    accent = fix("accent", [canvas, panel])
    ok = fix("ok", [panel])
    warn = fix("warn", [panel])
    danger = fix("danger", [panel])
    syntax = {k: fix(k, [code_bg]) for k in ("comment", "string", "keyword", "type", "constant")}
    syntax["variable"] = v["variable"]
    md_fg, step = legible(muted, toward, [code_bg])
    if step:
        moved.append(f"md-code-fg {step}%")
    diff_add, add_step = legible(ok, toward, [v["ok_soft"]])
    diff_del, del_step = legible(danger, toward, [v["danger_soft"]])
    hover_a, pressed_a = (0.06, 0.10) if dark else (0.05, 0.09)
    tokens = {
        "paper": v["paper"],
        "panel": panel,
        "canvas": canvas,
        "chrome": v["chrome"],
        "chrome-hover": v["chrome_hover"],
        "edge-shadow": f"-14px 0 28px -18px {rgba('#000000', 0.65) if dark else rgba(ink, 0.14)}",
        "ink": ink,
        "muted": muted,
        "faint": v["faint"],
        "line": rgba(ink, 0.10),
        "line-strong": rgba(ink, 0.22),
        "line-muted": rgba(ink, 0.07),
        "accent": accent,
        "accent-soft": v["accent_soft"],
        "ok": ok,
        "ok-soft": v["ok_soft"],
        "ok-line": over(ok, 0.30, panel),
        "ok-dot": ok,
        "warn-ink": warn,
        "warn-soft": v["warn_soft"],
        "danger": danger,
        "danger-soft": v["danger_soft"],
        "teal-ink": syntax["type"],
        "teal-line": over(syntax["type"], 0.30, panel),
        "teal-soft": over(syntax["type"], 0.12, panel),
        "solid": v.get("solid", v["paper"]),
        "on-solid": v.get("on_solid", ink),
        "glass": rgba(panel, 0.9),
        "glass-strong": rgba(panel, 0.96),
        "glass-soft": rgba(panel, 0.78),
        "scrim": rgba("#000000", 0.55) if dark else rgba(ink, 0.32),
        "check-a": v["paper"],
        "check-b": canvas,
        "selection-bg": over(accent, 0.20, panel),
        "selection-ink": ink,
        "diff-add-bg": v["ok_soft"],
        "diff-del-bg": v["danger_soft"],
        "diff-add-ink": diff_add,
        "diff-del-ink": diff_del,
        "overlay-hover": rgba(ink, hover_a),
        "overlay-pressed": rgba(ink, pressed_a),
        "syntax-comment": syntax["comment"],
        "syntax-regexp": "var(--muted)",
        "syntax-string": syntax["string"],
        "syntax-keyword": syntax["keyword"],
        "syntax-primitive": "var(--syntax-keyword)",
        "syntax-operator": "var(--muted)",
        "syntax-variable": syntax["variable"],
        "syntax-strong": "var(--ink)",
        "syntax-property": "var(--syntax-constant)",
        "syntax-type": syntax["type"],
        "syntax-constant": syntax["constant"],
        "syntax-punctuation": "var(--muted)",
        "syntax-object": "var(--syntax-variable)",
        "syntax-critical": "var(--diff-del-ink)",
        "syntax-info": "var(--syntax-type)",
        "syntax-warning": "var(--syntax-constant)",
        "syntax-diff-add": "var(--diff-add-ink)",
        "syntax-diff-delete": "var(--diff-del-ink)",
        "syntax-diff-unknown": "var(--syntax-type)",
        "md-code-bg": code_bg,
        "md-code-fg": md_fg,
        "md-code-copy": v["faint"],
        "md-code-copy-hover": rgba(ink, hover_a),
        "md-code-focus": accent,
        "md-inline-code-accent": accent,
    }
    for name, value in zip(ANSI, v["ansi"]):
        tokens[f"ansi-{name}"] = value
    selector = (
        f'html[data-theme="dark"][data-palette="{p["id"]}"]' if dark else f'html[data-palette="{p["id"]}"]'
    )
    face = "dark" if dark else "light"
    note = (
        f"/* {p['name']} — {face}. {p['intent']}\n"
        f"   Derived: diff inks moved {add_step}% / {del_step}% toward {'white' if dark else 'black'}; "
        + (f"moved for AA: {', '.join(moved)}; " if moved else "")
        + f"selection ink {contrast(ink, tokens['selection-bg']):.2f}:1. */\n"
    )
    body = "\n".join(f"  --{k}: {val};" for k, val in tokens.items())
    css = f"{note}{selector} {{\n  color-scheme: {face};\n{body}\n}}\n"
    preview = {"canvas": canvas, "chrome": v["chrome"], "accent": accent, "line": rgba(ink, 0.22)}
    return css, preview


# The hand-tuned palettes' terminal: the mapping the terminal panel used before ANSI tokens.
# One `:root` block serves all of them, since each name resolves against the palette in force.
LEGACY_ANSI = {
    "black": "var(--syntax-strong)",
    "red": "var(--syntax-critical)",
    "green": "var(--syntax-string)",
    "yellow": "var(--syntax-warning)",
    "blue": "var(--accent)",
    "magenta": "var(--syntax-info)",
    "cyan": "var(--syntax-type)",
    "white": "var(--syntax-comment)",
    "bright-black": "var(--syntax-comment)",
    "bright-red": "var(--diff-del-ink)",
    "bright-green": "var(--diff-add-ink)",
    "bright-yellow": "var(--syntax-constant)",
    "bright-blue": "var(--accent)",
    "bright-magenta": "var(--syntax-keyword)",
    "bright-cyan": "var(--syntax-type)",
    "bright-white": "var(--syntax-strong)",
}


def render() -> tuple[str, str]:
    head = (
        "/* GENERATED by scripts/make_palettes.py — edit the table there, not this file.\n"
        "   The palettes taken from a table and every palette's 16 terminal colours.\n"
        "   Imported after styles.css: a block here is (0,1,1) or (0,2,1) and wins over Harbor's\n"
        "   `:root` and dark block the same way the hand-tuned palettes do. */\n\n"
    )
    legacy = ":root {\n" + "\n".join(f"  --ansi-{k}: {v};" for k, v in LEGACY_ANSI.items()) + "\n}\n"
    parts = [head, "/* The hand-tuned palettes' terminal, read from their own roles. */\n", legacy]
    previews: dict[str, dict] = {}
    for p in PALETTES:
        light_css, light_preview = block(p, "light")
        dark_css, dark_preview = block(p, "dark")
        parts += ["\n", light_css, "\n", dark_css]
        previews[p["id"]] = {"light": light_preview, "dark": dark_preview}
    ts = [
        "// GENERATED by scripts/make_palettes.py — edit the table there, not this file.",
        "// The Settings preview chips of the generated palettes; the live colours are palettes.css.",
        'import type { PalettePreview } from "./theme";',
        "",
        "export const GENERATED_PREVIEWS: Record<string, { light: PalettePreview; dark: PalettePreview }> = {",
    ]
    for pid, faces in previews.items():
        ts.append(f'  "{pid}": {{')
        for face in ("light", "dark"):
            f = faces[face]
            ts.append(
                f'    {face}: {{ canvas: "{f["canvas"]}", chrome: "{f["chrome"]}", accent: "{f["accent"]}", line: "{f["line"]}" }},'
            )
        ts.append("  },")
    ts.append("};")
    return "".join(parts), "\n".join(ts) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="fail when a written file is out of date")
    args = parser.parse_args()
    css, ts = render()
    stale = [
        path for path, text in ((CSS_OUT, css), (TS_OUT, ts))
        if not path.exists() or path.read_text(encoding="utf-8") != text
    ]
    if args.check:
        for path in stale:
            print(f"out of date: {path.relative_to(ROOT)}")
        return 1 if stale else 0
    for path, text in ((CSS_OUT, css), (TS_OUT, ts)):
        path.write_text(text, encoding="utf-8", newline="\n")
        print(f"wrote {path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
