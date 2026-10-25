// The `/goal` grammar, pinned: only the exact control words are controls (everything
// else — including a sub-word like `pause everything` — is an objective), quotes group an
// objective without joining the control vocabulary, and the bare form SHOWS state.
import { describe, expect, it } from "vitest";
import type { GoalInfo } from "./api";
import {
  GOAL_USAGE,
  goalCommandHint,
  goalCommandInput,
  goalStatusBlock,
  parseGoalCommand,
  tokenizeGoalInput,
} from "./goalCommand";

describe("parseGoalCommand", () => {
  it("treats a bare /goal (or only whitespace) as status", () => {
    expect(parseGoalCommand("")).toEqual({ kind: "show" });
    expect(parseGoalCommand("   \n ")).toEqual({ kind: "show" });
  });

  it("creates from any input that is not a control word", () => {
    expect(parseGoalCommand("finish the release")).toEqual({
      kind: "create",
      objective: "finish the release",
    });
    expect(parseGoalCommand("  ship   it  ")).toEqual({
      kind: "create",
      objective: "ship it",
    });
  });

  it("matches controls on the whole trimmed input, case-insensitively", () => {
    expect(parseGoalCommand("clear")).toEqual({ kind: "clear" });
    expect(parseGoalCommand("PAUSE")).toEqual({ kind: "pause" });
    expect(parseGoalCommand("Resume")).toEqual({ kind: "resume" });
    expect(parseGoalCommand("edit")).toEqual({ kind: "invalid-edit" });
  });

  it("treats unknown sub-words as objectives, never as controls", () => {
    expect(parseGoalCommand("pause everything only after verification")).toEqual({
      kind: "create",
      objective: "pause everything only after verification",
    });
    expect(parseGoalCommand("clear the backlog")).toEqual({
      kind: "create",
      objective: "clear the backlog",
    });
    expect(parseGoalCommand("resumed")).toEqual({ kind: "create", objective: "resumed" });
    expect(parseGoalCommand("editlater")).toEqual({
      kind: "create",
      objective: "editlater",
    });
  });

  it("edits only with a replacement objective", () => {
    expect(parseGoalCommand("edit new objective")).toEqual({
      kind: "edit",
      objective: "new objective",
    });
    expect(parseGoalCommand("EDIT\n  the second one  ")).toEqual({
      kind: "edit",
      objective: "the second one",
    });
  });

  it("is quote-aware: quotes group the objective and hide control words", () => {
    expect(parseGoalCommand('"pause the deployment"')).toEqual({
      kind: "create",
      objective: "pause the deployment",
    });
    expect(parseGoalCommand('edit "new objective"')).toEqual({
      kind: "edit",
      objective: "new objective",
    });
    expect(parseGoalCommand('"edit" the release notes')).toEqual({
      kind: "create",
      objective: "edit the release notes",
    });
    expect(parseGoalCommand("'single quoted'")).toEqual({
      kind: "create",
      objective: "single quoted",
    });
    expect(parseGoalCommand('fix "the" bug')).toEqual({
      kind: "create",
      objective: "fix the bug",
    });
  });
});

describe("tokenizeGoalInput", () => {
  it("splits outside quotes and keeps quoted text together", () => {
    expect(tokenizeGoalInput('one "two words" three')).toEqual([
      { text: "one", quoted: false },
      { text: "two words", quoted: true },
      { text: "three", quoted: false },
    ]);
  });

  it("honors escapes inside quotes", () => {
    expect(tokenizeGoalInput('"say \\"hi\\""')).toEqual([
      { text: 'say "hi"', quoted: true },
    ]);
  });
});

describe("goalCommandInput", () => {
  it("recognizes only a leading /goal token and returns its argument", () => {
    expect(goalCommandInput("/goal")).toBe("");
    expect(goalCommandInput("/goal   ")).toBe("");
    expect(goalCommandInput("/goal ship it")).toBe("ship it");
    expect(goalCommandInput("  /Goal edit two  ")).toBe("edit two");
  });

  it("leaves messages alone", () => {
    expect(goalCommandInput("goals are nice")).toBeNull();
    expect(goalCommandInput("/goals")).toBeNull();
    expect(goalCommandInput("please /goal ship it")).toBeNull();
  });
});

const goal = (over: Partial<GoalInfo> = {}): GoalInfo => ({
  goal_id: "g1",
  objective: "ship the launch note",
  phase: "active",
  activation: "armed",
  rounds_started: 2,
  max_rounds: 256,
  revision: 3,
  blocker: null,
  ...over,
});

describe("goalStatusBlock", () => {
  it("renders the reference design's lines and the commands the state can use", () => {
    const block = goalStatusBlock("Goal", goal());
    expect(block.title).toBe("Goal");
    expect(block.text.split("\n")).toEqual([
      "Status: active",
      "Objective: ship the launch note",
      "Rounds: 2/256",
      "Activation: armed",
      "",
      "Commands: /goal edit <objective>, /goal pause, /goal clear",
    ]);
  });

  it("lists the acceptance checks that gate done, and nothing when there are none", () => {
    const text = goalStatusBlock("Goal", goal({ checks: ["python -m pytest -q", "ruff check ."] })).text;
    expect(text).toContain("Rounds: 2/256\nChecks: python -m pytest -q; ruff check .\nActivation: armed");
    expect(goalStatusBlock("Goal", goal({ checks: [] })).text).not.toContain("Checks:");
  });

  it("says what the rounds spent, and nothing before any were charged", () => {
    const text = goalStatusBlock("Goal", goal({ tokens_used: 3_200_000, token_budget: 10_000_000 })).text;
    expect(text).toContain("Rounds: 2/256\nSpent: 3.2M of 10M tokens\nActivation: armed");
    expect(goalStatusBlock("Goal", goal()).text).not.toContain("Spent:");
  });

  it("names the blocker and offers resume for blocked, disarmed and paused goals", () => {
    const blocked = goalStatusBlock(
      "Goal",
      goal({
        phase: "blocked",
        activation: "disarmed",
        blocker: { code: "round-limit", message: "Goal reached its limit." },
      }),
    );
    expect(blocked.text).toContain("Status: blocked");
    expect(blocked.text).toContain("Blocker: round-limit: Goal reached its limit.");
    expect(blocked.text).toContain("Commands: /goal edit <objective>, /goal resume, /goal clear");

    expect(goalCommandHint(goal({ activation: "disarmed" }))).toContain("/goal resume");
    expect(goalCommandHint(goal({ phase: "paused", activation: "disarmed" }))).toContain(
      "/goal resume",
    );
    expect(goalCommandHint(goal({ phase: "complete", activation: "disarmed" }))).toBe(
      "/goal <objective>, /goal clear",
    );
  });

  it("carries the usage the refusals append", () => {
    expect(GOAL_USAGE).toBe("Usage: /goal [<objective>|clear|edit <objective>|pause|resume]");
  });
});
