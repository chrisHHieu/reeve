"""How each kind of page is laid out, and the starting file a page is written from.

OpenDesign does not let a model start from a blank file: every kind of artifact has a skill with a
layout recipe and an example page the model reads before writing (read 2026-10-19). These are
Reeve's own: a recipe per kind of page, the order of its parts and what each part needs to carry,
and one starting file whose `:root` is bound to the DESIGN.md, with the base every page needs
(type scale, container, buttons with their states, focus, reduced motion) already right. The model
spends its effort on the design, not on re-deriving a reset.
"""

from __future__ import annotations

RECIPES: dict[str, str] = {
    "landing": """\
Landing page. You are its brand designer: one hero, three to six sections, the product's own
words, one decisive flourish and not three. Top to bottom:
1. Top bar: the wordmark, 3–5 links, one secondary and one primary action. Sticky.
2. Hero: a headline that says the outcome for the reader (not the feature list), one sentence
   under it, the primary action and a quieter second one, and the product itself beside or under
   it: a real-looking screen, a chart, the thing in use. Not an abstract blob.
3. Proof right after: named customers, a number with its unit and period, or a short quote with a
   name and a role.
4. Three to five capabilities, each with a specific claim and how it works, not a single word under
   an icon. Alternate sides or use a grid; vary the rhythm so it does not read as a template.
5. How it works or the product in more depth: steps, a larger screenshot, a comparison.
6. Social proof with people's names, roles and companies; then pricing or a pricing teaser.
7. FAQ: the five questions a buyer really asks, short answers.
8. A closing call to action that repeats the promise, then a footer with real columns.""",
    "pricing": """\
Pricing page. You are a product designer whose job is a decision: make comparing easy and the
recommended plan obvious by layout.
1. A heading that says who it is for, one line under it, and the billing switch (monthly / yearly,
   with what yearly saves) if there is one.
2. Two to four plans side by side (stacked on a phone). Each: name, who it is for in a line, the
   price with its unit and period, the main action, and what it adds over the plan before ("All of
   Free, plus…"). The recommended plan stands out by layout (raised, wider, a label), not by
   colour alone.
3. A comparison table under the plans for the details, grouped, with checks and real limits.
4. A row for larger buyers (talk to sales), the FAQ on billing, trials and cancelling, and trust
   lines (security, refunds) near the actions.""",
    "dashboard": """\
Dashboard. You are a systems designer: density is the feature, numbers in mono and tabular
figures, no decoration that is not data.
1. A left rail (220–240 px) with the product, the sections, and the person; a top bar with search,
   the time range and the main action.
2. A row of three to five numbers that matter, each with its unit, the change against the last
   period, and a tiny trend.
3. One main chart that answers the screen's question, labelled axes, a legend only if needed.
4. A table or list of the things behind the numbers: real-looking rows (names, ids, statuses,
   times), sortable headers, a status column with a dot and a word.
5. A side column for what needs attention: alerts, a short activity feed, next steps.
6. Draw the empty, loading and error states as pages beside it when the data can be missing.""",
    "app": """\
App screen (phone, 390 px wide unless asked otherwise). You are an interaction designer: real screens
of a normal day, the main action in thumb reach, never a "Feature one" screen.
1. A top bar with the screen's title and at most two actions; no fake status bar.
2. The content as the person would see it on a normal day: a list, cards, a detail. Real-looking
   names, times and amounts.
3. The main action within thumb reach (bottom bar or a large button), targets at least 44 px.
4. A tab bar if the app has sections, with the current one marked by more than colour.
5. One screen per file for a flow (`onboarding-1.html`, `onboarding-2.html`), linked to each other,
   so the person can click through.""",
    "slides": """\
Slides (one HTML page, each slide a 16:9 section of 1280×720 px stacked down the page). You are a
slide designer: one idea per slide, headlines 40 px and up, body 24 px and up, no three slides alike
in a row.
1. One idea per slide, said in its title as a sentence; a small kicker above it.
2. Big numbers with their units and what they compare to; charts with a takeaway in the title.
3. A consistent grid and margins on every slide; the same place for titles and page numbers.
4. Start with the problem or the result, not an agenda; end with the one thing to do or remember.
5. Speaker notes, when asked for, under each slide in an <aside class="notes">.""",
    "article": """\
Article, report or documentation page. You are its editor and typographer: the reading column is
the design.
1. A reading column of 640–720 px; a contents list beside it on wide screens, at the top on phones.
2. Title, a one-paragraph summary, the date and author or owner.
3. Headings that say what the section concludes; short paragraphs; lists for steps.
4. Figures and tables with captions; callouts for warnings and tips, used sparingly.
5. A clear end: next steps, related pages, or how to get help.""",
    "form": """\
Form, sign-up or onboarding. You are an interaction designer: the fewest fields, and errors that
say how to fix them.
1. One column, labels above the fields, the fewest fields that do the job.
2. Say why each sensitive field is asked; group long forms into steps with a step count.
3. The right input types and autocomplete; errors under the field in words that say how to fix it.
4. One primary action at the end that says what happens ("Create account"); a quiet way back.
5. Draw the success state as its own page beside it.""",
}

KINDS: tuple[str, ...] = tuple(RECIPES)

#: The file a design is written from: its `:root` takes the DESIGN.md's (or the chosen direction's)
#: values; the rest is the base every page needs. The model replaces the <main> and keeps the base.
STARTER = """\
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Page title</title>
<!-- The direction's fonts, copied beside the page from Reeve's kit (design_guide says how). -->
<link rel="stylesheet" href="fonts/plus-jakarta-sans/font.css">
<link rel="stylesheet" href="fonts/be-vietnam-pro/font.css">
<link rel="stylesheet" href="fonts/jetbrains-mono/font.css">
<style>
:root {
  /* From DESIGN.md (or the direction chosen): change these values, keep the names. */
  --ground: #fbfbfc; --surface: #ffffff; --ink: #16181d; --muted: #5d6270; --line: #e4e6eb;
  --accent: #3b5bdb; --accent-ink: #ffffff; --signal: #0b7a5c;
  --font-display: 'Plus Jakarta Sans', system-ui, sans-serif; --font-body: 'Be Vietnam Pro', system-ui, sans-serif;
  --font-mono: 'JetBrains Mono', ui-monospace, monospace;
  --radius: 10px; --radius-lg: 16px;
  --space-1: 4px; --space-2: 8px; --space-3: 12px; --space-4: 16px; --space-5: 24px; --space-6: 32px;
  --space-7: 48px; --space-8: 64px; --space-9: 96px;
  --step--1: 0.8125rem; --step-0: 1rem; --step-1: 1.25rem; --step-2: 1.5625rem; --step-3: 2rem;
  --step-4: 2.75rem; --step-5: 3.5rem;
  --ease: cubic-bezier(.2, .8, .2, 1);
}
*, *::before, *::after { box-sizing: border-box; }
html { -webkit-text-size-adjust: 100%; }
body { margin: 0; background: var(--ground); color: var(--ink); font: 400 var(--step-0)/1.55 var(--font-body); overflow-wrap: break-word; }
h1, h2, h3, h4 { font-family: var(--font-display); line-height: 1.12; letter-spacing: -0.015em; margin: 0 0 var(--space-4); text-wrap: balance; }
h1 { font-size: clamp(var(--step-3), 6vw, var(--step-5)); }
h2 { font-size: clamp(var(--step-2), 4vw, var(--step-4)); }
h3 { font-size: var(--step-1); }
p { margin: 0 0 var(--space-4); max-width: 68ch; text-wrap: pretty; }
small, .meta { font-size: var(--step--1); color: var(--muted); }
.num { font-variant-numeric: tabular-nums; }
a { color: inherit; text-underline-offset: 3px; }
img, svg { display: block; max-width: 100%; }
.wrap { width: min(1120px, 100% - 2 * var(--space-5)); margin-inline: auto; }
.stack > * + * { margin-top: var(--space-4); }
.row { display: flex; flex-wrap: wrap; gap: var(--space-4); align-items: center; }
.row > * { min-width: 0; }
header a, nav a { display: inline-flex; align-items: center; min-height: 44px; min-width: 44px; padding-inline: var(--space-2); overflow-wrap: anywhere; text-decoration: none; }
header a:hover, nav a:hover { text-decoration: underline; }
.grid { display: grid; gap: var(--space-5); grid-template-columns: repeat(auto-fit, minmax(min(260px, 100%), 1fr)); }
.surface { background: var(--surface); border: 1px solid var(--line); border-radius: var(--radius-lg); }
.btn {
  display: inline-flex; align-items: center; justify-content: center; gap: var(--space-2);
  min-height: 44px; padding: 0 var(--space-5); border-radius: var(--radius); border: 1px solid var(--line);
  background: var(--surface); color: var(--ink); font: 600 var(--step-0)/1.2 var(--font-body); text-decoration: none;
  max-width: 100%; text-align: center; overflow-wrap: anywhere;
  cursor: pointer; transition: background .15s var(--ease), border-color .15s var(--ease), transform .15s var(--ease);
}
.btn:hover { border-color: color-mix(in srgb, var(--ink) 30%, var(--line)); }
.btn:active { transform: translateY(1px); }
.btn.primary { background: var(--accent); border-color: var(--accent); color: var(--accent-ink); }
.btn.primary:hover { background: color-mix(in srgb, var(--accent) 88%, var(--ink)); }
.btn:disabled, .btn[aria-disabled="true"] { opacity: .55; cursor: not-allowed; }
:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; border-radius: 4px; }
/* The parts the layouts (design_guide kind=…) are built from. */
.section { padding-block: var(--space-8); }
@media (min-width: 900px) { .section { padding-block: var(--space-9); } }
.kicker { margin: 0 0 var(--space-3); font: 500 var(--step--1)/1.4 var(--font-mono); letter-spacing: 0.06em; text-transform: uppercase; color: var(--muted); }
.lead { font-size: var(--step-1); line-height: 1.5; color: var(--muted); max-width: 60ch; }
.split { display: grid; gap: var(--space-6); align-items: center; }
@media (min-width: 900px) { .split { grid-template-columns: 1.1fr 1fr; gap: var(--space-8); } }
.card { background: var(--surface); border: 1px solid var(--line); border-radius: var(--radius-lg); padding: var(--space-5); min-width: 0; }
.links { list-style: none; margin: 0; padding: 0; }
.links a { display: inline-flex; align-items: center; min-height: 44px; min-width: 44px; max-width: 100%; color: var(--muted); text-decoration: none; overflow-wrap: anywhere; }
.links a:hover, .links a[aria-current="page"] { color: var(--ink); }
.scroll-x { overflow-x: auto; }
table.data { width: 100%; border-collapse: collapse; font-size: var(--step--1); }
table.data th, table.data td { padding: var(--space-3); border-bottom: 1px solid var(--line); text-align: left; white-space: nowrap; }
table.data th { font-weight: 600; color: var(--muted); }
details.faq { border-bottom: 1px solid var(--line); }
details.faq summary { display: flex; align-items: center; min-height: 44px; padding-block: var(--space-3); font-weight: 600; cursor: pointer; list-style: none; }
details.faq summary::-webkit-details-marker { display: none; }
details.faq p { color: var(--muted); }
.placeholder { display: grid; place-items: center; margin: 0; aspect-ratio: 16 / 10; padding: var(--space-5); border-radius: var(--radius-lg); background: color-mix(in srgb, var(--ink) 6%, var(--surface)); color: var(--muted); font-size: var(--step--1); text-align: center; }
label { display: block; margin-bottom: var(--space-2); font-weight: 600; font-size: var(--step--1); }
input, select, textarea { width: 100%; min-height: 44px; padding: 0 var(--space-3); border: 1px solid var(--line); border-radius: var(--radius); background: var(--surface); color: var(--ink); font: inherit; }
textarea { min-height: 120px; padding-block: var(--space-3); }
.sr-only { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); white-space: nowrap; }
@media (prefers-reduced-motion: reduce) { *, *::before, *::after { transition: none !important; animation: none !important; } }
</style>
</head>
<body>
<header class="wrap row" style="justify-content: space-between; padding-block: var(--space-4)">
  <a href="#" style="font-weight: 700; text-decoration: none">Wordmark</a>
  <nav class="row" aria-label="Main"><a href="#">Product</a><a href="#">Pricing</a><a class="btn primary" href="#">Start</a></nav>
</header>
<main class="wrap">
  <!-- The design goes here. -->
</main>
</body>
</html>"""


def recipe_text(kind: str | None) -> str:
    """The recipe for `kind`, or the list of kinds there are when none fits."""
    if kind in RECIPES:
        return RECIPES[kind]
    return "Recipes by kind of page (ask design_guide with kind= for one): " + ", ".join(KINDS) + "."
