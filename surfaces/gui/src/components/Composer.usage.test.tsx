// @vitest-environment jsdom
// The usage chip's popover: the context-makeup panel (context_breakdown sidecar —
// the "Context windows" panel) and the average cache hit rate. The makeup
// section is HIDDEN when the server sent no breakdown (older servers) and the
// cache-hit row is computed from what always exists (the billing-class sums).
// The panel is height-capped (viewport-aware) with a scrolling body; per-model
// rows are compact and expand on click, with only the current model open.
import type { ComponentProps } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { Composer } from "./Composer";
import type { SessionUsage } from "../types";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

type ComposerProps = ComponentProps<typeof Composer>;

const props = (usage: SessionUsage, over: Partial<ComposerProps> = {}): ComposerProps => ({
  mode: "interactive",
  model: "gpt-5.6-sol",
  running: false,
  connected: true,
  onSend: vi.fn(),
  onInterrupt: vi.fn(),
  onModeChange: vi.fn(),
  onModelChange: vi.fn(),
  usage,
  contextWindow: 1_000_000,
  ...over,
});

const usage = (over: Partial<SessionUsage> = {}): SessionUsage => ({
  byModel: {
    "gpt-5.6-sol": {
      model: "gpt-5.6-sol",
      input: 100,
      output: 20,
      cache_read: 50,
      cache_write: 10,
    },
  },
  context: 180,
  prompt: 160,
  breakdown: { messages: 600, mcp_tools: 100, skills: 50 },
  ...over,
});

const openPopover = (u: SessionUsage, over: Partial<ComposerProps> = {}) => {
  render(<Composer {...props(u, over)} />);
  fireEvent.click(screen.getByTestId("usage-chip"));
};

describe("Composer / usage popover", () => {
  it("shows the makeup panel reconciled to the real prompt", () => {
    openPopover(usage());
    expect(screen.getByTestId("context-makeup")).toBeTruthy();
    // The estimate (750) exceeds the real prompt (160), so it scales down; ratios hold.
    expect(screen.getByText("Messages")).toBeTruthy();
    expect(screen.getByText("80.0%")).toBeTruthy();
    expect(screen.getByText("MCP tools")).toBeTruthy();
    expect(screen.getByText("13.3%")).toBeTruthy();
    expect(screen.getByText("Skills")).toBeTruthy();
    expect(screen.getByText("6.7%")).toBeTruthy();
    // Zero rows and categories the server never sent are not drawn.
    expect(screen.queryByText("System tools")).toBeNull();
    expect(screen.queryByText("Meta context")).toBeNull();
  });

  it("shows the uncategorized remainder as Other", () => {
    // Estimates (60+20=80) undershoot the real prompt (160) → the gap is "Other".
    openPopover(usage({ prompt: 160, breakdown: { messages: 60, system_tools: 20 } }));
    expect(screen.getByText("Other")).toBeTruthy();
  });

  it("measures the context against where the session compacts, and names the model's limit", () => {
    // Session b3a8491e-9db: 381k in a 1M model whose auto cap compacts at 380k read
    // "381k of 1M · 38%" a moment before the compaction.
    openPopover(usage({ context: 381_000 }), { compactAt: 380_000, contextBar: true });
    expect(screen.getByTestId("context-gauge-used").textContent).toBe("381k used · auto-compacts on the next step");
    expect(screen.getByTestId("context-gauge-limit").textContent).toBe("Auto-compacts at 380k · model max 1M");
    expect(screen.getByTestId("context-gauge").getAttribute("data-level")).toBe("soon");
    expect(screen.getByTestId("usage-chip").getAttribute("title")).toContain(
      "Context 381k of 380k before auto-compact (100%)",
    );
  });

  it("says how much room is left before the auto-compact, calm while there is plenty", () => {
    // The owner's screen: "222k of 1M · 22%" in a session that compacts at 380k.
    openPopover(usage({ context: 222_000 }), { compactAt: 380_000 });
    expect(screen.getByTestId("context-gauge-pct").textContent).toBe("58%");
    expect(screen.getByTestId("context-gauge-used").textContent).toBe("222k used · 158k until auto-compact");
    expect(screen.getByTestId("context-gauge").getAttribute("data-level")).toBe("ok");
    expect(screen.getByTestId("usage-popover").textContent).not.toContain("of 1M");
  });

  it("falls back to the model window when the server has not said where it compacts", () => {
    openPopover(usage({ context: 250_000 }));
    expect(screen.getByTestId("context-gauge-used").textContent).toBe("250k of 1M · 25%");
    expect(screen.queryByTestId("context-gauge-limit")).toBeNull();
  });

  it("hides the makeup section entirely on an older server (no breakdown)", () => {
    openPopover(usage({ breakdown: undefined }));
    expect(screen.queryByTestId("context-makeup")).toBeNull();
  });

  it("lists what fills the window largest first, each with a bar as long as its share", () => {
    // They were seven dots in stepped blues, in a fixed order: the colours said nothing of
    // which part is large. The length of a bar does.
    openPopover(usage({ prompt: 1000, breakdown: { skills: 100, messages: 600, mcp_tools: 300 } }));
    const rows = screen.getAllByTestId("context-makeup-row");
    expect(rows.map((row) => row.textContent)).toEqual(["Messages60.0%", "MCP tools30.0%", "Skills10.0%"]);
    expect(rows.map((row) => (row.querySelector(".usage-fill-bar i") as HTMLElement).style.width)).toEqual(["60%", "30%", "10%"]);
    // No colour is assigned per category any more.
    expect(screen.getByTestId("context-makeup").querySelector("[style*='background']")).toBeNull();
    // Sentence-case labels, not tracked capitals.
    expect(screen.getByText("What fills it")).toBeTruthy();
  });

  it("writes the percentage beside the ring from 70%, so getting full is not said by colour alone", () => {
    const at = (context: number) => {
      cleanup();
      render(<Composer {...props(usage({ context }), { compactAt: 380_000, contextBar: true })} />);
      const chip = screen.getByTestId("usage-chip");
      return { level: chip.getAttribute("data-level"), number: screen.queryByTestId("usage-chip-pct")?.textContent ?? null };
    };
    expect(at(222_000)).toEqual({ level: "ok", number: null }); // 58%: the ring alone
    expect(at(262_000)).toEqual({ level: "ok", number: null }); // 69%
    expect(at(266_000)).toEqual({ level: "near", number: "70%" });
    expect(at(357_000)).toEqual({ level: "soon", number: "94%" });
    expect(at(400_000)).toEqual({ level: "soon", number: "100%" });
  });

  it("writes no number against the model's whole window: nothing is near without a compaction point", () => {
    render(<Composer {...props(usage({ context: 950_000 }), { contextBar: true })} />);
    expect(screen.getByTestId("usage-chip").getAttribute("data-level")).toBe("ok");
    expect(screen.queryByTestId("usage-chip-pct")).toBeNull();
  });

  it("says Compacting on the ring while the conversation is being compacted", () => {
    render(<Composer {...props(usage({ context: 357_000 }), { compactAt: 380_000, contextBar: true, compacting: true })} />);
    const chip = screen.getByTestId("usage-chip");
    expect(chip.textContent).toBe("Compacting");
    expect(chip.getAttribute("aria-label")).toBe("Compacting the conversation");
    expect(screen.queryByTestId("usage-chip-pct")).toBeNull();
  });

  it("offers no way to compact from the panel, not even as a line of text", () => {
    // Owner, 2026-09-16: the panel answers "how full"; `/compact` is the one entry.
    // Owner, 2026-10-12: no line pointing at it either.
    openPopover(usage({ context: 357_000 }), { compactAt: 380_000, contextBar: true });
    const pop = screen.getByTestId("usage-popover");
    expect(pop.textContent).not.toContain("/compact");
    expect(pop.querySelector("button:not([data-testid^='usage-model-'])")).toBeNull();
  });

  it("caps the popover at the viewport and scrolls the model body under the fixed header", () => {
    openPopover(usage());
    const pop = screen.getByTestId("usage-popover");
    // Its own ceiling (the stylesheet takes the smaller of it and the room the window has);
    // the flex column lets the body scroll.
    expect(pop.className).toContain("[--pop-cap:560px]");
    expect(pop.className).toContain("flex-col");
    const scroller = screen.getByTestId("usage-popover-scroll");
    expect(scroller.className).toContain("overflow-y-auto");
    expect(scroller.className).toContain("overscroll-contain");
    // Headline + makeup sit FIXED above the scroller; the totals body is inside it.
    expect(scroller.contains(screen.getByTestId("context-makeup"))).toBe(false);
    expect(scroller.contains(screen.getByText("Context window"))).toBe(false);
    expect(scroller.contains(screen.getByText("This session"))).toBe(true);
    // The session total still renders (values are not dropped).
    expect(scroller.contains(screen.getByText("180 tokens"))).toBe(true);
  });

  it("shows one row per model with its total, every one closed until it is pressed", () => {
    openPopover(
      usage({
        byModel: {
          "gpt-5.6-sol": {
            model: "gpt-5.6-sol",
            input: 100,
            output: 20,
            cache_read: 50,
            cache_write: 10,
          },
          "claude-opus-4.8": {
            model: "claude-opus-4.8",
            input: 10,
            output: 5,
            cache_read: 0,
            cache_write: 0,
          },
        },
      }),
    );
    const current = screen.getByTestId("usage-model-gpt-5.6-sol");
    const other = screen.getByTestId("usage-model-claude-opus-4.8");
    // Nothing is opened for the reader, the model in use included: the panel that opened
    // its six lines was tall enough to cover the conversation.
    expect(current.getAttribute("aria-expanded")).toBe("false");
    expect(other.getAttribute("aria-expanded")).toBe("false");
    // The closed row still states the model and its session total (10 + 5 = 15)…
    expect(other.textContent).toContain("15");
    // …and its lines are mounted (so opening can be a motion) but out of reach.
    const detail = screen.getByTestId("usage-model-detail-claude-opus-4.8");
    expect(detail.getAttribute("data-open")).toBe("false");
    expect(detail.getAttribute("aria-hidden")).toBe("true");
    expect(detail.hasAttribute("inert")).toBe(true);
    // A press opens it: the per-model lines, nothing dropped.
    fireEvent.click(other);
    expect(other.getAttribute("aria-expanded")).toBe("true");
    expect(detail.getAttribute("data-open")).toBe("true");
    expect(detail.hasAttribute("inert")).toBe(false);
    expect(detail.textContent).toContain("Input");
    // The other model's lines are the ones with a cache split.
    fireEvent.click(current);
    expect(screen.getByTestId("usage-model-detail-gpt-5.6-sol").textContent).toContain("Uncached input");
    // A second press shuts it.
    fireEvent.click(other);
    expect(other.getAttribute("aria-expanded")).toBe("false");
    expect(detail.getAttribute("data-open")).toBe("false");
  });

  it("shows the average cache hit rate from the billing-class sums", () => {
    openPopover(usage());
    // cache_read 50 of prompt-side 100+50+10=160 → 31.3%.
    expect(screen.getByText("Average cache hit rate")).toBeTruthy();
    expect(screen.getByText("31.3%")).toBeTruthy();
  });

  it("lazily loads and shows the server's session stats", async () => {
    const loadMetrics = vi.fn().mockResolvedValue({
      events: 10,
      llm: { calls: 12, retries: 3, errors: 1, error_rate: 0.1 },
      latency: { ttft_ms_avg: 1234 },
      tools: { calls: 8, results: 8, timeout_or_error: 2 },
    });
    render(<Composer {...props(usage())} loadMetrics={loadMetrics} />);
    expect(loadMetrics).not.toHaveBeenCalled(); // not until the popover opens

    fireEvent.click(screen.getByTestId("usage-chip"));
    expect(await screen.findByTestId("session-metrics")).toBeTruthy();
    expect(loadMetrics).toHaveBeenCalledTimes(1);
    expect(screen.getByText("Model calls")).toBeTruthy();
    expect(screen.getByText("12 · 3 retries")).toBeTruthy();
    expect(screen.getByText("Failed turns")).toBeTruthy();
    expect(screen.getByText("First token (avg)")).toBeTruthy();
    expect(screen.getByText("1.2s")).toBeTruthy();
    expect(screen.getByText("8 · 2 failed")).toBeTruthy();
  });

  it("hides the stats section when the metrics load fails", async () => {
    const loadMetrics = vi.fn().mockRejectedValue(new Error("old server"));
    render(<Composer {...props(usage())} loadMetrics={loadMetrics} />);
    fireEvent.click(screen.getByTestId("usage-chip"));
    await waitFor(() => expect(loadMetrics).toHaveBeenCalled());
    expect(screen.queryByTestId("session-metrics")).toBeNull();
  });

  it("hides the stats section when no loader is wired", () => {
    openPopover(usage());
    expect(screen.queryByTestId("session-metrics")).toBeNull();
  });

  it("carries no compaction action — the slash command is the one entry point", () => {
    // Owner call 2026-09-16: the popover ANSWERS "how full is the window"; offering a
    // lever there made a second path with its own busy/result chrome.
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({}) });
    vi.stubGlobal("fetch", fetchMock);
    openPopover(usage());
    expect(screen.queryByTestId("compact-now")).toBeNull();
    expect(screen.queryByTestId("compact-result")).toBeNull();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("opens on hover and closes after the pointer leaves both trigger and popover", async () => {
    render(<Composer {...props(usage())} />);
    const wrapper = screen.getByTestId("usage-chip").parentElement!;
    fireEvent.mouseEnter(wrapper);
    // 120ms open debounce, real timers.
    expect(await screen.findByTestId("usage-popover", {}, { timeout: 1500 })).toBeTruthy();
    fireEvent.mouseLeave(wrapper);
    // 220ms close grace, then gone.
    await waitFor(
      () => expect(screen.queryByTestId("usage-popover")).toBeNull(),
      { timeout: 1500 },
    );
  });
});
