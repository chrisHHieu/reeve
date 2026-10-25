// The human-facing `/goal` command — the `command-goal` grammar, mirrored into the
// composer. One parser, one status block, one prefix test: the send path asks whether a
// draft is a command before it is allowed to become a message.
//
// The semantics, kept literally:
//   · a bare `/goal` SHOWS status and never mutates;
//   · only the exact words `clear`/`pause`/`resume`/`edit` are controls, matched against
//     the whole trimmed input (case-insensitively) — every other input, sub-word included,
//     is an objective (`/goal pause everything` creates an objective, it does not pause);
//   · `edit` with nothing after it is a refusal, not a create;
//   · quotes group an objective: `/goal "pause the deployment"` creates that objective,
//     and `edit "new objective"` replaces with it.
import type { GoalInfo } from "./api";
import { spendLabel } from "./components/session/GoalDock";

export const GOAL_USAGE =
  "Usage: /goal [<objective>|clear|edit <objective>|pause|resume]";

/** What one `/goal` invocation means. Closed on purpose: every caller switches on it. */
export type GoalCommand =
  | { kind: "show" }
  | { kind: "create"; objective: string }
  | { kind: "edit"; objective: string }
  | { kind: "invalid-edit" }
  | { kind: "pause" }
  | { kind: "resume" }
  | { kind: "clear" };

/** One quote-aware token. `quoted` keeps `"edit"` an objective, not the edit verb. */
interface GoalToken {
  text: string;
  quoted: boolean;
}

/** Split on whitespace outside quotes; quotes group and are not part of the token. */
export function tokenizeGoalInput(input: string): GoalToken[] {
  const tokens: GoalToken[] = [];
  let current = "";
  let currentQuoted = false;
  let quote: '"' | "'" | null = null;
  for (let i = 0; i < input.length; i++) {
    const ch = input[i];
    if (quote !== null) {
      if (ch === "\\" && i + 1 < input.length) current += input[++i];
      else if (ch === quote) quote = null;
      else current += ch;
      continue;
    }
    if (ch === '"' || ch === "'") {
      quote = ch;
      currentQuoted = true;
      continue;
    }
    if (/\s/.test(ch)) {
      if (current !== "" || currentQuoted) {
        tokens.push({ text: current, quoted: currentQuoted });
        current = "";
        currentQuoted = false;
      }
      continue;
    }
    current += ch;
  }
  if (current !== "" || currentQuoted) tokens.push({ text: current, quoted: currentQuoted });
  return tokens;
}

/** Parse only the grammar `/goal` owns; arbitrary other input is an objective. */
export function parseGoalCommand(rawInput: string): GoalCommand {
  const input = rawInput.trim();
  if (input.length === 0) return { kind: "show" };
  const control = input.toLowerCase();
  if (control === "clear") return { kind: "clear" };
  if (control === "pause") return { kind: "pause" };
  if (control === "resume") return { kind: "resume" };
  if (control === "edit") return { kind: "invalid-edit" };
  const tokens = tokenizeGoalInput(input);
  if (tokens.length > 1 && !tokens[0].quoted && tokens[0].text.toLowerCase() === "edit") {
    return { kind: "edit", objective: tokens.slice(1).map((t) => t.text).join(" ") };
  }
  return { kind: "create", objective: tokens.map((t) => t.text).join(" ") };
}

/** The argument a draft carries when it IS `/goal …`; null when it is a message. */
export function goalCommandInput(text: string): string | null {
  const trimmed = text.trim();
  const match = /^\/goal(?=\s|$)/i.exec(trimmed);
  return match === null ? null : trimmed.slice(match[0].length).trim();
}

/** Commands that are meaningful from one exact live state. */
export function goalCommandHint(goal: GoalInfo): string {
  if (goal.phase === "active") {
    return goal.activation === "armed"
      ? "/goal edit <objective>, /goal pause, /goal clear"
      : "/goal edit <objective>, /goal resume, /goal clear";
  }
  if (goal.phase === "complete") return "/goal <objective>, /goal clear";
  return "/goal edit <objective>, /goal resume, /goal clear";
}

/** The direct status/result block: phase (and blocker), objective, rounds, activation,
 *  then the commands that state can actually use. */
export function goalStatusBlock(
  title: string,
  goal: GoalInfo,
): { title: string; text: string } {
  const lines = [`Status: ${goal.phase}`];
  if (goal.phase === "blocked" && goal.blocker) {
    lines.push(`Blocker: ${goal.blocker.code}: ${goal.blocker.message}`);
  }
  lines.push(
    `Objective: ${goal.objective}`,
    `Rounds: ${goal.rounds_started}/${goal.max_rounds}`,
    ...(spendLabel(goal) ? [`Spent: ${spendLabel(goal)}`] : []),
    ...(goal.checks?.length ? [`Checks: ${goal.checks.join("; ")}`] : []),
    `Activation: ${goal.activation}`,
    "",
    `Commands: ${goalCommandHint(goal)}`,
  );
  return { title, text: lines.join("\n") };
}
