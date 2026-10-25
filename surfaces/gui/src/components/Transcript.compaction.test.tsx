import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { Transcript } from "./Transcript";
import { compactionNotice } from "../compactionNotice";
import type { Item } from "../types";

afterEach(cleanup);

// A compaction leaves a marker where it happened. Inside a turn it is a moment of that turn:
// as a boundary it cut one turn into two groups (owner, 2026-10-13).

const user = (text: string): Item => ({ kind: "user", text, ts: 100 });
const call = (id: string, status = "ok"): Item => ({
  kind: "tool",
  id,
  name: "run_shell",
  args: { command: "pytest -q" },
  status,
  ts: 110,
});
const marker = compactionNotice({ saved_tokens: 152_000, now_tokens: 30_000, summary: "Goal: keep the tests green." });

describe("the compaction marker", () => {
  it("says what happened as from → to, and that there is something to open", () => {
    expect(marker).toMatchObject({ text: "Compacted", span: "182k → 30k tokens", divider: true });
    render(<Transcript items={[user("go"), marker]} onApprove={vi.fn()} />);
    const drawn = screen.getByTestId("compaction-divider");
    expect(drawn.textContent).toContain("Compacted");
    expect(drawn.textContent).toContain("182k → 30k tokens");
    expect(drawn.textContent).toContain("What it kept");
  });

  it("stands inside the turn it happened in, which stays one turn", () => {
    const { container } = render(
      <Transcript
        items={[user("go"), call("a"), marker, call("b"), { kind: "assistant", text: "Done.", ts: 130 }]}
        onApprove={vi.fn()}
      />,
    );
    expect(container.querySelectorAll(".stepgroup")).toHaveLength(1);
    expect(screen.getByTestId("compaction-divider").closest(".turn-rail")).not.toBeNull();
    // one turn, so one footer
    expect(screen.getAllByTestId("bubble-meta").filter((el) => el.className.includes("shown"))).toHaveLength(1);
  });

  it("stays in the running turn while it is the newest thing in it", () => {
    const { container } = render(
      <Transcript items={[user("go"), call("a"), marker]} onApprove={vi.fn()} running />,
    );
    expect(container.querySelectorAll(".stepgroup")).toHaveLength(1);
    expect(screen.getByTestId("compaction-divider").closest(".stepgroup")).not.toBeNull();
  });

  it("is a boundary between two turns when it ran between them", () => {
    render(
      <Transcript
        items={[user("go"), call("a"), { kind: "assistant", text: "Done.", ts: 130 }, marker, user("next")]}
        onApprove={vi.fn()}
      />,
    );
    expect(screen.getByTestId("compaction-divider").closest(".stepgroup")).toBeNull();
    // the answer before it is still the turn's answer, not a step pushed back into the turn
    expect(screen.getByTestId("turn-answer").textContent).toContain("Done.");
  });
});
