// Where a turn's visuals stand in its answer. A visual is part of the answer, not a
// step beside it: the model places one with a line of its own, `:visual[v1]`, and the answer is
// drawn as its prose with each visual where it was placed. The answer draws what it placed and
// nothing else: a model also shows drafts (the probes, 2026-10-09: one turn showed five, two of
// them titled "Test"), and only the visuals it chose to put in its answer are the answer. When it
// placed none, the last one shown stands after the opening paragraph, where a lead sentence hands
// over to it; the others stay on their step rows, folded.

import { readBlocks, type VisualBlock } from "./visualModel";

export interface TurnVisual {
  /** The name the harness gave it (`v1`), which the answer places it by. */
  id?: string;
  /** The call that showed it: the anchor its step row scrolls to. */
  callId: string;
  blocks: VisualBlock[];
}

type Carrier = { kind?: string; name?: string; callId?: string; id?: string; presentation?: { card?: unknown } };

/** The visuals a turn's calls showed, in the order they were shown. */
export function turnVisuals(items: readonly unknown[] | undefined): TurnVisual[] {
  const out: TurnVisual[] = [];
  for (const raw of items ?? []) {
    const item = raw as Carrier;
    if (item?.kind !== "tool" || item.name !== "show_visual") continue;
    const card = item.presentation?.card as { card?: string; id?: unknown; blocks?: unknown } | undefined;
    if (card?.card !== "visual") continue;
    const blocks = readBlocks(card.blocks);
    if (blocks.length === 0) continue;
    out.push({ id: typeof card.id === "string" ? card.id : undefined, callId: String(item.callId ?? item.id ?? out.length), blocks });
  }
  return out;
}

const MARKER = /^[ \t]*:visual\[([\w-]+)\][ \t]*$/gm;

/** The answer without its placement lines: what a copy, a narration line or a stream shows. */
export function stripVisualMarkers(text: string): string {
  return text.replace(MARKER, "").replace(/\n{3,}/g, "\n\n");
}

/** The ids a text places. */
export function placedIds(text: string): Set<string> {
  return new Set([...text.matchAll(MARKER)].map((m) => m[1]));
}

export type AnswerPart = { kind: "text"; text: string } | { kind: "visual"; visual: TurnVisual };

/**
 * The answer as prose and visuals in reading order. A marker naming no visual is dropped; each
 * visual is drawn once, at its first marker. With `fallback` and no visual placed, the last
 * visual goes after the opening paragraph (or at the end of an answer that is one paragraph).
 */
export function answerParts(text: string, visuals: readonly TurnVisual[], fallback: boolean): AnswerPart[] {
  const byId = new Map(visuals.filter((v) => v.id).map((v) => [v.id!, v]));
  const parts: AnswerPart[] = [];
  const drawn = new Set<TurnVisual>();
  let last = 0;
  for (const match of text.matchAll(MARKER)) {
    const visual = byId.get(match[1]);
    const before = text.slice(last, match.index);
    if (before.trim()) parts.push({ kind: "text", text: before });
    if (visual && !drawn.has(visual)) {
      parts.push({ kind: "visual", visual });
      drawn.add(visual);
    }
    last = (match.index ?? 0) + match[0].length;
  }
  const rest = text.slice(last);
  if (rest.trim()) parts.push({ kind: "text", text: rest });
  if (!fallback) return parts;
  if (drawn.size > 0 || visuals.length === 0) return parts;
  const unplaced = visuals.slice(-1);
  // The opening paragraph: the first text part, cut at its first blank line.
  const first = parts.findIndex((p) => p.kind === "text");
  const lead: AnswerPart[] = unplaced.map((visual) => ({ kind: "visual", visual }));
  if (first < 0) return [...lead, ...parts];
  const body = (parts[first] as { text: string }).text;
  const cut = body.search(/\n[ \t]*\n/);
  if (cut < 0) {
    parts.splice(first + 1, 0, ...lead);
    return parts;
  }
  parts.splice(first, 1, { kind: "text", text: body.slice(0, cut) }, ...lead, { kind: "text", text: body.slice(cut) });
  return parts;
}

/** The calls whose visual the closing answer `text` draws: those it placed, else the last one. */
export function drawnInAnswer(text: string, visuals: readonly TurnVisual[]): string[] {
  return answerParts(text, visuals, true).flatMap((part) => (part.kind === "visual" ? [part.visual.callId] : []));
}
