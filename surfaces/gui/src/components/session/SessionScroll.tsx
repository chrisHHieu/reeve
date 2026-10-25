// The conversation column: the scroll region (empty state, the transcript, the live step) and
// the way back to the bottom.
//
// Extracted from App.tsx verbatim — this is the transcript area as it was, and the props are
// the session state it reads. One file to open for "what does the conversation show".

import { useEffect } from "react";
import { useStableCallback } from "../../app/useStableCallback";
import type { ApprovalDecision } from "../../types";
import { ConnectorToolsContext } from "../../connectorTools";
import { AgentColorsContext, type AgentColorIndex } from "../../subagents";
import { Icon } from "../Icon";
import { LiveStep } from "../LiveStep";
import { REVEAL_ARTIFACT_EVENT } from "../Markdown";
import { liveThoughtOf } from "../../streamBlocks";
import { Transcript } from "../Transcript";
import { Elapsed } from "../../ui/Elapsed";
import type { CompactNote, LiveState } from "../../live/liveSession";
import { useLiveBlocks } from "../../live/useShellState";
import { IdleHero } from "./IdleHero";
import { SessionSkeleton } from "./SessionSkeleton";
import { TurnPin } from "./TurnPin";
import { TranscriptSession } from "./transcriptSession";

export interface SessionScrollProps {
  idle: any;
  hydrating: any;
  sessionId: any;
  items: any;
  running: any;
  liveActivity: any;
  /** The conversation's store: the live step's text is read from it HERE, so a streamed token
   *  re-renders this column and not App (live/useShellState.ts). */
  live?: { subscribe: (listener: () => void) => () => void; getSnapshot: () => LiveState };
  /** The live step's blocks, when no store is given (tests). */
  blocks?: any;
  streaming: any;
  compacting: any;
  /** When the compaction on screen began (ms). */
  compactingSince?: number | null;
  /** "Compacting the conversation · 182k tokens". */
  compactingWords?: string;
  /** How the last `/compact` between turns ended when it left no marker. */
  compactNote?: CompactNote | null;
  /** How full the conversation is ("12k of 200k tokens"), for "Nothing to compact yet". */
  contextWords?: string;
  /** Run `/compact` again after it failed. */
  onCompactAgain?: () => void;
  following: any;
  connectorToolIndex: any;
  /** `GET /v1/subagents` colors, for the task card's agent title (P2). */
  agentColors: AgentColorIndex;
  scrollRef: any;
  /** Callback ref for the scroller: App keys its follow/resize/wheel wiring on the node. */
  attachScroller?: (el: HTMLDivElement | null) => void;
  handleScroll: any;
  approve: any;
  retry: any;
  revealArtifact: any;
  undoCall: any;
  setSurface: any;
  followLatest: any;
  /** Open a delegated call's child session (its tab in the workspace panel) from a tool row. */
  openChild: any;
  /** Promote a running delegated call to background (P6); the App owns the route call. */
  promoteTask: () => void;
  /** Fork the session from a message row's action (the same whole-prefix fork `/fork` runs). */
  onForkSession?: () => void;
}

const NO_BLOCKS: never[] = [];

export function SessionScroll(props: SessionScrollProps) {
  const { openChild, promoteTask, idle, hydrating, sessionId, items, running, liveActivity, streaming, compacting, following, connectorToolIndex, agentColors, scrollRef, handleScroll, approve, retry, revealArtifact, undoCall, setSurface, followLatest } = props;
  // App hands over a new arrow on every render, and TurnGroup's memo compares this prop: left as
  // it came, every turn re-rendered on every streamed token: the memo missed on this prop in
  // 10,944 of 10,944 comparisons (counted 2026-10-01 over 4 s of streaming, 40 turns).
  const blocks = useLiveBlocks(props.live, props.blocks ?? NO_BLOCKS);
  const onForkSession = useStableCallback(props.onForkSession);
  // The same for everything else a transcript row is handed: App builds these inline or fresh each render,
  // and a memo'd row that is given a new function re-renders (ToolCallRow, the group rows, DelegationCard).
  const onApprove = useStableCallback<[ApprovalDecision], void>(approve);
  const onRetry = useStableCallback(retry);
  const onOpenIntegrations = useStableCallback(() => setSurface("integrations"));
  const onReveal = useStableCallback((path: string) => void revealArtifact(sessionId, path, "open"));
  const onUndo = useStableCallback<[string], Promise<{ ok: boolean; error?: string }>>(undoCall);
  const onOpenChild = useStableCallback(openChild);
  const onPromoteTask = useStableCallback(promoteTask);
  // A produced-file card's native actions have no session id of their own; this view does,
  // and it is the only one that can resolve the path against the right conversation. `mode`
  // rides the event because "show me the file" and "show me where it lives" are different
  // requests about the same path (the card's folder button vs its menu).
  useEffect(() => {
    if (!sessionId) return;
    const onReveal = (event: Event) => {
      const detail = (event as CustomEvent).detail || {};
      const path = String(detail.path || "");
      const mode = detail.mode === "open" ? "open" : "reveal";
      if (path) void revealArtifact(sessionId, path, mode);
    };
    window.addEventListener(REVEAL_ARTIFACT_EVENT, onReveal);
    return () => window.removeEventListener(REVEAL_ARTIFACT_EVENT, onReveal);
  }, [sessionId, revealArtifact]);
  return (
    <>
      {/* Which question you are reading, once the row that started the turn has scrolled away. */}
      {!idle && <TurnPin scrollRef={scrollRef} watch={items} following={!!following} running={!!running} />}
      <div className="main-scroll" ref={props.attachScroller ?? scrollRef} onScroll={handleScroll}>
        {idle && hydrating ? (
          <SessionSkeleton />
        ) : idle ? (
          <IdleHero />
        ) : (
          <>
            <TranscriptSession.Provider value={sessionId ? String(sessionId) : ""}>
            <ConnectorToolsContext.Provider value={connectorToolIndex}>
            <AgentColorsContext.Provider value={agentColors}>
            <Transcript
              items={items}
              onApprove={onApprove ?? (() => {})}
              running={running}
              liveActivity={liveActivity}
              onRetry={onRetry}
              onOpenConnectors={onOpenIntegrations}
              // "open", not "reveal": a spilled log is something to READ, and the
              // person clicking it wants the text, not its folder. The server
              // validates the path is under one of this session's roots.
              onReveal={onReveal}
              // Deliberately does NOT touch the transcript: the files go back, and
              // the history keeps saying the agent wrote them, because it did. The
              // model re-reads the files on its next turn, so it sees the truth
              // without being told a second, larger story about it.
              onUndo={onUndo}
              onOpenChild={onOpenChild}
              onPromoteTask={onPromoteTask}
              onForkSession={onForkSession}
              // The live step, in the order it happened: thinking, calls being written,
              // and the text as it arrives (components/LiveStep.tsx). It rides INSIDE the
              // running turn so the turn's status line stays the last thing on screen.
              // Split in two (owner, 2026-09-30): the steps on the timeline, and the prose
              // after the step's last call in the turn's answer slot, off the timeline.
              // The text shows from the first word: holding a sentence back for a second
              // or two made the app look slower than the model was.
              liveTail={<LiveStep blocks={blocks} showText={!!streaming} hideLiveThought part="steps" />}
              liveAnswer={<LiveStep blocks={blocks} showText={!!streaming} part="answer" />}
              liveThought={liveThoughtOf(blocks)}
            />
            </AgentColorsContext.Provider>
            </ConnectorToolsContext.Provider>
            </TranscriptSession.Provider>
            {/* A `/compact` between turns has no turn to say it, so it gets the line a turn has:
                the dots, what it works through and a clock (owner, 2026-10-13; it was a spinner
                of its own with no size and no clock). While a turn runs, the compaction is
                named on that turn's status line instead (App's `liveActivity`). When it ends
                the marker takes this place; when it leaves none, the note does. */}
            {compacting && !running ? (
              <CompactingLine words={props.compactingWords || "Compacting the conversation"} since={props.compactingSince ?? Date.now()} />
            ) : props.compactNote && !running ? (
              <CompactNoteLine note={props.compactNote} contextWords={props.contextWords} onAgain={props.onCompactAgain} />
            ) : null}
            {/* The bare burst used to stand in for "working" here. The running-turn
                HEADER now carries its own spinner, so this was a second
                spinner on the same screen — the header's is the canonical one. */}
          </>
        )}
      </div>
      {/* Scrolled up from the bottom → the way back down (the floating circle:
          34px, hairline border, lv2 shadow). Shows whenever the reader has left
          the bottom — during a run it doubles as "jump to latest"; while reading
          history it's just "back to where I was". Zero-height strip keeps it
          floating over the scroll area, above the composer, without reserving
          layout space. */}
      {!following && (
        <div className="relative h-0 z-10">
          <button
            className="absolute bottom-3 left-1/2 -translate-x-1/2 w-[34px] h-[34px] rounded-full border border-line bg-panel shadow-md text-muted hover:text-ink cursor-pointer flex items-center justify-center transition-colors"
            data-testid="jump-to-latest"
            title="Back to bottom"
            onClick={followLatest}
          >
            <Icon name="chevronDown" size={14} />
          </button>
        </div>
      )}
    </>
  );
}

/** A `/compact` between turns, while it runs: the line a running turn has. */
function CompactingLine({ words, since }: { words: string; since: number }) {
  return (
    <div className="turn-status compacting-line" data-testid="compacting-line" data-state="running" aria-live="polite">
      <span className="turn-status-node" aria-hidden>
        <span className="turn-dots">
          <i />
          <i />
          <i />
        </span>
      </span>
      <div className="turn-status-main">
        <div className="turn-status-line">
          <span className="turn-status-label shimmer-text">{words}</span>
          <Elapsed from={since} className="turn-status-clock" />
        </div>
      </div>
    </div>
  );
}

/** How a `/compact` between turns ended when it left no marker. It stays until the next
 *  message: a toast in a corner was gone before it was read. */
function CompactNoteLine({
  note,
  contextWords,
  onAgain,
}: {
  note: CompactNote;
  contextWords?: string;
  onAgain?: () => void;
}) {
  return (
    <div className="turn-status compact-note" data-testid="compact-note" data-kind={note.nothing ? "nothing" : "failed"} role="status">
      <span className="turn-status-node" aria-hidden>
        {!note.nothing && <Icon name="x" size={13} />}
      </span>
      <div className="turn-status-main">
        <div className="turn-status-line">
          {note.nothing ? (
            <>
              <span className="compact-note-words">Nothing to compact yet</span>
              {contextWords && <span>· {contextWords}</span>}
            </>
          ) : (
            <>
              <span className="compact-note-words" data-failed>
                Could not compact
              </span>
              <span title={note.detail}>· {note.text}</span>
              {onAgain && (
                <button type="button" className="compact-again" data-testid="compact-again" onClick={onAgain}>
                  Try again
                </button>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
