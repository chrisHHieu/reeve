// The line above the composer when the conversation's connection is down: what it says once
// the app stopped asking, and that it names a remote machine when that machine is the cause.
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { ConnectionNote } from "./ConnectionNote";

afterEach(cleanup);

describe("ConnectionNote", () => {
  it("says nothing while connected", () => {
    render(<ConnectionNote connected gaveUp={false} />);
    expect(screen.queryByTestId("connection-note")).toBeNull();
  });

  it("once the app gave up, says so and offers to ask again", () => {
    const onRetry = vi.fn();
    render(<ConnectionNote connected={false} gaveUp onRetry={onRetry} />);
    expect(screen.getByTestId("connection-note").textContent).toContain("Reeve could not open this conversation.");
    fireEvent.click(screen.getByTestId("connection-retry"));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it("names the remote machine that refused to start, which is the cause", () => {
    render(<ConnectionNote connected={false} gaveUp machine="ubuntu" />);
    expect(screen.getByTestId("connection-note").textContent).toContain("Reeve could not start on ubuntu.");
  });
});
