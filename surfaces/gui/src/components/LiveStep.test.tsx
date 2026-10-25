// The live step renders in the order the step happened.
//
// The bug this replaced: the trace was gated on `!streaming`, so it vanished the instant a
// sentence arrived and never came back for a step that thought again afterwards. Every
// test here is about order surviving.
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { LiveStep } from "./LiveStep";
import { ThinkRow, firstLine, latestLine } from "./ThinkRow";
import type { StreamBlock } from "../streamBlocks";

afterEach(cleanup);

const think = (text: string): StreamBlock => ({ kind: "reasoning", text });
const say = (text: string): StreamBlock => ({ kind: "text", text });
const call = (name: string, argsRaw = ""): StreamBlock => ({
  kind: "tool_call",
  callId: "c1",
  name,
  argsRaw,
});

describe("LiveStep", () => {
  it("keeps thinking on screen after prose arrives", () => {
    // Exactly what the old `!streaming` gate got wrong.
    render(<LiveStep blocks={[think("checking the file"), say("Let me read it.")]} showText />);
    expect(screen.getByTestId("think-summary").textContent).toContain("checking the file");
    expect(screen.getByText("Let me read it.")).toBeTruthy();
  });

  it("puts the whole step in ONE assistant bubble, not one per text block", () => {
    const { container } = render(
      <LiveStep blocks={[say("Starting."), call("read_file"), say("Done.")]} showText />,
    );
    expect(container.querySelectorAll(".bubble-assistant").length).toBe(1);
    // No role label inside it (owner 2026-09-19).
    expect(container.querySelectorAll(".who").length).toBe(0);
  });

  it("splits a step into its steps and its answer (owner, 2026-09-30)", () => {
    // The prose before a call is narration and stays with the steps on the timeline; the
    // prose after the step's last call is the answer so far and streams off it.
    const blocks = [think("plan"), say("Let me read it."), call("read_file"), say("It says 42.")];
    const steps = render(<LiveStep blocks={blocks} showText part="steps" />);
    expect(steps.container.textContent).toContain("Let me read it.");
    expect(steps.getByTestId("live-call")).toBeTruthy();
    expect(steps.container.textContent).not.toContain("It says 42.");
    cleanup();
    const answer = render(<LiveStep blocks={blocks} showText part="answer" />);
    expect(answer.getByTestId("live-answer").textContent).toContain("It says 42.");
    expect(answer.container.textContent).not.toContain("Let me read it.");
    expect(answer.queryByTestId("live-call")).toBeNull();
  });

  it("gives a step with no call all its prose to the answer, and draws nothing when it has none", () => {
    const blocks = [think("plan"), say("Here is the answer.")];
    const answer = render(<LiveStep blocks={blocks} showText part="answer" />);
    expect(answer.getByTestId("live-answer").textContent).toContain("Here is the answer.");
    cleanup();
    const steps = render(<LiveStep blocks={blocks} showText part="steps" />);
    expect(steps.container.textContent).not.toContain("Here is the answer.");
    cleanup();
    const none = render(<LiveStep blocks={[call("read_file")]} showText part="answer" />);
    expect(none.container.innerHTML).toBe("");
  });

  it("renders no bubble for a step that has only thought so far", () => {
    const { container } = render(<LiveStep blocks={[think("still weighing it")]} showText />);
    expect(container.querySelector(".bubble-assistant")).toBeNull();
    expect(screen.getByTestId("think-summary")).toBeTruthy();
  });

  it("leaves the live trace to the status line, and shows it as Thought once it ends", () => {
    // Design B (2026-09-28): one live line, at the bottom of the turn. The trace being
    // written is not a row here; when the stream moves on it is, with its own clock.
    const { rerender } = render(<LiveStep blocks={[think("weighing it")]} showText hideLiveThought />);
    expect(screen.queryByTestId("think-row")).toBeNull();
    rerender(
      <LiveStep blocks={[think("weighing it"), say("Here goes.")]} showText hideLiveThought />,
    );
    const row = screen.getByTestId("think-row");
    expect(row.getAttribute("data-state")).toBe("ok");
    expect(row.textContent).toContain("weighing it");
  });

  it("renders every block of a step, in order", () => {
    const { container } = render(
      <LiveStep
        blocks={[think("first"), say("Starting."), think("second"), say("Done.")]}
        showText
      />,
    );
    // One bubble wraps the whole step, so order is read off the ROWS inside it, not off
    // the chrome around them.
    const kinds = [...container.querySelectorAll(".think-row, .live-text")].map((el) =>
      el.className.includes("think-row") ? "reasoning" : "text",
    );
    expect(kinds).toEqual(["reasoning", "text", "reasoning", "text"]);
  });

  it("shows a call being written as its verb and subject, never its JSON", () => {
    // GUI review F1: the row showed `read_file {"path":"q3.` for the whole stream.
    render(<LiveStep blocks={[call("read_file", '{"path":"q3.')]} showText />);
    const row = screen.getByTestId("live-call");
    expect(row.textContent).toContain("Reading");
    expect(row.textContent).toContain("q3.");
    expect(row.textContent).not.toContain('{"');
    // The still-arriving signal is the shimmer on the verb, not a
    // pulsing accent dot.
    expect(row.querySelector(".live-call-name")!.className).toContain("shimmer-text");
    expect(row.querySelector(".live-call-dot")).toBeNull();
  });

  it("shows a shell command unescaped, as it grows", () => {
    render(<LiveStep blocks={[call("run_shell", '{"command": "python3 -c \\"\\nimport')]} showText />);
    const row = screen.getByTestId("live-call");
    expect(row.textContent).toContain('$ python3 -c " import');
    expect(row.textContent).not.toContain('{"command"');
  });

  it("names an MCP call by its verb only — its arguments are the server's business", () => {
    render(<LiveStep blocks={[call("mcp__wealthlog__query_database", '{"sql":"SELECT')]} showText />);
    const row = screen.getByTestId("live-call");
    expect(row.textContent).toContain("Wealthlog");
    expect(row.textContent).not.toContain("SELECT");
  });

  it("keeps a long subject to one ellipsized line", () => {
    render(<LiveStep blocks={[call("read_file", '{"path":"' + "d/".repeat(80))]} showText />);
    const subject = screen.getByTestId("live-call").querySelector(".live-call-args")!;
    expect(subject.textContent!.length).toBeLessThanOrEqual(73);
    expect(subject.textContent!.endsWith("…")).toBe(true);
  });

  it("hides the preview for a tool that has its own card", () => {
    // `ask_user` still renders a card (and its row is suppressed); a raw JSON preview here
    // was the flash readers saw before it. (`propose_plan` is NOT suppressed anymore — its
    // content is a transcript step — so it is previewed like any other tool.)
    render(<LiveStep blocks={[call("ask_user", '{"question":"which one"}')]} showText />);
    const row = screen.getByTestId("live-call");
    expect(row.textContent).not.toContain("which one");
    expect(row.querySelector(".live-call-args")).toBeNull();
  });

  it("says nothing about a call whose name has not arrived yet", () => {
    render(<LiveStep blocks={[call("", "")]} showText />);
    expect(screen.queryByTestId("live-call")).toBeNull();
  });

  it("counts the lines an edit is still writing, instead of echoing raw JSON", () => {
    render(
      <LiveStep blocks={[call("write_file", '{"path":"a.py","content":"x\\ny\\n')]} showText />,
    );
    const row = screen.getByTestId("live-call");
    expect(screen.getByTestId("live-churn").textContent).toContain("+2");
    // The file and the count, never the raw fragment.
    expect(row.querySelector(".live-call-args")!.textContent).toBe("a.py");
    expect(row.textContent).not.toContain('{"');
  });

  it("draws a call written whole as waiting to run while the next one is still arriving", () => {
    // Two writes in one message: the engine runs neither until the message has arrived, so the
    // first is not being edited. Both read "Editing", shimmering, and the first looked like a
    // write that never finished (owner's screenshot, 2026-10-20).
    render(
      <LiveStep
        blocks={[
          call("write_file", '{"path":"getfonts.py","content":"a\\nb\\n"}'),
          call("write_file", '{"content":"x\\ny\\n'),
        ]}
        showText
      />,
    );
    const [first, second] = screen.getAllByTestId("live-call");
    expect(first.querySelector(".live-call-name")!.textContent).toBe("Edit");
    expect(first.querySelector(".live-call-name")!.className).not.toContain("shimmer-text");
    expect(first.querySelector(".live-call-args")!.textContent).toBe("getfonts.py");
    expect(first.textContent).toContain("waiting to run");
    expect(second.querySelector(".live-call-name")!.textContent).toBe("Editing");
    expect(second.querySelector(".live-call-name")!.className).toContain("shimmer-text");
    expect(second.textContent).not.toContain("waiting to run");
  });

  it("counts a Reeve replace_in_file, whose keys are old/new", () => {
    render(
      <LiveStep
        blocks={[call("replace_in_file", '{"path":"a.py","old":"x\\ny","new":"z')]}
        showText
      />,
    );
    expect(screen.getByTestId("live-churn").textContent).toContain("+1");
    expect(screen.getByTestId("live-churn").textContent).toContain("−2");
  });

  it("holds prose back until the caller promotes it", () => {
    // Sub-threshold text is narration and belongs inside the live turn group — rendering
    // it here as well would put the same words on screen twice.
    render(<LiveStep blocks={[think("hmm"), say("One moment")]} showText={false} />);
    expect(screen.queryByText("One moment")).toBeNull();
    expect(screen.getByTestId("think-summary")).toBeTruthy();
  });

  it("renders nothing when the step has nothing to show", () => {
    const { container } = render(<LiveStep blocks={[]} showText />);
    expect(container.innerHTML).toBe("");
    // Whitespace-only reasoning is not something to show either.
    cleanup();
    const blank = render(<LiveStep blocks={[think("   ")]} showText />);
    expect(blank.container.innerHTML).toBe("");
  });

  it("marks only the block still being written as live", () => {
    const { container } = render(
      <LiveStep blocks={[think("earlier thought"), think("current thought")]} showText />,
    );
    // One vocabulary for every trace, the reference design's: the RUNNING row says "Thinking" and the
    // settled one "Thought · Ns" (owner ask 2026-09-11). What follows is the trace's own
    // line — the newest while it streams, the first once it has stopped.
    const states = [...container.querySelectorAll(".think-row")].map((el) =>
      el.getAttribute("data-state"),
    );
    expect(states).toEqual(["ok", "running"]);
    const titles = [...container.querySelectorAll(".think-row-title")].map((el) =>
      el.textContent?.trim(),
    );
    expect(titles[1]).toBe("Thinking");
    // The finished trace carries its own clock. It is wall-clock measured, so this render
    // (no time passing) legitimately lands on "Thought" — the duration needs a tick.
    expect(titles[0]!.startsWith("Thought")).toBe(true);
    const summaries = [...container.querySelectorAll("[data-testid='think-summary']")].map(
      (el) => el.textContent,
    );
    expect(summaries).toEqual(["earlier thought", "current thought"]);
  });

  it("times a finished trace with the wall clock", () => {
    // The reader-facing clock: a thought block that stops being live gets "Thought · Ns",
    // measured from when this component first rendered it (a streamed block has no stamp
    // to subtract from). One mounted instance, as in the app — a fresh render would just
    // be the first sight of the block and could not measure anything.
    vi.useFakeTimers();
    try {
      vi.setSystemTime(new Date("2026-09-11T10:00:00Z"));
      const view = render(<LiveStep blocks={[think("weighing options")]} showText />);
      expect(view.container.querySelector(".think-row-title")?.textContent?.trim()).toBe("Thinking");
      // Eight seconds later a tool call arrives: the trace is no longer the live block, so
      // it has settled and can be timed.
      vi.setSystemTime(new Date("2026-09-11T10:00:08Z"));
      view.rerender(
        <LiveStep blocks={[think("weighing options"), call("read_file", '{"path":"a.csv"}')]} showText />,
      );
      expect(view.container.querySelector(".think-row-title")?.textContent?.trim()).toBe(
        "Thought · 8s",
      );
    } finally {
      vi.useRealTimers();
    }
  });
});

describe("the reasoning summary", () => {
  it("is the latest line while running and the first line once stopped", () => {
    const trace = "Read the brief\nChecked the CSV\nComparing to Q2";
    expect(latestLine(trace)).toBe("Comparing to Q2");
    expect(firstLine(trace)).toBe("Read the brief");
  });

  it("survives a trailing newline, which is where a token boundary usually lands", () => {
    expect(latestLine("thinking\nstill thinking\n")).toBe("still thinking");
  });

  it("handles a trace with no newline at all", () => {
    expect(latestLine("one long thought")).toBe("one long thought");
    expect(firstLine("one long thought")).toBe("one long thought");
  });

  it("keeps the summary line while open, and shows the raw trace beneath it", () => {
    render(<ThinkRow text={"a\nb"} running />);
    expect(screen.getByTestId("think-summary").textContent).toBe("b");
    fireEvent.click(document.querySelector("[data-disclosure-row]")!);
    // The summary STAYS: it is the only line naming what this row is, and losing the thing
    // you clicked on is what makes a disclosure feel like a page navigation.
    expect(screen.getByTestId("think-summary")).toBeTruthy();
    // Raw, newlines preserved, no markdown chrome anywhere in the trace.
    expect(screen.getByTestId("think-body").textContent).toBe("a\nb");
  });
});
