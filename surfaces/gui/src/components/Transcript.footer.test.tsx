import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { Transcript, turnModels } from "./Transcript";
import { wakeNotice } from "../wakeNotice";
import type { Item } from "../types";

afterEach(cleanup);

// The answer's footer (owner, 2026-10-12): copy, fork, the time and the model that answered the
// turn stay on screen once the turn settles; while it runs there is none.

const user = (text: string): Item => ({ kind: "user", text, ts: 990 });
const call = (id: string, model: string, status = "ok"): Item => ({
  kind: "tool",
  id,
  name: "run_shell",
  args: { command: "pytest -q" },
  status,
  ts: 1000,
  model,
});
const answer = (text: string, model?: string): Item => ({
  kind: "assistant",
  text,
  ts: 1010,
  ...(model ? { model } : {}),
});

describe("which models answered a turn", () => {
  it("names the model of the closing answer first, then the others by how much they did", () => {
    const items = [call("a", "google:gemini-3.8-flash"), call("b", "anthropic:claude-haiku-5-5"), call("c", "anthropic:claude-haiku-5-5")];
    const models = turnModels([...items, answer("done", "google:gemini-3.8-flash")], "google:gemini-3.8-flash");
    expect(models).toEqual([
      { model: "google:gemini-3.8-flash", steps: 1 },
      { model: "anthropic:claude-haiku-5-5", steps: 2 },
    ]);
  });

  it("counts nobody for items an older server sent without a model", () => {
    expect(turnModels([{ kind: "tool", id: "x", name: "grep", args: {}, status: "ok" }])).toEqual([]);
  });
});

describe("the closing answer's footer", () => {
  it("is on screen without a hover once the turn settles, and names the model", () => {
    render(
      <Transcript
        items={[user("run the tests"), call("t1", "google:gemini-3.8-flash"), answer("All green.", "google:gemini-3.8-flash")]}
        running={false}
        onApprove={vi.fn()}
      />,
    );
    // The user's message keeps its on-hover row; the answer's is the one that stays.
    const shown = screen.getAllByTestId("bubble-meta").filter((el) => el.className.includes("shown"));
    expect(shown).toHaveLength(1);
    expect(shown[0].textContent).toContain("Gemini 3.8 Flash");
    expect(screen.getByTestId("bubble-model").textContent).toContain("Gemini 3.8 Flash");
  });

  it("says +1 when a second model answered steps of the same turn", () => {
    render(
      <Transcript
        items={[
          user("draft the notes"),
          call("t1", "google:gemini-3.8-flash"),
          call("t2", "anthropic:claude-haiku-5-5"),
          answer("Drafted.", "google:gemini-3.8-flash"),
        ]}
        running={false}
        onApprove={vi.fn()}
      />,
    );
    const model = screen.getByTestId("bubble-model");
    expect(model.textContent).toContain("Gemini 3.8 Flash");
    expect(model.textContent).toContain("+1");
    expect(model.getAttribute("aria-label")).toContain("Claude Haiku 5 5");
  });

  it("gives a turn the engine started its own footer, apart from the turn before it", () => {
    // Owner-hit 2026-10-13: a subagent's report started a turn with no boundary, so its
    // answer was the previous turn's second answer and the first one's hover row lay on it.
    render(
      <Transcript
        items={[
          user("start it in the background"),
          call("t1", "google:gemini-3.8-flash"),
          answer("Started.", "google:gemini-3.8-flash"),
          { kind: "notice", tone: "info", text: "A subagent finished and reported back", wake: "background_task:c1", ts: 1020 },
          answer("It is done.", "anthropic:claude-haiku-5-5"),
        ]}
        running={false}
        onApprove={vi.fn()}
      />,
    );
    const shown = screen.getAllByTestId("bubble-meta").filter((el) => el.className.includes("shown"));
    expect(shown.map((el) => el.textContent?.includes("Gemini 3.8 Flash"))).toEqual([true, false]);
    expect(shown[1].textContent).toContain("Claude Haiku 5 5");
    expect(screen.getAllByTestId("turn-answer")).toHaveLength(1);
    expect(screen.getByText("A subagent finished and reported back")).toBeTruthy();
  });

  it("names the subagent that reported back, and opens its session from the line", () => {
    const onOpenChild = vi.fn();
    const wake = wakeNotice(
      { synthetic: "background_task", session_id: "c1", title: "Write tests for the flag", state: "completed", steps: 7 },
      1020,
    )!;
    render(
      <Transcript
        items={[user("start it"), answer("Started."), wake, answer("It is done.")]}
        running={false}
        onApprove={vi.fn()}
        onOpenChild={onOpenChild}
      />,
    );
    const marker = screen.getByTestId("wake-marker");
    expect(marker.textContent).toContain("Subagent finished");
    expect(marker.textContent).toContain("Write tests for the flag");
    expect(marker.textContent).toContain("7 steps");
    fireEvent.click(screen.getByTestId("wake-open"));
    expect(onOpenChild).toHaveBeenCalledWith({ runId: "c1", title: "Write tests for the flag" });
  });

  it("a background workflow reports back as a workflow, with how many agents worked (owner, 2026-10-13)", () => {
    const done = wakeNotice({
      synthetic: "background_task", session_id: "r1", title: "file-review workflow", state: "completed",
      workflow: true, agents: 3,
    })!;
    expect(done.text).toBe("Workflow finished · file-review · 3 agents");
    const failed = wakeNotice({
      synthetic: "background_task", session_id: "r2", title: "file-review workflow", state: "error",
      workflow: true, agents: 1,
    })!;
    expect(failed.text).toBe("Workflow failed · file-review · 1 agent");
    expect(failed.wakeFacts).toMatchObject({ kind: "task", sessionId: "r2", failed: true });
  });

  it("names the background command and how it ended; a failure is said in red words", () => {
    const done = wakeNotice({ synthetic: "background_shell", task_id: "bg-1", title: "npm run build", state: "finished", exit_code: 0 })!;
    expect(done.text).toBe("Background command finished · npm run build · exit 0");
    expect(done.wakeFacts).toMatchObject({ kind: "shell", detail: "exit 0" });
    const failed = wakeNotice({ synthetic: "background_shell", task_id: "bg-2", title: "npm run lint", state: "finished", exit_code: 1 })!;
    expect(failed.wakeFacts!.failed).toBe(true);
    const heard = wakeNotice({ synthetic: "background_shell", task_id: "bg-3", title: "npm run dev", state: "matched" })!;
    expect(heard.wakeFacts!.verb).toBe("Background command said what it was waited for");
    // nothing to open for a command
    render(<Transcript items={[user("go"), answer("ok"), done, answer("built")]} running={false} onApprove={vi.fn()} onOpenChild={vi.fn()} />);
    expect(screen.queryByTestId("wake-open")).toBeNull();
  });

  it("keeps every answer's row in the flow when one turn ends in two answers", () => {
    render(
      <Transcript
        items={[user("go"), call("t1", "google:gemini-3.8-flash"), answer("First part."), answer("Second part.", "google:gemini-3.8-flash")]}
        running={false}
        onApprove={vi.fn()}
      />,
    );
    // An on-hover row takes no room: under the first answer it would cover the second.
    const inAnswer = screen.getByTestId("turn-answer").querySelectorAll('[data-testid="bubble-meta"]');
    expect(inAnswer).toHaveLength(2);
    for (const meta of inAnswer) expect(meta.className).toContain("shown");
    expect(screen.getAllByTestId("bubble-model")).toHaveLength(1);
  });

  it("says how long the turn worked, and the settled turn has no line of its own", () => {
    // Owner, 2026-10-13: no "Worked for 24s · 2 commands" between the steps and the answer.
    render(
      <Transcript
        items={[
          { kind: "user", text: "run the tests", ts: 1000 },
          { ...call("t1", "google:gemini-3.8-flash"), ts: 1004, endTs: 1024 } as Item,
          { kind: "assistant", text: "All green.", ts: 1024, model: "google:gemini-3.8-flash" },
        ]}
        running={false}
        onApprove={vi.fn()}
      />,
    );
    expect(screen.queryByTestId("turn-status")).toBeNull();
    const worked = screen.getByTestId("turn-worked");
    expect(worked.textContent).toBe("Worked for 24s");
    expect(worked.closest('[data-testid="bubble-meta"]')!.className).toContain("shown");
    // a clean turn says nothing went against it
    expect(screen.queryByTestId("turn-summary-failed")).toBeNull();
    expect(screen.queryByTestId("turn-stopped")).toBeNull();
  });

  it("closes a turn that ended without an answer, with nothing to copy", () => {
    render(
      <Transcript
        items={[
          { kind: "user", text: "build it", ts: 1000 },
          { ...call("t1", "google:gemini-3.8-flash", "interrupted"), ts: 1001, endTs: 1045 } as Item,
        ]}
        running={false}
        onApprove={vi.fn()}
      />,
    );
    const foot = screen.getByTestId("turn-foot");
    expect(foot.textContent).toContain("Worked for 45s");
    expect(foot.textContent).toContain("Gemini 3.8 Flash");
    expect(screen.getByTestId("turn-stopped").textContent).toBe("stopped");
    expect(foot.querySelector('[data-testid="bubble-copy"]')).toBeNull();
    expect(foot.querySelector('[data-testid="bubble-fork"]')).toBeNull();
  });

  it("has no standing footer while the turn still runs", () => {
    render(
      <Transcript
        items={[user("run the tests"), call("t1", "google:gemini-3.8-flash", "…"), answer("Working on it", "google:gemini-3.8-flash")]}
        running
        onApprove={vi.fn()}
      />,
    );
    expect(screen.queryByTestId("bubble-model")).toBeNull();
    for (const meta of screen.queryAllByTestId("bubble-meta")) expect(meta.className).not.toContain("shown");
  });
});
