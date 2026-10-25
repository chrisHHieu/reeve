// The to-dos: the standing `todo_write` list, as a segment of the activity row above the
// composer (`session/ActivityRow.tsx`) and the list that opens under it.
//
// One line per task with a glyph that says which of the three states it is in, and nothing at
// all when the plan is empty: no fallback, because an empty plan is a true answer about a job
// that did not need one. It once listed the last four tool calls instead, and that list came
// from the whole transcript while the plan came from the current turn, so a new turn opened
// showing the previous turn's steps as though they were running. The transcript already
// streams every step, in this same column, correctly scoped.
//
// The status vocabulary is reeve's (`done`, not `completed`); the words in the count's title
// are the ones a person reads, which is why the label still says "completed".
//
// The list opens only when the person opens it (owner, 2026-09-28): the row already names the
// step under way and the count. There is no progress bar, and no line that reads as one
// (owner, 2026-09-28 and 2026-10-12). The standing plan and its lifetime are `todoPlan.ts`'s
// business.

import { useId } from "react";
import type { TodoItem } from "../types";

type Status = TodoItem["status"];

/**
 * The header summary: per-status counts, zero-count segments dropped as noise. A non-empty
 * list always keeps at least one segment, so the header is never blank while the card is.
 *
 * En spaces around the separator (U+2002): HTML collapses runs of ASCII spaces, so the
 * breathing room has to be a literal wide space.
 */
export function progressLabel(items: readonly TodoItem[]): string {
  const done = items.filter((t) => t.status === "done").length;
  const active = items.filter((t) => t.status === "in_progress").length;
  const pending = items.length - done - active;
  return [
    ...(done > 0 ? [`${done} completed`] : []),
    ...(active > 0 ? [`${active} in progress`] : []),
    ...(pending > 0 ? [`${pending} pending`] : []),
  ].join("\u2002\u00b7\u2002");
}

/** "1 of 3", or "3 of 3 done" once every step is — the header's glanceable count. */
export function progressCount(items: readonly TodoItem[]): string {
  const done = items.filter((t) => t.status === "done").length;
  return done === items.length ? `${done} of ${items.length} done` : `${done} of ${items.length}`;
}

/** The plan, one line a task, each with the glyph of its state. */
export function TodoList({ items }: { items: readonly TodoItem[] }) {
  const line = (item: TodoItem, index: number) => (
    <li key={index} className={`todo-dock-item ${item.status}`} data-status={item.status}>
      <span className="todo-dock-glyph" aria-hidden>
        <StatusGlyph status={item.status} />
      </span>
      <span className="todo-dock-content">{item.content}</span>
    </li>
  );
  return (
    <ul className="todo-dock-list" data-testid="todo-dock-list">
      {items.map(line)}
    </ul>
  );
}

// ---- Status glyphs (ported from the TodoPanel.tsx, the figma 14×14 artboard) ----
// One shape, three treatments: the marks differ only by STATE, never by shape.

/** Done: an outlined circle with the check drawn inside — lighter than a solid disc. */
function CompletedGlyph() {
  return (
    <svg width={14} height={14} viewBox="0 0 14 14" fill="none" aria-hidden="true" className="todo-dock-glyph-completed">
      <circle cx="7" cy="7" r="6.4" stroke="currentColor" strokeWidth="1.2" />
      <path
        d="M10.9631 5.71411L7.70154 8.97571C7.48011 9.19714 7.27736 9.40099 7.09229 9.54993C6.89742 9.70669 6.66314 9.85279 6.3634 9.90027C6.2049 9.92534 6.04339 9.92534 5.88489 9.90027C5.58515 9.85279 5.35087 9.70669 5.15601 9.54993C4.97093 9.40099 4.76818 9.19714 4.54675 8.97571L3.03516 7.46411L3.96313 6.53613L5.47473 8.04773C5.7169 8.28989 5.86196 8.43389 5.97888 8.52795C6.08597 8.61409 6.10875 8.60701 6.08997 8.604C6.11259 8.60758 6.13571 8.60758 6.15833 8.604C6.13954 8.60701 6.16232 8.61409 6.26941 8.52795C6.38633 8.43389 6.53139 8.28989 6.77356 8.04773L10.0352 4.78613L10.9631 5.71411Z"
        fill="currentColor"
      />
    </svg>
  );
}

/** In progress: a ring stroked with a gradient that fades to transparent, spinning —
    a comet tail, "under way" rather than "waiting". */
function ProgressGlyph() {
  const gradientId = useId();
  return (
    <svg width={14} height={14} viewBox="0 0 14 14" fill="none" aria-hidden="true" className="todo-dock-glyph-progress">
      <defs>
        <linearGradient id={gradientId} x1="2.5" y1="12" x2="10.5" y2="3.5" gradientUnits="userSpaceOnUse">
          <stop stopColor="currentColor" />
          <stop offset="1" stopColor="currentColor" stopOpacity="0" />
        </linearGradient>
      </defs>
      <circle cx="7" cy="7" r="6.4" stroke={`url(#${gradientId})`} strokeWidth="1.2" />
    </svg>
  );
}

/** Pending: a dashed, unstarted ring. */
function PendingGlyph() {
  return (
    <svg width={14} height={14} viewBox="0 0 14 14" fill="none" aria-hidden="true" className="todo-dock-glyph-pending">
      <circle cx="7" cy="7" r="6.4" stroke="currentColor" strokeWidth="1.2" strokeDasharray="2.4 2.4" />
    </svg>
  );
}

/** A state the list does not know reads as pending. */
export function StatusGlyph({ status }: { status: Status }) {
  if (status === "done") return <CompletedGlyph />;
  if (status === "in_progress") return <ProgressGlyph />;
  return <PendingGlyph />;
}
