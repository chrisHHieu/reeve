// The goal strip, tested as state logic rather than markup.
//
// The rules worth pinning are the ones the GoalBar learned: a complete goal and no
// goal render nothing; resume is offered exactly when the goal is paused OR active but
// disarmed (a restart disarms without rewriting the phase); pause is offered only while
// active AND armed; clear asks before deleting; and a refusal lands inline instead of
// closing the editor.
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { goalState, MAX_GOAL_ROUNDS, parseTokenBudget, spendLabel, type GoalAction, type GoalInfo } from "./GoalDock";
import { ActivityRow } from "./ActivityRow";

// The goal is a segment of the activity row and the panel under it; alone in the row, it is
// what these tests read.
const GoalDock = ({ goal, onAction }: { goal: GoalInfo | null | undefined; onAction: GoalAction }) => (
  <ActivityRow goal={goal} onGoalAction={onAction} />
);

/** Render the row and open the goal's panel, as a person does to reach its actions. */
function renderOpen(ui: Parameters<typeof render>[0]) {
  const view = render(ui);
  const toggle = screen.queryByTestId("goal-dock-toggle");
  if (toggle) fireEvent.click(toggle);
  return view;
}

afterEach(cleanup);

const goal = (over: Partial<GoalInfo> = {}): GoalInfo => ({
  goal_id: "g1",
  objective: "ship the launch note",
  phase: "active",
  activation: "armed",
  rounds_started: 1,
  max_rounds: 256,
  revision: 2,
  blocker: null,
  ...over,
});

const okAction = vi.fn(async () => ({ ok: true }));

describe("the goal strip", () => {
  it("renders nothing with no goal or a loading one", () => {
    const { container } = render(<GoalDock goal={null} onAction={okAction} />);
    expect(container.innerHTML).toBe("");
    render(<GoalDock goal={undefined} onAction={okAction} />);
    expect(screen.queryByTestId("goal-dock")).toBeNull();
  });

  it("renders nothing for a complete goal — a finished goal is a result", () => {
    render(<GoalDock goal={goal({ phase: "complete" })} onAction={okAction} />);
    expect(screen.queryByTestId("goal-dock")).toBeNull();
  });

  it("says the objective in the row, with no word beside a goal that is simply running, and the round in the panel", () => {
    renderOpen(<GoalDock goal={goal()} onAction={okAction} />);
    expect(screen.queryByTestId("goal-dock-state")).toBeNull();
    expect(screen.getByTestId("goal-dock-objective").textContent).toBe("ship the launch note");
    // GUI UX review, Goal: "1/256" read as a fraction of something; 256 is only the
    // default cap. A default cap is left to the title, a chosen one is said.
    const rounds = screen.getByTestId("goal-dock-rounds");
    expect(rounds.textContent).toBe("round 1");
    expect(rounds.getAttribute("title")).toBe("Round limit: 256");
  });

  it("says the round against a cap someone chose", () => {
    renderOpen(<GoalDock goal={{ ...goal(), rounds_started: 2, max_rounds: 8 }} onAction={okAction} />);
    expect(screen.getByTestId("goal-dock-rounds").textContent).toBe("round 2 of 8");
  });

  it("shows each acceptance check's standing — the goal's real progress", () => {
    renderOpen(
      <GoalDock
        goal={{
          ...goal(),
          checks: ["pytest -q", "ruff check .", "npm test"],
          check_status: [
            { command: "pytest -q", state: "passed" },
            { command: "ruff check .", state: "failed" },
            { command: "npm test", state: "not_run" },
          ],
        }}
        onAction={okAction}
      />,
    );
    const checks = screen.getByTestId("goal-dock-checks");
    expect([...checks.querySelectorAll("[data-state]")].map((el) => [el.getAttribute("data-state"), el.textContent])).toEqual([
      ["passed", "pytest -q ✓"],
      ["failed", "ruff check . ✗"],
      ["not_run", "npm test not run"],
    ]);
  });

  it("names its actions in words, and Clear reads as the dangerous one", () => {
    renderOpen(<GoalDock goal={goal()} onAction={okAction} />);
    expect(screen.getByRole("button", { name: "Pause goal" }).textContent).toContain("Pause");
    expect(screen.getByRole("button", { name: "Edit goal" }).textContent).toContain("Edit");
    const clear = screen.getByRole("button", { name: "Clear goal" });
    expect(clear.textContent).toContain("Clear");
    expect(clear.className).toContain("danger");
  });

  it("tells an armed active goal from a disarmed one", () => {
    const { rerender } = renderOpen(<GoalDock goal={goal()} onAction={okAction} />);
    expect(screen.queryByTestId("goal-dock-state")).toBeNull();
    rerender(<GoalDock goal={goal({ activation: "disarmed" })} onAction={okAction} />);
    expect(screen.getByTestId("goal-dock-state").textContent).toBe("Not running");
  });

  it("offers pause only while armed, and resume while paused or disarmed", () => {
    const { rerender } = renderOpen(<GoalDock goal={goal()} onAction={okAction} />);
    expect(screen.getByRole("button", { name: "Pause goal" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Resume goal" })).toBeNull();

    rerender(<GoalDock goal={goal({ activation: "disarmed" })} onAction={okAction} />);
    expect(screen.queryByRole("button", { name: "Pause goal" })).toBeNull();
    expect(screen.getByRole("button", { name: "Resume goal" })).toBeTruthy();

    rerender(<GoalDock goal={goal({ phase: "paused", activation: "disarmed" })} onAction={okAction} />);
    expect(screen.getByTestId("goal-dock-state").textContent).toBe("Paused");
    expect(screen.getByRole("button", { name: "Resume goal" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Pause goal" })).toBeNull();
  });

  it("says a blocked goal is blocked, with the blocker's sentence on the segment and in the panel", () => {
    renderOpen(
      <GoalDock
        goal={goal({
          phase: "blocked",
          activation: "disarmed",
          blocker: { code: "round-limit", message: "Goal reached its configured limit of 256 rounds." },
        })}
        onAction={okAction}
      />,
    );
    expect(screen.getByTestId("goal-dock-state").textContent).toBe("Blocked");
    expect(screen.getByTestId("goal-dock").getAttribute("title")).toBe(
      "Goal reached its configured limit of 256 rounds.",
    );
    expect(screen.getByTestId("goal-dock-blocker").textContent).toBe("Goal reached its configured limit of 256 rounds.");
  });

  it("calls pause and resume through the action prop", async () => {
    const onAction = vi.fn(async () => ({ ok: true }));
    const { rerender } = renderOpen(<GoalDock goal={goal()} onAction={onAction} />);
    fireEvent.click(screen.getByRole("button", { name: "Pause goal" }));
    await waitFor(() => expect(onAction).toHaveBeenCalledWith("pause", undefined));

    rerender(<GoalDock goal={goal({ phase: "paused" })} onAction={onAction} />);
    fireEvent.click(screen.getByRole("button", { name: "Resume goal" }));
    await waitFor(() => expect(onAction).toHaveBeenCalledWith("resume", undefined));
  });

  it("edits the objective and the cap inline, Enter saves and Escape cancels", async () => {
    const onAction = vi.fn(async () => ({ ok: true }));
    renderOpen(<GoalDock goal={goal()} onAction={onAction} />);
    fireEvent.click(screen.getByRole("button", { name: "Edit goal" }));

    const input = screen.getByLabelText("Goal objective") as HTMLInputElement;
    const cap = screen.getByLabelText("Round limit") as HTMLInputElement;
    expect(input.value).toBe("ship the launch note");
    expect(cap.value).toBe("256");
    // The form caps at the bound, not the old 20.
    expect(cap.max).toBe(String(MAX_GOAL_ROUNDS));

    fireEvent.change(input, { target: { value: "ship it better" } });
    fireEvent.change(cap, { target: { value: "8" } });
    fireEvent.keyDown(input, { key: "Enter" });
    await waitFor(() =>
      expect(onAction).toHaveBeenCalledWith("edit", { objective: "ship it better", max_rounds: 8, token_budget: 0 }),
    );

    fireEvent.click(screen.getByRole("button", { name: "Edit goal" }));
    fireEvent.keyDown(screen.getByLabelText("Goal objective"), { key: "Escape" });
    expect(screen.queryByLabelText("Goal objective")).toBeNull();
  });

  it("asks before clearing, and the confirm sends clear", async () => {
    const onAction = vi.fn(async () => ({ ok: true }));
    renderOpen(<GoalDock goal={goal()} onAction={onAction} />);
    fireEvent.click(screen.getByRole("button", { name: "Clear goal" }));

    // A dialog, not a tick that appears beside the button that was just pressed.
    expect(screen.getByTestId("confirm-dialog").textContent).toContain("Clear this goal?");
    expect(onAction).not.toHaveBeenCalled();
    fireEvent.click(screen.getByTestId("confirm-dialog-ok"));
    await waitFor(() => expect(onAction).toHaveBeenCalledWith("clear", undefined));
  });

  it("cancels a clear confirm without deleting anything", () => {
    const onAction = vi.fn(async () => ({ ok: true }));
    renderOpen(<GoalDock goal={goal()} onAction={onAction} />);
    fireEvent.click(screen.getByRole("button", { name: "Clear goal" }));
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByTestId("confirm-dialog")).toBeNull();
    expect(onAction).not.toHaveBeenCalled();
  });

  it("shows a refusal inline and keeps the editor open", async () => {
    const onAction = vi.fn(async () => ({ ok: false, error: "the goal used all 256 rounds" }));
    renderOpen(<GoalDock goal={goal()} onAction={onAction} />);
    fireEvent.click(screen.getByRole("button", { name: "Edit goal" }));
    fireEvent.keyDown(screen.getByLabelText("Goal objective"), { key: "Enter" });

    await waitFor(() =>
      expect(screen.getByRole("alert").textContent).toContain("the goal used all 256 rounds"),
    );
    expect(screen.getByLabelText("Goal objective")).toBeTruthy();
  });

  it("drops an open editor when the goal identity changes underneath it", () => {
    const { rerender } = renderOpen(<GoalDock goal={goal()} onAction={okAction} />);
    fireEvent.click(screen.getByRole("button", { name: "Edit goal" }));
    expect(screen.getByLabelText("Goal objective")).toBeTruthy();

    rerender(<GoalDock goal={goal({ goal_id: "g2", objective: "a different goal" })} onAction={okAction} />);
    expect(screen.queryByLabelText("Goal objective")).toBeNull();
    expect(screen.getByTestId("goal-dock-objective").textContent).toBe("a different goal");
  });

  it("keeps an open editor when the SAME goal's revision moves under it", () => {
    // A round charge bumps the revision while the person is typing; closing the form on
    // every live frame would make the editor unusable during exactly the work it manages.
    const { rerender } = renderOpen(<GoalDock goal={goal()} onAction={okAction} />);
    fireEvent.click(screen.getByRole("button", { name: "Edit goal" }));
    fireEvent.change(screen.getByLabelText("Goal objective"), { target: { value: "mid-edit" } });

    rerender(<GoalDock goal={goal({ revision: 3, rounds_started: 2 })} onAction={okAction} />);
    expect((screen.getByLabelText("Goal objective") as HTMLInputElement).value).toBe("mid-edit");
  });
});

describe("the ledger on the strip", () => {
  it("says what the rounds spent, against the budget when one is set", () => {
    renderOpen(<GoalDock goal={goal({ rounds_started: 3, tokens_used: 1_240_000 })} onAction={okAction} />);
    expect(screen.getByTestId("goal-dock-rounds").textContent).toBe("round 3 · 1.24M tokens");
    cleanup();
    renderOpen(<GoalDock goal={goal({ tokens_used: 400_000, token_budget: 5_000_000 })} onAction={okAction} />);
    expect(screen.getByTestId("goal-dock-spend").textContent).toBe(" · 400k of 5M tokens");
    // An older server sends neither field; the strip reads as it always did.
    expect(spendLabel(goal())).toBe("");
  });

  it("warns while rounds stop moving, and only for a running goal", () => {
    renderOpen(<GoalDock goal={goal({ stalled_rounds: 2 })} onAction={okAction} />);
    expect(screen.getByTestId("goal-dock-stall").textContent).toBe("No progress in the last 2 rounds, stops at 3");
    expect(screen.getByTestId("goal-dock-state").textContent).toBe("Stalled");
    cleanup();
    renderOpen(<GoalDock goal={goal({ stalled_rounds: 0 })} onAction={okAction} />);
    expect(screen.queryByTestId("goal-dock-stall")).toBeNull();
    cleanup();
    // Blocked no-progress: the blocker sentence says it; a chip would repeat it.
    renderOpen(<GoalDock goal={goal({ phase: "blocked", stalled_rounds: 3 })} onAction={okAction} />);
    expect(screen.queryByTestId("goal-dock-stall")).toBeNull();
  });

  it("edits the token budget in words a person types, and refuses nonsense", async () => {
    const onAction = vi.fn(async () => ({ ok: true }));
    renderOpen(<GoalDock goal={goal({ token_budget: 2_000_000 })} onAction={onAction} />);
    fireEvent.click(screen.getByRole("button", { name: "Edit goal" }));
    const budget = screen.getByLabelText("Token budget") as HTMLInputElement;
    expect(budget.value).toBe("2M");
    fireEvent.change(budget, { target: { value: "lots" } });
    expect((screen.getByRole("button", { name: "Save goal" }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(budget, { target: { value: "7.5M" } });
    fireEvent.keyDown(budget, { key: "Enter" });
    await waitFor(() =>
      expect(onAction).toHaveBeenCalledWith("edit", {
        objective: "ship the launch note",
        max_rounds: 256,
        token_budget: 7_500_000,
      }),
    );
  });

  it("parses budgets", () => {
    expect(parseTokenBudget("")).toBe(0);
    expect(parseTokenBudget("0")).toBe(0);
    expect(parseTokenBudget("500k")).toBe(500_000);
    expect(parseTokenBudget(" 1.5 M ")).toBe(1_500_000);
    expect(parseTokenBudget("250,000")).toBe(250_000);
    expect(parseTokenBudget("-5")).toBeNull();
    expect(parseTokenBudget("5 tokens")).toBeNull();
  });
});

describe("the label rule", () => {
  it("splits active by activation and passes the other phases through", () => {
    expect(goalState(goal())).toBe("");
    expect(goalState(goal({ activation: "disarmed" }))).toBe("Not running");
    expect(goalState(goal({ stalled_rounds: 1 }))).toBe("Stalled");
    expect(goalState(goal({ phase: "paused" }))).toBe("Paused");
    expect(goalState(goal({ phase: "blocked" }))).toBe("Blocked");
    expect(goalState(goal({ phase: "complete" }))).toBe("");
  });
});
