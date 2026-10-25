// The line under a write that touched a design page: what Reeve measured, in words,
// on the live row and on a reloaded one.

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import * as api from "../api";
import { Transcript } from "./Transcript";
import { TranscriptSession } from "./session/transcriptSession";
import { itemsFromMessages } from "../itemsFromMessages";
import { pageBox } from "./rail/PageFrame";
import { DesignVersions } from "./rail/DesignVersions";
import type { DesignVersion } from "../api";
import type { DesignCheck, Item } from "../types";

afterEach(cleanup);

const user: Item = { kind: "user", text: "draw the pricing page" };
const clean: DesignCheck = { summary: "Checked at 1440, 834 and 390 px: nothing to fix", lines: [], issues: 0 };
const fix: DesignCheck = {
  summary: "Checked at 1440, 834 and 390 px: 2 to fix",
  lines: [
    "At 390 px the page is 212 px wider than the window; first past the edge: div.plans.",
    "Text under 4.5:1: p.note is #9a9a9a on #ffffff, 2.8:1.",
  ],
  issues: 2,
};

const write = (designCheck?: DesignCheck[]): Item =>
  ({
    kind: "tool",
    id: "w1",
    callId: "w1",
    name: "write_file",
    args: { path: "design/pricing.html", content: "<p>x</p>" },
    status: "ok",
    ...(designCheck ? { designCheck } : {}),
  }) as Item;

describe("the design check under a write", () => {
  it("says a clean page in one quiet line with nothing to open", () => {
    render(<Transcript items={[user, write([clean])]} onApprove={vi.fn()} />);
    expect(screen.getByTestId("design-check").textContent).toBe(clean.summary);
    expect(screen.queryByTestId("design-check-toggle")).toBeNull();
  });

  it("names how many things to fix and shows them on request", () => {
    render(<Transcript items={[user, write([fix])]} onApprove={vi.fn()} />);
    const toggle = screen.getByTestId("design-check-toggle");
    expect(toggle.textContent).toContain("2 to fix");
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    expect(screen.queryByText(/212 px wider/)).toBeNull();
    fireEvent.click(toggle);
    expect(toggle.getAttribute("aria-expanded")).toBe("true");
    expect(screen.getByText(/212 px wider/)).toBeTruthy();
  });

  it("draws nothing under a write that touched no design page", () => {
    render(<Transcript items={[user, write()]} onApprove={vi.fn()} />);
    expect(screen.queryByTestId("design-check")).toBeNull();
  });

  it("comes back with the session from the message's sidecar", () => {
    const items = itemsFromMessages([
      { role: "user", content: "draw the pricing page" },
      {
        role: "assistant",
        content: "",
        tool_calls: [{ id: "w1", type: "function", function: { name: "write_file", arguments: '{"path":"design/pricing.html","content":"x"}' } }],
      },
      { role: "tool", tool_call_id: "w1", content: "Wrote design/pricing.html", _design_check: [fix] },
    ] as never);
    const tool = items.find((item) => item.kind === "tool") as Extract<Item, { kind: "tool" }>;
    expect(tool.designCheck).toEqual([fix]);
  });
});

describe("a page seen at a chosen width", () => {
  it("is as wide as the panel until a width is chosen", () => {
    expect(pageBox("fit", { w: 600, h: 800 })).toBeNull();
  });

  it("is drawn at the width and scaled down to fit a narrower panel, centred", () => {
    expect(pageBox(1440, { w: 720, h: 800 })).toEqual({ scale: 0.5, width: 1440, height: 1600, left: 0 });
    expect(pageBox(390, { w: 600, h: 800 })).toEqual({ scale: 1, width: 390, height: 800, left: 105 });
  });
});

describe("what a write changed", () => {
  it("is listed under the check, and what nobody pointed at can be asked back", () => {
    const changed: DesignCheck = {
      summary: "Checked at 1440, 834 and 390 px: nothing to fix",
      lines: [],
      issues: 0,
      path: "design/pricing.html",
      changes: ["h1 type 28px/700 → 22px/700", "button.cta size 120×44 → 374×48"],
      unasked: ["h1 type 28px/700 → 22px/700"],
    };
    const seen: Array<{ text?: string }> = [];
    const listen = (event: Event) => seen.push((event as CustomEvent).detail);
    window.addEventListener("reeve:design-attach", listen);
    render(<Transcript items={[user, write([changed])]} onApprove={vi.fn()} />);
    fireEvent.click(screen.getByTestId("design-check-toggle"));
    expect(screen.getByTestId("design-check-changed").textContent).toContain("button.cta size 120×44 → 374×48");
    fireEvent.click(screen.getByTestId("design-check-putback"));
    window.removeEventListener("reeve:design-attach", listen);
    expect(seen[0].text).toBe("Put back what changed in design/pricing.html that I did not point at: h1 type 28px/700 → 22px/700.");
  });
});

describe("a design's versions", () => {
  it("are listed newest first, and two picked are drawn side by side with what changed", () => {
    const versions: DesignVersion[] = [
      { n: 1, summary: "Checked: 2 to fix", issues: 2, changes: [], unasked: [], thumb: "data:image/jpeg;base64,AA" },
      { n: 2, summary: "Checked: nothing to fix", issues: 0, changes: ["h1 moved"], unasked: [], thumb: "data:image/jpeg;base64,BB" },
    ];
    render(<DesignVersions versions={versions} />);
    const rows = screen.getAllByRole("button");
    expect(rows[0].textContent).toContain("v2");
    expect(screen.queryByTestId("page-versions-compare")).toBeNull();
    fireEvent.click(screen.getByTestId("page-version-1"));
    fireEvent.click(screen.getByTestId("page-version-2"));
    expect(screen.getByTestId("page-versions-compare").textContent).toContain("v1 → v2");
    expect(screen.getByTestId("page-versions-compare").textContent).toContain("h1 moved");
  });
});

describe("the card a design write leaves (owner-approved mockup, 2026-10-20)", () => {
  const shown: DesignCheck = { ...clean, path: "design/pricing.html" };
  const failed: DesignCheck = {
    summary: "Not opened: the browser exited at start (code 0)",
    lines: [],
    issues: 0,
    path: "design/pricing.html",
  };
  const edit = (designCheck: DesignCheck[]): Item =>
    ({
      kind: "tool",
      id: "e1",
      callId: "e1",
      name: "replace_in_file",
      args: { path: "design/pricing.html", old: "a", new: "b" },
      status: "ok",
      designCheck,
    }) as Item;

  it("draws the page itself, so a page the check could not open still shows", async () => {
    const grant = vi.spyOn(api, "previewGrant").mockResolvedValue({
      ok: true,
      origin: "http://127.0.0.1:9100",
      url: "http://127.0.0.1:9100/s/k1/design/pricing.html",
      key: "k1",
    });
    vi.spyOn(api, "previewRevoke").mockImplementation(() => {});
    const width = vi.spyOn(HTMLElement.prototype, "clientWidth", "get").mockReturnValue(440);
    render(
      <TranscriptSession.Provider value="s1">
        <Transcript items={[user, write([failed])]} onApprove={vi.fn()} />
      </TranscriptSession.Provider>,
    );
    const frame = await waitFor(() => {
      const found = screen.getByTestId("page-peek").querySelector("iframe");
      if (!found) throw new Error("no frame yet");
      return found;
    });
    expect(grant).toHaveBeenCalledWith("s1", "design/pricing.html", expect.anything());
    expect(frame.getAttribute("srcdoc")).toContain("/s/k1/design/pricing.html");
    expect(frame.getAttribute("tabindex")).toBe("-1");
    expect(screen.getByTestId("design-peek").textContent).toContain("pricing.html");
    expect(screen.getByTestId("design-peek").textContent).toContain("exited at start");
    width.mockRestore();
  });

  it("opens the page from the picture and from Open", () => {
    const opened: string[] = [];
    const listen = (event: Event) => opened.push((event as CustomEvent).detail.path);
    window.addEventListener("reeve-open-artifact", listen);
    render(<Transcript items={[user, write([shown])]} onApprove={vi.fn()} />);
    fireEvent.click(screen.getByTestId("design-check-open"));
    fireEvent.click(screen.getByRole("button", { name: "Open" }));
    window.removeEventListener("reeve-open-artifact", listen);
    expect(opened).toEqual(["design/pricing.html", "design/pricing.html"]);
  });

  it("gives a page one card a turn, under its latest change, and the earlier changes their words", () => {
    render(<Transcript items={[user, write([{ ...fix, path: "design/pricing.html" }]), edit([shown])]} onApprove={vi.fn()} />);
    const lines = screen.getAllByTestId("design-check");
    expect(lines).toHaveLength(2);
    // The write's line: what its check said, no picture and no Open.
    expect(lines[0].querySelector("[data-testid=design-peek]")).toBeNull();
    expect(lines[0].querySelector("[data-testid=design-check-open]")).toBeNull();
    expect(lines[0].textContent).toContain("2 to fix");
    // The edit's: the card.
    expect(lines[1].querySelector("[data-testid=design-peek]")).not.toBeNull();
  });

  it("gives the page a card again in the next turn", () => {
    const next: Item = { kind: "user", text: "make the heading shorter" };
    render(<Transcript items={[user, write([shown]), next, edit([shown])]} onApprove={vi.fn()} />);
    expect(screen.getAllByTestId("design-peek")).toHaveLength(2);
  });

  it("keeps a card for each page changed in the same turn", () => {
    const other: DesignCheck = { ...clean, path: "design/pricing.a.html" };
    render(<Transcript items={[user, write([shown]), edit([other])]} onApprove={vi.fn()} />);
    expect(screen.getAllByTestId("design-peek")).toHaveLength(2);
  });

  it("shows the check's picture where no conversation can serve the page", () => {
    render(<Transcript items={[user, write([{ ...shown, thumb: "data:image/jpeg;base64,AAAA" }])]} onApprove={vi.fn()} />);
    expect(screen.queryByTestId("page-peek")).toBeNull();
    expect(screen.getByTestId("design-peek").querySelector("img")?.getAttribute("src")).toBe("data:image/jpeg;base64,AAAA");
  });
});
