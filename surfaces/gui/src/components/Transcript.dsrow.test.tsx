import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { Transcript } from "./Transcript";
import type { Item } from "../types";

afterEach(cleanup);

// The row grammar, ported from the reference design: ONE collapsed line per tool call —
// family word, a 2px dot, a one-line summary — and, on click, one card. This file is the
// specification of that grammar; `Transcript.test.tsx` covers the turn around it and
// `Transcript.result.test.tsx` covers the details panel that took over the old chips.


/** A settled turn with one tool call, opened so its rows are on screen. */
function renderTurn(tool: Extract<Item, { kind: "tool" }>, extra: Item[] = []) {
  render(
    <Transcript
      items={[{ kind: "user", text: "go" }, tool, ...extra]}
      running={false}
      onApprove={vi.fn()}
    />,
  );
}

describe("the collapsed row", () => {
  it("is one line: family word, dot, summary — and no chips", () => {
    renderTurn({
      kind: "tool",
      id: "t1",
      name: "read_file",
      args: { path: "/d/Project/reeve/harness/providers/gemini_provider.py" },
      status: "ok",
      presentation: { summary: "Read gemini_provider.py" },
      undoable: true,
      callId: "c1",
    });
    expect(screen.getByText("Read")).toBeTruthy();
    // The path reads as the header does: the file NAME as the payload, the folder as the
    // quiet context after it — not the tool's own "Read <name>" sentence, which repeats the
    // label and buries the folder inside one ellipsizable string.
    expect(screen.getByText("gemini_provider.py")).toBeTruthy();
    expect(screen.getByText("…/harness/providers")).toBeTruthy();
    // The row is a disclosure target, not a list of affordances.
    const line = document.querySelector("[data-disclosure-row]")!;
    expect(line.getAttribute("role")).toBe("button");
    expect(line.getAttribute("aria-expanded")).toBe("false");
    // Everything the old row wore as a chip is gone from it.
    for (const gone of ["step-diff", "step-took", "tool-undo", "tool-full-output", "raw"]) {
      expect(screen.queryByTestId(gone)).toBeNull();
      expect(screen.queryByText(gone)).toBeNull();
    }
  });

  it("opens on a click anywhere on the row, and closes again", () => {
    renderTurn({
      kind: "tool",
      id: "t1",
      name: "read_file",
      args: { path: "a.py" },
      status: "ok",
      presentation: {
        summary: "Read a.py",
        card: {
          card: "read",
          path: "a.py",
          offset: 1,
          total_lines: 2,
          lines: [
            { number: 1, text: "one" },
            { number: 2, text: "two" },
          ],
        },
      },
    });
    expect(screen.queryByTestId("read-body")).toBeNull();
    fireEvent.click(document.querySelector("[data-disclosure-row]")!);
    expect(screen.getByTestId("read-body").textContent).toContain("one");
    // The file link stays inline while open: it is the only line naming what ran. (Scoped to
    // the row: the card's own banner names the same file.)
    const row = document.querySelector("[data-disclosure-row]")!;
    expect(row.textContent).toContain("a.py");
    fireEvent.click(document.querySelector("[data-disclosure-row]")!);
    expect(screen.queryByTestId("read-body")).toBeNull();
  });

  it("opens from the keyboard too — a div pretending to be a button must do the work", () => {
    renderTurn({ kind: "tool", id: "t1", name: "run_shell", args: { command: "ls" }, status: "ok" });
    const line = document.querySelector("[data-disclosure-row]")!;
    fireEvent.keyDown(line, { key: "Enter" });
    expect(line.getAttribute("aria-expanded")).toBe("true");
    fireEvent.keyDown(line, { key: " " });
    expect(line.getAttribute("aria-expanded")).toBe("false");
  });

  it("says nothing is expandable when there is nothing to show", () => {
    renderTurn({ kind: "tool", id: "t1", name: "mystery_tool", args: {}, status: "ok" });
    // No body, no output, no card — so no button role, no aria-expanded, and a pointer that
    // does not invite a click that would do nothing.
    expect(document.querySelector("[data-disclosure-row]")).toBeNull();
    expect(screen.getByText("Used mystery_tool")).toBeTruthy();
  });
});

describe("the card a row opens", () => {
  it("draws a terminal command with its exit status and output", () => {
    renderTurn({
      kind: "tool",
      id: "t1",
      name: "run_shell",
      args: { command: "pytest -q" },
      status: "ok",
      presentation: {
        summary: "Command succeeded",
        card: {
          card: "terminal",
          title: "pytest -q",
          cwd: "/d/Project/reeve",
          output: "3 passed\n",
          exit_code: 0,
        },
      },
    });
    fireEvent.click(document.querySelector("[data-disclosure-row]")!);
    expect(screen.getByTestId("terminal-command").textContent).toBe("pytest -q");
    expect(screen.getByTestId("terminal-status").textContent).toBe("exit 0");
    expect(screen.getByTestId("terminal-output").textContent).toContain("3 passed");
  });

  it("turns the row RED when the command failed, even though the call succeeded", () => {
    renderTurn({
      kind: "tool",
      id: "t1",
      name: "run_shell",
      args: { command: "false" },
      // The CALL succeeded — a command that ran and reported failure is still a failure to
      // the person reading, and the exit status is the tool's own verdict.
      status: "ok",
      presentation: {
        summary: "Command failed (exit 2)",
        card: { card: "terminal", title: "false", output: "", exit_code: 2 },
      },
    });
    expect(document.querySelector(".tool-row")!.getAttribute("data-state")).toBe("error");
    expect(screen.getByTestId("state-dot-error")).toBeTruthy();
  });

  it("draws a diff with the engine's churn, and a read window with the file's line numbers", () => {
    const { unmount } = render(
      <Transcript
        items={[
          { kind: "user", text: "go" },
          {
            kind: "tool",
            id: "t1",
            name: "write_file",
            args: { path: "app.py" },
            status: "ok",
            presentation: {
              summary: "Wrote app.py",
              card: {
                card: "diff",
                diffs: [
                  {
                    path: "app.py",
                    old_text: "old line",
                    new_text: "new line",
                    added: 1,
                    removed: 1,
                  },
                ],
              },
            },
          },
        ]}
        running={false}
        onApprove={vi.fn()}
      />,
    );
    fireEvent.click(document.querySelector("[data-disclosure-row]")!);
    // The churn rides the ROW: `Edit · app.py  +1 −1`, not a footer inside
    // the card.
    const churn = screen.getByTestId("tool-churn").textContent;
    expect(churn).toContain("+1");
    expect(churn).toContain("−1");
    expect(screen.queryByText(/└/)).toBeNull();
    unmount();

    renderTurn({
      kind: "tool",
      id: "t2",
      name: "read_file",
      args: { path: "big.py" },
      status: "ok",
      presentation: {
        summary: "Read big.py",
        card: {
          card: "read",
          path: "big.py",
          offset: 200,
          total_lines: 400,
          lines: [{ number: 200, text: "x = 1" }],
        },
      },
    });
    fireEvent.click(document.querySelector("[data-disclosure-row]")!);
    // The gutter keeps the FILE's number, or the reader cannot go and look.
    expect(screen.getByText("200")).toBeTruthy();
    expect(screen.getByTestId("read-window").textContent).toContain("1 / 400");
  });

  it("groups search hits under their file", () => {
    renderTurn({
      kind: "tool",
      id: "t1",
      name: "grep",
      args: { pattern: "needle" },
      status: "ok",
      presentation: {
        summary: "Found 2 matches",
        card: {
          card: "search",
          shape: "matches",
          truncated: false,
          total: 2,
          files: [
            {
              path: "src/a.ts",
              matches: [
                { line_number: 4, line: "needle" },
                { line_number: 9, line: "needle again" },
              ],
            },
          ],
        },
      },
    });
    fireEvent.click(document.querySelector("[data-disclosure-row]")!);
    expect(screen.getByText("src/a.ts")).toBeTruthy();
    expect(screen.getByText("2 matches")).toBeTruthy();
  });

  it("falls back to the plain IN/OUT body when the tool sent no card", () => {
    renderTurn({
      kind: "tool",
      id: "t1",
      name: "mcp__wealthlog__query",
      args: { table: "assets" },
      status: "ok",
      preview: '{"rows": 3}',
    });
    fireEvent.click(document.querySelector("[data-disclosure-row]")!);
    expect(screen.getByText("IN")).toBeTruthy();
    expect(screen.getByText("OUT")).toBeTruthy();
    // The family label names the server, so an unbounded MCP verb still reads as a row.
    expect(screen.getByText("Wealthlog")).toBeTruthy();
  });
});

describe("explore grouping", () => {
  it("two greps in a row fold into one Exploring group", () => {
    render(
      <Transcript
        items={[
          { kind: "user", text: "survey" },
          { kind: "tool", id: "t1", name: "grep", looks: true, args: { pattern: "a" }, status: "ok" },
          { kind: "tool", id: "t2", name: "grep", looks: true, args: { pattern: "b" }, status: "ok" },
        ]}
        running={false}
        onApprove={vi.fn()}
      />,
    );
    const group = screen.getByTestId("explore-group");
    expect(group.textContent).toContain("Explored");
    expect(group.textContent).toContain("2 searches");
    // Collapsed by default: the two rows only appear once the header is opened.
    expect(screen.queryAllByTestId("turn-step")).toHaveLength(0);
    fireEvent.click(group.querySelector(".tool-row-line")!);
    expect(screen.getAllByTestId("turn-step")).toHaveLength(2);
  });
});

describe("a step the turn never answered for", () => {
  it("stops pretending to run, and is amber rather than red", () => {
    renderTurn({
      kind: "tool",
      id: "t1",
      name: "run_shell",
      args: { command: "x" },
      status: "no result",
    });
    expect(document.querySelector(".tool-row")!.getAttribute("data-state")).toBe("stopped");
    expect(screen.getByTestId("state-dot-warning")).toBeTruthy();
    expect(screen.queryByTestId("state-dot-error")).toBeNull();
  });
});

describe("expansion survives a group folding", () => {
  // An Explored group unmounts its rows when it closes; the open flags live in the
  // transcript's store, so reopening the group finds the row the reader had open.
  it("remembers which rows were open, because the group unmounts them", () => {
    const read = (id: string, path: string) => ({
      kind: "tool" as const,
      id,
      name: "read_file",
      looks: true,
      args: { path },
      status: "ok" as const,
      callId: id,
      presentation: {
        summary: `Read ${path}`,
        card: { card: "read" as const, path, offset: 1, total_lines: 1, lines: [{ number: 1, text: "only" }] },
      },
    });
    render(
      <Transcript
        items={[{ kind: "user", text: "go" }, read("c1", "a.py"), read("c2", "b.py")]}
        running={false}
        onApprove={vi.fn()}
      />,
    );
    const group = () => screen.getByTestId("explore-group").querySelector(".tool-row-line")!;
    fireEvent.click(group());
    fireEvent.click(document.querySelector("[data-disclosure-row]")!);
    expect(screen.getByTestId("read-body")).toBeTruthy();

    fireEvent.click(group());
    expect(screen.queryByTestId("read-body")).toBeNull();
    fireEvent.click(group());
    // A row that forgot would be closed here, and the reader would have lost the place they
    // were reading — which is the only reason they had a row open.
    expect(screen.getByTestId("read-body")).toBeTruthy();
  });
});
