import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { ApprovalCard } from "./components/ApprovalCard";
import { approvalItemFromEvent, approvalItemFromInbox } from "./approvalItem";
import type { InboxItem } from "./api";

afterEach(cleanup);

// GUI UX review, U2: the live card offered Always allow and Edit; back in the same session,
// the parked prompt drew a thinner card with neither.
const live = {
  name: "run_shell",
  arguments: { command: "git status" },
  reason: "requires approval",
  axis: "consent",
  category: "",
  provenance: null,
  standing_target: null,
  presentation: { summary: "Show working tree status", kind: "local" },
};

const parked: InboxItem = {
  id: "i1",
  session_id: "s1",
  kind: "approval",
  title: "Run `run_shell`?",
  body: "requires approval\ncommand: git status",
  state: "pending",
  resolution: null,
  inbox: "default",
  created_at: "",
  resolved_at: null,
  data: {
    tool: "run_shell",
    arguments: { command: "git status" },
    presentation: live.presentation,
    card: { reason: "requires approval", axis: "consent", provenance: null, standing_target: null },
  },
} as unknown as InboxItem;

describe("approvalItemFromInbox", () => {
  it("rebuilds exactly the item the live frame made", () => {
    expect(approvalItemFromInbox(parked)).toEqual(approvalItemFromEvent(live));
  });

  it("draws the same card, buttons and all", () => {
    const buttons = () => screen.getAllByRole("button").map((b) => b.textContent?.trim());
    render(<ApprovalCard item={approvalItemFromEvent(live)} onApprove={vi.fn()} />);
    const fromLive = buttons();
    cleanup();
    render(<ApprovalCard item={approvalItemFromInbox(parked)!} onApprove={vi.fn()} />);
    expect(buttons()).toEqual(fromLive);
    expect(fromLive.some((label) => /Always allow/.test(label || ""))).toBe(true);
  });

  it("leaves an item parked before the payload existed to the generic card", () => {
    expect(approvalItemFromInbox({ ...parked, data: {} } as InboxItem)).toBeNull();
  });
});
