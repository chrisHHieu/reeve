"""The directions a design can take when the project has no DESIGN.md yet.

Asked to choose "a small system" from nothing, a model chooses the safest one it knows: one grey
sans, one blue, cards with shadows. The same model drew better pages in OpenDesign, whose prompt
hands it named schools of style with their fonts, colours and posture written out (read
2026-10-19). These are Reeve's own: six directions far enough apart that picking one is a decision,
each concrete enough to bind a page's `:root` to without improvising.

Fonts are families from the kit Reeve ships (`fonts.py`), copied beside the page, with a system
stack after each as the fallback: they render offline, so the check's screenshots and the preview
show the same page. Until 2026-10-20 they were system stacks alone, and every direction came out in
Segoe UI or Georgia on Windows. A direction is a starting point the DESIGN.md is written from; the
project's own DESIGN.md always wins over it.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Direction:
    id: str
    name: str
    mood: str
    use_for: str
    references: tuple[str, ...]
    display: str
    body: str
    mono: str
    #: ground, surface, ink, muted, line, accent, accent ink (text on the accent), second signal.
    palette: dict[str, str] = field(default_factory=dict)
    posture: tuple[str, ...] = ()
    #: The kit families the direction uses (`fonts.KIT` slugs), to copy beside the page.
    fonts: tuple[str, ...] = ()


DIRECTIONS: tuple[Direction, ...] = (
    Direction(
        id="quiet-product",
        name="Quiet product",
        mood="Precise and calm, software that feels shipped: crisp neutrals, one confident accent, detail in the small things.",
        use_for="SaaS, developer and productivity products, settings, most app screens",
        references=("Linear", "Vercel", "Stripe docs", "Raycast"),
        display="'Plus Jakarta Sans', system-ui, 'Segoe UI', sans-serif",
        body="'Be Vietnam Pro', system-ui, 'Segoe UI', sans-serif",
        mono="'JetBrains Mono', ui-monospace, Consolas, monospace",
        fonts=("plus-jakarta-sans", "be-vietnam-pro", "jetbrains-mono"),
        palette={
            "ground": "#fbfbfc", "surface": "#ffffff", "ink": "#16181d", "muted": "#5d6270",
            "line": "#e4e6eb", "accent": "#3b5bdb", "accent-ink": "#ffffff", "signal": "#0b7a5c",
        },
        posture=(
            "display sizes set tight (letter-spacing -0.02em), weight 600, never above 56 px",
            "hairline 1 px borders do the separating; shadows only on menus and dialogs",
            "numbers in tabular figures; small mono labels for metadata",
            "one product visual (a real-looking screen, a chart) beats three icons",
            "a sticky top bar with a light blur; content in a 1120 px column",
        ),
    ),
    Direction(
        id="editorial",
        name="Editorial",
        mood="A good magazine: big serif headlines, generous margins, ink on paper and one considered colour.",
        use_for="stories, reports, essays, publishing, portfolios, premium brands that sell with words",
        references=("Monocle", "FT Weekend", "The New Yorker online", "It's Nice That"),
        display="'Newsreader', 'Iowan Old Style', Georgia, serif",
        body="'Be Vietnam Pro', system-ui, 'Segoe UI', sans-serif",
        mono="'JetBrains Mono', ui-monospace, Consolas, monospace",
        fonts=("newsreader", "be-vietnam-pro", "jetbrains-mono"),
        palette={
            "ground": "#fbfaf7", "surface": "#ffffff", "ink": "#1d1b18", "muted": "#66615a",
            "line": "#e6e2da", "accent": "#a8322d", "accent-ink": "#ffffff", "signal": "#2f5d50",
        },
        posture=(
            "serif display at real size (48–72 px), one word or phrase of a headline in its italic, sans for body, mono for kickers",
            "no shadows and no rounded cards: rules, columns and white space do the work",
            "the accent appears at most twice per screen",
            "one strong image or figure per section, captioned",
            "a reading column of 640–720 px; pull quotes break it",
        ),
    ),
    Direction(
        id="warm-human",
        name="Warm and human",
        mood="Friendly and approachable: soft warm ground, rounded shapes, generous touch targets, a cheerful second colour.",
        use_for="consumer apps, health, learning, family, food, community, onboarding",
        references=("Headspace", "Duolingo web", "Airbnb", "Notion's marketing pages"),
        display="'Lora', Georgia, serif",
        body="'Be Vietnam Pro', system-ui, 'Segoe UI', sans-serif",
        mono="'JetBrains Mono', ui-monospace, Consolas, monospace",
        fonts=("lora", "be-vietnam-pro", "jetbrains-mono"),
        palette={
            "ground": "#fdf8f3", "surface": "#ffffff", "ink": "#2b2420", "muted": "#6f625a",
            "line": "#eee3d8", "accent": "#c0392f", "accent-ink": "#ffffff", "signal": "#1d746b",
        },
        posture=(
            "radius 16–24 px on cards and 999 px on buttons and chips",
            "soft, wide, low shadows (0 8px 24px at 6–8% ink) on raised cards",
            "illustration or photography placeholders sized for real images, never emoji",
            "body 17 px, buttons at least 48 px tall",
            "sections alternate the ground and a lightly tinted band",
        ),
    ),
    Direction(
        id="technical",
        name="Technical",
        mood="A tool for people who read logs: dense, exact, monospace details, status colours that mean something.",
        use_for="dashboards, monitoring, admin, infrastructure, data tables, terminals, developer consoles",
        references=("Grafana", "Datadog", "Sentry", "Fly.io"),
        display="'Space Grotesk', system-ui, 'Segoe UI', sans-serif",
        body="'IBM Plex Sans', system-ui, 'Segoe UI', sans-serif",
        mono="'JetBrains Mono', ui-monospace, Consolas, monospace",
        fonts=("space-grotesk", "ibm-plex-sans", "jetbrains-mono"),
        palette={
            "ground": "#f6f7f9", "surface": "#ffffff", "ink": "#111418", "muted": "#59606b",
            "line": "#dfe3e8", "accent": "#0b63c9", "accent-ink": "#ffffff", "signal": "#16733f",
        },
        posture=(
            "13–14 px body, 32–36 px rows; density is a feature, but every column aligns",
            "mono for ids, numbers, timestamps; tabular figures everywhere",
            "status in a small coloured dot plus a word: green ok, amber warn, red fail",
            "a left rail of 220–240 px and a top bar with search and a time range",
            "charts with thin lines, labelled axes, no 3D, no gradients under lines",
        ),
    ),
    Direction(
        id="bold-brand",
        name="Bold brand",
        mood="Confident marketing: huge type, solid colour blocks, high contrast, one memorable move per section.",
        use_for="launches, landing pages, campaigns, events, products that need to stand out",
        references=("Stripe Sessions", "Figma Config", "Spotify Wrapped", "Nike"),
        display="'Bricolage Grotesque', system-ui, 'Segoe UI', sans-serif",
        body="'Be Vietnam Pro', system-ui, 'Segoe UI', sans-serif",
        mono="'JetBrains Mono', ui-monospace, Consolas, monospace",
        fonts=("bricolage-grotesque", "be-vietnam-pro", "jetbrains-mono"),
        palette={
            "ground": "#ffffff", "surface": "#f4f3ef", "ink": "#141210", "muted": "#5a554d",
            "line": "#e6e2da", "accent": "#c4291c", "accent-ink": "#ffffff", "signal": "#ffc83d",
        },
        posture=(
            "display at 64–96 px, weight 800, letter-spacing -0.03em, two lines at most",
            "whole sections in a solid accent or ink colour, text reversed out",
            "the second signal colour (#ffc83d) only as a fill or a shape behind ink, never as text, once per section",
            "big numbers and short claims instead of paragraphs",
            "no gradient washes: colour comes in flat blocks",
        ),
    ),
    Direction(
        id="swiss-grid",
        name="Swiss grid",
        mood="International style: a strict grid, flush-left type, black and white with one red, nothing decorative.",
        use_for="agencies, architecture, exhibitions, documentation, annual reports, schedules",
        references=("Müller-Brockmann posters", "Vitsoe", "Swiss Federal Railways", "Experimental Jetset"),
        display="'Archivo', 'Helvetica Neue', Arial, sans-serif",
        body="'Archivo', 'Helvetica Neue', Arial, sans-serif",
        mono="'JetBrains Mono', ui-monospace, Consolas, monospace",
        fonts=("archivo", "jetbrains-mono"),
        palette={
            "ground": "#ffffff", "surface": "#f2f2f2", "ink": "#0a0a0a", "muted": "#5c5c5c",
            "line": "#d6d6d6", "accent": "#e3000f", "accent-ink": "#ffffff", "signal": "#0a0a0a",
        },
        posture=(
            "a visible 12-column grid; every edge lands on it",
            "flush left, ragged right; large numerals and tight headings (weight 700, -0.02em)",
            "no radius, no shadow; thick or hairline rules mark sections",
            "red only for the one thing that matters on the screen",
            "captions and metadata in small caps above what they label",
        ),
    ),
)


def directions_text() -> str:
    """The directions, written out for the guide: what each feels like, when it fits, its fonts,
    its palette with names, and how it lays a page out."""
    blocks = [
        "Directions (choose the one that fits the product and the brief; name it in your answer and "
        "offer another if the person wants a different feel):"
    ]
    for d in DIRECTIONS:
        palette = ", ".join(f"{name} {value}" for name, value in d.palette.items())
        blocks.append(
            f"\n{d.name} [{d.id}]: {d.mood}\n"
            f"  for: {d.use_for}. Like: {', '.join(d.references)}.\n"
            f"  display: {d.display}\n  body: {d.body}\n  mono: {d.mono}\n"
            f"  fonts to copy beside the page: {', '.join(d.fonts)}\n"
            f"  palette: {palette}\n"
            + "".join(f"  - {line}\n" for line in d.posture)
        )
    return "".join(blocks)


def direction_ids() -> list[str]:
    return [d.id for d in DIRECTIONS]
