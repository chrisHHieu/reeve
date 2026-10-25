// The composer while Reeve starts (the boot screen redesign, 2026-10-23): the box can be written in
// from the first frame. A message sent then is held, as one sent during a compaction is, and sent
// once the conversation is open; what is typed goes with the box to the conversation that opens.
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { Composer } from "./Composer";

function stubFetch() {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => ({ ok: true, json: async () => ({ skills: [] }) }) as Response),
  );
}

const props = (extra: Partial<Parameters<typeof Composer>[0]> = {}) => ({
  mode: "interactive",
  model: "gpt-5.6-sol",
  running: false,
  connected: true,
  sessionId: "s1",
  resetKey: "s1",
  onSend: vi.fn(),
  onQueue: vi.fn(),
  onInterrupt: vi.fn(),
  onModeChange: vi.fn(),
  onModelChange: vi.fn(),
  ...extra,
});

const box = () => screen.getByRole("textbox", { name: "Message" }) as HTMLTextAreaElement;

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("what is typed while Reeve starts", () => {
  it("goes with the box to the conversation that opens", async () => {
    stubFetch();
    const { rerender } = render(<Composer {...props({ resetKey: "stand-in-1", provisional: true, connected: false, holding: true })} />);
    fireEvent.change(box(), { target: { value: "typed before it opened" } });
    // The real conversation opens and the start ends in the same render.
    rerender(<Composer {...props({ resetKey: "opened-1" })} />);
    await waitFor(() => expect(box().value).toBe("typed before it opened"));
    // And it is not left behind in the stand-in either.
    rerender(<Composer {...props({ resetKey: "stand-in-1" })} />);
    await waitFor(() => expect(box().value).toBe(""));
  });

  it("leaves the opened conversation's own draft in place when nothing was typed", async () => {
    stubFetch();
    const { rerender } = render(<Composer {...props({ resetKey: "opened-2" })} />);
    fireEvent.change(box(), { target: { value: "left here yesterday" } });
    rerender(<Composer {...props({ resetKey: "elsewhere-2" })} />);
    await waitFor(() => expect(box().value).toBe(""));
    rerender(<Composer {...props({ resetKey: "stand-in-2", provisional: true })} />);
    rerender(<Composer {...props({ resetKey: "opened-2" })} />);
    await waitFor(() => expect(box().value).toBe("left here yesterday"));
  });

  it("does not follow a conversation the person switches to after the start", async () => {
    stubFetch();
    const { rerender } = render(<Composer {...props({ resetKey: "a-3" })} />);
    fireEvent.change(box(), { target: { value: "for a-3" } });
    rerender(<Composer {...props({ resetKey: "b-3" })} />);
    await waitFor(() => expect(box().value).toBe(""));
  });
});

describe("a message sent while Reeve starts", () => {
  it("is held, said as waiting for Reeve, and the send button takes it", () => {
    stubFetch();
    const p = props({ connected: false, holding: true, holdFor: "Reeve is ready" });
    render(<Composer {...p} />);
    expect(box().placeholder).toBe("Enter queues it until Reeve is ready");
    fireEvent.change(box(), { target: { value: "first thing" } });
    const send = screen.getByRole("button", { name: "Send" }) as HTMLButtonElement;
    expect(send.disabled).toBe(false);
    fireEvent.click(send);
    expect(p.onQueue).toHaveBeenCalledWith("first thing", [], undefined);
    expect(p.onSend).not.toHaveBeenCalled();
  });

  it("shows in the queue as waiting for Reeve", () => {
    stubFetch();
    render(<Composer {...props({ connected: false, holding: true, holdFor: "Reeve is ready", queued: [{ id: "q1", text: "first thing" }] })} />);
    expect(screen.getByText(/when Reeve is ready/)).toBeTruthy();
  });

  it("without a hold, is not sent over a connection that is not there", () => {
    stubFetch();
    render(<Composer {...props({ connected: false })} />);
    fireEvent.change(box(), { target: { value: "nowhere to go" } });
    expect((screen.getByRole("button", { name: "Send" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("holds the row's controls but the send while the conversation has not opened", () => {
    stubFetch();
    const { rerender } = render(<Composer {...props({ connected: false, holding: true, holdFor: "Reeve is ready", provisional: true, workspace: "/w" })} />);
    // A mode picked before the conversation opened was undone when it did: the mode is its.
    expect(screen.getByRole("button", { name: "Mode", hidden: true }).closest("[inert]")).not.toBeNull();
    expect(screen.getByRole("button", { name: "Attach", hidden: true }).closest("[inert]")).not.toBeNull();
    expect(screen.getByRole("button", { name: "Send" }).closest("[inert]")).toBeNull();
    rerender(<Composer {...props({ workspace: "/w" })} />);
    expect(screen.getByRole("button", { name: "Mode" }).closest("[inert]")).toBeNull();
  });

  it("a compaction's hold still says it waits for the compaction", () => {
    stubFetch();
    render(<Composer {...props({ holding: true })} />);
    expect(box().placeholder).toBe("Enter queues it until compacting is done");
  });
});
