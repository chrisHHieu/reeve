// The views added on 2026-10-11: a notebook that is read and never run, a video that is played
// from the preview door, and an HTML file run as a page with the internet off until asked.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MediaView } from "./MediaView";
import { NotebookView, tableFromHtml } from "./NotebookView";
import { holderDocument, PageFrame } from "./PageFrame";
import { previewGrant, previewRevoke, revealArtifact } from "../../api";

vi.mock("../../api", () => ({
  previewGrant: vi.fn(),
  previewRevoke: vi.fn(),
  revealArtifact: vi.fn(),
  // PageFrame lists the page's folder to find its variants.
  readArtifact: vi.fn(async () => null),
  getDesignVersions: vi.fn(async () => []),
  writeSessionEntry: vi.fn(),
}));
vi.mock("../Markdown", () => ({
  Markdown: ({ text }: { text: string }) => <div data-testid="md">{text}</div>,
}));

const grant = vi.mocked(previewGrant);
const ORIGIN = "http://127.0.0.1:50123";

beforeEach(() => {
  grant.mockImplementation(async (_session, path, options = {}) => {
    const key = options.internet ? "open-key" : "shut-key";
    return { ok: true, origin: ORIGIN, key, internet: !!options.internet, url: `${ORIGIN}/s/${key}/${path}` };
  });
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

const notebook = (cells: unknown[], extra: object = {}) =>
  JSON.stringify({ cells, metadata: { kernelspec: { display_name: "Python 3", language: "python" } }, nbformat: 4, ...extra });

describe("NotebookView", () => {
  it("shows the cells in order with the outputs saved in the file, and says it runs nothing", () => {
    render(
      <NotebookView
        text={notebook([
          { cell_type: "markdown", source: ["## Monthly totals\n", "By month."] },
          {
            cell_type: "code",
            execution_count: 1,
            source: "df.sum()",
            outputs: [
              { output_type: "stream", name: "stdout", text: ["loading\n", "done\n"] },
              { output_type: "execute_result", data: { "text/plain": "12480.5" } },
            ],
          },
        ])}
      />,
    );
    expect(screen.getByTestId("notebook").textContent).toContain("2 cells · Python 3");
    expect(screen.getByTestId("notebook").textContent).toContain("Reeve does not run cells here");
    expect(screen.getByTestId("md").textContent).toBe("## Monthly totals\nBy month.");
    const cell = screen.getByTestId("nb-code-cell");
    expect(cell.textContent).toContain("[1]");
    expect(cell.textContent).toContain("df.sum()");
    expect(cell.textContent).toContain("loading\ndone");
    expect(cell.textContent).toContain("12480.5");
  });

  it("draws a table from an output's HTML as text, and never as markup", () => {
    const html =
      '<table><thead><tr><th></th><th>amount</th></tr></thead><tbody><tr><th>2026-07</th><td>4120.0</td></tr></tbody></table>' +
      '<script>window.__ran = true</script><img src=x onerror="window.__ran = true">';
    render(<NotebookView text={notebook([{ cell_type: "code", source: "df", outputs: [{ output_type: "display_data", data: { "text/html": html, "text/plain": "fallback" } }] }])} />);
    const table = document.querySelector(".nb-table") as HTMLTableElement;
    expect(table.textContent).toContain("amount");
    expect(table.textContent).toContain("4120.0");
    expect(screen.getByTestId("nb-code-cell").querySelector("script, img")).toBeNull();
    expect((window as unknown as { __ran?: boolean }).__ran).toBeUndefined();
    expect(tableFromHtml("<p>no table</p>")).toBeNull();
  });

  it("shows a saved picture, and an error with its colour codes taken out", () => {
    render(
      <NotebookView
        text={notebook([
          { cell_type: "code", source: "plot()", outputs: [{ output_type: "display_data", data: { "image/png": "iVBORw0K\nGgo=" } }] },
          { cell_type: "code", source: "1/0", outputs: [{ output_type: "error", ename: "ZeroDivisionError", evalue: "division by zero", traceback: ["\u001b[0;31mZeroDivisionError\u001b[0m: division by zero"] }] },
        ])}
      />,
    );
    expect(document.querySelector("img.nb-img")?.getAttribute("src")).toBe("data:image/png;base64,iVBORw0KGgo=");
    expect(document.querySelector(".nb-out.is-error")?.textContent).toBe("ZeroDivisionError: division by zero");
  });

  it("says why a file that is not a notebook of this format cannot be shown", () => {
    const first = render(<NotebookView text={"{ not json"} />);
    expect(screen.getByTestId("nb-unreadable").textContent).toContain("could not be read");
    first.unmount();
    render(<NotebookView text={JSON.stringify({ worksheets: [{}], nbformat: 3 })} />);
    expect(screen.getByTestId("nb-unreadable").textContent).toContain("older format");
  });
});

describe("MediaView", () => {
  it("plays a video from the preview door, granted for that one file, and never by itself", async () => {
    render(<MediaView sessionId="s1" path="clips/demo.mp4" name="demo.mp4" kind="video" size={1024} />);
    const stage = await screen.findByTestId("fv-video");
    const video = stage.querySelector("video") as HTMLVideoElement;
    expect(grant).toHaveBeenCalledWith("s1", "clips/demo.mp4", { internet: false, single: true });
    expect(video.getAttribute("src")).toBe(`${ORIGIN}/s/shut-key/clips/demo.mp4`);
    expect(video.hasAttribute("controls")).toBe(true);
    expect(video.hasAttribute("autoplay")).toBe(false);
  });

  it("says a file it cannot play, with one way out, and hands its key back when closed", async () => {
    const view = render(<MediaView sessionId="s1" path="talk.mov" name="talk.mov" kind="video" />);
    const stage = await screen.findByTestId("fv-video");
    fireEvent.error(stage.querySelector("video") as HTMLVideoElement);
    expect(screen.getByTestId("fv-media-unplayable").textContent).toContain("This video is in a format that cannot be played here.");
    fireEvent.click(screen.getByText("Open in default app"));
    expect(revealArtifact).toHaveBeenCalledWith("s1", "talk.mov", "open");
    view.unmount();
    expect(previewRevoke).toHaveBeenCalledWith("shut-key");
  });
});

describe("PageFrame", () => {
  // What the page in our frame says. The view listens to that window alone, so the message
  // is sent as coming from it (when the test DOM has loaded it; a real browser always has).
  const said = (data: object, origin = ORIGIN) =>
    act(() => {
      const holder = screen.queryByTestId("artifact-frame") as HTMLIFrameElement | null;
      const source = holder?.contentWindow?.frames[0] ?? null;
      window.dispatchEvent(new MessageEvent("message", { data: { reevePreview: 1, ...data }, origin, source }));
    });

  it("the outer frame lets the page go nowhere but its own address until the internet is on", () => {
    const shut = holderDocument(ORIGIN, `${ORIGIN}/s/k/a.html`, 'a "page"', false);
    expect(shut).toContain(`frame-src ${ORIGIN};`);
    expect(shut).not.toContain("https:");
    expect(shut).toContain('title="a &quot;page&quot;"');
    expect(holderDocument(ORIGIN, `${ORIGIN}/s/k/a.html`, "a", true)).toContain("frame-src http: https:;");
  });

  it("frames the page from the door, sandboxed so it cannot move the app's window", async () => {
    render(<PageFrame sessionId="s1" path="site/index.html" name="index.html" />);
    const frame = (await screen.findByTestId("artifact-frame")) as HTMLIFrameElement;
    expect(frame.getAttribute("sandbox")).toBe("allow-scripts allow-same-origin allow-forms allow-modals");
    expect(frame.getAttribute("sandbox")).not.toContain("allow-top-navigation");
    expect(frame.getAttribute("sandbox")).not.toContain("allow-popups");
    expect(frame.getAttribute("srcdoc")).toContain(`src="${ORIGIN}/s/shut-key/site/index.html"`);
    expect(screen.getByTestId("page-where").textContent).toBe("site/index.html");
    expect(screen.getByTestId("page-net").textContent).toBe("Internet off");
  });

  it("shows the page the person went to and counts what the page tried to reach", async () => {
    render(<PageFrame sessionId="s1" path="site/index.html" name="index.html" />);
    await screen.findByTestId("artifact-frame");
    said({ type: "page", path: "/s/shut-key/site/pricing.html" });
    expect(screen.getByTestId("page-where").textContent).toBe("site/pricing.html");
    said({ type: "blocked", url: "https://fonts.googleapis.com/css2?family=X" });
    said({ type: "blocked", url: "https://fonts.googleapis.com/other" });
    said({ type: "blocked", url: "https://plausible.io/js/script.js" });
    said({ type: "blocked", url: "inline" });
    expect(screen.getByTestId("page-net").textContent).toBe("Internet off · 2 blocked");
    // Another page on this machine cannot speak for this one.
    said({ type: "page", path: "/s/shut-key/elsewhere.html" }, "http://localhost:3000");
    said({ type: "page", path: "/s/another-key/elsewhere.html" });
    expect(screen.getByTestId("page-where").textContent).toBe("site/pricing.html");
  });

  it("asks before the internet is turned on, naming what was tried, and stays on the page", async () => {
    render(<PageFrame sessionId="s1" path="site/index.html" name="index.html" />);
    await screen.findByTestId("artifact-frame");
    said({ type: "page", path: "/s/shut-key/site/pricing.html" });
    said({ type: "blocked", url: "https://plausible.io/js/script.js" });

    fireEvent.click(screen.getByTestId("page-net-allow"));
    const dialog = screen.getByTestId("page-net-dialog");
    expect(dialog.getAttribute("role")).toBe("alertdialog");
    expect(dialog.textContent).toContain("index.html tried to reach plausible.io.");
    expect(dialog.textContent).toContain("send data there");
    expect(grant).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByTestId("page-net-confirm"));
    await waitFor(() => expect(screen.getByTestId("page-net").textContent).toContain("Internet on for this page"));
    expect(grant).toHaveBeenLastCalledWith("s1", "site/index.html", { internet: true, single: false });
    expect(previewRevoke).toHaveBeenCalledWith("shut-key");
    const frame = screen.getByTestId("artifact-frame");
    expect(frame.getAttribute("srcdoc")).toContain(`src="${ORIGIN}/s/open-key/site/pricing.html"`);
    expect(frame.getAttribute("srcdoc")).toContain("frame-src http: https:;");

    fireEvent.click(screen.getByTestId("page-net-off"));
    await waitFor(() => expect(screen.getByTestId("page-net").textContent).toBe("Internet off"));
  });

  it("says the page on show is the saved file while the editor holds unsaved text", async () => {
    render(<PageFrame sessionId="s1" path="site/index.html" name="index.html" unsaved />);
    expect((await screen.findByTestId("page-unsaved")).textContent).toContain("This is the saved file");
  });

  it("says why when the page cannot be served, with the way to open it outside", async () => {
    grant.mockResolvedValueOnce({ ok: false, error: "this session's files are on the remote host" });
    render(<PageFrame sessionId="s1" path="site/index.html" name="index.html" />);
    expect((await screen.findByTestId("page-unavailable")).textContent).toContain("on the remote host");
    fireEvent.click(screen.getByText("Open in browser"));
    expect(revealArtifact).toHaveBeenCalledWith("s1", "site/index.html", "open");
    expect(screen.queryByTestId("artifact-frame")).toBeNull();
  });
});
