// Talking about a design by touching it (phase 3): what the page's design bridge reports
// (`harness/interface/server/preview_design.py`) becomes something the agent can act on.
//
// - A pointed element becomes a text attachment in the composer: where it is (a CSS path), what it
//   says, the start of its HTML, its size, so "make this bigger" has a "this".
// - Pinned comments become one numbered attachment, answered by number.
// - Retyped text is placed in the file where the old text occurs exactly once, or not at all.
//
// The page is not trusted, so everything it reports is text: quoted, clipped, never markup.
import type { Attachment } from "../../types";

/** How an element is drawn, as the page computed it. */
export interface ElementStyle {
  font?: string;
  size?: number;
  weight?: string;
  color?: string;
  bg?: string;
  radius?: string;
}

/** An element as the page described it. Every field is the page's word for it. */
export interface PageElement {
  selector: string;
  tag: string;
  cls?: string;
  text?: string;
  html?: string;
  w?: number;
  h?: number;
  /** Where it is on the page (not the window), in px. */
  x?: number;
  y?: number;
  style?: ElementStyle;
}

export interface DesignPin {
  n: number;
  el: PageElement;
  fx: number;
  fy: number;
  comment: string;
  sent?: boolean;
  /** Where on the window the pin was dropped, for the box its words are typed in. */
  cx?: number;
  cy?: number;
  /** The page changed and its element is gone: the comment no longer has a place. */
  lost?: boolean;
}

/** The scope every comment, point and mark is sent with: the harness checks it after the write
 *  (what changed outside what was pointed at). */
export const SCOPE_RULE =
  "Change only the parts named here; anything else you notice, say it in your answer instead of changing it.";

/** Asked of the app's window to put attachments (and, when the composer is empty, words) in the
 *  composer, keeping what the person has typed. */
export const DESIGN_ATTACH_EVENT = "reeve:design-attach";

const clip = (value: unknown, max: number) => {
  const text = String(value ?? "").replace(/\s+/g, " ").trim();
  return text.length > max ? `${text.slice(0, max)}…` : text;
};

/** "“Choose plan” button", or "div.plans" when it says nothing: the chip's name. */
export function elementLabel(el: PageElement): string {
  const words = clip(el.text, 32);
  const kind = el.cls ? `${el.tag}.${el.cls.split(".")[0]}` : el.tag;
  const role = el.tag === "a" ? "link" : el.tag === "button" ? "button" : kind;
  return words ? `“${words}” ${role}` : kind;
}

/** "Manrope 15 px / 600 · #ffffff on #6a3577 · corners 8px": how an element looks, in a line. */
export function styleLine(style?: ElementStyle): string {
  if (!style) return "";
  const type = [clip(style.font, 40), style.size ? `${style.size} px` : "", style.weight ? `/ ${clip(style.weight, 4)}` : ""]
    .filter(Boolean)
    .join(" ");
  const colour = style.color ? `${clip(style.color, 24)}${style.bg ? ` on ${clip(style.bg, 24)}` : ""}` : "";
  const corners = style.radius ? `corners ${clip(style.radius, 20)}` : "";
  return [type, colour, corners].filter(Boolean).join(" · ");
}

function describe(el: PageElement, width?: number): string[] {
  const lines = [`selector: ${clip(el.selector, 300)}`];
  if (el.text) lines.push(`text: “${clip(el.text, 120)}”`);
  if (el.w !== undefined && el.h !== undefined) {
    lines.push(`size: ${el.w} × ${el.h} px${width ? ` in a ${width} px window` : ""}`);
  }
  if (el.x !== undefined && el.y !== undefined) lines.push(`position: x ${el.x}, y ${el.y} on the page`);
  const look = styleLine(el.style);
  if (look) lines.push(`style: ${look}`);
  if (el.html) lines.push(`html: ${clip(el.html, 400)}`);
  return lines;
}

/** A pointed element as the attachment the composer carries. */
export function pointedAttachment(file: string, el: PageElement, width?: number): Attachment {
  return {
    kind: "text",
    name: `${file} · ${elementLabel(el)}`,
    text: [`The person pointed at this element of ${file}, as the page is now. ${SCOPE_RULE}`, ...describe(el, width)].join("\n"),
  };
}

/** Pins with words, as one numbered attachment. Pins with no comment are left out. */
export function pinsAttachment(file: string, pins: DesignPin[], width?: number): Attachment | null {
  const said = pins.filter((pin) => pin.comment.trim());
  if (said.length === 0) return null;
  const blocks = said.map((pin) =>
    [`${pin.n}. ${clip(pin.comment, 2000)}`, ...describe(pin.el, width).map((line) => `   ${line}`)].join("\n"),
  );
  return {
    kind: "text",
    name: `${file} · ${said.length} comment${said.length === 1 ? "" : "s"}`,
    text: [
      `The person pinned ${said.length} comment${said.length === 1 ? "" : "s"} on ${file}. Answer each by its number: what you changed, or why not. ${SCOPE_RULE}`,
      "",
      ...blocks,
    ].join("\n"),
  };
}

const escapeHtml = (text: string) => text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

const count = (haystack: string, needle: string) => {
  if (!needle) return 0;
  let n = 0;
  for (let at = haystack.indexOf(needle); at !== -1; at = haystack.indexOf(needle, at + needle.length)) n += 1;
  return n;
};

/** Where retyped text goes in the file: the one place the old text is, as written (raw or with its
 *  entities), else nowhere. An ambiguous change is never guessed at. */
export function placeRetype(
  source: string,
  before: string,
  after: string,
): { ok: true; content: string } | { ok: false; reason: "missing" | "ambiguous" | "unchanged" } {
  if (before === after) return { ok: false, reason: "unchanged" };
  const forms = Array.from(new Set([before, escapeHtml(before), before.trim(), escapeHtml(before.trim())])).filter(Boolean);
  let ambiguous = false;
  for (const form of forms) {
    const seen = count(source, form);
    if (seen === 1) {
      const trimmed = form === before.trim() || form === escapeHtml(before.trim());
      // The source's own indentation around the text stays when the new text has none of its own.
      const kept = !trimmed && after.trim() === after ? before.replace(before.trim(), escapeHtml(after)) : null;
      const replacement = kept ?? escapeHtml(trimmed ? after.trim() : after);
      const at = source.indexOf(form);
      return { ok: true, content: source.slice(0, at) + replacement + source.slice(at + form.length) };
    }
    if (seen > 1) ambiguous = true;
  }
  return { ok: false, reason: ambiguous ? "ambiguous" : "missing" };
}

/** What the agent is told of a retype that was saved, or asked to do when it could not be placed. */
export function retypeAttachment(file: string, el: PageElement, before: string, after: string, saved: boolean): Attachment {
  const was = clip(before, 300);
  const now = clip(after, 300);
  return {
    kind: "text",
    name: `${file} · ${saved ? "text retyped" : "text to change"}`,
    text: saved
      ? `The person retyped text in ${file} (${clip(el.selector, 200)}): “${was}” → “${now}”. The file is saved with it; read it again before editing.`
      : `The person retyped text in the preview of ${file} (${clip(el.selector, 200)}) and it could not be placed in the file: change “${was}” to “${now}”.`,
  };
}

/** A lever a page declared (`#reeve-tweaks`): a custom property on the root or an attribute on
 *  `<html>`, and its options. `current` is what the page loaded with. */
export interface Lever {
  name: string;
  label: string;
  css: string | null;
  attr: string | null;
  options: string[];
  current: string;
}

/** The levers the page reported, as text fields only; a malformed one is dropped. */
export function asLevers(value: unknown): Lever[] {
  if (!Array.isArray(value)) return [];
  const out: Lever[] = [];
  for (const raw of value.slice(0, 8)) {
    if (!raw || typeof raw !== "object") continue;
    const v = raw as Record<string, unknown>;
    const options = Array.isArray(v.options) ? v.options.filter((o): o is string => typeof o === "string").map((o) => o.slice(0, 60)).slice(0, 8) : [];
    const css = typeof v.css === "string" && /^--[\w-]+$/.test(v.css) ? v.css : null;
    const attr = typeof v.attr === "string" && /^[a-z][\w-]*$/i.test(v.attr) ? v.attr : null;
    if (typeof v.name !== "string" || options.length < 2 || (!css && !attr)) continue;
    out.push({
      name: v.name.slice(0, 40),
      label: typeof v.label === "string" ? v.label.slice(0, 40) : v.name.slice(0, 40),
      css,
      attr,
      options,
      current: typeof v.current === "string" ? v.current.slice(0, 60) : options[0],
    });
  }
  return out;
}

/** Whether an option reads as a colour, so it is offered as a swatch. */
export const isColor = (value: string) => /^#[0-9a-f]{3,8}$/i.test(value) || /^(rgb|hsl|oklch)a?\(/i.test(value);

/** The levers moved from what the page loaded with, as what the agent is asked to keep. */
export function tweaksAttachment(file: string, levers: Lever[], chosen: Record<string, string>): Attachment | null {
  const moved = levers.filter((lever) => chosen[lever.name] && chosen[lever.name] !== lever.current);
  if (moved.length === 0) return null;
  const lines = moved.map((lever) => {
    const where = lever.css ? `${lever.css} on :root` : `${lever.attr} on <html>`;
    return `- ${lever.label}: ${chosen[lever.name]} (was ${lever.current}; ${where})`;
  });
  return {
    kind: "text",
    name: `${file} · tweaks`,
    text: [`The person tried these tweaks on ${file} and wants them kept in the file:`, ...lines].join("\n"),
  };
}

/** The variants of a page in its folder: `pricing.a.html`, `pricing.b.html`… beside `pricing.html`
 *  (or beside one another). Two or more, in letter order; else none. */
export function variantsOf(fileName: string, names: string[]): { base: string; letter: string; name: string }[] {
  const match = /^(.*?)(?:\.([a-z]))?\.(html?)$/i.exec(fileName);
  if (!match) return [];
  const stem = match[1];
  const pattern = new RegExp(`^${stem.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}\\.([a-z])\\.html?$`, "i");
  const found = names
    .map((name) => ({ name, hit: pattern.exec(name) }))
    .filter((entry) => entry.hit)
    .map((entry) => ({ base: `${stem}.${match[3]}`, letter: entry.hit![1].toLowerCase(), name: entry.name }))
    .sort((a, b) => a.letter.localeCompare(b.letter));
  return found.length >= 2 ? found : [];
}

/** What keeping one variant asks of the agent. */
export function keepVariantText(base: string, kept: string, others: string[]): string {
  const rest = others.length ? `, and delete the other variants (${others.join(", ")})` : "";
  return `Keep ${kept} as ${base}: make ${base} what ${kept} is${rest}.`;
}

/** Put attachments (or only words) in the composer, keeping what the person typed; `text` goes in
 *  only when the composer is empty. */
export function attachToComposer(attachments: Attachment[], text?: string): void {
  window.dispatchEvent(new CustomEvent(DESIGN_ATTACH_EVENT, { detail: { attachments, text } }));
}

/** A mark the person drew on the page (Mark): a box, a stroke or words, with a note. */
export interface DesignMark {
  note: string;
  kinds: ("box" | "pen" | "text")[];
  /** The marked area on the page, in px, at `width`. */
  area: { x: number; y: number; w: number; h: number };
  width: number;
  /** The elements under the mark, as the page named them. */
  under: PageElement[];
  /** The picture attached beside these words: the area with the ink on it, the area alone (the ink
   *  could not be drawn on it), or none (the area could not be shot). */
  pictured: "inked" | "plain" | "none";
}

/** What a mark asks of the agent: the note, where it is, the parts under it (their selectors, so
 *  the harness knows what was pointed at), and that the picture beside it shows the ink. */
export function markAttachment(file: string, mark: DesignMark): Attachment {
  const what = mark.kinds.length
    ? mark.kinds.map((kind) => (kind === "box" ? "a box" : kind === "pen" ? "a stroke" : "words")).join(" and ")
    : "a mark";
  const note = mark.note.trim() ? ` and wrote: “${clip(mark.note, 1000)}”` : "";
  const lines = [
    `The person drew ${what} on ${file}${note}. ${SCOPE_RULE}`,
    `area: x ${mark.area.x}–${mark.area.x + mark.area.w}, y ${mark.area.y}–${mark.area.y + mark.area.h} on the page, in a ${mark.width} px window`,
    mark.pictured === "inked"
      ? "picture: attached beside this, the area with the ink on it"
      : mark.pictured === "plain"
        ? "picture: attached beside this, the area without the ink"
        : "picture: none could be taken; find the area from these lines",
  ];
  mark.under.slice(0, 4).forEach((el, index) => {
    lines.push(`under it ${index + 1}: ${elementLabel(el)}`, ...describe(el, mark.width).map((line) => `   ${line}`));
  });
  return { kind: "text", name: `${file} · a mark`, text: lines.join("\n") };
}
