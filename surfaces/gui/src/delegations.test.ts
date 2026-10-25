// The delegation card's derivations (the reference design's subagent topology, fed by Reeve's own
// rows): statuses, counters, timing bounds and the vocabulary of the header. Pure functions,
// so this pins the numbers without a DOM.

import { describe, expect, it } from "vitest";
import {
  agentTitle,
  childSettled,
  delegationDescription,
  delegationElapsedMs,
  delegationHeaderLabel,
  delegationLabel,
  delegationLiveLine,
  delegationNodes,
  delegationRunning,
  delegationStatusLabel,
  delegationSummary,
  failureText,
  isDelegationCall,
  nodeDurationMs,
  nodeModel,
  nodeSteps,
  nodeTokens,
  rosterHeadline,
  rosterOrder,
  stepsLabel,
  withReports,
} from "./delegations";
import type { ToolItem } from "./cards/cardModels";

function taskCall(over: Partial<ToolItem> = {}): ToolItem {
  return {
    kind: "tool",
    id: "t1",
    name: "task",
    args: {
      description: "Inventory host-core A2A",
      prompt: "inventory the A2A surface",
      subagent_type: "explore",
    },
    callId: "c1",
    status: "…",
    ...over,
  } as ToolItem;
}

describe("delegation derivations", () => {
  it("reads steps from run_steps first, then the live frames", () => {
    expect(nodeSteps(taskCall({ stepCount: 68 }))).toBe(68);
    expect(nodeSteps(taskCall({ subagent: { toolcalls: 5 } }))).toBe(5);
    expect(nodeSteps(taskCall())).toBeNull();
    expect(stepsLabel(1)).toBe("1 step");
    expect(stepsLabel(68)).toBe("68 steps");
  });

  it("reads the child's token spend from its result, and reports nothing while it runs", () => {
    // The settled shape `_foreground_result` writes (`harness/tools/task.py`).
    const settled = taskCall({
      status: "ok",
      preview: '{"task_id": "explore-2", "status": "completed", "tokens": 41200, "steps": 14, "report": "…"}',
    });
    expect(nodeTokens(settled)).toBe(41200);
    // A background spawn returns before any spend exists — null, never a made-up zero.
    expect(nodeTokens(taskCall({ status: "ok", preview: '{"task_id": "explore-3", "status": "running"}' }))).toBeNull();
    // A truncated preview still carries the number (it sits before the report).
    expect(nodeTokens(taskCall({ status: "ok", preview: '{"task_id": "x", "tokens": 7, "repo' }))).toBe(7);
    // Text that merely contains the word is not a token count.
    expect(nodeTokens(taskCall({ status: "ok", preview: "the report mentions tokens: many" }))).toBeNull();
    expect(nodeTokens(taskCall())).toBeNull();
  });

  it("reads the child's model from the live frame, then the call's own override", () => {
    // The resolved route the manager wrote onto the child row wins.
    expect(nodeModel(taskCall({ subagent: { model: "deepseek:deepseek-flash" } }))).toBe(
      "deepseek:deepseek-flash",
    );
    // A replayed session without the frames falls back to the requested override.
    expect(
      nodeModel(
        taskCall({
          args: { subagent_type: "explore", provider: "deepseek", model: "deepseek-flash" },
        }),
      ),
    ).toBe("deepseek:deepseek-flash");
    expect(nodeModel(taskCall({ args: { subagent_type: "explore", model: "zai:glm-5" } }))).toBe(
      "zai:glm-5",
    );
    // A provider-only override's model id lives in the agent's definition, which the
    // client cannot see: omit rather than half-show it.
    expect(
      nodeModel(taskCall({ args: { subagent_type: "explore", provider: "deepseek" } })),
    ).toBeNull();
    expect(nodeModel(taskCall())).toBeNull();
    expect(delegationNodes([taskCall({ subagent: { model: "deepseek:deepseek-flash" } })], {})[0]?.model).toBe(
      "deepseek:deepseek-flash",
    );
  });

  it("measures a node's duration from the settle frame, then the cell's own clock", () => {
    expect(nodeDurationMs(taskCall({ subagent: { durationMs: 263_000 } }))).toBe(263_000);
    expect(
      nodeDurationMs(taskCall({ subagent: { startedAt: 1_000, endedAt: 1_263 } })),
    ).toBe(263_000);
    expect(nodeDurationMs(taskCall({ durationMs: 4_000 }))).toBe(4_000);
    // Receipt stamps are NOT a duration source: the browser clock around the wait would
    // charge the child for the approval it sat on (and render float milliseconds).
    expect(nodeDurationMs(taskCall({ ts: 10, endTs: 15 }))).toBeNull();
    expect(nodeDurationMs(taskCall())).toBeNull();
  });

  it("maps wire statuses to the outcomes the card speaks", () => {
    expect(delegationNodes([taskCall()], {})[0]?.outcome).toBe("running");
    expect(delegationNodes([taskCall({ status: "ok" })], {})[0]?.outcome).toBe("completed");
    expect(delegationNodes([taskCall({ status: "error" })], {})[0]?.outcome).toBe("failed");
    expect(delegationNodes([taskCall({ status: "interrupted" })], {})[0]?.outcome).toBe(
      "stopped",
    );
    expect(delegationStatusLabel("stopped")).toBe("Stopped");
  });

  it("a child whose turn ended mid-batch is Finished, not Running, while its call waits", () => {
    // Two spawns in one message run in one parallel chunk, and no result of that chunk is
    // recorded until every call in it finished. The faster child's settle frame
    // arrives first by design (`session_roots.py` emits `ended_at` before the result), so from
    // that moment every fact the client holds says the child has stopped — and the row used to
    // keep reading "Running" until the slower child ended (owner-hit 2026-10-18).
    const waited = taskCall({
      subagent: { sessionId: "child-1", toolcalls: 9, endedAt: 1_700_000_100, durationMs: 12_000 },
    });
    const sibling = taskCall({ id: "t2", callId: "c2", subagent: { sessionId: "child-2" } });
    expect(childSettled(waited)).toBe(true);
    expect(delegationRunning(waited)).toBe(false);
    const [first, second] = delegationNodes([waited, sibling], {});
    expect(first?.outcome).toBe("settled");
    expect(delegationStatusLabel(first!.outcome)).toBe("Finished");
    expect(first?.live).toBe("↳ Finished its turn — waiting for the result");
    // Its own measured elapsed and its own step tally, both from the settle frame.
    expect(first?.durationMs).toBe(12_000);
    expect(first?.steps).toBe(9);
    // The group counts it finished and still says it is working — true of the sibling.
    const summary = delegationSummary([first!, second!]);
    expect(summary).toMatchObject({ total: 2, finished: 1, running: 1, failed: 0, stopped: 0 });
    expect(delegationHeaderLabel(summary)).toBe("Subagents working");
    expect(second?.outcome).toBe("running");

    // The result lands: the verdict is known, and the row takes its own status again.
    const landed = taskCall({
      status: "ok",
      subagent: { sessionId: "child-1", toolcalls: 9, endedAt: 1_700_000_100, durationMs: 12_000 },
    });
    expect(childSettled(landed)).toBe(false);
    expect(delegationNodes([landed], {})[0]?.outcome).toBe("completed");
    expect(delegationLiveLine(landed)).toBeNull();
  });

  it("a settled background spawn is still a RUNNING child until its settle frame", () => {
    // `background=true` returns immediately: the row is settled while the child works.
    const working = taskCall({
      status: "ok",
      subagent: { sessionId: "child-9", background: true, toolcalls: 2 },
    });
    expect(delegationRunning(working)).toBe(true);
    expect(delegationNodes([working], {})[0]?.outcome).toBe("running");
    expect(delegationLiveLine(working)).toBe("↳ 2 steps");
    expect(delegationSummary(delegationNodes([working], {})).running).toBe(1);

    // The manager's settle frame — ended_at/duration_ms — ends it with the child's own
    // measured elapsed, not the spawn call's 366ms.
    const done = taskCall({
      status: "ok",
      subagent: {
        sessionId: "child-9",
        background: true,
        toolcalls: 7,
        endedAt: 1_700_000_100,
        durationMs: 8_000,
      },
    });
    expect(delegationNodes([done], {})[0]?.outcome).toBe("completed");
    expect(delegationNodes([done], {})[0]?.durationMs).toBe(8_000);
    expect(delegationNodes([done], {})[0]?.steps).toBe(7);
    expect(delegationLiveLine(done)).toBeNull();
  });

  it("counts the group and bounds its clock: earliest start to latest end", () => {
    const nodes = delegationNodes(
      [
        taskCall({
          id: "a",
          callId: "a",
          status: "ok",
          subagent: { startedAt: 100, endedAt: 500 },
        }),
        taskCall({
          id: "b",
          callId: "b",
          status: "error",
          subagent: { startedAt: 90, endedAt: 300 },
        }),
        taskCall({ id: "c", callId: "c" }),
      ],
      {},
    );
    const summary = delegationSummary(nodes);
    expect(summary).toMatchObject({
      total: 3,
      finished: 2,
      running: 1,
      failed: 1,
      stopped: 0,
      startedAtMs: 90_000,
      endedAtMs: 500_000,
    });
    // Settled group: the wall clock that held the children.
    const settled = delegationSummary(
      delegationNodes(
        [
          taskCall({
            id: "a",
            callId: "a",
            status: "ok",
            subagent: { startedAt: 100, endedAt: 500 },
          }),
          taskCall({
            id: "b",
            callId: "b",
            status: "error",
            subagent: { startedAt: 90, endedAt: 300 },
          }),
        ],
        {},
      ),
    );
    expect(delegationElapsedMs(settled, 999_999)).toBe(410_000);
    // A live child means the end is "now" — the metric ticks.
    expect(delegationElapsedMs(summary, 600_000)).toBe(510_000);
  });

  it("omits elapsed without timing, and words the header with issues first", () => {
    const untimed = delegationSummary(delegationNodes([taskCall()], {}));
    expect(delegationElapsedMs(untimed, Date.now())).toBeNull();
    expect(delegationHeaderLabel(untimed)).toBe("Subagent working");

    const done = delegationSummary(delegationNodes([taskCall({ status: "ok" })], {}));
    expect(delegationHeaderLabel(done)).toBe("Subagent completed");

    const stopped = delegationSummary(
      delegationNodes([taskCall({ status: "interrupted" })], {}),
    );
    expect(delegationHeaderLabel(stopped)).toBe("Subagent finished with warnings");

    const mixed = delegationSummary(
      delegationNodes(
        [
          taskCall({ id: "a", callId: "a", status: "ok" }),
          taskCall({ id: "b", callId: "b", status: "error" }),
        ],
        {},
      ),
    );
    expect(delegationHeaderLabel(mixed)).toBe("Subagents finished with issues");

    const two = delegationSummary(
      delegationNodes(
        [
          taskCall({ id: "a", callId: "a", status: "ok" }),
          taskCall({ id: "b", callId: "b", status: "ok" }),
        ],
        {},
      ),
    );
    expect(delegationHeaderLabel(two)).toBe("Subagents completed");
  });

  it("keeps the retry/tool/count live line while running and drops it at rest", () => {
    expect(
      delegationLiveLine(taskCall({ subagent: { retry: { attempt: 3, message: "boom" } } })),
    ).toBe("↳ Retrying (attempt 3) · boom");
    expect(
      delegationLiveLine(taskCall({ subagent: { tool: "grep", title: "max_iterations" } })),
    ).toBe("↳ grep max_iterations");
    expect(delegationLiveLine(taskCall({ subagent: { toolcalls: 2 } }))).toBe("↳ 2 steps");
    expect(delegationLiveLine(taskCall({ subagent: { toolcalls: 1 } }))).toBe("↳ 1 step");
    expect(delegationLiveLine(taskCall({ status: "ok", stepCount: 14 }))).toBeNull();
  });

  it("says what a child is doing between its tools, not the tool that already ended", () => {
    const ran = { tool: "grep", title: "max_iterations", toolcalls: 3 };
    expect(delegationLiveLine(taskCall({ subagent: { ...ran, phase: "thinking" } }))).toBe("↳ Thinking");
    expect(delegationLiveLine(taskCall({ subagent: { ...ran, phase: "writing" } }))).toBe("↳ Writing");
    // A tool frame is the newest word again.
    expect(delegationLiveLine(taskCall({ subagent: { ...ran, phase: "start" } }))).toBe(
      "↳ grep max_iterations",
    );
    // A retry outranks both: it is the one thing the reader may have to act on.
    expect(
      delegationLiveLine(
        taskCall({ subagent: { ...ran, phase: "thinking", retry: { attempt: 2, message: "429" } } }),
      ),
    ).toBe("↳ Retrying (attempt 2) · 429");
    // Settled: no line at all, whatever the last frame said.
    expect(delegationLiveLine(taskCall({ status: "ok", subagent: { ...ran, phase: "writing" } }))).toBeNull();
  });

  it("shows a running child's spend so far, and the result's figure once it settles", () => {
    expect(nodeTokens(taskCall({ subagent: { tokens: 4200 } }))).toBe(4200);
    // No spend reported yet is not a spend of zero.
    expect(nodeTokens(taskCall({ subagent: { tokens: 0 } }))).toBeNull();
    // The result is the measured total; a late frame's figure does not outrank it.
    expect(
      nodeTokens(
        taskCall({
          status: "ok",
          preview: '{"task_id": "c", "status": "completed", "tokens": 9100}',
          subagent: { tokens: 4200 },
        }),
      ),
    ).toBe(9100);
  });

  it("reads the roster running-first, then what settled most recently", () => {
    const call = (id: string, over: Partial<ToolItem>) => taskCall({ id, callId: id, ...over });
    const nodes = delegationNodes(
      [
        call("a", { status: "ok", subagent: { startedAt: 10, endedAt: 20 } }),
        call("b", { subagent: { startedAt: 30 } }),
        call("c", { status: "ok", subagent: { startedAt: 40, endedAt: 50 } }),
        call("d", { subagent: { startedAt: 60 } }),
      ],
      {},
    );
    // Running rows keep their spawn order (a row must not move while it is watched).
    expect(rosterOrder(nodes).map((node) => node.key)).toEqual(["b", "d", "c", "a"]);
    // A running node knows when it started: that is what its clock counts from.
    expect(nodes[1].startedAtMs).toBe(30_000);
    expect(rosterHeadline(delegationSummary(nodes))).toBe("2 running · 2 finished");
    const failed = delegationNodes([call("e", { status: "error", preview: "boom" })], {});
    expect(rosterHeadline(delegationSummary(failed))).toBe("1 finished · 1 with issues");
    expect(rosterHeadline(delegationSummary(nodes.slice(1, 2)))).toBe("1 running");
  });

  it("uses the declared agent color, falling back to the stable hash", () => {
    const nodes = delegationNodes([taskCall()], { explore: "#ff00ff", other: "#000000" });
    expect(nodes[0]?.color).toBe("#ff00ff");
    const fallback = delegationNodes([taskCall()], {});
    expect(fallback[0]?.color).toMatch(/^#[0-9a-f]{6}$/i);
  });

  it("reads the fallbacks: description → prompt → agent run", () => {
    expect(agentTitle("explore")).toBe("Explore");
    expect(delegationDescription({ description: "Inventory host-core A2A" })).toBe(
      "Inventory host-core A2A",
    );
    expect(delegationDescription({ prompt: "first line\nsecond" })).toBe("first line");
    expect(delegationLabel(taskCall())).toBe("Inventory host-core A2A");
    expect(delegationLabel(taskCall({ args: { prompt: "line one\nline two" } }))).toBe("line one");
    expect(delegationLabel(taskCall({ args: { subagent_type: "general" } }))).toBe("general run");
  });

  it("surfaces a failed child's own text", () => {
    expect(failureText(taskCall({ preview: '{"error":"provider exploded"}' }))).toBe(
      "provider exploded",
    );
    expect(failureText(taskCall({ preview: "not json, shown whole" }))).toBe("not json, shown whole");
  });
});

describe("a background workflow run in the roster", () => {
  const run = (over: Partial<ToolItem> = {}): ToolItem =>
    ({
      kind: "tool",
      id: "w1",
      name: "workflow",
      args: { script: "return 1", meta: { name: "audit-repo", description: "Audit every module" } },
      callId: "w1",
      status: "ok",
      ...over,
    }) as ToolItem;

  it("is a delegation only once it went to the background", () => {
    expect(isDelegationCall(run())).toBe(false);
    expect(isDelegationCall(run({ subagent: { background: true } }))).toBe(true);
    expect(isDelegationCall(taskCall())).toBe(true);
    // A reopened session has no frames: the runner its stored presentation names is the
    // sign it went to the background (owner, 2026-10-13: a reload dropped it from the roster).
    expect(isDelegationCall(run({ runId: "runner-1" }))).toBe(true);
    const [node] = delegationNodes([run({ status: "ok", runId: "runner-1" })], {});
    expect(node.background).toBe(true);
    expect(node.childId).toBe("runner-1");
  });

  it("takes a failure from the child's own report, which a reload has and its spawn call does not", () => {
    const nodes = delegationNodes(
      [taskCall({ status: "ok", runId: "c-ok" }), taskCall({ id: "t2", callId: "c2", status: "ok", runId: "c-bad" })],
      {},
    );
    const reported = withReports(nodes, new Map([["c-ok", "completed"], ["c-bad", "failed"]]));
    expect(reported.map((n) => n.outcome)).toEqual(["completed", "failed"]);
    expect(reported[1].failure).toContain("background");
    // Nothing to change: the same array, so a memoized roster does not redraw.
    expect(withReports(nodes, new Map([["c-ok", "completed"]]))).toBe(nodes);
  });

  it("reads as a Workflow node named by its own sentence, running until it settles", () => {
    const [node] = delegationNodes([run({ subagent: { background: true, tool: "workflow", title: "verify" } })], {});
    expect([node.title, node.description, node.outcome, node.live]).toEqual([
      "Workflow",
      "Audit every module",
      "running",
      "↳ verify",
    ]);
    const named = run({ args: { meta: { name: "audit-repo" } }, subagent: { background: true, endedAt: 3 } });
    expect([delegationLabel(named), delegationNodes([named], {})[0].outcome]).toEqual([
      "audit-repo workflow",
      "completed",
    ]);
  });
});
