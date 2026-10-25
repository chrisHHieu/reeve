"""What a web page looks like, read by Reeve's browser, as a reference for a design.

The Browser tab's lightbulb puts a page's look into the message: its colours (the ink most text is
in, the grounds the most area sits on, the accents its links and buttons wear), its type (families
and sizes of headings and text), the shape of its controls and the width of its content. The
numbers are measured from the page's computed styles, never guessed from a picture, so a model can
take a direction from them.

It is a reference, not a template: the brief says so, and says not to take the site's name, logo
or words, because a design that passes for someone else's brand is not one Reeve makes.
"""

from __future__ import annotations

from typing import Any

#: Evaluated in the page: the measures the brief is written from.
BRIEF_JS = r"""
(() => {
  const hex = (c) => {
    const m = /rgba?\(([^)]+)\)/.exec(c || "");
    if (!m) return null;
    const p = m[1].split(/[ ,/]+/).filter(Boolean).map(Number);
    if (p.length > 3 && p[3] < 0.5) return null;
    return "#" + p.slice(0, 3).map((n) => Math.round(n).toString(16).padStart(2, "0")).join("");
  };
  const add = (map, key, n) => { if (key) map[key] = (map[key] || 0) + n; };
  const ink = {}, ground = {}, accent = {}, fonts = {}, heads = {}, radii = {};
  const sizes = {};
  const els = Array.from(document.querySelectorAll("html, body, body *")).slice(0, 4000);
  let widest = 0;
  for (const el of els) {
    const r = el.getBoundingClientRect();
    if (r.width < 1 || r.height < 1) continue;
    const s = getComputedStyle(el);
    if (s.visibility === "hidden" || s.display === "none" || +s.opacity === 0) continue;
    const own = Array.from(el.childNodes).filter((n) => n.nodeType === 3).map((n) => n.textContent.trim()).join("");
    const family = (s.fontFamily || "").split(",")[0].replace(/["']/g, "").trim();
    if (own.length) {
      add(ink, hex(s.color), own.length);
      add(fonts, family, own.length);
      const tag = el.tagName.toLowerCase();
      if (/^h[1-3]$/.test(tag) || parseFloat(s.fontSize) >= 28) add(heads, family, own.length);
      const key = /^h[1-6]$/.test(tag) ? tag : tag === "p" ? "p" : null;
      if (key && !sizes[key]) sizes[key] = { size: Math.round(parseFloat(s.fontSize)), weight: s.fontWeight, line: s.lineHeight };
    }
    const bg = hex(s.backgroundColor);
    if (bg) add(ground, bg, Math.min(r.width * r.height, 2e6));
    const tag = el.tagName.toLowerCase();
    if (tag === "a" || tag === "button" || el.getAttribute("role") === "button") {
      add(accent, bg || hex(s.color), 1);
      const radius = Math.round(parseFloat(s.borderTopLeftRadius) || 0);
      if (bg) add(radii, String(radius), 1);
    }
    if (r.width < window.innerWidth - 2 && r.width > widest && own.length > 40) widest = Math.round(r.width);
  }
  const top = (map, n) => Object.entries(map).sort((a, b) => b[1] - a[1]).slice(0, n).map((e) => e[0]);
  const body = getComputedStyle(document.body);
  return {
    title: document.title || "",
    width: window.innerWidth,
    ink: top(ink, 4),
    ground: top(ground, 4),
    accent: top(accent, 5),
    fonts: top(fonts, 3),
    heads: top(heads, 2),
    sizes,
    body: { size: Math.round(parseFloat(body.fontSize)), line: body.lineHeight },
    radius: top(radii, 2),
    content: widest,
  };
})()
"""


def _saturated(color: str) -> bool:
    """Whether a `#rrggbb` colour is a colour and not a grey."""
    try:
        r, g, b = (int(color[i : i + 2], 16) for i in (1, 3, 5))
    except (ValueError, IndexError):
        return False
    return max(r, g, b) - min(r, g, b) > 40


def brief_text(measured: dict[str, Any], address: str) -> str:
    """The brief, in words, from what BRIEF_JS measured on the page at `address`."""
    lines = [f"The look of {address}, as Reeve's browser read it at {measured.get('width') or '?'} px wide:"]
    ink = [c for c in measured.get("ink") or [] if isinstance(c, str)]
    ground = [c for c in measured.get("ground") or [] if isinstance(c, str)]
    accent = [c for c in measured.get("accent") or [] if isinstance(c, str) and _saturated(c)]
    if ink or ground:
        parts = []
        if ink:
            parts.append(f"text {ink[0]}" + (f" (also {', '.join(ink[1:3])})" if len(ink) > 1 else ""))
        if ground:
            parts.append(f"grounds {', '.join(ground[:3])}")
        if accent:
            parts.append(f"accents {', '.join(dict.fromkeys(accent))}")
        lines.append("- Colours: " + "; ".join(parts) + ".")
    fonts = [f for f in measured.get("fonts") or [] if isinstance(f, str) and f]
    heads = [f for f in measured.get("heads") or [] if isinstance(f, str) and f]
    sizes = measured.get("sizes") if isinstance(measured.get("sizes"), dict) else {}
    body = measured.get("body") if isinstance(measured.get("body"), dict) else {}
    if fonts or heads:
        head_sizes = [f"{key} {sizes[key].get('size')} px / {sizes[key].get('weight')}" for key in ("h1", "h2", "h3") if isinstance(sizes.get(key), dict)]
        text = f"text in {fonts[0]}" if fonts else ""
        if body.get("size"):
            text += f" at {body['size']} px, line {body.get('line')}"
        head = f"headings in {heads[0]}" if heads else ""
        if head_sizes:
            head += f" ({', '.join(head_sizes)})"
        lines.append("- Type: " + "; ".join(part for part in (head, text) if part) + ".")
    radius = [r for r in measured.get("radius") or [] if isinstance(r, str)]
    if radius:
        lines.append(f"- Shape: controls with a {radius[0]} px corner" + (f" (also {radius[1]} px)" if len(radius) > 1 else "") + ".")
    if measured.get("content"):
        lines.append(f"- Layout: text columns up to about {measured['content']} px wide.")
    lines.append(
        "Use this as a reference for a direction: the feel, the proportions, the kind of palette. "
        "Do not copy the site: not its name, logo, pictures or words."
    )
    return "\n".join(lines)
