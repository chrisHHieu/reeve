import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useEffect } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { buildConnectorToolIndex, ConnectorToolsContext } from "../connectorTools";
import { itemsFromMessages } from "../itemsFromMessages";
import { AgentColorsContext } from "../subagents";
import { updateLastTool, updateToolMetadata } from "../toolRows";
import type { Item } from "../types";
import { Transcript } from "./Transcript";

afterEach(cleanup);

/** Whether `a` comes before `b` in the page. */
const before = (a: Element, b: Element) => !!(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING);

// A delegated call (`task`) folds into the DELEGATION CARD (DelegationCard): one row
// ("Delegated · 1 task … 1 of 1 finished") and, under it, one line per child. There is no
// parent tile and no diagram (owner, 2026-10-13). The line is the doorway into the child
// session — the whole line is the
// button — and the child id rides the result (`runRef.ts`) and the live `tool_metadata`
// frames, so a replayed row opens it too.
const DELEGATED: Item[] = [
  {
    kind: "tool",
    id: "t1",
    name: "task",
    args: {
      description: "Research repo subagents",
      prompt: "Research repo subagents",
      subagent_type: "explore",
    },
    status: "ok",
    preview: '{"run_id":"explore-2","status":"completed","steps":14,"report":"…"}',
    runId: "explore-2",
    stepCount: 14,
  } as Item,
];

describe("a delegated run's card", () => {
  it("summarizes the group and opens the child session from the node", () => {
    const onOpenChild = vi.fn();
    render(
      <Transcript items={DELEGATED} onApprove={vi.fn()} onOpenChild={onOpenChild} />,
    );
    // A settled turn groups its steps; expand it, then the card.
    expect(screen.getByTestId("delegation-metrics").textContent).toBe("1 of 1 finished");
    expect(screen.getByTestId("delegation-card-head").textContent).toContain("Delegated");
    expect(screen.getByTestId("delegation-card-head").textContent).toContain("1 task");
    fireEvent.click(screen.getByTestId("delegation-card-head"));
    const row = screen.getByTestId("task-row");
    expect(row.textContent).toContain("Explore");
    expect(row.textContent).toContain("Research repo subagents");
    // At rest the node speaks PI's vocabulary: `Completed · duration` beside `N steps`.
    expect(screen.getByTestId("task-status").textContent).toBe("Completed");
    expect(screen.getByTestId("task-steps").textContent).toBe("14 steps");
    expect(screen.queryByTestId("task-spinner")).toBeNull();
    // The reader is already in the parent: it is not drawn as a tile of its own.
    expect(screen.queryByText("Main agent")).toBeNull();
    expect(screen.getByTestId("delegation-card").querySelector(".delegation-root")).toBeNull();
    fireEvent.click(screen.getByTestId("task-row").querySelector(".delegation-node-header")!);
    expect(onOpenChild).toHaveBeenCalledWith({
      runId: "explore-2",
      title: "Research repo subagents",
    });
  });

  it("the node itself is the doorway, and nothing renders without an opener", () => {
    const onOpenChild = vi.fn();
    render(
      <Transcript items={DELEGATED} onApprove={vi.fn()} onOpenChild={onOpenChild} />,
    );
    fireEvent.click(screen.getByTestId("delegation-card-head"));
    fireEvent.click(screen.getByTestId("task-row").querySelector(".delegation-node-header")!);
    expect(onOpenChild).toHaveBeenCalledTimes(1);

    // A surface with nowhere to put the child view offers no affordance at all.
    cleanup();
    render(<Transcript items={DELEGATED} onApprove={vi.fn()} />);
    fireEvent.click(screen.getByTestId("delegation-card-head"));
    expect(screen.getByTestId("task-row").getAttribute("data-openable")).toBe("false");
  });
});

describe("a running delegation's live line", () => {
  const RUNNING: Item = {
    kind: "tool",
    id: "t1",
    name: "task",
    args: { description: "Research repo subagents", subagent_type: "explore" },
    status: "…",
    callId: "sub-1",
  };

  it("opens itself and shows ↳ <tool> <title> once a tool_metadata frame names the child's tool", () => {
    const items = updateToolMetadata([RUNNING], "sub-1", {
      session_id: "child-1",
      tool: "grep",
      title: "max_iterations",
      toolcalls: 3,
      phase: "start",
    });
    render(<Transcript items={items} onApprove={vi.fn()} />);
    // A live card renders open without a click — the work IS the show.
    expect(screen.getByTestId("task-spinner")).toBeTruthy();
    expect(screen.getByTestId("task-live").textContent).toBe("↳ grep max_iterations");
    expect(screen.getByTestId("task-status").textContent).toBe("Running");
    // The first frame's session id is what lets the node be opened mid-run.
    const onOpenChild = vi.fn();
    cleanup();
    render(<Transcript items={items} onApprove={vi.fn()} onOpenChild={onOpenChild} />);
    fireEvent.click(screen.getByTestId("task-row").querySelector(".delegation-node-header")!);
    expect(onOpenChild).toHaveBeenCalledWith({ runId: "child-1", title: "Research repo subagents" });
  });

  it("falls back to ↳ N steps when only the count has arrived", () => {
    const items = updateToolMetadata([RUNNING], "sub-1", {
      session_id: "child-1",
      toolcalls: 2,
    });
    render(<Transcript items={items} onApprove={vi.fn()} />);
    expect(screen.getByTestId("task-live").textContent).toBe("↳ 2 steps");
  });

  it("metadata never settles the row — the call's own finish still does", () => {
    const items = updateToolMetadata([RUNNING], "sub-1", { session_id: "child-1", toolcalls: 4 });
    expect(items[0].kind === "tool" && items[0].status).toBe("…");
  });
});

// P2: node detail. The manager's settle frame carries the child's elapsed (its last
// assistant stamp minus its first user stamp), the retry frame carries the going-again
// provider call, and a FAILED child keeps the failure's own text inside its node.
describe("a delegation node's detail (P2)", () => {
  const TASK: Item = {
    kind: "tool",
    id: "t1",
    name: "task",
    args: { description: "Research repo subagents", subagent_type: "explore" },
    status: "…",
    callId: "sub-1",
  };

  const settle = (items: Item[]): Item[] =>
    updateLastTool(items, { name: "task", status: "ok", callId: "sub-1" });

  it("settles to `Completed · duration` and `N steps` from the manager's measured frame", () => {
    const items = settle(
      updateToolMetadata([TASK], "sub-1", {
        session_id: "child-1",
        toolcalls: 3,
        duration_ms: 12_400,
      }),
    );
    render(<Transcript items={items} onApprove={vi.fn()} />);
    fireEvent.click(screen.getByTestId("delegation-card-head"));
    expect(screen.getByTestId("task-status").textContent).toBe("Completed · 12.4s");
    expect(screen.getByTestId("task-steps").textContent).toBe("3 steps");
  });

  it("speaks the reference design's Locale.duration: ms under a second, then minutes", () => {
    const render1 = settle(updateToolMetadata([TASK], "sub-1", { toolcalls: 1, duration_ms: 123 }));
    render(<Transcript items={render1} onApprove={vi.fn()} />);
    fireEvent.click(screen.getByTestId("delegation-card-head"));
    expect(screen.getByTestId("task-status").textContent).toBe("Completed · 123ms");

    cleanup();
    const minutes = settle(updateToolMetadata([TASK], "sub-1", { toolcalls: 4, duration_ms: 63_000 }));
    render(<Transcript items={minutes} onApprove={vi.fn()} />);
    fireEvent.click(screen.getByTestId("delegation-card-head"));
    expect(screen.getByTestId("task-status").textContent).toBe("Completed · 1m 3s");
    expect(screen.getByTestId("task-steps").textContent).toBe("4 steps");
  });

  it("falls back to the started/ended pair when no duration was measured", () => {
    const items = settle(
      updateToolMetadata([TASK], "sub-1", {
        toolcalls: 2,
        started_at: 1_000,
        ended_at: 1_012.5,
      }),
    );
    render(<Transcript items={items} onApprove={vi.fn()} />);
    fireEvent.click(screen.getByTestId("delegation-card-head"));
    expect(screen.getByTestId("task-status").textContent).toBe("Completed · 12.5s");
  });

  it("shows `↳ Retrying (attempt N) · message` while a provider call goes again", () => {
    const long = "x".repeat(120);
    const items = updateToolMetadata([TASK], "sub-1", {
      session_id: "child-1",
      retry: { attempt: 3, message: long },
    });
    render(<Transcript items={items} onApprove={vi.fn()} />);
    const line = screen.getByTestId("task-live").textContent ?? "";
    expect(line.startsWith("↳ Retrying (attempt 3) · ")).toBe(true);
    // The node column is narrow: the provider's message truncates, with the ellipsis.
    const shown = line.slice("↳ Retrying (attempt 3) · ".length);
    expect(shown.length).toBe(60);
    expect(shown.endsWith("…")).toBe(true);
  });

  it("a null retry frame clears the line once the request produced something", () => {
    let items = updateToolMetadata([TASK], "sub-1", { retry: { attempt: 1, message: "boom" } });
    items = updateToolMetadata(items, "sub-1", { retry: null, tool: "grep", title: "cache", toolcalls: 1 });
    expect(items[0].kind === "tool" && items[0].subagent?.retry).toBeUndefined();
    render(<Transcript items={items} onApprove={vi.fn()} />);
    expect(screen.getByTestId("task-live").textContent).toBe("↳ grep cache");
  });

  it("marks a detached spawn `(background)` from the metadata frame", () => {
    const items = updateToolMetadata([TASK], "sub-1", {
      session_id: "child-1",
      toolcalls: 0,
      background: true,
      model: "gpt-5.5",
      job_id: "child-1",
    });
    render(<Transcript items={items} onApprove={vi.fn()} />);
    expect(screen.getByTestId("task-background").textContent).toBe("(background)");
    // The part metadata the GUI does not draw yet is still carried on the row.
    expect(items[0].kind === "tool" && items[0].subagent?.model).toBe("gpt-5.5");
    expect(items[0].kind === "tool" && items[0].subagent?.jobId).toBe("child-1");
  });

  it("draws the agent's declared color, keeping the capitalized name", () => {
    render(
      <AgentColorsContext.Provider value={{ explore: "#ff00ff" }}>
        <Transcript items={[TASK]} onApprove={vi.fn()} />
      </AgentColorsContext.Provider>,
    );
    const title = document.querySelector(".delegation-node-title") as HTMLElement;
    expect(title.textContent).toBe("Explore");
    expect(title.style.color).toBe("rgb(255, 0, 255)");
  });

  it("a failed child shows Failed beside the node and the failure's own text", () => {
    const onOpenChild = vi.fn();
    const failed: Item[] = [
      {
        kind: "tool",
        id: "t1",
        name: "task",
        args: { description: "Research repo subagents", subagent_type: "explore" },
        status: "error",
        callId: "sub-1",
        runId: "child-1",
        preview: '{"error":"Subagent failed (task_id: child-1): the provider exploded"}',
      } as Item,
    ];
    const { container } = render(
      <Transcript items={failed} onApprove={vi.fn()} onOpenChild={onOpenChild} />,
    );
    // A failed child is FINISHED (1 of 1): the count beside it is what carries the problem,
    // and the head's tooltip keeps the old sentence for a pointer that rests on it.
    expect(screen.getByTestId("delegation-metrics").textContent).toBe("1 of 1 finished · 1 failed");
    expect(container.querySelector(".delegation-card-label")!.textContent).toBe("Delegated");
    expect(screen.getByTestId("delegation-card-head").getAttribute("title")).toBe("Subagent finished with issues");
    fireEvent.click(screen.getByTestId("delegation-card-head"));
    expect(screen.getByTestId("task-status").textContent).toBe("Failed");
    expect(screen.getByTestId("task-error-body").textContent).toContain("the provider exploded");
    // The node stays the doorway even when the child failed.
    fireEvent.click(screen.getByTestId("task-row").querySelector(".delegation-node-header")!);
    expect(onOpenChild).toHaveBeenCalledWith({
      runId: "child-1",
      title: "Research repo subagents",
    });
  });
});

// A turn: what the agent said along the way is narration among its steps, what it said
// last is the answer after them, each step is one line in words, and an approval is a chip on
// the row of the call it let through.
const TURN: Item[] = [
  { kind: "user", text: "post the digest" },
  { kind: "assistant", text: "Checking what merged since yesterday." },
  { kind: "tool", id: "t1", name: "read_file", looks: true, args: { path: "docs/runbook.md" }, status: "ok" },
  { kind: "approval", name: "send_message", args: { target: "slack:T1/C9" }, reason: "", resolved: "once" },
  { kind: "tool", id: "t2", name: "send_message", args: { target: "slack:T1/C9", text: "hi" }, status: "ok", preview: '{"ok": true}' },
  { kind: "assistant", text: "Posted to #all-reeve." },
];

describe("TurnGroup", () => {
  it("groups the whole turn; answer stays outside; narration and humanized steps inside", () => {
    const { container } = render(<Transcript items={TURN} onApprove={vi.fn()} />);

    // At rest the turn stays as it was read — no fold (owner, 2026-09-28): the steps are on
    // screen. Settled, the turn has no line of its own (owner, 2026-10-13): no status
    // line, no step count, and no approval count.
    expect(container.querySelector("details")).toBeNull();
    expect(screen.queryByTestId("turn-status")).toBeNull();
    expect(container.textContent).not.toMatch(/Worked for|approval/);
    // The narration is a step of the turn, so it comes before the answer.
    expect(before(screen.getByTestId("turn-narration"), screen.getByText("Posted to #all-reeve."))).toBe(true);

    // Narration renders inside; steps are English lines, not raw args; the approval is a
    // chip on the send_message row, not a separate box.
    expect(screen.getByTestId("turn-narration").textContent).toContain("Checking what merged");
    // Every step is one line: the family word plus its summary, and the reader's OWN
    // approval decision rides the row it governed (owner report 2026-09-18: the choice
    // used to be invisible, recorded only in a details panel).
    expect(screen.getByText(/runbook\.md/)).toBeTruthy();
    expect(screen.getByText(/Sent a Slack message to/)).toBeTruthy();
    expect(screen.queryByText("send_message approval")).toBeNull();
    expect(screen.getByText("✓ approved")).toBeTruthy();

    // The arguments and the result live in the row's OWN expanded body (owner 2026-09-19:
    // the Inspect pill and its side panel are gone); the IN tree is visible inline, deeper
    // OUT nodes stay behind the tree's own twisties. A built-in tool's call reads as
    // labelled lines, not JSON (owner rule 2026-09-28: only MCP payloads keep the tree).
    // The disclosure target is the row LINE, not the wrapper: a click on the wrapper is a
    // click with no handler, and testing that would prove nothing.
    fireEvent.click(document.querySelectorAll("[data-disclosure-row]")[1]);
    const io = Array.from(document.querySelectorAll(".tool-io"))
      .map((el) => el.textContent || "")
      .join("\n");
    expect(io).toContain("slack:T1/C9");
    expect(document.querySelector('[data-testid="tool-io-fields"]')).toBeTruthy();
    expect(io).not.toContain("{");
  });

  it("a steered message stays INSIDE the running turn — it does not split the group", () => {
    const items: Item[] = [
      { kind: "user", text: "xem tiếp continuous agent loop đi" },
      { kind: "tool", id: "t1", name: "read_file", looks: true, args: { path: "a.md" }, status: "ok" },
      { kind: "steer", text: "tôi là ai?" },
      { kind: "tool", id: "t2", name: "web_search", args: { query: "x" }, status: "ok" },
      { kind: "assistant", text: "Anh là Nguyễn Hoàng Hiếu." },
    ];
    const { container } = render(<Transcript items={items} onApprove={vi.fn()} />);
    // ONE disclosure for the whole turn: a user-kind item here used to break the run and
    // render a second "Working…" block right where the steer was read.
    expect(container.querySelectorAll(".stepgroup")).toHaveLength(1);
    // The note lives inside the group, marked "steered", and is NOT counted as a step.
    const note = screen.getByTestId("turn-steer");
    expect(note.textContent).toContain("tôi là ai?");
    expect(note.textContent).toContain("steered");
    // The final answer still renders as its own bubble outside the disclosure.
    expect(screen.getByText("Anh là Nguyễn Hoàng Hiếu.")).toBeTruthy();
  });

  it("in a subagent's session, a message from the agent that started it says so", () => {
    const items: Item[] = [
      { kind: "user", text: "survey the repo" },
      { kind: "tool", id: "t1", name: "list_files", looks: true, args: { path: "." }, status: "ok" },
      { kind: "steer", text: "include the cache module", from: "agent" },
      { kind: "assistant", text: "done" },
    ];
    render(<Transcript items={items} onApprove={vi.fn()} />);
    const note = screen.getByTestId("turn-steer");
    expect(note.getAttribute("data-from")).toBe("agent");
    expect(note.textContent).toContain("main agent");
    expect(note.textContent).not.toContain("steered");
    expect(note.textContent).toContain("include the cache module");
  });

  it("resolved asks stay inside the turn too — answering a question does not split it", () => {
    const items: Item[] = [
      { kind: "user", text: "dọn dẹp repo" },
      { kind: "tool", id: "t1", name: "grep", looks: true, args: { pattern: "TODO" }, status: "ok" },
      { kind: "question", question: "Xoá luôn file cũ?", resolved: "Có" },
      { kind: "tool", id: "t2", name: "ask_user", args: {}, status: "ok" },
      { kind: "toolreq", tool: "rg", reason: "thiếu rg", resolved: "installed" },
      { kind: "tool", id: "t3", name: "read_file", looks: true, args: { path: "~/notes/a.md" }, status: "ok" },
      { kind: "assistant", text: "Xong." },
    ];
    const { container } = render(<Transcript items={items} onApprove={vi.fn()} />);
    // ONE group for the whole turn — a resolved ask/question used to flush() into an
    // empty block and re-open the clock for the rest of the work.
    expect(container.querySelectorAll(".stepgroup")).toHaveLength(1);
    expect(screen.getByText("Xong.")).toBeTruthy();
  });

  it("pending asks during a running turn do not break the live group", () => {
    const items: Item[] = [
      { kind: "assistant", text: "Đang hỏi bạn." },
      { kind: "tool", id: "t1", name: "ask_user", args: {}, status: "…" },
      { kind: "question", question: "Chọn A hay B?" },
      { kind: "toolreq", tool: "rg", reason: "thiếu rg", installable: true },
    ];
    const { container } = render(<Transcript items={items} onApprove={vi.fn()} />);
    // The asks must not open a second group: exactly ONE live clock on screen, no empty
    // blocks behind the cards.
    expect(container.querySelectorAll(".stepgroup")).toHaveLength(1);
    expect(screen.getAllByTestId("turn-status-label")).toHaveLength(1);
  });

  it("a provider retry line stays inside the turn — a hiccup is not a new turn", () => {
    const items: Item[] = [
      { kind: "user", text: "go" },
      { kind: "tool", id: "t1", name: "grep", looks: true, args: { pattern: "x" }, status: "ok" },
      { kind: "notice", tone: "info", text: "Rate limited — retrying (1/4).", inline: true },
      { kind: "tool", id: "t2", name: "read_file", looks: true, args: { path: "a.md" }, status: "ok" },
      { kind: "assistant", text: "Xong." },
    ];
    const { container } = render(<Transcript items={items} onApprove={vi.fn()} />);
    // ONE group: the stream dying and the same request going again is a fact about THIS
    // step, not a boundary that re-opens the clock.
    expect(container.querySelectorAll(".stepgroup")).toHaveLength(1);
    expect(screen.getByText(/retrying \(1\/4\)/)).toBeTruthy();
  });

  it("a folder grant's project line, replayed, stays inside the turn instead of splitting it", () => {
    const items = itemsFromMessages([
      { role: "user", content: "cập nhật CLAUDE.md" },
      {
        role: "assistant",
        content: "Xin quyền root trước:",
        tool_calls: [{ id: "c1", function: { name: "request_directory", arguments: '{"path": "D:/w"}' } }],
      },
      { role: "tool", tool_call_id: "c1", content: "granted: D:\\w (writable)" },
      { role: "notice", kind: "project_presence", text: "“w” already has project memory (2 entries)." },
      {
        role: "assistant",
        content: "Cập nhật CLAUDE.md:",
        tool_calls: [{ id: "c2", function: { name: "read_file", arguments: '{"path": "CLAUDE.md"}' } }],
      },
      { role: "tool", tool_call_id: "c2", content: "ok" },
      { role: "assistant", content: "Xong." },
    ] as any);
    const { container } = render(<Transcript items={items} onApprove={vi.fn()} />);
    // ONE group ("Worked for …" once), the line a step inside it — not a centred banner
    // between a finished-looking half and a second group.
    expect(container.querySelectorAll(".stepgroup")).toHaveLength(1);
    expect(container.querySelector(".stepgroup [data-testid='turn-note']")?.textContent).toContain(
      "already has project memory",
    );
  });

  it("a thought-then-answer turn draws the Thought step and no empty narration dot", () => {
    const items: Item[] = [
      { kind: "user", text: "có MCP không?" },
      { kind: "assistant", text: "Có, session này đang kết nối MCP.", reasoning: "The user asks…" },
    ];
    const { container } = render(<Transcript items={items} onApprove={vi.fn()} />);
    expect(container.querySelectorAll(".stepgroup .think-row, .stepgroup [data-testid='think-row']").length).toBeGreaterThan(0);
    // The trace moved into the turn carries no prose: nothing may hang a dot on the rail.
    expect(container.querySelectorAll(".stepgroup .turn-narr")).toHaveLength(0);
    expect(screen.getByText("Có, session này đang kết nối MCP.")).toBeTruthy();
  });

  it("an answered question leaves a Q&A note inside the turn", () => {
    const items: Item[] = [
      { kind: "user", text: "vẽ luồng bài toán" },
      { kind: "tool", id: "t1", name: "read_file", looks: true, args: { path: "a.md" }, status: "ok" },
      { kind: "question", question: "Bạn muốn mình vẽ màu nào?", resolved: "tiếp tục" },
      { kind: "tool", id: "t2", name: "write_file", args: { path: "x.html" }, status: "ok" },
      { kind: "assistant", text: "Xong." },
    ];
    const { container } = render(<Transcript items={items} onApprove={vi.fn()} />);
    // ONE group: the answer used to vanish with the card, leaving no trace of the ask.
    expect(container.querySelectorAll(".stepgroup")).toHaveLength(1);
    const qa = screen.getByTestId("turn-qa");
    expect(qa.textContent).toContain("Bạn muốn mình vẽ màu nào?");
    expect(qa.textContent).toContain("tiếp tục");
    // The pending card still renders in the composer head, never in the transcript.
    expect(screen.queryByTestId("turn-qa-pending")).toBeNull();
  });

  it("a resolved ask leaves the human's decision as a note", () => {
    const items: Item[] = [
      { kind: "user", text: "làm đi" },
      { kind: "planreq", plan: "step 1", resolved: "approved" },
      { kind: "toolreq", tool: "rg", reason: "missing", resolved: "installed" },
      { kind: "tool", id: "t1", name: "run_shell", args: { command: "ls" }, status: "ok" },
      { kind: "assistant", text: "Xong." },
    ];
    const { container } = render(<Transcript items={items} onApprove={vi.fn()} />);
    // ONE group; the decisions are notes, not steps (only run_shell counts).
    expect(container.querySelectorAll(".stepgroup")).toHaveLength(1);
    const notes = screen.getAllByTestId("turn-decision").map((n) => n.textContent ?? "");
    const joined = notes.join(" | ");
    expect(joined).toContain("Plan approved");
    expect(joined).toContain("Tool installed");
  });

  it("a running turn renders flat under an activity line — no disclosure to collapse", () => {
    const items: Item[] = [
      { kind: "assistant", text: "Looking at the repo." },
      { kind: "tool", id: "t1", name: "grep", looks: true, args: { pattern: "TODO" }, status: "…" },
    ];
    const { container } = render(<Transcript items={items} onApprove={vi.fn()} />);
    // The running header names the current activity, not a flat "Working".
    expect(screen.getByTestId("turn-status-label").textContent).toBe("Exploring");
    expect(screen.getByTestId("turn-status-clock").textContent).toMatch(/^\d+s$/);
    // The steps ARE the show: narration and the live step render flat, nothing to toggle.
    expect(screen.getByTestId("turn-narration").textContent).toContain("Looking at the repo");
    // The in-flight signal is the shimmer across the row's TITLE
    // plus its state — there is no per-row spinner, and no dot, because a working row must
    // not look like a finished one.
    expect(document.querySelector(".tool-row")!.getAttribute("data-state")).toBe("running");
    expect(document.querySelector(".tool-row-title")!.className).toContain("shimmer-text");
    // The old coloured glare that swept the whole row is gone.
    expect(container.querySelector(".step-sweep")).toBeNull();
    expect(container.querySelector("details")).toBeNull();
    expect(screen.queryByTestId("turn-live-line")).toBeNull();
    // The live status sits UNDER the steps, at the edge where the next one will land
    // — it used to head the group.
    const group = container.querySelector(".stepgroup")!;
    expect(group.lastElementChild).toBe(screen.getByTestId("turn-status"));
  });

  it("a settled tool row's title does not shimmer", () => {
    const items: Item[] = [
      { kind: "tool", id: "t1", name: "grep", looks: true, args: { pattern: "TODO" }, status: "ok" },
    ];
    const { container } = render(<Transcript items={items} onApprove={vi.fn()} />);
    // A settled turn groups its steps; expand it to reach the row.
    expect(document.querySelector(".tool-row")!.getAttribute("data-state")).toBe("ok");
    expect(document.querySelector(".tool-row-title")!.className).not.toContain("shimmer-text");
    expect(container.querySelector(".step-sweep")).toBeNull();
  });

  it("a running row's clock counts seconds, not the seconds-as-milliseconds artifact", () => {
    // `ts` is unix SECONDS (the model's unit); `Elapsed` counts from epoch MILLISECONDS.
    // Passing seconds straight through read as "29795733m 57s" (owner screenshot 2026-09-16).
    // A plain tool row stands in here: the `task` card has its own clockless anatomy, and
    // this boundary lives in ToolCallRow for every row.
    const now = Date.now() / 1000;
    render(
      <Transcript
        items={[
          {
            kind: "tool",
            id: "t1",
            name: "grep",
            args: { pattern: "max_iterations" },
            status: "…",
            ts: now - 5,
          },
        ]}
        onApprove={vi.fn()}
      />,
    );
    const clock = screen.getByTestId("elapsed").textContent || "";
    expect(clock).not.toContain("m");
    expect(Number(clock.replace("s", ""))).toBeLessThan(60);
  });

  it("the running header times from the turn's first stamp, not the render moment", () => {
    const now = Date.now() / 1000;
    const items: Item[] = [
      { kind: "assistant", text: "Looking at the repo.", ts: now - 25 },
      { kind: "tool", id: "t1", name: "grep", looks: true, args: { pattern: "TODO" }, status: "…", ts: now - 20 },
    ];
    render(<Transcript items={items} onApprove={vi.fn()} />);
    expect(screen.getByTestId("turn-status-label").textContent).toBe("Exploring");
    expect(screen.getByTestId("turn-status-clock").textContent).toBe("25s");
  });

  it("the working clock shows the instant a turn runs, before any activity lands", () => {
    const items: Item[] = [{ kind: "user", text: "go" }];
    render(<Transcript items={items} onApprove={vi.fn()} running />);
    expect(screen.getByTestId("turn-status-label").textContent).toBe("Working");
    expect(screen.getByTestId("turn-status-clock").textContent).toMatch(/^\d+s$/);
    // And it renders for a session with NO items at all — that is the "hit send and wait"
    // moment, and the whole reason the clock is not tied to the first landed token.
    cleanup();
    render(<Transcript items={[]} onApprove={vi.fn()} running />);
    expect(screen.getByTestId("turn-status-label").textContent).toBe("Working");
  });

  it("every trace item is a settled Thought row — the live trace is the status line's", () => {
    // A trace becomes an item only when its message is complete, so an item is never the
    // live edge. The last one used to be read as live ("Think", running) just for being
    // last, which put a second "Thinking" on screen while the model waited on a tool
    // (owner report 2026-09-28). The trace being written arrives as `liveThought` and
    // rides the status line.
    const items: Item[] = [
      { kind: "assistant", text: "", reasoning: "reading the provider first", ts: 100 },
      { kind: "tool", id: "t1", name: "read_file", looks: true, args: { path: "/x/a.py" }, status: "ok", ts: 104 },
      { kind: "assistant", text: "", reasoning: "the fix is a whitelist entry", ts: 110 },
    ];
    render(
      <Transcript items={items} onApprove={vi.fn()} running liveThought="now checking the tests" />,
    );
    const rows = screen.getAllByTestId("think-row");
    expect(rows).toHaveLength(2);
    for (const row of rows) {
      expect(row.getAttribute("data-state")).toBe("ok");
      expect(row.textContent).toContain("Thought");
    }
    expect(rows[1].textContent).toContain("whitelist entry");
    expect(screen.getByTestId("turn-status-thought").textContent).toBe("now checking the tests");
    // A settled trace spills nothing: the paragraph waits behind the click.
    fireEvent.click(document.querySelectorAll("[data-disclosure-row]")[0]);
    expect(screen.getAllByTestId("think-body")[0].textContent).toContain(
      "reading the provider first",
    );
  });

  it("the status line wears three dots while running and is gone once done", () => {
    const tool: Item = { kind: "tool", id: "t1", name: "grep", looks: true, args: {}, status: "ok", ts: 100 };
    const { container, rerender } = render(
      <Transcript items={[{ kind: "user", text: "go" }, tool]} onApprove={vi.fn()} running />,
    );
    expect(container.querySelectorAll(".turn-status .turn-dots > i")).toHaveLength(3);
    expect(container.querySelector(".turn-status .turn-done")).toBeNull();
    expect(container.querySelector(".turn-burst")).toBeNull(); // the spinning ring is gone
    rerender(<Transcript items={[{ kind: "user", text: "go" }, tool]} onApprove={vi.fn()} />);
    expect(container.querySelector(".turn-status")).toBeNull();
    expect(screen.queryByTestId("turn-status-thought")).toBeNull();
  });

  it("a settled trace carries its own clock, measured from the event before it", () => {
    // Reversal, owner ask 2026-09-11 (matching the rows): the row USED to carry no
    // clock on the grounds that the turn header already answers "how long did this take".
    // The owner overruled it — a thought's own duration is the number a person waits on,
    // and the turn's total does not answer it. The span is the gap between the previous
    // event's stamp and this block's own: the tool ended at 104, this trace landed at 110.
    const items: Item[] = [
      { kind: "assistant", text: "one", reasoning: "first", ts: 100 },
      { kind: "tool", id: "t1", name: "read_file", looks: true, args: { path: "/x/a.py" }, status: "ok", ts: 104 },
      { kind: "assistant", text: "two", reasoning: "second", ts: 110 },
    ];
    render(<Transcript items={items} onApprove={vi.fn()} running />);
    const rows = screen.getAllByTestId("think-row");
    expect(rows[1].textContent).toContain("Thought · 6s");
    // The first trace has nothing before it in the fixture, so it has no span to report —
    // a row without a measurable interval says "Thought" and invents nothing.
    expect(rows[0].textContent).toContain("Thought");
    expect(rows[0].textContent).not.toContain("·");
  });

  it("keeps a declined call as a 'Wanted to' row of its own, and counts it on the group's line", () => {
    render(
      <Transcript
        items={[
          { kind: "tool", id: "t1", name: "read_file", looks: true, args: { path: "a.md" }, status: "ok" },
          { kind: "approval", name: "run_shell", args: { command: "rm -rf build/" }, reason: "", resolved: "deny" },
        ]}
        onApprove={vi.fn()}
      />,
    );
    expect(screen.getByTestId("stepgroup-declined").textContent).toBe("1 declined");
    const ask = screen.getByTestId("turn-ask").textContent;
    for (const said of ["Wanted to run", "rm -rf build/", "✕ declined"]) expect(ask).toContain(said);
  });

  it("draws a turn that only answered as bubbles, with no group", () => {
    const { container } = render(
      <Transcript
        items={[
          { kind: "user", text: "hi" },
          { kind: "assistant", text: "Hello there." },
        ]}
        onApprove={vi.fn()}
      />,
    );
    expect(container.querySelector(".stepgroup")).toBeNull();
    expect(screen.getByText("Hello there.")).toBeTruthy();
  });

  it("consecutive read-only steps fold into one Explore group, and the count stays real", () => {
    const items: Item[] = [
      { kind: "user", text: "look around" },
      { kind: "tool", id: "t1", name: "grep", looks: true, args: { pattern: "x" }, status: "ok" },
      { kind: "tool", id: "t2", name: "read_file", looks: true, args: { path: "a.md" }, status: "ok" },
      { kind: "tool", id: "t3", name: "list_files", looks: true, args: { path: "." }, status: "ok" },
      { kind: "assistant", text: "Found it." },
    ];
    const { container } = render(<Transcript items={items} onApprove={vi.fn()} />);
    // A run of read-only calls folds under one Explore group,
    // collapsed by default; the children appear only when the header is opened.
    const group = screen.getByTestId("explore-group");
    expect(group.getAttribute("data-state")).toBe("ok");
    expect(group.textContent).toContain("Explored");
    // The line names what was read, and counts the other kinds of looking.
    expect(group.textContent).toContain("a.md");
    expect(group.textContent).toContain("1 search");
    expect(group.textContent).toContain("1 list");
    expect(screen.queryAllByTestId("turn-step")).toHaveLength(0);
    fireEvent.click(group.querySelector(".tool-row-line")!);
    expect(screen.getAllByTestId("turn-step")).toHaveLength(3);
    expect(container.textContent).toContain("Found it.");
  });

  it("what folds is the server's verdict on the call, not the tool's name", () => {
    // `git status` through the shell only looked, so it joins the run; a read the server did not
    // call a look stays a row of its own.
    const items: Item[] = [
      { kind: "user", text: "where are we" },
      { kind: "tool", id: "t1", name: "run_shell", looks: true, args: { command: "git status" }, status: "ok" },
      { kind: "tool", id: "t2", name: "read_file", looks: true, args: { path: "a.md" }, status: "ok" },
      { kind: "tool", id: "t3", name: "read_file", args: { path: "b.md" }, status: "ok" },
      { kind: "assistant", text: "Clean tree." },
    ];
    render(<Transcript items={items} onApprove={vi.fn()} />);
    const group = screen.getByTestId("explore-group");
    expect(group.textContent).toContain("a.md");
    expect(group.textContent).not.toContain("b.md");
    expect(screen.getAllByTestId("turn-step")).toHaveLength(1);
  });

  it("same-server mcp calls fold into one group; a lone call stays a row", () => {
    const items: Item[] = [
      { kind: "user", text: "money check" },
      { kind: "tool", id: "t1", name: "mcp__wealthlog__get_goals", args: {}, status: "ok" },
      { kind: "tool", id: "t2", name: "mcp__wealthlog__get_portfolio", args: {}, status: "ok" },
      { kind: "tool", id: "t3", name: "run_shell", args: { command: "ls" }, status: "ok" },
      { kind: "tool", id: "t4", name: "mcp__wealthlog__get_bills", args: {}, status: "ok" },
      { kind: "assistant", text: "Done." },
    ];
    const { container } = render(<Transcript items={items} onApprove={vi.fn()} />);
    // get_goals + get_portfolio fold into one MCP group (collapsed by default); the shell and
    // the lone get_bills (a run of one) stay normal rows.
    const group = screen.getByTestId("mcp-group");
    expect(group.textContent).toContain("Wealthlog");
    expect(group.textContent).not.toMatch(/Call(ed|ing)/);
    expect(group.textContent).toContain("MCP");
    expect(group.textContent).toContain("get_goals");
    expect(group.textContent).toContain("get_portfolio");
    expect(screen.getAllByTestId("turn-step")).toHaveLength(2); // shell + lone get_bills
    expect(document.querySelectorAll('[data-testid="turn-step"] [data-testid="tool-what"]')).toHaveLength(1);
    // Opening the group reveals the two folded calls.
    fireEvent.click(group.querySelector(".tool-row-line")!);
    expect(screen.getAllByTestId("turn-step")).toHaveLength(4);
    expect(container.textContent).toContain("Done.");
  });

  it("folds a shell's detached-run lifecycle under one Ran header", () => {
    const items: Item[] = [
      { kind: "user", text: "run it" },
      { kind: "tool", id: "t1", name: "run_shell", args: { command: "build.sh" }, status: "ok" },
      { kind: "tool", id: "t2", name: "shell_task_output", args: {}, status: "ok" },
      { kind: "tool", id: "t3", name: "shell_task_kill", args: {}, status: "ok" },
      { kind: "assistant", text: "Built." },
    ];
    render(<Transcript items={items} onApprove={vi.fn()} />);
    const group = screen.getByTestId("family-group");
    expect(group.getAttribute("data-family")).toBe("shell");
    expect(group.textContent).toContain("Ran");
    expect(group.textContent).toContain("3 calls");
    // Collapsed: the three calls are hidden behind the header.
    expect(screen.queryAllByTestId("turn-step")).toHaveLength(0);
    fireEvent.click(group.querySelector(".tool-row-line")!);
    expect(screen.getAllByTestId("turn-step")).toHaveLength(3);
  });
});

// The stream's in-flight step (LiveStep) is not an item yet; the caller hands it over as
// `liveTail`. Owner report 2026-09-28: drawn after the transcript, it sat BELOW the running
// turn's status line, so "Thinking for 51s" stood above the trace it was timing.
describe("the live tail", () => {
  const Tail = ({ onMount }: { onMount?: () => void }) => {
    useEffect(() => onMount?.(), [onMount]);
    return <div data-testid="live-tail">thinking…</div>;
  };

  it("renders inside the running turn, between its steps and its status line", () => {
    const items: Item[] = [
      { kind: "user", text: "go" },
      { kind: "tool", id: "t1", name: "grep", looks: true, args: { pattern: "x" }, status: "ok" },
    ];
    const { container } = render(
      <Transcript items={items} running onApprove={vi.fn()} liveTail={<Tail />} />,
    );
    const group = container.querySelector(".stepgroup")!;
    const tail = screen.getByTestId("live-tail");
    expect(tail.closest(".stepgroup")).toBe(group);
    expect(group.lastElementChild).toBe(screen.getByTestId("turn-status"));
    expect(before(container.querySelector(".tool-row")!, tail)).toBe(true);
  });

  it("rides the early clock before any item, and is not remounted when the first lands", () => {
    const onMount = vi.fn();
    const tail = <Tail onMount={onMount} />;
    const user: Item = { kind: "user", text: "go" };
    const { container, rerender } = render(
      <Transcript items={[user]} running onApprove={vi.fn()} liveTail={tail} />,
    );
    expect(screen.getByTestId("live-tail").closest(".stepgroup")).toBeTruthy();
    expect(container.querySelector(".stepgroup")!.lastElementChild).toBe(screen.getByTestId("turn-status"));

    rerender(
      <Transcript
        items={[user, { kind: "tool", id: "t1", name: "grep", looks: true, args: {}, status: "…" }]}
        running
        onApprove={vi.fn()}
        liveTail={tail}
      />,
    );
    expect(container.querySelectorAll(".stepgroup")).toHaveLength(1);
    expect(screen.getByTestId("live-tail").closest(".stepgroup")).toBeTruthy();
    // One mount: a remount would reset a trace's clock mid-thought.
    expect(onMount).toHaveBeenCalledTimes(1);
  });

  it("puts a running turn's answer in its answer slot: above the status, off the timeline", () => {
    // Owner report 2026-09-28 (the Kilo Code turn): the sentence peeled off as the answer sat
    // BELOW "Thinking for 19s". Owner, 2026-09-30: the answer is not a step on the timeline
    // either. It sits in the group's answer slot, between the steps and the status line.
    const items: Item[] = [
      { kind: "user", text: "go on to kilo code" },
      { kind: "assistant", text: "", reasoning: "Let me do it." },
      { kind: "assistant", text: "Kilo Code is not in the survey corpus." },
    ];
    render(<Transcript items={items} running onApprove={vi.fn()} />);
    const text = screen.getByText("Kilo Code is not in the survey corpus.");
    const status = screen.getByTestId("turn-status");
    expect(text.closest(".stepgroup")).toBe(status.closest(".stepgroup"));
    expect(text.closest(".turn-answer")).toBeTruthy();
    expect(text.closest(".turn-rail")).toBeNull();
    expect(before(text, status)).toBe(true);
  });

  it("streams the answer half off the timeline, above the status line", () => {
    const items: Item[] = [
      { kind: "user", text: "go" },
      { kind: "tool", id: "t1", name: "grep", looks: true, args: { pattern: "x" }, status: "ok" },
    ];
    render(
      <Transcript
        items={items}
        running
        onApprove={vi.fn()}
        liveTail={<Tail />}
        liveAnswer={<div data-testid="live-answer-probe">the answer so far</div>}
      />,
    );
    const answer = screen.getByTestId("live-answer-probe");
    expect(answer.closest(".turn-answer")).toBeTruthy();
    expect(answer.closest(".turn-rail")).toBeNull();
    expect(screen.getByTestId("live-tail").closest(".turn-rail")).toBeTruthy();
    expect(before(answer, screen.getByTestId("turn-status"))).toBe(true);
  });

  it("goes last when no turn is running", () => {
    const { container } = render(
      <Transcript items={[{ kind: "user", text: "hi" }]} onApprove={vi.fn()} liveTail={<Tail />} />,
    );
    expect(container.querySelector(".stepgroup")).toBeNull();
    expect(container.querySelector(".transcript")!.lastElementChild).toBe(screen.getByTestId("live-tail"));
  });
});

describe("a turn as it ends", () => {
  const LIVE: Item[] = [
    { kind: "user", text: "build the app" },
    { kind: "tool", id: "t1", name: "read_file", looks: true, args: { path: "data.json" }, status: "ok" },
    { kind: "assistant", text: "Inspecting the fetched dataset next." },
  ];

  it("leaves the answer where it streamed: the same element, not drawn again", () => {
    // Owner, 2026-09-30: as a turn ended the page seemed to reload once. The answer was taken out
    // of the group into a new bubble, which played its entry again and swapped places with the
    // status line.
    const { container, rerender } = render(<Transcript items={LIVE} onApprove={vi.fn()} running />);
    const streaming = container.querySelector(".turn-answer .bubble-assistant")!;
    expect(streaming.textContent).toContain("Inspecting the fetched dataset");
    expect(streaming.className).not.toContain("reveal-up");
    rerender(<Transcript items={LIVE} onApprove={vi.fn()} />);
    const settled = container.querySelector(".turn-answer .bubble-assistant")!;
    expect(settled).toBe(streaming);
    // The status line goes; the answer's footer says how long it took (2026-10-13).
    expect(screen.queryByTestId("turn-status")).toBeNull();
    expect(before(container.querySelector(".turn-rail")!, settled)).toBe(true);
  });

  it("keeps a message that also called tools as narration, never in the answer's place", () => {
    // Its words lead into its calls; they must not show as the answer for the moment before the
    // calls arrive.
    const { container } = render(<Transcript items={[...LIVE.slice(0, 2), { kind: "assistant", text: "Inspecting the fetched dataset next.", withCalls: true }]} onApprove={vi.fn()} running />);
    expect(container.querySelector(".turn-answer")).toBeNull();
    const narration = screen.getByTestId("turn-narration");
    expect(narration.textContent).toContain("Inspecting the fetched dataset");
    expect(narration.closest(".turn-rail")).toBeTruthy();
  });

  it("does not split the turn or make an answer of the narration while an approval waits", () => {
    const items: Item[] = [
      ...LIVE.slice(0, 2),
      { kind: "assistant", text: "Writing the page now.", withCalls: true },
      { kind: "approval", name: "write_file", args: { path: "app.html" }, reason: "" },
    ];
    const { container } = render(<Transcript items={items} onApprove={vi.fn()} running />);
    expect(container.querySelectorAll(".stepgroup")).toHaveLength(1);
    expect(container.querySelector(".bubble-assistant")).toBeNull();
  });

  it("streams a reply with no steps as a plain bubble", () => {
    const { container } = render(
      <Transcript
        items={[
          { kind: "user", text: "hi" },
          { kind: "assistant", text: "Hello!" },
        ]}
        onApprove={vi.fn()}
        running
      />,
    );
    expect(container.querySelector("details")).toBeNull();
    expect(container.querySelector(".bubble-assistant")?.textContent).toContain("Hello!");
  });
});

describe("a bubble's actions", () => {
  const TS = 1752969720; // unix seconds, as the server stamps them
  const ITEMS: Item[] = [
    { kind: "user", text: "post the digest", ts: TS },
    // From before the server stamped messages: no ts.
    { kind: "assistant", text: "Done — posted to #all-reeve." },
  ];

  it("copies the bubble's own text, and says Copied once the clipboard took it", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    render(<Transcript items={ITEMS} onApprove={vi.fn()} />);
    const copies = screen.getAllByTestId("bubble-copy");
    expect(copies).toHaveLength(2);
    fireEvent.click(copies[0]);
    expect(writeText).toHaveBeenCalledWith("post the digest");
    // Only after the write resolves: a write that failed must not read as done.
    await waitFor(() => expect(copies[0].textContent).toBe("Copied"));
    fireEvent.click(copies[1]);
    expect(writeText).toHaveBeenCalledWith("Done — posted to #all-reeve.");
  });

  it("shows the date and time of a stamped bubble, and the full stamp on hover", () => {
    render(<Transcript items={ITEMS} onApprove={vi.fn()} />);
    const stamps = screen.getAllByTestId("bubble-ts");
    expect(stamps).toHaveLength(1);
    const when = new Date(TS * 1000);
    // The date too, not the time alone (owner 2026-09-19).
    expect(stamps[0].textContent).toBe(when.toLocaleString([], { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }));
    expect(stamps[0].getAttribute("title")).toBe(when.toLocaleString());
  });

  it("offers Fork session on an answer when the surface can fork", () => {
    const onForkSession = vi.fn();
    render(<Transcript items={ITEMS} onApprove={vi.fn()} onForkSession={onForkSession} />);
    const forks = screen.getAllByTestId("bubble-fork");
    expect(forks).toHaveLength(1); // the answer's; the person's bubble has copy and time only
    fireEvent.click(forks[0]);
    expect(onForkSession).toHaveBeenCalledTimes(1);
  });

  it("offers no Fork without a way to fork, and holds it while the turn runs", () => {
    const { rerender } = render(<Transcript items={ITEMS} onApprove={vi.fn()} />);
    expect(screen.queryByTestId("bubble-fork")).toBeNull();
    rerender(<Transcript items={ITEMS} onApprove={vi.fn()} onForkSession={vi.fn()} running />);
    expect(screen.getByTestId("bubble-fork").hasAttribute("disabled")).toBe(true);
  });
});

// A notice with a title is prose: a heading and its paragraphs, not a centred status line.
describe("titled notices", () => {
  it("draws the title and a paragraph for each blank-line break", () => {
    const banner: Item = { kind: "notice", tone: "info", title: "Goal created.", text: "First paragraph about what it does.\n\nSecond paragraph about what it can't tell." };
    render(<Transcript items={[banner]} running={false} onApprove={() => {}} />);
    const block = screen.getByTestId("mode-notice");
    expect(block.textContent).toContain("Goal created.");
    expect(block.querySelectorAll("p")).toHaveLength(2);
    expect(block.className).toContain("notice-block");
  });

  it("leaves a notice with no title a one-line status", () => {
    render(<Transcript items={[{ kind: "notice", tone: "info", text: "Context compacted" }]} running={false} onApprove={() => {}} />);
    expect(screen.queryByTestId("mode-notice")).toBeNull();
    expect(screen.getByText("Context compacted").className).not.toContain("notice-block");
  });
});

// The compacted marker is a collapsible divider (the TurnDivider shape made a
// control): the label centred between two faint rules, the token delta on the line, a
// chevron after it — and the model's summary under the rule when it is opened.
describe("compacted divider", () => {
  const marker = (over: Partial<Item> = {}): Item =>
    ({
      kind: "notice",
      tone: "info",
      text: "Context compacted — earlier turns were summarized",
      freed: "48.2k freed",
      detail: "## Objective\nShip the launch note.",
      divider: true,
      ...over,
    }) as Item;

  it("draws the marker as a divider with the delta, folded until clicked", () => {
    render(<Transcript items={[marker()]} running={false} onApprove={() => {}} />);
    const row = screen.getByTestId("compaction-divider");
    expect(row.textContent).toContain("Context compacted — earlier turns were summarized");
    expect(row.querySelectorAll(".compaction-divider-line")).toHaveLength(2);
    // The one number the compaction bought stays visible while folded.
    expect(row.textContent).toContain("48.2k freed");
    expect(screen.queryByTestId("compaction-summary")).toBeNull();

    fireEvent.click(screen.getByTestId("compaction-toggle"));
    expect(screen.getByTestId("compaction-summary").textContent).toContain(
      "Ship the launch note.",
    );
  });

  it("arrives closed, like a fold's marker (owner, 2026-10-13)", () => {
    render(<Transcript items={[marker({})]} running={false} onApprove={() => {}} />);
    expect(screen.queryByTestId("compaction-summary")).toBeNull();
    expect(screen.getByTestId("compaction-toggle").getAttribute("aria-expanded")).toBe("false");
  });
});

// The revert boundary reuses the compaction divider's shape — a centred label
// between two faint rules — but has nothing to fold: the hidden messages stay on screen
// below it, and the line states what the MODEL no longer sees.
describe("revert boundary", () => {
  const boundary = (over: Partial<Item> = {}): Item =>
    ({
      kind: "notice",
      tone: "info",
      boundary: "revert",
      hidden: 2,
      text: "Reverted — the next 2 messages are hidden from the model",
      ...over,
    }) as Item;

  it("draws the undo line as a divider with the hidden count and no toggle", () => {
    render(<Transcript items={[boundary()]} running={false} onApprove={() => {}} />);
    const row = screen.getByTestId("revert-divider");
    expect(row.textContent).toContain("Reverted — the next 2 messages are hidden from the model");
    expect(row.textContent).toContain("2 hidden");
    expect(row.querySelectorAll(".compaction-divider-line")).toHaveLength(2);
    expect(screen.queryByTestId("compaction-toggle")).toBeNull();
    expect(screen.queryByTestId("compaction-summary")).toBeNull();
  });
});

// The trace comes with the answer. Reeve used to drop reasoning from a settled bubble on
// the grounds that LiveStep had already shown it live — but a trace that disappears when the
// work finishes is a trace nobody can check, and the reference design renders reasoning in the assistant body
// like any other block. Collapsed and raw, one line, is what keeps that from being noise.
describe("reasoning at rest", () => {
  const ANSWER: Item[] = [
    { kind: "assistant", text: "Xin chào! Mình là trợ lý AI.", reasoning: "The user is greeting me in Vietnamese." },
  ];

  it("folds the answer's trace into the turn; only the answer is outside", () => {
    const { container } = render(<Transcript items={ANSWER} running={false} onApprove={vi.fn()} />);
    // The answer is a bubble OUTSIDE the turn, and visible.
    expect(screen.getByText("Xin chào! Mình là trợ lý AI.")).toBeTruthy();
    // The trace lives INSIDE the turn's group — nothing leaks above it — and is on screen.
    const group = container.querySelector(".stepgroup")!;
    const row = screen.getByTestId("think-row");
    expect(row.closest(".stepgroup")).toBe(group);
    expect(row.getAttribute("data-state")).toBe("ok");
    expect(screen.getByTestId("think-summary").textContent).toBe(
      "The user is greeting me in Vietnamese.",
    );
    // Collapsed: the paragraph itself is not on screen until the row is opened.
    expect(screen.queryByTestId("think-body")).toBeNull();
    fireEvent.click(document.querySelector("[data-disclosure-row]")!);
    expect(screen.getByTestId("think-body").textContent).toContain("greeting me in Vietnamese");
  });

  it("a thinking-only message is just the trace, with no empty bubble", () => {
    render(
      <Transcript
        items={[{ kind: "assistant", text: "", reasoning: "plotting a plan" } as Item]}
        running={false}
        onApprove={vi.fn()}
      />,
    );
    expect(screen.getByTestId("think-row")).toBeTruthy();
    expect(document.querySelector(".bubble-assistant")).toBeNull();
  });
});

describe("the final trace stays inside the work group", () => {
  const TURN: Item[] = [
    { kind: "user", text: "compute it" },
    { kind: "tool", id: "t1", name: "read_file", looks: true, args: { path: "a.py" }, status: "ok", ts: 100 },
    { kind: "assistant", text: "The answer is 4.", reasoning: "Let me compute.", ts: 110 },
  ];

  it("keeps the Thought row inside 'Worked for Ns' and only the answer outside", () => {
    const { container } = render(<Transcript items={TURN} onApprove={vi.fn()} />);
    expect(container.querySelector(".stepgroup")).toBeTruthy();
    // The answer text is OUTSIDE the group and rendered.
    expect(container.textContent).toContain("The answer is 4.");
    // The trace is shown at rest, and it lives INSIDE the group.
    const think = screen.getByTestId("think-row");
    expect(think.closest(".stepgroup")).toBe(container.querySelector(".stepgroup"));
  });
});

describe("turn summary", () => {
  // The settled header answers "what did this turn touch" without unfolding the rows: files
  // and their churn, commands run, anything that failed. Derived from the items, so it can
  // never disagree with the rows underneath it.
  const diffCard = (path: string, added: number, removed: number) => ({
    summary: `Edited ${path}`,
    card: { card: "diff", title: path, diffs: [{ path, old_text: "a", new_text: "b", added, removed }] },
  });

  function renderSettled(items: Item[]) {
    const { container } = render(
      <Transcript items={[{ kind: "user", text: "do it" }, ...items, { kind: "assistant", text: "done" }]} onApprove={vi.fn()} />,
    );
    return container;
  }

  it("says a failure in the turn's footer, and nothing about files or commands", () => {
    renderSettled([
      {
        kind: "tool",
        id: "e1",
        name: "replace_in_file",
        args: {},
        status: "ok",
        presentation: diffCard("src/a.ts", 12, 3),
      } as Item,
      { kind: "tool", id: "s1", name: "run_shell", args: {}, status: "ok" },
      { kind: "tool", id: "s2", name: "run_shell", args: {}, status: "error" },
    ]);
    // The settled line is gone (owner, 2026-10-13). The files are the change receipt's to
    // list; what the footer keeps is the one thing a clean turn must not hide.
    expect(screen.queryByTestId("turn-status")).toBeNull();
    expect(screen.queryByTestId("turn-summary-changes")).toBeNull();
    expect(screen.queryByTestId("turn-summary-commands")).toBeNull();
    expect(screen.getByTestId("turn-summary-failed").closest('[data-testid="bubble-meta"]')).toBeTruthy();
    expect(screen.getByTestId("turn-summary-failed").textContent).toBe("1 failed");
  });

  it("says nothing about changes when the turn only read", () => {
    const container = renderSettled([
      { kind: "tool", id: "r1", name: "read_file", looks: true, args: { path: "a.ts" }, status: "ok" },
    ]);
    expect(container.querySelector(".stepgroup")).toBeTruthy();
    expect(screen.queryByTestId("turn-summary-changes")).toBeNull();
    expect(screen.queryByTestId("turn-summary-commands")).toBeNull();
    expect(screen.queryByTestId("turn-summary-failed")).toBeNull();
  });
});

describe("connector tool grouping", () => {
  const index = buildConnectorToolIndex([
    {
      name: "gmail",
      title: "Gmail",
      logo: "gmail",
      brand_color: "#ea4335",
      tools: [
        { name: "gmail_get_message", label: "Read message" },
        { name: "gmail_search_messages", label: "Search Gmail" },
      ],
    },
  ] as any);

  const renderWith = (items: Item[]) =>
    render(
      <ConnectorToolsContext.Provider value={index}>
        <Transcript items={items} onApprove={vi.fn()} />
      </ConnectorToolsContext.Provider>,
    );

  it("folds consecutive calls to one connector under a branded header", () => {
    const items: Item[] = [
      { kind: "user", text: "read the receipts" },
      { kind: "tool", id: "g1", name: "gmail_search_messages", args: {}, status: "ok" },
      { kind: "tool", id: "g2", name: "gmail_get_message", args: { message_id: "a" }, status: "ok" },
      { kind: "tool", id: "g3", name: "gmail_get_message", args: { message_id: "b" }, status: "ok" },
      { kind: "assistant", text: "Summarised." },
    ];
    renderWith(items);
    const group = screen.getByTestId("connector-group");
    expect(group.textContent).toContain("Gmail");
    // The per-tool breakdown uses the connector's own labels, not the wire names.
    expect(group.textContent).toContain("Read message ×2");
    expect(group.textContent).toContain("Search Gmail");
    // Collapsed: the three calls are behind the header, not three "Tool" rows.
    expect(screen.queryAllByTestId("turn-step")).toHaveLength(0);
    fireEvent.click(group.querySelector(".tool-row-line")!);
    expect(screen.getAllByTestId("turn-step")).toHaveLength(3);
    // Each call now reads as Gmail + its action, not the generic "Tool".
    expect(screen.queryByText("Tool")).toBeNull();
    expect(screen.getAllByText("Gmail").length).toBeGreaterThanOrEqual(3);
    // The header also counts the calls, on the right.
    expect(screen.getByTestId("server-calls").textContent).toBe("3 calls");
  });

  it("a server's row is the server, the tool, and what it was sent; its payload is a tree", () => {
    // Owner, 2026-10-13: a server's tools are not a fixed set, so the row is built from what
    // every call has, and a connector's result keeps the JSON tree as an MCP server's does.
    renderWith([
      { kind: "user", text: "read it" },
      {
        kind: "tool",
        id: "g1",
        name: "gmail_get_message",
        args: { message_id: "18c2f0a9", format: "full" },
        status: "ok",
        preview: '{"subject": "Q3 invoice", "labels": ["INBOX"]}',
      },
      { kind: "tool", id: "s1", name: "run_shell", args: { command: "ls" }, status: "ok" },
      { kind: "tool", id: "m1", name: "mcp__browser__browser_take_screenshot", args: { filename: "a.png" }, status: "ok" },
    ]);
    const [gmail, , browser] = screen.getAllByTestId("turn-step");
    expect(gmail.querySelector(".tool-row-title")!.textContent).toBe("Gmail");
    expect(gmail.querySelector('[data-testid="tool-what"]')!.textContent).toBe("Read message");
    expect(gmail.querySelector(".tool-row-args")!.textContent).toBe('message_id="18c2f0a9" format="full"');
    expect(gmail.querySelector('[data-testid="mcp-tag"]')).toBeNull();
    // An MCP server: titled by the server, tagged, its tool named without the server's prefix.
    expect(browser.querySelector(".tool-row-title")!.textContent).toBe("Browser");
    expect(browser.querySelector('[data-testid="mcp-tag"]')!.textContent).toBe("MCP");
    expect(browser.querySelector('[data-testid="tool-what"]')!.textContent).toBe("Take screenshot");
    expect(browser.textContent).not.toMatch(/Call(ed|ing)|mcp__/);
    // The connector's result is a tree, not labelled lines.
    fireEvent.click(gmail.querySelector(".tool-row-line")!);
    expect(gmail.querySelector('[data-testid="jt-tree"]')).not.toBeNull();
    expect(gmail.querySelector('[data-testid="tool-io-fields"]')).toBeNull();
  });
});

