// What a person sees when Reeve picked a turn up after its process ended: the line the
// turn goes on under, a stopped background command with what it printed, and the card that asks
// whether a call that may already have happened should run again.

import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { ApprovalCard } from "./ApprovalCard";
import { Transcript } from "./Transcript";
import { itemsFromMessages } from "../itemsFromMessages";
import { jobStoppedNotice, restartNotice, wakeNotice } from "../wakeNotice";
import { approvalItemFromEvent } from "../approvalItem";
import type { Item } from "../types";

afterEach(cleanup);

describe("a turn picked up after a restart", () => {
  it("says how far the turn had got and what the restart stopped", () => {
    const line = wakeNotice({ synthetic: "restart", steps: 3, jobs: ["npm run dev"] });
    expect(line?.wakeFacts).toMatchObject({ kind: "restart", verb: "Reeve restarted" });
    expect(line?.wakeFacts?.detail).toBe("3 steps were already done · npm run dev was stopped");
    expect(wakeNotice({ synthetic: "restart" })?.wakeFacts?.detail).toBe("picked up where it left off");
  });

  it("draws the continuation as Reeve's line, never as the person's bubble", () => {
    const items = itemsFromMessages([
      { role: "user", content: "Fix the export" },
      { role: "user", content: "Reeve restarted while you were working…", source: { synthetic: "restart", steps: 2 } },
      { role: "assistant", content: "Fixed." },
    ] as never);
    const users = items.filter((it) => it.kind === "user");
    expect(users).toHaveLength(1);
    render(<Transcript items={items} running={false} onApprove={vi.fn()} />);
    expect(screen.getByTestId("wake-marker").textContent).toContain("Reeve restarted");
    expect(screen.getByTestId("wake-marker").textContent).toContain("2 steps were already done");
  });

  it("replays the persisted notices as quiet lines, not errors with a Retry", () => {
    const items = itemsFromMessages([
      { role: "user", content: "go" },
      { role: "notice", kind: "restart", text: "Reeve restarted · picked up where it left off", steps: 1 },
      { role: "notice", kind: "restart", text: "Reeve restarted 3 times during this turn, so it was not picked up again.", given_up: true },
      { role: "notice", kind: "job_stopped", text: "Background task stopped", command: "npm run dev", tail: "ready\non :5173" },
    ] as never);
    const notices = items.filter((it): it is Extract<Item, { kind: "notice" }> => it.kind === "notice");
    expect(notices.every((n) => !n.retriable)).toBe(true);
    expect(notices[0].wakeFacts?.kind).toBe("restart");
    expect(notices[1].tone).toBe("warn");
    expect(notices[2].wakeFacts).toMatchObject({ kind: "shell", title: "npm run dev", output: "ready\non :5173" });
  });

  it("opens a stopped command's output in place, and says how long it is", async () => {
    const stopped = jobStoppedNotice({ command: "npm run dev", tail: "VITE ready\nLocal: http://localhost:5173/" });
    render(<Transcript items={[{ kind: "user", text: "start it" } as Item, stopped]} running={false} onApprove={vi.fn()} />);
    const toggle = screen.getByTestId("wake-output-toggle");
    expect(toggle.textContent).toBe("Show output · 2 lines");
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    expect(screen.queryByTestId("wake-output")).toBeNull();
    await act(async () => {
      fireEvent.click(toggle);
    });
    expect(toggle.getAttribute("aria-expanded")).toBe("true");
    expect(screen.getByTestId("wake-output").textContent).toContain("localhost:5173");
  });

  it("a restart notice with nothing given up is an inline line of the turn", () => {
    expect(restartNotice({ steps: 4 })).toMatchObject({ inline: true, wakeFacts: { kind: "restart" } });
  });
});

describe("a call that may already have happened", () => {
  const item = approvalItemFromEvent({
    name: "run_shell",
    arguments: { command: "npm run db:migrate" },
    reason: "this was already under way when the session stopped, so it may already have happened — run it again?",
    human_only: true,
    uncertain: true,
  });

  it("asks whether to run it again, with checking first as the filled answer", () => {
    const onApprove = vi.fn();
    render(<ApprovalCard item={item} onApprove={onApprove} />);
    expect(screen.getByText("This command had started when Reeve stopped. Run it again?")).toBeTruthy();
    expect(screen.getByTestId("approval-may-have-run")).toBeTruthy();
    // No lasting grant and no editor: the question is about something that already happened.
    expect(screen.queryByText("Always allow this command")).toBeNull();
    expect(screen.queryByTestId("approval-edit-open")).toBeNull();
    expect(screen.queryByTestId("approval-always-asks")).toBeNull();
    fireEvent.click(screen.getByTestId("approval-check-first"));
    expect(onApprove).toHaveBeenLastCalledWith("deny");
    fireEvent.click(screen.getByTestId("approval-run-again"));
    expect(onApprove).toHaveBeenLastCalledWith("once");
  });

  it("an ordinary approval keeps its answers", () => {
    render(<ApprovalCard item={approvalItemFromEvent({ name: "run_shell", arguments: { command: "ls" } })} onApprove={vi.fn()} />);
    expect(screen.queryByTestId("approval-check-first")).toBeNull();
    expect(screen.getByTestId("approval-edit-open")).toBeTruthy();
  });
});
