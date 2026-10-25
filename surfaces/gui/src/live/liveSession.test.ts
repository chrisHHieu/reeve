import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Item, WsEvent } from "../types";
import {
  LiveRegistry,
  LiveSession,
  MAX_REFUSALS,
  RECONNECT_BASE_MS,
  RECONNECT_CAP_MS,
  awaitingAnswer,
  reconnectDelay,
  type SessionSocket,
  type SocketFactory,
  type SocketHandlers,
} from "./liveSession";

/** A scripted socket: the test plays the server by calling the captured handlers. */
class FakeSocket {
  readyState = 0;
  sent: Array<[string, unknown[]]> = [];
  closed = false;
  constructor(public handlers: SocketHandlers) {}
  open() {
    this.readyState = 1;
    this.handlers.onOpen();
  }
  drop() {
    this.readyState = 3;
    this.handlers.onClose();
  }
  emit(type: string, data: Record<string, unknown> = {}) {
    this.handlers.onEvent({ type, data } as WsEvent);
  }
  close() {
    this.closed = true;
    this.readyState = 3;
  }
  private record(name: string) {
    return (...args: unknown[]) => void this.sent.push([name, args]);
  }
  userMessage = this.record("userMessage");
  approve = this.record("approve");
  respondTool = this.record("respondTool");
  respondPlan = this.record("respondPlan");
  respondQuestion = this.record("respondQuestion");
  interrupt = this.record("interrupt");
  retry = this.record("retry");
  setMode = this.record("setMode");
  setPlan = this.record("setPlan");
  setModel = this.record("setModel");
  setThinking = this.record("setThinking");
}

function harness() {
  const sockets: FakeSocket[] = [];
  const factory: SocketFactory = (_id, _params, handlers) => {
    const socket = new FakeSocket(handlers);
    sockets.push(socket);
    return socket as unknown as SessionSocket;
  };
  return { sockets, factory, last: () => sockets[sockets.length - 1] };
}

const params = { workspace: "" };
const user = (text: string): Item => ({ kind: "user", text });

describe("LiveSession state", () => {
  it("publishes a new snapshot only when something changed", () => {
    const live = new LiveSession("a");
    const first = live.getSnapshot();
    live.setRunning(false); // no change
    expect(live.getSnapshot()).toBe(first);
    live.setRunning(true);
    expect(live.getSnapshot()).not.toBe(first);
    expect(first.running).toBe(false); // snapshots are never mutated
  });

  it("notifies subscribers, and stops after unsubscribe", () => {
    const live = new LiveSession("a");
    const seen = vi.fn();
    const stop = live.subscribe(seen);
    live.setItems([user("hi")]);
    expect(seen).toHaveBeenCalledTimes(1);
    stop();
    live.setItems((p) => [...p, user("again")]);
    expect(seen).toHaveBeenCalledTimes(1);
  });

  it("accepts both values and updaters", () => {
    const live = new LiveSession("a");
    live.setItems([user("one")]);
    live.setItems((p) => [...p, user("two")]);
    expect(live.current.items.map((i) => (i as { text: string }).text)).toEqual(["one", "two"]);
  });

  it("hydrate marks the session read and clears the loading flag", () => {
    const live = new LiveSession("a");
    live.patch({ loading: true });
    live.hydrate({ items: [user("x")], revert: null, todo: [], usage: live.current.usage });
    expect(live.current.hydrated).toBe(true);
    expect(live.current.loading).toBe(false);
  });
});

describe("awaitingAnswer", () => {
  const approval = (resolved?: "once"): Item =>
    ({ kind: "approval", name: "run_shell", args: {}, reason: "", ...(resolved ? { resolved } : {}) }) as Item;

  it("is true while the last prompt is unanswered, false once it records an answer", () => {
    expect(awaitingAnswer([user("go"), approval()])).toBe(true);
    expect(awaitingAnswer([user("go"), approval("once")])).toBe(false);
  });

  it("looks past rows that follow a prompt, but a newer turn ends the question", () => {
    const tool = { kind: "tool", id: "t", name: "x", args: {}, status: "…" } as Item;
    expect(awaitingAnswer([approval(), tool])).toBe(true);
    expect(awaitingAnswer([approval(), { kind: "assistant", text: "done" } as Item])).toBe(false);
  });

  it("counts a question answered with an empty string as answered", () => {
    const q = { kind: "question", question: "ok?", resolved: "" } as Item;
    expect(awaitingAnswer([q])).toBe(false);
    expect(awaitingAnswer([{ kind: "question", question: "ok?" } as Item])).toBe(true);
  });

  it("is tracked on the state as items change", () => {
    const live = new LiveSession("a");
    live.setItems([approval()]);
    expect(live.current.needsAttention).toBe(true);
    live.setItems((p) => [...p.slice(0, -1), approval("once")]);
    expect(live.current.needsAttention).toBe(false);
  });
});

describe("LiveSession socket", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("routes frames to the handler and reflects the open state", () => {
    const h = harness();
    const live = new LiveSession("a", h.factory);
    const onEvent = vi.fn();
    live.connect({ params, onEvent });
    expect(live.current.connected).toBe(false);
    h.last().open();
    expect(live.current.connected).toBe(true);
    h.last().emit("turn_start");
    expect(onEvent).toHaveBeenCalledWith(live, { type: "turn_start", data: {} });
  });

  it("keeps a healthy socket when connect is called again (switching back and forth)", () => {
    const h = harness();
    const live = new LiveSession("a", h.factory);
    live.connect({ params, onEvent: vi.fn(), nonce: 0 });
    h.last().open();
    live.connect({ params, onEvent: vi.fn(), nonce: 0 });
    live.connect({ params, onEvent: vi.fn(), nonce: 0 });
    expect(h.sockets).toHaveLength(1);
    expect(h.sockets[0].closed).toBe(false);
  });

  it("replaces the socket when the nonce changes", () => {
    const h = harness();
    const live = new LiveSession("a", h.factory);
    live.connect({ params, onEvent: vi.fn(), nonce: 0 });
    live.connect({ params, onEvent: vi.fn(), nonce: 1 });
    expect(h.sockets).toHaveLength(2);
    expect(h.sockets[0].closed).toBe(true);
  });

  it("ignores frames from a socket it already replaced", () => {
    const h = harness();
    const live = new LiveSession("a", h.factory);
    const onEvent = vi.fn();
    live.connect({ params, onEvent, nonce: 0 });
    const old = h.last();
    live.connect({ params, onEvent, nonce: 1 });
    old.emit("assistant_delta", { text: "stale" });
    expect(onEvent).not.toHaveBeenCalled();
    // ...and its late close must not flip the new socket's state
    h.last().open();
    old.handlers.onClose();
    expect(live.current.connected).toBe(true);
  });

  it("reconnects with backoff after an unexpected drop, and tells the app it was a reconnect", () => {
    const h = harness();
    const live = new LiveSession("a", h.factory);
    const onOpen = vi.fn();
    live.connect({ params, onEvent: vi.fn(), onOpen });
    h.last().open();
    expect(onOpen).toHaveBeenLastCalledWith(live, false);

    h.last().drop();
    expect(live.current.connected).toBe(false);
    expect(h.sockets).toHaveLength(1);
    vi.advanceTimersByTime(RECONNECT_BASE_MS);
    expect(h.sockets).toHaveLength(2);
    h.last().open();
    expect(live.current.connected).toBe(true);
    expect(onOpen).toHaveBeenLastCalledWith(live, true);
  });

  it("backs off further while the server stays down, and resets once it is back", () => {
    expect(reconnectDelay(0)).toBe(RECONNECT_BASE_MS);
    expect(reconnectDelay(1)).toBe(RECONNECT_BASE_MS * 2);
    expect(reconnectDelay(20)).toBe(RECONNECT_CAP_MS);

    const h = harness();
    const live = new LiveSession("a", h.factory);
    live.connect({ params, onEvent: vi.fn() });
    h.last().open();
    h.last().drop();
    vi.advanceTimersByTime(RECONNECT_BASE_MS);
    h.last().drop(); // the retry failed
    vi.advanceTimersByTime(RECONNECT_BASE_MS); // not yet: the second wait is longer
    expect(h.sockets).toHaveLength(2);
    vi.advanceTimersByTime(RECONNECT_BASE_MS);
    expect(h.sockets).toHaveLength(3);
  });

  it("does not reconnect a socket it closed on purpose", () => {
    const h = harness();
    const live = new LiveSession("a", h.factory);
    live.connect({ params, onEvent: vi.fn() });
    h.last().open();
    live.dispose();
    vi.advanceTimersByTime(RECONNECT_CAP_MS * 2);
    expect(h.sockets).toHaveLength(1);
  });

  it("holds a command sent while no socket is open and sends it on the next open", () => {
    const h = harness();
    const live = new LiveSession("a", h.factory);
    live.connect({ params, onEvent: vi.fn() });
    h.last().open();
    h.last().drop();

    // the person clicks Allow in the gap
    expect(live.approve("once")).toBe(false);
    expect(live.setMode("danger-full-access")).toBe(false);
    expect(h.sockets[0].sent).toEqual([]);

    vi.advanceTimersByTime(RECONNECT_BASE_MS);
    h.last().open();
    expect(h.last().sent).toEqual([
      ["approve", ["once"]],
      ["setMode", ["danger-full-access"]],
    ]);
  });

  it("never replays a Stop after a reconnect: it would hit a turn nobody meant to cancel", () => {
    const h = harness();
    const live = new LiveSession("a", h.factory);
    live.connect({ params, onEvent: vi.fn() });
    h.last().open();
    h.last().drop();
    expect(live.interrupt()).toBe(false);
    vi.advanceTimersByTime(RECONNECT_BASE_MS);
    h.last().open();
    expect(h.last().sent).toEqual([]);
  });

  it("stops asking when the server keeps closing before it ever says ready", () => {
    // e.g. "no valid workspace": the server sends an error and closes. Retrying at once only
    // repeats the refusal (and writes another error line into the transcript each time).
    const h = harness();
    const live = new LiveSession("a", h.factory);
    live.connect({ params, onEvent: vi.fn() });
    for (let i = 0; i < MAX_REFUSALS; i++) {
      h.last().open();
      h.last().drop();
      vi.advanceTimersByTime(RECONNECT_CAP_MS);
    }
    expect(h.sockets).toHaveLength(MAX_REFUSALS);
    expect(live.current.gaveUp).toBe(true);
    vi.advanceTimersByTime(RECONNECT_CAP_MS * 4);
    expect(h.sockets).toHaveLength(MAX_REFUSALS); // and it stays stopped
  });

  it("keeps retrying a connection that WAS healthy: that is the network, not a refusal", () => {
    const h = harness();
    const live = new LiveSession("a", h.factory);
    live.connect({ params, onEvent: vi.fn() });
    for (let i = 0; i < MAX_REFUSALS + 3; i++) {
      h.last().open();
      h.last().emit("ready");
      h.last().drop();
      vi.advanceTimersByTime(RECONNECT_CAP_MS);
    }
    expect(h.sockets).toHaveLength(MAX_REFUSALS + 4);
    expect(live.current.gaveUp).toBe(false);
  });

  it("keeps asking while no server answers at all: a sidecar restarting is not a refusal", () => {
    // The socket never opens while the engine is down. Counting that as a refusal gave up for
    // good ~3.5s into a restart, and the app said "Not connected" until reloaded.
    const h = harness();
    const live = new LiveSession("a", h.factory);
    live.connect({ params, onEvent: vi.fn() });
    for (let i = 0; i < MAX_REFUSALS + 5; i++) {
      h.last().drop();
      vi.advanceTimersByTime(RECONNECT_CAP_MS);
    }
    expect(live.current.gaveUp).toBe(false);
    expect(h.sockets).toHaveLength(MAX_REFUSALS + 6);
    h.last().open();
    h.last().emit("ready");
    expect(live.current.connected).toBe(true);
  });

  it("lets the reader ask again once it gave up", () => {
    const h = harness();
    const live = new LiveSession("a", h.factory);
    live.connect({ params, onEvent: vi.fn() });
    for (let i = 0; i < MAX_REFUSALS; i++) {
      h.last().open();
      h.last().drop();
      vi.advanceTimersByTime(RECONNECT_CAP_MS);
    }
    expect(live.current.gaveUp).toBe(true);
    live.reconnect();
    expect(live.current.gaveUp).toBe(false);
    expect(h.sockets).toHaveLength(MAX_REFUSALS + 1);
    h.last().open();
    h.last().emit("ready");
    expect(live.current.connected).toBe(true);
  });

  it("sends straight through an open socket", () => {
    const h = harness();
    const live = new LiveSession("a", h.factory);
    live.connect({ params, onEvent: vi.fn() });
    h.last().open();
    expect(live.userMessage("hello", [], "m")).toBe(true);
    expect(h.last().sent).toEqual([["userMessage", ["hello", [], "m"]]]);
  });
});

describe("LiveRegistry", () => {
  const make = (capacity = 3) => {
    const h = harness();
    return { h, registry: new LiveRegistry({ capacity, factory: h.factory }) };
  };

  it("creates a session once per id", () => {
    const { registry } = make();
    expect(registry.ensure("a")).toBe(registry.ensure("a"));
  });

  it("evicts the least recently viewed idle session, closing its socket", () => {
    const { registry, h } = make(2);
    for (const id of ["a", "b"]) {
      registry.activate(id).connect({ params, onEvent: vi.fn() });
    }
    registry.activate("c");
    expect(registry.has("a")).toBe(false);
    expect(registry.has("b")).toBe(true);
    expect(h.sockets[0].closed).toBe(true);
  });

  it("never evicts a running session, the active one, or one waiting on an answer", () => {
    const { registry } = make(2);
    registry.activate("running").setRunning(true);
    registry.activate("asking").setItems([{ kind: "approval", name: "x", args: {}, reason: "" } as Item]);
    registry.activate("idle");
    registry.activate("active");
    expect(registry.has("running")).toBe(true);
    expect(registry.has("asking")).toBe(true);
    expect(registry.has("active")).toBe(true);
    expect(registry.has("idle")).toBe(false);
  });

  it("reports activity for the sidebar instantly, and versions it", () => {
    const { registry } = make(5);
    const before = registry.getVersion();
    registry.ensure("a").setRunning(true);
    expect(registry.getVersion()).toBeGreaterThan(before);
    expect(registry.activity()).toEqual({ a: { running: true, needsAttention: false } });
    registry.ensure("a").setRunning(false);
    expect(registry.activity()).toEqual({});
  });

  it("drop forgets the session and its socket", () => {
    const { registry, h } = make();
    registry.activate("a").connect({ params, onEvent: vi.fn() });
    registry.drop("a");
    expect(registry.has("a")).toBe(false);
    expect(h.sockets[0].closed).toBe(true);
  });
});

describe("LiveRegistry activity", () => {
  const approval = { kind: "approval", name: "x", args: {}, reason: "" } as Item;

  it("reports a conversation that starts waiting on the reader", () => {
    const registry = new LiveRegistry();
    const heard: Array<[string, string]> = [];
    registry.onActivity((id, kind) => heard.push([id, kind]));
    const live = registry.ensure("bg");
    live.setRunning(true);
    live.setItems([approval]);
    expect(heard).toEqual([["bg", "needs-attention"]]);
  });

  it("reports a finished turn, once", () => {
    const registry = new LiveRegistry();
    const heard: string[] = [];
    registry.onActivity((_id, kind) => heard.push(kind));
    const live = registry.ensure("bg");
    live.setRunning(true);
    live.setRunning(false);
    live.setRunning(false);
    expect(heard).toEqual(["finished"]);
  });

  it("does not call a turn that stopped on a question finished", () => {
    const registry = new LiveRegistry();
    const heard: string[] = [];
    registry.onActivity((_id, kind) => heard.push(kind));
    const live = registry.ensure("bg");
    live.setRunning(true);
    live.setItems([approval]); // needs-attention
    live.setRunning(false); // the turn ended while the prompt is still open
    expect(heard).toEqual(["needs-attention"]);
  });

  it("stays quiet about answering, and about a draft", () => {
    const registry = new LiveRegistry();
    const heard: string[] = [];
    registry.onActivity((_id, kind) => heard.push(kind));
    const live = registry.ensure("bg");
    live.startDraft();
    live.setItems([approval]);
    heard.length = 0;
    live.setItems([{ ...approval, resolved: "once" } as Item]);
    expect(heard).toEqual([]);
  });

  it("stops reporting after unsubscribe", () => {
    const registry = new LiveRegistry();
    const heard: string[] = [];
    const stop = registry.onActivity((_id, kind) => heard.push(kind));
    stop();
    registry.ensure("bg").setRunning(true);
    registry.ensure("bg").setRunning(false);
    expect(heard).toEqual([]);
  });
});

describe("LiveSession drafts", () => {
  it("a draft is hydrated at once and marked, so leaving it empty can drop it", () => {
    const live = new LiveSession("d");
    live.startDraft();
    expect(live.draft).toBe(true);
    expect(live.current.hydrated).toBe(true);
    expect(live.current.items).toEqual([]);
  });
});

describe("LiveSession jobs roster", () => {
  const job = (over: Record<string, unknown> = {}) => ({
    id: "bg-1",
    command: "npm run build",
    status: "running" as const,
    started_at: 100,
    exit_code: null,
    ...over,
  });

  it("keeps the array it holds when a frame changes nothing a row shows", () => {
    const live = new LiveSession("a");
    const seen = vi.fn();
    live.setJobs([job()]);
    const held = live.current.jobs;
    live.subscribe(seen);
    live.setJobs([job()]); // the same roster, pushed again
    expect(live.current.jobs).toBe(held);
    expect(seen).not.toHaveBeenCalled();
    live.setJobs([job({ last_line: "ready" })]);
    expect(live.current.jobs).not.toBe(held);
    expect(seen).toHaveBeenCalledTimes(1);
  });

  it("seeds the roster from the read made when the socket opens", async () => {
    const live = new LiveSession("a");
    live.seedJobs(Promise.resolve([job()]));
    await Promise.resolve();
    await Promise.resolve();
    expect(live.current.jobs.map((row) => row.id)).toEqual(["bg-1"]);
  });

  it("drops that read when a frame arrived while it was in flight", async () => {
    const live = new LiveSession("a");
    let answer: (jobs: ReturnType<typeof job>[]) => void = () => {};
    live.seedJobs(new Promise((resolve) => (answer = resolve)));
    // The frame is newer than whatever the read is about to say.
    live.setJobs([job({ status: "finished" as const, exit_code: 0 })]);
    answer([job()]);
    await Promise.resolve();
    await Promise.resolve();
    expect(live.current.jobs[0].status).toBe("finished");
  });

  it("leaves the roster as it was when the read fails", async () => {
    const live = new LiveSession("a");
    live.setJobs([job()]);
    live.seedJobs(Promise.reject(new Error("401")));
    await Promise.resolve();
    await Promise.resolve();
    expect(live.current.jobs).toHaveLength(1);
  });
});
