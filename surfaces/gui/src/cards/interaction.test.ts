import { describe, expect, it } from "vitest";
import type { ToolItem } from "./cardModels";
import { answerPairs, askFields, folderFields, planVerdict } from "./interaction";
import { fieldsOf, humanKey } from "./fields";

const tool = (name: string, args: unknown, preview?: string, status = "ok"): ToolItem =>
  ({ kind: "tool", id: "t", name, args, status, ...(preview !== undefined ? { preview } : {}) }) as ToolItem;

const text = (rows: { label: string; value: string }[]) => rows.map((r) => `${r.label}: ${r.value}`);

describe("interactive asks read as what happened, never as their JSON", () => {
  it("a question and its answer", () => {
    const rows = askFields(tool("ask_user", { question: "Polish which first?" }, '{"answer": "Dashboard"}'));
    expect(text(rows)).toEqual(["Question: Polish which first?", "Answer: Dashboard"]);
  });

  it("a grouped ask pairs each question with its own answer", () => {
    const rows = askFields(
      tool(
        "ask_user",
        { questions: [{ header: "Scope", question: "Which page?" }, { question: "Ship today?" }] },
        JSON.stringify({ answers: { Scope: "Dashboard", "Ship today?": "Yes" } }),
      ),
    );
    expect(text(rows)).toEqual([
      "Question 1: Which page?",
      "Answer: Dashboard",
      "Question 2: Ship today?",
      "Answer: Yes",
    ]);
    expect(rows.some((r) => r.value.includes("{"))).toBe(false);
  });

  it("a folder grant says which folder, what access, and the outcome", () => {
    const rows = folderFields(
      tool(
        "request_directory",
        { path: "D:\\wealth\\frontend", reason: "fix the goal form", writable: true },
        JSON.stringify({ granted: true, path: "D:\\wealth\\frontend", writable: true }),
      ),
    );
    expect(text(rows)).toEqual([
      "Folder: D:\\wealth\\frontend",
      "Asked for: Read and write",
      "Why: fix the goal form",
      "Result: Granted · read and write",
    ]);
  });

  it("a replayed grant (the model-facing line) and a refusal read the same way", () => {
    const replay = folderFields(tool("request_directory", { path: "D:\\b" }, "granted: D:\\b (writable)"));
    expect(text(replay)).toContain("Result: Granted · read and write");
    const refused = folderFields(
      tool("request_directory", { path: "D:\\b" }, JSON.stringify({ granted: false, reason: "the user declined the request" })),
    );
    expect(text(refused)).toContain("Result: Not granted");
    expect(text(refused)).toContain("Note: the user declined the request");
  });

  it("a plan's verdict, and nothing while it waits", () => {
    expect(text(planVerdict(tool("propose_plan", { plan: "x" }, '{"approved": true, "mode": "workspace-write"}')))).toEqual([
      "Verdict: Approved · workspace write",
    ]);
    expect(text(planVerdict(tool("propose_plan", { plan: "x" }, '{"approved": false, "feedback": "smaller"}')))).toEqual([
      "Verdict: Changes requested",
      "Feedback: smaller",
    ]);
    expect(planVerdict(tool("propose_plan", { plan: "x" }, undefined, "…"))).toEqual([]);
  });

  it("the transcript's Q&A note reads a grouped answer as pairs", () => {
    expect(answerPairs('{"Scope": "Dashboard", "When": "today"}')).toEqual([
      { question: "Scope", answer: "Dashboard" },
      { question: "When", answer: "today" },
    ]);
    expect(answerPairs("just text")).toBeNull();
  });
});

describe("a built-in tool's call as labelled lines", () => {
  it("names keys in words and keeps paths mono", () => {
    expect(humanKey("subagent_type")).toBe("Subagent type");
    expect(humanKey("maxResults")).toBe("Max results");
    const rows = fieldsOf('{"path": "src/a.ts", "recursive": true, "tags": ["a", "b"], "_card_meta": {"x": 1}}')!;
    expect(rows).toEqual([
      { label: "Path", value: "src/a.ts", mono: true },
      { label: "Recursive", value: "Yes" },
      { label: "Tags", value: "a, b" },
    ]);
  });

  it("draws a field with no value as a line that says so, at either depth", () => {
    // `Object.entries(null)` throws: this used to unmount the app (see RowBoundary.test.tsx).
    expect(fieldsOf('{"goal": null}')).toEqual([{ label: "Goal", value: "None", tone: "muted" }]);
    expect(fieldsOf('{"goal": {"objective": "ship", "deadline": null}}')).toEqual([
      { label: "Goal", value: "Objective: ship · Deadline: …" },
    ]);
  });

  it("is not a fields view for plain text", () => {
    expect(fieldsOf("plain output")).toBeNull();
    expect(fieldsOf("[1, 2]")).toBeNull();
  });
});
