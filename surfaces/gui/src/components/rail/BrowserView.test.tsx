// The Browser tab body is controlled: history lives in the parent tab object, so these tests pin
// what the view owns through its props — the address, what it knows of a page before framing it
// (a local server that does not answer, a site that refuses frames, a page that takes long), the
// sandbox, the page width and the menu.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import type { ComponentProps } from "react";
import { BrowserView } from "./BrowserView";
import { browserClearData, canEmbedBrowser, openExternal } from "../../desktop";
import { browserCapture, browserFrameCheck, revealDownload } from "../../api";
import { DESIGN_ATTACH_EVENT } from "./designAsks";

vi.mock("../../desktop", () => ({
  openExternal: vi.fn(),
  canEmbedBrowser: vi.fn(() => false),
  browserClearData: vi.fn(() => Promise.resolve()),
  BROWSER_PARTITION: "persist:reeve-browser",
}));
vi.mock("../../api", () => ({ browserFrameCheck: vi.fn(), browserCapture: vi.fn(), revealDownload: vi.fn() }));

const mockedOpenExternal = vi.mocked(openExternal);
const frameCheck = vi.mocked(browserFrameCheck);
const capture = vi.mocked(browserCapture);
const probe = vi.fn();

beforeEach(() => {
  frameCheck.mockResolvedValue({ ok: true, frameable: true });
  probe.mockResolvedValue(new Response(""));
  vi.stubGlobal("fetch", probe);
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

async function renderView(overrides: Partial<ComponentProps<typeof BrowserView>> = {}) {
  const props = {
    url: "",
    canGoBack: false,
    canGoForward: false,
    onNavigate: vi.fn(),
    onBack: vi.fn(),
    onForward: vi.fn(),
    onOpenTerminal: vi.fn(),
    ...overrides,
  };
  render(<BrowserView {...props} />);
  await act(async () => {});
  return props;
}

const openMenu = () => fireEvent.click(screen.getByTestId("browser-more"));

describe("BrowserView", () => {
  it("starts on a page that asks for an address", async () => {
        await renderView();
        expect(screen.queryByTestId("browser-frame")).toBeNull();
        expect(screen.getByTestId("browser-start").textContent).toContain("Type an address above, like localhost:3000.");
      });
    
      it("frames a local address that answers, sandboxed and without a referrer", async () => {
    await renderView({ url: "http://127.0.0.1:1420/" });
    expect(probe).toHaveBeenCalledWith("http://127.0.0.1:1420/", { mode: "no-cors", cache: "no-store" });
    expect(frameCheck).not.toHaveBeenCalled(); // a dev server is not asked about
    const frame = screen.getByTestId("browser-frame");
    expect(frame.getAttribute("sandbox")).toBe(
      "allow-scripts allow-forms allow-same-origin allow-popups allow-popups-to-escape-sandbox",
    );
    expect(frame.getAttribute("referrerpolicy")).toBe("no-referrer");
    expect(frame.getAttribute("src")).toBe("http://127.0.0.1:1420/");
  });

  it("says nothing runs at a local address that does not answer, with a way to start it", async () => {
    probe.mockRejectedValue(new TypeError("Failed to fetch"));
    const props = await renderView({ url: "http://localhost:5173/" });
    const refused = screen.getByTestId("browser-refused");
    expect(refused.textContent).toContain("Nothing is running on localhost:5173");
    expect(screen.queryByTestId("browser-frame")).toBeNull();
    fireEvent.click(screen.getByText("Open a terminal"));
    expect(props.onOpenTerminal).toHaveBeenCalled();
    probe.mockResolvedValue(new Response(""));
    fireEvent.click(screen.getByText("Try again"));
    await act(async () => {});
    expect(screen.getByTestId("browser-frame")).toBeTruthy();
  });

  it("names a site that refuses frames instead of leaving a blank pane", async () => {
    frameCheck.mockResolvedValue({ ok: true, frameable: false, reason: "x-frame-options" });
    await renderView({ url: "https://github.com/x" });
    expect(frameCheck).toHaveBeenCalledWith("https://github.com/x");
    expect(screen.getByTestId("browser-blocked").textContent).toContain("github.com won’t open inside Reeve");
    expect(screen.queryByTestId("browser-frame")).toBeNull();
    fireEvent.click(screen.getByText("Open in your browser"));
    expect(mockedOpenExternal).toHaveBeenCalledWith("https://github.com/x");
  });

  it("says a page takes long, and stops it on request", async () => {
    vi.useFakeTimers();
    render(<BrowserView url="https://example.com/slow" canGoBack={false} canGoForward={false} onNavigate={vi.fn()} onBack={vi.fn()} onForward={vi.fn()} />);
    await act(async () => {});
    expect(screen.getByTestId("browser-frame")).toBeTruthy();
    await act(async () => {
      vi.advanceTimersByTime(5000);
    });
    expect(screen.getByTestId("browser-slow").textContent).toContain("Still waiting for example.com");
    fireEvent.click(screen.getByText("Stop"));
    expect(screen.getByTestId("browser-stopped")).toBeTruthy();
    expect(screen.queryByTestId("browser-frame")).toBeNull();
  });

  it("commits the normalized address on Enter and on Go", async () => {
    const { onNavigate } = await renderView();
    const input = screen.getByTestId("browser-address");
    fireEvent.change(input, { target: { value: "example.com/docs" } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(onNavigate).toHaveBeenCalledWith("https://example.com/docs");
    fireEvent.change(input, { target: { value: "localhost:5173" } });
    fireEvent.click(screen.getByTestId("browser-go"));
    expect(onNavigate).toHaveBeenCalledWith("http://localhost:5173");
  });

  it("says under the box what it typed is not an address, and does not navigate", async () => {
    const { onNavigate } = await renderView();
    const input = screen.getByTestId("browser-address");
    fireEvent.change(input, { target: { value: "not a url ::" } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(onNavigate).not.toHaveBeenCalled();
    const error = screen.getByTestId("browser-error");
    expect(error.getAttribute("role")).toBe("alert");
    expect(error.textContent).toContain("“not a url ::” is not a web address");
    expect(input.getAttribute("aria-invalid")).toBe("true");

    fireEvent.change(input, { target: { value: "example.com" } });
    expect(screen.queryByTestId("browser-error")).toBeNull();
    fireEvent.keyDown(input, { key: "Enter" });
    expect(onNavigate).toHaveBeenCalledWith("https://example.com");
  });

  it("drives back/forward from the props and disables what history doesn't allow", async () => {
    const { onBack, onForward } = await renderView({ url: "https://example.com", canGoBack: true });
    fireEvent.click(screen.getByTestId("browser-back"));
    expect(onBack).toHaveBeenCalledTimes(1);
    expect((screen.getByTestId("browser-forward") as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(screen.getByTestId("browser-forward"));
    expect(onForward).not.toHaveBeenCalled();
  });

  it("runs without the sandbox only after asking, and says so while it does", async () => {
    await renderView({ url: "https://example.com" });
    openMenu();
    fireEvent.click(screen.getByTestId("browser-sandbox"));
    expect(screen.getByTestId("browser-sandbox-dialog").textContent).toContain("Run example.com without the sandbox?");
    expect(screen.getByTestId("browser-frame").hasAttribute("sandbox")).toBe(true);
    fireEvent.click(screen.getByTestId("browser-sandbox-confirm"));
    expect(screen.getByTestId("browser-frame").hasAttribute("sandbox")).toBe(false);
    expect(screen.getByTestId("browser-sandbox-warning").textContent).toContain("This page runs without the sandbox.");
    fireEvent.click(screen.getByText("Turn the sandbox back on"));
    expect(screen.getByTestId("browser-frame").hasAttribute("sandbox")).toBe(true);
  });

  it("remounts the frame on reload", async () => {
    await renderView({ url: "https://example.com" });
    const first = screen.getByTestId("browser-frame");
    fireEvent.click(screen.getByTestId("browser-reload"));
    await act(async () => {});
    expect(screen.getByTestId("browser-frame")).not.toBe(first);
  });

  it("the menu opens the page outside, sets its width, and asks Reeve about it", async () => {
    await renderView({ url: "https://example.com/x" });
    openMenu();
    fireEvent.click(screen.getByTestId("browser-external"));
    expect(mockedOpenExternal).toHaveBeenCalledWith("https://example.com/x");

    openMenu();
    fireEvent.click(screen.getByTestId("browser-width-phone"));
    expect((screen.getByTestId("browser-frame") as HTMLIFrameElement).style.width).toBe("390px");

    const asked: string[] = [];
    const listen = (event: Event) => asked.push((event as CustomEvent).detail.text);
    window.addEventListener("reeve-insert-text", listen);
    fireEvent.click(screen.getByTestId("browser-ask"));
    window.removeEventListener("reeve-insert-text", listen);
    expect(asked).toEqual(["About this page: https://example.com/x\n\n"]);
    expect(screen.getByTestId("browser-asked").textContent).toContain("Added example.com to your message.");
  });

  it("puts a picture of the page and its look in the message, and saves its HTML, through Reeve's browser", async () => {
    await renderView({ url: "https://example.com/x" });
    const attached: { name: string; kind: string }[][] = [];
    const listen = (event: Event) => attached.push((event as CustomEvent).detail.attachments);
    window.addEventListener(DESIGN_ATTACH_EVENT, listen);
    capture.mockResolvedValueOnce({ ok: true, png: "data:image/png;base64,AA" });
    await act(async () => fireEvent.click(screen.getByTestId("browser-shoot")));
    expect(capture).toHaveBeenLastCalledWith("https://example.com/x", "view", 1280);
    expect(screen.getByTestId("browser-outcome").textContent).toContain("A picture of example.com is in your message.");
    capture.mockResolvedValueOnce({ ok: true, brief: "The look of example.com: …" });
    await act(async () => fireEvent.click(screen.getByTestId("browser-brief")));
    expect(capture).toHaveBeenLastCalledWith("https://example.com/x", "brief", 1280);
    window.removeEventListener(DESIGN_ATTACH_EVENT, listen);
    expect(attached.map((list) => list.map((a) => [a.kind, a.name]))).toEqual([
      [["image", "example.com · 1280 px"]],
      [["text", "example.com · its look"]],
    ]);
    capture.mockResolvedValueOnce({ ok: true, saved: "C:/Users/someone/Downloads/example.com.html", name: "example.com.html", folder: "Downloads" });
    openMenu();
    await act(async () => fireEvent.click(screen.getByTestId("browser-save")));
    expect(screen.getByTestId("browser-outcome").textContent).toContain("Saved example.com.html to Downloads.");
    fireEvent.click(screen.getByTestId("browser-outcome-reveal"));
    expect(vi.mocked(revealDownload)).toHaveBeenCalledWith("C:/Users/someone/Downloads/example.com.html");
  });

  it("says why a page could not be read, and clears the tab's history from the menu", async () => {
    const onClearHistory = vi.fn();
    await renderView({ url: "https://example.com", canGoBack: true, onClearHistory });
    capture.mockResolvedValueOnce({ ok: false, error: "the page did not finish loading within 45 s" });
    await act(async () => fireEvent.click(screen.getByTestId("browser-shoot")));
    expect(screen.getByTestId("browser-outcome").textContent).toContain(
      "Reeve's browser could not read example.com: the page did not finish loading within 45 s.",
    );
    openMenu();
    fireEvent.click(screen.getByTestId("browser-clear-history"));
    expect(onClearHistory).toHaveBeenCalled();
  });

  it("in the desktop app opens a site that refuses frames in a webview, and the app's menu shows over it", async () => {
    // The Tauri shell's page was a native window above the app: it was hidden while a menu was open
    // and the tab went white (owner, 2026-10-20). A `<webview>` is drawn with the app.
    vi.mocked(canEmbedBrowser).mockReturnValue(true);
    try {
      const props = await renderView({ url: "https://claude.ai", tabId: "browser-1", onVisited: vi.fn() });
      // Nothing is asked of the site first, and no frame is drawn: the page is a webview.
      expect(frameCheck).not.toHaveBeenCalled();
      expect(screen.queryByTestId("browser-frame")).toBeNull();
      const view = screen.getByTestId("browser-native");
      expect(view.tagName.toLowerCase()).toBe("webview");
      expect(view.getAttribute("src")).toBe("https://claude.ai");
      expect(view.getAttribute("partition")).toBe("persist:reeve-browser");
      let at = "https://claude.ai/";
      const page = Object.assign(view, {
        getURL: vi.fn(() => at),
        loadURL: vi.fn(() => Promise.resolve()),
        reload: vi.fn(),
        capturePage: vi.fn(async () => ({ toDataURL: () => "data:image/png;base64,AAAA" })),
      });
      await act(async () => view.dispatchEvent(new Event("dom-ready")));
      // A link clicked in the page is recorded by the tab; the same address spelled again is not.
      await act(async () => view.dispatchEvent(new Event("did-stop-loading")));
      at = "https://claude.ai/login";
      await act(async () => view.dispatchEvent(new Event("did-stop-loading")));
      expect(props.onVisited).toHaveBeenCalledTimes(1);
      expect(props.onVisited).toHaveBeenCalledWith("https://claude.ai/login");
      expect(page.loadURL).not.toHaveBeenCalled();
      // The menu opens over the page, and the page stays.
      openMenu();
      expect(screen.getByTestId("browser-native")).toBe(view);
      expect(view.hidden).toBe(false);
      expect(screen.queryByTestId("browser-sandbox")).toBeNull();
      await act(async () => fireEvent.click(screen.getByTestId("browser-clear-data")));
      expect(browserClearData).toHaveBeenCalled();
      // The camera takes the page as it is seen, signed in, not a fresh load by Reeve's browser.
      await act(async () => fireEvent.click(screen.getByTestId("browser-shoot")));
      expect(page.capturePage).toHaveBeenCalled();
      expect(capture).not.toHaveBeenCalled();
    } finally {
      vi.mocked(canEmbedBrowser).mockReturnValue(false);
    }
  });
});
