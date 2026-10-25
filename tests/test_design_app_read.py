"""What the project's app already has, read from its files, and how the design and the handoff use it
(step 4).

Pinned: the app is found at the root, in a usual sub-folder or under `apps/`; the stack, router,
scripts and package manager come from its package files; the theme's colours come from a Tailwind
config (nested names, `DEFAULT`, quoted keys, never a `var()`) and from the `:root`/`@theme` blocks of
its stylesheets (theme-like files first, a component's own variables not); fonts from `fontFamily`
and `@font-face`; components are the capitalised files of its component folder, not tests or
stories; the guide names them; the handoff says the app's token for each colour, a new token where
there is none, Tailwind's own white and black, and which fonts are new.
"""

from __future__ import annotations

import json
from pathlib import Path

from harness.tools.design.app_read import read_app
from harness.tools.design.check import DesignReport
from harness.tools.design.guide import app_section, guide_text
from harness.tools.design.handoff import Locked, handoff_markdown, lock_record, lock_summary

_TAILWIND = """import type { Config } from "tailwindcss";
export default {
  content: ["./src/**/*.tsx"],
  theme: {
    extend: {
      colors: {
        brand: "#1F5C4A",
        paper: { DEFAULT: "#fbf7f0", dark: '#f3ead9' },
        "ink-soft": "#5f5a52",
        accent: "var(--accent)",
      },
      fontFamily: { display: ["Newsreader", "serif"], body: ['"Be Vietnam Pro"', "sans-serif"], ui: ["var(--sans)"] },
    },
  },
} satisfies Config;
"""


def _app(root: Path, *, under: str = "web") -> Path:
    app = root / under if under else root
    (app / "src" / "components" / "pricing").mkdir(parents=True)
    (app / "public").mkdir()
    (app / "package.json").write_text(
        json.dumps(
            {
                "scripts": {"dev": "vite", "build": "vite build", "test": "vitest", "typecheck": "tsc --noEmit", "deploy": "x"},
                "dependencies": {"react": "18", "react-router-dom": "6"},
                "devDependencies": {"vite": "5", "tailwindcss": "3"},
            }
        ),
        encoding="utf-8",
    )
    (app / "pnpm-lock.yaml").write_text("", encoding="utf-8")
    (app / "tailwind.config.ts").write_text(_TAILWIND, encoding="utf-8")
    (app / "src" / "index.css").write_text(
        ':root { --accent: #c2410c; --radius: 8px; --line: #e6dfd3; --mix: color-mix(in srgb, #fff 10%, #000); }\n'
        '@font-face { font-family: "Newsreader"; src: url(/fonts/n.woff2); }\n',
        encoding="utf-8",
    )
    (app / "src" / "components" / "Card.css").write_text(".card { --card-ground: #123456; }", encoding="utf-8")
    for name in ("SiteHeader.tsx", "Button.tsx", "Button.test.tsx", "Button.stories.tsx", "helpers.ts", "pricing/index.tsx"):
        (app / "src" / "components" / name).write_text("export {}", encoding="utf-8")
    (app / "src" / "components" / "pricing" / "PriceCard.tsx").write_text("export {}", encoding="utf-8")
    return app


def test_the_app_is_read_from_its_files(tmp_path):
    _app(tmp_path)
    facts = read_app(tmp_path)
    assert facts.found and facts.shown_root == "web"
    assert facts.stack() == "React + Vite + Tailwind" and facts.router == "react-router"
    assert facts.scripts == {"dev": "vite", "build": "vite build", "typecheck": "tsc --noEmit", "test": "vitest"}
    assert facts.package_manager == "pnpm"
    assert [(token.name, token.value, token.source) for token in facts.colors] == [
        ("brand", "#1f5c4a", "tailwind.config.ts"),
        ("paper", "#fbf7f0", "tailwind.config.ts"),
        ("paper.dark", "#f3ead9", "tailwind.config.ts"),
        ("ink-soft", "#5f5a52", "tailwind.config.ts"),
        ("--accent", "#c2410c", "src/index.css"),
        ("--line", "#e6dfd3", "src/index.css"),
    ]
    assert facts.fonts == ["Newsreader", "Be Vietnam Pro"]
    assert facts.theme_files == ["tailwind.config.ts", "src/index.css"]
    assert facts.components == ["Button", "PriceCard", "SiteHeader"]
    assert facts.public_dir == "public" and facts.component_dir == "src/components"
    assert facts.token_for("#1F5C4B") == ["brand"] and facts.token_for("#000000") == []
    assert facts.summary()["scripts"]["test"] == "pnpm run test"


def test_the_app_is_found_where_projects_keep_it_and_not_where_there_is_none(tmp_path):
    assert not read_app(tmp_path).found
    (tmp_path / "package.json").write_text('{"dependencies": {"lodash": "4"}}', encoding="utf-8")
    assert not read_app(tmp_path).found
    _app(tmp_path, under="apps/site")
    assert read_app(tmp_path).shown_root == "apps/site"
    (tmp_path / "broken").mkdir()
    (tmp_path / "broken" / "package.json").write_text("{ not json", encoding="utf-8")
    assert read_app(tmp_path / "broken").found is False


def test_next_says_next_not_react_and_its_router(tmp_path):
    (tmp_path / "app").mkdir()
    (tmp_path / "package.json").write_text('{"dependencies": {"next": "15", "react": "19"}}', encoding="utf-8")
    facts = read_app(tmp_path)
    assert facts.frameworks == ["Next.js"] and facts.router == "Next.js app/"


def test_the_guide_names_the_apps_colors_fonts_and_components(tmp_path):
    _app(tmp_path)
    section = app_section(tmp_path)
    assert section.startswith("The app in this project (web, React + Vite + Tailwind), as Reeve read it from tailwind.config.ts, src/index.css:")
    assert "- Colors: brand #1f5c4a, paper #fbf7f0, paper.dark #f3ead9" in section
    assert "- Fonts: Newsreader, Be Vietnam Pro." in section
    assert "- Components in src/components: Button, PriceCard, SiteHeader." in section
    assert section in guide_text(tmp_path, [tmp_path])
    assert app_section(tmp_path / "web" / "public") == ""


def _locked(root: Path) -> Locked:
    system = {
        "width": 1440,
        "colors": {
            "background": [{"key": "#fbf7f0", "where": "body"}, {"key": "#ffffff", "where": "div.card"}, {"key": "#abcdef", "where": "aside"}],
            "text": [{"key": "#1f5c4a", "where": "h1"}],
        },
        "type": [
            {"family": "Newsreader", "size": 48, "line": 52.8, "weight": 700, "tag": "h1", "sample": "Plans"},
            {"family": "Inter", "size": 16, "line": "normal", "weight": 400, "tag": "p", "sample": "Words"},
        ],
    }
    page = root / "design" / "pricing.html"
    page.parent.mkdir(parents=True)
    page.write_text("<!doctype html>", encoding="utf-8")
    return Locked(page=page, shown="design/pricing.html", sha256="ab" * 32, report=DesignReport(shown="design/pricing.html", widths=[1440]),
                  system=[system], app=read_app(root))


def test_the_handoff_says_the_apps_token_for_each_color_and_which_fonts_are_new(tmp_path):
    _app(tmp_path)
    locked = _locked(tmp_path)
    text = handoff_markdown(locked, lock_record(locked, handoff="design/pricing.handoff.md", now=1_790_000_000))
    assert "## The app (read by Reeve from its files)" in text
    assert "- Where: `web` · React + Vite + Tailwind · routes by react-router" in text
    assert "- Run and check: `pnpm run dev`, `pnpm run build`, `pnpm run typecheck`, `pnpm run test`" in text
    assert "| Background | `#fbf7f0` | 0× | `body` | `paper` |" in text
    assert "| Background | `#ffffff` | 0× | `div.card` | `white` (Tailwind's own) |" in text
    assert "| Background | `#abcdef` | 0× | `aside` | **new token** |" in text
    assert "| Text | `#1f5c4a` | 0× | `h1` | `brand` |" in text
    assert "| Newsreader | 48 px | 52.8 px | 700 | 0 px | 0× `<h1>` | “Plans” | yes |" in text
    assert "| Inter | 16 px | normal | 400 | 0 px | 0× `<p>` | “Words” | **add it** |" in text
    app = lock_summary(locked)["app"]
    assert app["colors_new"] == 1 and app["fonts_new"] == ["Inter"] and app["stack"] == "React + Vite + Tailwind"


def test_without_an_app_the_handoff_has_no_app_columns(tmp_path):
    locked = _locked(tmp_path)
    text = handoff_markdown(locked, lock_record(locked, handoff="design/pricing.handoff.md", now=1_790_000_000))
    assert "## The app" not in text and "In the app" not in text
    assert lock_summary(locked)["app"] == {"found": False}
