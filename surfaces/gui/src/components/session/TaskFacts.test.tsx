// The task head's facts: summed from the turns' receipts and the answers' usage, nothing guessed.
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import type { Item, SessionUsage } from "../../types";
import { SEE_CHANGES_EVENT } from "../../seeChanges";
import { TaskFacts, taskFacts, tokensSpent } from "./TaskFacts";

afterEach(cleanup);

const receipt = (over: Partial<Extract<Item, { kind: "changes" }>>): Item => ({ kind: "changes", ...over }) as Item;
const check = (ok: boolean | null, editsSince = 0) => ({ command: "pytest", ok, summary: ok ? "12 passed" : "1 failed", editsSince, timedOut: false });

describe("taskFacts", () => {
  it("sums the person's files over the turns, the agent's notes apart", () => {
    const facts = taskFacts([
      receipt({ overwritten: ["/w/a.py"], stats: [{ path: "/w/a.py", added: 3, removed: 1 }] }),
      receipt({
        created: ["/w/b.py", "/w/memory/n.md"],
        overwritten: ["/w/a.py"],
        stats: [{ path: "/w/a.py", added: 2, removed: 0 }, { path: "/w/b.py", added: 10, removed: 0 }],
        notes: ["/w/memory/n.md"],
      }),
    ]);
    expect(facts).toMatchObject({ files: 2, added: 15, removed: 1, lastPath: "/w/b.py" });
  });

  it("keeps the last check only until an edit comes after it", () => {
    expect(taskFacts([receipt({ overwritten: ["/w/a"], check: check(true) })]).check?.ok).toBe(true);
    // Edited after the check, in the same turn.
    expect(taskFacts([receipt({ overwritten: ["/w/a"], check: check(true, 2) })]).check).toBeUndefined();
    // A later turn edited and ran nothing.
    expect(taskFacts([receipt({ overwritten: ["/w/a"], check: check(true) }), receipt({ overwritten: ["/w/b"] })]).check).toBeUndefined();
    // A later turn that only ran a check (it named no file) keeps it.
    expect(taskFacts([receipt({ overwritten: ["/w/a"] }), receipt({ did: ["ran"], check: check(false) })]).check?.ok).toBe(false);
  });
});

describe("tokensSpent", () => {
  it("counts every token, cache reads included, and says them by model", () => {
    const usage: SessionUsage = {
      byModel: {
        "anthropic:a": { input: 1000, output: 500, cache_read: 40_000, cache_write: 2000 },
        "openai:b": { input: 100, output: 50, cache_read: 0, cache_write: 0 },
      },
      context: 0,
      prompt: 0,
    };
    expect(tokensSpent(usage).total).toBe(43_650);
    expect(tokensSpent(usage).title).toBe("anthropic:a: 43.5k\nopenai:b: 150");
  });
});

describe("TaskFacts", () => {
  const usage: SessionUsage = { byModel: { m: { input: 48_000, output: 0, cache_read: 0, cache_write: 0 } }, context: 0, prompt: 0 };

  it("draws what is known, and the files open Review on the last one", () => {
    render(<TaskFacts items={[receipt({ overwritten: ["/w/a.py"], stats: [{ path: "/w/a.py", added: 31, removed: 7 }], check: check(true) })]} usage={usage} />);
    expect(screen.getByTestId("task-files").textContent).toBe("1 file+31−7");
    // The check is not drawn beside the files (owner, 2026-10-15).
    expect(screen.queryByTestId("task-check")).toBeNull();
    expect(screen.getByTestId("task-tokens").textContent).toBe("48k tokens");
    const seen = vi.fn();
    window.addEventListener(SEE_CHANGES_EVENT, (e) => seen((e as CustomEvent).detail.path), { once: true });
    fireEvent.click(screen.getByTestId("task-files"));
    expect(seen).toHaveBeenCalledWith("/w/a.py");
  });

  it("draws nothing for a conversation that has done nothing yet", () => {
    render(<TaskFacts items={[]} usage={{ byModel: {}, context: 0, prompt: 0 }} />);
    expect(screen.queryByTestId("task-facts")).toBeNull();
  });
});
