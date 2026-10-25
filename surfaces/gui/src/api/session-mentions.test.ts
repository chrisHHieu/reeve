// P4 — the @agent names the composer recognises ride the `user_message` frame as their
// OWN field (never pasted into the text a provider reads as the person's literal line).
// The frame is what the server's mention resolver consumes; the Python suite pins the
// other half (validation, the synthetic instruction, and the `task` ask-skip).
import { afterEach, expect, it, vi } from "vitest";
import { Session } from "./automations";

afterEach(() => {
  vi.unstubAllGlobals();
});

class FakeWebSocket {
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  readyState = FakeWebSocket.OPEN;
  onmessage: ((event: MessageEvent) => void) | null = null;
  onopen: (() => void) | null = null;
  onclose: (() => void) | null = null;
  send = vi.fn();

  constructor(public readonly url: string) {}
}

it("user_message carries mentions when present and omits the field otherwise", () => {
  vi.stubGlobal("WebSocket", FakeWebSocket);
  const session = new Session("s1", "/workspace", { onEvent: vi.fn() });
  const socket = (session as unknown as { ws: FakeWebSocket }).ws;

  session.userMessage("please @reviewer check the diff", [], "gpt-5.5", undefined, [
    "reviewer",
  ]);
  expect(JSON.parse(socket.send.mock.calls[0][0])).toEqual({
    type: "user_message",
    text: "please @reviewer check the diff",
    model: "gpt-5.5",
    mentions: ["reviewer"],
  });

  session.userMessage("plain");
  expect(JSON.parse(socket.send.mock.calls[1][0])).toEqual({
    type: "user_message",
    text: "plain",
  });
});
