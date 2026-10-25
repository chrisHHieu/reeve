import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { declaredFiles, DeliveredFiles } from "./Deliverables";
import { OPEN_ARTIFACT_EVENT, REVEAL_ARTIFACT_EVENT } from "../Markdown";
import type { Presented } from "../../cards/types";

afterEach(cleanup);

/** A tool item as the transcript builds it: the `present` result's card. */
function presentedItem(files: { path: string; description?: string }[]): { presentation?: Presented } {
  return { presentation: { summary: "Presented 1 file", kind: "write", card: { card: "files", files } } };
}

describe("declaredFiles", () => {
  it("reads the present tool's card and keeps declaration order", () => {
    const files = declaredFiles([
      presentedItem([{ path: "hello.md", description: "Bản chào" }, { path: "docs/notes.md" }]),
    ]);
    expect(files).toEqual([
      { path: "hello.md", description: "Bản chào" },
      { path: "docs/notes.md", description: undefined },
    ]);
  });

  it("keeps one row per path: the later description wins, the first position stays", () => {
    // A model that declares a file twice is correcting itself; the reader must not get two
    // rows, and must not see the list reshuffle under the correction.
    const files = declaredFiles([
      presentedItem([{ path: "report.md", description: "Draft" }, { path: "other.md" }]),
      presentedItem([{ path: "report.md", description: "Final report" }]),
    ]);
    expect(files).toEqual([
      { path: "report.md", description: "Final report" },
      { path: "other.md", description: undefined },
    ]);
  });

  it("ignores items that declared nothing, and entries that name no file", () => {
    const files = declaredFiles([
      { presentation: { summary: "Read a file", card: { card: "read", path: "a.md", offset: 0, lines: [], total_lines: 1 } } },
      presentedItem([{ path: "   " }, { path: "ok.md" }]),
      undefined as never,
    ].filter(Boolean) as { presentation?: Presented }[]);
    expect(files).toEqual([{ path: "ok.md", description: undefined }]);
  });
});

describe("DeliveredFiles", () => {
  it("draws one full-width row for a single file, with the model's own line", () => {
    render(<DeliveredFiles files={[{ path: "notes/hello.md", description: "File markdown hello" }]} />);
    const row = screen.getByTestId("delivered-files-row");
    expect(row.getAttribute("data-single")).toBe("true");
    const card = screen.getByTestId("delivered-file-card");
    expect(card.textContent).toContain("hello.md");
    expect(card.textContent).toContain("File markdown hello");
    // The hover affordance the stylesheet swaps in is in the DOM either way (jsdom runs no
    // layout, so the CSS rule itself is not observable here — DESIGN.md carries that).
    expect(card.textContent).toContain("Preview in sidebar");
    expect(screen.queryByTestId("delivered-files-toggle")).toBeNull();
  });

  it("falls back to the file's kind when the model explained nothing", () => {
    render(<DeliveredFiles files={[{ path: "reports/deck.pptx" }]} />);
    expect(screen.getByTestId("delivered-file-card").textContent).toContain("PowerPoint");
  });

  it("opens a file through the artifact viewer and reveals it with the session's help", () => {
    const events: { type: string; path?: string; mode?: string }[] = [];
    const onOpen = (e: Event) => events.push({ type: "open", path: (e as CustomEvent).detail.path });
    const onReveal = (e: Event) =>
      events.push({ type: "reveal", path: (e as CustomEvent).detail.path, mode: (e as CustomEvent).detail.mode });
    window.addEventListener(OPEN_ARTIFACT_EVENT, onOpen);
    window.addEventListener(REVEAL_ARTIFACT_EVENT, onReveal);
    try {
      render(<DeliveredFiles files={[{ path: "notes/a.md" }]} />);
      fireEvent.click(screen.getByTestId("delivered-file-open-main"));
      fireEvent.click(screen.getByTestId("delivered-file-reveal"));
      fireEvent.click(screen.getByTestId("delivered-file-menu"));
      fireEvent.click(screen.getByRole("button", { name: /Open in default app/ }));
      expect(events).toEqual([
        { type: "open", path: "notes/a.md" },
        { type: "reveal", path: "notes/a.md", mode: "reveal" },
        { type: "reveal", path: "notes/a.md", mode: "open" },
      ]);
    } finally {
      window.removeEventListener(OPEN_ARTIFACT_EVENT, onOpen);
      window.removeEventListener(REVEAL_ARTIFACT_EVENT, onReveal);
    }
  });

  it("folds past four files behind one control, and unfolds again", () => {
    const files = Array.from({ length: 8 }, (_, i) => ({ path: `report-${i}.docx` }));
    render(<DeliveredFiles files={files} />);
    expect(screen.getAllByTestId("delivered-file-card")).toHaveLength(4);
    const toggle = screen.getByTestId("delivered-files-toggle");
    expect(toggle.textContent).toContain("All 8 files");
    fireEvent.click(toggle);
    expect(screen.getAllByTestId("delivered-file-card")).toHaveLength(8);
    fireEvent.click(screen.getByTestId("delivered-files-toggle"));
    expect(screen.getAllByTestId("delivered-file-card")).toHaveLength(4);
  });

  it("draws its menu outside the turn, so the turn cannot cut it", () => {
    // Owner-hit 2026-10-13: drawn inside the card, the menu was clipped by the turn's
    // `content-visibility: auto` and "Copy path" sat under the receipt row.
    render(<DeliveredFiles files={[{ path: "notes/a.md" }]} />);
    fireEvent.click(screen.getByTestId("delivered-file-menu"));
    const menu = screen.getByTestId("delivered-file-actions");
    expect(screen.getByTestId("delivered-file-card").contains(menu)).toBe(false);
    expect(menu.parentElement).toBe(document.body);
    expect(menu.style.position).toBe("fixed");
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByTestId("delivered-file-actions")).toBeNull();
  });

  it("copies the path the reveal and the app-launch act on", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText } });
    render(<DeliveredFiles files={[{ path: "notes/a.md" }]} />);
    fireEvent.click(screen.getByTestId("delivered-file-menu"));
    fireEvent.click(screen.getByTestId("copy-button"));
    expect(writeText).toHaveBeenCalledWith("notes/a.md");
  });
});
