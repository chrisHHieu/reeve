// A draft belongs to the conversation it was typed in. The composer used to wipe its box on
// every conversation change, so glancing at another conversation cost a half-written message;
// it now keeps the draft under the conversation it was left in and puts it back on return.
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
  onInterrupt: vi.fn(),
  onModeChange: vi.fn(),
  onModelChange: vi.fn(),
  ...extra,
});

const box = () => screen.getByPlaceholderText(/Ask anything/) as HTMLTextAreaElement;

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("Composer drafts, per conversation", () => {
  it("puts a half-written message back when you return to its conversation", async () => {
    stubFetch();
    const { rerender } = render(<Composer {...props({ resetKey: "draft-a1" })} />);
    fireEvent.change(box(), { target: { value: "a half-written thought" } });

    rerender(<Composer {...props({ resetKey: "draft-b1" })} />);
    await waitFor(() => expect(box().value).toBe(""));

    rerender(<Composer {...props({ resetKey: "draft-a1" })} />);
    await waitFor(() => expect(box().value).toBe("a half-written thought"));
  });

  it("keeps one conversation's draft out of another", async () => {
    stubFetch();
    const { rerender } = render(<Composer {...props({ resetKey: "draft-a2" })} />);
    fireEvent.change(box(), { target: { value: "for A only" } });

    rerender(<Composer {...props({ resetKey: "draft-b2" })} />);
    await waitFor(() => expect(box().value).toBe(""));
    fireEvent.change(box(), { target: { value: "for B only" } });

    rerender(<Composer {...props({ resetKey: "draft-a2" })} />);
    await waitFor(() => expect(box().value).toBe("for A only"));
    rerender(<Composer {...props({ resetKey: "draft-b2" })} />);
    await waitFor(() => expect(box().value).toBe("for B only"));
  });

  it("does not bring a SENT message back", async () => {
    stubFetch();
    const onSend = vi.fn();
    const { rerender } = render(<Composer {...props({ resetKey: "draft-a3", onSend })} />);
    fireEvent.change(box(), { target: { value: "send me" } });
    fireEvent.keyDown(box(), { key: "Enter" });
    expect(onSend).toHaveBeenCalled();
    expect(box().value).toBe("");

    rerender(<Composer {...props({ resetKey: "draft-b3", onSend })} />);
    rerender(<Composer {...props({ resetKey: "draft-a3", onSend })} />);
    await waitFor(() => expect(box().value).toBe(""));
  });

  it("keeps a prefill that arrives with the switch (the Skills doorway)", async () => {
    stubFetch();
    const { rerender } = render(<Composer {...props({ resetKey: "draft-a4" })} />);
    fireEvent.change(box(), { target: { value: "unfinished" } });
    rerender(
      <Composer {...props({ resetKey: "draft-b4", prefill: { text: "Build a skill", nonce: 7 } })} />,
    );
    await waitFor(() => expect(box().value).toBe("Build a skill"));
    rerender(<Composer {...props({ resetKey: "draft-a4" })} />);
    await waitFor(() => expect(box().value).toBe("unfinished"));
  });
});
