// Which question am I reading? Once you have scrolled up and the message that started the
// turn is above the view, one line under the top bar says it: "You asked · <your message>"
// (Claude Code's sticky prompt, owner ask 2026-09-28; redrawn 2026-10-13).
//
// It was a floating card over the answer: it lay on the lines being read, looked like the
// composer, and was drawn across the top bar's popovers. It is a line of the window's frame
// now: flush under the title, the page's own colour, no shadow and no blur. It also moves:
// the arrows go to your message above and below, and the count opens the list of everything
// you asked in this conversation (`Alt ↑` / `Alt ↓` do the same from the keyboard).
//
// A turn nobody typed (a subagent's report, a background command that ended) says what
// started it; the line used to go on showing your earlier message over an answer that was
// not to it.
//
// Only for a reader who scrolled away: at the live bottom the turn being read is the one
// running, and a line appearing there was motion nobody asked for (owner, 2026-09-28). One
// overlay for the whole column, not a sticky copy per turn: it never changes the transcript's
// layout, and the scroll handler does its work at most once per frame. Everything is read from
// the marks the transcript puts on a turn's first row (`data-turn-anchor`), so this component
// holds no copy of the conversation.

import { useCallback, useEffect, useRef, useState, type RefObject } from "react";
import { Icon } from "../Icon";
import { AnchoredPopover } from "../primitives/AnchoredPopover";

/** The line's height: a turn's first row is "above" once it has passed under the line. */
const LINE_PX = 32;
/** Where a message lands when it is gone to: this far under the view's top. */
const LAND_PX = 16;

export interface TurnAnchor {
  /** The transcript's own id for the turn's first row. */
  id: string;
  /** The person's message, or a turn the engine started. */
  kind: "user" | "engine";
  /** What started an engine turn ("Subagent finished"); empty for a message. */
  label: string;
  text: string;
  /** How many files the message carried. */
  files: number;
  /** Epoch seconds, when the row has a stamp. */
  ts: number | null;
  icon: "bot" | "terminal";
  el: HTMLElement;
}

/** The last row whose box has fully scrolled above `top`, or null. */
export function turnAbove<T extends HTMLElement>(messages: Iterable<T>, top: number): T | null {
  let current: T | null = null;
  for (const message of messages) {
    if (message.getBoundingClientRect().bottom <= top + 4) current = message;
    else break;
  }
  return current;
}

/** Every turn's first row in the scroller, in order. */
export function readAnchors(scroller: HTMLElement): TurnAnchor[] {
  return [...scroller.querySelectorAll<HTMLElement>("[data-turn-anchor]")].map((el) => {
    const ts = Number(el.dataset.turnTs);
    return {
      id: el.dataset.turnAnchor || "",
      kind: el.dataset.turnKind === "engine" ? "engine" : "user",
      label: el.dataset.turnLabel || "",
      text: el.dataset.turnText || "",
      files: Number(el.dataset.turnFiles) || 0,
      ts: Number.isFinite(ts) && ts > 0 ? ts : null,
      icon: el.dataset.turnIcon === "terminal" ? "terminal" : "bot",
      el,
    };
  });
}

/** "2 files" / "1 file". */
export function filesWords(count: number): string {
  return `${count} file${count === 1 ? "" : "s"}`;
}

/** What a turn is called in one line: its words, or its files when it had no words. */
export function anchorWords(anchor: Pick<TurnAnchor, "text" | "files">): string {
  return anchor.text.trim() || (anchor.files > 0 ? filesWords(anchor.files) : "");
}

/** A message's place among the person's own: its number, or the number of the last one before
 *  a turn the engine started (0 when there is none). */
export function ordinalOf(anchors: readonly Pick<TurnAnchor, "id" | "kind">[], id: string): number {
  let count = 0;
  for (const anchor of anchors) {
    if (anchor.kind === "user") count += 1;
    if (anchor.id === id) return count;
  }
  return 0;
}

function clock(ts: number | null): string {
  if (ts === null) return "";
  return new Date(ts * 1000).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}

function editable(target: EventTarget | null): boolean {
  const el = target as HTMLElement | null;
  if (!el || typeof el.closest !== "function") return false;
  return el.isContentEditable || el.closest("input, textarea, select, [contenteditable='true']") !== null;
}

interface Pinned extends Omit<TurnAnchor, "el"> {
  /** Its number among the person's messages (see `ordinalOf`). */
  ordinal: number;
  total: number;
  /** It is the conversation's last turn. */
  last: boolean;
}

function samePinned(a: Pinned | null, b: Pinned | null): boolean {
  if (a === null || b === null) return a === b;
  return (
    a.id === b.id &&
    a.text === b.text &&
    a.label === b.label &&
    a.files === b.files &&
    a.ordinal === b.ordinal &&
    a.total === b.total &&
    a.last === b.last
  );
}

export function TurnPin({
  scrollRef,
  watch,
  following,
  running = false,
}: {
  scrollRef: RefObject<HTMLDivElement | null>;
  watch: unknown;
  /** The view is pinned to the live bottom: nothing to show. */
  following: boolean;
  /** A turn is running: said on the line when it is the turn being read. */
  running?: boolean;
}) {
  const [pinned, setPinned] = useState<Pinned | null>(null);
  const [list, setList] = useState<TurnAnchor[] | null>(null);
  const countRef = useRef<HTMLButtonElement>(null);
  const listRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const scroller = scrollRef.current;
    if (!scroller || following) return;
    let frame = 0;
    const update = () => {
      frame = 0;
      const top = scroller.getBoundingClientRect().top;
      const anchors = readAnchors(scroller);
      // Under the line counts as above the view: the line arrives as the row leaves.
      const el = turnAbove(
        anchors.map((anchor) => anchor.el),
        top + LINE_PX - 4,
      );
      const at = el ? anchors.findIndex((anchor) => anchor.el === el) : -1;
      // The next turn starts at the very top of the view (where a message lands when it is
      // gone to): nothing of this turn is left on screen, so the line has nothing to name.
      const after = at >= 0 ? anchors[at + 1] : undefined;
      const gone = after !== undefined && after.el.getBoundingClientRect().top <= top + LINE_PX + LAND_PX;
      const current = at >= 0 && !gone ? anchors[at] : undefined;
      const next: Pinned | null = current
        ? {
            id: current.id,
            kind: current.kind,
            label: current.label,
            text: current.text,
            files: current.files,
            ts: current.ts,
            icon: current.icon,
            ordinal: ordinalOf(anchors, current.id),
            total: anchors.filter((anchor) => anchor.kind === "user").length,
            last: anchors[anchors.length - 1] === current,
          }
        : null;
      setPinned((prev) => (samePinned(prev, next) ? prev : next));
    };
    const schedule = () => {
      if (!frame) frame = requestAnimationFrame(update);
    };
    update();
    scroller.addEventListener("scroll", schedule, { passive: true });
    window.addEventListener("resize", schedule);
    return () => {
      if (frame) cancelAnimationFrame(frame);
      scroller.removeEventListener("scroll", schedule);
      window.removeEventListener("resize", schedule);
    };
    // `watch` changes when the transcript does, so a newly rendered message is seen.
  }, [scrollRef, watch, following]);

  const shown = !following && pinned !== null;

  /** Put a turn's first row just under the top of the view. */
  const goTo = useCallback(
    (el: HTMLElement | null | undefined) => {
      const scroller = scrollRef.current;
      if (!scroller || !el) return;
      const offset = el.getBoundingClientRect().top - scroller.getBoundingClientRect().top;
      const calm = typeof window.matchMedia === "function" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
      scroller.scrollTo({ top: scroller.scrollTop + offset - LAND_PX, behavior: calm ? "auto" : "smooth" });
    },
    [scrollRef],
  );

  /** Up is the nearest of your messages that starts above the view: the one this answer is
   *  to, then the one before it. Down is the next one below, and after the last, the end. */
  const step = useCallback(
    (direction: -1 | 1) => {
      const scroller = scrollRef.current;
      if (!scroller) return;
      const top = scroller.getBoundingClientRect().top;
      const mine = readAnchors(scroller).filter((anchor) => anchor.kind === "user");
      if (direction === -1) {
        const above = mine.filter((anchor) => anchor.el.getBoundingClientRect().top < top + LAND_PX - 8);
        goTo(above[above.length - 1]?.el);
        return;
      }
      const below = mine.find((anchor) => anchor.el.getBoundingClientRect().top > top + LAND_PX + 8);
      if (below) goTo(below.el);
      else {
        const calm = typeof window.matchMedia === "function" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
        scroller.scrollTo({ top: scroller.scrollHeight, behavior: calm ? "auto" : "smooth" });
      }
    },
    [scrollRef, goTo],
  );

  // Alt ↑ / Alt ↓ from anywhere in the conversation, except while typing: Option with an arrow
  // moves the caret by paragraph on a Mac.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (!event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return;
      if (event.key !== "ArrowUp" && event.key !== "ArrowDown") return;
      if (event.defaultPrevented || editable(event.target)) return;
      event.preventDefault();
      step(event.key === "ArrowUp" ? -1 : 1);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [step]);

  const goCurrent = () => {
    const scroller = scrollRef.current;
    if (!scroller || !pinned) return;
    goTo(scroller.querySelector<HTMLElement>(`[data-turn-anchor="${pinned.id}"]`));
  };

  const openList = () => {
    const scroller = scrollRef.current;
    setList((open) => (open !== null || !scroller ? null : readAnchors(scroller)));
  };
  const closeList = useCallback(() => {
    setList((open) => {
      if (open !== null) countRef.current?.focus();
      return null;
    });
  }, []);
  // The list opens on where you are, with the keyboard already in it.
  useEffect(() => {
    if (list === null) return;
    const frame = requestAnimationFrame(() => {
      const here = listRef.current?.querySelector<HTMLElement>("[data-on]") ?? listRef.current?.querySelector<HTMLElement>("button");
      here?.scrollIntoView?.({ block: "center" });
      here?.focus({ preventScroll: true });
    });
    return () => cancelAnimationFrame(frame);
  }, [list]);
  // A line that is gone takes its list with it.
  useEffect(() => {
    if (!shown) setList(null);
  }, [shown]);

  const words = pinned ? anchorWords(pinned) : "";
  const mine = pinned?.kind !== "engine";
  let seen = 0;

  return (
    <div className="turn-pin-strip" aria-hidden={!shown}>
      <div className="turn-pin" data-shown={shown ? "" : undefined} data-kind={pinned?.kind} data-testid="turn-pin">
        <span className="turn-pin-who" data-testid="turn-pin-who">
          {mine ? (
            "You asked"
          ) : (
            <>
              <Icon name={pinned?.icon ?? "bot"} size={14} />
              {pinned?.label}
            </>
          )}
        </span>
        <button
          type="button"
          className="turn-pin-text"
          data-testid="turn-pin-go"
          tabIndex={shown ? 0 : -1}
          title={shown ? words : undefined}
          aria-label={shown ? `Go to ${mine ? "your message" : "where this turn started"}: ${words}` : undefined}
          onClick={goCurrent}
        >
          {/* Keyed by turn: moving into another turn fades the words, not the whole line. */}
          <span key={pinned?.id ?? "none"}>{words}</span>
        </button>
        {pinned && pinned.files > 0 && pinned.text.trim() !== "" && (
          <span className="turn-pin-fact" data-testid="turn-pin-files">
            <Icon name="paperclip" size={12} />
            {filesWords(pinned.files)}
          </span>
        )}
        {running && pinned?.last && (
          <span className="turn-pin-fact" data-testid="turn-pin-working">
            <span className="turn-dots" aria-hidden>
              <i />
              <i />
            </span>
            Working
          </span>
        )}
        <span className="turn-pin-nav">
          <button
            type="button"
            className="turn-pin-step"
            data-testid="turn-pin-up"
            tabIndex={shown ? 0 : -1}
            aria-label="Go up to your message"
            title="Your message above (Alt ↑)"
            onClick={() => step(-1)}
          >
            <Icon name="chevronUp" size={14} />
          </button>
          {pinned !== null && pinned.ordinal > 0 && (
            <button
              type="button"
              ref={countRef}
              className="turn-pin-count"
              data-testid="turn-pin-count"
              tabIndex={shown ? 0 : -1}
              aria-haspopup="true"
              aria-expanded={list !== null}
              aria-label={`Your messages in this conversation, ${pinned.ordinal} of ${pinned.total}`}
              title="Your messages in this conversation"
              onClick={openList}
            >
              {pinned.ordinal} of {pinned.total}
            </button>
          )}
          <button
            type="button"
            className="turn-pin-step"
            data-testid="turn-pin-down"
            tabIndex={shown ? 0 : -1}
            aria-label="Go down to your next message"
            title="Your next message (Alt ↓)"
            onClick={() => step(1)}
          >
            <Icon name="chevronDown" size={14} />
          </button>
        </span>
      </div>
      <AnchoredPopover
        anchorRef={countRef}
        open={list !== null}
        onClose={closeList}
        width={360}
        label="Your messages in this conversation"
        testId="turn-pin-list"
        className="turn-pin-list pop-in-down"
      >
        <div className="turn-pin-list-head">Your messages</div>
        <div className="turn-pin-list-rows" ref={listRef}>
          {(list ?? []).map((anchor) => {
            if (anchor.kind === "user") seen += 1;
            return (
              <button
                key={anchor.id}
                type="button"
                className="turn-pin-item"
                data-kind={anchor.kind}
                data-on={anchor.id === pinned?.id ? "" : undefined}
                aria-current={anchor.id === pinned?.id ? "true" : undefined}
                data-testid="turn-pin-item"
                onClick={() => {
                  setList(null);
                  goTo(anchor.el);
                }}
              >
                <span className="turn-pin-item-n" aria-hidden={anchor.kind !== "user" || undefined}>
                  {anchor.kind === "user" ? seen : <Icon name={anchor.icon} size={12} />}
                </span>
                <span className="turn-pin-item-text">
                  {anchor.kind === "engine" && anchor.label ? `${anchor.label}${anchor.text ? " · " : ""}` : ""}
                  {anchorWords(anchor)}
                </span>
                <span className="turn-pin-item-time">{clock(anchor.ts)}</span>
              </button>
            );
          })}
        </div>
      </AnchoredPopover>
    </div>
  );
}
