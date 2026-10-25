// The reasoning disclosure: one row, titled "Thinking" while it runs, "Thought · Ns" at rest.
//
// The row's labels, masks and follow behaviour:
//
//   COLLAPSED, and raw when opened. The trace is scratch paper: verbatim text, newlines
//   preserved, no markdown. It is what the model was thinking, not something written for a
//   reader, and dressing it up would misrepresent how provisional it is.
//
//   The collapsed line FOLLOWS THE END while the block is still streaming — the newest line
//   is the interesting one — and the reference design masks BOTH edges of that line (16px) once it is wider
//   than the row, so the newest words stay readable without the row wrapping or growing. Once
//   settled it shows the FIRST line, because a finished trace is identified by how it opened.
//
//   The OPEN body follows the tail too while the trace streams, and stops following the
//   moment the reader scrolls up: a
//   body that yanked itself back down would fight the reader. Its overflow gets the same
//   edge masks.
//
//   The glyph is the BRAIN, and the row carries its duration at rest (owner ask 2026-09-11,
//   matching the rows): `duration` is the seconds between the previous event's stamp
//   and this block's own — the wait the reader actually experienced. A row without a
//   measurable span says just "Thought": the reference design prints "a few seconds" there, and this row
//   deliberately invents nothing.
//
//   IT SHOWS IN A SETTLED BUBBLE TOO. Reeve used to drop reasoning from the answer once
//   the turn ended; the reference design renders it in the assistant body like any other block, and a trace
//   that vanishes when the work finishes is a trace nobody can check. Raw and one click away
//   is what keeps that from being noise.

import { useEffect, useLayoutEffect, useRef, useState, type CSSProperties, memo } from "react";
import { DisclosureRow } from "./primitives/DisclosureRow";
import { Icon } from "./Icon";

/** Seconds → "45s" / "21m 30s" / "1h 04m" — the Thought row's duration shape. */
export function thoughtFor(seconds: number): string {
  const s = Math.max(0, Math.round(seconds));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ${String(s % 60).padStart(2, "0")}s`;
  const h = Math.floor(m / 60);
  return `${h}h ${String(m % 60).padStart(2, "0")}m`;
}

/** One line of a trace as words: its markdown marks are not for the summary line, where
 *  `**Starting the task**` used to be shown as typed (owner-hit 2026-10-13). */
export function plainLine(line: string): string {
  return line
    .replace(/^\s*#{1,6}\s+/, "")
    .replace(/^\s*[-*+]\s+/, "")
    .replace(/\*\*|__|`/g, "")
    .replace(/^\*(.+)\*$/, "$1")
    .trim();
}

/** The trace's first line, trimmed — how a settled trace identifies itself. */
export function firstLine(text: string): string {
  const cut = text.indexOf("\n");
  return (cut === -1 ? text : text.slice(0, cut)).trim();
}

/** The trace's newest line — what is being thought right now. */
export function latestLine(text: string): string {
  const visible = text.trimEnd();
  const cut = visible.lastIndexOf("\n");
  return cut === -1 ? visible : visible.slice(cut + 1);
}

/** The summary's edge fade, on the one edge whose text is out of view. The line
 *  is pinned — to its end while running, to its start once settled — so the other edge holds
 *  real text, and fading it ate the first letter of every settled row (G2: "All" read "\ll"). */
const SUMMARY_MASK = {
  start: "linear-gradient(to right, transparent 0, black 16px, black 100%)",
  end: "linear-gradient(to right, black 0, black calc(100% - 16px), transparent 100%)",
} as const;

type ScrollMask = "none" | "top" | "bottom" | "both";

/** The reference design builds ONE gradient whose stops depend on which edges are hidden — a composite of two
 *  masks would union instead of intersect. 24px edges, matching their scroll helper. */
export function scrollMaskStyle(state: ScrollMask, edge = 24): CSSProperties | undefined {
  if (state === "none") return undefined;
  const top = state === "top" || state === "both";
  const bottom = state === "bottom" || state === "both";
  const first = top ? `transparent 0px, black ${edge}px` : `black 0px, black ${edge}px`;
  const last = bottom
    ? `black calc(100% - ${edge}px), transparent 100%`
    : `black calc(100% - ${edge}px), black 100%`;
  const maskImage = `linear-gradient(to bottom, ${first}, ${last})`;
  return {
    WebkitMaskImage: maskImage,
    maskImage,
    WebkitMaskRepeat: "no-repeat",
    maskRepeat: "no-repeat",
    WebkitMaskSize: "100% 100%",
    maskSize: "100% 100%",
  };
}

/** Is the summary line wider than the row? (Its hidden edge fades when it is.) */
function useSummaryOverflow(ref: React.RefObject<HTMLElement | null>, dependency: string): boolean {
  const [masked, setMasked] = useState(false);
  useEffect(() => {
    const element = ref.current;
    if (!element) return;
    const next = () => {
      const value = element.scrollWidth > element.clientWidth + 1;
      setMasked((current) => (current === value ? current : value));
    };
    next();
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(next);
    observer.observe(element);
    return () => observer.disconnect();
  }, [ref, dependency]);
  return masked;
}

function ThinkRowInner({
  text,
  running,
  duration,
}: {
  text: string;
  running?: boolean;
  /** Seconds the block spent thinking, when a span was measurable. Rest-only: a running
   * row is still accumulating, and a ticking clock on scratch paper reads as a deadline. */
  duration?: number;
}) {
  const [open, setOpen] = useState(false);
  const summaryRef = useRef<HTMLSpanElement>(null);
  const bodyRef = useRef<HTMLDivElement>(null);
  const summary = plainLine(running ? latestLine(text) : firstLine(text));
  const summaryMasked = useSummaryOverflow(summaryRef, summary);
  const summaryEdge = running ? "start" : "end";
  const following = useRef(true);
  const [bodyMask, setBodyMask] = useState<ScrollMask>("none");

  const syncBody = () => {
    const element = bodyRef.current;
    if (!element) return;
    if (running && following.current) element.scrollTop = element.scrollHeight;
    const hidden = element.scrollHeight - element.clientHeight;
    const atTop = element.scrollTop <= 1;
    const atBottom = element.scrollTop >= hidden - 1;
    const next: ScrollMask = hidden <= 1 ? "none" : atTop ? "bottom" : atBottom ? "top" : "both";
    setBodyMask((current) => (current === next ? current : next));
  };

  // Layout effect, not effect: the newest token must never paint one frame scrolled behind.
  useLayoutEffect(() => {
    syncBody();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [text, running, open]);

  // A width/height change moves both edges of the mask with no scroll event.
  useEffect(() => {
    const element = bodyRef.current;
    if (!element || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(syncBody);
    observer.observe(element);
    return () => observer.disconnect();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  // The collapsed line starts pinned to its end while running, and to its start once settled.
  useLayoutEffect(() => {
    const element = summaryRef.current;
    if (!element) return;
    element.scrollLeft = running ? element.scrollWidth - element.clientWidth : 0;
  }, [summary, running]);

  const title = running
    ? "Thinking"
    : duration !== undefined && duration > 0
      ? `Thought · ${thoughtFor(duration)}`
      : "Thought";
  return (
    <div
      className="think-row"
      data-variant="think"
      data-state={running ? "running" : "ok"}
      data-testid="think-row"
    >
      {running && <span className="sr-only">Reasoning</span>}
      <DisclosureRow
        rowClassName="think-row-line"
        leadingClassName="think-row-leading"
        // While it runs, the label shimmers — the motion is the
        // "still working" signal, and it rides the title, not a band sweeping the row.
        titleClassName={"think-row-title" + (running ? " shimmer-text" : "")}
        icon={<Icon name="brain" size={14} running={running} />}
        title={title}
        open={open}
        expandable
        expandOnRowClick
        keepContentWhenOpen
        onToggle={() => setOpen((value) => !value)}
        collapsedContent={
          <>
            {summary !== "" && (
              <>
                <span className="rv-dot" aria-hidden />
                <span
                  ref={summaryRef}
                  className="think-row-summary"
                  data-mask={summaryMasked ? summaryEdge : "none"}
                  data-reasoning-streaming-line
                  data-testid="think-summary"
                  style={
                    summaryMasked
                      ? { maskImage: SUMMARY_MASK[summaryEdge], WebkitMaskImage: SUMMARY_MASK[summaryEdge] }
                      : undefined
                  }
                >
                  {summary}
                </span>
              </>
            )}
          </>
        }
      >
        <div
          ref={bodyRef}
          className="think-row-body"
          data-testid="think-body"
          data-mask={bodyMask}
          style={scrollMaskStyle(bodyMask)}
          onScroll={() => {
            const element = bodyRef.current;
            if (!element) return;
            following.current = element.scrollHeight - element.scrollTop - element.clientHeight <= 2;
            syncBody();
          }}
        >
          {text}
        </div>
      </DisclosureRow>
    </div>
  );
}

export const ThinkRow = memo(ThinkRowInner);
