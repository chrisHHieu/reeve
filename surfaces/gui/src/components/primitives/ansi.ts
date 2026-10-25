// ANSI colour for terminal output.
//
// The one hard part is carriage returns. A command that redraws a line — a progress bar, a
// spinner, most of npm — emits `\r` to move the cursor back and overwrite. Rendered
// naively, every frame becomes its own line and a 3-second install produces 400 lines of
// noise. The reference design solves this with a full column buffer that replays cursor
// movement, wide characters and erase sequences; that is ~450 lines of careful work, and
// it is the right thing for a harness whose terminal card is a first-class surface.
//
// This is the cheap version of the same idea: within a line, keep only what was written
// after the LAST carriage return, because that is what a terminal would be showing. That
// is exactly right for redraw-in-place progress output, and exactly wrong for a program
// that moves the cursor backwards to patch text mid-line — which is rare, and which
// degrades to showing the line's final state rather than garbage. Stated plainly: this
// parser shows the LAST frame of a redrawn line, not every frame.
//
// Colours map onto the theme where the theme has a semantic answer (a red error is
// `--danger`, not `rgb(205,49,49)`) and fall back to the literal colour otherwise. Same
// reasoning: a palette that ignores the theme is a palette
// that breaks in dark mode, and the eight basic ANSI hues are the ones that mean something.

import Anser from "anser";
import type { CSSProperties } from "react";

export interface AnsiSpan {
  text: string;
  /** Inline style for this run, or undefined for the default rendering. */
  style?: CSSProperties;
}

export type AnsiLine = AnsiSpan[];

/**
 * The sixteen basic ANSI foregrounds, keyed by the rgb anser reports for them and mapped
 * onto theme roles where one fits.
 *
 * The keys are anser's own palette rather than the conventional xterm one (its "red" is
 * `187,0,0`, not `205,49,49`), and they are stored WHITESPACE-STRIPPED because anser is
 * inconsistent about spaces inside the triplet — `"0, 0, 0"` beside `"255,255,255"`. A
 * lookup keyed on the raw string would therefore miss black and white entirely.
 *
 * Black and white map to brightness roles rather than to a hue, because that is what they
 * are: `--faint` for dark, `--ink` for light. The six hues map to the semantic role each
 * one is conventionally reaching for — which is also why the bright variants share their
 * base hue's role: a terminal's "bright red" is a lighter red, not a different colour.
 */
const FG_BY_RGB: Record<string, string> = {
  "0,0,0": "var(--faint)",
  "187,0,0": "var(--danger)",
  "0,187,0": "var(--ok)",
  "187,187,0": "var(--warn-ink)",
  "0,0,187": "var(--accent)",
  "187,0,187": "var(--teal-ink)",
  "0,187,187": "var(--accent)",
  "255,255,255": "var(--ink)",
  "85,85,85": "var(--faint)",
  "255,85,85": "var(--danger)",
  "0,255,0": "var(--ok)",
  "255,255,85": "var(--warn-ink)",
  "85,85,255": "var(--accent)",
  "255,85,255": "var(--teal-ink)",
  "85,255,255": "var(--accent)",
};

/** The theme role for an anser rgb triplet, or the literal colour when it has none. */
function foreground(rgb: string): string {
  const key = rgb.replace(/\s+/g, "");
  return FG_BY_RGB[key] ?? `rgb(${key.split(",").join(", ")})`;
}

/** The properties a decoration may set. Kept to one table so the styles stay consistent. */
const DECORATION_STYLE: Record<string, CSSProperties> = {
  bold: { fontWeight: 600 },
  dim: { opacity: 0.7 },
  italic: { fontStyle: "italic" },
  underline: { textDecoration: "underline" },
  strikethrough: { textDecoration: "line-through" },
  hidden: { visibility: "hidden" },
  // `blink` is deliberately absent — a blinking card in a transcript is a distraction the
  // output was not asking for, and every terminal that matters ignores it too.
};

function styleFor(part: {
  fg?: string;
  bg?: string;
  decoration?: string;
  decorations?: string[];
}): CSSProperties | undefined {
  const style: CSSProperties = {};
  if (part.fg) style.color = foreground(part.fg);
  if (part.bg) {
    // Backgrounds stay literal. A block character's backdrop is the program's own idea of
    // its colours — a progress bar drawn in the theme's "warning" tint would read as a
    // warning, which is the one thing it is not.
    style.background = `rgb(${part.bg.replace(/\s+/g, ", ")})`;
  }
  const names = part.decorations ?? (part.decoration ? [part.decoration] : []);
  for (const name of names) Object.assign(style, DECORATION_STYLE[name] ?? {});
  return Object.keys(style).length ? style : undefined;
}

/**
 * Parse ANSI text into lines of styled runs.
 *
 * A trailing newline is a line TERMINATOR, not an empty line: `"a\n"` is one line of
 * output, and a card rendering a blank second row looks like it dropped something.
 */
export function parseAnsiLines(text: string): AnsiLine[] {
  if (!text) return [];
  const raw = text.replace(/\r\n/g, "\n").split("\n");
  if (raw.length > 1 && raw[raw.length - 1] === "") raw.pop();
  return raw.map((line) => {
    const frame = line.includes("\r") ? line.slice(line.lastIndexOf("\r") + 1) : line;
    if (!frame) return [];
    // `remove_empty` keeps the runs aligned to the source text, so copying a line yields
    // what the command actually printed rather than a re-joined approximation.
    const parts = Anser.ansiToJson(frame, {
      json: true,
      remove_empty: true,
      use_classes: false,
    }) as Array<{
      content: string;
      fg?: string;
      bg?: string;
      decoration?: string;
      decorations?: string[];
    }>;
    const spans: AnsiSpan[] = [];
    for (const part of parts) {
      if (!part.content) continue;
      spans.push({ text: part.content, style: styleFor(part) });
    }
    return spans;
  });
}

/** Lines back as plain text, so a copy affordance can hand over what was printed. */
export function ansiLinesToText(lines: AnsiLine[]): string {
  return lines.map((line) => line.map((span) => span.text).join("")).join("\n");
}
