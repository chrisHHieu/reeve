// A calculator the person drives: sliders for the inputs, figures computed from the
// block's formulas, and a chart over a range. Everything is recomputed here, in the GUI, as a
// slider moves — no request goes to the model.
//
// The drawn values glide to the slider's value rather than jump: each frame moves them a fixed
// share of the remaining distance, so lines and figures settle smoothly and stop. Figures change
// in place (no pop), the best output's tint crossfades, and reduced motion draws the target at once.

import { useContext, useEffect, useMemo, useRef, useState, type CSSProperties } from "react";
import { LineChart } from "./ChartBlock";
import { compile, type Compiled, type Scope } from "./formula";
import { formatValue, reducedMotion, useVisualLocale } from "./format";
import type { WhatIfSpec } from "./visualModel";
import { keepInputs, keptInputs, registerVisualState, VisualIdentity } from "./visualState";

const MAX_POINTS = 400;

function safe(fn: Compiled | null, scope: Scope): number {
  if (!fn) return NaN;
  try {
    const value = fn(scope);
    return Number.isFinite(value) ? value : NaN;
  } catch {
    return NaN;
  }
}

function tryCompile(formula: string | number): Compiled | null {
  try {
    return compile(formula);
  } catch {
    return null;
  }
}

export function WhatIfBlock({ spec }: { spec: WhatIfSpec }) {
  const locale = useVisualLocale();
  // Which visual this is, when an answer draws it: its moves are said with the next message and
  // kept across a reload (`visualState.ts`), keyed by its call and its inputs.
  const identity = useContext(VisualIdentity);
  const key = identity ? `${identity.callId}:${spec.inputs.map((i) => i.name).join(",")}` : null;
  const initial = useMemo(() => {
    const kept = key ? keptInputs(key) : null;
    return Object.fromEntries(
      spec.inputs.map((i) => {
        const value = kept?.[i.name];
        return [i.name, typeof value === "number" && value >= i.min && value <= i.max ? value : i.value];
      }),
    );
  }, [spec.inputs, key]);
  const [target, setTarget] = useState<Scope>(initial);
  const [shown, setShown] = useState<Scope>(initial);
  const drawn = useRef<Scope>(initial);
  const frame = useRef(0);

  useEffect(() => {
    const step = () => {
      let done = true;
      const next: Scope = { ...drawn.current };
      for (const input of spec.inputs) {
        const goal = target[input.name] ?? input.value;
        const now = drawn.current[input.name] ?? goal;
        const gap = goal - now;
        if (reducedMotion() || Math.abs(gap) <= Math.abs(input.max - input.min) / 2000) next[input.name] = goal;
        else {
          next[input.name] = now + gap * 0.22;
          done = false;
        }
      }
      drawn.current = next;
      setShown(next);
      frame.current = done ? 0 : requestAnimationFrame(step);
    };
    frame.current = requestAnimationFrame(step);
    return () => cancelAnimationFrame(frame.current);
  }, [target, spec.inputs]);

  const compiled = useMemo(
    () => ({
      values: spec.values.map((v) => ({ name: v.name, fn: tryCompile(v.formula) })),
      outputs: spec.outputs.map((o) => tryCompile(o.formula)),
      chart: spec.chart
        ? {
            from: tryCompile(spec.chart.x.from),
            to: tryCompile(spec.chart.x.to),
            step: tryCompile(spec.chart.x.step),
            series: spec.chart.series.map((s) => tryCompile(s.formula)),
          }
        : null,
    }),
    [spec],
  );

  // The scope a formula sees: the inputs as drawn (the step terms snap integers such as a
  // number of years, so a term does not read 19.6 years mid-glide), then each value in order.
  const evaluate = (at: Scope) => {
    const scope: Scope = {};
    for (const input of spec.inputs) {
      const value = at[input.name] ?? input.value;
      scope[input.name] = Number.isInteger(input.step) && Number.isInteger(input.min) ? Math.round(value) : value;
    }
    for (const v of compiled.values) scope[v.name] = safe(v.fn, scope);
    return { scope, outputs: compiled.outputs.map((fn) => safe(fn, scope)) };
  };
  const { scope, outputs } = evaluate(shown);

  // Where the person left it, for their next message (`visualState.ts`): said only once moved.
  const latest = useRef({ target, evaluate });
  latest.current = { target, evaluate };
  // Kept once the sliders rest, not on every frame of a drag.
  useEffect(() => {
    if (!key || target === initial) return;
    const timer = window.setTimeout(() => keepInputs(key, target), 300);
    return () => window.clearTimeout(timer);
  }, [key, target, initial]);
  useEffect(() => {
    if (!identity || !key) return;
    return registerVisualState(key, () => {
      const { target: at, evaluate: compute } = latest.current;
      if (spec.inputs.every((input) => (at[input.name] ?? input.value) === input.value)) return null;
      const inputs = spec.inputs.map((input) => `${input.label} = ${formatValue(at[input.name] ?? input.value, { unit: input.unit, locale })}`);
      const figures = compute(at).outputs.map(
        (value, i) => `${spec.outputs[i].label} = ${formatValue(value, { unit: spec.outputs[i].unit, decimals: spec.outputs[i].decimals, locale })}`,
      );
      return { id: identity.id, line: `${inputs.join("; ")}${figures.length ? `. It shows ${figures.join("; ")}` : ""}` };
    });
  }, [identity, key, spec, locale]);

  const best = (() => {
    if (!spec.highlight) return -1;
    let at = -1;
    outputs.forEach((value, i) => {
      if (!Number.isFinite(value)) return;
      if (at < 0 || (spec.highlight === "min" ? value < outputs[at] : value > outputs[at])) at = i;
    });
    return at;
  })();

  let chart: { labels: string[]; xs: number[]; series: { name: string; values: number[] }[] } | null = null;
  if (spec.chart && compiled.chart) {
    const from = safe(compiled.chart.from, scope);
    const to = safe(compiled.chart.to, scope);
    let step = safe(compiled.chart.step, scope);
    if (Number.isFinite(from) && Number.isFinite(to) && to > from) {
      if (!(step > 0) || (to - from) / step > MAX_POINTS) step = (to - from) / MAX_POINTS;
      const xs: number[] = [];
      for (let x = from; x <= to + step / 1e6; x += step) xs.push(Number(x.toPrecision(12)));
      chart = {
        labels: xs.map((x) => formatValue(x, { locale })),
        xs,
        series: spec.chart.series.map((s, k) => ({
          name: s.name,
          // `x` is the chart's variable whatever it is named, as the harness reads it.
          values: xs.map((x) => safe(compiled.chart!.series[k], { ...scope, x, [spec.chart!.x.name]: x })),
        })),
      };
    }
  }

  return (
    <div className="vz-whatif">
      <div className="vz-sliders">
        {spec.inputs.map((input) => {
          const value = target[input.name] ?? input.value;
          // How far along its range the slider stands: the track is filled up to there.
          const fill = input.max > input.min ? ((value - input.min) / (input.max - input.min)) * 100 : 0;
          return (
            <label key={input.name} className="vz-slider">
              <span className="vz-slider-head">
                <span>{input.label}</span>
                <output>{formatValue(value, { unit: input.unit, locale })}</output>
              </span>
              <input
                type="range"
                min={input.min}
                max={input.max}
                step={input.step}
                value={value}
                style={{ "--fill": `${Math.max(0, Math.min(100, fill))}%` } as CSSProperties}
                onChange={(event) => setTarget((t) => ({ ...t, [input.name]: Number(event.target.value) }))}
              />
            </label>
          );
        })}
      </div>
      {spec.outputs.length > 0 && (
        <div className="vz-outputs">
          {spec.outputs.map((output, i) => (
            <div key={i} className="vz-output" data-best={i === best || undefined}>
              <span className="vz-output-label">{output.label}</span>
              <span className="vz-output-value">{formatValue(outputs[i], { unit: output.unit, decimals: output.decimals, locale })}</span>
            </div>
          ))}
        </div>
      )}
      {chart && spec.chart && (
        <LineChart
          labels={chart.labels}
          xs={chart.xs}
          series={chart.series}
          unit={spec.chart.unit}
          area={spec.chart.kind === "area"}
          xLabel={spec.chart.x.label}
        />
      )}
    </div>
  );
}
