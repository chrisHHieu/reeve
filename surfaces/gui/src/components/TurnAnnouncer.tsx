// A turn's state, said to a screen reader (GUI rendering review, G6). Only the composer's
// dictation, the waiting row and the prompt queue had live regions, so a person using one
// heard nothing when the agent started, needed them, or finished. The rule the review cites
// for streaming UIs: announce state changes — Working, Needs approval, Done, Stopped — and
// never the tokens.
import { useEffect, useRef, useState } from "react";
import type { Item } from "../types";

/** The one sentence for where the conversation stands now. */
export function turnAnnouncement(items: readonly Item[], running: boolean): string {
  const last = items[items.length - 1];
  if (running) {
    const waiting = items.some((item) => item.kind === "approval" && !item.resolved);
    return waiting ? "Needs approval" : "Working";
  }
  if (!last) return "";
  // The turn's tail after the person's last message: its receipt, and how it ended.
  const lastUser = items.map((item) => item.kind).lastIndexOf("user");
  const turn = items.slice(lastUser + 1);
  if (turn.some((item) => item.kind === "notice" && item.text === "Stopped.")) return "Stopped";
  if (turn.some((item) => item.kind === "notice" && item.retriable)) return "Stopped with an error";
  const parts = ["Done"];
  const changes = [...turn].reverse().find((item) => item.kind === "changes");
  if (changes && changes.kind === "changes") {
    const files = new Set([...(changes.created ?? []), ...(changes.overwritten ?? []), ...(changes.deleted ?? [])]).size;
    if (files > 0) parts.push(`${files} file${files === 1 ? "" : "s"} changed`);
    if (changes.check) {
      parts.push(
        changes.check.ok === false ? "check failed" : changes.check.editsSince > 0 ? "edited after the check" : changes.check.ok ? "check passed" : "check result unknown",
      );
    }
  }
  return parts.join(" · ");
}

export function TurnAnnouncer({ items, running }: { items: readonly Item[]; running: boolean }) {
  // Quiet until a turn actually runs in this view: opening a session replays its history,
  // and "Done" for work finished yesterday is not news.
  const [live, setLive] = useState(false);
  const wasRunning = useRef(running);
  useEffect(() => {
    if (running && !wasRunning.current) setLive(true);
    wasRunning.current = running;
  }, [running]);
  const text = live || running ? turnAnnouncement(items, running) : "";
  return (
    <span role="status" aria-live="polite" className="sr-only" data-testid="turn-announcer">
      {text}
    </span>
  );
}
