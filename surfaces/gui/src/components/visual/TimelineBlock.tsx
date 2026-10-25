// A timeline: items with a start and, for a span, an end, laid on one time axis. A
// span is a bar, a single date a mark with its date beside it; items with a group are gathered
// under it in the order they first appear, in the group's colour. The axis ticks by day, week,
// month or year, whichever gives four to twelve ticks, and today is a dashed rule when it falls
// inside the range. A done item is drawn quiet.
//
// A reader can look closer: Ctrl+wheel (the wheel alone when expanded) or the + and − buttons zoom
// around the pointer or the middle, a drag pans, "Today" brings today to the middle and "Show all"
// goes back. Pointing at an item names it with its dates, how long it runs and its note.

import { useEffect, useMemo, useRef, useState, type PointerEvent as ReactPointerEvent } from "react";
import { reducedMotion, useVisualLocale, vizColor, type NumberLocale } from "./format";
import { useExpanded } from "./expanded";
import type { TimelineItem, TimelineSpec } from "./visualModel";

const DAY = 86_400_000;
const at = (iso: string) => Date.parse(`${iso}T00:00:00Z`);

interface Tick {
  t: number;
  label: string;
}

/** Ticks over [from, to]: a day, a week, a month or a year apart, labelled the visual's way. */
export function timeTicks(from: number, to: number, locale: NumberLocale): Tick[] {
  const days = (to - from) / DAY;
  const ticks: Tick[] = [];
  const date = (t: number, options: Intl.DateTimeFormatOptions) => new Date(t).toLocaleDateString(locale, { timeZone: "UTC", ...options });
  if (days <= 70) {
    const start = new Date(from);
    // Days a step apart when under three weeks are looked at; else Mondays, one to four weeks apart.
    const step = days <= 10 ? 1 : days <= 21 ? 2 : days <= 28 ? 7 : days <= 56 ? 14 : 28;
    let t =
      step < 7
        ? Date.UTC(start.getUTCFullYear(), start.getUTCMonth(), start.getUTCDate() + 1)
        : Date.UTC(start.getUTCFullYear(), start.getUTCMonth(), start.getUTCDate() + ((8 - start.getUTCDay()) % 7));
    for (; t <= to; t += step * DAY) ticks.push({ t, label: date(t, { day: "numeric", month: "numeric" }) });
    return ticks;
  }
  if (days <= 365 * 3) {
    const months = Math.ceil(days / 30);
    const step = months <= 12 ? 1 : months <= 24 ? 2 : 3;
    const start = new Date(from);
    let y = start.getUTCFullYear();
    let m = start.getUTCMonth() + 1;
    let previousYear = -1;
    for (;;) {
      if (m > 11) {
        y += Math.floor(m / 12);
        m %= 12;
      }
      const t = Date.UTC(y, m, 1);
      if (t > to) break;
      if (m % step === 0) {
        const withYear = y !== previousYear;
        // Vietnamese short months read "T10", "T1/2027": Intl's ("thg 9 2026", "Tháng 10") mixes two forms.
        const label =
          locale === "vi-VN"
            ? `T${m + 1}${withYear ? `/${y}` : ""}`
            : date(t, withYear ? { month: "short", year: "numeric" } : { month: "short" });
        ticks.push({ t, label });
        previousYear = y;
      }
      m += 1;
    }
    return ticks;
  }
  const years = days / 365;
  const step = years <= 12 ? 1 : years <= 30 ? 5 : 10;
  for (let y = Math.ceil(new Date(from).getUTCFullYear() / step) * step; Date.UTC(y, 0, 1) <= to; y += step) {
    const t = Date.UTC(y, 0, 1);
    if (t >= from) ticks.push({ t, label: String(y) });
  }
  return ticks;
}

/** How long a span runs, in the unit a person would say it in. */
export function spanLength(days: number, locale: NumberLocale): string {
  const vi = locale === "vi-VN";
  const say = (n: number, one: string, many: string, word: string) => (vi ? `${n.toLocaleString("vi-VN")} ${word}` : `${n} ${n === 1 ? one : many}`);
  if (days < 14) return say(days, "day", "days", "ngày");
  if (days < 90) return say(Math.round(days / 7), "week", "weeks", "tuần");
  if (days < 730) return say(Math.round(days / 30.44), "month", "months", "tháng");
  return say(Math.round((days / 365.25) * 10) / 10, "year", "years", "năm");
}

type Window = { from: number; to: number };

export function TimelineBlock({ spec }: { spec: TimelineSpec }) {
  const locale = useVisualLocale();
  const expanded = useExpanded();
  const vi = locale === "vi-VN";
  const full = useMemo<Window>(() => {
    const starts = spec.items.map((i) => at(i.start));
    const ends = spec.items.map((i) => at(i.end ?? i.start));
    const from = Math.min(...starts);
    const to = Math.max(...ends);
    // A margin either side, so a mark at an end is not cut by the frame.
    const pad = Math.max((to - from) * 0.05, DAY * 2);
    return { from: from - pad, to: to + pad };
  }, [spec.items]);
  const groups = useMemo(() => {
    const out: { name?: string; items: TimelineItem[] }[] = [];
    for (const item of spec.items) {
      const group = out.find((g) => g.name === item.group);
      if (group) group.items.push(item);
      else out.push({ name: item.group, items: [item] });
    }
    return out;
  }, [spec.items]);
  const [view, setView] = useState<Window>(full);
  useEffect(() => setView(full), [full]);
  const zoomed = view.from > full.from + 1 || view.to < full.to - 1;
  const ticks = useMemo(() => timeTicks(view.from, view.to, locale), [view, locale]);
  const width = view.to - view.from;
  const frac = (t: number) => (t - view.from) / width;
  const x = (t: number) => `${(frac(t) * 100).toFixed(3)}%`;
  const now = Date.now();
  const yearly = (full.to - full.from) / DAY > 365 * 3;
  const date = (iso: string) => new Date(at(iso)).toLocaleDateString(locale, { timeZone: "UTC", day: "numeric", month: "short", year: "numeric" });
  const markDate = (iso: string) =>
    new Date(at(iso)).toLocaleDateString(locale, { timeZone: "UTC", ...(yearly ? { year: "numeric" } : { day: "numeric", month: "numeric" }) });

  // The window moves smoothly to a new one (a button), or at once (the wheel, a drag).
  const animation = useRef(0);
  useEffect(() => () => cancelAnimationFrame(animation.current), []);
  const goTo = (target: Window, smooth: boolean) => {
    cancelAnimationFrame(animation.current);
    if (!smooth || reducedMotion() || typeof requestAnimationFrame === "undefined") return setView(target);
    const start = view;
    const began = performance.now();
    const step = () => {
      const k = Math.max(0, Math.min(1, (performance.now() - began) / 320));
      const e = 1 - (1 - k) ** 3;
      setView({ from: start.from + (target.from - start.from) * e, to: start.to + (target.to - start.to) * e });
      if (k < 1) animation.current = requestAnimationFrame(step);
    };
    animation.current = requestAnimationFrame(step);
  };
  /** Never wider than everything, never narrower than a day, never off the ends. */
  const bounded = (from: number, to: number): Window => {
    const fullWidth = full.to - full.from;
    const span = Math.min(Math.max(to - from, DAY), fullWidth);
    const start = Math.max(full.from, Math.min(from, full.to - span));
    return { from: start, to: start + span };
  };
  const zoomAround = (factor: number, center: number, smooth: boolean) => {
    const pivot = view.from + width * center;
    goTo(bounded(pivot - (pivot - view.from) * factor, pivot + (view.to - pivot) * factor), smooth);
  };

  const track = useRef<HTMLSpanElement>(null);
  const body = useRef<HTMLDivElement>(null);
  const centerOf = (clientX: number) => {
    const box = track.current?.getBoundingClientRect();
    return box && box.width > 0 ? Math.max(0, Math.min(1, (clientX - box.left) / box.width)) : 0.5;
  };
  // A wheel listener that can keep the page from scrolling: React's is passive.
  const latest = useRef({ zoomAround, centerOf, expanded });
  latest.current = { zoomAround, centerOf, expanded };
  useEffect(() => {
    const node = body.current;
    if (!node) return;
    const onWheel = (event: WheelEvent) => {
      if (!event.ctrlKey && !latest.current.expanded) return;
      event.preventDefault();
      latest.current.zoomAround(Math.exp(event.deltaY * 0.0025), latest.current.centerOf(event.clientX), false);
    };
    node.addEventListener("wheel", onWheel, { passive: false });
    return () => node.removeEventListener("wheel", onWheel);
  }, []);
  const drag = useRef<{ x: number; view: Window; width: number } | null>(null);
  const [dragging, setDragging] = useState(false);
  const onPointerDown = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (event.pointerType !== "mouse" || event.button !== 0 || !zoomed) return;
    const trackWidth = track.current?.getBoundingClientRect().width ?? 0;
    if (trackWidth <= 0) return;
    drag.current = { x: event.clientX, view, width: trackWidth };
    event.currentTarget.setPointerCapture(event.pointerId);
    setDragging(true);
    setPointed(null);
  };
  const onPointerMove = (event: ReactPointerEvent<HTMLDivElement>) => {
    const from = drag.current;
    if (!from) return;
    const shift = ((event.clientX - from.x) / from.width) * (from.view.to - from.view.from);
    setView(bounded(from.view.from - shift, from.view.to - shift));
  };
  const endDrag = () => {
    drag.current = null;
    setDragging(false);
  };

  const [pointed, setPointed] = useState<{ item: TimelineItem; x: number; y: number } | null>(null);
  const point = (item: TimelineItem, event: ReactPointerEvent<HTMLElement>) => {
    if (drag.current) return;
    const box = body.current?.getBoundingClientRect();
    if (!box) return;
    setPointed({ item, x: event.clientX - box.left, y: event.clientY - box.top });
  };
  const runs = (item: TimelineItem) => !!item.end && at(item.end) > at(item.start);
  return (
    <div className="vz-timeline">
      {/* The zoom buttons sit in the axis row's label column, which is otherwise empty. */}
      <div className="vz-tl-row vz-tl-axis">
        <div className="vz-tl-tools" data-export-skip>
          <button type="button" title={vi ? "Thu nhỏ" : "Zoom out"} aria-label={vi ? "Thu nhỏ" : "Zoom out"} disabled={!zoomed} onClick={() => zoomAround(1.6, 0.5, true)}>
            −
          </button>
          <button type="button" title={vi ? "Phóng to" : "Zoom in"} aria-label={vi ? "Phóng to" : "Zoom in"} onClick={() => zoomAround(0.6, 0.5, true)}>
            +
          </button>
          {now > full.from && now < full.to && (
            <button type="button" onClick={() => goTo(bounded(now - width / 2, now + width / 2), true)}>
              {vi ? "Hôm nay" : "Today"}
            </button>
          )}
          {zoomed && (
            <button type="button" onClick={() => goTo(full, true)}>
              {vi ? "Xem hết" : "Show all"}
            </button>
          )}
        </div>
        <span className="vz-tl-track" ref={track} aria-hidden>
          {ticks.map((tick) => (
            <span key={tick.t} className="vz-tl-tick" style={{ left: x(tick.t) }}>
              {tick.label}
            </span>
          ))}
        </span>
      </div>
      <div
        ref={body}
        className="vz-tl-body"
        data-pannable={zoomed || undefined}
        data-dragging={dragging || undefined}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={endDrag}
        onPointerCancel={endDrag}
        onPointerLeave={() => setPointed(null)}
      >
        <div className="vz-tl-lines" aria-hidden>
          {ticks.map((tick) => (
            <span key={tick.t} className="vz-tl-grid" style={{ left: x(tick.t) }} />
          ))}
          {now >= view.from && now <= view.to && <span className="vz-tl-today" style={{ left: x(now) }} />}
        </div>
        {groups.map((group, g) => (
          <div key={g} className="vz-tl-group">
            {group.name && <div className="vz-tl-group-name">{group.name}</div>}
            {group.items.map((item, i) => {
              const start = at(item.start);
              const hue = vizColor(g);
              return (
                <div key={i} className="vz-tl-row" data-done={item.done || undefined}>
                  <span className="vz-tl-label" title={item.label}>
                    {item.label}
                  </span>
                  <span className="vz-tl-track">
                    {runs(item) ? (
                      <span
                        className="vz-tl-bar"
                        style={{ left: x(start), width: `${(((at(item.end!) - start) / width) * 100).toFixed(3)}%`, background: hue }}
                        onPointerMove={(event) => point(item, event)}
                        onPointerLeave={() => setPointed(null)}
                      />
                    ) : (
                      <span
                        className="vz-tl-milestone"
                        style={{ left: x(start) }}
                        data-flip={frac(start) > 0.8 || undefined}
                        onPointerMove={(event) => point(item, event)}
                        onPointerLeave={() => setPointed(null)}
                      >
                        <span className="vz-tl-mark" style={{ background: hue }} />
                        <span className="vz-tl-mark-date">{markDate(item.start)}</span>
                      </span>
                    )}
                  </span>
                </div>
              );
            })}
          </div>
        ))}
        {pointed && (
          <div
            className="vz-readout vz-tl-readout"
            style={{ left: pointed.x, top: pointed.y }}
            data-flip={pointed.x > (body.current?.clientWidth ?? 0) * 0.6 || undefined}
          >
            <b>{pointed.item.label}</b>
            <span>
              {date(pointed.item.start)}
              {runs(pointed.item) ? ` – ${date(pointed.item.end!)}` : ""}
            </span>
            {runs(pointed.item) && <span>{spanLength(Math.round((at(pointed.item.end!) - at(pointed.item.start)) / DAY) + 1, locale)}</span>}
            {pointed.item.group && <span>{pointed.item.group}</span>}
            {pointed.item.note && <span className="vz-tl-note">{pointed.item.note}</span>}
            {pointed.item.done && <span>{vi ? "Đã xong" : "Done"}</span>}
          </div>
        )}
      </div>
    </div>
  );
}
