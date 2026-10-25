// Commands a person, a project or a plugin defines: they join the "/" popup
// after the app's own, with where they come from in the pill; picking one inserts
// `/name `, and sending expands the body the server returns with the typed arguments —
// the same rules as the built-in templates. The list never carries bodies; a body is
// fetched when its command is picked, or when it is sent typed.
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { Composer } from "./Composer";

const COMMANDS = [
  {
    name: "release-helper:ship",
    description: "Cut a release",
    argument_hint: "<version>",
    source: "plugin",
    plugin: "release-helper@inline",
  },
  { name: "standup", description: "My standup", argument_hint: "", source: "user" },
];
const BODIES: Record<string, string> = {
  "release-helper:ship": "Prepare release $1 from the main branch.",
  standup: "Summarize yesterday for $ARGUMENTS.",
};

function stubFetch() {
  const urls: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      urls.push(url);
      if (url.includes("/skills")) return { ok: true, json: async () => ({ skills: [] }) } as Response;
      const one = /\/v1\/commands\/([^?]+)/.exec(url);
      if (one) {
        const name = decodeURIComponent(one[1]);
        return { ok: true, json: async () => ({ name, body: BODIES[name] }) } as Response;
      }
      if (url.includes("/v1/commands")) return { ok: true, json: async () => ({ commands: COMMANDS }) } as Response;
      return { ok: true, json: async () => ({}) } as Response;
    }),
  );
  return urls;
}

type P = Parameters<typeof Composer>[0] & { onSend: ReturnType<typeof vi.fn> };
const props = (): P =>
  ({
    mode: "interactive",
    model: "m",
    models: ["m"],
    running: false,
    connected: true,
    sessionId: "s1",
    onSend: vi.fn(),
    onInterrupt: vi.fn(),
    onModeChange: vi.fn(),
    onModelChange: vi.fn(),
  }) as P;

const box = () => screen.getByPlaceholderText(/Ask anything/);
const draft = () => (box() as HTMLTextAreaElement).value;

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("Composer / defined commands", () => {
  it("lists a plugin's command with its source and argument hint, and the list has no bodies", async () => {
    const urls = stubFetch();
    render(<Composer {...props()} />);
    fireEvent.change(box(), { target: { value: "/ship" } });
    const row = await screen.findByTestId("slash-command-release-helper:ship");
    expect(row.textContent).toContain("Cut a release — <version>");
    expect(row.textContent).toContain("plugin");
    expect(urls.filter((u) => /\/v1\/commands\//.test(u))).toEqual([]);
  });

  it("picking inserts the name; sending expands the fetched body with the arguments", async () => {
    stubFetch();
    const p = props();
    render(<Composer {...p} />);
    fireEvent.change(box(), { target: { value: "/ship" } });
    fireEvent.click(await screen.findByTestId("slash-command-release-helper:ship"));
    expect(draft()).toBe("/release-helper:ship ");
    expect(p.onSend).not.toHaveBeenCalled();

    fireEvent.change(box(), { target: { value: "/release-helper:ship 1.2.0" } });
    fireEvent.keyDown(box(), { key: "Enter" });
    await waitFor(() => expect(p.onSend).toHaveBeenCalledTimes(1));
    expect(p.onSend.mock.calls[0][0]).toBe("Prepare release 1.2.0 from the main branch.");
    expect(draft()).toBe("");
  });

  it("a command typed without the popup is fetched, then sent expanded", async () => {
    stubFetch();
    const p = props();
    render(<Composer {...p} />);
    // The popup opens as "/" is typed, which is how the composer learns the names.
    fireEvent.change(box(), { target: { value: "/stand" } });
    await screen.findByTestId("slash-command-standup");
    fireEvent.change(box(), { target: { value: "/standup the platform team" } });
    fireEvent.keyDown(box(), { key: "Enter" });
    await waitFor(() => expect(p.onSend).toHaveBeenCalledTimes(1));
    expect(p.onSend.mock.calls[0][0]).toBe("Summarize yesterday for the platform team.");
  });
});
