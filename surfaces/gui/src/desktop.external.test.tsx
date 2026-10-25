// A link that leaves the app. In the desktop app a click on a link in an answer did nothing —
// the webview ignores `window.open` and a `target="_blank"` click — while the same click opened
// a tab at localhost:1420 (owner report, 2026-10-15). The shell's opener plugin plus this router
// are the fix, and the renderers keep `target="_blank"` for browser dev. The shell is faked as
// `window.reeveDesktop`, as in DesktopShellBridge.test.tsx.
//
// Every external click leaves the app through the OS browser (owner, 2026-10-15): the app's own
// Browser tab is a localhost/dev-server pane, not a place to read the web, so nothing is routed
// into it and a click is never kept.

import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import type { MouseEvent as ReactMouseEvent } from "react";
import { installExternalLinkRouter, openExternal } from "./desktop";

let opener: ReturnType<typeof vi.fn>;

/** A real click on a real element, returned so the test can read `defaultPrevented`. */
const click = (el: Element, init: MouseEventInit = { button: 0 }) => {
  const event = new MouseEvent(init.button === 1 ? "auxclick" : "click", {
    bubbles: true,
    cancelable: true,
    ...init,
  });
  el.dispatchEvent(event);
  return event;
};

afterEach(() => {
  cleanup();
  delete (globalThis as any).reeveDesktop;
  vi.restoreAllMocks();
});

// FIRST on purpose: nothing has attached to `document` yet, so this is the browser's real
// starting state.
describe("in a browser", () => {
  it("installs nothing: a link keeps its own target=_blank behaviour", () => {
    installExternalLinkRouter();

    render(<a href="https://example.com">the docs</a>);
    const event = click(screen.getByText("the docs"));
    expect(event.defaultPrevented).toBe(false);
  });

  it("opens through window.open, which in a browser is the right thing", () => {
    const tab = vi.spyOn(window, "open").mockReturnValue(null);

    openExternal("https://example.com/z");
    expect(tab).toHaveBeenCalledWith("https://example.com/z", "_blank", "noopener,noreferrer");
  });
});

// Everything below runs in the desktop shell, where the router IS installed. It is installed
// once — main.tsx calls it once at boot — so `document` keeps one listener and each test only
// swaps the shell behind it.
describe("in the desktop shell", () => {
  beforeAll(() => {
    // The router only asks whether the shell is there; the tests below put a recorder in it.
    (globalThis as any).reeveDesktop = { invoke: async () => null, on: () => () => {} };
    installExternalLinkRouter();
  });

  const shell = () => {
    opener = vi.fn(async () => {});
    (globalThis as any).reeveDesktop = {
      invoke: (cmd: string, args?: { url?: string }) => (cmd === "open_external" ? opener(args?.url) : Promise.resolve(null)),
      on: () => () => {},
    };
  };

  it("hands an http link to the OS browser, and stops the webview", async () => {
    shell();
    render(<a href="https://example.com">the docs</a>);
    const event = click(screen.getByText("the docs"));

    await waitFor(() => expect(opener).toHaveBeenCalledWith("https://example.com/"));
    expect(event.defaultPrevented).toBe(true);
  });

  it("routes a click that landed on the link's own text, not the anchor", async () => {
    shell();
    render(
      <a href="https://example.com/a">
        <span>named</span>
      </a>,
    );
    click(screen.getByText("named"));
    await waitFor(() => expect(opener).toHaveBeenCalledWith("https://example.com/a"));
  });

  it("takes a mailto link too — the one kind whose default app is not a browser", () => {
    shell();
    render(<a href="mailto:ops@example.com">mail us</a>);
    click(screen.getByText("mail us"));
    expect(opener).toHaveBeenCalledWith("mailto:ops@example.com");
  });

  it("takes a middle click as well: the webview ignores its new-window request too", () => {
    shell();
    render(<a href="https://example.com">the docs</a>);
    click(screen.getByText("the docs"), { button: 1 });
    expect(opener).toHaveBeenCalledWith("https://example.com/");
  });

  it("leaves a link that handled its own click alone", () => {
    // A file mention or a citation chip draws as a button, and a link renderer that handled the
    // click itself called preventDefault by the time we run (React's root container is below).
    shell();
    const onLink = vi.fn((e: ReactMouseEvent) => e.preventDefault());
    render(
      <a href="https://example.com" onClick={onLink}>
        ours
      </a>,
    );
    const event = click(screen.getByText("ours"));
    expect(onLink).toHaveBeenCalled();
    expect(opener).not.toHaveBeenCalled();
    expect(event.defaultPrevented).toBe(true);
  });

  it("keeps its hands off a relative or artifact link", () => {
    shell();
    render(
      <>
        <a href="/settings">settings</a>
        <a href="artifact:reports/x.html">a report</a>
      </>,
    );
    const relative = click(screen.getByText("settings"));
    const artifact = click(screen.getByText("a report"));
    expect(opener).not.toHaveBeenCalled();
    expect(relative.defaultPrevented).toBe(false);
    expect(artifact.defaultPrevented).toBe(false);
  });

  it("falls back to a tab when the shell refuses the URL", async () => {
    shell();
    opener.mockRejectedValueOnce(new Error("opener: URL not allowed"));
    const tab = vi.spyOn(window, "open").mockReturnValue(null);

    openExternal("https://example.com/x");
    await waitFor(() =>
      expect(tab).toHaveBeenCalledWith("https://example.com/x", "_blank", "noopener,noreferrer"),
    );
  });
});
