// A fold of the model's own context (`fold_context`) is ONE step of its turn.
//
// It used to be drawn twice: a generic "Worked · Folded its own context into a note" row,
// and the compaction divider after the step — which is a turn breaker, so the turn read
// "Worked for 1m 10s", a rule across the transcript, and then the rest of the same turn as a
// second group (owner, 2026-10-05).

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { rowSpec } from "./registry";
import type { ToolItem } from "./cardModels";
import { Transcript } from "../components/Transcript";
import { itemsFromMessages } from "../itemsFromMessages";
import { LiveSession } from "../live/liveSession";
import { handleSessionEvent, type SessionEventHost } from "../live/sessionEvents";
import type { WsEvent } from "../types";

afterEach(cleanup);

const NOTE = "## Done\n- renamed `fetch_record` in 4 files\n\n## Left\n- the changelog";
const CARD = {
  card: "fold",
  folded_messages: 38,
  kept_messages: 6,
  before_tokens: 74_895,
  after_tokens: 33_400,
  saved_tokens: 41_495,
};

const fold = (extra: Partial<ToolItem> = {}): ToolItem =>
  ({
    kind: "tool",
    id: "t1",
    name: "fold_context",
    args: { note: NOTE },
    status: "ok",
    presentation: { summary: "Folded 38 earlier messages into its own note", card: CARD },
    ...extra,
  }) as ToolItem;

describe("the fold row", () => {
  it("says what was folded and what it freed, on one line", () => {
    const spec = rowSpec(fold());
    expect(spec.title).toBe("Folded");
    expect(spec.icon).toBe("fold");
    expect(spec.summary).toBe("38 earlier messages");
    expect(spec.summarySuffix).toBe("41.5k freed");
    expect(spec.fold).toEqual({ note: NOTE, card: CARD });
  });

  it("counts one message as one", () => {
    const one = fold({
      presentation: { summary: "x", card: { ...CARD, folded_messages: 1 } as never },
    });
    expect(rowSpec(one).summary).toBe("1 earlier message");
  });

  it("claims no number it was not given: a row from before the card keeps its note", () => {
    const spec = rowSpec(
      fold({ presentation: { summary: "Folded its own context into a note" }, args: JSON.stringify({ note: NOTE }) }),
    );
    expect(spec.title).toBe("Folded");
    expect(spec.summary).toBe("its own context into a note");
    expect(spec.summarySuffix).toBeUndefined();
    expect(spec.fold).toEqual({ note: NOTE, card: null });
  });

  it("drops a card with a count missing instead of drawing a zero", () => {
    const { saved_tokens: _saved, ...partial } = CARD;
    const spec = rowSpec(fold({ presentation: { summary: "x", card: partial as never } }));
    expect(spec.summarySuffix).toBeUndefined();
    expect(spec.fold?.card).toBeNull();
  });

  it("a refused fold keeps the noun and the refusal, not a note card", () => {
    const spec = rowSpec(
      fold({
        status: "error",
        presentation: { summary: "Fold its own context into a note" },
        preview: "Error: not folded — folding now would free ~1,200 of ~46,000 tokens.",
      }),
    );
    expect(spec.title).toBe("Fold");
    expect(spec.fold).toBeUndefined();
    expect(spec.errorSummary).toContain("not folded");
  });
});

const FOLDED_NOTICE = {
  role: "notice",
  kind: "compacted",
  reason: "folded",
  text: "Context folded — the model replaced earlier turns with its own note",
  saved_tokens: 41_495,
  now_tokens: 33_400,
  summary: NOTE,
};

const REPLAYED = [
  { role: "user", content: "rename it everywhere, then the changelog" },
  {
    role: "assistant",
    content: "",
    tool_calls: [
      { id: "f1", function: { name: "fold_context", arguments: JSON.stringify({ note: NOTE }) } },
      { id: "r1", function: { name: "web_fetch", arguments: '{"url": "https://x.example/p"}' } },
    ],
  },
  {
    role: "tool",
    tool_call_id: "f1",
    content: "Folded 38 older messages into your note; the last 6 stay as they are.",
    _presentation: { summary: "Folded 38 earlier messages into its own note", card: CARD },
  },
  { role: "tool", tool_call_id: "r1", content: "ok" },
  FOLDED_NOTICE,
  {
    role: "assistant",
    content: "Enough facts. Writing the report.",
    tool_calls: [{ id: "w1", function: { name: "write_file", arguments: '{"path": "report.md", "content": "x"}' } }],
  },
  { role: "tool", tool_call_id: "w1", content: "ok" },
  { role: "assistant", content: "Done." },
];

describe("a fold in the transcript", () => {
  it("replays inside its turn as the marker a compaction leaves: one group, the numbers on it", () => {
    const items = itemsFromMessages(REPLAYED as any);
    // The fold's `compacted` notice is still skipped: the call itself draws the marker.
    expect(items.some((item) => item.kind === "notice")).toBe(false);

    const { container } = render(<Transcript items={items} onApprove={vi.fn()} defaultOpen />);
    // One group: the marker stands inside the turn and does not cut it (owner, 2026-10-13:
    // a fold wears the marker a compaction does).
    expect(container.querySelectorAll(".stepgroup")).toHaveLength(1);
    const marker = screen.getByTestId("compaction-divider");
    expect(marker.closest(".stepgroup")).not.toBeNull();
    expect(marker.textContent).toContain("Folded by the agent");
    expect(marker.textContent).toContain("74.9k → 33.4k tokens");

    // Behind the marker: the note the model now reads, as prose.
    expect(screen.queryByTestId("compaction-summary")).toBeNull();
    fireEvent.click(screen.getByTestId("compaction-toggle"));
    expect(screen.getByTestId("compaction-summary").textContent).toContain("renamed fetch_record in 4 files");
    expect(screen.getByTestId("compaction-summary").querySelector("h2")).not.toBeNull();
  });

  it("a compaction the harness ran is still the divider", () => {
    const items = itemsFromMessages([
      { ...FOLDED_NOTICE, reason: "summarized", text: "Context compacted — earlier turns were summarized" },
    ] as any);
    expect(items).toHaveLength(1);
    expect(items[0]).toMatchObject({ kind: "notice", divider: true, text: "Compacted", span: "74.9k → 33.4k tokens" });
  });
});

describe("a fold arriving live", () => {
  const host: SessionEventHost = {
    factsChanged: () => {},
    railStale: () => {},
    reloadTranscript: () => {},
    engineReplaced: () => {},
    turnEnded: () => {},
    showsEnvironmentError: () => false,
  };
  const feed = (live: LiveSession, frames: Array<[string, Record<string, unknown>]>) => {
    for (const [type, data] of frames) handleSessionEvent(live, { type, data } as WsEvent, host);
  };

  it("adds no divider and still moves the context meter", () => {
    const live = new LiveSession("s");
    feed(live, [
      ["turn_start", { input: "go on" }],
      ["tool_proposed", { name: "fold_context", call_id: "f1", arguments: { note: NOTE } }],
      [
        "tool_finished",
        {
          name: "fold_context",
          call_id: "f1",
          status: "ok",
          presentation: { summary: "Folded 38 earlier messages into its own note", card: CARD },
        },
      ],
      ["compacted", { reason: "folded", text: FOLDED_NOTICE.text, saved_tokens: 41_495, now_tokens: 33_400 }],
    ]);
    expect(live.current.items.map((item) => item.kind)).toEqual(["user", "tool"]);
    expect(rowSpec(live.current.items[1] as ToolItem).summarySuffix).toBe("41.5k freed");
    expect(live.current.usage.context).toBe(33_400);

    // The harness's own compaction keeps its marker.
    feed(live, [["compacted", { reason: "summarized", text: "Context compacted", saved_tokens: 9_000 }]]);
    expect(live.current.items.map((item) => item.kind)).toEqual(["user", "tool", "notice"]);
  });
});
