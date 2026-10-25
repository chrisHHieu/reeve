import { afterEach, expect, it, vi } from "vitest";
import { getHealth, Session } from "./api";

afterEach(() => vi.unstubAllGlobals());

// The launch token is what lets the GUI past the sidecar: every REST call sends it as a header,
// and a session's socket offers it as its second subprotocol (a browser socket cannot set headers).
it("sends the launch token with a REST call and with a session's socket", async () => {
  vi.stubGlobal("__REEVE_API_TOKEN__", "launch-token");
  const headers: (string | null)[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (_url: string, init?: RequestInit) => {
      headers.push(new Headers(init?.headers).get("X-Reeve-Token"));
      return { json: async () => ({ status: "ok" }) } as Response;
    }),
  );
  const opened: { url: string; protocols?: string | string[] }[] = [];
  vi.stubGlobal(
    "WebSocket",
    class {
      static readonly CONNECTING = 0;
      static readonly OPEN = 1;
      readyState = 0;
      onmessage = null;
      onopen = null;
      onclose = null;
      send = vi.fn();
      constructor(url: string, protocols?: string | string[]) {
        opened.push({ url, protocols });
      }
    },
  );

  await getHealth();
  expect(headers).toEqual(["launch-token"]);

  new Session("s1", "/workspace", { onEvent: vi.fn() });
  expect(opened.map((socket) => socket.protocols)).toEqual([["reeve", "launch-token"]]);
});
