"""Paste-ready skeletons for each kind of page.

OpenDesign's prompt names the single biggest reason its pages look designed: the model does not
write CSS and structure from scratch, it copies a seed and pastes section layouts (read
2026-10-19). Reeve had the seed (`recipes.STARTER`) and recipes in prose; these are the layouts.
Each is the `<main>` (and what goes around it) of one kind of page, built only from the starter's
classes, so it is responsive and passes the check as it stands; the model fills it and makes it its
own. Every `[[…]]` is a slot: what goes there, in words. The check names any slot left in a page.

A layout is structure, not the look: the direction and DESIGN.md decide type, colour and posture,
and the model is expected to change the rhythm (swap the sides of a split, drop a section, add the
one flourish) so two pages of the same kind do not read as one template.
"""

from __future__ import annotations

LAYOUTS: dict[str, str] = {
    "landing": """\
<main>
  <section class="wrap section split">
    <div class="stack">
      <p class="kicker">[[the product, in two to four words]]</p>
      <h1>[[the outcome for the reader, 4–9 words]]</h1>
      <p class="lead">[[one sentence: who it is for and what changes for them]]</p>
      <div class="row">
        <a class="btn primary" href="#start">[[main action, verb first]]</a>
        <a class="btn" href="#how">[[the quieter action]]</a>
      </div>
      <p class="meta">[[what lowers the risk: a free plan, no card, cancel any time]]</p>
    </div>
    <figure class="placeholder" role="img" aria-label="[[what the product shot shows]]">[[the product in use: a real-looking screen, 16:10]]</figure>
  </section>
  <section class="wrap section" aria-label="Customers">
    <p class="kicker">[[who uses it]]</p>
    <ul class="row links" style="gap: var(--space-6)">
      <li>[[customer]]</li><li>[[customer]]</li><li>[[customer]]</li><li>[[customer]]</li>
    </ul>
  </section>
  <section class="wrap section stack" id="how">
    <h2>[[what it does, said as a result]]</h2>
    <div class="split">
      <div class="stack"><h3>[[capability one, a specific claim]]</h3><p>[[how it works, two sentences]]</p></div>
      <figure class="placeholder" role="img" aria-label="[[what this picture shows]]">[[a close-up of that part of the product]]</figure>
    </div>
    <div class="split">
      <figure class="placeholder" role="img" aria-label="[[what this picture shows]]">[[a close-up of that part of the product]]</figure>
      <div class="stack"><h3>[[capability two]]</h3><p>[[how it works, two sentences]]</p></div>
    </div>
  </section>
  <section class="wrap section">
    <figure class="card stack" style="margin: 0">
      <blockquote style="margin: 0"><p class="lead">[[a quote the person gave you, or none: leave the section out]]</p></blockquote>
      <figcaption class="meta">[[name, role, company]]</figcaption>
    </figure>
  </section>
  <section class="wrap section stack" aria-labelledby="faq">
    <h2 id="faq">[[Questions]]</h2>
    <details class="faq"><summary>[[a question buyers really ask]]</summary><p>[[a short, direct answer]]</p></details>
    <details class="faq"><summary>[[a question buyers really ask]]</summary><p>[[a short, direct answer]]</p></details>
    <details class="faq"><summary>[[a question buyers really ask]]</summary><p>[[a short, direct answer]]</p></details>
  </section>
  <section class="section" id="start" style="background: var(--ink); color: var(--ground)">
    <div class="wrap stack">
      <h2>[[the promise again, shorter]]</h2>
      <div class="row"><a class="btn primary" href="[[the page it opens]].html">[[main action]]</a></div>
    </div>
  </section>
</main>
<footer class="wrap section">
  <div class="grid">
    <div><p class="kicker">[[group]]</p><ul class="links"><li><a href="[[page]].html">[[link]]</a></li><li><a href="[[page]].html">[[link]]</a></li></ul></div>
    <div><p class="kicker">[[group]]</p><ul class="links"><li><a href="[[page]].html">[[link]]</a></li><li><a href="[[page]].html">[[link]]</a></li></ul></div>
  </div>
  <p class="meta">[[© year, company]]</p>
</footer>""",
    "pricing": """\
<main class="wrap">
  <section class="section stack" style="text-align: center; justify-items: center; display: grid">
    <p class="kicker">[[the product · pricing]]</p>
    <h1>[[who it is for and the promise, 4–9 words]]</h1>
    <p class="lead">[[one line: how the plans differ, and what yearly saves]]</p>
    <div class="row" role="group" aria-label="Billing">
      <button class="btn primary" type="button" aria-pressed="true">[[Monthly]]</button>
      <button class="btn" type="button" aria-pressed="false">[[Yearly, the saving]]</button>
    </div>
  </section>
  <section class="grid" aria-label="Plans">
    <article class="card stack">
      <h2 style="font-size: var(--step-1)">[[plan]]</h2>
      <p class="meta">[[who it is for, one line]]</p>
      <p><span class="num" data-month="[[price]]" data-year="[[price, billed yearly]]" style="font: 700 var(--step-4)/1 var(--font-display)">[[price]]</span> <span class="meta">[[/ month]]</span></p>
      <a class="btn" href="[[the page it opens]].html">[[start action]]</a>
      <ul class="stack" style="padding-left: 1.1em"><li>[[what it includes]]</li><li>[[what it includes]]</li></ul>
    </article>
    <article class="card stack" style="border-color: var(--ink); box-shadow: 0 0 0 1px var(--ink)">
      <p class="kicker" style="color: var(--ink)">[[Recommended]]</p>
      <h2 style="font-size: var(--step-1)">[[plan]]</h2>
      <p class="meta">[[who it is for]]</p>
      <p><span class="num" data-month="[[price]]" data-year="[[price, billed yearly]]" style="font: 700 var(--step-4)/1 var(--font-display)">[[price]]</span> <span class="meta">[[/ month]]</span></p>
      <a class="btn primary" href="[[the page it opens]].html">[[start action]]</a>
      <ul class="stack" style="padding-left: 1.1em"><li>[[all of the plan before, plus…]]</li><li>[[what it adds]]</li></ul>
    </article>
    <article class="card stack">
      <h2 style="font-size: var(--step-1)">[[plan]]</h2>
      <p class="meta">[[who it is for]]</p>
      <p><span class="num" data-month="[[price]]" data-year="[[price, billed yearly]]" style="font: 700 var(--step-4)/1 var(--font-display)">[[price]]</span> <span class="meta">[[/ month]]</span></p>
      <a class="btn" href="[[the page it opens]].html">[[talk to sales]]</a>
      <ul class="stack" style="padding-left: 1.1em"><li>[[what it adds]]</li><li>[[what it adds]]</li></ul>
    </article>
  </section>
  <section class="section stack">
    <h2>[[Compare the plans]]</h2>
    <div class="scroll-x">
      <table class="data">
        <thead><tr><th scope="col">[[Feature]]</th><th scope="col">[[plan]]</th><th scope="col">[[plan]]</th><th scope="col">[[plan]]</th></tr></thead>
        <tbody><tr><th scope="row">[[a limit people compare]]</th><td>[[value]]</td><td>[[value]]</td><td>[[value]]</td></tr></tbody>
      </table>
    </div>
  </section>
  <section class="section stack" aria-labelledby="faq">
    <h2 id="faq">[[Billing questions]]</h2>
    <details class="faq"><summary>[[trials, refunds, cancelling, seats]]</summary><p>[[a short answer]]</p></details>
    <details class="faq"><summary>[[a question]]</summary><p>[[a short answer]]</p></details>
  </section>
  <script>
    // Monthly or yearly: the pressed button decides which price every plan shows.
    document.querySelectorAll('[aria-label="Billing"] button').forEach((button, i, all) => {
      button.addEventListener('click', () => {
        all.forEach((other) => {
          other.setAttribute('aria-pressed', String(other === button));
          other.classList.toggle('primary', other === button);
        });
        document.querySelectorAll('[data-month]').forEach((price) => {
          price.textContent = i ? price.dataset.year : price.dataset.month;
        });
      });
    });
  </script>
</main>""",
    "dashboard": """\
<style>
  .dash { display: grid; min-height: 100vh; }
  .dash > aside { padding: var(--space-4); border-bottom: 1px solid var(--line); }
  @media (min-width: 900px) { .dash { grid-template-columns: 232px 1fr; } .dash > aside { border-bottom: 0; border-right: 1px solid var(--line); } }
  .dash > main { padding: var(--space-5); min-width: 0; }
  .kpi .num { font: 600 var(--step-3)/1.1 var(--font-display); }
  .dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin-right: var(--space-2); background: var(--signal); }
</style>
<div class="dash">
  <aside>
    <p style="font-weight: 700">[[product]]</p>
    <nav aria-label="Sections"><ul class="links"><li><a href="[[this screen]].html" aria-current="page">[[section]]</a></li><li><a href="[[screen]].html">[[section]]</a></li><li><a href="[[screen]].html">[[section]]</a></li></ul></nav>
  </aside>
  <main class="stack">
    <div class="row" style="justify-content: space-between">
      <h1 style="font-size: var(--step-2); margin: 0">[[the screen's question, as a title]]</h1>
      <div class="row">
        <label class="sr-only" for="range">Time range</label>
        <select id="range" style="width: auto"><option>[[Last 7 days]]</option></select>
        <a class="btn primary" href="[[the page it opens]].html">[[main action]]</a>
      </div>
    </div>
    <section class="grid" aria-label="Key numbers">
      <article class="card kpi"><p class="meta">[[metric]]</p><p class="num">[[value with unit]]</p><p class="meta">[[change against last period]]</p></article>
      <article class="card kpi"><p class="meta">[[metric]]</p><p class="num">[[value with unit]]</p><p class="meta">[[change]]</p></article>
      <article class="card kpi"><p class="meta">[[metric]]</p><p class="num">[[value with unit]]</p><p class="meta">[[change]]</p></article>
    </section>
    <section class="card stack" aria-label="[[chart title]]">
      <h2 style="font-size: var(--step-1)">[[what the chart says, as a sentence]]</h2>
      <svg viewBox="0 0 600 160" role="img" aria-label="[[the trend in words]]" style="width: 100%; height: auto">
        <polyline fill="none" stroke="currentColor" stroke-width="2" points="[[x,y pairs]]"/>
      </svg>
    </section>
    <section class="card stack" aria-label="[[table title]]">
      <h2 style="font-size: var(--step-1)">[[the things behind the numbers]]</h2>
      <div class="scroll-x">
        <table class="data">
          <thead><tr><th scope="col">[[Name]]</th><th scope="col">[[Status]]</th><th scope="col">[[Updated]]</th></tr></thead>
          <tbody><tr><td>[[a real-looking name]]</td><td><span class="dot" aria-hidden="true"></span>[[status word]]</td><td class="num">[[time]]</td></tr></tbody>
        </table>
      </div>
    </section>
  </main>
</div>""",
    "app": """\
<style>
  .phone { max-width: 390px; min-height: 100vh; margin-inline: auto; display: grid; grid-template-rows: auto 1fr auto; background: var(--surface); }
  .phone > header { display: flex; align-items: center; justify-content: space-between; padding: var(--space-3) var(--space-4); }
  .phone > main { padding: 0 var(--space-4); }
  .phone > nav { display: grid; grid-template-columns: repeat(4, 1fr); border-top: 1px solid var(--line); }
  .phone > nav a { display: grid; place-items: center; min-height: 56px; font-size: var(--step--1); color: var(--muted); text-decoration: none; }
  .phone > nav a[aria-current="page"] { color: var(--ink); font-weight: 600; }
  .item { display: flex; justify-content: space-between; gap: var(--space-3); padding-block: var(--space-3); border-bottom: 1px solid var(--line); }
</style>
<div class="phone">
  <header>
    <h1 style="font-size: var(--step-2); margin: 0">[[the screen's title]]</h1>
    <button class="btn" type="button" aria-label="[[what the icon does]]">[[icon as inline SVG]]</button>
  </header>
  <main class="stack">
    <p class="meta">[[what this screen shows today, one line]]</p>
    <ul class="links">
      <li class="item"><div><p style="margin: 0; font-weight: 600">[[a real-looking item]]</p><p class="meta" style="margin: 0">[[detail]]</p></div><span class="num">[[amount or time]]</span></li>
      <li class="item"><div><p style="margin: 0; font-weight: 600">[[a real-looking item]]</p><p class="meta" style="margin: 0">[[detail]]</p></div><span class="num">[[amount or time]]</span></li>
    </ul>
    <a class="btn primary" href="[[the screen it opens]].html" style="width: 100%">[[the main action, in thumb reach]]</a>
  </main>
  <nav aria-label="Sections">
    <a href="[[this screen]].html" aria-current="page">[[tab]]</a><a href="[[screen]].html">[[tab]]</a><a href="[[screen]].html">[[tab]]</a><a href="[[screen]].html">[[tab]]</a>
  </nav>
</div>""",
    "slides": """\
<style>
  .slide { position: relative; width: min(1280px, 100%); aspect-ratio: 16 / 9; margin: 0 auto var(--space-6); padding: clamp(16px, 5%, 72px); background: var(--surface); border: 1px solid var(--line); display: grid; align-content: center; gap: var(--space-4); overflow: hidden; }
  .slide h2 { font-size: clamp(24px, 4.6vw, 64px); margin: 0; }
  .slide .big { font: 700 clamp(40px, 9vw, 140px)/1 var(--font-display); margin: 0; }
  .slide .page { position: absolute; right: 4%; bottom: 4%; font: var(--step--1) var(--font-mono); color: var(--muted); }
</style>
<main class="section">
  <section class="slide" aria-label="Slide 1">
    <p class="kicker">[[the talk, the date]]</p>
    <h2>[[the one result or problem, as a sentence]]</h2>
    <span class="page">1</span>
  </section>
  <section class="slide" aria-label="Slide 2">
    <p class="kicker">[[what the number is]]</p>
    <p class="big num">[[the number with its unit]]</p>
    <p class="lead">[[what it compares to]]</p>
    <span class="page">2</span>
  </section>
  <section class="slide" aria-label="Slide 3">
    <h2>[[the one thing to do or remember]]</h2>
    <span class="page">3</span>
  </section>
</main>""",
    "article": """\
<main class="wrap section">
  <article class="stack" style="max-width: 70ch; margin-inline: auto">
    <p class="kicker">[[section · date]]</p>
    <h1>[[a title that says what the piece concludes]]</h1>
    <p class="lead">[[the summary in one paragraph]]</p>
    <p class="meta">[[author or owner, reading time]]</p>
    <h2>[[a heading that says what the section concludes]]</h2>
    <p>[[short paragraphs, real content]]</p>
    <figure class="stack" style="margin: 0">
      <div class="placeholder" role="img" aria-label="[[what the figure shows]]">[[the figure]]</div>
      <figcaption class="meta">[[the caption: what to see in it]]</figcaption>
    </figure>
    <aside class="card"><p style="margin: 0"><strong>[[Note]]</strong> [[a warning or tip, used once]]</p></aside>
    <h2>[[next steps]]</h2>
    <ul class="links"><li><a href="[[page]].html">[[related page]]</a></li><li><a href="[[page]].html">[[how to get help]]</a></li></ul>
  </article>
</main>""",
    "form": """\
<main class="wrap section" style="max-width: 480px">
  <div class="stack">
    <h1 style="font-size: var(--step-3)">[[what the form gets them, as a title]]</h1>
    <p class="lead">[[why, in one line; what happens next]]</p>
    <form class="stack" novalidate>
      <div><label for="name">[[Name]]</label><input id="name" name="name" autocomplete="name"></div>
      <div><label for="email">[[Work email]]</label><input id="email" name="email" type="email" autocomplete="email"><p class="meta" style="margin: var(--space-2) 0 0">[[why it is asked]]</p></div>
      <div><label for="team">[[Team size]]</label><select id="team" name="team"><option>[[1–10]]</option></select></div>
      <button class="btn primary" type="submit" style="width: 100%">[[what happens: Create account]]</button>
    </form>
    <ul class="links"><li><a href="[[page]].html">[[the quiet way back: Sign in instead]]</a></li></ul>
  </div>
</main>""",
}


#: The kinds whose layout is the whole body, not the part under the starting file's header.
SHELLS = frozenset({"dashboard", "app", "slides"})


def layout_text(kind: str | None) -> str:
    """The layout for `kind`, with how to use it, or nothing when there is none."""
    if kind not in LAYOUTS:
        return ""
    # A dashboard, an app screen and a deck are their own shell; the others sit under the header.
    where = (
        "paste it in place of everything inside the starting file's <body> (it is its own shell)"
        if kind in SHELLS
        else "paste it in place of the starting file's <main>, keeping its header"
    )
    return (
        f"Layout for {kind}: {where}, "
        "replace every [[slot]] with the product's own words, and make it yours: change the rhythm, "
        "drop what the brief does not need, add the one flourish the direction allows.\n" + LAYOUTS[kind]
    )
