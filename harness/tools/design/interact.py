"""What a design check does to a page, besides looking at it (phase 2).

Three passes, each in the harness's browser at the narrowest width checked, after the measuring:

- **interactions**: each control is clicked, the way a person clicks (DevTools input, not a script's
  `click()`), on a fresh load each. What it changed is read off the page, and an overlay that
  opened (a menu, a drawer, a dialog) is closed with Esc and, failing that, with a click outside.
  Frames are recorded while it opens and closes, so "it appeared at once" and "it vanished at once"
  are seen, not guessed. A dialog is also asked whether focus moved into it.
- **dark**: the page loaded with `prefers-color-scheme: dark` and measured again, when it has dark
  styles or DESIGN.md speaks of dark.
- **long text**: the page loaded with longer words in its labels and a longer sentence in its
  headings (a German compound, a Vietnamese phrase), and measured for what then runs off.

The passes only observe. What counts as a problem is `check.py`'s, against the craft rules the
model was given (`guide.py`).
"""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable, Optional

#: Controls tried per check. The ones that say they open something go first.
MAX_CONTROLS = 6
#: A click or a close is waited on until the page has held still for a few frames, at least
#: `SETTLE_MIN_S` (a script may answer a beat late) and at most `SETTLE_MAX_S` (past the 150–250 ms
#: the craft rules give a transition, with room for one that is slow).
SETTLE_MIN_S = 0.12
SETTLE_MAX_S = 0.7
_POLL_S = 0.05

#: Helpers the passes call, installed once per load. Elements are found again after a reload by
#: their order, which a reload of the same file keeps.
INSTALL_JS = r"""
(() => {
  if (window.__reeve) return true;
  const STATE_CLASS = /^(open|opened|active|show|shown|visible|hidden|closed|is-[\w-]+|has-[\w-]+)$/;
  const describe = (el) => {
    if (!el || el.nodeType !== 1) return '';
    const tag = el.tagName.toLowerCase();
    if (el.id) return tag + '#' + el.id;
    const cls = [...el.classList].filter((c) => c.length < 40 && !STATE_CLASS.test(c)).slice(0, 2);
    if (cls.length) return tag + '.' + cls.join('.');
    const label = (el.getAttribute('aria-label') || el.textContent || '').trim().replace(/\s+/g, ' ').slice(0, 24);
    if (label && ['button', 'a', 'summary'].includes(tag)) return tag + ' "' + label + '"';
    const parent = el.parentElement;
    return parent && parent !== document.body ? describe(parent) + ' > ' + tag : tag;
  };
  const onScreen = (el) => {
    const cs = getComputedStyle(el);
    if (cs.display === 'none' || cs.visibility === 'hidden' || Number(cs.opacity) < 0.05) return false;
    if (el.checkVisibility && !el.checkVisibility({ opacityProperty: true, visibilityProperty: true })) return false;
    const r = el.getBoundingClientRect();
    if (r.width < 1 || r.height < 1) return false;
    return r.right > 0 && r.bottom > 0 && r.left < innerWidth && r.top < innerHeight;
  };
  const OPENS = '[aria-expanded], [aria-haspopup], [aria-controls]';
  // The choice already made (selected tab, current page, the pressed one of a set; a lone pressed
  // toggle turns off) has nothing to answer: trying it read the kit's billing toggle as dead.
  const chosen = (el) => el.getAttribute('aria-selected') === 'true'
    || !['', 'false'].includes(el.getAttribute('aria-current') || '')
    || (el.getAttribute('aria-pressed') === 'true' && [...(el.parentElement?.children || [])]
      .some((other) => other !== el && other.hasAttribute('aria-pressed')));
  const controls = () => {
    const found = [...document.querySelectorAll(
      'button, [role=button], [role=tab], [role=menuitem], summary, a[href^="#"], ' + OPENS
    )].filter((el) => {
      if (!onScreen(el) || el.disabled) return false;
      const r = el.getBoundingClientRect();
      const hit = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
      if (!hit || !(hit === el || el.contains(hit))) return false;
      if (getComputedStyle(el).pointerEvents === 'none') return false;
      const form = el.closest('form');
      const type = (el.getAttribute('type') || '').toLowerCase();
      // A button that would submit a form leaves the page; it is not a control to try here.
      if (form && el.tagName === 'BUTTON' && (type === '' || type === 'submit')) return false;
      return !chosen(el);
    });
    const unique = [...new Set(found)];
    const opener = (el) => el.matches(OPENS) || /menu|open|more|sign|filter|settings|close/i.test(
      (el.getAttribute('aria-label') || '') + ' ' + (el.textContent || '') + ' ' + el.className
    );
    unique.sort((a, b) => Number(opener(b)) - Number(opener(a)));
    return unique;
  };
  const candidates = () => [...document.querySelectorAll('body *')].filter((el) => {
    const role = el.getAttribute('role') || '';
    if (el.tagName === 'DIALOG' || ['dialog', 'alertdialog', 'menu', 'listbox', 'tooltip'].includes(role)) return true;
    const cs = getComputedStyle(el);
    if (cs.position !== 'fixed' && cs.position !== 'absolute') return false;
    const hidden = cs.display === 'none' || cs.visibility === 'hidden' || Number(cs.opacity) < 0.05;
    return hidden || el.getBoundingClientRect().width >= 80;
  }).slice(0, 60);
  const state = (el) => {
    const cs = getComputedStyle(el);
    const r = el.getBoundingClientRect();
    return [onScreen(el) ? 1 : 0, Math.round(Number(cs.opacity) * 100), cs.transform,
      Math.round(r.left), Math.round(r.top), Math.round(r.width), Math.round(r.height)].join('|');
  };
  let rec = null;
  window.__reeve = {
    control(i) {
      const el = controls()[i];
      if (!el) return null;
      const r = el.getBoundingClientRect();
      return {
        where: describe(el), x: r.left + r.width / 2, y: r.top + r.height / 2,
        promises: el.matches(OPENS), count: Math.min(controls().length, %MAX%),
      };
    },
    signature() {
      const on = [];
      let i = 0;
      for (const el of document.body.querySelectorAll('*')) {
        i += 1;
        if (i > 4000) break;
        if (onScreen(el)) on.push(i);
      }
      const aria = [...document.querySelectorAll('[aria-expanded],[aria-selected],[aria-checked],[aria-pressed],details,dialog')]
        .map((e) => [e.getAttribute('aria-expanded'), e.getAttribute('aria-selected'), e.getAttribute('aria-checked'),
          e.getAttribute('aria-pressed'), e.hasAttribute('open')].join(',')).join(';');
      const classes = [...document.body.querySelectorAll('[class]')].map((e) => e.getAttribute('class')).join(' ');
      // A control that only restyles an element or sets a field also answered: without
      // these, a colour swatch or a stepper read as a button that did nothing.
      const styles = [...document.body.querySelectorAll('[style]')].map((e) => e.getAttribute('style')).join(';');
      const fields = [...document.querySelectorAll('input, select, textarea')]
        .map((e) => (e.type === 'checkbox' || e.type === 'radio') ? String(e.checked) : String(e.value)).join('|');
      return [on.join(','), aria, (document.body.innerText || '').length, location.hash, Math.round(scrollY),
        classes.length, styles, fields].join('#');
    },
    start(overlayOnly) {
      const els = overlayOnly ? [document.querySelector('[data-reeve-overlay]')].filter(Boolean) : candidates();
      rec = { els, frames: [els.map(state)], on: true };
      const tick = () => {
        if (!rec || !rec.on) return;
        rec.frames.push(rec.els.map(state));
        requestAnimationFrame(tick);
      };
      requestAnimationFrame(tick);
      return els.length;
    },
    settled() {
      if (!rec || rec.frames.length < 5) return false;
      const last = rec.frames.slice(-4).map((frame) => frame.join('~'));
      return last.every((frame) => frame === last[0]);
    },
    stop() {
      if (!rec) return [];
      rec.on = false;
      rec.frames.push(rec.els.map(state));
      const out = rec.els.map((el, k) => {
        const seq = rec.frames.map((frame) => frame[k]);
        const r = el.getBoundingClientRect();
        return {
          k, where: describe(el), was_on: seq[0][0] === '1', now_on: seq[seq.length - 1][0] === '1',
          steps: new Set(seq).size, area: r.width * r.height,
          modal: el.tagName === 'DIALOG' || ['dialog', 'alertdialog'].includes(el.getAttribute('role') || '') || el.getAttribute('aria-modal') === 'true',
          rect: { l: r.left, t: r.top, r: r.right, b: r.bottom },
        };
      });
      const els = rec.els;
      rec = null;
      window.__reeveLast = els;
      return out;
    },
    mark(k) {
      const el = (window.__reeveLast || [])[k];
      if (!el) return false;
      el.setAttribute('data-reeve-overlay', '');
      return true;
    },
    focusInside() {
      const el = document.querySelector('[data-reeve-overlay]');
      return !!(el && document.activeElement && el.contains(document.activeElement));
    },
  };
  return true;
})()
""".replace("%MAX%", str(MAX_CONTROLS))

#: Longer words in the labels and a longer sentence in the headings, at once.
STRETCH_JS = r"""
(() => {
  const word = ' Rechtsschutzversicherungsgesellschaften';
  const phrase = ' — Kế hoạch triển khai hệ thống cho toàn bộ phòng ban khu vực phía Nam';
  let changed = 0;
  const first = (el) => [...el.childNodes].find((n) => n.nodeType === 3 && n.textContent.trim());
  for (const el of document.querySelectorAll('button, a, label, th, [role=tab], [role=button], summary, li, dt, figcaption')) {
    const node = first(el);
    if (node && node.textContent.trim().length <= 40) { node.textContent += word; changed += 1; }
  }
  for (const el of document.querySelectorAll('h1, h2, h3, h4, td, p')) {
    const node = first(el);
    if (node) { node.textContent += phrase; changed += 1; }
  }
  return changed;
})()
"""

#: What spills out of its own box once the text is longer: an element whose content is wider than
#: it is, and that does not clip or scroll it on purpose. And, when the page runs off, the parts
#: past the window's edge, deepest first: a real session rewrote a page five times on "runs 18 px
#: off" alone, with nothing saying which part (2026-10-19).
SPILL_JS = r"""
(() => {
  const vw = document.documentElement.clientWidth;
  const name = (el) => {
    const tag = el.tagName.toLowerCase();
    const cls = [...el.classList].slice(0, 2);
    return el.id ? tag + '#' + el.id : cls.length ? tag + '.' + cls.join('.') : tag;
  };
  const shown = (el) => {
    const cs = getComputedStyle(el);
    return cs.display !== 'none' && cs.visibility !== 'hidden';
  };
  const out = [];
  for (const el of document.body.querySelectorAll('*')) {
    if (!shown(el)) continue;
    const cs = getComputedStyle(el);
    if (['hidden', 'auto', 'scroll', 'clip'].includes(cs.overflowX) || cs.textOverflow === 'ellipsis') continue;
    if (el.scrollWidth > el.clientWidth + 2 && el.clientWidth > 0 && [...el.childNodes].some((n) => n.nodeType === 3 && n.textContent.trim())) {
      out.push(name(el));
      if (out.length >= 6) break;
    }
  }
  const excess = document.documentElement.scrollWidth - vw;
  const past = [];
  if (excess > 1) {
    const over = (el) => shown(el) && getComputedStyle(el).position !== 'fixed' && el.getBoundingClientRect().right > vw + 1;
    for (const el of document.body.querySelectorAll('*')) {
      if (!over(el) || [...el.children].some(over)) continue;
      past.push({ where: name(el), by: Math.round(el.getBoundingClientRect().right - vw) });
      if (past.length >= 3) break;
    }
  }
  return { excess, spill: [...new Set(out)], past };
})()
"""


Send = Callable[..., Awaitable[dict]]
Load = Callable[[], Awaitable[None]]


async def _value(send: Send, expression: str) -> Any:
    result = await send("Runtime.evaluate", {"expression": expression, "returnByValue": True, "awaitPromise": True})
    if result.get("exceptionDetails"):
        return None
    return (result.get("result") or {}).get("value")


async def _settle(send: Send) -> None:
    """Wait until what is recorded has held still for four frames, within the bounds above."""
    loop = asyncio.get_running_loop()
    started = loop.time()
    await asyncio.sleep(SETTLE_MIN_S)
    while loop.time() - started < SETTLE_MAX_S:
        if await _value(send, "window.__reeve.settled()"):
            return
        await asyncio.sleep(_POLL_S)


async def _click(send: Send, x: float, y: float) -> None:
    await send("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": x, "y": y})
    for kind in ("mousePressed", "mouseReleased"):
        await send("Input.dispatchMouseEvent", {"type": kind, "x": x, "y": y, "button": "left", "clickCount": 1})


async def _escape(send: Send) -> None:
    for kind in ("keyDown", "keyUp"):
        await send(
            "Input.dispatchKeyEvent",
            {"type": kind, "key": "Escape", "code": "Escape", "windowsVirtualKeyCode": 27, "nativeVirtualKeyCode": 27},
        )


def _outside(rect: dict[str, float], width: int, height: int) -> tuple[float, float]:
    """A point in the window that is not on the overlay: the corner farthest from it."""
    corners = [(6.0, 6.0), (width - 6.0, 6.0), (6.0, height - 6.0), (width - 6.0, height - 6.0)]

    def inside(point: tuple[float, float]) -> bool:
        return rect["l"] <= point[0] <= rect["r"] and rect["t"] <= point[1] <= rect["b"]

    free = [point for point in corners if not inside(point)]
    cx, cy = (rect["l"] + rect["r"]) / 2, (rect["t"] + rect["b"]) / 2
    return max(free or corners, key=lambda p: (p[0] - cx) ** 2 + (p[1] - cy) ** 2)


async def _close(send: Send, how: str, rect: dict[str, float], width: int, height: int) -> dict[str, Any]:
    """Close the marked overlay one way, recording it. `closed` and how many states it passed."""
    await _value(send, "window.__reeve.start(true)")
    if how == "esc":
        await _escape(send)
    else:
        x, y = _outside(rect, width, height)
        await _click(send, x, y)
    await _settle(send)
    frames = await _value(send, "window.__reeve.stop()") or []
    if not frames:
        return {"closed": False, "steps": 0}
    return {"closed": not frames[0]["now_on"], "steps": int(frames[0]["steps"])}


def _panel(opened: list[dict[str, Any]], width: int, height: int) -> dict[str, Any]:
    """The overlay a click opened: the largest that does not cover the window. A scrim behind a
    drawer or a dialog covers it all and is not the thing to close or to click outside of."""
    window = float(width * height) or 1.0
    panels = [frame for frame in opened if frame["area"] < 0.9 * window]
    return max(panels or opened, key=lambda frame: frame["area"])


async def _open(send: Send, load: Load, index: int, width: int, height: int) -> Optional[dict[str, Any]]:
    """A fresh load, then a click on control `index`, recorded. None when there is no such control
    or the click left the page."""
    await load()
    if not await _value(send, INSTALL_JS):
        return None
    control = await _value(send, f"window.__reeve.control({index})")
    if not control:
        return None
    try:
        before = await _value(send, "window.__reeve.signature()")
        await _value(send, "window.__reeve.start(false)")
        await _click(send, float(control["x"]), float(control["y"]))
        await _settle(send)
        frames = await _value(send, "window.__reeve.stop()")
        after = await _value(send, "window.__reeve.signature()")
    except Exception:
        return {"control": control, "left": True}
    if frames is None or after is None:
        return {"control": control, "left": True}
    opened = [frame for frame in frames if not frame["was_on"] and frame["now_on"]]
    overlay = _panel(opened, width, height) if opened else None
    if overlay is not None:
        await _value(send, f"window.__reeve.mark({int(overlay['k'])})")
    return {"control": control, "overlay": overlay, "changed": before != after}


async def try_controls(send: Send, load: Load, width: int, height: int) -> list[dict[str, Any]]:
    """Click each control on a fresh load and say what happened. An overlay it opens is closed with
    Esc, then opened again and closed with a click outside. Never raises: a control whose click left
    the page or broke the helpers is skipped."""
    results: list[dict[str, Any]] = []
    for index in range(MAX_CONTROLS):
        try:
            first = await _open(send, load, index, width, height)
        except Exception:
            break
        if first is None:
            break
        if first.get("left"):
            continue
        control, overlay = first["control"], first["overlay"]
        entry: dict[str, Any] = {"where": control["where"], "promises": bool(control.get("promises"))}
        if overlay is None:
            entry["dead"] = not first["changed"]
            results.append(entry)
            continue
        entry.update(opened=overlay["where"], open_steps=int(overlay["steps"]), modal=bool(overlay["modal"]))
        try:
            if overlay["modal"]:
                entry["focus_inside"] = bool(await _value(send, "window.__reeve.focusInside()"))
            esc = await _close(send, "esc", overlay["rect"], width, height)
            entry["esc"] = esc["closed"]
            closes = [esc["steps"]] if esc["closed"] else []
            if esc["closed"]:
                again = await _open(send, load, index, width, height)
                if not again or again.get("left") or again.get("overlay") is None:
                    entry["outside"] = None
                    entry["close_steps"] = min(closes)
                    results.append(entry)
                    continue
                overlay = again["overlay"]
            outside = await _close(send, "outside", overlay["rect"], width, height)
            entry["outside"] = outside["closed"]
            if outside["closed"]:
                closes.append(outside["steps"])
            if closes:
                entry["close_steps"] = min(closes)
        except Exception:
            pass
        results.append(entry)
    return results


async def stretch(send: Send, load: Load) -> dict[str, Any]:
    """The page with longer text: how far it then runs off, and what spills out of its box."""
    await load()
    changed = await _value(send, STRETCH_JS)
    await _value(send, "new Promise(r => requestAnimationFrame(() => requestAnimationFrame(() => r(true))))")
    spilled = await _value(send, SPILL_JS) or {}
    past = [item for item in spilled.get("past") or [] if isinstance(item, dict) and isinstance(item.get("where"), str)]
    return {
        "changed": int(changed or 0),
        "excess": int(spilled.get("excess") or 0),
        "spill": list(spilled.get("spill") or []),
        "past": [{"where": item["where"][:80], "by": int(item.get("by") or 0)} for item in past[:3]],
    }
