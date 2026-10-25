import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { InboxItemCard } from "./InboxItemCard";
import { allToAgent, answerOf, FORM_FROM, formAnswers, LEFT_TO_AGENT, toggle, type FormQuestion } from "./questionAnswers";

afterEach(cleanup);

const q = (header: string, options: { label: string; recommended?: boolean }[], extra: Partial<FormQuestion> = {}): FormQuestion => ({
  question: `${header}?`,
  header,
  options: options.map((option) => ({ label: option.label, description: "", recommended: !!option.recommended, preview: "" })),
  allowText: true,
  multi: false,
  ...extra,
});

describe("what a form question answers", () => {
  const audience = q("Audience", [{ label: "Teams", recommended: true }, { label: "Solo" }]);
  const states = q("States", [{ label: "Empty", recommended: true }, { label: "Error", recommended: true }, { label: "Loading" }], { multi: true });
  const reference = q("Reference", []);

  it("stands on the recommended option until touched, and leaves an unanswered question to the agent", () => {
    expect(answerOf(audience, undefined)).toBe("Teams");
    expect(answerOf(states, undefined)).toBe("Empty, Error");
    expect(answerOf(reference, undefined)).toBe(LEFT_TO_AGENT);
    // Clicking the standing choice takes it off: the question goes back to the agent.
    expect(answerOf(audience, toggle(audience, undefined, "Teams"))).toBe(LEFT_TO_AGENT);
    expect(answerOf(audience, toggle(audience, undefined, "Solo"))).toBe("Solo");
    expect(answerOf(states, toggle(states, undefined, "Loading"))).toBe("Empty, Error, Loading");
    expect(answerOf(states, toggle(states, undefined, "Error"))).toBe("Empty");
  });

  it("takes typed words over the choices of a single question, and beside them in a multi", () => {
    expect(answerOf(audience, { picked: [], text: "Clinics" })).toBe("Clinics");
    expect(answerOf(states, { picked: ["Empty"], text: "offline" })).toBe("Empty, offline");
    expect(answerOf(reference, { picked: null, text: " linear.app/pricing " })).toBe("linear.app/pricing");
  });

  it("answers every question in one map, or leaves them all", () => {
    expect(JSON.parse(formAnswers([audience, reference], { 1: { picked: null, text: "stripe.com" } }))).toEqual({ Audience: "Teams", Reference: "stripe.com" });
    expect(JSON.parse(allToAgent([audience, states]))).toEqual({ Audience: LEFT_TO_AGENT, States: LEFT_TO_AGENT });
  });
});

const item = (count: number) =>
  ({
    id: "q1",
    session_id: "s",
    kind: "question",
    title: "Audience?",
    body: "",
    state: "pending",
    resolution: null,
    inbox: "default",
    created_at: "",
    resolved_at: null,
    questions: Array.from({ length: count }, (_, index) => ({
      question: `Question ${index + 1}?`,
      header: `Q${index + 1}`,
      options: [{ label: `Yes ${index + 1}`, recommended: index === 0 }, { label: `No ${index + 1}` }],
    })),
  }) as never;

describe("the question form", () => {
  it(`is a form from ${FORM_FROM} questions and a stepper below`, () => {
    render(<InboxItemCard item={item(FORM_FROM - 1)} onResolve={() => undefined} />);
    expect(screen.getByTestId("question-stepper")).toBeTruthy();
    expect(screen.queryByTestId("question-form")).toBeNull();
    cleanup();
    render(<InboxItemCard item={item(FORM_FROM)} onResolve={() => undefined} />);
    expect(screen.getAllByTestId("question-form-item")).toHaveLength(FORM_FROM);
  });

  it("sends what was chosen, the recommended where untouched, and the rest to the agent", () => {
    const onResolve = vi.fn();
    render(<InboxItemCard item={item(6)} onResolve={onResolve} />);
    expect(screen.getByText(`${6} questions before I start`)).toBeTruthy();
    expect(screen.getByText("5 left to the agent")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "No 2" }));
    expect(screen.queryByLabelText("Your own answer to: Question 3?")).toBeNull();
    fireEvent.click(screen.getAllByRole("button", { name: "Other…" })[2]);
    fireEvent.change(screen.getByLabelText("Your own answer to: Question 3?"), { target: { value: "Maybe" } });
    expect(screen.getByText("3 left to the agent")).toBeTruthy();
    fireEvent.click(screen.getByTestId("question-send"));
    const sent = JSON.parse(onResolve.mock.calls[0][1]);
    expect(sent).toEqual({ Q1: "Yes 1", Q2: "No 2", Q3: "Maybe", Q4: LEFT_TO_AGENT, Q5: LEFT_TO_AGENT, Q6: LEFT_TO_AGENT });
  });

  it("leaves everything to the agent in one press", () => {
    const onResolve = vi.fn();
    render(<InboxItemCard item={item(5)} onResolve={onResolve} />);
    fireEvent.click(screen.getByTestId("question-form-leave"));
    expect(Object.values(JSON.parse(onResolve.mock.calls[0][1]))).toEqual(Array(5).fill(LEFT_TO_AGENT));
  });
});
