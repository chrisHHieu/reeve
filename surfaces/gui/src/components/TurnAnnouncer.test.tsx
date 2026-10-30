import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { TurnAnnouncer, turnAnnouncement } from "./TurnAnnouncer";
import type { Item } from "../types";

afterEach(cleanup);

// GUI rendering review, G6: a screen reader heard nothing when a turn started or ended —
// only the composer's dictation and the queue had live regions. The rule it asks for:
// announce state changes, never tokens.
const user = { kind: "user", text: "rename qty" } as Item;
const answer = { kind: "assistant", text: "Renamed it." } as Item;
const receipt = {
  kind: "changes",
  overwritten: ["/w/a.py", "/w/b.py"],
  check: { command: "pytest -q", ok: true, summary: "3 passed", editsSince: 0, timedOut: false },
} as Item;

describe("turnAnnouncement", () => {
  it("names the live state and the settled result", () => {
    expect(turnAnnouncement([user], true)).toBe("Working");
    expect(turnAnnouncement([user, { kind: "approval", name: "run_shell", args: {}, reason: "" } as Item], true)).toBe(
      "Needs approval",
    );
    expect(turnAnnouncement([user, answer, receipt], false)).toBe("Done · 2 files changed · check passed");
    expect(turnAnnouncement([user, answer], false)).toBe("Done");
    expect(turnAnnouncement([user, { kind: "notice", tone: "warn", text: "Stopped." } as Item], false)).toBe(
      "Stopped",
    );
  });
});

describe("TurnAnnouncer", () => {
  it("speaks when a turn starts and when it ends, and stays quiet over a replayed history", () => {
    const { rerender } = render(<TurnAnnouncer items={[user, answer, receipt]} running={false} />);
    const status = screen.getByRole("status");
    expect(status.className).toContain("sr-only");
    expect(status.textContent).toBe(""); // opening a session is not an event

    rerender(<TurnAnnouncer items={[user]} running />);
    expect(status.textContent).toBe("Working");

    rerender(<TurnAnnouncer items={[user, answer, receipt]} running={false} />);
    expect(status.textContent).toBe("Done · 2 files changed · check passed");
  });
});
