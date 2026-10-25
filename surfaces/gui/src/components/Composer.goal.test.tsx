// The composer's `/goal` command: the popover row runs the bare
// status, a typed `/goal …` draft is intercepted on send (never a chat message), every
// result/refusal lands in `onGoalNotice`, and the mutations travel the human route.
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { Composer } from "./Composer";
import type { GoalInfo } from "../api";

const activeGoal = (over: Partial<GoalInfo> = {}): GoalInfo => ({
  goal_id: "g1",
  objective: "ship the launch note",
  phase: "active",
  activation: "armed",
  rounds_started: 2,
  max_rounds: 256,
  revision: 3,
  blocker: null,
  ...over,
});

function stubFetch(goalState: { goal: GoalInfo | null }) {
  const calls: { url: string; method: string; body: any }[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      const method = (init?.method || "GET").toUpperCase();
      const body = init?.body ? JSON.parse(String(init.body)) : undefined;
      calls.push({ url, method, body });
      if (url.includes("/skills")) return { ok: true, json: async () => ({ skills: [] }) } as Response;
      if (/\/goal$/.test(url)) {
        if (method === "POST") {
          const b = body || {};
          if (b.action === "clear") goalState.goal = null;
          else if (b.action === "set")
            goalState.goal = activeGoal({
              goal_id: "g2",
              objective: b.objective,
              max_rounds: b.max_rounds ?? 256,
              rounds_started: 0,
              revision: 1,
            });
          else if (b.action === "pause" && goalState.goal)
            goalState.goal = { ...goalState.goal, phase: "paused", activation: "disarmed" };
          else if (b.action === "resume" && goalState.goal)
            goalState.goal = { ...goalState.goal, phase: "active", activation: "armed" };
          else if (b.action === "edit" && goalState.goal)
            goalState.goal = { ...goalState.goal, objective: b.objective ?? goalState.goal.objective };
          return { ok: true, json: async () => ({ ok: true, goal: goalState.goal }) } as Response;
        }
        return { ok: true, json: async () => ({ goal: goalState.goal }) } as Response;
      }
      return { ok: true, json: async () => ({}) } as Response;
    }),
  );
  return calls;
}

type GoalProps = Parameters<typeof Composer>[0] & {
  onGoalNotice: ReturnType<typeof vi.fn>;
};

const props = (extra: Partial<Parameters<typeof Composer>[0]> = {}): GoalProps =>
  ({
    mode: "interactive",
    model: "gpt-5.6-sol",
    running: false,
    connected: true,
    sessionId: "s1",
    onSend: vi.fn(),
    onInterrupt: vi.fn(),
    onModeChange: vi.fn(),
    onModelChange: vi.fn(),
    onGoalNotice: vi.fn(),
    ...extra,
  }) as GoalProps;

const box = () => screen.getByPlaceholderText(/Ask anything/);
const notices = (p: GoalProps) =>
  p.onGoalNotice.mock.calls.map((c) => ({ text: c[0], tone: c[1], title: c[2] }));
const posts = (calls: { url: string; method: string; body: any }[]) =>
  calls.filter((c) => c.method === "POST" && /\/goal$/.test(c.url));

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("Composer / the /goal command", () => {
  it("bare /goal shows status only: the popover row, and no mutation", async () => {
    const calls = stubFetch({ goal: null });
    const p = props();
    render(<Composer {...p} />);
    fireEvent.change(box(), { target: { value: "/goal" } });
    fireEvent.click(await screen.findByTestId("slash-command-goal"));
    await waitFor(() => expect(p.onGoalNotice).toHaveBeenCalled());
    expect(notices(p)[0]).toMatchObject({
      text: "No goal is currently set.\nUsage: /goal [<objective>|clear|edit <objective>|pause|resume]",
      tone: "info",
    });
    expect(posts(calls)).toEqual([]);
    expect(p.onSend).not.toHaveBeenCalled();
    expect((box() as HTMLTextAreaElement).value).toBe("");
  });

  it("bare /goal with a goal renders the reference design status lines from the GET", async () => {
    stubFetch({ goal: activeGoal() });
    const p = props();
    render(<Composer {...p} />);
    fireEvent.change(box(), { target: { value: "/goal " } });
    fireEvent.keyDown(box(), { key: "Enter" });
    await waitFor(() => expect(p.onGoalNotice).toHaveBeenCalled());
    const first = notices(p)[0];
    expect(first.title).toBe("Goal");
    expect(first.text).toContain("Status: active");
    expect(first.text).toContain("Objective: ship the launch note");
    expect(first.text).toContain("Rounds: 2/256");
    expect(first.text).toContain("Activation: armed");
    expect(first.text).toContain("Commands: /goal edit <objective>, /goal pause, /goal clear");
  });

  it("creates through the route's set action, with a quoted objective decoded", async () => {
    const calls = stubFetch({ goal: null });
    const p = props();
    render(<Composer {...p} />);
    fireEvent.change(box(), { target: { value: '/goal "ship the release notes"' } });
    fireEvent.keyDown(box(), { key: "Enter" });
    await waitFor(() => expect(posts(calls).length).toBe(1));
    expect(posts(calls)[0].body).toEqual({
      action: "set",
      objective: "ship the release notes",
    });
    expect(notices(p)[0]).toMatchObject({ title: "Goal created", tone: "info" });
    expect(notices(p)[0].text).toContain("Objective: ship the release notes");
    expect(p.onSend).not.toHaveBeenCalled();
  });

  it("refuses to replace a non-complete goal with the reference design's sentence, and the complete one replaces", async () => {
    const active = stubFetch({ goal: activeGoal() });
    const p = props();
    render(<Composer {...p} />);
    fireEvent.change(box(), { target: { value: "/goal a different objective" } });
    fireEvent.keyDown(box(), { key: "Enter" });
    await waitFor(() => expect(p.onGoalNotice).toHaveBeenCalled());
    expect(notices(p)[0]).toMatchObject({
      text: "A goal is already active. Use /goal edit <objective> to change it or /goal clear before replacing it.",
      tone: "warn",
    });
    expect(posts(active)).toEqual([]);

    cleanup();
    const complete = stubFetch({ goal: activeGoal({ phase: "complete", activation: "disarmed" }) });
    const q = props();
    render(<Composer {...q} />);
    fireEvent.change(box(), { target: { value: "/goal the next objective" } });
    fireEvent.keyDown(box(), { key: "Enter" });
    await waitFor(() => expect(posts(complete).length).toBe(1));
    expect(posts(complete)[0].body).toEqual({ action: "set", objective: "the next objective" });
    expect(notices(q)[0]).toMatchObject({ title: "Goal created" });
  });

  it("clears, pauses and resumes; and refuses each with no goal set", async () => {
    const calls = stubFetch({ goal: activeGoal() });
    const p = props();
    render(<Composer {...p} />);
    fireEvent.change(box(), { target: { value: "/goal pause" } });
    fireEvent.keyDown(box(), { key: "Enter" });
    await waitFor(() => expect(posts(calls).length).toBe(1));
    expect(posts(calls)[0].body).toEqual({ action: "pause" });
    expect(notices(p)[0]).toMatchObject({ title: "Goal paused" });

    fireEvent.change(box(), { target: { value: "/goal resume" } });
    fireEvent.keyDown(box(), { key: "Enter" });
    await waitFor(() => expect(posts(calls).length).toBe(2));
    expect(posts(calls)[1].body).toEqual({ action: "resume" });
    expect(notices(p)[1]).toMatchObject({ title: "Goal resumed" });

    fireEvent.change(box(), { target: { value: "/goal clear" } });
    fireEvent.keyDown(box(), { key: "Enter" });
    await waitFor(() => expect(posts(calls).length).toBe(3));
    expect(posts(calls)[2].body).toEqual({ action: "clear" });
    expect(notices(p)[2]).toMatchObject({ text: "Goal cleared.", tone: "info" });

    cleanup();
    const empty = stubFetch({ goal: null });
    const q = props();
    render(<Composer {...q} />);
    fireEvent.change(box(), { target: { value: "/goal pause" } });
    fireEvent.keyDown(box(), { key: "Enter" });
    await waitFor(() => expect(q.onGoalNotice).toHaveBeenCalledTimes(1));
    fireEvent.change(box(), { target: { value: "/goal resume" } });
    fireEvent.keyDown(box(), { key: "Enter" });
    await waitFor(() => expect(q.onGoalNotice).toHaveBeenCalledTimes(2));
    expect(notices(q)[0].text).toBe(
      "No goal is currently set; /goal pause requires one.\nUsage: /goal [<objective>|clear|edit <objective>|pause|resume]",
    );
    expect(notices(q)[1].text).toContain("/goal resume requires one");
    expect(posts(empty)).toEqual([]);
  });

  it("edits the objective, refuses edit without one, and unknown words are objectives", async () => {
    const calls = stubFetch({ goal: activeGoal() });
    const p = props();
    render(<Composer {...p} />);

    fireEvent.change(box(), { target: { value: "/goal edit" } });
    fireEvent.keyDown(box(), { key: "Enter" });
    await waitFor(() => expect(p.onGoalNotice).toHaveBeenCalled());
    expect(notices(p)[0]).toMatchObject({
      text: "Goal editing requires a replacement objective.\nUsage: /goal [<objective>|clear|edit <objective>|pause|resume]",
      tone: "warn",
    });
    expect(posts(calls)).toEqual([]);

    fireEvent.change(box(), { target: { value: "/goal edit the improved objective" } });
    fireEvent.keyDown(box(), { key: "Enter" });
    await waitFor(() => expect(posts(calls).length).toBe(1));
    expect(posts(calls)[0].body).toEqual({
      action: "edit",
      objective: "the improved objective",
    });
    expect(notices(p)[1]).toMatchObject({ title: "Goal updated" });

    // `pause everything` is not the pause verb — it is an objective, and it hits set.
    fireEvent.change(box(), { target: { value: "/goal pause everything" } });
    fireEvent.keyDown(box(), { key: "Enter" });
    await waitFor(() => expect(notices(p)[2]).toBeTruthy());
    expect(notices(p)[2]).toMatchObject({ tone: "warn" });
    expect(notices(p)[2].text).toContain("A goal is already active");
    expect(posts(calls).length).toBe(1); // refused as a replacement, never a pause POST
  });

  it("a plain message that merely starts with /goal-like text still sends", async () => {
    stubFetch({ goal: null });
    const p = props();
    render(<Composer {...p} />);
    fireEvent.change(box(), { target: { value: "goals are useful" } });
    fireEvent.keyDown(box(), { key: "Enter" });
    await waitFor(() => expect(p.onSend).toHaveBeenCalled());
    expect(p.onGoalNotice).not.toHaveBeenCalled();
  });
});
