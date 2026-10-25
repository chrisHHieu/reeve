import { act, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { createRowStateStore, ToolRowStateProvider, useRowOpen } from "./rowState";

const renders: Record<string, number> = {};

function Row({ id }: { id: string }) {
  const [open, toggle] = useRowOpen(id);
  renders[id] = (renders[id] ?? 0) + 1;
  return (
    <button type="button" data-testid={id} data-open={open || undefined} onClick={toggle}>
      {id}
    </button>
  );
}

describe("row expansion state", () => {
  it("opening one row re-renders that row and no other", () => {
    for (const key of Object.keys(renders)) delete renders[key];
    const ids = ["a", "b", "c", "d"];
    render(
      <ToolRowStateProvider>
        {ids.map((id) => (
          <Row key={id} id={id} />
        ))}
      </ToolRowStateProvider>,
    );
    const before = { ...renders };
    act(() => screen.getByTestId("b").click());
    expect(screen.getByTestId("b").dataset.open).toBe("true");
    expect(renders.b).toBe(before.b + 1);
    for (const id of ["a", "c", "d"]) expect(renders[id]).toBe(before[id]);
    act(() => screen.getByTestId("b").click());
    expect(screen.getByTestId("b").dataset.open).toBeUndefined();
  });

  it("a row keeps its open state across an unmount, as inside a folded group", () => {
    const { rerender } = render(
      <ToolRowStateProvider>
        <Row id="x" />
      </ToolRowStateProvider>,
    );
    act(() => screen.getByTestId("x").click());
    rerender(<ToolRowStateProvider>{null}</ToolRowStateProvider>);
    rerender(
      <ToolRowStateProvider>
        <Row id="x" />
      </ToolRowStateProvider>,
    );
    expect(screen.getByTestId("x").dataset.open).toBe("true");
  });

  it("without a provider a row keeps its own state", () => {
    render(<Row id="solo" />);
    act(() => screen.getByTestId("solo").click());
    expect(screen.getByTestId("solo").dataset.open).toBe("true");
  });

  it("keeps what a finish needs across the remount a finish causes, and the person's say", () => {
    const store = createRowStateStore();
    // Watched running, then the row is remounted elsewhere (into a group): the fact is the
    // store's, so the new mount still handles the finish, and only once.
    store.sawRunning("c1");
    expect(store.takeFinish("c1")).toBe(true);
    expect(store.takeFinish("c1")).toBe(false);
    expect(store.takeFinish("never-ran")).toBe(false);
    // The harness opens and folds; once the person has touched a row it leaves it alone.
    store.setByHarness("c1", true);
    expect(store.isOpen("c1")).toBe(true);
    store.toggle("c1");
    store.setByHarness("c1", true);
    expect(store.isOpen("c1")).toBe(false);
  });
});
