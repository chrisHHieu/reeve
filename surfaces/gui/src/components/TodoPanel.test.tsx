// The plan strip, tested as a rule rather than as markup.
//
// Inherited from the right-rail Progress panel this replaced, and worth keeping: the
// strip shows the plan and NOTHING else. It once fell back to the last four tool calls
// when there was no plan, and that list came from the whole transcript while the plan came
// from the current turn — so a new turn opened showing the previous turn's steps as though
// they were running. The absence of any fallback is the behaviour under test here.
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { progressLabel } from "./TodoPanel";
import { ActivityRow } from "./session/ActivityRow";
import type { TodoItem } from "../types";

// The to-dos are a segment of the activity row; alone in it, they are what these tests read.
const TodoPanel = ({ items, settled }: { items: TodoItem[]; settled?: boolean }) => (
  <ActivityRow todo={items} settled={settled} />
);

// The separator is en-space · en-space (U+2002), copied from the reference design: a wider gap than an
// ASCII space, and invisible in a diff unless it is spelled out like this.
const SEP = "\u2002\u00b7\u2002";

// Without this each render stacks on the last, and "no plan" finds the previous test's.
afterEach(cleanup);

// Closed means out of reach, not just out of sight: the list stays mounted so closing can
// animate, and is hidden from assistive tech and focus (inert) while closed.
const isOpen = () => {
  const body = screen.getByTestId("todo-dock-body");
  expect(body.hasAttribute("inert")).toBe(body.getAttribute("aria-hidden") === "true");
  return body.getAttribute("aria-hidden") === "false";
};

const PLAN = [
  { content: "Read the log", status: "done" as const },
  { content: "Patch it", status: "in_progress" as const },
  { content: "Run the tests", status: "pending" as const },
];

describe("the plan strip", () => {
  it("shows the plan the model wrote", () => {
    render(<TodoPanel items={PLAN} />);
    const list = screen.getByTestId("todo-dock-list");
    expect(list.textContent).toContain("Read the log");
    expect(list.textContent).toContain("Patch it");
    expect(list.textContent).toContain("Run the tests");
  });

  it("marks each step with its own status, so the three are told apart", () => {
    const { container } = render(<TodoPanel items={PLAN} />);
    const marks = [...container.querySelectorAll(".todo-dock-item")].map((el) => el.className);
    expect(marks).toEqual([
      "todo-dock-item done",
      "todo-dock-item in_progress",
      "todo-dock-item pending",
    ]);
  });

  it("renders nothing at all with no plan — not every job needs one", () => {
    const { container } = render(<TodoPanel items={[]} />);
    expect(container.innerHTML).toBe("");
    expect(screen.queryByTestId("todo-dock")).toBeNull();
  });

  it("never shows tool steps, however long the turn runs", () => {
    // The old fallback rendered these. A panel repeating the transcript is a second
    // transcript with a worse view of the same data.
    render(<TodoPanel items={[]} />);
    expect(screen.queryByText(/earlier step/)).toBeNull();
    expect(screen.queryByText(/tool call/)).toBeNull();
  });

  it("starts as the header, which carries the whole state, and opens only on a click", () => {
    // Owner, 2026-09-28: the list stays down; the person opens it when they want it.
    render(<TodoPanel items={PLAN} />);
    expect(isOpen()).toBe(false);
    // Closed, the header names the step under way and the count; the per-status breakdown
    // rides the title, so the closed header is still a complete answer (GUI UX review).
    const peek = screen.getByTestId("todo-dock-peek");
    expect(peek.getAttribute("data-shown")).toBe("true");
    expect(peek.textContent).toBe("Patch it");
    const progress = screen.getByTestId("todo-dock-progress");
    expect(progress.textContent).toBe("1 of 3");
    expect(progress.getAttribute("title")).toBe(`1 completed${SEP}1 in progress${SEP}1 pending`);
    expect(document.querySelector(".todo-dock-bar")).toBeNull(); // no progress bar (owner, 2026-09-28)
    expect(screen.getByTestId("todo-dock-toggle").getAttribute("aria-expanded")).toBe("false");

    fireEvent.click(screen.getByTestId("todo-dock-toggle"));
    expect(isOpen()).toBe(true);
    // Open, the list names the step; the header does not say it twice.
    expect(screen.getByTestId("todo-dock-peek").getAttribute("data-shown")).toBe("false");
  });

  it("a finished plan reads as done in the header, with nothing under way", () => {
    const done = PLAN.map((t) => ({ ...t, status: "done" as const }));
    render(<TodoPanel items={done} />);
    expect(isOpen()).toBe(false);
    expect(screen.getByTestId("todo-dock-progress").textContent).toBe("3 of 3 done");
    expect(screen.getByTestId("todo-dock-peek").getAttribute("data-shown")).toBe("false"); // nothing under way
  });

  it("never opens itself when a new plan arrives, and keeps the person's choice when it does", () => {
    const done = PLAN.map((t) => ({ ...t, status: "done" as const }));
    const { rerender } = render(<TodoPanel items={done} />);
    rerender(<TodoPanel items={[...PLAN, { content: "Verify it", status: "pending" }]} />);
    expect(isOpen()).toBe(false); // new work does not push the conversation up

    fireEvent.click(screen.getByTestId("todo-dock-toggle"));
    rerender(<TodoPanel items={[...PLAN, { content: "Verify it", status: "in_progress" }]} />);
    expect(isOpen()).toBe(true); // opened by the person, it stays open across writes
    expect(screen.getByTestId("todo-dock-list").textContent).toContain("Verify it");
  });
});

describe("between turns", () => {
  // GUI UX review, To-dos: the dock unmounted the moment a turn ended, so what the turn had
  // set out to do — and how far it got — left the screen with it.
  it("stays as a closed chip whose count says how far the plan got, and opens on a click", () => {
    render(<TodoPanel items={PLAN} settled />);
    const dock = screen.getByTestId("todo-dock");
    expect(dock.getAttribute("data-state")).toBe("settled");
    expect(screen.getByTestId("todo-dock-progress").textContent).toBe("1 of 3");
    expect(isOpen()).toBe(false);
    fireEvent.click(screen.getByTestId("todo-dock-toggle"));
    expect(isOpen()).toBe(true);
    expect(screen.getByTestId("todo-dock-list").textContent).toContain("Patch it");
  });
});

describe("the header summary", () => {
  it("names every status that has anything in it", () => {
    expect(progressLabel(PLAN)).toBe(`1 completed${SEP}1 in progress${SEP}1 pending`);
  });

  it("drops empty statuses as noise rather than reporting zeroes", () => {
    expect(
      progressLabel([
        { content: "a", status: "done" },
        { content: "b", status: "done" },
      ]),
    ).toBe("2 completed");
  });

  it("is empty only when the plan is, so a rendered header is never blank", () => {
    expect(progressLabel([])).toBe("");
    expect(progressLabel([{ content: "a", status: "pending" }])).toBe("1 pending");
  });
});
