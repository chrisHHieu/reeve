"""Build the file-icon sprite, its associations and its palette from Catppuccin's VS Code icons.

    python surfaces/gui/scripts/build-file-icons.py <path to a checkout of catppuccin/vscode-icons>

Writes, under surfaces/gui/src/assets/file-icons/:

  sprite.svg    one <symbol> per icon of the `css-variables` flavor, coloured by `var(--fi-<colour>)`
  maps.json     file names, extensions, language ids and folder names -> icon, from the repo's
                defaults (read as text: the TypeScript is never run)
  palette.css   the --fi-* colours: Latte on a light theme, Mocha on a dark one, so one sprite
                follows the theme
  LICENSE       Catppuccin's MIT licence, which travels with the icons

The checkout is data: nothing in it is executed.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "src" / "assets" / "file-icons"
_COLOR = re.compile(r"var\(--vscode-ctp-([a-z0-9]+)\)")
_HEX = re.compile(r"#[0-9a-fA-F]{6}\b")


def _strings(block: str) -> list[str]:
    return re.findall(r"'([^']*)'", block)


def _associations(source: str, keys: tuple[str, ...]) -> dict[str, dict[str, list[str]]]:
    """`'icon': { fileExtensions: [...], ... }` entries of a defaults file, by icon."""
    body = source[source.index("{", source.index("= {")) + 1 :]
    out: dict[str, dict[str, list[str]]] = {}
    for match in re.finditer(r"\n  (?:'([^']+)'|([A-Za-z0-9_]+)): \{(.*?)\n  \},", body, re.S):
        icon = match.group(1) or match.group(2)
        lists = {}
        for key in keys:
            found = re.search(key + r":\s*\[(.*?)\]", match.group(3), re.S)
            if found:
                lists[key] = _strings(found.group(1))
        out[icon] = lists
    return out


def _palette(icons: Path, flavor: str) -> dict[str, str]:
    """Each Catppuccin colour name's hex in `flavor`, read by lining up the colours of every icon
    in the css-variables flavor with the same icon in `flavor` (same shapes, same order)."""
    seen: dict[str, str] = {}
    for variable in (icons / "css-variables").glob("*.svg"):
        names = _COLOR.findall(variable.read_text(encoding="utf-8"))
        other = icons / flavor / variable.name
        if not other.exists():
            continue
        hexes = _HEX.findall(other.read_text(encoding="utf-8"))
        if len(names) != len(hexes):
            continue
        for name, value in zip(names, hexes):
            if seen.setdefault(name, value.lower()) != value.lower():
                raise SystemExit(f"{flavor}: {name} is both {seen[name]} and {value}")
    return seen


def _symbol(name: str, svg: str) -> str:
    view = re.search(r'viewBox="([^"]+)"', svg).group(1)
    inner = re.sub(r"^.*?<svg[^>]*>|</svg>\s*$", "", svg, flags=re.S).strip()
    # Ids inside an icon are made the icon's own: one sprite holds them all, and a shared `a`
    # would let one icon's gradient paint another (the bug the old sprite had).
    for local in set(re.findall(r'id="([^"]+)"', inner)):
        inner = inner.replace(f'id="{local}"', f'id="{name}-{local}"')
        inner = inner.replace(f"url(#{local})", f"url(#{name}-{local})").replace(f'href="#{local}"', f'href="#{name}-{local}"')
    inner = _COLOR.sub(r"var(--fi-\1)", inner)
    inner = re.sub(r"\s*\n\s*", " ", inner)
    return f'<symbol id="{name}" viewBox="{view}">{inner}</symbol>'


def main(repo: Path) -> None:
    icons = repo / "icons"
    files = _associations((repo / "src/defaults/fileIcons.ts").read_text(encoding="utf-8"), ("languageIds", "fileExtensions", "fileNames"))
    folders = _associations((repo / "src/defaults/folderIcons.ts").read_text(encoding="utf-8"), ("folderNames",))
    available = {p.stem for p in (icons / "css-variables").glob("*.svg")}

    maps: dict[str, dict[str, str]] = {"fileNames": {}, "fileExtensions": {}, "languageIds": {}, "folderNames": {}}
    for icon, lists in files.items():
        if icon not in available:
            continue
        for key in ("fileNames", "fileExtensions", "languageIds"):
            for value in lists.get(key, []):
                maps[key].setdefault(value.lower(), icon)
    for icon, lists in folders.items():
        if f"folder_{icon}" not in available:
            continue
        for value in lists.get("folderNames", []):
            maps["folderNames"].setdefault(value.lower(), f"folder_{icon}")
    maps["defaults"] = {"file": "_file", "folder": "_folder", "folderOpen": "_folder_open"}  # type: ignore[assignment]

    symbols = [_symbol(p.stem, p.read_text(encoding="utf-8")) for p in sorted((icons / "css-variables").glob("*.svg"))]
    sprite = '<svg xmlns="http://www.w3.org/2000/svg" width="0" height="0"><defs>\n' + "\n".join(symbols) + "\n</defs></svg>\n"

    latte, mocha = _palette(icons, "latte"), _palette(icons, "mocha")
    used = sorted(set(_COLOR.findall("".join(p.read_text(encoding="utf-8") for p in (icons / "css-variables").glob("*.svg")))))
    missing = [c for c in used if c not in latte or c not in mocha]
    if missing:
        raise SystemExit(f"no hex for {missing}")
    rows = lambda flavor: "\n".join(f"  --fi-{c}: {flavor[c]};" for c in used)  # noqa: E731
    palette = (
        "/* File-icon colours (generated by scripts/build-file-icons.py from Catppuccin; do not edit).\n"
        "   Latte on a light theme and Mocha on a dark one, so the one sprite follows the theme. */\n"
        f":root {{\n{rows(latte)}\n}}\n\nhtml[data-theme=\"dark\"] {{\n{rows(mocha)}\n}}\n"
    )

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "sprite.svg").write_text(sprite, encoding="utf-8", newline="\n")
    (OUT / "maps.json").write_text(json.dumps(maps, indent=0, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    (OUT / "palette.css").write_text(palette, encoding="utf-8", newline="\n")
    (OUT / "LICENSE").write_text((repo / "LICENSE").read_text(encoding="utf-8"), encoding="utf-8", newline="\n")
    print(f"{len(symbols)} icons, {len(maps['fileExtensions'])} extensions, {len(maps['fileNames'])} file names, "
          f"{len(maps['languageIds'])} language ids, {len(maps['folderNames'])} folder names, {len(sprite) // 1024} KB sprite")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
