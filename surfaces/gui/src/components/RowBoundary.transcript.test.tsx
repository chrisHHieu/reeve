// The boundary is where it has to be: around each tool row of the transcript. A renderer that
// throws (here the fields view, made to) leaves the rest of the conversation on screen.

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import type { Item } from "../types";

vi.mock("../cards/fields", async (original) => ({
  ...(await original<typeof import("../cards/fields")>()),
  fieldsOf: () => {
    throw new Error("a renderer bug nobody has found yet");
  },
}));

import { Transcript } from "./Transcript";

afterEach(cleanup);

describe("a tool row whose renderer throws, inside a transcript", () => {
  it("is replaced by a plain row and the conversation stays", () => {
    const errors = vi.spyOn(console, "error").mockImplementation(() => {});
    const items = [
      { kind: "user", text: "do we have a goal?" },
      { kind: "tool", id: "t1", name: "goal", args: { action: "status" }, callId: "c1", status: "ok", preview: '{"goal": null}' },
      { kind: "assistant", text: "No goal is set." },
    ] as Item[];

    render(<Transcript items={items} running={false} onApprove={vi.fn()} />);

    expect(screen.getByTestId("tool-row-fallback").textContent).toContain("could not be drawn");
    expect(screen.getByText("do we have a goal?")).toBeTruthy();
    expect(screen.getByText("No goal is set.")).toBeTruthy();
    errors.mockRestore();
  });
});
