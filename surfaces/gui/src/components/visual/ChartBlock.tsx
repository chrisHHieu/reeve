// Charts drawn to scale from the block's labels and series: one series of bars reads
// as a ranked list, several as columns, grouped or stacked; lines and areas share an axis with a
// hover readout; a pie is a donut beside its legend. Marks, ticks and labels come from one scale,
// and every colour is a theme token, so the chart reads the same in light and dark.
//
// An axis chart is drawn at its frame's own width and a fixed height, in pixels: a viewBox
// scaled to the width made a chart in a wide window 380px tall with 15px ticks, the largest
// thing in the answer and the least read (the owner's report, 2026-10-09). Expanded, it is taller.
//
// What a reader can do with it: name a series in the legend to hide or show it (pointing at one
// brings it forward), stack or group columns, drag across a line chart to look closer at that
// stretch and double-click to see it all again. Columns of four series or more open stacked:
// eight bars side by side in one month read as noise (the owner's report, 2026-10-10).
//
// Motion is the app's: a line draws itself once, bars grow once, a choice quiets the other marks,
// the readout follows the pointer. Nothing animates while values change under a slider: those
// are redrawn each frame by the block that owns them.

import { useLayoutEffect, useRef, useState, type PointerEvent } from "react";
import type { ChartSpec, Series } from "./visualModel";
import { axisFor, crossings, formatAxis, formatValue, useVisualLocale, vizColor as color } from "./format";
import { useExpanded } from "./expanded";

export function ChartBlock({ spec }: { spec: ChartSpec }) {
  if (spec.kind === "pie") return <Donut labels={spec.labels} series={spec.series[0]} unit={spec.unit} />;
  if (spec.kind === "bar" && spec.series.length === 1) return <BarList labels={spec.labels} series={spec.series[0]} unit={spec.unit} />;
  if (spec.kind === "bar") return <Columns labels={spec.labels} series={spec.series} unit={spec.unit} />;
  return <LineChart labels={spec.labels} series={spec.series} unit={spec.unit} area={spec.kind === "area"} xLabel={spec.x_label} />;
}

/** One series of bars as a ranked list: label, bar, value and share. Bars grow once on arrival. */
function BarList({ labels, series, unit }: { labels: string[]; series: Series; unit?: string }) {
  const locale = useVisualLocale();
  const n = Math.min(labels.length, series.values.length);
  const values = series.values.slice(0, n);
  const max = Math.max(...values.map((v) => Math.abs(v || 0)), 0) || 1;
  const total = values.reduce((sum, v) => sum + (Number.isFinite(v) ? v : 0), 0);
  const [picked, setPicked] = useState<number | null>(null);
  return (
    <div className="vz-bars" data-picked={picked !== null || undefined}>
      {values.map((value, i) => (
        <button
          type="button"
          key={i}
          className="vz-bar"
          aria-pressed={picked === i}
          onClick={() => setPicked((p) => (p === i ? null : i))}
        >
          <span className="vz-bar-label" title={labels[i]}>
            {labels[i]}
          </span>
          <span className="vz-bar-track">
            <span
              className="vz-bar-fill"
              data-negative={value < 0 || undefined}
              style={{ width: `${(Math.abs(value || 0) / max) * 100}%`, animationDelay: `${Math.min(i, 10) * 30}ms` }}
            />
          </span>
          <span className="vz-bar-value">
            {formatValue(value, { unit, locale })}
            {total > 0 && value >= 0 && <span className="vz-bar-share"> · {Math.round((value / total) * 100)}%</span>}
          </span>
        </button>
      ))}
    </div>
  );
}

/** Before the frame is measured (and in a test, which lays nothing out). */
const FALLBACK_WIDTH = 640;
const PAD = { top: 18, right: 14, bottom: 26 };
/** The plot's height: small inside an answer, room to read expanded. */
const useHeight = () => (useExpanded() ? 400 : 196);

/** The width the plot is drawn at: its frame's, followed as the window resizes. */
function useWidth() {
  const ref = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(FALLBACK_WIDTH);
  useLayoutEffect(() => {
    const frame = ref.current;
    if (!frame) return;
    const read = () => {
      const measured = Math.round(frame.getBoundingClientRect().width);
      if (measured > 0) setWidth(measured);
    };
    read();
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(read);
    observer.observe(frame);
    return () => observer.disconnect();
  }, []);
  return { ref, width };
}

/** Where the left edge of the plot sits: room for the widest tick label. */
const leftPad = (labels: string[]) => Math.min(72, 12 + Math.max(...labels.map((t) => t.length)) * 6.2);

/** Which series are drawn, and which one the pointer brings forward. One always stays drawn. */
function useSeriesView(count: number) {
  const [hidden, setHidden] = useState<ReadonlySet<number>>(new Set());
  const [focus, setFocus] = useState<number | null>(null);
  const toggle = (i: number) =>
    setHidden((before) => {
      const next = new Set(before);
      if (next.has(i)) next.delete(i);
      else if (count - next.size > 1) next.add(i);
      return next;
    });
  const shown = (i: number) => !hidden.has(i);
  return { shown, toggle, focus, setFocus };
}

type SeriesView = ReturnType<typeof useSeriesView>;

function Legend({ names, view }: { names: string[]; view?: SeriesView }) {
  if (!view || names.length < 2) {
    return (
      <div className="vz-legend">
        {names.map((name, i) => (
          <span key={i}>
            <i style={{ background: color(i) }} />
            {name}
          </span>
        ))}
      </div>
    );
  }
  return (
    <div className="vz-legend" role="group" aria-label="Series">
      {names.map((name, i) => (
        <button
          type="button"
          key={i}
          aria-pressed={view.shown(i)}
          title={view.shown(i) ? `Hide ${name}` : `Show ${name}`}
          onClick={() => view.toggle(i)}
          onPointerEnter={() => view.shown(i) && view.setFocus(i)}
          onPointerLeave={() => view.setFocus(null)}
        >
          <i style={{ background: color(i) }} />
          {name}
        </button>
      ))}
    </div>
  );
}

/** The x a fractional index stands for: interpolated between the points' own numbers when the
 *  chart has them (a what-if's range), else the nearer label. */
function labelAt(labels: string[], xs: number[] | undefined, at: number, locale: string | undefined): string {
  const low = Math.floor(at);
  if (xs && Number.isFinite(xs[low]) && Number.isFinite(xs[low + 1])) {
    return formatValue(xs[low] + (xs[low + 1] - xs[low]) * (at - low), { decimals: 1, locale });
  }
  return labels[Math.round(at)] ?? "";
}

/** An x-axis label that stays inside the frame: the first starts at the plot's edge, the last ends at it. */
const anchorAt = (i: number, first: number, last: number) => (i === last && last > first ? "end" : i === first && last > first ? "start" : "middle");
const clipLabel = (label: string, max: number) => (label.length > max ? `${label.slice(0, max - 1)}…` : label);

export function LineChart({
  labels,
  series,
  unit,
  area,
  xLabel,
  xs,
}: {
  labels: string[];
  series: Series[];
  unit?: string;
  area?: boolean;
  xLabel?: string;
  /** The number each point stands at, when the x axis is a range of numbers. */
  xs?: number[];
}) {
  const locale = useVisualLocale();
  const H = useHeight();
  const { ref: frame, width: W } = useWidth();
  const svg = useRef<SVGSVGElement>(null);
  const seriesView = useSeriesView(series.length);
  const n = Math.min(labels.length, ...series.map((s) => s.values.length));
  // The stretch looked at: all of it, or what the reader dragged across.
  const [zoom, setZoom] = useState<[number, number] | null>(null);
  const lo = zoom && zoom[1] < n ? zoom[0] : 0;
  const hi = zoom && zoom[1] < n ? zoom[1] : n - 1;
  const m = Math.max(hi - lo + 1, 0);
  const drawn = series.map((s, k) => ({ s, k })).filter(({ k }) => seriesView.shown(k));
  const all = drawn.flatMap(({ s }) => s.values.slice(lo, hi + 1)).filter(Number.isFinite);
  const axis = axisFor(all.length ? Math.min(...all) : 0, all.length ? Math.max(...all) : 1);
  const ticks = axis.ticks.map((t) => formatAxis(t, locale));
  const left = leftPad(ticks);
  const plot = W - left - PAD.right;
  const x = (i: number) => (m <= 1 ? left + plot / 2 : left + ((i - lo) / (m - 1)) * plot);
  const y = (v: number) => PAD.top + (1 - (v - axis.min) / (axis.max - axis.min)) * (H - PAD.top - PAD.bottom);
  const indexAt = (event: PointerEvent<SVGSVGElement>) => {
    const box = svg.current?.getBoundingClientRect();
    if (!box || box.width === 0 || m === 0) return null;
    const px = ((event.clientX - box.left) / box.width) * W;
    const at = m === 1 ? lo : lo + Math.round(((px - left) / plot) * (m - 1));
    return Math.max(lo, Math.min(hi, at));
  };
  const [at, setAt] = useState<number | null>(null);
  const [brush, setBrush] = useState<{ from: number; to: number } | null>(null);
  const every = Math.max(1, Math.ceil(m / Math.max(2, Math.floor(plot / 70))));
  const path = (values: number[]) => {
    let d = "";
    for (let i = lo; i <= hi; i++) {
      if (!Number.isFinite(values[i])) continue;
      d += `${i > lo && Number.isFinite(values[i - 1]) ? "L" : "M"}${x(i).toFixed(1)},${y(values[i]).toFixed(1)} `;
    }
    return d.trim();
  };
  // Two series compared: where one overtakes the other is what the comparison turns on.
  const crossed = drawn.length === 2 ? crossings(drawn[0].s.values, drawn[1].s.values, n).filter((c) => c >= lo && c <= hi).slice(0, 3) : [];
  const dots = m <= 40;
  const zoomable = n >= 4;
  const focus = seriesView.focus;
  return (
    <div className="vz-chart">
      {series.length > 1 && <Legend names={series.map((s) => s.name)} view={seriesView} />}
      <div className="vz-plot" ref={frame}>
        <svg
          ref={svg}
          viewBox={`0 0 ${W} ${H}`}
          width={W}
          height={H}
          className="vz-svg"
          data-zoomable={zoomable || undefined}
          role="img"
          aria-label={series.map((s) => s.name).join(", ")}
          onPointerDown={(event) => {
            if (!zoomable || event.pointerType !== "mouse" || event.button !== 0) return;
            const i = indexAt(event);
            if (i === null) return;
            event.currentTarget.setPointerCapture(event.pointerId);
            setBrush({ from: i, to: i });
          }}
          onPointerMove={(event) => {
            const i = indexAt(event);
            if (brush && i !== null) setBrush({ ...brush, to: i });
            setAt(i);
          }}
          onPointerUp={() => {
            if (brush && Math.abs(brush.to - brush.from) >= 1) {
              setZoom([Math.min(brush.from, brush.to), Math.max(brush.from, brush.to)]);
              setAt(null);
            }
            setBrush(null);
          }}
          onPointerLeave={() => {
            if (!brush) setAt(null);
          }}
          onDoubleClick={() => setZoom(null)}
        >
          {axis.ticks.map((t, k) => (
            <g key={t}>
              <line x1={left} x2={W - PAD.right} y1={y(t)} y2={y(t)} className={t === 0 ? "vz-zero" : "vz-grid"} />
              <text x={left - 6} y={y(t) + 4} textAnchor="end" className="vz-tick">
                {ticks[k]}
              </text>
            </g>
          ))}
          {labels.slice(lo, hi + 1).map((label, offset) => {
            const i = lo + offset;
            return (offset % every === 0 && hi - i >= every / 2) || i === hi ? (
              <text key={i} x={x(i)} y={H - 7} textAnchor={anchorAt(i, lo, hi)} className="vz-tick">
                {clipLabel(label, 12)}
              </text>
            ) : null;
          })}
          {brush && brush.from !== brush.to && (
            <rect
              className="vz-brush"
              x={Math.min(x(brush.from), x(brush.to))}
              y={PAD.top}
              width={Math.abs(x(brush.to) - x(brush.from))}
              height={H - PAD.top - PAD.bottom}
            />
          )}
          {area &&
            drawn.map(({ s, k }) => (
              <path
                key={`a${k}`}
                className="vz-area"
                d={`${path(s.values)} L${x(hi)},${y(Math.max(axis.min, 0))} L${x(lo)},${y(Math.max(axis.min, 0))} Z`}
                fill={color(k)}
                data-dim={focus !== null && focus !== k ? "" : undefined}
              />
            ))}
          {crossed.map((c) => (
            <g key={c} className="vz-cross" data-testid="chart-crossing">
              <line x1={x(c)} x2={x(c)} y1={PAD.top} y2={H - PAD.bottom} />
              <text x={x(c)} y={PAD.top - 6} textAnchor="middle">
                {xLabel ? `${xLabel} ` : ""}≈ {labelAt(labels, xs, c, locale)}
              </text>
            </g>
          ))}
          {drawn.map(({ s, k }) => (
            <path
              key={k}
              className="vz-line"
              pathLength={1}
              d={path(s.values)}
              fill="none"
              stroke={color(k)}
              strokeWidth={focus === k ? 2.6 : 2}
              strokeLinejoin="round"
              strokeLinecap="round"
              data-dim={focus !== null && focus !== k ? "" : undefined}
            />
          ))}
          {dots &&
            drawn.map(({ s, k }) => (
              <g key={`d${k}`} className="vz-dots" data-dim={focus !== null && focus !== k ? "" : undefined}>
                {s.values.slice(lo, hi + 1).map((v, offset) =>
                  Number.isFinite(v) ? <circle key={offset} cx={x(lo + offset)} cy={y(v)} r={2.4} fill={color(k)} /> : null,
                )}
              </g>
            ))}
          {at !== null && !brush && (
            <g className="vz-hover">
              <line x1={x(at)} x2={x(at)} y1={PAD.top} y2={H - PAD.bottom} />
              {drawn.map(({ s, k }) => (Number.isFinite(s.values[at]) ? <circle key={k} cx={x(at)} cy={y(s.values[at])} r={4} fill={color(k)} /> : null))}
            </g>
          )}
        </svg>
        {zoom && (
          <button type="button" className="vz-zoom-reset" data-export-skip onClick={() => setZoom(null)}>
            Show all
          </button>
        )}
        {at !== null && !brush && (
          <div className="vz-readout" style={{ left: `${(x(at) / W) * 100}%` }} data-flip={x(at) > W * 0.6 || undefined}>
            <b>
              {xLabel ? `${xLabel} ` : ""}
              {labels[at]}
            </b>
            {drawn.map(({ s, k }) => (
              <span key={k}>
                <i style={{ background: color(k) }} />
                {series.length > 1 ? `${s.name}: ` : ""}
                {formatValue(s.values[at], { unit, locale })}
              </span>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

/** Several series of bars over one axis: grouped side by side, or stacked into a total. */
function Columns({ labels, series, unit }: { labels: string[]; series: Series[]; unit?: string }) {
  const locale = useVisualLocale();
  const H = useHeight();
  const { ref: frame, width: W } = useWidth();
  const seriesView = useSeriesView(series.length);
  const [stacked, setStacked] = useState(series.length >= 4);
  const n = Math.min(labels.length, ...series.map((s) => s.values.length));
  const drawn = series.map((s, k) => ({ s, k })).filter(({ k }) => seriesView.shown(k));
  const value = (s: Series, i: number) => (Number.isFinite(s.values[i]) ? s.values[i] : 0);
  // A stack's ends: what its positive values add up to above zero and its negative ones below.
  const ends = labels.slice(0, n).map((_, i) => ({
    up: drawn.reduce((sum, { s }) => sum + Math.max(value(s, i), 0), 0),
    down: drawn.reduce((sum, { s }) => sum + Math.min(value(s, i), 0), 0),
  }));
  const all = stacked ? ends.flatMap((e) => [e.up, e.down]) : drawn.flatMap(({ s }) => s.values.slice(0, n)).filter(Number.isFinite);
  const axis = axisFor(Math.min(0, ...all), Math.max(0, ...all));
  const ticks = axis.ticks.map((t) => formatAxis(t, locale));
  const left = leftPad(ticks);
  const band = (W - left - PAD.right) / Math.max(n, 1);
  const barW = stacked ? Math.max(4, Math.min(44, band * 0.56)) : Math.max(2, Math.min(28, (band * 0.72) / Math.max(drawn.length, 1)));
  const y = (v: number) => PAD.top + (1 - (v - axis.min) / (axis.max - axis.min)) * (H - PAD.top - PAD.bottom);
  const [at, setAt] = useState<number | null>(null);
  const every = Math.max(1, Math.ceil(n / Math.max(2, Math.floor((W - left) / 64))));
  const focus = seriesView.focus;
  const bars = (i: number) => {
    const cx = left + band * (i + 0.5);
    if (!stacked) {
      const start = cx - (barW * drawn.length) / 2;
      return drawn.map(({ s, k }, j) => {
        const v = value(s, i);
        return (
          <rect
            key={k}
            className="vz-col"
            x={start + j * barW}
            y={y(Math.max(v, 0))}
            width={Math.max(barW - 1, 1)}
            height={Math.abs(y(v) - y(0))}
            rx={2}
            fill={color(k)}
            data-dim={focus !== null && focus !== k ? "" : undefined}
          />
        );
      });
    }
    let up = 0;
    let down = 0;
    return drawn.map(({ s, k }) => {
      const v = value(s, i);
      const from = v >= 0 ? up : down;
      const to = from + v;
      if (v >= 0) up = to;
      else down = to;
      return (
        <rect
          key={k}
          className="vz-col"
          x={cx - barW / 2}
          y={y(Math.max(from, to))}
          width={barW}
          height={Math.abs(y(from) - y(to))}
          fill={color(k)}
          data-dim={focus !== null && focus !== k ? "" : undefined}
        />
      );
    });
  };
  return (
    <div className="vz-chart">
      <div className="vz-chart-head">
        <Legend names={series.map((s) => s.name)} view={seriesView} />
        <div className="vz-mode" role="radiogroup" aria-label="Columns">
          <button type="button" role="radio" aria-checked={!stacked} onClick={() => setStacked(false)}>
            Grouped
          </button>
          <button type="button" role="radio" aria-checked={stacked} onClick={() => setStacked(true)}>
            Stacked
          </button>
        </div>
      </div>
      <div className="vz-plot" ref={frame}>
        <svg
          viewBox={`0 0 ${W} ${H}`}
          width={W}
          height={H}
          className="vz-svg"
          role="img"
          aria-label={series.map((s) => s.name).join(", ")}
          onPointerLeave={() => setAt(null)}
        >
          {axis.ticks.map((t, k) => (
            <g key={t}>
              <line x1={left} x2={W - PAD.right} y1={y(t)} y2={y(t)} className={t === 0 ? "vz-zero" : "vz-grid"} />
              <text x={left - 6} y={y(t) + 4} textAnchor="end" className="vz-tick">
                {ticks[k]}
              </text>
            </g>
          ))}
          {labels.slice(0, n).map((label, i) => (
            <g key={i} onPointerEnter={() => setAt(i)} className="vz-group" data-dim={at !== null && at !== i ? "" : undefined}>
              <rect x={left + band * i} y={PAD.top} width={band} height={H - PAD.top - PAD.bottom} fill="transparent" />
              {bars(i)}
              {(i % every === 0 || i === n - 1) && (
                <text x={left + band * (i + 0.5)} y={H - 7} textAnchor="middle" className="vz-tick">
                  {clipLabel(label, 10)}
                </text>
              )}
            </g>
          ))}
        </svg>
        {at !== null && (
          <div className="vz-readout" style={{ left: `${((left + band * (at + 0.5)) / W) * 100}%` }} data-flip={left + band * at > W * 0.6 || undefined}>
            <b>{labels[at]}</b>
            {drawn.map(({ s, k }) => (
              <span key={k} data-quiet={s.values[at] === 0 || !Number.isFinite(s.values[at]) || undefined}>
                <i style={{ background: color(k) }} />
                {s.name}: {formatValue(s.values[at], { unit, locale })}
              </span>
            ))}
            {stacked && drawn.length > 1 && (
              <span className="vz-readout-total">Total: {formatValue(ends[at].up + ends[at].down, { unit, locale })}</span>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

/** One series as shares of a whole: a donut beside its legend; picking a slice in either names it. */
function Donut({ labels, series, unit }: { labels: string[]; series?: Series; unit?: string }) {
  const locale = useVisualLocale();
  const n = Math.min(labels.length, series?.values.length ?? 0);
  const values = (series?.values ?? []).slice(0, n).map((v) => (Number.isFinite(v) && v > 0 ? v : 0));
  const total = values.reduce((a, b) => a + b, 0) || 1;
  const [picked, setPicked] = useState<number | null>(null);
  const [pointed, setPointed] = useState<number | null>(null);
  const lit = pointed ?? picked;
  let angle = -Math.PI / 2;
  const arcs = values.map((v) => {
    const start = angle;
    angle += (v / total) * Math.PI * 2;
    return { start, end: angle };
  });
  const arcPath = (start: number, end: number, r: number, inner: number) => {
    const large = end - start > Math.PI ? 1 : 0;
    const p = (a: number, rad: number) => `${(80 + rad * Math.cos(a)).toFixed(2)},${(80 + rad * Math.sin(a)).toFixed(2)}`;
    if (end - start >= Math.PI * 2 - 1e-6) end = start + Math.PI * 2 - 1e-4;
    return `M${p(start, r)} A${r},${r} 0 ${large} 1 ${p(end, r)} L${p(end, inner)} A${inner},${inner} 0 ${large} 0 ${p(start, inner)} Z`;
  };
  return (
    <div className="vz-donut">
      <svg viewBox="0 0 160 160" className="vz-donut-svg" role="img" aria-label={series?.name ?? "shares"}>
        {arcs.map((arc, i) =>
          values[i] > 0 ? (
            <path
              key={i}
              d={arcPath(arc.start, arc.end, lit === i ? 76 : 72, 48)}
              fill={color(i)}
              className="vz-slice"
              data-dim={lit !== null && lit !== i ? "" : undefined}
              onClick={() => setPicked((p) => (p === i ? null : i))}
              onPointerEnter={() => setPointed(i)}
              onPointerLeave={() => setPointed(null)}
            />
          ) : null,
        )}
        <text x="80" y="78" textAnchor="middle" className="vz-donut-total">
          {formatAxis(lit !== null ? values[lit] : total, locale)}
        </text>
        <text x="80" y="94" textAnchor="middle" className="vz-tick">
          {lit !== null ? `${Math.round((values[lit] / total) * 100)}%` : "total"}
        </text>
      </svg>
      <ul className="vz-legend-list">
        {labels.slice(0, n).map((label, i) => (
          <li key={i}>
            <button
              type="button"
              aria-pressed={picked === i}
              onClick={() => setPicked((p) => (p === i ? null : i))}
              onPointerEnter={() => setPointed(i)}
              onPointerLeave={() => setPointed(null)}
            >
              <i style={{ background: color(i) }} />
              <span className="vz-legend-name">{label}</span>
              <span className="vz-legend-value">
                {formatValue(values[i], { unit, locale })} · {Math.round((values[i] / total) * 100)}%
              </span>
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}
