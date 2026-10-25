// A Word or PowerPoint file is drawn from its bytes into a frame that cannot run scripts.
// The two libraries are replaced here (they were tried on real files in the dev app, see
// OfficeView.tsx); what is pinned is what the view does around them.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { mendCharts, officeKind, OfficeView } from "./OfficeView";
import { previewGrant, revealArtifact } from "../../api";
import { openExternal } from "../../desktop";

vi.mock("../../api", () => ({
  previewGrant: vi.fn(),
  previewRevoke: vi.fn(),
  revealArtifact: vi.fn(),
}));
vi.mock("../../desktop", () => ({ openExternal: vi.fn() }));

const renderWord = vi.fn();
vi.mock("docx-preview", () => ({
  renderAsync: (...args: unknown[]) => renderWord(...args),
}));
const destroyDeck = vi.fn();
const previewDeck = vi.fn();
vi.mock("pptx-preview", () => ({
  init: (stage: HTMLElement) => ({
    slideCount: 4,
    preview: async (bytes: ArrayBuffer) => {
      previewDeck(bytes);
      stage.innerHTML = '<div class="pptx-preview-slide-wrapper">Slide one</div>';
    },
    destroy: destroyDeck,
  }),
}));

const grant = vi.mocked(previewGrant);
const ORIGIN = "http://127.0.0.1:50123";
const bytes = new Uint8Array([80, 75, 3, 4]).buffer;

beforeEach(() => {
  grant.mockImplementation(async (_session, path) => ({ ok: true, origin: ORIGIN, key: "k", url: `${ORIGIN}/s/k/${path}` }));
  vi.stubGlobal("fetch", vi.fn(async () => new Response(bytes)));
  renderWord.mockImplementation(async (_data: ArrayBuffer, stage: HTMLElement) => {
    stage.innerHTML =
      '<div class="docx-wrapper"><section class="docx">Page one <a href="https://example.com/x">site</a> ' +
      '<a href="javascript:alert(1)">trap</a><script>window.__ran = true</script></section><section class="docx">Page two</section></div>';
  });
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  vi.unstubAllGlobals();
});

const inside = () => (screen.getByTestId("office-frame") as HTMLIFrameElement).contentDocument as Document;

describe("OfficeView", () => {
  it("knows which files it can draw", () => {
    expect(officeKind("a.docx")).toBe("word");
    expect(officeKind("A.PPTX")).toBe("slides");
    expect(officeKind("old.doc")).toBeNull();
    expect(officeKind("old.ppt")).toBeNull();
  });

  it("draws a Word file from its own bytes, as its pages, in a frame that cannot run scripts", async () => {
    render(<OfficeView sessionId="s1" path="docs/proposal.docx" name="proposal.docx" />);
    expect(screen.getByTestId("office-wait")).toBeTruthy();
    await waitFor(() => expect(screen.getByTestId("office-count").textContent).toBe("2 pages"));

    expect(grant).toHaveBeenCalledWith("s1", "docs/proposal.docx", { internet: false, single: true });
    expect(globalThis.fetch).toHaveBeenCalledWith(`${ORIGIN}/s/k/docs/proposal.docx`);
    // The file's own bytes (a copy of them: the response hands out its own buffer).
    expect(Array.from(new Uint8Array(renderWord.mock.calls[0][0] as ArrayBuffer))).toEqual([80, 75, 3, 4]);
    const frame = screen.getByTestId("office-frame");
    expect(frame.getAttribute("sandbox")).toBe("allow-same-origin");
    expect(frame.getAttribute("sandbox")).not.toContain("allow-scripts");
    expect(inside().body.textContent).toContain("Page one");
    expect(screen.queryByTestId("office-wait")).toBeNull();
    // A Word file needs no note about what may differ: only decks get one.
    expect(screen.queryByTestId("office-note")).toBeNull();
  });

  it("takes out what could run, and sends a web link to the person's browser", async () => {
    render(<OfficeView sessionId="s1" path="proposal.docx" name="proposal.docx" />);
    await waitFor(() => expect(screen.getByTestId("office-count").textContent).toBe("2 pages"));
    const doc = inside();
    expect(doc.querySelector("script")).toBeNull();
    const links = Array.from(doc.querySelectorAll("a"));
    expect(links.map((link) => link.getAttribute("href"))).toEqual(["https://example.com/x", null]);
    fireEvent.click(links[0]);
    expect(openExternal).toHaveBeenCalledWith("https://example.com/x");
    expect((window as unknown as { __ran?: boolean }).__ran).toBeUndefined();
  });

  it("draws a deck as its slides, says what can differ, and lets go of the library when closed", async () => {
    const view = render(<OfficeView sessionId="s1" path="kickoff.pptx" name="kickoff.pptx" />);
    await waitFor(() => expect(screen.getByTestId("office-count").textContent).toBe("4 slides"));
    expect(previewDeck).toHaveBeenCalledWith(bytes);
    expect(inside().body.textContent).toContain("Slide one");
    expect(screen.getByTestId("office-note").textContent).toContain("fonts, bullets and effects can differ from PowerPoint");
    view.unmount();
    expect(destroyDeck).toHaveBeenCalled();
  });

  it("zooms by the person's choice, and back to the panel's width", async () => {
    render(<OfficeView sessionId="s1" path="proposal.docx" name="proposal.docx" />);
    await waitFor(() => expect(screen.getByTestId("office-count").textContent).toBe("2 pages"));
    expect(screen.getByTestId("office-zoom").textContent).toBe("100%");
    fireEvent.click(screen.getByTestId("office-zoom-in"));
    // (The scale itself is CSS `zoom` on the frame's body, which the test DOM does not keep;
    // it was seen on the dev app, where a 816px page fits a 576px panel at 68%.)
    expect(screen.getByTestId("office-zoom").textContent).toBe("125%");
    fireEvent.click(screen.getByTestId("office-zoom-out"));
    fireEvent.click(screen.getByTestId("office-zoom-out"));
    expect(screen.getByTestId("office-zoom").textContent).toBe("75%");
    fireEvent.click(screen.getByTestId("office-zoom"));
    expect(screen.getByTestId("office-zoom").textContent).toBe("100%");
  });

  it("an older format is not sent to a library that cannot read it", () => {
    render(<OfficeView sessionId="s1" path="old.doc" name="old.doc" />);
    expect(screen.getByTestId("office-older").textContent).toContain("older Word format");
    expect(grant).not.toHaveBeenCalled();
    fireEvent.click(screen.getByText("Open in Word"));
    expect(revealArtifact).toHaveBeenCalledWith("s1", "old.doc", "open");
  });

  it("says why a file could not be drawn, with the way to open it outside", async () => {
    renderWord.mockRejectedValueOnce(new Error("not a Word document"));
    render(<OfficeView sessionId="s1" path="broken.docx" name="broken.docx" />);
    const refusal = await screen.findByTestId("office-undrawn");
    expect(refusal.textContent).toContain("could not be drawn here: not a Word document");
    expect(screen.getByText("Open in Word")).toBeTruthy();
  });
});

describe("mendCharts", () => {
  it("makes a mark that would be invisible seen, and takes out the library's placeholder title", () => {
    const stage = document.createElement("div");
    stage.innerHTML =
      '<div _echarts_instance_="ec_1"><svg>' +
      '<path id="grid" d="M57 261L518 261" fill="none" stroke="#eee"></path>' +
      '<path id="bar" d="M81 261l105 0l0 -80l-105 0Z" fill="none"></path>' +
      '<path id="own" d="M1 1l5 0l0 -8l-5 0Z" fill="#c00"></path>' +
      '<text id="label" fill="none">Weeks</text><text id="axis" fill="#333">Design</text><text id="title">图表标题</text>' +
      "</svg></div>";
    expect(mendCharts(stage)).toBe(1);
    expect(stage.querySelector("#bar")?.getAttribute("fill")).toBe("#4472C4");
    expect(stage.querySelector("#grid")?.getAttribute("fill")).toBe("none"); // a line stays a line
    expect(stage.querySelector("#own")?.getAttribute("fill")).toBe("#c00"); // a colour of its own stays
    expect(stage.querySelector("#label")?.getAttribute("fill")).toBe("#444444");
    expect(stage.querySelector("#axis")?.getAttribute("fill")).toBe("#333");
    expect(stage.querySelector("#title")).toBeNull();
    expect(mendCharts(document.createElement("div"))).toBe(0);
  });
});
