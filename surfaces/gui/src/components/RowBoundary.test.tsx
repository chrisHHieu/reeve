// A tool row draws data nobody here controls, so it must not be able to take the page down.
//
// Real session, 2026-10-04: `goal status` returned `{"goal": null}`; the fields view threw on
// the null while rendering, nothing caught it, and the app unmounted on every reload of that
// session. Two things are pinned: the null is drawn, and a row that throws for any other
// reason costs only itself.

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { RowBoundary } from "./RowBoundary";
import { Transcript } from "./Transcript";
import type { Item } from "../types";

afterEach(cleanup);

function goalStatus(): Item {
  return {
    kind: "tool",
    id: "t1",
    name: "goal",
    args: { action: "status" },
    callId: "c1",
    status: "ok",
    preview: '{"goal": null}',
  } as Item;
}

describe("a result with an empty field", () => {
  it("is drawn, with the empty field as a line that says so", () => {
    render(
      <Transcript
        items={[{ kind: "user", text: "do we have a goal?" }, goalStatus()]}
        running={false}
        onApprove={vi.fn()}
      />,
    );
    expect(screen.queryByTestId("tool-row-fallback")).toBeNull();
    const row = document.querySelector("[data-disclosure-row]") as HTMLElement;
    if (row.getAttribute("aria-expanded") === "false") fireEvent.click(row);
    expect(screen.getByText("None")).toBeTruthy();
    expect(screen.getByText("do we have a goal?")).toBeTruthy();
  });
});

describe("a row that cannot be drawn", () => {
  function Boom({ explode }: { explode: boolean }): JSX.Element {
    if (explode) throw new Error("presenter bug");
    return <b>drawn</b>;
  }

  it("costs that row, names the call, and leaves its neighbours standing", () => {
    const errors = vi.spyOn(console, "error").mockImplementation(() => {});
    render(
      <div>
        <p>the answer above it</p>
        <RowBoundary resetKey="c1:ok:false" title="goal">
          <Boom explode />
        </RowBoundary>
        <RowBoundary resetKey="c2:ok:false" title="read_file">
          <Boom explode={false} />
        </RowBoundary>
      </div>,
    );
    const fallback = screen.getByTestId("tool-row-fallback");
    expect(fallback.textContent).toContain("goal");
    expect(fallback.textContent).toContain("could not be drawn");
    expect(screen.getByText("the answer above it")).toBeTruthy();
    expect(screen.getByText("drawn")).toBeTruthy();
    errors.mockRestore();
  });

  it("is drawn again when its content changes", () => {
    const errors = vi.spyOn(console, "error").mockImplementation(() => {});
    const { rerender } = render(
      <RowBoundary resetKey="c1:running:false" title="goal">
        <Boom explode />
      </RowBoundary>,
    );
    expect(screen.getByTestId("tool-row-fallback")).toBeTruthy();
    rerender(
      <RowBoundary resetKey="c1:ok:false" title="goal">
        <Boom explode={false} />
      </RowBoundary>,
    );
    expect(screen.queryByTestId("tool-row-fallback")).toBeNull();
    expect(screen.getByText("drawn")).toBeTruthy();
    errors.mockRestore();
  });
});
