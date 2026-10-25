// One conversation's live state, held outside React.
//
// The app used to keep ONE `items` list and ONE WebSocket, both torn down whenever the
// reader opened a different conversation. The server does not stop a turn when its socket
// closes, so a running session left in the background kept working while the browser
// stopped listening: coming back showed a frozen transcript (restored from a snapshot taken
// at the moment of leaving) until a transcript re-read replaced it, and everything the
// engine streamed in between -- a half-written answer, a command's output, an approval the
// agent was waiting on -- was gone.
//
// Here each conversation owns its state and its socket. The socket stays open while the
// reader is elsewhere, so events keep landing in the right conversation; the view just
// subscribes to whichever one is on screen. Returning is a re-subscribe, not a reload.
//
// The pieces:
//   · `LiveSession`  — state (the fields the transcript is drawn from), the non-reactive facts
//                      a view applies when it becomes active (mode, model, ...), and the socket
//   · `liveRegistry` — the pool: one LiveSession per id, least-recently-viewed ones evicted
//                      (never a running or the active one)
//
// Pure TypeScript with an injectable socket factory, so the rules (event ordering, reconnect,
// the outbox, eviction) are tested without React or a server.
import type { GoalInfo, SessionRevert } from "../api";
import type { JobRow } from "../api/jobs";
import { sameJobs } from "../jobView";
import { Session } from "../api";
import type { StreamBlock } from "../streamBlocks";
import type { Item, SessionUsage, StepTokens, TodoItem, WsEvent } from "../types";
import { emptyUsage } from "../usage";
import { emptyJobOutput, type JobOutputBuffer } from "./jobOutput";

/** A `/compact` that left no marker: there was nothing to fold, or it failed (with why). */
export interface CompactNote {
  nothing: boolean;
  text: string;
  detail?: string;
}

/** What a transcript is drawn from. Replaced (never mutated) on every change, so it is a
 *  valid `useSyncExternalStore` snapshot. */
export interface LiveState {
  items: Item[];
  /** The live step, as ordered blocks (streamBlocks.ts). */
  blocks: StreamBlock[];
  running: boolean;
  todo: TodoItem[];
  usage: SessionUsage;
  goal: GoalInfo | null;
  /** "Compacting context…" while the summarizer runs. */
  compacting: boolean;
  /** When the compaction on screen began (ms): its line's clock and the top bar's. */
  compactingSince: number | null;
  /** A person asked for it between turns (`/compact`). That one ends on `compacted` or
   *  `compact_failed`, not on the next frame: a job's roster can arrive meanwhile. */
  compactingManual: boolean;
  /** How the last `/compact` between turns ended when it left no marker. It leaves with the
   *  person's next message. */
  compactNote: CompactNote | null;
  /** Message-level revert marker. */
  revert: SessionRevert | null;
  /** The socket is open. */
  connected: boolean;
  /** The server keeps refusing the connection (it closes before saying `ready`), so the app
   *  stopped trying. The reader can ask again. */
  gaveUp: boolean;
  /** The transcript was read from the server at least once (or the session is a new draft),
   *  so `items` is the conversation and not just whatever happened to stream in. */
  hydrated: boolean;
  /** A transcript read is in flight: the view draws a skeleton, not an empty state. */
  loading: boolean;
  /** A permission/question/plan prompt the agent is blocked on is on screen, unanswered. */
  needsAttention: boolean;
  /** Background shell jobs' output, by job id, as the socket streams it (`jobOutput.ts`).
   *  Per CONVERSATION state: a panel left open in a background conversation keeps
   *  filling, and coming back to it is a re-render rather than a re-read. */
  jobOutput: Record<string, JobOutputBuffer>;
  /** The session's background shell jobs, as the engine last pushed them: the one
   *  roster the header chip and the strip above the composer both draw. Replaced whole by
   *  each `jobs` frame; read once when the socket opens. */
  jobs: JobRow[];
}

/** Facts about the session that belong to the VIEW (composer pickers, the folder chip) rather
 *  than to the transcript. The event handler records them here for every session; the view
 *  applies the active session's. Not reactive: a change is announced through the host. */
export interface SessionFacts {
  /** The last `ready` frame, patched by `mode_changed` / `plan_changed` / `model_changed` /
   *  `thinking_changed` so activating the session later applies today's values, not the
   *  connect-time ones. */
  ready: Record<string, any> | null;
  /** A remote-host start refusal, drawn on the setup row. */
  environmentError: string;
  /** The To-dos list a `todo_write` proposal carried, waiting on its result. */
  proposedPlan: TodoItem[] | null;
  /** The model of the step now running (its `assistant_message` usage): the calls it makes
   *  are stamped with it, so the turn's footer can say which model did the work. */
  stepModel?: string;
  /** What the steps that made no row yet spent: the next row of the turn carries it. */
  pendingTokens?: StepTokens;
}

/** What a conversation did that the reader may want to hear about while looking elsewhere. */
export type ActivityKind = "needs-attention" | "finished";

export interface SocketParams {
  workspace: string;
}

/** The slice of `Session` the live layer needs; a test supplies a fake. */
export type SessionSocket = Pick<
  Session,
  | "close"
  | "userMessage"
  | "approve"
  | "respondTool"
  | "respondPlan"
  | "respondQuestion"
  | "interrupt"
  | "retry"
  | "setMode"
  | "setPlan"
  | "setModel"
  | "setThinking"
> & { readonly readyState: number };

export interface SocketHandlers {
  onEvent: (event: WsEvent) => void;
  onOpen: () => void;
  onClose: () => void;
}

export type SocketFactory = (
  sessionId: string,
  params: SocketParams,
  handlers: SocketHandlers,
) => SessionSocket;

const defaultFactory: SocketFactory = (id, params, handlers) =>
  new Session(id, params.workspace, handlers);

// WebSocket readyState values; spelled out so a test environment without the global works.
const CONNECTING = 0;
const OPEN = 1;

/** How many times in a row a connection may be closed by the server before it ever said
 *  `ready` (an engine that cannot be built, a refused handshake) before the app stops asking.
 *  A socket that WAS healthy and dropped is retried without limit: that is a network event,
 *  not a refusal. */
export const MAX_REFUSALS = 3;

/** Reconnect after 0.5s, doubling to a cap: a sleeping laptop or a restarting sidecar comes
 *  back on its own, and a dead server is not hammered. */
export const RECONNECT_BASE_MS = 500;
export const RECONNECT_CAP_MS = 8000;
export const reconnectDelay = (attempt: number): number =>
  Math.min(RECONNECT_CAP_MS, RECONNECT_BASE_MS * 2 ** Math.max(0, attempt));

function initialState(): LiveState {
  return {
    items: [],
    blocks: [],
    running: false,
    todo: [],
    usage: emptyUsage(),
    goal: null,
    compacting: false,
    compactingSince: null,
    compactingManual: false,
    compactNote: null,
    revert: null,
    connected: false,
    gaveUp: false,
    hydrated: false,
    loading: false,
    needsAttention: false,
    jobOutput: {},
    jobs: [],
  };
}

type Updater<T> = T | ((previous: T) => T);
const resolve = <T>(next: Updater<T>, previous: T): T =>
  typeof next === "function" ? (next as (p: T) => T)(previous) : next;

export interface ConnectOptions {
  params: SocketParams;
  /** Called for each frame. Built by the app: it owns the view-level side effects. */
  onEvent: (live: LiveSession, event: WsEvent) => void;
  /** The socket opened (first connect or a reconnect). */
  onOpen?: (live: LiveSession, reconnected: boolean) => void;
  /** Bumped to force a fresh socket for the same session (a plugin reload, a remote bind). */
  nonce?: number;
}

export class LiveSession {
  readonly id: string;
  /** Monotonic: when the reader last had this session on screen. Drives eviction. */
  lastViewed = 0;
  facts: SessionFacts = {
    ready: null,
    environmentError: "",
    proposedPlan: null,
  };
  /** Started by the app as a new, empty conversation (never read from the server). Leaving
   *  one that stayed empty drops it; an existing conversation is kept however empty it is. */
  draft = false;
  /** Events were missed (the socket dropped and came back while this was in the background),
   *  so the transcript is read again the moment the reader opens it, not for all twelve at
   *  once when the server restarts. */
  stale = false;
  /** Guards the transcript read: only the newest read for this session may land. */
  loadSeq = 0;
  /** Same idea for the goal read (a `goal` frame that arrives first wins). */
  goalVersion = 0;
  /** ...and for the jobs read made when the socket opens (`seedJobs`). */
  jobsVersion = 0;

  private state: LiveState = initialState();
  private listeners = new Set<() => void>();
  private socket: SessionSocket | null = null;
  private options: ConnectOptions | null = null;
  private factory: SocketFactory;
  private appliedNonce: number | undefined;
  /** Frames that could not be sent because no socket was open; flushed on the next open. */
  private outbox: Array<(socket: SessionSocket) => void> = [];
  private retryTimer: ReturnType<typeof setTimeout> | null = null;
  private attempts = 0;
  private refusals = 0;
  private sawReady = false;
  /** Whether this socket ever opened: one that never did never reached a server to refuse it. */
  private sawOpen = false;
  private everOpened = false;
  private closedByUs = false;
  private onChange: (kind?: ActivityKind) => void;

  constructor(
    id: string,
    factory: SocketFactory = defaultFactory,
    onChange: (kind?: ActivityKind) => void = () => {},
  ) {
    this.id = id;
    this.factory = factory;
    this.onChange = onChange;
  }

  // -- subscription ----------------------------------------------------------------------

  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  };

  getSnapshot = (): LiveState => this.state;

  /** Synchronous read, for event handlers that need the value as of right now. */
  get current(): LiveState {
    return this.state;
  }

  private commit(next: LiveState): void {
    if (next === this.state) return;
    const wasRunning = this.state.running;
    const hadAttention = this.state.needsAttention;
    this.state = next;
    for (const listener of Array.from(this.listeners)) listener();
    if (wasRunning !== next.running || hadAttention !== next.needsAttention) {
      // A prompt outranks a finish: a turn that stopped because it asked is not "done".
      this.onChange(
        !hadAttention && next.needsAttention
          ? "needs-attention"
          : wasRunning && !next.running && !next.needsAttention
            ? "finished"
            : undefined,
      );
    }
  }

  patch(partial: Partial<LiveState>): void {
    let changed = false;
    for (const key of Object.keys(partial) as Array<keyof LiveState>) {
      if (!Object.is(this.state[key], partial[key])) changed = true;
    }
    if (changed) this.commit({ ...this.state, ...partial });
  }

  setItems = (next: Updater<Item[]>): void => {
    const items = resolve(next, this.state.items);
    this.patch({ items, needsAttention: awaitingAnswer(items) });
  };
  setBlocks = (next: Updater<StreamBlock[]>): void =>
    this.patch({ blocks: resolve(next, this.state.blocks) });
  /** Drop the live step. One list cannot half-clear. */
  resetStream = (): void => this.patch({ blocks: [] });
  setRunning = (running: boolean): void => this.patch({ running });
  setTodo = (next: Updater<TodoItem[]>): void =>
    this.patch({ todo: resolve(next, this.state.todo) });
  setUsage = (next: Updater<SessionUsage>): void =>
    this.patch({ usage: resolve(next, this.state.usage) });
  setGoal = (next: Updater<GoalInfo | null>): void => {
    this.goalVersion += 1;
    this.patch({ goal: resolve(next, this.state.goal) });
  };
  setCompacting = (compacting: boolean, manual = false): void =>
    this.patch({
      compacting,
      compactingSince: compacting ? (this.state.compacting ? this.state.compactingSince : Date.now()) : null,
      compactingManual: compacting && (manual || (this.state.compacting && this.state.compactingManual)),
    });
  setCompactNote = (compactNote: CompactNote | null): void => this.patch({ compactNote });
  /** Fold one streamed job-output frame into that job's buffer (`jobOutput.ts` owns the
   *  rules: duplicates dropped, holes marked, the tail bounded). */
  setJobOutput = (id: string, next: Updater<JobOutputBuffer>): void => {
    const previous = this.state.jobOutput[id] ?? emptyJobOutput();
    const buffer = resolve(next, previous);
    if (Object.is(buffer, previous)) return;
    this.patch({ jobOutput: { ...this.state.jobOutput, [id]: buffer } });
  };
  /** Forget some jobs' buffers — the panel's Clear, and a job that left the list. */
  dropJobOutput = (ids: readonly string[]): void => {
    if (ids.length === 0) return;
    const next = { ...this.state.jobOutput };
    let changed = false;
    for (const id of ids) {
      if (id in next) {
        delete next[id];
        changed = true;
      }
    }
    if (changed) this.patch({ jobOutput: next });
  };
  /** The roster as the engine holds it now (a `jobs` frame, or the read at connect). A frame
   *  that changes nothing a row shows keeps the old array, so a quiet roster re-renders
   *  nobody. */
  setJobs = (jobs: readonly JobRow[]): void => {
    this.jobsVersion += 1;
    if (sameJobs(this.state.jobs, jobs)) return;
    this.patch({ jobs: [...jobs] });
  };
  /** The roster read made when the socket opens, for a client that was not connected when
   *  the frames went out. A frame that lands while the read is in flight is NEWER than what
   *  the read will answer, so the read is dropped then; a read that fails leaves the roster
   *  as it was. */
  seedJobs = (read: Promise<readonly JobRow[]>): void => {
    const asked = this.jobsVersion;
    void read
      .then((jobs) => {
        if (this.jobsVersion === asked) this.setJobs(jobs);
      })
      .catch(() => {
        /* the frames will say */
      });
  };
  setRevert =(revert: SessionRevert | null): void => this.patch({ revert });
  setConnected = (connected: boolean): void => this.patch({ connected });

  /** The conversation as the server holds it. Replaces what is shown, keeps the socket. */
  hydrate(loaded: {
    items: Item[];
    revert: SessionRevert | null;
    todo: TodoItem[];
    usage: SessionUsage;
  }): void {
    this.patch({
      items: loaded.items,
      revert: loaded.revert,
      todo: loaded.todo,
      usage: loaded.usage,
      hydrated: true,
      loading: false,
      needsAttention: awaitingAnswer(loaded.items),
    });
  }

  /** A brand-new conversation: empty, and nothing on the server to wait for. */
  startDraft(): void {
    this.draft = true;
    this.hydrateEmpty();
  }

  /** A read that failed leaves an empty, hydrated conversation: the reader sees an honest
   *  empty state instead of a skeleton that never resolves. */
  hydrateEmpty(): void {
    this.hydrate({ items: [], revert: null, todo: [], usage: emptyUsage() });
  }

  /** Back to a blank conversation (a /clear), socket untouched. */
  reset(): void {
    this.patch({
      ...initialState(),
      connected: this.state.connected,
      hydrated: true,
    });
  }

  // -- the socket ------------------------------------------------------------------------

  /** Open the socket if there is none, or replace it when the nonce changed.
   *  Idempotent: the view calls this on every activation, and a healthy socket is kept. */
  connect(options: ConnectOptions): void {
    const previous = this.options;
    this.options = options;
    const unchanged = this.socket !== null && previous !== null && this.appliedNonce === options.nonce;
    if (unchanged) return;
    this.open();
  }

  /** Close the socket and open a new one now (the engine behind it was replaced, or the
   *  reader asked again after the app gave up). */
  reconnect(): void {
    this.refusals = 0;
    this.attempts = 0;
    if (this.options) this.open();
  }

  private open(): void {
    const options = this.options;
    if (!options) return;
    this.teardownSocket();
    this.cancelRetry();
    this.closedByUs = false;
    this.appliedNonce = options.nonce;
    this.sawReady = false;
    this.sawOpen = false;
    this.patch({ gaveUp: false });
    const socket = this.factory(this.id, options.params, {
      onEvent: (event) => {
        // A frame from a socket that was replaced meanwhile must not touch the new one's state.
        if (this.socket !== socket) return;
        if (event.type === "ready") {
          this.sawReady = true;
          this.refusals = 0;
        }
        this.options?.onEvent(this, event);
      },
      onOpen: () => {
        if (this.socket !== socket) return;
        const reconnected = this.everOpened;
        this.sawOpen = true;
        this.everOpened = true;
        this.attempts = 0;
        this.setConnected(true);
        this.flush(socket);
        this.options?.onOpen?.(this, reconnected);
      },
      onClose: () => {
        if (this.socket !== socket) return;
        this.socket = null;
        this.setConnected(false);
        if (this.closedByUs) return;
        if (this.sawOpen && !this.sawReady) {
          // Opened, then closed before it ever said ready: the server refused (no folder, bad
          // handshake, an engine that would not build). Trying again at once repeats the
          // refusal, and each one writes another error line into the transcript.
          // A socket that never opened is not counted: no server answered, so none refused.
          // Counting it gave up for good ~3.5s into a sidecar restart, which takes longer than
          // that, and the app said "Not connected" until reloaded (owner-hit, 2026-10-13).
          this.refusals += 1;
          if (this.refusals >= MAX_REFUSALS) {
            this.patch({ gaveUp: true });
            return;
          }
        }
        this.scheduleRetry();
      },
    });
    this.socket = socket;
  }

  private scheduleRetry(): void {
    if (this.retryTimer !== null || !this.options) return;
    const delay = reconnectDelay(this.attempts);
    this.attempts += 1;
    this.retryTimer = setTimeout(() => {
      this.retryTimer = null;
      if (!this.closedByUs && this.socket === null) this.open();
    }, delay);
  }

  private cancelRetry(): void {
    if (this.retryTimer !== null) clearTimeout(this.retryTimer);
    this.retryTimer = null;
  }

  private teardownSocket(): void {
    const socket = this.socket;
    this.socket = null;
    if (socket) {
      try {
        socket.close();
      } catch {
        /* already closed */
      }
    }
  }

  /** Stop listening for good: the session left the pool. */
  dispose(): void {
    this.closedByUs = true;
    this.cancelRetry();
    this.teardownSocket();
    this.options = null;
    this.listeners.clear();
  }

  get hasSocket(): boolean {
    return this.socket !== null;
  }

  // -- commands --------------------------------------------------------------------------

  /** Run a command on the socket. When none is open the command waits for the next open,
   *  rather than vanishing: an approval clicked in a dropped-connection gap is delivered on
   *  reconnect, and a mode the person picked still reaches the engine. Returns whether it
   *  went out now. */
  act(command: (socket: SessionSocket) => void, opts: { queue?: boolean } = {}): boolean {
    const socket = this.socket;
    if (socket && (socket.readyState === OPEN || socket.readyState === CONNECTING)) {
      command(socket);
      return true;
    }
    if (opts.queue !== false) this.outbox.push(command);
    return false;
  }

  private flush(socket: SessionSocket): void {
    const pending = this.outbox;
    this.outbox = [];
    for (const command of pending) command(socket);
  }

  userMessage(...args: Parameters<SessionSocket["userMessage"]>): boolean {
    // "Nothing to compact yet" or a failed `/compact` is said until the person goes on.
    this.patch({ compactNote: null });
    return this.act((s) => s.userMessage(...args));
  }
  approve(...args: Parameters<SessionSocket["approve"]>): boolean {
    return this.act((s) => s.approve(...args));
  }
  respondTool(...args: Parameters<SessionSocket["respondTool"]>): boolean {
    return this.act((s) => s.respondTool(...args));
  }
  respondPlan(...args: Parameters<SessionSocket["respondPlan"]>): boolean {
    return this.act((s) => s.respondPlan(...args));
  }
  respondQuestion(...args: Parameters<SessionSocket["respondQuestion"]>): boolean {
    return this.act((s) => s.respondQuestion(...args));
  }
  /** Stop is only meaningful now: a Stop that fired after a reconnect would hit a turn the
   *  person never meant to cancel. */
  interrupt(): boolean {
    return this.act((s) => s.interrupt(), { queue: false });
  }
  retry(): boolean {
    return this.act((s) => s.retry(), { queue: false });
  }
  setMode(mode: string): boolean {
    return this.act((s) => s.setMode(mode));
  }
  setPlan(plan: boolean): boolean {
    return this.act((s) => s.setPlan(plan));
  }
  setModel(model: string): boolean {
    return this.act((s) => s.setModel(model));
  }
  setThinking(level: string): boolean {
    return this.act((s) => s.setThinking(level));
  }
}

/** Whether the transcript ends in a prompt the agent is blocked on. A card is "open" until
 *  the item records its answer (`resolved`); the last such item decides. */
export function awaitingAnswer(items: Item[]): boolean {
  for (let i = items.length - 1; i >= 0; i--) {
    const item = items[i] as Item & { resolved?: unknown };
    switch (item.kind) {
      case "approval":
      case "toolreq":
      case "planreq":
      case "question":
        // `=== undefined`, not falsy: a question answered with an empty string is answered.
        return item.resolved === undefined;
      case "assistant":
      case "user":
        return false;
      default:
        continue;
    }
  }
  return false;
}

/** The pool. Holds the most recently viewed conversations; a running one is never evicted
 *  (its events are the point), nor the one on screen. */
export class LiveRegistry {
  private sessions = new Map<string, LiveSession>();
  private listeners = new Set<() => void>();
  private activityListeners = new Set<(id: string, kind: ActivityKind) => void>();
  private clock = 0;
  private statusVersion = 0;
  private factory: SocketFactory;
  readonly capacity: number;

  constructor(options: { capacity?: number; factory?: SocketFactory } = {}) {
    this.capacity = options.capacity ?? 12;
    this.factory = options.factory ?? defaultFactory;
  }

  get(id: string): LiveSession | undefined {
    return this.sessions.get(id);
  }

  has(id: string): boolean {
    return this.sessions.has(id);
  }

  /** The conversation's live state, created blank on first use. */
  ensure(id: string): LiveSession {
    let live = this.sessions.get(id);
    if (!live) {
      live = new LiveSession(id, this.factory, (kind) => {
        this.notify();
        if (kind) for (const listener of Array.from(this.activityListeners)) listener(id, kind);
      });
      this.sessions.set(id, live);
    }
    return live;
  }

  /** The reader is looking at `id`: it becomes the most recently viewed and the pool is
   *  trimmed around it. */
  activate(id: string): LiveSession {
    const live = this.ensure(id);
    live.lastViewed = ++this.clock;
    this.evict(id);
    return live;
  }

  private evict(active: string): void {
    if (this.sessions.size <= this.capacity) return;
    const candidates = Array.from(this.sessions.values())
      .filter((s) => s.id !== active && !s.current.running && !s.current.needsAttention)
      .sort((a, b) => a.lastViewed - b.lastViewed);
    for (const victim of candidates) {
      if (this.sessions.size <= this.capacity) break;
      this.drop(victim.id);
    }
  }

  /** Forget a conversation: its socket closes and its state goes. */
  drop(id: string): void {
    const live = this.sessions.get(id);
    if (!live) return;
    live.dispose();
    this.sessions.delete(id);
    this.notify();
  }

  clear(): void {
    for (const live of this.sessions.values()) live.dispose();
    this.sessions.clear();
    this.notify();
  }

  // -- registry-level subscription: which sessions are running / waiting on the reader ----

  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  };

  /** A conversation needs the reader or finished its turn. The app tells the reader when it is
   *  not the one on screen. */
  onActivity = (listener: (id: string, kind: ActivityKind) => void): (() => void) => {
    this.activityListeners.add(listener);
    return () => {
      this.activityListeners.delete(listener);
    };
  };

  /** Changes whenever any session starts/stops running or starts/stops waiting on an
   *  answer. A number, so it is a cheap `useSyncExternalStore` snapshot. */
  getVersion = (): number => this.statusVersion;

  private notify(): void {
    this.statusVersion += 1;
    for (const listener of Array.from(this.listeners)) listener();
  }

  /** Per-session activity for the sidebar: instant, where the polled list lags by seconds. */
  activity(): Record<string, { running: boolean; needsAttention: boolean }> {
    const out: Record<string, { running: boolean; needsAttention: boolean }> = {};
    for (const [id, live] of this.sessions) {
      const { running, needsAttention } = live.current;
      if (running || needsAttention) out[id] = { running, needsAttention };
    }
    return out;
  }
}

/** The app's pool. */
export const liveRegistry = new LiveRegistry();
