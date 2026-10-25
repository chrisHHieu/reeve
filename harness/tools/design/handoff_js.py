"""What a lock measures inside the page besides the check's measure.

One expression, like `measure.py`'s, run in the same load (`handoff.LOCK_JS`): the page as a system
(colours by role, type, spacing, corners, shadows, its own tokens, where its layout changes, its font
files, its parts by name, its motion). It returns plain data and decides nothing; `handoff.py` says it.
"""

from __future__ import annotations

#: The second measuring script: what the page is made of, as a system. One expression returning
#: JSON-able values, like `MEASURE_JS`, run after it in the same load.
HANDOFF_JS = r"""
(() => {
  const MAX_SCAN = 4000;
  const de = document.documentElement;
  const vw = de.clientWidth;
  // The root and the body paint the page's ground, so they are scanned too.
  const all = [de, document.body, ...document.body.querySelectorAll('*')].slice(0, MAX_SCAN);
  const visible = (el) => {
    const cs = getComputedStyle(el);
    if (cs.display === 'none' || cs.visibility === 'hidden' || Number(cs.opacity) === 0) return false;
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  };
  const ownText = (el) => [...el.childNodes].some((n) => n.nodeType === 3 && n.textContent.trim().length > 0);
  const parse = (value) => {
    const m = /rgba?\(([^)]+)\)/.exec(value || '');
    if (!m) return null;
    const p = m[1].split(/[ ,/]+/).filter(Boolean).map(Number);
    return { r: p[0], g: p[1], b: p[2], a: p.length > 3 ? p[3] : 1 };
  };
  const hex = (c) => '#' + [c.r, c.g, c.b].map((n) => Math.round(n).toString(16).padStart(2, '0')).join('');
  const name = (el) => {
    const tag = el.tagName.toLowerCase();
    if (el.id) return tag + '#' + el.id;
    const cls = [...el.classList].filter((c) => c.length < 40).slice(0, 2);
    return cls.length ? tag + '.' + cls.join('.') : tag;
  };
  const bump = (map, key, extra) => {
    const entry = map.get(key) || { count: 0, ...extra };
    entry.count += 1;
    map.set(key, entry);
    return entry;
  };
  const px = (value) => Math.round(parseFloat(value) * 10) / 10;

  // Colours by the role they play. A background is weighed by the area it paints, so the ground of
  // the page is told from a badge's.
  const roles = { background: new Map(), text: new Map(), border: new Map() };
  const type = new Map();
  const spacing = new Map();
  const radii = new Map();
  const shadows = new Map();
  for (const el of all) {
    if (!visible(el)) continue;
    const cs = getComputedStyle(el);
    const r = el.getBoundingClientRect();
    const bg = parse(cs.backgroundColor);
    if (bg && bg.a > 0) {
      const entry = bump(roles.background, hex(bg), { area: 0, where: name(el) });
      entry.area += Math.round(r.width * Math.min(r.height, 4000));
    }
    for (const side of ['Top', 'Right', 'Bottom', 'Left']) {
      if (parseFloat(cs['border' + side + 'Width']) > 0 && cs['border' + side + 'Style'] !== 'none') {
        const c = parse(cs['border' + side + 'Color']);
        if (c && c.a > 0) bump(roles.border, hex(c), { where: name(el) });
        break;
      }
    }
    for (const prop of ['paddingTop', 'paddingRight', 'paddingBottom', 'paddingLeft', 'marginTop', 'marginBottom', 'rowGap', 'columnGap']) {
      const v = px(cs[prop]);
      if (v > 0 && v < 400) bump(spacing, String(v), {});
    }
    const radius = cs.borderTopLeftRadius;
    if (radius && radius !== '0px') bump(radii, radius, {});
    if (cs.boxShadow && cs.boxShadow !== 'none') bump(shadows, cs.boxShadow, { where: name(el) });
    if (!ownText(el)) continue;
    const fg = parse(cs.color);
    if (fg && fg.a > 0) bump(roles.text, hex(fg), { where: name(el) });
    const family = (cs.fontFamily || '').split(',')[0].replace(/["']/g, '').trim();
    const size = px(cs.fontSize);
    const weight = parseInt(cs.fontWeight, 10) || 400;
    const line = cs.lineHeight === 'normal' ? 'normal' : px(cs.lineHeight);
    const key = family + '|' + size + '|' + weight;
    const entry = bump(type, key, {
      family, size, weight, line,
      tracking: cs.letterSpacing === 'normal' ? 0 : px(cs.letterSpacing),
      tag: el.tagName.toLowerCase(),
      sample: (el.textContent || '').trim().replace(/\s+/g, ' ').slice(0, 40),
    });
    if (entry.tag !== el.tagName.toLowerCase() && /^h[1-6]$/.test(el.tagName.toLowerCase())) entry.tag = el.tagName.toLowerCase();
  }

  // Motion: every element's transitions and animations, shown or not (a closed menu is hidden and
  // still says how it will open).
  const motion = new Map();
  for (const el of all.slice(2)) {
    const cs = getComputedStyle(el);
    const props = (cs.transitionProperty || '').split(',').map((s) => s.trim());
    const durs = (cs.transitionDuration || '').split(',').map((s) => s.trim());
    const eases = (cs.transitionTimingFunction || '').split(/,(?![^(]*\))/).map((s) => s.trim());
    props.forEach((prop, i) => {
      const dur = parseFloat(durs[i % durs.length] || '0') * (/ms$/.test(durs[i % durs.length] || '') ? 1 : 1000);
      if (!dur || prop === 'none') return;
      bump(motion, 'transition|' + prop + '|' + Math.round(dur) + '|' + (eases[i % eases.length] || 'ease'), {
        kind: 'transition', what: prop, ms: Math.round(dur), easing: eases[i % eases.length] || 'ease', where: name(el),
      });
    });
    if (cs.animationName && cs.animationName !== 'none') {
      const d = cs.animationDuration || '0s';
      const ms = Math.round(parseFloat(d) * (/ms$/.test(d) ? 1 : 1000));
      if (ms) bump(motion, 'animation|' + cs.animationName + '|' + ms, {
        kind: 'animation', what: cs.animationName, ms, easing: cs.animationTimingFunction || 'ease', where: name(el),
      });
    }
  }

  // What the page's own stylesheets say: custom properties on the root, the widths its layout
  // changes at, and the font files it loads. A sheet the browser will not let a page read (another
  // origin) is skipped.
  const vars = new Map();
  const breakpoints = new Set();
  const faces = [];
  const walk = (rules) => {
    for (const rule of rules) {
      if (rule.type === 1 && /(^|,)\s*(:root|html)\s*(,|$)/.test(rule.selectorText || '')) {
        for (const prop of rule.style) {
          if (prop.startsWith('--') && vars.size < 80) vars.set(prop, rule.style.getPropertyValue(prop).trim().slice(0, 80));
        }
      } else if (rule.type === 4) {
        for (const m of (rule.conditionText || rule.media.mediaText || '').matchAll(/(min|max)-width:\s*([\d.]+)(px|em|rem)/g)) {
          breakpoints.add(m[1] + ' ' + Math.round(parseFloat(m[2]) * (m[3] === 'px' ? 1 : 16)));
        }
        if (rule.cssRules) walk(rule.cssRules);
      } else if (rule.type === 5 && faces.length < 24) {
        const src = rule.style.getPropertyValue('src') || '';
        const urls = [...src.matchAll(/url\(\s*["']?([^"')]+)["']?\s*\)/g)].map((m) => m[1]).filter((u) => !u.startsWith('data:'));
        faces.push({
          family: (rule.style.getPropertyValue('font-family') || '').replace(/["']/g, '').trim(),
          weight: (rule.style.getPropertyValue('font-weight') || '').trim(),
          style: (rule.style.getPropertyValue('font-style') || '').trim(),
          urls: urls.slice(0, 3),
        });
      } else if (rule.cssRules) {
        walk(rule.cssRules);
      }
    }
  };
  for (const sheet of document.styleSheets) {
    try { walk(sheet.cssRules); } catch (e) { /* another origin's sheet */ }
  }

  // The page's parts from top to bottom: its landmarks, and the blocks directly inside its body or
  // its main, each named by its heading, its label or its id.
  const LANDMARK = 'header, nav, main, section, article, aside, footer, form, [role=banner], [role=main], [role=contentinfo], [role=region]';
  const holder = document.querySelector('main') || document.body;
  const candidates = new Set();
  for (const el of document.body.children) candidates.add(el);
  for (const el of holder.children) candidates.add(el);
  for (const el of document.querySelectorAll(LANDMARK)) {
    if (el.parentElement && el.parentElement.closest(LANDMARK) && el.parentElement.closest(LANDMARK) !== holder) continue;
    candidates.add(el);
  }
  const sections = [];
  for (const el of candidates) {
    if (el === holder && holder !== document.body) continue;
    if (['SCRIPT', 'STYLE', 'TEMPLATE', 'NOSCRIPT', 'LINK', 'META'].includes(el.tagName)) continue;
    if (!visible(el)) continue;
    const r = el.getBoundingClientRect();
    if (r.height < 16) continue;
    const heading = el.matches('h1,h2,h3') ? el : el.querySelector('h1, h2, h3, legend');
    const label = el.getAttribute('aria-label') || '';
    const controls = el.querySelectorAll('button, a[href], input, select, textarea, summary').length;
    // How many columns its children sit in at this width.
    const kids = [...el.children].filter(visible).map((k) => k.getBoundingClientRect());
    const rows = new Map();
    for (const k of kids) { const top = Math.round(k.top / 4); rows.set(top, (rows.get(top) || 0) + 1); }
    const columns = kids.length ? Math.max(...rows.values()) : 0;
    sections.push({
      tag: el.tagName.toLowerCase(),
      name: name(el),
      heading: heading ? (heading.textContent || '').trim().replace(/\s+/g, ' ').slice(0, 60) : '',
      label: label.slice(0, 60),
      y: Math.round(r.top + scrollY),
      h: Math.round(r.height),
      w: Math.round(r.width),
      columns,
      controls,
    });
  }
  sections.sort((a, b) => a.y - b.y);

  const top = (map, n, by) => [...map.entries()].sort((a, b) => (b[1][by || 'count'] - a[1][by || 'count'])).slice(0, n)
    .map(([key, v]) => ({ key, ...v }));
  return {
    width: vw,
    height: de.scrollHeight,
    colors: {
      background: top(roles.background, 12, 'area'),
      text: top(roles.text, 12),
      border: top(roles.border, 8),
    },
    type: top(type, 16).sort((a, b) => b.size - a.size || b.weight - a.weight),
    spacing: top(spacing, 14).map((e) => ({ px: Number(e.key), count: e.count })).sort((a, b) => a.px - b.px),
    radii: top(radii, 8).map((e) => ({ value: e.key, count: e.count })),
    shadows: top(shadows, 5).map((e) => ({ value: e.key.slice(0, 160), count: e.count, where: e.where })),
    motion: top(motion, 16).map(({ key, ...rest }) => rest),
    vars: [...vars.entries()].map(([name, value]) => ({ name, value })),
    breakpoints: [...breakpoints].sort((a, b) => Number(a.split(' ')[1]) - Number(b.split(' ')[1])),
    faces,
    sections: sections.slice(0, 24),
  };
})()
"""
