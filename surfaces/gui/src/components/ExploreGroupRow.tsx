// The Explored line: a run of LOOKING calls (reading, searching, listing, git's read-only
// commands, looking a tool up) and the thoughts between them, as one line of the timeline
// named by what was looked at ("export.py, writer.py · 1 search · thought 10s"). Owner,
// 2026-10-13, design A: grouping follows meaning, so a thought between two reads no longer
// splits the run (it did, and grouping looked like chance), and the line says what was read
// instead of "2 reads". Opened, every call and every thought is there in order.
//
// A thought right before something is DONE is not in here: it is the decision, and it stays
// a line of its own. A call that failed is never in here either. One look alone is a plain
// row. The header shimmers while any member is in flight.

import { useState, memo } from "react";
import type { ToolItem } from "../cards/cardModels";
import { exploreLine } from "../toolMeta";
import { Disclosure } from "../ui/Disclosure";
import { Icon } from "./Icon";
import { ThinkRow } from "./ThinkRow";
import { ToolCallRow } from "./ToolCallRow";

/** One member of a looking run, in the order it happened. */
export type ExploreEntry =
  | { kind: "tool"; tool: ToolItem }
  | { kind: "thought"; text: string; seconds?: number };

function ExploreGroupRowInner({
  entries,
  onReveal,
}: {
  entries: ExploreEntry[];
  onReveal?: (path: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const tools = entries.flatMap((e) => (e.kind === "tool" ? [e.tool] : []));
  const thought = entries.reduce((sum, e) => sum + (e.kind === "thought" ? e.seconds ?? 0 : 0), 0);
  const running = tools.some((t) => t.status === "\u2026");
  const line = exploreLine(tools, thought);
  const toggle = () => setOpen((v) => !v);
  return (
    <div
      className="tool-row explore-group"
      data-state={running ? "running" : "ok"}
      data-testid="explore-group"
    >
      <div
        className="tool-row-line"
        role="button"
        tabIndex={0}
        aria-expanded={open}
        onClick={toggle}
        onKeyDown={(event) => {
          if (event.key !== "Enter" && event.key !== " ") return;
          event.preventDefault();
          toggle();
        }}
      >
        <span className="tool-row-leading">
          <Icon name="search" size={14} running={running} />
        </span>
        <span className={"tool-row-title" + (running ? " shimmer-text" : "")}>
          {running ? "Exploring" : "Explored"}
        </span>
        {line.full && (
          <span className="tool-row-summary explore-line" title={line.full}>
            <span className="explore-line-full">{line.full}</span>
            <span className="explore-line-short">{line.short}</span>
          </span>
        )}
        <span className={"tool-row-chevron" + (open ? " open" : "")} aria-hidden>
          <Icon name="chevronRight" size={12} />
        </span>
      </div>
      {/* The calls open and close as a motion, the way one row's body does. */}
      <Disclosure open={open}>
        <div className="explore-group-body">
          {entries.map((entry, i) =>
            entry.kind === "tool" ? (
              <ToolCallRow key={entry.tool.id} tool={entry.tool} onOpenFile={onReveal} />
            ) : (
              <ThinkRow key={`thought-${i}`} text={entry.text} duration={entry.seconds} />
            ),
          )}
        </div>
      </Disclosure>
    </div>
  );
}

const sameEntries = (a: ExploreEntry[], b: ExploreEntry[]) =>
  a.length === b.length &&
  a.every((entry, i) => {
    const other = b[i];
    if (entry.kind === "tool") return other.kind === "tool" && other.tool === entry.tool;
    return other.kind === "thought" && other.text === entry.text && other.seconds === entry.seconds;
  });

export const ExploreGroupRow = memo(
  ExploreGroupRowInner,
  (a, b) => sameEntries(a.entries, b.entries) && a.onReveal === b.onReveal,
);
