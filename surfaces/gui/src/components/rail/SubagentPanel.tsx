// A subagent's own session, read beside the chat that started it (owner, 2026-10-13).
//
// It used to replace the chat column (`ChildSessionView`): reading a child meant leaving the
// main agent, a crumb led back, and the child's task was drawn as the reader's own bubble,
// half a window tall. Here it is a tab of the workspace panel, one per child, so both are on
// screen. What is bespoke is the frame only: the head (the child's mark, its task, one line of
// facts), the task it was given, and a foot. The steps are the ordinary `Transcript`, the same
// components the chat draws, because a child is a real session.
//
// LIVE-UPDATE: a child's events go to sockets viewing ITS session id, which this client is
// not, so while the child is busy this polls the messages on a short cadence; a busy→idle
// flip re-runs the effect, which is what fetches the final answer after the last poll. No
// child socket is opened: one would let this view talk to the child, and it is read only (a
// follow-up goes through the main agent).

import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { getSessionMessages } from "../../api";
import { itemsFromMessages } from "../../itemsFromMessages";
import type { DelegationNode, DelegationOutcome } from "../../delegations";
import { stepsLabel } from "../../delegations";
import type { Item } from "../../types";
import { Elapsed, formatDuration } from "../../ui/Elapsed";
import { formatTokens, totalTokens, usageFromMessages } from "../../usage";
import { Icon } from "../Icon";
import { Transcript } from "../Transcript";

/** Poll only while the child works; a finished session is final. */
const CHILD_POLL_MS = 800;
/** Closer to the end than this, the panel is following the newest step. */
const FOLLOW_SLACK_PX = 64;
/** A task longer than this is folded to three lines. */
const BRIEF_FOLD_CHARS = 220;

export interface SubagentPanelProps {
  /** The child session. */
  sessionId: string;
  /** The task it was given, as its row in the chat names it. */
  title: string;
  /** The workflow run it is an agent of, when it is one. */
  group?: string;
  /** What the chat already knows of the child (`delegations.ts`); absent for a child the
   *  chat has no row for (an agent of a workflow). */
  node?: DelegationNode;
  /** The child's kind, when there is no row to say it. */
  agent?: string;
  /** The child is working (the app-wide `session_status`). */
  busy: boolean;
  /** Send a child that holds up the turn to the background. */
  onPromote?: () => void;
  /** Open a child of this child: its own tab. */
  onOpenChild?: (run: { runId: string; title: string }) => void;
  /** The panel has taken the chat's place (a narrow window, or full screen): the way back,
   *  and whether the main agent is still working behind it. */
  back?: { onBack: () => void; mainRunning: boolean };
}

const ENDED: Record<Exclude<DelegationOutcome, "running">, string> = {
  settled: "Finished",
  completed: "Completed",
  failed: "Failed",
  stopped: "Stopped",
};

function Mark({ outcome }: { outcome: DelegationOutcome | "unknown" }) {
  const settled = outcome === "completed" || outcome === "failed" || outcome === "stopped";
  return (
    <span className="rail-mark subagent-panel-mark" data-state={outcome === "unknown" ? "idle" : outcome} aria-hidden>
      {settled ? (
        <Icon name={outcome === "completed" ? "check" : outcome === "failed" ? "x" : "stop"} size={16} />
      ) : (
        <Icon name="bot" size={16} running={outcome === "running"} />
      )}
    </span>
  );
}

/** The task the child was given: not a message from the reader, so not their bubble. Three
 *  lines, opened in place. */
function Brief({ text }: { text: string }) {
  const long = text.length > BRIEF_FOLD_CHARS || text.split("\n").length > 3;
  const [open, setOpen] = useState(false);
  const body = useRef<HTMLParagraphElement>(null);
  // The fold moves between three lines and the text's own height, which is measured when it
  // is asked for: `max-height` needs a number to move to.
  const [full, setFull] = useState<number | null>(null);
  const toggle = () => {
    setFull(body.current?.scrollHeight ?? null);
    setOpen((v) => !v);
  };
  return (
    <div className="subagent-brief" data-testid="child-brief">
      <div className="subagent-brief-label">Asked by the main agent</div>
      <p
        ref={body}
        className="subagent-brief-text"
        data-folded={long && !open ? "true" : "false"}
        style={long && open && full !== null ? { maxHeight: full } : undefined}
      >
        {text}
      </p>
      {long && (
        <button type="button" className="subagent-brief-toggle" aria-expanded={open} data-testid="child-brief-toggle" onClick={toggle}>
          {open ? "Show less" : "Show all"}
        </button>
      )}
    </div>
  );
}

export function SubagentPanel({ sessionId, title, group, node, agent, busy, onPromote, onOpenChild, back }: SubagentPanelProps) {
  const [items, setItems] = useState<Item[]>([]);
  const [usage, setUsage] = useState(() => usageFromMessages([]));
  const [error, setError] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let alive = true;
    let timer: number | null = null;
    const load = async () => {
      try {
        const messages = await getSessionMessages(sessionId);
        if (!alive) return;
        setItems(itemsFromMessages(messages));
        setUsage(usageFromMessages(messages));
        setError(null);
        setLoaded(true);
        if (busy) timer = window.setTimeout(load, CHILD_POLL_MS);
      } catch (err) {
        if (!alive) return;
        setError(err instanceof Error ? err.message : String(err));
        setLoaded(true);
      }
    };
    void load();
    return () => {
      alive = false;
      if (timer !== null) window.clearTimeout(timer);
    };
  }, [sessionId, busy, attempt]);

  // What the chat knows wins; a child with no row there is only known to be working or not.
  const outcome: DelegationOutcome | "unknown" = node ? node.outcome : busy ? "running" : "unknown";
  const running = outcome === "running";
  const kind = node?.title || agent || "";
  const spent = totalTokens(usage) > 0 ? totalTokens(usage) : (node?.tokens ?? 0);

  // The first message is the task; the rest is what the child did with it.
  const brief = items[0]?.kind === "user" ? items[0].text : "";
  const steps = useMemo(() => (items[0]?.kind === "user" ? items.slice(1) : items), [items]);

  // A running child is followed to its newest step until the reader scrolls away; a finished
  // one opens at its task, to be read from the top.
  const scroller = useRef<HTMLDivElement>(null);
  const following = useRef(busy);
  const [away, setAway] = useState(false);
  const onScroll = () => {
    const el = scroller.current;
    if (!el) return;
    const near = el.scrollHeight - el.scrollTop - el.clientHeight < FOLLOW_SLACK_PX;
    following.current = near;
    setAway(!near);
  };
  useLayoutEffect(() => {
    const el = scroller.current;
    if (el && following.current && running) el.scrollTop = el.scrollHeight;
  }, [steps, running]);
  const jump = () => {
    const el = scroller.current;
    if (!el) return;
    following.current = true;
    setAway(false);
    el.scrollTop = el.scrollHeight;
  };

  const state = running ? (
    <>
      Running{node?.startedAtMs != null && <> <Elapsed from={node.startedAtMs} /></>}
    </>
  ) : outcome === "unknown" ? null : (
    <>
      {ENDED[outcome]}
      {node?.durationMs != null && ` ${outcome === "completed" || outcome === "settled" ? "in" : "after"} ${formatDuration(node.durationMs)}`}
    </>
  );
  const facts = [
    kind ? <b key="kind">{kind}</b> : null,
    node?.model ? (
      <span key="model" className="subagent-panel-model" title={node.model}>
        {node.model}
      </span>
    ) : null,
    state ? <span key="state">{state}</span> : null,
    node?.background && running ? <span key="bg">in the background</span> : null,
    !running && node?.steps != null ? <span key="steps">{stepsLabel(node.steps)}</span> : null,
    spent > 0 ? <span key="spent">{formatTokens(spent)} tokens</span> : null,
  ].filter(Boolean);

  return (
    <section className="subagent-panel" data-testid="child-view" data-outcome={outcome} aria-label={`Subagent: ${title || "session"}`}>
      {back && (
        <div className="subagent-panel-back">
          <button type="button" data-testid="child-back" onClick={back.onBack}>
            <Icon name="arrowLeft" size={14} />
            Back to the chat
          </button>
          {back.mainRunning && <span className="subagent-panel-back-note">The main agent is working</span>}
        </div>
      )}
      <header className="subagent-panel-head" data-testid="child-head">
        {group && (
          <div className="subagent-panel-group" data-testid="child-group">
            {group}
          </div>
        )}
        <div className="subagent-panel-title">
          <Mark outcome={outcome} />
          <h3 className={running ? "shimmer-text" : undefined} title={title}>
            {title || "Subagent"}
          </h3>
        </div>
        {(facts.length > 0 || (running && !node?.background && onPromote)) && (
          <div className="subagent-panel-facts" data-testid="child-facts">
            {facts}
            {running && node && !node.background && onPromote && (
              <button type="button" className="tool-run-open subagent-panel-promote" data-testid="child-promote" onClick={onPromote}>
                Run in background
              </button>
            )}
          </div>
        )}
        {node?.failure && (
          <div className="subagent-panel-why" data-testid="child-failure">
            {node.failure}
          </div>
        )}
      </header>

      <div className="subagent-panel-scroll" ref={scroller} onScroll={onScroll} aria-busy={!loaded || undefined}>
        {!loaded ? (
          <div className="subagent-panel-skeleton" data-testid="child-loading" aria-hidden>
            <i style={{ width: "38%" }} />
            <i style={{ width: "92%" }} />
            <i style={{ width: "71%" }} />
            <i style={{ width: "46%", marginTop: 14 }} />
            <i style={{ width: "62%" }} />
          </div>
        ) : error ? (
          <div className="subagent-panel-error" data-testid="child-error">
            <p>This session could not be loaded. {error}</p>
            <button type="button" className="btn sm" onClick={() => { setLoaded(false); setAttempt((n) => n + 1); }}>
              Try again
            </button>
          </div>
        ) : (
          <>
            {brief && <Brief key={sessionId} text={brief} />}
            {steps.length === 0 ? (
              <p className="subagent-panel-quiet" data-testid="child-empty">
                {running ? "It has been given its task and has not taken a step yet." : "This session has nothing in it."}
              </p>
            ) : (
              <div className="subagent-panel-transcript" data-testid="child-transcript">
                {/* `onApprove` is a no-op: a child's ask is answered in the chat's own dock. */}
                <Transcript
                  items={steps}
                  running={running}
                  onApprove={() => {}}
                  defaultOpen
                  onOpenChild={onOpenChild}
                  answerLabel="Its report · what the main agent received"
                />
              </div>
            )}
          </>
        )}
      </div>
      {running && away && (
        <button type="button" className="subagent-panel-jump" data-testid="child-jump" onClick={jump}>
          Jump to latest
        </button>
      )}

      <footer className="subagent-panel-foot">
        <Icon name="lock" size={12} />
        <span data-testid="child-foot-note">Read only</span>
        {usage.context > 0 && (
          <span className="subagent-panel-context" data-testid="child-usage" title="Tokens in the child's context window now">
            {formatTokens(usage.context)} in context
          </span>
        )}
      </footer>
    </section>
  );
}
