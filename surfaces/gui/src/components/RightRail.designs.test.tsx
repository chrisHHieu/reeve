// @vitest-environment jsdom
// Designs in the panel: a link to a design page opens the page's own tab with the
// design's tools, Designs lists the conversation's pages, and a design the agent writes while
// the panel is open comes up by itself, but never over a tab the person is reading and never
// for a reopened conversation's history.
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { RightRail } from "./RightRail";
import { OPEN_ARTIFACT_EVENT } from "./Markdown";
import type { Item } from "../types";

vi.mock("../api", async () => {
  const actual: any = await vi.importActual("../api");
  return {
    ...actual,
    getSessionChanges: vi.fn().mockResolvedValue({ is_git: false, files: [] }),
    getRoots: vi.fn().mockResolvedValue([{ path: "/ws", label: "ws", writable: true, exists: true, primary: true }]),
    getUndoableCalls: vi.fn().mockResolvedValue([]),
    readArtifact: vi.fn(async (_sid: string, path: string) =>
      path === "design"
        ? { ok: true, path, kind: "folder", entries: ["pricing.html", "pricing.a.html", "pricing.b.html"].map((name) => ({ name, dir: false })) }
        : { ok: true, path, kind: "code", content: "<h1>Plans</h1>", mtime_ms: 1 },
    ),
    previewGrant: vi.fn(async (_sid: string, path: string) => ({
      ok: true,
      origin: "http://127.0.0.1:50123",
      key: "k",
      internet: false,
      url: `http://127.0.0.1:50123/s/k/${path}`,
    })),
    previewRevoke: vi.fn(),
    getDesignVersions: vi.fn().mockResolvedValue([
      { n: 1, summary: "Checked at 1440, 834 and 390 px: nothing to fix", issues: 0, changes: [], unasked: [] },
    ]),
    getDesignFiles: vi.fn().mockResolvedValue({
      files: [
        { path: "design/pricing.html", name: "pricing.html", mtime_ms: Date.now(), checks: 2, summary: "Checked: nothing to fix", issues: 0, thumb: "data:image/jpeg;base64,AA" },
        { path: "design/pricing.a.html", name: "pricing.a.html", mtime_ms: Date.now(), checks: 1, summary: "Checked: nothing to fix", issues: 0, variant_of: "design/pricing.html", letter: "a" },
        { path: "design/pricing.b.html", name: "pricing.b.html", mtime_ms: Date.now(), checks: 1, summary: "Checked: nothing to fix", issues: 0, variant_of: "design/pricing.html", letter: "b" },
      ],
      design_md: { path: "DESIGN.md", colors: ["#6a3577"] },
    }),
  };
});

afterEach(() => {
  cleanup();
  localStorage.clear();
});

const write = (callId: string, path: string): Item =>
  ({
    kind: "tool",
    id: callId,
    callId,
    name: "write_file",
    args: { path },
    status: "ok",
    designCheck: [{ summary: "Checked: nothing to fix", lines: [], issues: 0, path }],
  }) as Item;

describe("designs in the panel", () => {
  it("opens a design link in the page's own tab, with the design's bar, not in Files", async () => {
    render(<RightRail active sessionId="s1" refreshKey={0} />);
    await act(async () => {});
    await act(async () => {
      window.dispatchEvent(new CustomEvent(OPEN_ARTIFACT_EVENT, { detail: { path: "design/pricing.html" } }));
    });
    expect(screen.getByTestId("rail-tab-design:design/pricing.html")).toBeTruthy();
    expect(screen.queryByTestId("rail-tab-files")).toBeNull();
    expect(await screen.findByTestId("design-frame")).toBeTruthy();
    expect(screen.getByTestId("design-mode-point")).toBeTruthy();
    expect((await screen.findByTestId("design-foot")).textContent).toContain("nothing to fix");
    fireEvent.click(screen.getByTestId("design-view-code"));
    expect((await screen.findByTestId("design-code")).textContent).toContain("<h1>Plans</h1>");
  });

  it("opens a page outside design/ in Files even when a check named it, and a design page in its tab", async () => {
    // A flow drawn as HTML came up as a design with its tools (owner, 2026-10-20): only a page under
    // design/ is one.
    const checked = [write("w1", "C:/ws/northwind-pricing/a-classic.html")];
    render(<RightRail active sessionId="s1" refreshKey={0} items={checked} />);
    await act(async () => {});
    await act(async () => {
      window.dispatchEvent(new CustomEvent(OPEN_ARTIFACT_EVENT, { detail: { path: "northwind-pricing/a-classic.html" } }));
    });
    expect(screen.queryByTestId("rail-tab-design:northwind-pricing/a-classic.html")).toBeNull();
    expect(screen.getByTestId("rail-tab-files")).toBeTruthy();
    await act(async () => {
      window.dispatchEvent(new CustomEvent(OPEN_ARTIFACT_EVENT, { detail: { path: "design/pricing.html" } }));
    });
    expect(screen.getByTestId("rail-tab-design:design/pricing.html")).toBeTruthy();
  });

  it("lists every page with its own card, variants after their page, and opens a card in its tab", async () => {
    render(<RightRail active sessionId="s1" refreshKey={0} />);
    await act(async () => {});
    fireEvent.click(screen.getByTestId("rail-door-designs"));
    expect(await screen.findByTestId("design-card-design/pricing.html")).toBeTruthy();
    const cards = Array.from(document.querySelectorAll("[data-testid^='design-card-design/']")).map((el) => el.getAttribute("data-testid"));
    expect(cards).toEqual(["design-card-design/pricing.html", "design-card-design/pricing.a.html", "design-card-design/pricing.b.html"]);
    expect(screen.getByTestId("design-card-design/pricing.a.html").textContent).toContain("Variant A of pricing.html");
    expect(screen.getByTestId("design-card-design-md")).toBeTruthy();
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Open design/pricing.b.html" }));
    });
    expect(screen.getByTestId("rail-tab-design:design/pricing.b.html")).toBeTruthy();
    expect(await screen.findByTestId("design-view")).toBeTruthy();
  });

  it("compares a page with its variants from its card: the page first, then each variant", async () => {
    render(<RightRail active sessionId="s1" refreshKey={0} />);
    await act(async () => {});
    fireEvent.click(screen.getByTestId("rail-door-designs"));
    const compare = await screen.findByTestId("design-card-compare-design/pricing.html");
    await act(async () => {
      fireEvent.click(compare);
    });
    expect(screen.getByTestId("rail-tab-design:design/pricing.html").getAttribute("aria-selected")).toBe("true");
    expect(await screen.findByTestId("page-variants")).toBeTruthy();
    const columns = Array.from(screen.getByTestId("page-variants").children).map((el) => el.getAttribute("data-testid"));
    expect(columns).toEqual(["page-variant-base", "page-variant-a", "page-variant-b"]);
  });

  it("brings up a design the agent writes while the panel is open, and only then", async () => {
    const history = [write("old", "design/old.html")];
    const { rerender } = render(<RightRail active sessionId="s1" refreshKey={0} items={history} mainRunning />);
    await act(async () => {});
    // A reopened conversation's history opens nothing.
    expect(screen.queryByTestId("rail-tab-design:design/old.html")).toBeNull();
    await act(async () => {
      rerender(<RightRail active sessionId="s1" refreshKey={0} items={[...history, write("w1", "design/pricing.html")]} mainRunning />);
    });
    expect(screen.getByTestId("rail-tab-design:design/pricing.html").getAttribute("aria-selected")).toBe("true");
  });

  it("does not take a tab the person is reading, and does nothing after the turn", async () => {
    const { rerender } = render(<RightRail active sessionId="s1" refreshKey={0} items={[]} mainRunning />);
    await act(async () => {});
    fireEvent.click(screen.getByTestId("rail-door-review"));
    await act(async () => {
      rerender(<RightRail active sessionId="s1" refreshKey={0} items={[write("w1", "design/pricing.html")]} mainRunning />);
    });
    expect(screen.queryByTestId("rail-tab-design:design/pricing.html")).toBeNull();
    expect(screen.getByTestId("rail-tab-review").getAttribute("aria-selected")).toBe("true");
    await act(async () => {
      rerender(<RightRail active sessionId="s1" refreshKey={0} items={[write("w1", "design/pricing.html"), write("w2", "design/b.html")]} mainRunning={false} />);
    });
    expect(screen.queryByTestId("rail-tab-design:design/b.html")).toBeNull();
  });
});
