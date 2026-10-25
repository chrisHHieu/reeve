"""What `design_guide` tells the model before it draws.

Reeve ships no skills (a fresh install has none, `extensions/skills/roots.py`), so the craft a
design needs travels with the design tools instead: how a design is made here, the project's own
DESIGN.md (or a direction to write one from, `directions.py`), a recipe for the kind of page
(`recipes.py`), the file a page starts from, short rulebooks on the parts of a screen that first
drafts get wrong, and a last look before answering. The rules are Reeve's own words. The
measurable half of them is also what the check measures, so a rule the model forgets is caught
after the write; the half a browser cannot measure (taste, hierarchy, wording) is only here.

The same model drew plainer pages here than in OpenDesign (owner, 2026-10-19). Reading its prompt
showed why: it gives the model a designer's brief (named directions with fonts, colours and
posture; recipes and example pages; real copy), where this guide gave a list of mistakes to avoid
and rules that pulled toward grey (one font, one accent, no shadows, [PRICE] placeholders). The
guide now gives the brief too; the check still guards the mistakes.

A second comparison (owner, 2026-10-20: "is the prompt senior yet?") found what was still missing
against OpenDesign's charter: faces with character (every direction rendered in system fonts), paste-
ready section layouts, the tells of a machine-made page named one by one, a role per kind of page,
the system said before the first write, and a self-critique with a bar to clear rather than "fix
the weakest". The kit (`fonts.py`), the layouts (`layouts.py`), `MACHINE_MADE` and `LAST_LOOK`
are those.

The guide is read when asked for, not carried in every request: a coding session that never draws
a screen never pays for it.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable, Optional

from .app_read import read_app
from .directions import directions_text
from .fonts import kit_text
from .layouts import layout_text
from .recipes import RECIPES, STARTER, recipe_text
from .tokens import DESIGN_FILE, find_design_md

#: How much of a DESIGN.md the guide quotes; the rest is read with `read_file`.
_DESIGN_MD_CHARS = 8_000
#: How deep and how wide the search for a project's styles goes.
_SCAN_DEPTH = 4
_SCAN_FILES = 4_000
_SKIP_DIRS = frozenset(
    {".git", "node_modules", "dist", "build", "out", ".next", ".nuxt", "coverage", "vendor", ".venv",
     "venv", "__pycache__", "target", ".turbo", ".cache", "design"}
)
_STYLE_NAMES = ("tailwind.config", "theme.", "tokens.", "variables.", "_variables.", "colors.")
_STYLE_SUFFIXES = frozenset({".css", ".scss", ".sass", ".less", ".js", ".ts", ".mjs", ".cjs", ".json"})

HOW = """\
How a design is made in Reeve
- You are the designer here, for the medium asked: a slide designer for slides, an interaction
  designer for an app, a web designer for a site. Commit to one direction and carry it through every
  part; a page that tries every style reads as none. Make the first draft one you would show.
- A new page: before the first write, ask the brief in one `ask_user` call with `questions`, the
  ones the request, DESIGN.md and the app do not already answer, up to 10: who it is for, what a
  viewer should do next, the content that exists (names, prices, copy), a page to follow, the
  feeling or direction, density, light or dark, language, the states to draw (empty, loading,
  error), the device it is mostly seen on. Give each options with the one you would pick first and
  `recommended`; from 5 the person sees one form and answers only what they care about. A question
  left to you ("Up to you…"): choose, and say what you chose. Do not ask for a revision of a page
  that exists.
- Before the first write, say in one or two sentences the system you will use: the direction, the
  display and text faces, the ground and the accent, the layout. Then copy the fonts and write. The
  person can redirect you for the price of a sentence instead of a page.
- A design is one HTML page under `design/` at the project root (`design/pricing.html`), with its
  CSS and script inline, so it opens by itself with no build step. Assets go beside it and are
  linked by relative paths. It works offline: no CDN scripts, no remote images.
- Write it with the file tools. After every write Reeve opens the page in its own browser at 1440,
  834 and 390 px and appends what it measured to the write's result: parts that run off the window,
  text too faint to read, colors and fonts DESIGN.md does not name, controls too small to hit,
  controls with no name, script errors. Fix what it reports before you answer; `check_design`
  checks again on demand, at other widths too.
- Write the copy the product would have: specific, in its voice, with plausible names, numbers,
  prices and dates, never lorem ipsum or [PRICE] brackets, which make a design read as a wireframe.
  Facts the person gave are used as given. Say in your answer what you invented, so nobody ships an
  invented price as real. A plausible price, name or date is copy; a performance claim ("10× faster",
  "99.9% uptime", "trusted by 10,000 teams"), a customer's logo or a quote is a claim: use one only
  when the person gave it, and otherwise leave the section out rather than fake it. You cannot draw pictures: an image is the person's file, a chart
  or diagram you build in SVG, or a placeholder sized for the real image with a line saying what
  goes there. Icons are inline SVG with stroke icons (Lucide-style), never emoji.
- Reeve also clicks every control at 390 px: a menu or dialog that opens must move in and out, and
  close on Esc and on a click outside; a dialog takes the focus. It loads the page with longer text,
  and in dark mode when the page has dark styles. A page that loads data draws its other states as
  pages beside it, `name.loading.html`, `name.empty.html`, `name.error.html`, each checked too.
- The person can touch the preview. An attachment named "<file> · <part>" is a part they pointed
  at: its selector, text and HTML say which; change that part. "<file> · N comments" are pinned
  comments: answer each by its number, saying what you changed or why not. "<file> · text
  retyped" means they changed the words in the file themselves: read it again before editing.
- Give a design two to four tweaks: levers that cut across it (the accent, density, light or
  dark), never one per colour or per label. Declare them in the page and make the CSS read them:
  <script type="application/json" id="reeve-tweaks">{"accent": {"label": "Accent", "css":
  "--accent", "options": ["#74358c", "#0d6a8a"]}, "density": {"label": "Density", "attr":
  "data-density", "options": ["roomy", "compact"]}}</script>. "css" is a custom property on :root,
  "attr" an attribute on <html>; the first option or the page's own value is the default. The
  person moves them in the preview at once; "<file> · tweaks" asks you to keep the values chosen.
- Asked for options, make two or three pages side by side as `name.a.html`, `name.b.html`… that
  differ in one real decision each (layout, density, emphasis), and present them together.
- Say in the answer what you decided and why, in a sentence or two. Notes never go inside the page.
- A page meant to become app code is still drawn here first, then built with the app's own
  components; the design stays as the reference. The person locks the version to build from in its
  tab (Lock); Reeve then measures it into `<name>.handoff.md` beside it. You never lock a page or
  write a handoff. "<file> · handoff" asks you to build the locked page into the app from that file;
  then start the app and call `compare_design` with its route and the page, fix what it reports, and
  call it again before saying the work is done.
  A check that says a page is no longer its locked version: say so in your answer.
"""

#: The tells of a page drawn by default, after OpenDesign's anti-slop list (read 2026-10-20) and
#: Reeve's own check: each is a choice nobody made.
MACHINE_MADE = """\
What makes a page read as machine-made. None of these unless the brief or DESIGN.md asks for it:
- a purple or violet gradient wash, a gradient on every section, glass panels by default;
- emoji as icons, an icon beside every heading, three identical icon-title-text cards as the idea;
- a rounded card with a thick coloured stripe down one side;
- Inter, Roboto or Arial as the display face; headings and text in the same family and weight;
- a beige, cream or peach ground the direction did not choose;
- invented proof: performance numbers, customer logos, quotes nobody gave you;
- "Feature one", lorem ipsum, [PRICE], or a [[slot]] left from a layout;
- everything centred, every section the same height, the same card three times down the page;
- hand-drawn SVG people, faces or scenery (a placeholder sized for the real picture instead);
- designer controls inside the product: viewport switches, theme pickers, "demo" badges (tweaks go
  in reeve-tweaks, never in the page's own UI).
"""

DESIGN_MD_SHAPE = """\
DESIGN.md sections, each short and concrete:
1. Look: two or three words for the style, and what the product should feel like.
2. Color: each color with a name, its hex, and where it goes (ground, panels, text, muted text,
   lines, the one accent, success, warning, danger). Text colors with their contrast on the ground.
3. Type: the families (one, two at most), the size scale, weights, line heights.
4. Spacing and shape: the spacing steps (4/8 based), radii, borders, shadows if any.
5. Layout: max widths, the grid, how wide screens and phones differ.
6. Components the app already has (buttons, inputs, cards…) and how each looks and behaves.
7. Motion: durations and easing; what moves and what never does.
8. Voice: how buttons, errors and empty states are worded.
"""

RULES: dict[str, str] = {
    "layout": """\
Layout
- One clear first thing per screen: the heading or the main action is found in under a second.
  Hierarchy comes from size, weight and space before color, and before borders or shadows.
- Space on a 4/8 px scale; more space between groups than inside them. Group with space first.
- Text measure 45–75 characters; body 15–17 px; line height 1.4–1.6 for body, tighter for headings.
- Containers have a max width (about 1100–1280 px for content) and fluid insides: grid or flex with
  gap, minmax() and wrap, not fixed pixel widths. Nothing runs off a 390 px window: columns stack,
  toolbars wrap, wide tables scroll inside their own box.
- Align to one grid; avoid one-off offsets. Equal things are equal in size.
""",
    "type": """\
Type
- The direction's families: a display face for headings and a text face for reading, or one family
  in two weights. No font the page cannot load offline unless DESIGN.md names it.
- A size scale with clear steps (for example 12/14/16/20/24/32/40); no sizes between steps.
- Numbers that change or line up use tabular figures. Don't change letter spacing of body text;
  ALL CAPS labels get a little tracking (0.04–0.08em) and stay short.
- Headings say what the section is. No orphaned one-word last lines in headings when avoidable.
""",
    "color": """\
Color
- Colors come from DESIGN.md: the ground, surfaces, ink, muted ink, lines, the accent for the main
  action and the current item, one second signal, and status colors. A palette that is all grey
  reads as unfinished; a palette on every card reads as noise. Shadows, tints and gradients only as
  the direction's posture allows, and never a rounded card with a thick colored stripe down one side.
- Text contrast 4.5:1 (3:1 from 24 px, or 18.66 px bold); muted text still passes. Placeholder and
  disabled text are the only exceptions.
- Color never carries meaning alone: an error also has an icon or words; a status chip also has a
  label. Things that must be told apart also differ in lightness, not only hue.
- Dark mode is its own palette (lighter accents, raised surfaces lighter, not inverted colors).
""",
    "states": """\
States
- Every view that loads data has: loading (a skeleton the size of what comes, so nothing jumps),
  empty (one sentence and the one next action), error (what happened and how to fix it, with a
  retry), and the full state. Lists also have one item and very many items.
- Every control has hover, focus-visible, active, disabled and, where it applies, busy states.
  Focus is always visible. A busy button keeps its width and cannot be pressed twice.
- Long text wraps or truncates with an ellipsis and the full text on hover; it never overflows.
""",
    "interaction": """\
Interaction
- Every click answers within 100 ms: a pressed state, a change, or a busy state.
- Menus, dialogs, drawers and toasts animate in AND out (150–250 ms, ease-out, opacity and
  transform only; never width, height, top or left). Respect prefers-reduced-motion.
- Esc and a click outside close menus and dialogs; focus moves into a dialog and back after.
- Destructive actions say what they destroy ("Delete note", not "OK") and offer undo or a confirm.
- Forms: one column, labels above fields, the right input type and autocomplete, errors under the
  field in words that say how to fix it, shown on blur or submit, not on every keystroke.
""",
    "access": """\
Access
- Real elements: <button> for actions, <a href> for navigation, <label> for every field. Never a
  clickable <div>. Icon-only buttons get aria-label; decorative SVG gets aria-hidden.
- Targets at least 44 px on touch screens, 24 px elsewhere. Tab order follows reading order.
- Images have alt text that says what matters in them, or alt="" when decorative.
- The page still works at 200% zoom and with a screen reader reading top to bottom.
""",
    "words": """\
Words
- Buttons say what happens: "Save changes", "Create account", "Delete note". Never "OK", "Submit",
  "Click here".
- One name per thing across the whole product (Sign in everywhere, never Log in on one screen).
- Errors say what happened and what to do, without blame. Empty states say what will be here and
  the one action that fills it.
- Short sentences, sentence case, no exclamation marks.
""",
}

TOPICS: tuple[str, ...] = tuple(RULES)

#: The last look before the answer: the half of a design the check cannot measure, scored against a
#: bar (OpenDesign's five-axis critique, read 2026-10-20; theirs fixes under 3, this asks for 4).
LAST_LOOK = """\
Before you answer, score the page silently from 1 to 5 on each, as its designer, at the width it is
mostly seen at:
1. Direction: it looks like the direction chosen, not like the default a model drifts back to.
2. Hierarchy: the eye lands in one place per screen, and everything else is quieter.
3. Execution: sizes and gaps on the scale, edges on one grid, contrast and alignment right, not close.
4. Specificity: every word, number and picture belongs to this product and this brief.
5. Restraint: the accent at most twice per screen, one decisive flourish (a headline treatment, one
   entrance, one real picture) and not three, nothing from the machine-made list.
Anything under 4 is not done: fix the weakest, score again; two passes is normal. The check after
each write covers what can be measured; this covers the rest.
"""


def _style_sources(root: Path) -> list[str]:
    """Files under `root` a DESIGN.md can be drafted from: Tailwind and theme configs, and the
    stylesheets that declare custom properties. Bounded in depth and count, skipping dependencies."""
    found: list[str] = []
    seen = 0
    base_depth = len(root.parts)
    for current, dirs, files in os.walk(root):
        depth = len(Path(current).parts) - base_depth
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS and not d.startswith(".")] if depth < _SCAN_DEPTH else []
        for name in files:
            seen += 1
            if seen > _SCAN_FILES or len(found) >= 8:
                return found
            path = Path(current) / name
            lower = name.lower()
            if path.suffix.lower() not in _STYLE_SUFFIXES:
                continue
            if lower.startswith(_STYLE_NAMES):
                found.append(path.relative_to(root).as_posix())
                continue
            if path.suffix.lower() in (".css", ".scss", ".sass", ".less"):
                try:
                    with path.open("r", encoding="utf-8", errors="replace") as handle:
                        head = handle.read(20_000)
                except OSError:
                    continue
                if ":root" in head or "--" in head and "var(" in head:
                    found.append(path.relative_to(root).as_posix())
    return found


def design_md_section(workspace: Path, roots: Iterable[Path]) -> str:
    """What the guide says about the project's DESIGN.md: its text, or how to draft one."""
    roots = list(roots) or [workspace]
    design_md = find_design_md(workspace / "design", roots) or find_design_md(workspace, roots)
    if design_md is not None:
        try:
            text = design_md.read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        try:
            shown = design_md.relative_to(workspace).as_posix()
        except ValueError:
            shown = str(design_md)
        if text:
            clipped = text[:_DESIGN_MD_CHARS]
            more = (
                f"\n[… {len(text) - _DESIGN_MD_CHARS:,} more characters: read_file {shown}]"
                if len(text) > _DESIGN_MD_CHARS
                else ""
            )
            return (
                f"This project's {shown} — follow it; the check holds the page to its colors and fonts:\n"
                f"{clipped}{more}\n"
            )
    sources = _style_sources(workspace)
    if sources:
        listed = "\n".join(f"- {path}" for path in sources)
        return (
            f"This project has no {DESIGN_FILE}. Draft one at the project root from the styles the app already "
            f"uses before drawing, so the design matches the app, and say you did:\n{listed}\n\n{DESIGN_MD_SHAPE}\n"
            "If the app's styles are thin, take what they have and fill the rest from the direction below "
            "that is closest to them.\n\n" + directions_text()
        )
    return (
        f"This project has no {DESIGN_FILE} and no styles to draft one from. Pick the direction below that "
        "fits the product and the brief, write DESIGN.md at the project root from it (its palette with each "
        "colour's name and use, its fonts, its posture), and say which you chose and that another is one "
        f"sentence away.\n\n{DESIGN_MD_SHAPE}\n" + directions_text()
    )


#: How many of the app's colours and components the guide names.
_APP_COLORS, _APP_COMPONENTS = 16, 24


def app_section(workspace: Path) -> str:
    """The app this project already is, as Reeve read it from its files (`app_read.py`): a page
    meant for it is drawn in its colours and fonts, so building it needs no new tokens. Nothing
    when there is no app to read."""
    facts = read_app(workspace)
    if not facts.found or not (facts.colors or facts.fonts or facts.components):
        return ""
    lines = [
        f"The app in this project ({facts.shown_root}, {facts.stack()}), as Reeve read it from"
        f" {', '.join(facts.theme_files[:3]) or 'its package.json'}:"
    ]
    seen: list[str] = []
    named: list[str] = []
    for token in facts.colors:
        if token.value in seen:
            continue
        seen.append(token.value)
        named.append(f"{token.name} {token.value}")
        if len(named) == _APP_COLORS:
            break
    if named:
        more = len({token.value for token in facts.colors}) - len(named)
        lines.append("- Colors: " + ", ".join(named) + (f", and {more} more" if more > 0 else "") + ".")
    if facts.fonts:
        lines.append("- Fonts: " + ", ".join(facts.fonts[:6]) + ".")
    if facts.components:
        lines.append(f"- Components in {facts.component_dir}: " + ", ".join(facts.components[:_APP_COMPONENTS]) + ".")
    lines.append(
        "A page meant for this app is drawn in these colors and fonts, so building it needs no new tokens;"
        " use a color or font it does not have only where the design needs one, and say so. Draw the parts"
        " the app already has (its header, buttons, cards) the way it draws them."
    )
    return "\n".join(lines)


def guide_text(
    workspace: Path, roots: Iterable[Path], topics: Optional[Iterable[str]] = None, kind: Optional[str] = None
) -> str:
    """The guide: how designs are made here, the project's DESIGN.md (or the directions to write one
    from), the recipe for `kind`, the file a page starts from, the rules asked for (all of them when
    none are named), and the last look before answering."""
    wanted = [topic for topic in (topics or []) if topic in RULES] or list(TOPICS)
    parts = [HOW, MACHINE_MADE, design_md_section(workspace, roots), app_section(workspace), kit_text(), recipe_text(kind)]
    parts += [
        "Start each page from this file: bind its :root to DESIGN.md, link the fonts it uses (copied "
        "beside it), keep its base, and replace the <main> with the layout for the kind of page.\n" + STARTER
    ]
    layout = layout_text(kind)
    if layout:
        parts.append(layout)
    parts += ["Craft rules"] + [RULES[topic] for topic in wanted] + [LAST_LOOK]
    return "\n".join(part.rstrip() + "\n" for part in parts)


KIND_NAMES: tuple[str, ...] = tuple(RECIPES)
