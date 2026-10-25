// The Metrics tab (redesigned 2026-09-25): three views over one payload — Usage (tokens by
// provider → model and by day), Harness (five numbers by day with the days a change shipped,
// health checks, the numbers by model, the harness's review of its own logs), Tools (per-tool
// health and failure reasons). It formats what the server folded and never lists sessions.
// Evals left the page on 2026-10-14: they run by hand and spend tokens.

import { act } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

vi.mock("../api", () => ({
  getOpsMetrics: vi.fn(),
  refreshSelfReview: vi.fn(() => Promise.resolve({ started: true })),
  METRICS_CHANGED: "reeve:metrics-changed",
}));

import { getOpsMetrics, METRICS_CHANGED, refreshSelfReview, type OpsMetrics } from "../api";
import { OpsView } from "./OpsView";

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  window.localStorage.clear();
});

/** Today as the range's days name it (local, `YYYY-MM-DD`): a day the 7-day chart draws. */
function localToday(): string {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

function payload(over: Partial<OpsMetrics> = {}): OpsMetrics {
  return {
    range: "7d",
    backfilling: false,
    headline: {
      sessions: 3,
      turns_started: 20,
      turns_completed: 19,
      turns_failed: 1,
      completion: 0.95,
      tool_calls: 100,
      tool_ok: 96,
      tool_success: 0.96,
      tool_errors: 3,
      tool_timeouts: 1,
      blocked: 2,
      llm_calls: 40,
      retries: 2,
      retry_rate: 0.05,
      model_errors: 1,
      tokens_in: 1_000_000,
      tokens_out: 200_000,
      cache_read: 3_000_000,
      cache_write: 0,
      tokens_total: 4_200_000,
      cache_served: 3_000_000,
      cache_hit: 0.75,
      tokens_per_turn: 221_052.6,
      ttft_avg_ms: 1240,
      ttft_p95_ms: 4800,
      steps: 120,
      steps_per_turn: 6,
      loop_flags: 1,
      failed_spend: 4.6,
      compactions: 2,
      subagents: 5,
    },
    daily: [
      {
        day: "2026-09-18",
        turns: 8,
        failed: 0,
        llm_calls: 14,
        tool_calls: 40,
        tool_failures: 1,
        tool_error_rate: 0.025,
        completion: 1,
        steps: 48,
        steps_per_turn: 6,
        loops: 0,
        blocked: 0,
        tokens_in: 400_000,
        tokens_out: 80_000,
        cache_read: 1_000_000,
        cache_write: 0,
        tokens: 1_480_000,
        ttft_avg_ms: 1100,
        ttft_p95_ms: 3900,
        wall_ms: 600_000,
      },
      {
        day: "2026-09-19",
        turns: 12,
        failed: 1,
        llm_calls: 26,
        tool_calls: 60,
        tool_failures: 3,
        tool_error_rate: 0.05,
        completion: 0.9167,
        steps: 72,
        steps_per_turn: 6,
        loops: 1,
        blocked: 2,
        tokens_in: 600_000,
        tokens_out: 120_000,
        cache_read: 2_000_000,
        cache_write: 0,
        tokens: 2_720_000,
        ttft_avg_ms: 1400,
        ttft_p95_ms: 4800,
        wall_ms: 900_000,
      },
    ],
    by_model: [
      {
        name: "zai-coding-plan:glm-5.3",
        provider: "zai-coding-plan",
        sessions: 2,
        turns: 14,
        llm_calls: 28,
        retries: 1,
        retry_rate: 0.0357,
        tokens: 3_400_000,
        tokens_in: 1_000_000,
        tokens_out: 200_000,
        cache_read: 2_200_000,
        cache_write: 0,
        tool_calls: 70,
        tool_failures: 3,
        tool_success: 0.9571,
        cache_hit: 0.6875,
        steps: 84,
        steps_per_turn: 6,
        ttft_avg_ms: 1300,
        harness: { turns: 14, turns_failed: 1, completion: 0.9286, tool_success: 0.9571, steps_per_turn: 6, tokens_per_turn: 242_857, ttft_p95_ms: 4800 },
      },
    ],
    tools: [
      { name: "run_shell", calls: 60, errors: 2, timeouts: 1, blocked: 0, p95_ms: 9400 },
      { name: "read_file", calls: 30, errors: 1, timeouts: 0, blocked: 0, p95_ms: 300 },
    ],
    failures: [
      {
        session_id: "pinned-session-1",
        turn: 14,
        day: "2026-09-19",
        agent: "reeve",
        tool: "run_shell",
        status: "error",
        reason: "exit 1: npm test",
      },
    ],
    notable: [
      {
        day: "2026-09-19",
        level: "warn",
        kind: "tool_error_rate",
        z: 2.1,
        text: "Tool failure rate 0.05 — 2.1σ above the 6-day baseline.",
      },
    ],
    harness: {
      spend: { turn_total: 1_000_000, main: 990_000, summarizer: 5_000, prepare: 5_000, delegated: 0, unattributed: 0, unattributed_pct: 0 },
      stalls: { compaction: 1, "provider retry": 0, unexplained: 2 },
      ttft: [
        { band: "0-50k", p50_ms: 2600, p90_ms: 6400, samples: 40 },
        { band: ">400k", p50_ms: 8200, p90_ms: 13800, samples: 3 },
      ],
      compaction: { prepared: 2, summarized: 1 },
      prepares: { parked: 3 },
      usage_partial: [{ model: "opencode-go:minimax-m3", sessions: 1 }],
    },
    usage_daily: [
      { day: "2026-09-19", provider: "zai-coding-plan", calls: 10, tokens_in: 500_000, tokens_out: 50_000, cache_read: 1_000_000, cache_write: 0, tokens: 1_550_000 },
    ],
    background: [{ purpose: "memory", calls: 4, tokens: 9_000 }],
    ...over,
  };
}

describe("OpsView", () => {
  it("opens on Usage: totals, and providers grouped over their models", async () => {
    vi.mocked(getOpsMetrics).mockResolvedValue(payload());
    render(<OpsView onOpenTrace={vi.fn()} />);
    expect(await screen.findByTestId("usage-table")).toBeTruthy();
    expect(screen.getByTestId("ops-tab-usage").getAttribute("aria-selected")).toBe("true");
    expect(screen.getAllByText("zai-coding-plan").length).toBeGreaterThan(0); // provider row + legend
    expect(within(screen.getByTestId("usage-table")).getByText("glm-5.3")).toBeTruthy(); // model row, prefix stripped
    expect(screen.getByText("Memory passes")).toBeTruthy(); // background spend
    expect(screen.queryByText("Draft the launch note")).toBeNull(); // no session lists
    expect(getOpsMetrics).toHaveBeenCalledWith("7d");
  });

  it("shows the harness health checks with their targets", async () => {
    vi.mocked(getOpsMetrics).mockResolvedValue(payload());
    render(<OpsView onOpenTrace={vi.fn()} />);
    await screen.findByTestId("usage-table");
    fireEvent.click(screen.getByTestId("ops-tab-harness"));
    const checks = await screen.findByTestId("health-checks");
    expect(checks.textContent).toContain("Unexplained waits");
    expect(checks.textContent).toContain("opencode-go:minimax-m3"); // the under-reporting route
    expect(screen.getByTestId("ttft-bands").textContent).toContain(">400k");
    expect(screen.getByText(/2.1σ above/)).toBeTruthy();
    // No eval anywhere on the page any more (2026-10-14).
    expect(screen.queryByText(/Eval runs/)).toBeNull();
    expect(screen.queryByText(/Is the harness getting better/)).toBeNull();
  });

  it("draws the chosen number by day, with the days a change shipped, and against the span before", async () => {
    vi.mocked(getOpsMetrics).mockResolvedValue(
      payload({
        ships: [{ day: localToday(), adrs: ["0182", "0183"] }],
        previous: {
          since: "2026-09-06", until: "2026-09-13", calls: 8, tokens_in: 1, tokens_out: 1, cache_read: 1, cache_write: 0, tokens: 3, cache_hit: 0.5,
          harness: { turns: 10, turns_failed: 0, completion: 0.9, tool_success: 0.99, steps_per_turn: 4, tokens_per_turn: 1, ttft_p95_ms: 6000 },
        },
      }),
    );
    render(<OpsView onOpenTrace={vi.fn()} />);
    await screen.findByTestId("usage-table");
    fireEvent.click(screen.getByTestId("ops-tab-harness"));
    const completion = await screen.findByTestId("harness-tile-completion");
    expect(completion.getAttribute("aria-pressed")).toBe("true"); // the default
    // 95% against 90%: +5 pt, good. Tool success 96% against 99%: −3 pt, bad. TTFT down is good.
    expect(within(completion).getByTestId("kpi-change").textContent).toBe("▲ 5 pt");
    expect(within(completion).getByTestId("kpi-change").className).toContain("text-ok");
    expect(within(screen.getByTestId("harness-tile-tool")).getByTestId("kpi-change").className).toContain("text-danger");
    expect(within(screen.getByTestId("harness-tile-ttft")).getByTestId("kpi-change").className).toContain("text-ok");
    expect(within(screen.getByTestId("harness-tile-turns")).getByTestId("kpi-change").textContent).toBe("▲ 100%");
    // Turns are soft columns, and the ship day is marked on its column.
    fireEvent.click(screen.getByTestId("harness-tile-turns"));
    expect(screen.getAllByTestId("soft-column")).toHaveLength(7);
    expect(screen.getAllByTestId("ship-mark")).toHaveLength(1);
    expect(window.localStorage.getItem("reeve:metrics-harness-tile")).toBe("turns");
  });

  it("gives each model its own turns and tools", async () => {
    vi.mocked(getOpsMetrics).mockResolvedValue(payload());
    render(<OpsView onOpenTrace={vi.fn()} />);
    await screen.findByTestId("usage-table");
    fireEvent.click(screen.getByTestId("ops-tab-harness"));
    const table = await screen.findByTestId("harness-by-model");
    expect(table.textContent).toContain("glm-5.3");
    expect(table.textContent).toContain("92.9%"); // completion, under 97%: marked
    expect(within(table).getByText("92.9%").className).toContain("text-warnInk");
  });

  it("shows the harness's own review, its age, and refreshes it", async () => {
    vi.mocked(getOpsMetrics).mockResolvedValue(
      payload({
        review: {
          at: Date.now() / 1000 - 2 * 3600,
          days: 30,
          running: false,
          shapes: [
            { name: "cut-details-lost", owner: "compaction/parts", count: 2229, per: "cut details", of: 4351, share: 0.5123 },
            { name: "edit-misses", owner: "tools/edit surface", count: 26, per: null, of: null, share: null },
          ],
          claims: {
            verdicts: { confirmed: 23, refuted: 1, "too early": 12, "not live": 3 },
            refuted: [{ id: "auto-window-ignores-price", adr: "0113", claim: "x", metric: "compaction-starts", per: "steps", expect: "down", before: [37, 5286], after: [37, 2381], resolution: "" }],
          },
        },
      }),
    );
    render(<OpsView onOpenTrace={vi.fn()} />);
    await screen.findByTestId("usage-table");
    fireEvent.click(screen.getByTestId("ops-tab-harness"));
    const shapes = await screen.findByTestId("failure-shapes");
    expect(shapes.textContent).toContain("cut-details-lost");
    expect(shapes.textContent).toContain("51.2%");
    expect(screen.getByTestId("review-status").textContent).toContain("as of 2h ago");
    const claim = screen.getByTestId("refuted-claim");
    expect(claim.textContent).toContain("0.70%");
    expect(claim.textContent).toContain("1.55%");
    fireEvent.click(screen.getByTestId("review-refresh"));
    expect(refreshSelfReview).toHaveBeenCalledTimes(1);
    expect(screen.getByTestId("review-status").textContent).toContain("Reviewing the logs");
  });

  it("lists tool health and routes a failure to its trace", async () => {
    vi.mocked(getOpsMetrics).mockResolvedValue(payload());
    const onOpenTrace = vi.fn();
    render(<OpsView onOpenTrace={onOpenTrace} />);
    await screen.findByTestId("usage-table");
    fireEvent.click(screen.getByTestId("ops-tab-tools"));
    expect(await screen.findByTestId("tool-health")).toBeTruthy();
    expect(screen.getAllByText("run_shell").length).toBeGreaterThan(0);
    fireEvent.click(screen.getAllByText("Open trace")[0]);
    expect(onOpenTrace).toHaveBeenCalledWith("pinned-session-1");
  });

  it("remembers the chosen view", async () => {
    vi.mocked(getOpsMetrics).mockResolvedValue(payload());
    const first = render(<OpsView onOpenTrace={vi.fn()} />);
    await screen.findByTestId("usage-table");
    fireEvent.click(screen.getByTestId("ops-tab-tools"));
    first.unmount();
    render(<OpsView onOpenTrace={vi.fn()} />);
    expect(await screen.findByTestId("tool-health")).toBeTruthy();
  });

  it("switches ranges through the pills", async () => {
    vi.mocked(getOpsMetrics).mockResolvedValue(payload());
    render(<OpsView onOpenTrace={vi.fn()} />);
    await screen.findByTestId("ops-view");
    fireEvent.click(screen.getByTestId("ops-range-today"));
    await waitFor(() => expect(getOpsMetrics).toHaveBeenLastCalledWith("today"));
  });

  it("explains an empty history instead of charting zeroes", async () => {
    vi.mocked(getOpsMetrics).mockResolvedValue(
      payload({
        headline: { ...payload().headline, sessions: 0, turns_started: 0, turns_completed: 0 },
        by_model: [],
        daily: [],
        failures: [],
        notable: [],
      }),
    );
    render(<OpsView onOpenTrace={vi.fn()} />);
    expect(await screen.findByText(/Nothing to measure yet/)).toBeTruthy();
  });

  // 2026-10-13: each number says how it moved against the span before, the checks have their
  // days, and the page opens on the last numbers it had while the new ones come.
  it("says how each number moved against the span before", async () => {
    vi.mocked(getOpsMetrics).mockResolvedValue(
      payload({
        previous: { since: "2026-09-06", until: "2026-09-13", calls: 8, tokens_in: 400_000, tokens_out: 50_000, cache_read: 2_270_000, cache_write: 0, tokens: 2_720_000, cache_hit: 0.5 },
      }),
    );
    render(<OpsView onOpenTrace={vi.fn()} />);
    await screen.findByTestId("usage-table");
    const changes = screen.getAllByTestId("kpi-change").map((el) => el.textContent);
    // Total 3.4M against 2.72M: +25%. Cache hit 2.2M/3.2M = 69% against 50%: +19 pt, good.
    expect(changes[0]).toBe("▲ 25%");
    expect(changes[1]).toBe("▲ 19 pt");
    expect(screen.getAllByTestId("kpi-change")[1].className).toContain("text-ok");
    expect(screen.getAllByTestId("kpi-change")[0].className).not.toContain("text-ok"); // a total moving is neither
  });

  it("opens again on the last numbers, and says it is refreshing them", async () => {
    vi.mocked(getOpsMetrics).mockResolvedValue(payload());
    const first = render(<OpsView onOpenTrace={vi.fn()} />);
    await screen.findByTestId("usage-table");
    first.unmount();
    vi.mocked(getOpsMetrics).mockReturnValue(new Promise(() => {})); // the server has not answered yet
    render(<OpsView onOpenTrace={vi.fn()} />);
    expect(screen.getByTestId("usage-table")).toBeTruthy(); // at once, no skeleton
    expect(screen.getByTestId("ops-updated").textContent).toContain("Refreshing");
  });

  it("keeps the last fold visible when the sidecar drops", async () => {
    vi.mocked(getOpsMetrics).mockRejectedValue(new Error("offline"));
    render(<OpsView onOpenTrace={vi.fn()} />);
    expect(await screen.findByText(/Sidecar not reachable/)).toBeTruthy();
  });
});

// Live: the server pushes `metrics_changed` after each step it folds, not when the turn ends. The page
// refetches at most about once a second, with one trailing fetch, and stops listening when it closes.
describe("OpsView follows the agent", () => {
  const push = () => window.dispatchEvent(new CustomEvent(METRICS_CHANGED));

  it("refetches when the server says the numbers moved, and shows the new ones", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      vi.mocked(getOpsMetrics).mockResolvedValue(payload());
      render(<OpsView onOpenTrace={vi.fn()} />);
      await screen.findByTestId("usage-table");
      expect(getOpsMetrics).toHaveBeenCalledTimes(1);

      const base = payload();
      vi.mocked(getOpsMetrics).mockResolvedValue(
        payload({ headline: { ...base.headline, llm_calls: 41 } }),
      );
      act(() => push());
      await act(async () => {
        await vi.advanceTimersByTimeAsync(1100);
      });
      expect(getOpsMetrics).toHaveBeenCalledTimes(2);
    } finally {
      vi.useRealTimers();
    }
  });

  it("answers a burst of pushes with one fetch, not one per push", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      vi.mocked(getOpsMetrics).mockResolvedValue(payload());
      render(<OpsView onOpenTrace={vi.fn()} />);
      await screen.findByTestId("usage-table");
      vi.mocked(getOpsMetrics).mockClear();

      act(() => {
        for (let i = 0; i < 12; i++) push();
      });
      await act(async () => {
        await vi.advanceTimersByTimeAsync(1100);
      });
      expect(getOpsMetrics).toHaveBeenCalledTimes(1);

      // Later pushes are heard again, not swallowed by the first burst.
      act(() => push());
      await act(async () => {
        await vi.advanceTimersByTimeAsync(1100);
      });
      expect(getOpsMetrics).toHaveBeenCalledTimes(2);
    } finally {
      vi.useRealTimers();
    }
  });

  it("stops listening when the page closes", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      vi.mocked(getOpsMetrics).mockResolvedValue(payload());
      const { unmount } = render(<OpsView onOpenTrace={vi.fn()} />);
      await screen.findByTestId("usage-table");
      vi.mocked(getOpsMetrics).mockClear();
      act(() => push()); // armed...
      unmount(); // ...and the page goes away before it fires
      await act(async () => {
        await vi.advanceTimersByTimeAsync(2000);
      });
      act(() => push());
      await act(async () => {
        await vi.advanceTimersByTimeAsync(2000);
      });
      expect(getOpsMetrics).not.toHaveBeenCalled();
    } finally {
      vi.useRealTimers();
    }
  });
});
