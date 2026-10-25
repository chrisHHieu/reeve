// The memory screen: each project's notes as files, and, while it still holds
// anything, what an earlier version saved about the person, for them to review.
import { describe, expect, it, vi, afterEach, beforeEach } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemorySection } from "./MemorySection";

const baseSettings = { enabled: true, user_rules: "", auto_memory: true };

let userFiles: unknown[] = [];
const wmFiles = [
  { name: "MEMORY.md", title: "MEMORY", type: "", size: 12, modified_at: "2026-09-12T01:50:00Z", injected: true },
  { name: "feedback_testing.md", title: "Testing", type: "feedback", size: 20, modified_at: "2026-09-11T10:00:00Z", injected: false },
];

let calls: { url: string; init?: RequestInit }[] = [];

afterEach(cleanup);

beforeEach(() => {
  calls = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      calls.push({ url, init });
      if (url.includes("/v1/workspaces/recent"))
        return { json: async () => ({ workspaces: [{ path: "/ws", name: "ws", exists: true }] }) } as Response;
      if (url.includes("/v1/memory/auto/file"))
        return { json: async () => ({ name: "MEMORY.md", exists: true, content: "# MEMORY\ncore" }) } as Response;
      if (url.includes("/v1/memory/auto/promote"))
        return { json: async () => ({ ok: true, target: "/ws/.agents/memory/feedback_testing.md" }) } as Response;
      if (url.includes("/v1/memory/auto") && url.includes("scope=user"))
        return { json: async () => ({ files: userFiles, dir: "/data/memory" }) } as Response;
      if (url.includes("/v1/memory/auto"))
        return { json: async () => ({ files: wmFiles, dir: "/data/projects/ws/memory" }) } as Response;
      if (url.includes("/v1/memory/settings")) {
        // Echo a PUT back (merged), the way the server does.
        const merged =
          init?.method === "PUT" && init.body ? { ...baseSettings, ...JSON.parse(String(init.body)) } : baseSettings;
        return { json: async () => merged } as Response;
      }
      return { json: async () => ({ ok: true }) } as Response;
    }),
  );
});

describe("MemorySection", () => {
  it("has no switch for remembering things about the person, and no card for it when there is nothing", async () => {
    render(<MemorySection />);
    await waitFor(() => expect(screen.getByTestId("auto-memory-card")).toBeTruthy());
    expect(screen.queryByTestId("memory-toggle-card")).toBeNull();
    expect(screen.queryByText(/Remember new things about/)).toBeNull();
    // The request for what was saved earlier came back empty: nothing to review, no card.
    await waitFor(() => expect(calls.some((c) => c.url.includes("scope=user"))).toBe(true));
    expect(screen.queryByTestId("user-memory-card")).toBeNull();
  });

  it("lists auto-memory files, labels the injected index, and opens a file", async () => {
    render(<MemorySection />);
    // The card lists the agent's machine-local markdown notes (the index is flagged).
    await waitFor(() => expect(screen.getByTestId("am-file-MEMORY.md")).toBeTruthy());
    expect(screen.getByTestId("am-file-MEMORY.md").textContent).toContain("always loaded");
    expect(screen.getByTestId("am-file-feedback_testing.md")).toBeTruthy();

    // Opening a file loads its content into the editor.
    fireEvent.click(screen.getByTitle("Open MEMORY.md"));
    const editor = await screen.findByTestId("am-editor");
    expect((editor.querySelector("textarea") as HTMLTextAreaElement).value).toContain("core");
  });

  it("lists what was saved about the person earlier, for review, and says it is no longer loaded", async () => {
    userFiles = [
      { name: "MEMORY.md", title: "MEMORY", type: "", size: 40, modified_at: "", injected: true },
      { name: "role.md", title: "Product manager", type: "user", size: 80, modified_at: "", injected: false },
    ];
    try {
      render(<MemorySection />);
      const card = await screen.findByTestId("user-memory-card");
      await waitFor(() => expect(card.textContent).toContain("Product manager"));
      expect(card.textContent).toContain("Saved about you earlier");
      expect(card.textContent).toContain("no longer loaded into any conversation");
      expect(card.textContent).toContain("about you"); // the type chip
      expect(card.querySelector('[data-testid="am-workspace-input"]')).toBeNull();
      expect(card.querySelector('[data-testid="am-promote-role.md"]')).toBeNull();
      expect(calls.some((c) => c.url.includes("/v1/memory/auto?") && c.url.includes("scope=user"))).toBe(true);
    } finally {
      userFiles = [];
    }
  });

  it("promotes a topic file into the project and never offers the index", async () => {
    render(<MemorySection />);
    await waitFor(() => expect(screen.getByTestId("am-file-feedback_testing.md")).toBeTruthy());

    // MEMORY.md names machine-local files — the backend refuses it, so the UI does not
    // offer it at all.
    expect(screen.queryByTestId("am-promote-MEMORY.md")).toBeNull();

    fireEvent.click(screen.getByTestId("am-promote-feedback_testing.md"));
    await waitFor(() => {
      const post = calls.find((c) => c.url.includes("/v1/memory/auto/promote"));
      expect(post?.init?.method).toBe("POST");
      expect(JSON.parse(String(post?.init?.body))).toEqual({
        workspace: "/ws",
        name: "feedback_testing.md",
      });
    });
  });
});
