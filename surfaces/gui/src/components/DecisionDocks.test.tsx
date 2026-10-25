import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { InboxItemCard } from "./InboxItemCard";
import { PlanCard, planTitle } from "./PlanCard";
import { turnAbove } from "./session/TurnPin";

afterEach(cleanup);

const question = (extra: Record<string, unknown> = {}) =>
  ({
    id: "q1",
    session_id: "s",
    kind: "question",
    title: "Where do we start?",
    body: "",
    state: "pending",
    resolution: null,
    inbox: "default",
    created_at: "",
    resolved_at: null,
    allow_text: true,
    options: [
      { label: "Goals", description: "Back goals with an account.", recommended: true },
      { label: "Tags", description: "Spending by tag." },
      { label: "Alerts" },
    ],
    ...extra,
  }) as never;

describe("the question dock", () => {
  it("has ONE Send, and a multi-select sends its ticks together with what was typed", () => {
    const onResolve = vi.fn();
    render(<InboxItemCard item={question({ multi: true })} onResolve={onResolve} />);
    expect(screen.getAllByRole("button", { name: /^Send/ })).toHaveLength(1);
    fireEvent.click(screen.getByText("Goals"));
    fireEvent.click(screen.getByText("Alerts"));
    fireEvent.change(screen.getByLabelText("Your own answer"), { target: { value: "and merchants" } });
    expect(screen.getByTestId("question-send").textContent).toBe("Send (3)");
    fireEvent.click(screen.getByTestId("question-send"));
    expect(onResolve).toHaveBeenCalledWith("q1", "Goals, Alerts, and merchants");
  });

  it("answers a single-select on click, and a number key picks that option", () => {
    const onResolve = vi.fn();
    render(<InboxItemCard item={question()} onResolve={onResolve} />);
    fireEvent.click(screen.getByText("Tags"));
    expect(onResolve).toHaveBeenLastCalledWith("q1", "Tags");
    fireEvent.keyDown(screen.getByTestId("question-options"), { key: "3" });
    expect(onResolve).toHaveBeenLastCalledWith("q1", "Alerts");
  });

  it("a digit typed into the answer box is text, not a pick", () => {
    const onResolve = vi.fn();
    render(<InboxItemCard item={question()} onResolve={onResolve} />);
    const input = screen.getByLabelText("Your own answer");
    fireEvent.keyDown(input, { key: "1" });
    expect(onResolve).not.toHaveBeenCalled();
    fireEvent.change(input, { target: { value: "option 1 but smaller" } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(onResolve).toHaveBeenCalledWith("q1", "option 1 but smaller");
  });
});

describe("the plan dock", () => {
  it("asks in one line, names the plan, and answers in one row", () => {
    const onRespond = vi.fn();
    render(<PlanCard item={{ kind: "planreq", plan: "# Goal contributions\n\n1. Step" }} onRespond={onRespond} />);
    expect(screen.getByTestId("plan-dock").textContent).toContain("Approve this plan?");
    expect(screen.getByTestId("plan-dock").textContent).toContain("Goal contributions");
    fireEvent.click(screen.getByTestId("plan-run"));
    expect(onRespond).toHaveBeenLastCalledWith(true, "danger-full-access");
    fireEvent.click(screen.getByTestId("plan-dismiss"));
    expect(onRespond).toHaveBeenLastCalledWith(false);
    fireEvent.click(screen.getByTestId("plan-changes"));
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "smaller steps" } });
    fireEvent.click(screen.getByTestId("plan-send-feedback"));
    expect(onRespond).toHaveBeenLastCalledWith(false, undefined, "smaller steps");
  });

  it("names the plan and counts its steps, and does not repeat the plan", () => {
    // The plan is read in the transcript, whole. The card used to show its title and first
    // three steps as well, so the plan was on screen twice (owner, 2026-10-13).
    render(
      <PlanCard
        item={{ kind: "planreq", plan: "# Goal contributions\n\n1. Read the **ledger**\n2. Add the column\n   - a detail\n3. Run the tests" }}
        onRespond={vi.fn()}
      />,
    );
    const dock = screen.getByTestId("plan-dock");
    expect(dock.textContent).toContain("Goal contributions");
    expect(dock.textContent).toContain("3 steps");
    expect(dock.querySelectorAll("li")).toHaveLength(0);
    expect(dock.textContent).not.toContain("Read the ledger");
  });

  it("in Full access, approving cannot ask for less: two answers, keys 1 and 2", () => {
    const onRespond = vi.fn();
    render(<PlanCard item={{ kind: "planreq", plan: "# Ship it" }} onRespond={onRespond} fullAccess />);
    expect(screen.queryByTestId("plan-run")).toBeNull();
    const dock = screen.getByTestId("plan-dock");
    fireEvent.keyDown(dock, { key: "3" });
    expect(onRespond).not.toHaveBeenCalled();
    fireEvent.keyDown(dock, { key: "1" });
    expect(onRespond).toHaveBeenLastCalledWith(true, undefined);
    fireEvent.keyDown(dock, { key: "2" });
    expect(screen.getByRole("textbox")).toBeTruthy();
  });

  it("titles a plan by its first heading, else its first line", () => {
    expect(planTitle("# **Goal** `contributions`\n\nbody")).toBe("Goal contributions");
    expect(planTitle("\n\nJust do it.\nThen more.")).toBe("Just do it.");
    expect(planTitle("")).toBe("");
    expect(planTitle("1. Review the code\n2. Fix it")).toBe("Review the code");
    expect(planTitle("- [ ] Ship it")).toBe("Ship it");
  });
});

describe("the turn pin", () => {
  const at = (bottom: number) => ({ getBoundingClientRect: () => ({ bottom }) }) as unknown as HTMLElement;
  it("is the last message whose bubble has scrolled above the view's top", () => {
    const [a, b, c] = [at(10), at(90), at(400)];
    expect(turnAbove([a, b, c], 100)).toBe(b);
    expect(turnAbove([a, b, c], 5)).toBeNull();
    expect(turnAbove([a, b, c], 500)).toBe(c);
  });
});

import { LEFT_TO_AGENT } from "./InboxItemCard";

describe("Let the agent choose", () => {
  it("answers a question with the agent's pick, and says it must tell what it chose", () => {
    const onResolve = vi.fn();
    render(<InboxItemCard item={question({ allow_text: false })} onResolve={onResolve} />);
    // Even when the options are all there is, the person can leave the pick to the agent.
    fireEvent.click(screen.getByTestId("question-leave"));
    expect(onResolve).toHaveBeenCalledWith("q1", LEFT_TO_AGENT);
    expect(LEFT_TO_AGENT).toMatch(/say in your answer what you chose/);
  });

  it("leaves one question of a group to the agent and moves on", () => {
    const onResolve = vi.fn();
    render(
      <InboxItemCard
        item={question({
          questions: [
            { question: "Who is the page for?", header: "Audience", options: ["Teams", "Solo"] },
            { question: "What should they do next?", header: "Action", options: ["Try it", "Talk to sales"] },
          ],
        })}
        onResolve={onResolve}
      />,
    );
    fireEvent.click(screen.getByTestId("question-leave"));
    fireEvent.click(screen.getByText("Try it"));
    expect(onResolve).toHaveBeenCalledWith("q1", JSON.stringify({ Audience: LEFT_TO_AGENT, Action: "Try it" }));
  });
});
