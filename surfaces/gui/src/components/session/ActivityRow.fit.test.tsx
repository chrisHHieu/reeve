// The row at the width it has (owner, 2026-10-18): it tries the fullest form and steps down
// while its segments overflow, or while the goal's sentence is squeezed below a readable width.
// jsdom has no layout, so the widths are the test's: what overflows depends on the form the row
// is trying when it asks.
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { ActivityRow } from "./ActivityRow";

const goal: any = { objective: "Ship the release notes", phase: "paused", round: 1, max_rounds: 0 };
const todo: any[] = [{ content: "a", status: "in_progress" }, { content: "b", status: "pending" }];

/** Make the row's segments `need` px wide in each form, inside a `room`-px column, and the goal's
 *  sentence `sentence` px in each form. */
function layout(room: number, need: Record<string, number>, sentence: Record<string, number> = {}) {
  const fitOf = (el: Element) => (el.closest("[data-fit]") as HTMLElement | null)?.dataset.fit ?? "wide";
  const scroll = Object.getOwnPropertyDescriptor(HTMLElement.prototype, "scrollWidth");
  const client = Object.getOwnPropertyDescriptor(HTMLElement.prototype, "clientWidth");
  Object.defineProperty(HTMLElement.prototype, "scrollWidth", {
    configurable: true,
    get(this: HTMLElement) {
      return this.classList.contains("act-segs") ? need[fitOf(this)] ?? 0 : 0;
    },
  });
  Object.defineProperty(HTMLElement.prototype, "clientWidth", {
    configurable: true,
    get(this: HTMLElement) {
      if (this.classList.contains("act-segs")) return room;
      if (this.dataset.testid === "goal-dock-objective") return sentence[fitOf(this)] ?? 200;
      return 0;
    },
  });
  return () => {
    if (scroll) Object.defineProperty(HTMLElement.prototype, "scrollWidth", scroll);
    if (client) Object.defineProperty(HTMLElement.prototype, "clientWidth", client);
  };
}

let restore: (() => void) | null = null;
afterEach(() => {
  restore?.();
  restore = null;
  cleanup();
});

const row = () => screen.getByTestId("activity-row");

describe("the activity row's form", () => {
  it("keeps names and words while they fit", () => {
    restore = layout(800, { wide: 700, medium: 400, narrow: 300 });
    render(<ActivityRow placement="head" goal={goal} onGoalAction={async () => {}} todo={todo} />);
    expect(row().dataset.fit).toBe("wide");
  });

  it("gives way to icons and numbers when the words overflow", () => {
    restore = layout(600, { wide: 900, medium: 560, narrow: 300 });
    render(<ActivityRow placement="head" goal={goal} onGoalAction={async () => {}} todo={todo} />);
    expect(row().dataset.fit).toBe("medium");
    // Hidden words are not lost: each segment says itself in full.
    expect(screen.getByTestId("todo-dock-toggle").getAttribute("aria-label")).toBe("To-dos: 0 of 2");
    expect(screen.getByTestId("goal-dock-toggle").getAttribute("aria-label")).toBe("Goal: Ship the release notes · Paused");
  });

  it("goes to two lines when even the numbers overflow", () => {
    restore = layout(400, { wide: 900, medium: 560, narrow: 380 });
    render(<ActivityRow placement="head" goal={goal} onGoalAction={async () => {}} todo={todo} />);
    expect(row().dataset.fit).toBe("narrow");
  });

  it("steps down when the goal's sentence would be squeezed, though nothing overflows", () => {
    restore = layout(800, { wide: 780, medium: 600, narrow: 300 }, { wide: 40, medium: 240 });
    render(<ActivityRow placement="head" goal={goal} onGoalAction={async () => {}} todo={todo} />);
    expect(row().dataset.fit).toBe("medium");
  });
});
