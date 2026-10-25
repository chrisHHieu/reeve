"""What a design check measures inside the page.

One expression, evaluated by the harness's browser after each load (`browser.render_page`), that
returns plain data and decides nothing: which parts run past the window, which text is too faint to
read, which colours and fonts are used and where, which controls are too small to hit, which images
and controls have no name. Judging it against the project's DESIGN.md and turning it into sentences
is `check.py`'s, in Python, where it can be tested without a browser.

Only what a browser can measure is here. Whether a design is good is the model's and the person's.
"""

from __future__ import annotations

#: The script. It must stay one expression returning JSON-able values (`returnByValue`).
MEASURE_JS = r"""
(() => {
  const MAX_SCAN = 4000;
  const vw = document.documentElement.clientWidth;
  const de = document.documentElement;

  const describe = (el) => {
    if (!el || el.nodeType !== 1) return '';
    const tag = el.tagName.toLowerCase();
    if (el.id) return tag + '#' + el.id;
    const cls = [...el.classList].filter((c) => c.length < 40).slice(0, 2);
    if (cls.length) return tag + '.' + cls.join('.');
    const parent = el.parentElement;
    if (parent && parent !== document.body && parent !== de) {
      const up = describe(parent);
      return up ? up + ' > ' + tag : tag;
    }
    return tag;
  };

  // Seen means drawn where a person can reach it: not inside something invisible (an ancestor's
  // opacity counts), and not parked beside the window, as a closed drawer is. Below the fold counts.
  const visible = (el) => {
    const cs = getComputedStyle(el);
    if (cs.display === 'none' || cs.visibility === 'hidden' || Number(cs.opacity) === 0) return false;
    if (el.checkVisibility && !el.checkVisibility({ opacityProperty: true, visibilityProperty: true })) return false;
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0 && r.right > 0 && r.bottom > 0;
  };

  const parse = (value) => {
    const m = /rgba?\(([^)]+)\)/.exec(value || '');
    if (!m) return null;
    const p = m[1].split(/[ ,/]+/).filter(Boolean).map(Number);
    return { r: p[0], g: p[1], b: p[2], a: p.length > 3 ? p[3] : 1 };
  };
  const hex = (c) => '#' + [c.r, c.g, c.b].map((n) => Math.round(n).toString(16).padStart(2, '0')).join('');
  const over = (top, bottom) => ({
    r: top.r * top.a + bottom.r * (1 - top.a),
    g: top.g * top.a + bottom.g * (1 - top.a),
    b: top.b * top.a + bottom.b * (1 - top.a),
    a: 1,
  });
  const lum = (c) => {
    const f = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); };
    return 0.2126 * f(c.r) + 0.7152 * f(c.g) + 0.0722 * f(c.b);
  };
  const ratio = (a, b) => { const x = lum(a), y = lum(b); return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05); };

  // The colour behind an element: the first painted background up the tree, blended down to an
  // opaque one. A background image or gradient on the way makes it unknown (null), and unknown is
  // never reported as a failure.
  const backdrop = (el) => {
    const layers = [];
    for (let node = el; node && node.nodeType === 1; node = node.parentElement) {
      const cs = getComputedStyle(node);
      if (cs.backgroundImage && cs.backgroundImage !== 'none') return null;
      const c = parse(cs.backgroundColor);
      if (c && c.a > 0) { layers.push(c); if (c.a >= 1) break; }
    }
    let base = { r: 255, g: 255, b: 255, a: 1 };
    for (let i = layers.length - 1; i >= 0; i--) base = over(layers[i], base);
    return base;
  };

  const ownText = (el) => [...el.childNodes].some((n) => n.nodeType === 3 && n.textContent.trim().length > 0);

  const all = [...document.body.querySelectorAll('*')].slice(0, MAX_SCAN);

  // Parts past the right edge: the outermost ones, whose parent still fits.
  const past = new Map();
  if (de.scrollWidth > vw + 1) {
    for (const el of all) {
      if (!visible(el)) continue;
      const r = el.getBoundingClientRect();
      if (r.right <= vw + 1 && r.left >= -1) continue;
      const parent = el.parentElement;
      const pr = parent ? parent.getBoundingClientRect() : null;
      if (parent && parent !== document.body && pr && pr.right > vw + 1) continue;
      const key = describe(el);
      past.set(key, (past.get(key) || 0) + 1);
    }
  }

  const contrast = new Map();
  const colors = new Map();
  const fonts = new Map();
  const note = (map, key, where) => {
    const entry = map.get(key) || { count: 0, where: [] };
    entry.count += 1;
    if (entry.where.length < 2 && !entry.where.includes(where)) entry.where.push(where);
    map.set(key, entry);
  };

  for (const el of all) {
    if (!visible(el)) continue;
    const cs = getComputedStyle(el);
    const bg = parse(cs.backgroundColor);
    if (bg && bg.a > 0) note(colors, hex(bg), describe(el) + ' background');
    for (const side of ['Top', 'Right', 'Bottom', 'Left']) {
      if (parseFloat(cs['border' + side + 'Width']) > 0 && cs['border' + side + 'Style'] !== 'none') {
        const c = parse(cs['border' + side + 'Color']);
        if (c && c.a > 0) { note(colors, hex(c), describe(el) + ' border'); }
        break;
      }
    }
    if (!ownText(el)) continue;
    const fg = parse(cs.color);
    if (fg && fg.a > 0) note(colors, hex(fg), describe(el) + ' text');
    const family = (cs.fontFamily || '').split(',')[0].replace(/["']/g, '').trim();
    if (family) note(fonts, family, describe(el));
    const back = backdrop(el);
    if (!fg || !back) continue;
    const shown = fg.a < 1 ? over(fg, back) : fg;
    const size = parseFloat(cs.fontSize) || 16;
    const bold = (parseInt(cs.fontWeight, 10) || 400) >= 700;
    const need = size >= 24 || (bold && size >= 18.66) ? 3 : 4.5;
    const got = ratio(shown, back);
    if (got + 0.005 < need) {
      const key = describe(el) + '|' + hex(shown) + '|' + hex(back);
      const entry = contrast.get(key) || { where: describe(el), fg: hex(shown), bg: hex(back), ratio: Math.round(got * 10) / 10, need, count: 0 };
      entry.count += 1;
      contrast.set(key, entry);
    }
  }

  // Controls too small to hit: under 44 px on a phone-sized window, under 24 px elsewhere
  // (WCAG 2.2 target size). A link inside a run of text is exempt, as WCAG exempts it.
  const touch = vw < 600 ? 44 : 24;
  const small = [];
  let smallCount = 0;
  const controls = document.querySelectorAll('button, a[href], input:not([type=hidden]), select, textarea, summary, [role=button], [role=tab], [role=checkbox], [role=switch]');
  for (const el of controls) {
    if (!visible(el)) continue;
    if (el.tagName === 'A' && getComputedStyle(el).display === 'inline' && el.parentElement && ownText(el.parentElement)) continue;
    const r = el.getBoundingClientRect();
    if (Math.min(r.width, r.height) + 0.5 < touch) {
      smallCount += 1;
      if (small.length < 6) small.push({ where: describe(el), w: Math.round(r.width), h: Math.round(r.height) });
    }
  }

  const named = (el) => {
    if ((el.getAttribute('aria-label') || '').trim() || el.getAttribute('aria-labelledby') || (el.getAttribute('title') || '').trim()) return true;
    if (el.id && document.querySelector('label[for="' + CSS.escape(el.id) + '"]')) return true;
    return !!el.closest('label');
  };
  const unlabeled = [];
  for (const el of document.querySelectorAll('input, select, textarea')) {
    const type = (el.getAttribute('type') || '').toLowerCase();
    if (['hidden', 'submit', 'button', 'reset', 'image'].includes(type)) continue;
    if (!visible(el) || named(el)) continue;
    unlabeled.push(describe(el));
  }
  for (const el of document.querySelectorAll('button, [role=button], a[href]')) {
    if (!visible(el) || (el.textContent || '').trim() || named(el)) continue;
    const img = el.querySelector('img[alt]:not([alt=""]), svg[aria-label], [role=img][aria-label]');
    if (!img) unlabeled.push(describe(el));
  }
  const noAlt = [...document.querySelectorAll('img:not([alt])')].filter(visible).map(describe);

  // What the page is made of, for telling one version from the next (`versions.py`): each part a
  // person sees (text, a control, an image), keyed by its place in the document the way the
  // preview's pointer names it, with what a change to it would change.
  const placeOf = (el) => {
    const parts = [];
    for (let n = el; n && n.nodeType === 1 && n !== document.body && n !== de; n = n.parentElement) {
      const t = n.tagName.toLowerCase();
      if (n.id) { parts.unshift(t + '#' + CSS.escape(n.id)); break; }
      const p = n.parentElement;
      const same = p ? [...p.children].filter((c) => c.tagName === n.tagName) : [n];
      parts.unshift(same.length > 1 ? t + ':nth-of-type(' + (same.indexOf(n) + 1) + ')' : t);
    }
    return parts.join(' > ') || 'body';
  };
  const inventory = [];
  for (const el of all) {
    if (inventory.length >= 400) break;
    if (!visible(el)) continue;
    const isControl = el.matches('button, a[href], input, select, textarea, img, svg');
    if (!isControl && !ownText(el)) continue;
    const cs = getComputedStyle(el);
    const r = el.getBoundingClientRect();
    const fg = parse(cs.color), bg = parse(cs.backgroundColor);
    inventory.push({
      key: placeOf(el),
      name: describe(el),
      text: (el.getAttribute('aria-label') || el.textContent || el.getAttribute('alt') || '').trim().replace(/\s+/g, ' ').slice(0, 60),
      x: Math.round(r.left), y: Math.round(r.top + scrollY), w: Math.round(r.width), h: Math.round(r.height),
      color: fg && fg.a > 0 ? hex(fg) : '', bg: bg && bg.a > 0 ? hex(bg) : '',
      size: Math.round(parseFloat(cs.fontSize) || 0), weight: parseInt(cs.fontWeight, 10) || 400,
    });
  }

  const top = (map, n) => [...map.entries()].sort((a, b) => b[1].count - a[1].count).slice(0, n)
    .map(([key, v]) => ({ key, count: v.count, where: v.where }));

  return {
    width: vw,
    scroll_width: de.scrollWidth,
    past: [...past.entries()].slice(0, 6).map(([where, count]) => ({ where, count })),
    contrast: [...contrast.values()].sort((a, b) => a.ratio - b.ratio).slice(0, 8),
    contrast_count: [...contrast.values()].reduce((n, e) => n + e.count, 0),
    colors: top(colors, 40),
    fonts: top(fonts, 12),
    faces: document.fonts ? [...new Set([...document.fonts].map((f) => f.family.replace(/["']/g, '')))].slice(0, 40) : [],
    small,
    small_count: smallCount,
    touch,
    unlabeled: unlabeled.slice(0, 8),
    unlabeled_count: unlabeled.length,
    no_alt: noAlt.slice(0, 8),
    no_alt_count: noAlt.length,
    title: document.title || '',
    inventory,
  };
})()
"""
