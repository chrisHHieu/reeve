"""What the project's app already has, read from its files (step 4).

A design is built into an app that has its own framework, theme and components. What of that can be
read from the files without running anything is read here, by the harness, so neither the guide nor
the handoff leaves it to a model's guess: the framework and styling from `package.json`, the colours
and fonts the theme names (a Tailwind config's `colors`/`fontFamily`, a stylesheet's custom
properties, `@theme`), the component files, the folder for public files, and the scripts that run and
check it.

It is a reading, not an understanding: a colour named only in a component, or a theme built at run
time, is not found, and the result says what it read from. Pure file reads, bounded; never raises.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator, Optional

from .pages import SKIP_DIRS

#: The packages that say what an app is built with, in the order they are named.
FRAMEWORKS: tuple[tuple[str, str], ...] = (
    ("next", "Next.js"), ("nuxt", "Nuxt"), ("@remix-run/react", "Remix"), ("@sveltejs/kit", "SvelteKit"),
    ("astro", "Astro"), ("@angular/core", "Angular"), ("react", "React"), ("vue", "Vue"), ("svelte", "Svelte"),
    ("solid-js", "Solid"), ("preact", "Preact"), ("vite", "Vite"),
)
STYLING: tuple[tuple[str, str], ...] = (
    ("tailwindcss", "Tailwind"), ("styled-components", "styled-components"), ("@emotion/react", "Emotion"),
    ("@vanilla-extract/css", "vanilla-extract"), ("sass", "Sass"), ("@mui/material", "MUI"), ("@chakra-ui/react", "Chakra"),
    ("@mantine/core", "Mantine"), ("bootstrap", "Bootstrap"),
)
ROUTERS: tuple[tuple[str, str], ...] = (
    ("react-router-dom", "react-router"), ("react-router", "react-router"), ("@tanstack/react-router", "TanStack Router"),
    ("vue-router", "vue-router"),
)
_SCRIPTS = ("dev", "start", "build", "typecheck", "check", "lint", "test")
_CONFIGS = ("tailwind.config.ts", "tailwind.config.js", "tailwind.config.mjs", "tailwind.config.cjs")
_COMPONENT_DIRS = ("src/components", "components", "app/components", "src/ui", "src/lib/components", "src/app/components")
_PUBLIC_DIRS = ("public", "static", "src/assets")
_SUBAPPS = ("web", "frontend", "client", "app", "site", "ui")
_COMPONENT_SUFFIXES = (".tsx", ".jsx", ".vue", ".svelte", ".astro")
_MAX_FILE = 300_000
#: An app's main stylesheet can be large (Reeve's own is 400 KB); its tokens are at the top.
_MAX_CSS_FILE = 2_000_000
_MAX_CSS = 40
_MAX_COMPONENTS = 80
_HEX = re.compile(r"#(?:[0-9a-fA-F]{8}|[0-9a-fA-F]{6}|[0-9a-fA-F]{3})\b")
_CSS_VAR = re.compile(r"(--[\w-]+)\s*:\s*([^;}{]+)")
_FACE = re.compile(r"@font-face\s*{[^}]*font-family\s*:\s*['\"]?([^;'\"]+)", re.IGNORECASE)
_TOKEN = re.compile(r"""\s*(?:(['"])(?P<q>[^'"]+)\1|(?P<b>[\w$-]+))\s*:\s*""")


@dataclass
class AppToken:
    """A colour the app's theme names: `brand`, `paper.50`, `--color-ink`."""

    name: str
    value: str
    source: str


@dataclass
class AppFacts:
    """What was read. `root` is None when no app was found in the folder."""

    root: Optional[Path] = None
    shown_root: str = ""
    frameworks: list[str] = field(default_factory=list)
    styling: list[str] = field(default_factory=list)
    router: str = ""
    colors: list[AppToken] = field(default_factory=list)
    fonts: list[str] = field(default_factory=list)
    theme_files: list[str] = field(default_factory=list)
    components: list[str] = field(default_factory=list)
    component_dir: str = ""
    public_dir: str = ""
    scripts: dict[str, str] = field(default_factory=dict)
    package_manager: str = "npm"

    @property
    def found(self) -> bool:
        return self.root is not None

    def stack(self) -> str:
        """"React + Vite + Tailwind": what it is built with, in a few words."""
        return " + ".join(self.frameworks + self.styling) or "a web app"

    def token_for(self, color: str, tolerance: int = 3) -> list[str]:
        """The app's names for `color` (a `#rrggbb`), nearest spelling first."""
        rgb = _rgb(color)
        if rgb is None:
            return []
        out = []
        for token in self.colors:
            other = _rgb(token.value)
            if other is not None and all(abs(a - b) <= tolerance for a, b in zip(rgb, other)):
                out.append(token.name)
        return out

    def summary(self) -> dict[str, Any]:
        """What the lock dialog shows."""
        if not self.found:
            return {"found": False}
        return {
            "found": True,
            "root": self.shown_root,
            "stack": self.stack(),
            "router": self.router,
            "theme": self.theme_files[:3],
            "colors": len(self.colors),
            "fonts": self.fonts[:4],
            "components": len(self.components),
            "component_dir": self.component_dir,
            "scripts": {name: f"{self.package_manager} run {name}" for name in self.scripts},
        }


def _rgb(color: str) -> Optional[tuple[int, int, int]]:
    raw = color.strip().lstrip("#")
    if len(raw) == 3:
        raw = "".join(ch * 2 for ch in raw)
    if len(raw) not in (6, 8):
        return None
    try:
        return int(raw[0:2], 16), int(raw[2:4], 16), int(raw[4:6], 16)
    except ValueError:
        return None


def _read(path: Path, limit: int = _MAX_FILE) -> str:
    try:
        if path.is_file() and path.stat().st_size <= limit:
            return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        pass
    return ""


def _package(folder: Path) -> Optional[dict[str, Any]]:
    try:
        data = json.loads(_read(folder / "package.json") or "null")
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _app_folder(root: Path) -> Optional[Path]:
    """The folder holding the app: the root, else a usual sub-folder or `apps/*` with a web framework."""
    def is_app(folder: Path) -> bool:
        data = _package(folder)
        if data is None:
            return False
        deps = {**(data.get("dependencies") or {}), **(data.get("devDependencies") or {})}
        return any(name in deps for name, _ in FRAMEWORKS)

    if is_app(root):
        return root
    candidates = [root / name for name in _SUBAPPS]
    for holder in (root / "apps", root / "packages"):
        try:
            candidates += sorted(child for child in holder.iterdir() if child.is_dir())
        except OSError:
            pass
    return next((folder for folder in candidates if is_app(folder)), None)


def _object_body(text: str, key: str) -> Optional[str]:
    """The `{…}` after `key:` (the first one), braces balanced; None when there is none."""
    match = re.search(rf"\b{re.escape(key)}\s*:\s*{{", text)
    if not match:
        return None
    depth, start = 0, match.end() - 1
    for index in range(start, len(text)):
        if text[index] == "{":
            depth += 1
        elif text[index] == "}":
            depth -= 1
            if depth == 0:
                return text[start + 1 : index]
    return None


def _walk_object(body: str, prefix: str = "") -> Iterator[tuple[str, str]]:
    """`name: 'value'` pairs of a JS object literal, nested names joined with dots."""
    index = 0
    while index < len(body):
        match = _TOKEN.match(body, index)
        if not match:
            index += 1
            continue
        name = match.group("q") or match.group("b") or ""
        index = match.end()
        if index < len(body) and body[index] == "{":
            depth, start = 0, index
            for end in range(index, len(body)):
                if body[end] == "{":
                    depth += 1
                elif body[end] == "}":
                    depth -= 1
                    if depth == 0:
                        yield from _walk_object(body[start + 1 : end], f"{prefix}{name}.")
                        index = end + 1
                        break
            else:
                return
            continue
        value = re.match(r"\s*(['\"`])([^'\"`]*)\1", body[index:])
        if value:
            yield (f"{prefix}{name}".replace(".DEFAULT", ""), value.group(2))
            index += value.end()


def _tailwind(folder: Path, facts: AppFacts) -> None:
    for name in _CONFIGS:
        text = _read(folder / name)
        if not text:
            continue
        facts.theme_files.append(name)
        colors = _object_body(text, "colors")
        if colors:
            for token, value in _walk_object(colors):
                if _HEX.fullmatch(value.strip()):
                    facts.colors.append(AppToken(token, value.strip().lower(), name))
        families = _object_body(text, "fontFamily")
        if families:
            # The first of each stack, as written: `["Newsreader", …]` or `['"Be Vietnam Pro"', …]`.
            for match in re.finditer(r"\[\s*(['\"])(.+?)\1", families):
                family = match.group(2).strip().strip("'\"")
                if family and not family.startswith("var(") and family not in facts.fonts:
                    facts.fonts.append(family)
        return


#: Stylesheets named like a theme are read first: their tokens are the app's, a component's are its own.
_THEME_NAMES = re.compile(r"^(index|global|globals|theme|tokens|variables|vars|base|styles|app|main|root)\b", re.IGNORECASE)
#: Where a theme declares its tokens: the root, the document, Tailwind 4's `@theme`.
_THEME_BLOCK = re.compile(r"(?:^|[}\s;])(?::root|html|@theme)\b[^{]*{([^{}]*)}", re.IGNORECASE)


def _stylesheets(folder: Path) -> list[Path]:
    found: list[Path] = []
    for base in (folder / "src", folder / "app", folder / "styles", folder / "assets"):
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*.css")):
            if any(part in SKIP_DIRS for part in path.parts):
                continue
            found.append(path)
            if len(found) >= 10 * _MAX_CSS:
                break
    ranked = sorted(found, key=lambda path: (not _THEME_NAMES.match(path.stem), len(path.parts), path.as_posix()))
    return ranked[:_MAX_CSS]


def _css(folder: Path, facts: AppFacts) -> None:
    for path in _stylesheets(folder):
        text = _read(path, _MAX_CSS_FILE)
        if not text:
            continue
        rel = path.relative_to(folder).as_posix()
        found = False
        for block in _THEME_BLOCK.findall(text):
            for name, value in _CSS_VAR.findall(block):
                hexes = _HEX.findall(value)
                if hexes and value.strip().lower() == hexes[0].lower():
                    facts.colors.append(AppToken(name, hexes[0].lower(), rel))
                    found = True
        for family in _FACE.findall(text):
            family = family.strip()
            if family and family not in facts.fonts:
                facts.fonts.append(family)
                found = True
        if found and rel not in facts.theme_files:
            facts.theme_files.append(rel)


def _components(folder: Path, facts: AppFacts) -> None:
    for name in _COMPONENT_DIRS:
        base = folder / name
        if not base.is_dir():
            continue
        facts.component_dir = name
        for path in sorted(base.rglob("*")):
            if len(facts.components) >= _MAX_COMPONENTS:
                return
            if any(part in SKIP_DIRS for part in path.parts) or path.suffix not in _COMPONENT_SUFFIXES:
                continue
            stem = path.stem if path.stem != "index" else path.parent.name
            if (stem[:1].isupper() or path.suffix in (".vue", ".svelte")) and ".test" not in path.name and ".stories" not in path.name:
                if stem not in facts.components:
                    facts.components.append(stem)
        return


def read_app(root: Path) -> AppFacts:
    """What the app under `root` has, or `AppFacts()` when there is none to read."""
    try:
        folder = _app_folder(root.resolve())
    except OSError:
        folder = None
    if folder is None:
        return AppFacts()
    data = _package(folder) or {}
    deps = {**(data.get("dependencies") or {}), **(data.get("devDependencies") or {})}
    facts = AppFacts(root=folder)
    try:
        facts.shown_root = folder.relative_to(root.resolve()).as_posix() if folder != root.resolve() else "."
    except ValueError:
        facts.shown_root = str(folder)
    facts.frameworks = [label for name, label in FRAMEWORKS if name in deps and label not in facts.frameworks]
    # Next/Nuxt/SvelteKit/Remix/Astro bring their framework: "Next.js + React" says nothing more.
    if facts.frameworks and facts.frameworks[0] in ("Next.js", "Remix") and "React" in facts.frameworks:
        facts.frameworks.remove("React")
    if facts.frameworks and facts.frameworks[0] == "Nuxt" and "Vue" in facts.frameworks:
        facts.frameworks.remove("Vue")
    if facts.frameworks and facts.frameworks[0] == "SvelteKit" and "Svelte" in facts.frameworks:
        facts.frameworks.remove("Svelte")
    facts.styling = [label for name, label in STYLING if name in deps]
    facts.router = next((label for name, label in ROUTERS if name in deps), "")
    if not facts.router and "Next.js" in facts.frameworks:
        facts.router = "Next.js app/" if (folder / "app").is_dir() or (folder / "src" / "app").is_dir() else "Next.js pages/"
    scripts = data.get("scripts") or {}
    facts.scripts = {name: str(scripts[name]) for name in _SCRIPTS if isinstance(scripts, dict) and name in scripts}
    for lock, manager in (("pnpm-lock.yaml", "pnpm"), ("yarn.lock", "yarn"), ("bun.lockb", "bun"), ("bun.lock", "bun")):
        if (folder / lock).is_file() or (root / lock).is_file():
            facts.package_manager = manager
            break
    facts.public_dir = next((name for name in _PUBLIC_DIRS if (folder / name).is_dir()), "")
    _tailwind(folder, facts)
    _css(folder, facts)
    _components(folder, facts)
    return facts
