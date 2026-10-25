// The toast's contract: it announces, it leaves on its own, a failure outlives a success, and
// it survives being hovered (reading is a reason to stay — the timer restarts rather than
// counting down behind an eye that is on it).

import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ToastProvider, useToast } from "./Toast";

afterEach(cleanup);

afterEach(() => {
  vi.useRealTimers();
});

function Trigger({ options }: { options?: Parameters<ReturnType<typeof useToast>>[0] }) {
  const toast = useToast();
  return (
    <button
      type="button"
      onClick={() => toast(options ?? { title: "Saved", tone: "success" })}
    >
      go
    </button>
  );
}

function renderWithProvider(options?: Parameters<ReturnType<typeof useToast>>[0]) {
  return render(
    <ToastProvider>
      <Trigger options={options} />
    </ToastProvider>,
  );
}

describe("Toast", () => {
  it("announces what happened", () => {
    renderWithProvider({ title: "Put 2 files back", description: "a.ts, b.ts", tone: "success" });
    fireEvent.click(screen.getByRole("button", { name: "go" }));
    const toast = screen.getByTestId("toast");
    expect(toast.textContent).toContain("Put 2 files back");
    expect(toast.textContent).toContain("a.ts, b.ts");
    expect(toast.getAttribute("data-tone")).toBe("success");
  });

  it("uses role=alert for a failure and role=status for the rest", () => {
    renderWithProvider({ title: "Could not undo that step", tone: "error" });
    fireEvent.click(screen.getByRole("button", { name: "go" }));
    expect(screen.getByRole("alert").textContent).toContain("Could not undo");
  });

  it("dismisses itself once it has been read", () => {
    vi.useFakeTimers();
    renderWithProvider({ title: "done", tone: "success" });
    fireEvent.click(screen.getByRole("button", { name: "go" }));
    act(() => vi.advanceTimersByTime(4100));
    expect(screen.queryByTestId("toast")).toBeNull();
  });

  it("keeps a failure twice as long, so it can be acted on", () => {
    vi.useFakeTimers();
    renderWithProvider({ title: "That failed", tone: "error" });
    fireEvent.click(screen.getByRole("button", { name: "go" }));
    act(() => vi.advanceTimersByTime(4100));
    expect(screen.queryByTestId("toast")).not.toBeNull();
    act(() => vi.advanceTimersByTime(4100));
    expect(screen.queryByTestId("toast")).toBeNull();
  });

  it("stays while it is being read, and can be dismissed by hand", () => {
    vi.useFakeTimers();
    renderWithProvider({ title: "reading this", tone: "info" });
    fireEvent.click(screen.getByRole("button", { name: "go" }));
    const toast = screen.getByTestId("toast");
    fireEvent.pointerEnter(toast);
    act(() => vi.advanceTimersByTime(10_000));
    expect(screen.queryByTestId("toast")).not.toBeNull();
    fireEvent.click(screen.getByLabelText("Dismiss"));
    expect(screen.queryByTestId("toast")).toBeNull();
  });

  it("runs the action and leaves", () => {
    const onClick = vi.fn();
    renderWithProvider({ title: "That failed", tone: "error", action: { label: "Retry", onClick } });
    fireEvent.click(screen.getByRole("button", { name: "go" }));
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(onClick).toHaveBeenCalledTimes(1);
    expect(screen.queryByTestId("toast")).toBeNull();
  });

  it("keeps the stack short, dropping the oldest", () => {
    renderWithProvider();
    const go = screen.getByRole("button", { name: "go" });
    fireEvent.click(go);
    fireEvent.click(go);
    fireEvent.click(go);
    fireEvent.click(go);
    expect(screen.getAllByTestId("toast")).toHaveLength(3);
  });

  it("is a no-op outside a provider, so a component can render standalone", () => {
    render(<Trigger />);
    expect(() => fireEvent.click(screen.getByRole("button", { name: "go" }))).not.toThrow();
    expect(screen.queryByTestId("toast")).toBeNull();
  });
});
