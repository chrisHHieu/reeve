// A collapsible view of a JSON value — the reference design shows payloads, results and schemas this way
// rather than as one block of text, and the difference matters at the size these actually
// are: a 4,000-character tool result in a `<pre>` is a wall, and the reader is looking for
// one field in it.
//
// Falls back to plain text when the value is not JSON, which is the common case for shell
// output and the reason this cannot simply assume a parse.

import { useRef, useState } from "react";
import { Disclosure } from "../ui/Disclosure";
import { useAutoHeight } from "../ui/useAutoHeight";

const INDENT = 14;
// A long plain-text result shows its first lines and one control for the rest. It used to
// sit in a 150px box that scrolled inside the scrolling transcript.
const CLAMP_OVER = 12;
const CLAMP_TO = 8;

function Leaf({ value }: { value: unknown }) {
  if (typeof value === "string") return <span className="jt-str">"{value}"</span>;
  if (typeof value === "number") return <span className="jt-num">{String(value)}</span>;
  if (typeof value === "boolean") return <span className="jt-bool">{String(value)}</span>;
  if (value === null) return <span className="jt-null">null</span>;
  return <span>{String(value)}</span>;
}

function Node({
  name,
  value,
  depth,
  defaultOpen,
}: {
  name?: string;
  value: unknown;
  depth: number;
  defaultOpen: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  const isObject = value !== null && typeof value === "object";
  if (!isObject) {
    return (
      <div className="jt-row" style={{ paddingLeft: depth * INDENT }}>
        {name !== undefined && <span className="jt-key">{name}: </span>}
        <Leaf value={value} />
      </div>
    );
  }
  const entries = Array.isArray(value)
    ? value.map((v, i) => [String(i), v] as const)
    : Object.entries(value as Record<string, unknown>);
  const brace = Array.isArray(value) ? ["[", "]"] : ["{", "}"];
  return (
    <div>
      <div className="jt-row" style={{ paddingLeft: depth * INDENT }}>
        <button
          type="button"
          className="jt-toggle"
          aria-expanded={open}
          aria-label={open ? `Fold ${name ?? "this value"}` : `Open ${name ?? "this value"}`}
          onClick={() => setOpen((v) => !v)}
        >
          {open ? "▾" : "▸"}
        </button>
        {name !== undefined && <span className="jt-key">{name}: </span>}
        <span className="jt-punct">{brace[0]}</span>
        {!open && (
          <span className="jt-collapsed">
            {" "}
            {entries.length} {entries.length === 1 ? "entry" : "entries"}{" "}
            <span className="jt-punct">{brace[1]}</span>
          </span>
        )}
      </div>
      {/* A value opens and folds as a motion: its entries were mounted and unmounted on the
          click, so everything under it jumped (owner, 2026-10-13). */}
      <Disclosure open={open}>
        {entries.map(([key, child]) => (
          <Node key={key} name={key} value={child} depth={depth + 1} defaultOpen={depth < 1} />
        ))}
        <div className="jt-row jt-punct" style={{ paddingLeft: depth * INDENT }}>
          {brace[1]}
        </div>
      </Disclosure>
    </div>
  );
}

/** Plain text, whole when it is short, its first lines and a way to the rest when it is long. */
function Text({ text, error, clamp }: { text: string; error?: boolean; clamp?: boolean }) {
  const [all, setAll] = useState(false);
  const lines = text.split("\n");
  const long = clamp === true && lines.length > CLAMP_OVER;
  // The same box with more or fewer lines in it: it glides to its new height.
  const box = useRef<HTMLDivElement>(null);
  useAutoHeight(box, long);
  return (
    <div className="jt-text" ref={box}>
      <pre className={`jt-pre${error ? " is-error" : ""}`} data-testid="jt-text">
        {long && !all ? lines.slice(0, CLAMP_TO).join("\n") : text}
      </pre>
      {long && (
        <div className="jt-more">
          <button
            type="button"
            className="jt-more-btn"
            data-testid="jt-more"
            aria-expanded={all}
            onClick={() => setAll((value) => !value)}
          >
            {all ? "Show fewer lines" : `Show all ${lines.length.toLocaleString("en-US")} lines`}
          </button>
          {!all && (
            <span>
              {CLAMP_TO} of {lines.length.toLocaleString("en-US")} lines shown
            </span>
          )}
        </div>
      )}
    </div>
  );
}

/**
 * Render `value` as a tree. A string is parsed first — tool payloads and results arrive as
 * JSON text — and stays text when it is not JSON, which shell output usually is not.
 * `clamp`: a long plain-text value shows its first lines and a control for the rest.
 */
export function JsonTree({ value, error, clamp }: { value: unknown; error?: boolean; clamp?: boolean }) {
  let parsed = value;
  if (typeof value === "string") {
    const trimmed = value.trim();
    if (!trimmed) return <p className="jt-none">Nothing captured</p>;
    if (trimmed.startsWith("{") || trimmed.startsWith("[")) {
      try {
        parsed = JSON.parse(trimmed);
      } catch {
        parsed = value;
      }
    }
  }
  if (typeof parsed === "string") return <Text text={parsed} error={error} clamp={clamp} />;
  return (
    <div className={`jt${error ? " is-error" : ""}`} data-testid="jt-tree">
      <Node value={parsed} depth={0} defaultOpen />
    </div>
  );
}
