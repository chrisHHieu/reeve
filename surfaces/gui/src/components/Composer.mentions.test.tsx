// The composer's "@" and the agents of "/" (owner, 2026-10-13: "@" is context, handing work
// to an agent is an act).
//
// What must hold: "@" lists the session's files and folders from ONE read of the folder,
// filtered as it is typed; a pick points at the file (`@path` stays in the sentence) and
// copies nothing; a folder opens with → and is added whole; agents are offered by "/", and a
// picked agent rides the send as its OWN field, as it always has.
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { Composer } from "./Composer";
import type { SubagentAgent } from "../api";

const AGENTS: SubagentAgent[] = [
  { name: "explore", description: "Fast explorer", mode: "subagent" },
  { name: "general", description: "General purpose", mode: "subagent" },
  { name: "reviewer", description: "Reviews diffs", mode: "all", default: true },
  { name: "build", description: "The primary agent", mode: "primary" },
  { name: "secret", description: "Hidden from menus", mode: "all", hidden: true },
];

const FILES = ["README.md", "docs/API.md", "docs/export-formats.md", "src/scenes/HookScene.tsx"];

const props = (extra: Partial<Parameters<typeof Composer>[0]> = {}) => ({
  mode: "interactive",
  model: "gpt-5.6-sol",
  running: false,
  connected: true,
  sessionId: "s1",
  agents: AGENTS,
  onSend: vi.fn(),
  onInterrupt: vi.fn(),
  onModeChange: vi.fn(),
  onModelChange: vi.fn(),
  ...extra,
});

const box = () => screen.getByPlaceholderText(/Ask anything/) as HTMLTextAreaElement;

/** The server: the file index for "@", and nothing for the "/" lists. */
const stubServer = (index: { ok: boolean; files: string[]; error?: string } = { ok: true, files: FILES }) => {
  const calls: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      calls.push(String(url));
      if (/\/sessions\/s1\/files\/index$/.test(String(url)))
        return { ok: true, json: async () => ({ ...index, truncated: false }) } as Response;
      return { ok: true, json: async () => ({}) } as Response;
    }),
  );
  return calls;
};

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("Composer @ — files and folders", () => {
  it("a bare @ shows the folder at once, folders first, and no agents", async () => {
    stubServer();
    render(<Composer {...props()} />);
    fireEvent.change(box(), { target: { value: "look at @" } });
    const popup = await screen.findByTestId("mention-popup");
    await waitFor(() => expect(screen.getAllByTestId("mention-dir").length).toBe(2));
    const paths = [...popup.querySelectorAll("[data-path]")].map((row) => row.getAttribute("data-path"));
    expect(paths).toEqual(["docs/", "src/", "README.md"]);
    expect(popup.textContent).not.toContain("explore");
    expect(popup.getAttribute("aria-label")).toBe("Files and folders");
  });

  it("filters as it is typed from the one read, without asking the server again", async () => {
    const calls = stubServer();
    render(<Composer {...props()} />);
    fireEvent.change(box(), { target: { value: "@" } });
    await screen.findAllByTestId("mention-dir");
    fireEvent.change(box(), { target: { value: "@ex" } });
    fireEvent.change(box(), { target: { value: "@exp" } });
    const rows = await screen.findAllByTestId("mention-file");
    expect(rows.map((row) => row.getAttribute("data-path"))).toEqual(["docs/export-formats.md"]);
    // The match is marked in the name.
    expect(rows[0].querySelector(".menu-match")?.textContent).toBe("exp");
    expect(calls.filter((url) => url.includes("/files")).length).toBe(1);
  });

  it("a pick points at the file: the path stays in the sentence, nothing is copied", async () => {
    const calls = stubServer();
    const onSend = vi.fn();
    render(<Composer {...props({ onSend })} />);
    fireEvent.change(box(), { target: { value: "compare with @hook" } });
    fireEvent.click((await screen.findAllByTestId("mention-file"))[0]);
    expect(box().value).toBe("compare with @src/scenes/HookScene.tsx ");
    expect(screen.queryByTestId("mention-popup")).toBeNull();
    // A chip says what the message points at, and nothing was read into it.
    expect(screen.getByTestId("pointer-chip-src/scenes/HookScene.tsx").textContent).toContain("HookScene.tsx");
    expect(calls.some((url) => url.includes("/artifacts/read"))).toBe(false);
    fireEvent.keyDown(box(), { key: "Enter" });
    await waitFor(() => expect(onSend).toHaveBeenCalled());
    expect(onSend.mock.calls[0][0]).toBe("compare with @src/scenes/HookScene.tsx");
    expect(onSend.mock.calls[0][1]).toEqual([]);
  });

  it("→ opens a folder, Enter adds it whole, ← goes back up", async () => {
    stubServer();
    render(<Composer {...props()} />);
    fireEvent.change(box(), { target: { value: "read @doc" } });
    await screen.findAllByTestId("mention-dir");
    fireEvent.keyDown(box(), { key: "ArrowRight" });
    expect(box().value).toBe("read @docs/");
    const rows = await screen.findAllByTestId(/mention-(dir|file)/);
    expect(rows[0].textContent).toContain("Add all of docs/");
    expect(rows.slice(1).map((row) => row.getAttribute("data-path"))).toEqual(["docs/API.md", "docs/export-formats.md"]);
    fireEvent.keyDown(box(), { key: "ArrowLeft" });
    expect(box().value).toBe("read @");
    fireEvent.change(box(), { target: { value: "read @docs/" } });
    await screen.findAllByTestId("mention-dir");
    fireEvent.keyDown(box(), { key: "Enter" });
    expect(box().value).toBe("read @docs/ ");
  });

  it("Enter on a folder opens it, as → does: the list stays, showing what is inside", async () => {
    // It used to add the folder with a space, which closed the list; the person deleted the space
    // to see inside (owner, 2026-10-21). Adding it whole is its "Add all of" row.
    stubServer();
    render(<Composer {...props()} />);
    fireEvent.change(box(), { target: { value: "read @" } });
    await screen.findAllByTestId("mention-dir");
    fireEvent.keyDown(box(), { key: "Enter" });
    expect(box().value).toBe("read @docs/");
    const rows = await screen.findAllByTestId(/mention-(dir|file)/);
    expect(rows[0].textContent).toContain("Add all of docs/");
    fireEvent.click(screen.getAllByTestId("mention-dir").find((row) => row.getAttribute("data-path") === "docs/")!);
    expect(box().value).toBe("read @docs/ ");
  });

  it("only the row the keyboard is on wears the file's coloured logo", async () => {
    stubServer();
    render(<Composer {...props()} />);
    fireEvent.change(box(), { target: { value: "@" } });
    await screen.findAllByTestId("mention-dir");
    const rows = () => [...document.querySelectorAll("[data-at-index]")];
    expect(rows().map((row) => !!row.querySelector('[data-component="file-icon"]'))).toEqual([true, false, false]);
    fireEvent.keyDown(box(), { key: "ArrowDown" });
    expect(rows().map((row) => !!row.querySelector('[data-component="file-icon"]'))).toEqual([false, true, false]);
  });

  it("what the conversation touched comes first", async () => {
    stubServer();
    render(<Composer {...props({ touchedFiles: [{ path: "docs/API.md", what: "changed" }] })} />);
    fireEvent.change(box(), { target: { value: "@" } });
    expect((await screen.findByTestId("mention-section-touched")).textContent).toBe("In this conversation");
    const first = (await screen.findAllByTestId("mention-file"))[0];
    expect(first.getAttribute("data-path")).toBe("docs/API.md");
    expect(first.textContent).toContain("changed");
  });

  it("a session on a remote host says why it cannot list, instead of showing nothing", async () => {
    stubServer({ ok: false, files: [], error: "This session's files live on the remote host" });
    render(<Composer {...props()} />);
    fireEvent.change(box(), { target: { value: "@" } });
    expect((await screen.findByTestId("mention-remote")).textContent).toContain("Type a path");
  });

  it("does not open for a mid-word '@', and Escape leaves the token as text", async () => {
    stubServer();
    render(<Composer {...props()} />);
    fireEvent.change(box(), { target: { value: "user@example" } });
    expect(screen.queryByTestId("mention-popup")).toBeNull();
    fireEvent.change(box(), { target: { value: "@do" } });
    await screen.findAllByTestId("mention-dir");
    fireEvent.keyDown(box(), { key: "Escape" });
    expect(screen.queryByTestId("mention-popup")).toBeNull();
    expect(box().value).toBe("@do");
  });
});

describe("Composer / — agents", () => {
  it("offers only unhidden non-primary agents, under their own heading", async () => {
    stubServer();
    render(<Composer {...props()} />);
    fireEvent.change(box(), { target: { value: "/" } });
    expect((await screen.findByTestId("slash-section-agents")).textContent).toContain("Agents");
    expect(screen.getByTestId("slash-agent-explore")).toBeTruthy();
    expect(screen.getByTestId("slash-agent-reviewer")).toBeTruthy();
    expect(screen.queryByTestId("slash-agent-build")).toBeNull();
    expect(screen.queryByTestId("slash-agent-secret")).toBeNull();
  });

  it("picking one hands the message to it: the name rides the send as its own field", async () => {
    stubServer();
    const p = props();
    render(<Composer {...p} />);
    fireEvent.change(box(), { target: { value: "/rev" } });
    fireEvent.click(await screen.findByTestId("slash-agent-reviewer"));
    expect(box().value).toBe("@reviewer ");
    expect(screen.getByTestId("mention-chip-reviewer")).toBeTruthy();
    fireEvent.change(box(), { target: { value: "@reviewer check the diff" } });
    fireEvent.keyDown(box(), { key: "Enter" });
    await waitFor(() =>
      expect(p.onSend).toHaveBeenCalledWith("@reviewer check the diff", [], undefined, ["reviewer"]),
    );
  });

  it("editing the token away drops the agent from the send", async () => {
    stubServer();
    const p = props();
    render(<Composer {...p} />);
    fireEvent.change(box(), { target: { value: "@reviewer check the diff" } });
    fireEvent.change(box(), { target: { value: "check the diff" } });
    fireEvent.keyDown(box(), { key: "Enter" });
    await waitFor(() => expect(p.onSend).toHaveBeenCalledWith("check the diff", [], undefined));
  });

  it("the agent's chip removes it", async () => {
    stubServer();
    render(<Composer {...props()} />);
    fireEvent.change(box(), { target: { value: "@explore find the cache" } });
    fireEvent.click(await screen.findByLabelText("Remove @explore"));
    expect(box().value).toBe("find the cache");
    expect(screen.queryByTestId("mention-chip-explore")).toBeNull();
  });
});
