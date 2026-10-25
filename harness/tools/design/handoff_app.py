"""The handoff's app half: which of the design's colours and fonts the app already has (step 4).

The lock measures the page; `app_read.read_app` reads the app's theme from its files. Held together,
the handoff can say, for each colour the page uses, the app token that already is that colour or that
a new token is needed, and the same for fonts, so the agent's first step (tokens into the theme) is
a list, not a search. Pure.
"""

from __future__ import annotations

from typing import Any

from .app_read import AppFacts


#: Colours Tailwind has whatever the theme says.
_TAILWIND_OWN = {"#ffffff": "white", "#000000": "black"}


def built_in(app: AppFacts, color: str) -> str:
    """Tailwind's own name for `color`, when the app is built with Tailwind and has one."""
    return _TAILWIND_OWN.get(color.lower(), "") if "Tailwind" in app.styling else ""


def color_cell(app: AppFacts, color: str) -> str:
    """The app's token(s) for `color`, or that one is needed."""
    names = app.token_for(color)
    if not names:
        own = built_in(app, color)
        return f"`{own}` (Tailwind's own)" if own else "**new token**"
    return ", ".join(f"`{name}`" for name in names[:2]) + (" …" if len(names) > 2 else "")


def has_font(app: AppFacts, family: str) -> bool:
    wanted = family.strip().strip("'\"").lower()
    return any(font.strip().lower() == wanted for font in app.fonts)


def font_cell(app: AppFacts, family: str) -> str:
    return "yes" if has_font(app, family) else "**add it**"


def app_lines(app: AppFacts) -> list[str]:
    """The handoff's "The app" section: what was read, and from where."""
    if not app.found:
        return []
    lines = [
        "",
        "## The app (read by Reeve from its files)",
        "",
        f"- Where: `{app.shown_root}` · {app.stack()}" + (f" · routes by {app.router}" if app.router else ""),
    ]
    if app.theme_files:
        lines.append("- Theme: " + ", ".join(f"`{name}`" for name in app.theme_files[:4]))
    if app.components:
        more = f" (and more; {len(app.components)} read)" if len(app.components) >= 40 else ""
        lines.append(f"- Components in `{app.component_dir}`: " + ", ".join(app.components[:40]) + more)
    if app.public_dir:
        lines.append(f"- Public files: `{app.public_dir}/` (font files go here)")
    if app.scripts:
        lines.append("- Run and check: " + ", ".join(f"`{app.package_manager} run {name}`" for name in app.scripts))
    lines.append(
        "- Colors and fonts below name the app's token where it already has one; a **new token** goes into"
        " the theme above before any component uses it."
    )
    return lines


def app_summary(app: AppFacts, colors: list[str], families: list[str]) -> dict[str, Any]:
    """The lock dialog's App row: what it is, and how many of the design's colours and fonts are new to it."""
    out = app.summary()
    if not app.found:
        return out
    out["colors_new"] = sum(1 for color in colors if not app.token_for(color) and not built_in(app, color))
    out["fonts_new"] = [family for family in families if not has_font(app, family)][:4]
    return out
