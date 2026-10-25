// What a run came to, in the page's four words. The server's own words are running, ok,
// error and skipped; a run whose conversation holds a prompt for the person is "Needs you"
// whatever else it is, because that is the one a person has to act on.

import { NEEDS } from "../../needs";

export type RunTone = "live" | "warm" | "bad" | "";

export interface RunLike {
  status: string;
  attention?: number;
  attention_kind?: string | null;
}

export function runState(run: RunLike): { tone: RunTone; word: string; waitsFor: string } {
  if ((run.attention || 0) > 0) return { tone: "warm", word: "Needs you", waitsFor: NEEDS[run.attention_kind || ""] || "" };
  if (run.status === "running") return { tone: "live", word: "Running", waitsFor: "" };
  if (run.status === "ok") return { tone: "", word: "Done", waitsFor: "" };
  if (run.status === "error") return { tone: "bad", word: "Failed", waitsFor: "" };
  if (run.status === "skipped") return { tone: "", word: "Skipped", waitsFor: "" };
  return { tone: "", word: run.status, waitsFor: "" };
}

const STARTED_BY: Record<string, string> = {
  schedule: "Schedule",
  manual: "You",
  catchup: "Schedule, late",
};

/** Who started a run. "Schedule, late" is a run missed while Reeve was closed, made up on start. */
export function startedBy(trigger: string): string {
  return STARTED_BY[trigger] || trigger;
}
