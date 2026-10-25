import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { PlanBlock } from "./PlanBlock";
import { rowSpec } from "./registry";
import type { ToolItem } from "./cardModels";
import { Transcript } from "../components/Transcript";
import type { Item } from "../types";

afterEach(cleanup);

const PLAN = [
  "# Add a --json flag to export",
  "",
  "1. Add the flag to the parser",
  "2. Pass it through to the writer",
  "3. Cover it with tests",
  "",
  "## Risks",
  "",
  "- The flag needs Python 3.9 or newer.",
].join("\n");

const call = (status: string, preview?: string): ToolItem =>
  ({ kind: "tool", id: "p1", callId: "p1", name: "propose_plan", args: { plan: PLAN, path: "plans/task.md" }, status, preview }) as ToolItem;

describe("PlanBlock", () => {
  it("shows the whole plan: nothing is clamped and nothing has to be opened", () => {
    render(<PlanBlock text={PLAN} path="plans/task.md" />);
    expect(screen.getByText(/Cover it with tests/)).toBeTruthy();
    expect(screen.getByText(/Python 3\.9 or newer/)).toBeTruthy();
    expect(screen.getByText(/plans\/task\.md/)).toBeTruthy();
    expect(screen.queryByTestId("plan-view-full")).toBeNull();
  });

  it("marks a plan that was sent back", () => {
    render(<PlanBlock text={PLAN} rejected />);
    expect(screen.getByTestId("plan-block").hasAttribute("data-rejected")).toBe(true);
  });

  it("renders nothing for an empty plan", () => {
    const { container } = render(<PlanBlock text="   " />);
    expect(container.firstChild).toBeNull();
  });
});

describe("the plan's row says where the plan stands", () => {
  it("waiting: it names itself and counts its steps", () => {
    expect(rowSpec(call("…"))).toMatchObject({ title: "Proposed a plan", summary: "3 steps" });
  });

  it("approved: one line that names the plan", () => {
    const spec = rowSpec(call("ok", JSON.stringify({ approved: true })));
    expect(spec).toMatchObject({ title: "Plan approved", summary: "Add a --json flag to export · 3 steps" });
  });

  it("sent back: the person's note is the line", () => {
    const spec = rowSpec(call("error", JSON.stringify({ approved: false, feedback: "Keep --pretty working too." })));
    expect(spec).toMatchObject({ title: "Plan sent back", summary: "Keep --pretty working too." });
  });
});

describe("a proposed plan in the transcript", () => {
  const user: Item = { kind: "user", text: "plan it" };

  it("is drawn whole under its row while it waits, and the row is at rest", () => {
    const { container } = render(<Transcript items={[user, call("…") as Item]} running onApprove={vi.fn()} />);
    expect(screen.getByTestId("plan-block").textContent).toContain("Python 3.9 or newer");
    expect(screen.getByTestId("plan-waiting").textContent).toBe("needs your OK");
    const row = container.querySelector(".tool-row")!;
    expect(row.getAttribute("data-state")).toBe("waiting");
    // waiting on a person is not work in flight: nothing shimmers
    expect(row.querySelector(".shimmer-text")).toBeNull();
  });

  it("folds behind one line once it is approved, and opens again on a click", () => {
    const approved = call("ok", JSON.stringify({ approved: true })) as Item;
    const { container } = render(
      <Transcript items={[user, approved, { kind: "planreq", plan: PLAN, resolved: "approved" }]} onApprove={vi.fn()} />,
    );
    expect(screen.queryByTestId("plan-block")).toBeNull();
    expect(container.textContent).toContain("Plan approved");
    // the row says the answer, so there is no second note saying it again
    expect(container.textContent!.match(/Plan approved/g)).toHaveLength(1);
    fireEvent.click(container.querySelector(".tool-row-line")!);
    expect(screen.getByTestId("plan-block").textContent).toContain("Cover it with tests");
  });

  it("a plan sent back is one quiet line with the note, not a failure", () => {
    const back = call("error", JSON.stringify({ approved: false, feedback: "Keep --pretty working too." })) as Item;
    const { container } = render(<Transcript items={[user, back]} onApprove={vi.fn()} />);
    const row = container.querySelector(".tool-row")!;
    expect(row.getAttribute("data-state")).toBe("sentback");
    expect(row.textContent).toContain("Plan sent back");
    expect(row.textContent).toContain("Keep --pretty working too.");
    expect(screen.queryByTestId("plan-block")).toBeNull();
    // it is the person's answer: it is not counted against the turn
    expect(row.querySelector(".sr-only")).toBeNull();
  });
});
