// The task head's facts (layout step 2, owner 2026-10-13: "B frame, A inside"): what the session
// has amounted to, at the right of its head, beside the goal, subagents, jobs and to-dos. The
// files it changed with their lines (a button: Review opens on them) and the tokens the
// conversation has spent. The last check is not drawn here: its last line read as noise beside
// the files (owner, 2026-10-15); Review says it, and `taskFacts` still computes it.
//
// Read from what the transcript already holds: each turn's receipt (`changes` items)
// and the usage sidecar of its answers. Nothing here is guessed; a fact with nothing behind it
// is not drawn.

import type { Item, SessionUsage, ChangesItem } from "../../types";
import { formatTokens } from "../../usage";
import { Icon } from "../Icon";
import { seeChangesIn } from "../../seeChanges";

export interface TaskFactsData {
  files: number;
  added: number;
  removed: number;
  /** The file the last receipt names first: where Review opens. */
  lastPath?: string;
  /** The last check a turn ran, when no file was edited after it. */
  check?: { ok: boolean | null; summary: string; command: string };
}

/** The session's receipts summed: the person's files (the agent's notes apart), and the last check. */
export function taskFacts(items: readonly Item[]): TaskFactsData {
  const files = new Map<string, [number, number]>();
  let lastPath: string | undefined;
  let check: TaskFactsData["check"];
  for (const item of items) {
    if (item.kind !== "changes") continue;
    const receipt = item as ChangesItem;
    const notes = new Set(receipt.notes ?? []);
    const stats = new Map((receipt.stats ?? []).map((s) => [s.path, s] as const));
    const touched = [...(receipt.created ?? []), ...(receipt.overwritten ?? []), ...(receipt.deleted ?? [])];
    const paths = [...touched, ...[...stats.keys()].filter((p) => !touched.includes(p))].filter((p) => !notes.has(p));
    for (const path of paths) {
      const slot = files.get(path) ?? [0, 0];
      const stat = stats.get(path);
      slot[0] += stat?.added ?? 0;
      slot[1] += stat?.removed ?? 0;
      files.set(path, slot);
    }
    if (paths.length) lastPath = paths[0];
    // A check stands only until the next edit: a later receipt with edits and no check of its
    // own leaves no proof for what it changed.
    if (receipt.check) check = receipt.check.editsSince > 0 ? undefined : receipt.check;
    else if (paths.length) check = undefined;
  }
  let added = 0;
  let removed = 0;
  for (const [a, r] of files.values()) {
    added += a;
    removed += r;
  }
  return { files: files.size, added, removed, lastPath, check: check ? { ok: check.ok, summary: check.summary, command: check.command } : undefined };
}

/** Every token the conversation has spent, cache reads included (what the account is billed for). */
export function tokensSpent(usage: SessionUsage): { total: number; title: string } {
  const rows = Object.entries(usage.byModel);
  const sum = (u: SessionUsage["byModel"][string]) => u.input + u.output + u.cache_read + u.cache_write;
  const total = rows.reduce((n, [, u]) => n + sum(u), 0);
  const title = rows.map(([model, u]) => `${model}: ${formatTokens(sum(u))}`).join("\n");
  return { total, title };
}

/** A line count, with its short form beside it where the two differ ("+28355" / "+28.4k"):
 *  the row shows one or the other by its width. */
function count(sign: string, n: number) {
  const full = `${sign}${n}`;
  const short = `${sign}${formatTokens(n)}`;
  if (short === full) return full;
  return (
    <>
      <span className="fact-full">{full}</span>
      <span className="fact-short">{short}</span>
    </>
  );
}

export function TaskFacts({ items, usage }: { items: readonly Item[]; usage: SessionUsage }) {
  const facts = taskFacts(items);
  const tokens = tokensSpent(usage);
  if (facts.files === 0 && tokens.total === 0) return null;
  return (
    <span className="head-facts" data-testid="task-facts">
      {facts.files > 0 && (
        <button
          type="button"
          className="head-fact is-button"
          data-testid="task-files"
          title={`${facts.files} file${facts.files === 1 ? "" : "s"} changed in this conversation · see them in Review`}
          onClick={() => facts.lastPath && seeChangesIn(facts.lastPath)}
        >
          <Icon name="file" size={13} />
          <b>{facts.files}</b>{" "}
          {/* The row drops the word, then shortens the counts, as it narrows (styles.css). */}
          <span className="fact-word">file{facts.files === 1 ? "" : "s"}</span>
          <span className="head-add">{count("+", facts.added)}</span>
          <span className="head-del">{count("−", facts.removed)}</span>
        </button>
      )}
      {tokens.total > 0 && (
        <span className="head-fact" data-testid="task-tokens" title={tokens.title}>
          <Icon name="ops" size={13} />
          {formatTokens(tokens.total)} tokens
        </span>
      )}
    </span>
  );
}
