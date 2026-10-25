// One open file: which controls a file gets, that nothing typed is dropped without asking, and
// that its menu is drawn outside the head that used to clip it.
import { createRef } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ComponentProps } from "react";
import type { ArtifactContent } from "../../api";
import { FilePreview, type FilePreviewHandle } from "./FilePreview";
import { readDraft, writeDraft } from "./fileKinds";
import { revealArtifact, writeSessionEntry } from "../../api";

vi.mock("../../api", () => ({
  revealArtifact: vi.fn(),
  writeSessionEntry: vi.fn(),
  previewGrant: vi.fn(async (_session: string, path: string) => ({
    ok: true,
    origin: "http://127.0.0.1:50123",
    key: "k",
    internet: false,
    url: `http://127.0.0.1:50123/s/k/${path}`,
  })),
  previewRevoke: vi.fn(),
  // PageFrame lists the page's folder to find its variants.
  readArtifact: vi.fn(async () => null),
  getDesignVersions: vi.fn(async () => []),
}));
vi.mock("../Markdown", () => ({
  Markdown: ({ text }: { text: string }) => <div data-testid="md">{text}</div>,
}));

const write = vi.mocked(writeSessionEntry);

const textFile = (path: string, content: string, extra: Partial<ArtifactContent> = {}): ArtifactContent => ({
  ok: true,
  path,
  kind: "text",
  content,
  size: content.length,
  mtime_ms: 5,
  ...extra,
});

function show(path: string, content: ArtifactContent | null, overrides: Partial<ComponentProps<typeof FilePreview>> = {}) {
  const ref = createRef<FilePreviewHandle>();
  const props = {
    sessionId: "s1",
    info: { path, name: path.split("/").pop() || path, kind: "text", size: 0, modified_at: 0, origin: "files" },
    content,
    onReload: vi.fn(async () => {}),
    onClose: vi.fn(),
    onOpenEntry: vi.fn(),
    onAction: vi.fn(),
    ...overrides,
  } as ComponentProps<typeof FilePreview>;
  const view = render(<FilePreview ref={ref} {...props} />);
  return { ...view, props, ref };
}

beforeEach(() => {
  localStorage.clear();
  write.mockResolvedValue({ ok: true, path: "x" } as never);
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  for (const path of ["main.py", "notes.md", "logo.svg", "big.txt"]) writeDraft("s1", path, null);
});

describe("FilePreview", () => {
  it("a code file has no Preview: it is read as itself, with one Edit button", () => {
    show("main.py", textFile("main.py", "x = 1\n"));
    expect(screen.queryByTestId("fm-mode-preview")).toBeNull();
    expect(screen.queryByTestId("fm-mode-edit")).toBeNull();
    expect(screen.getByTestId("fm-edit").textContent).toContain("Edit");
    expect(screen.getByTestId("artifact-body").textContent).toContain("x = 1");
    expect(screen.queryByTestId("fm-editor")).toBeNull();
  });

  it("Edit opens the editor; Save writes the file and comes back out", async () => {
    show("main.py", textFile("main.py", "x = 1\n"));
    fireEvent.click(screen.getByTestId("fm-edit"));
    const editor = screen.getByTestId("fm-editor") as HTMLTextAreaElement;
    fireEvent.change(editor, { target: { value: "x = 2\n" } });
    expect(screen.getByTestId("fm-file-state").textContent).toContain("Unsaved");
    fireEvent.click(screen.getByTestId("fm-save"));
    await waitFor(() => expect(write).toHaveBeenCalledWith("s1", "main.py", "x = 2\n", 5, undefined));
    await waitFor(() => expect(screen.queryByTestId("fm-editor")).toBeNull());
    expect(readDraft("s1", "main.py")).toBeNull();
  });

  it("Cancel with unsaved edits asks; Discard drops them, Keep editing does not", () => {
    show("main.py", textFile("main.py", "x = 1\n"));
    fireEvent.click(screen.getByTestId("fm-edit"));
    fireEvent.change(screen.getByTestId("fm-editor"), { target: { value: "x = 2\n" } });

    fireEvent.click(screen.getByTestId("fm-cancel"));
    expect(screen.getByTestId("fm-leave-dialog").textContent).toContain("Save your changes to main.py?");
    fireEvent.click(screen.getByTestId("fm-leave-keep"));
    expect((screen.getByTestId("fm-editor") as HTMLTextAreaElement).value).toBe("x = 2\n");

    fireEvent.click(screen.getByTestId("fm-cancel"));
    fireEvent.click(screen.getByTestId("fm-leave-discard"));
    expect(screen.queryByTestId("fm-editor")).toBeNull();
    expect(screen.getByTestId("artifact-body").textContent).toContain("x = 1");
    expect(readDraft("s1", "main.py")).toBeNull();
    expect(write).not.toHaveBeenCalled();
  });

  it("Cancel with nothing typed just leaves the editor", () => {
    show("main.py", textFile("main.py", "x = 1\n"));
    fireEvent.click(screen.getByTestId("fm-edit"));
    fireEvent.click(screen.getByTestId("fm-cancel"));
    expect(screen.queryByTestId("fm-leave-dialog")).toBeNull();
    expect(screen.queryByTestId("fm-editor")).toBeNull();
  });

  it("closing a file with unsaved edits asks first, and Save saves before it closes", async () => {
    const { props } = show("main.py", textFile("main.py", "x = 1\n"));
    fireEvent.click(screen.getByTestId("fm-edit"));
    fireEvent.change(screen.getByTestId("fm-editor"), { target: { value: "x = 3\n" } });
    fireEvent.click(screen.getByTestId("artifact-close"));
    expect(props.onClose).not.toHaveBeenCalled();
    fireEvent.click(screen.getByTestId("fm-leave-save"));
    await waitFor(() => expect(write).toHaveBeenCalledWith("s1", "main.py", "x = 3\n", 5, undefined));
    await waitFor(() => expect(props.onClose).toHaveBeenCalledTimes(1));
  });

  it("another file can only be opened over unsaved edits through the same question", () => {
    const { ref } = show("main.py", textFile("main.py", "x = 1\n"));
    const next = vi.fn();
    act(() => ref.current?.requestLeave(next));
    expect(next).toHaveBeenCalledTimes(1); // nothing typed: at once

    fireEvent.click(screen.getByTestId("fm-edit"));
    fireEvent.change(screen.getByTestId("fm-editor"), { target: { value: "x = 9\n" } });
    act(() => ref.current?.requestLeave(next));
    expect(next).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByTestId("fm-leave-discard"));
    expect(next).toHaveBeenCalledTimes(2);
  });

  it("a draft outlives the tab: the file comes back in its editor, still unsaved", () => {
    const first = show("main.py", textFile("main.py", "x = 1\n"));
    fireEvent.click(screen.getByTestId("fm-edit"));
    fireEvent.change(screen.getByTestId("fm-editor"), { target: { value: "x = 7\n" } });
    first.unmount();

    show("main.py", textFile("main.py", "x = 1\n"));
    expect((screen.getByTestId("fm-editor") as HTMLTextAreaElement).value).toBe("x = 7\n");
    expect(screen.getByTestId("fm-file-state").textContent).toContain("Unsaved");
  });

  it("Markdown has two sides, and Preview draws what is typed before it is saved", () => {
    show("notes.md", textFile("notes.md", "# Saved", { kind: "markdown" }));
    expect(screen.getByTestId("fm-mode-preview").getAttribute("aria-pressed")).toBe("true");
    expect(screen.queryByTestId("fm-edit")).toBeNull();
    expect(screen.getByTestId("md").textContent).toBe("# Saved");

    fireEvent.click(screen.getByTestId("fm-mode-edit"));
    fireEvent.change(screen.getByTestId("fm-editor"), { target: { value: "# Typed" } });
    fireEvent.click(screen.getByTestId("fm-mode-preview"));
    expect(screen.getByTestId("md").textContent).toBe("# Typed");
    expect(screen.getByTestId("fm-file-state").textContent).toContain("Unsaved");
  });

  it("an SVG is drawn, as an image that cannot run a script", () => {
    show("logo.svg", textFile("logo.svg", '<svg xmlns="http://www.w3.org/2000/svg"><rect/></svg>'));
    const img = screen.getByTestId("fv-svg").querySelector("img") as HTMLImageElement;
    expect(img.getAttribute("src")).toMatch(/^data:image\/svg\+xml;charset=utf-8,%3Csvg/);
    expect(screen.getByTestId("fm-mode-preview")).toBeTruthy();
    fireEvent.click(screen.getByTestId("fm-mode-edit"));
    expect((screen.getByTestId("fm-editor") as HTMLTextAreaElement).value).toContain("<svg");
  });

  it("the menu is drawn outside the head, and acts on this file", () => {
    const { props } = show("main.py", textFile("main.py", "x = 1\n"));
    expect(screen.queryByTestId("artifact-menu")).toBeNull();
    fireEvent.click(screen.getByTestId("artifact-more"));
    const menu = screen.getByTestId("artifact-menu");
    // Its parent is the document, not the head whose overflow clipped the old menu.
    expect(menu.parentElement).toBe(document.body);
    expect(menu.closest(".rev-file-head")).toBeNull();

    fireEvent.click(screen.getByTestId("fv-rename"));
    expect(props.onAction).toHaveBeenCalledWith("rename");
    expect(screen.queryByTestId("artifact-menu")).toBeNull();

    fireEvent.click(screen.getByTestId("artifact-more"));
    fireEvent.click(screen.getByTestId("fv-delete"));
    expect(props.onAction).toHaveBeenCalledWith("delete");

    fireEvent.click(screen.getByTestId("artifact-more"));
    fireEvent.click(screen.getByTestId("artifact-reveal"));
    expect(revealArtifact).toHaveBeenCalledWith("s1", "main.py", "reveal");
  });

  it("Escape closes the menu and hands focus back to its button", () => {
    show("main.py", textFile("main.py", "x = 1\n"));
    const more = screen.getByTestId("artifact-more");
    fireEvent.click(more);
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByTestId("artifact-menu")).toBeNull();
    expect(document.activeElement).toBe(more);
  });

  it("a read-only folder gets a viewer: no editor, no rename, no delete", () => {
    show("main.py", textFile("main.py", "x = 1\n"), { readOnly: true });
    expect(screen.queryByTestId("fm-edit")).toBeNull();
    fireEvent.click(screen.getByTestId("artifact-more"));
    expect(screen.queryByTestId("fv-rename")).toBeNull();
    expect(screen.queryByTestId("fv-delete")).toBeNull();
    expect(screen.getByTestId("artifact-copy-path")).toBeTruthy();
  });

  it("a file cut at the read ceiling is shown and never offered for editing", () => {
    show("big.txt", textFile("big.txt", "start", { truncated: true }));
    expect(screen.queryByTestId("fm-edit")).toBeNull();
    expect(screen.getByTestId("fv-truncated").textContent).toContain("Only the start of this file");
  });

  it("shows the shape of a page while the file is read, and says why when it cannot be", () => {
    const { rerender, props } = show("main.py", null);
    expect(screen.getByTestId("fv-skeleton")).toBeTruthy();
    expect(screen.getByTestId("artifact-body").getAttribute("aria-busy")).toBe("true");
    rerender(<FilePreview {...props} content={{ ok: false, path: "main.py", kind: "text", error: "binary file cannot be previewed" }} />);
    expect(screen.getByTestId("fv-unreadable").textContent).toContain("binary file cannot be previewed");
  });
});

describe("FilePreview: what a text file was read as", () => {
  const note = (encoding: string) =>
    textFile("notes.txt", "Họp thứ Hai.\n", { encoding, encodings: ["utf-8", "cp1258", "cp1252"] });

  afterEach(() => writeDraft("s1", "notes.txt", null));

  it("says nothing about UTF-8, and names anything else", () => {
    const first = show("notes.txt", note("utf-8"));
    expect(screen.queryByTestId("fv-encoding")).toBeNull();
    first.unmount();
    show("notes.txt", note("cp1258"));
    expect(screen.getByTestId("fv-encoding").textContent).toContain("Read as Windows-1258 (Vietnamese). Saving keeps it.");
  });

  it("the person can name the encoding: the file is read again as that", () => {
    const { props } = show("notes.txt", note("cp1250"));
    fireEvent.click(screen.getByTestId("fv-encoding-change"));
    expect(screen.getByTestId("fv-encoding-menu").parentElement).toBe(document.body);
    fireEvent.click(screen.getByTestId("fv-encoding-cp1258"));
    expect(props.onReload).toHaveBeenCalledWith("cp1258");
  });

  it("saves in the encoding the file was shown in", async () => {
    show("notes.txt", note("cp1258"));
    fireEvent.click(screen.getByTestId("fm-edit"));
    fireEvent.change(screen.getByTestId("fm-editor"), { target: { value: "Họp thứ Ba.\n" } });
    fireEvent.click(screen.getByTestId("fm-save"));
    await waitFor(() => expect(write).toHaveBeenCalledWith("s1", "notes.txt", "Họp thứ Ba.\n", 5, "cp1258"));
  });
});

describe("FilePreview: the kinds added on 2026-10-11", () => {
  it("an HTML file is run as a page from its folder, never as text in a sealed box", async () => {
    show("site/index.html", textFile("site/index.html", "<h1>Home</h1>", { kind: "html" }));
    const frame = await screen.findByTestId("artifact-frame");
    expect(frame.getAttribute("srcdoc")).toContain('src="http://127.0.0.1:50123/s/k/site/index.html"');
    expect(frame.getAttribute("srcdoc")).not.toContain("<h1>Home</h1>");
    expect(screen.getByTestId("fm-mode-preview")).toBeTruthy();
  });

  it("a notebook has a Preview, and a video is played with no editor offered", async () => {
    const first = show("analysis.ipynb", textFile("analysis.ipynb", JSON.stringify({ cells: [], nbformat: 4 })));
    expect(screen.getByTestId("notebook").textContent).toContain("0 cells");
    expect(screen.getByTestId("fm-mode-preview")).toBeTruthy();
    first.unmount();

    show("demo.mp4", { ok: true, path: "demo.mp4", kind: "video", size: 2048 });
    expect(await screen.findByTestId("fv-video")).toBeTruthy();
    expect(screen.queryByTestId("fm-edit")).toBeNull();
    expect(screen.queryByTestId("fm-mode-edit")).toBeNull();
    fireEvent.click(screen.getByTestId("artifact-more"));
    expect(screen.getByTestId("artifact-open-app")).toBeTruthy();
  });
});
