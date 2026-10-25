// The working timeline, design A (owner, 2026-10-13): looking is one line, whatever thoughts
// came between the looks; doing, a failure and the decision before doing are always lines of
// their own; what the agent said is the heading of the lines after it.
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { Transcript } from "./Transcript";
import type { Item } from "../types";

afterEach(cleanup);

const read = (id: string, path: string, ts: number, status = "ok"): Item => ({
  kind: "tool",
  id,
  name: "read_file",
  looks: true,
  args: { path },
  status,
  ts,
  endTs: ts + 1,
});
const thought = (text: string, ts: number): Item => ({ kind: "assistant", text: "", reasoning: text, ts });
const edit = (id: string, path: string, ts: number): Item => ({
  kind: "tool",
  id,
  name: "replace_in_file",
  args: { path },
  status: "ok",
  ts,
  endTs: ts + 1,
});

const draw = (items: Item[]) =>
  render(<Transcript items={[{ kind: "user", text: "go" }, ...items, { kind: "assistant", text: "Done." }]} running={false} onApprove={vi.fn()} />);

describe("timeline design A", () => {
  it("a thought between two looks joins the run, and the line names the files and the thinking", () => {
    draw([read("r1", "cli/export.py", 100), thought("Need the writer too.", 108), read("r2", "cli/writer.py", 110)]);
    const groups = screen.getAllByTestId("explore-group");
    expect(groups).toHaveLength(1);
    const line = groups[0].querySelector(".explore-line-full")!.textContent;
    expect(line).toBe("export.py, writer.py · thought 7s");
    // No Thought row outside the line.
    expect(document.querySelectorAll(".turn-rail > div > .think-row, .turn-rail > .think-row")).toHaveLength(0);
    fireEvent.click(groups[0].querySelector(".tool-row-line")!);
    expect(groups[0].textContent).toContain("Need the writer too.");
  });

  it("a thought right before something is done stays a line: it is the decision", () => {
    draw([
      read("r1", "a.py", 100),
      read("r2", "b.py", 102),
      thought("The default should be False.", 110),
      edit("e1", "a.py", 112),
    ]);
    const group = screen.getByTestId("explore-group");
    expect(group.textContent).not.toContain("thought");
    fireEvent.click(group.querySelector(".tool-row-line")!);
    expect(group.textContent).not.toContain("The default should be False.");
    expect(document.body.textContent).toContain("Thought");
  });

  it("a look that failed is its own line and ends the run; one look alone is a plain row", () => {
    draw([read("r1", "a.py", 100), read("r2", "secrets.env", 102, "error"), read("r3", "b.py", 104)]);
    expect(screen.queryByTestId("explore-group")).toBeNull();
    expect(document.querySelectorAll('.tool-row[data-state="error"]')).toHaveLength(1);
  });

  it("what the agent says between its steps is a heading of the lines after it", () => {
    draw([
      { kind: "assistant", text: "I'll find where the flags are parsed.", ts: 99 },
      read("r1", "a.py", 100),
      read("r2", "b.py", 102),
    ]);
    const heading = screen.getByTestId("turn-narration");
    expect(heading.textContent).toContain("I'll find where the flags are parsed.");
    expect(screen.getByTestId("explore-group")).toBeTruthy();
  });
});
