// A subagent's session in the workspace panel (owner, 2026-10-13): the child's transcript drawn
// with the chat's own components, under a head that says who it is and how it is going. The
// task it was given is the main agent's, not the reader's bubble; the last answer is named for
// what it is; nothing here can write to the child, and Esc is not taken from the chat.
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { SubagentPanel, type SubagentPanelProps } from "./SubagentPanel";
import type { DelegationNode } from "../../delegations";

const LONG_TASK =
  "You are a fresh explore subagent. Your only job is to report what tools you actually have. " +
  "Report only what you observe; never guess. Do this in order, and modify no file: list the " +
  "deferred tool catalog, describe one read-only tool, and say what you do not have.";

const messages = (task = "Research repo subagents") => [
  { role: "user", content: [{ type: "text", text: task }] },
  { role: "assistant", content: "Reading the spec.", reasoning: "Start with the task tool." },
  {
    role: "assistant",
    content: "",
    tool_calls: [{ id: "c1", function: { name: "read_file", arguments: '{"path":"subagent_spec.py"}' } }],
  },
  { role: "tool", tool_call_id: "c1", content: "216 lines", _presentation: { summary: "subagent_spec.py" } },
  { role: "assistant", content: "The report: children default to uncapped." },
];

function stubFetch(body: unknown = messages()) {
  const urls: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      urls.push(String(url));
      return { json: async () => ({ messages: body }) };
    }),
  );
  return urls;
}

const node = (over: Partial<DelegationNode> = {}): DelegationNode =>
  ({
    tool: { kind: "tool", id: "t1", name: "task", args: {}, status: "ok" },
    key: "t1",
    agentId: "explore",
    title: "Explore",
    description: "Research repo subagents",
    color: "#a4571f",
    outcome: "completed",
    background: false,
    steps: 3,
    tokens: 9100,
    model: "deepseek:deepseek-flash",
    durationMs: 42_000,
    startedAtMs: null,
    live: null,
    failure: null,
    childId: "child-1",
    kept: false,
    waiting: null,
    ...over,
  }) as DelegationNode;

function renderPanel(over: Partial<SubagentPanelProps> = {}) {
  return render(<SubagentPanel sessionId="child-1" title="Research repo subagents" busy={false} node={node()} {...over} />);
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("SubagentPanel", () => {
  it("draws the child's steps from its session, and its task as the main agent's words", async () => {
    const urls = stubFetch();
    renderPanel();
    await waitFor(() => expect(screen.getByTestId("child-transcript")).toBeTruthy());
    expect(urls[0]).toContain("/v1/sessions/child-1/messages");
    // The task is the brief above the steps, never a bubble of the reader's own.
    expect(screen.getByTestId("child-brief").textContent).toContain("Asked by the main agent");
    expect(screen.getByTestId("child-brief").textContent).toContain("Research repo subagents");
    expect(document.querySelector("[data-user-turn]")).toBeNull();
    const steps = screen.getByTestId("child-transcript");
    expect(steps.textContent).toContain("subagent_spec.py");
    expect(steps.textContent).toContain("children default to uncapped");
    expect(document.querySelector(".composer")).toBeNull();
    expect(screen.getByTestId("child-foot-note").textContent).toBe("Read only");
  });

  it("says who the child is and how it ended, in the head", async () => {
    stubFetch();
    renderPanel();
    const head = screen.getByTestId("child-head");
    expect(head.textContent).toContain("Research repo subagents");
    const facts = screen.getByTestId("child-facts").textContent ?? "";
    expect(facts).toContain("Explore");
    expect(facts).toContain("deepseek:deepseek-flash");
    expect(facts).toContain("Completed in 42.0s");
    expect(facts).toContain("3 steps");
    expect(screen.getByTestId("child-view").getAttribute("data-outcome")).toBe("completed");
    // A finished child offers nothing to do to it.
    expect(screen.queryByTestId("child-promote")).toBeNull();
    await waitFor(() => expect(screen.getByTestId("child-transcript")).toBeTruthy());
  });

  it("a child whose turn ended mid-batch says Finished, with its own measured time", async () => {
    // The settle frame arrives before the result, and a parallel batch records no result until
    // every call in it has finished: meanwhile the head says what it knows, and
    // nothing calls the child running.
    stubFetch();
    renderPanel({
      node: node({
        outcome: "settled",
        durationMs: 12_000,
        live: "↳ Finished its turn — waiting for the result",
      }),
    });
    const facts = screen.getByTestId("child-facts").textContent ?? "";
    expect(facts).toContain("Finished in 12.0s");
    expect(facts).not.toContain("Running");
    expect(facts).not.toContain("Completed");
    expect(screen.getByTestId("child-view").getAttribute("data-outcome")).toBe("settled");
    await waitFor(() => expect(screen.getByTestId("child-transcript")).toBeTruthy());
  });

  it("says why a child failed, under its facts", async () => {
    stubFetch();
    renderPanel({ node: node({ outcome: "failed", durationMs: 72_000, failure: "The model gave no answer after 3 tries." }) });
    expect(screen.getByTestId("child-facts").textContent).toContain("Failed after 1m 12s");
    expect(screen.getByTestId("child-failure").textContent).toBe("The model gave no answer after 3 tries.");
    await waitFor(() => expect(screen.getByTestId("child-transcript")).toBeTruthy());
  });

  it("offers Run in background only to a child that holds up the turn", async () => {
    stubFetch();
    const onPromote = vi.fn();
    const { rerender } = renderPanel({ busy: true, onPromote, node: node({ outcome: "running", durationMs: null, steps: null }) });
    fireEvent.click(screen.getByTestId("child-promote"));
    expect(onPromote).toHaveBeenCalledTimes(1);
    rerender(
      <SubagentPanel
        sessionId="child-1"
        title="Research repo subagents"
        busy
        onPromote={onPromote}
        node={node({ outcome: "running", background: true, durationMs: null, steps: null })}
      />,
    );
    expect(screen.queryByTestId("child-promote")).toBeNull();
    expect(screen.getByTestId("child-facts").textContent).toContain("in the background");
    await waitFor(() => expect(screen.getByTestId("child-transcript")).toBeTruthy());
  });

  it("names the last answer as the report once the child is done, and not while it runs", async () => {
    stubFetch();
    const { rerender } = renderPanel({ busy: true, node: node({ outcome: "running", durationMs: null }) });
    await waitFor(() => expect(screen.getByTestId("child-transcript")).toBeTruthy());
    expect(screen.queryByTestId("turn-answer-label")).toBeNull();
    rerender(<SubagentPanel sessionId="child-1" title="Research repo subagents" busy={false} node={node()} />);
    await waitFor(() => expect(screen.getByTestId("turn-answer-label").textContent).toContain("Its report"));
  });

  it("folds a long task to three lines and opens it in place", async () => {
    stubFetch(messages(LONG_TASK));
    renderPanel();
    await waitFor(() => expect(screen.getByTestId("child-brief")).toBeTruthy());
    const text = document.querySelector(".subagent-brief-text")!;
    expect(text.getAttribute("data-folded")).toBe("true");
    const toggle = screen.getByTestId("child-brief-toggle");
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    fireEvent.click(toggle);
    expect(text.getAttribute("data-folded")).toBe("false");
    expect(toggle.getAttribute("aria-expanded")).toBe("true");
    expect(toggle.textContent).toBe("Show less");
    // The whole task was in the page all along: folding hides, it does not cut.
    expect(text.textContent).toBe(LONG_TASK);
  });

  it("gives a short task no fold", async () => {
    stubFetch();
    renderPanel();
    await waitFor(() => expect(screen.getByTestId("child-brief")).toBeTruthy());
    expect(screen.queryByTestId("child-brief-toggle")).toBeNull();
  });

  it("says a child that has not taken a step is starting, and shows the shape of what is coming before that", async () => {
    let release: (value: unknown) => void = () => {};
    vi.stubGlobal(
      "fetch",
      vi.fn(() => new Promise((resolve) => (release = resolve))),
    );
    renderPanel({ busy: true, node: node({ outcome: "running", durationMs: null, steps: null }) });
    expect(screen.getByTestId("child-loading")).toBeTruthy();
    release({ json: async () => ({ messages: [{ role: "user", content: "Research repo subagents" }] }) });
    await waitFor(() => expect(screen.getByTestId("child-empty").textContent).toContain("has not taken a step yet"));
  });

  it("says a session could not be loaded where it would have been, and tries again", async () => {
    let fail = true;
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        if (fail) throw new Error("The app did not answer.");
        return { json: async () => ({ messages: messages() }) };
      }),
    );
    renderPanel();
    await waitFor(() => expect(screen.getByTestId("child-error").textContent).toContain("could not be loaded"));
    fail = false;
    fireEvent.click(screen.getByText("Try again"));
    await waitFor(() => expect(screen.getByTestId("child-transcript")).toBeTruthy());
  });

  it("leaves Esc to the chat", async () => {
    stubFetch();
    renderPanel();
    await waitFor(() => expect(screen.getByTestId("child-transcript")).toBeTruthy());
    const event = new KeyboardEvent("keydown", { key: "Escape", bubbles: true, cancelable: true });
    window.dispatchEvent(event);
    // The old view claimed the key to leave the child; here it is the chat's to stop a turn.
    expect(event.defaultPrevented).toBe(false);
  });

  it("leads back to the chat when it has taken the chat's place, and says the main agent still works", async () => {
    stubFetch();
    const onBack = vi.fn();
    renderPanel({ back: { onBack, mainRunning: true } });
    fireEvent.click(screen.getByTestId("child-back"));
    expect(onBack).toHaveBeenCalledTimes(1);
    expect(screen.getByText("The main agent is working")).toBeTruthy();
    await waitFor(() => expect(screen.getByTestId("child-transcript")).toBeTruthy());
  });

  it("names the workflow run an agent belongs to", async () => {
    stubFetch();
    renderPanel({ group: "file-review workflow", node: undefined, agent: "general", title: "shop/cart.py" });
    expect(screen.getByTestId("child-group").textContent).toBe("file-review workflow");
    // With no row in the chat, only what is known is said: no invented ending.
    expect(screen.getByTestId("child-view").getAttribute("data-outcome")).toBe("unknown");
    await waitFor(() => expect(screen.getByTestId("child-transcript")).toBeTruthy());
  });
});
