// A long answer opens with its contents (owner, 2026-10-13): 307 answers in the store have four
// headings or more, and one in a hundred runs to about six thousand characters, so coming back
// for "what is left to do" was a scroll. The line names the sections at one level and each name
// jumps to its section. Nothing is folded: a finished answer stays shown whole.

import { type RefObject } from "react";
import { Icon } from "../Icon";

export interface Heading {
  level: number;
  text: string;
}

/** An answer with this many headings or more gets its contents line. */
export const CONTENTS_FROM = 4;

const ATX = /^ {0,3}(#{1,6})[ \t]+(.+?)[ \t]*#*[ \t]*$/;
const FENCE = /^\s{0,3}(```|~~~)/;

/** A heading's words without the marks around them (`**`, backticks, a link's target). */
export function plainHeading(text: string): string {
  return text
    .replace(/!?\[([^\]]*)\]\([^)]*\)/g, "$1")
    .replace(/(\*\*|__|~~|`)/g, "")
    .replace(/(^|\s)[*_](\S)/g, "$1$2")
    .replace(/(\S)[*_](\s|$)/g, "$1$2")
    .trim();
}

/** The answer's own headings (`#` lines outside fences), in order. */
export function headingsOf(markdown: string): Heading[] {
  const out: Heading[] = [];
  let fence: string | null = null;
  for (const line of markdown.split("\n")) {
    const marker = FENCE.exec(line)?.[1];
    if (fence) {
      if (marker === fence) fence = null;
      continue;
    }
    if (marker) {
      fence = marker;
      continue;
    }
    const found = ATX.exec(line);
    if (found) {
      const text = plainHeading(found[2]);
      if (text) out.push({ level: found[1].length, text });
    }
  }
  return out;
}

/** The sections the contents line names: the highest level that holds two or more, once the
 *  answer has `CONTENTS_FROM` headings in all; otherwise none. */
export function contentsOf(headings: Heading[]): Heading[] | null {
  if (headings.length < CONTENTS_FROM) return null;
  for (let level = 1; level <= 6; level++) {
    const at = headings.filter((heading) => heading.level === level);
    if (at.length >= 2) return at;
  }
  return null;
}

const squash = (text: string) => text.replace(/\s+/g, " ").trim().toLowerCase();

export function AnswerContents({ entries, container }: { entries: Heading[]; container: RefObject<HTMLElement> }) {
  const jump = (index: number) => {
    const entry = entries[index];
    const shown = [...(container.current?.querySelectorAll<HTMLElement>(`h${entry.level}`) ?? [])];
    // By its words first (a heading inside a quote is not one of ours), by its place second.
    const target = shown.find((node) => squash(node.textContent ?? "") === squash(entry.text)) ?? shown[index];
    target?.scrollIntoView?.({ behavior: "smooth", block: "start" });
  };
  return (
    <nav className="md-contents" aria-label="In this answer" data-testid="md-contents">
      <span className="md-contents-lead">
        <Icon name="list" size={13} />
        In this answer
      </span>
      {entries.map((entry, index) => (
        <button key={index} type="button" className="md-contents-link" onClick={() => jump(index)}>
          {entry.text}
        </button>
      ))}
    </nav>
  );
}
