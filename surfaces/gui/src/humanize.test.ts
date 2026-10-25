import { describe, expect, it } from "vitest";
import { humanizeTool } from "./humanize";

// A step's one line, from the tool's name and arguments, for tools whose contract says nothing.
describe("humanizeTool", () => {
  it("leads a command with the model's own description of it, and keeps the command itself", () => {
    const line = humanizeTool("run_shell", { command: "git log --since=yesterday", description: "List yesterday's merges" });
    expect([line.pre, line.obj]).toEqual(["Ran ", "git log --since=yesterday"]);
    expect(line.post).toContain("list yesterday's merges");
  });

  it("names a tool it does not know, with its arguments in short", () => {
    const line = humanizeTool("gmail_search_messages", { query: "from:ci" });
    expect(line.pre).toBe("Used gmail_search_messages");
    expect(line.post).toContain("query=from:ci");
  });

  it("says a one-item plan by the item and its state", () => {
    const line = humanizeTool("todo_write", { todos: [{ content: "Post the digest", status: "in_progress" }] });
    expect(line.pre).toBe("Updated the plan — ");
    expect(line.obj).toContain("Post the digest");
    expect(line.post).toBe(" → in progress");
  });

  it("reads a plan stored before the rename, under `items`", () => {
    expect(humanizeTool("todo_write", { items: [{ content: "Old plan", status: "pending" }] }).obj).toContain("Old plan");
  });
});
