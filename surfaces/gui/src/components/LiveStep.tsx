// The step happening right now, rendered in the order it happened.
//
// Before this, the live view held two flat strings and showed the thinking ONLY while
// there was no text yet — so the moment a sentence arrived the trace disappeared, and a
// step that thought, spoke, then thought again looked like a step that thought once.
// `streamBlocks.ts` keeps the order; this renders it.
//
// The equivalent is `AssistantNodeView` → `AssistantMarkdown`, which walks the same block
// list and hands each kind to its own row. The shape is the same; the rows are
// reeve's, because a reasoning disclosure and a tool card already exist here and a
// second set would be two answers to one question.
//
// What this does NOT decide is whether streamed prose is the answer — the TRANSCRIPT does,
// by peeling a settled turn's trailing text off as the answer bubble. `showText` is the
// caller's only lever, and the 40-word gate that used to sit in front of it is gone: the reference design
// streams straight into the renderer, and holding a sentence for a second or two made the
// app look slower than the model was.

import { useRef } from "react";
import { Markdown } from "./Markdown";
import { ThinkRow } from "./ThinkRow";
import { Icon } from "./Icon";
import { churnFromArgs, liveIndex, liveSubject } from "../streamBlocks";
import { isHiddenTool, toolMeta } from "../toolMeta";
import type { StreamBlock } from "../streamBlocks";
import { VisualBlocks } from "./visual/VisualBlocks";
import { readBlocks } from "./visual/visualModel";
import { parsePartialJson } from "./visual/partialJson";
import { stripVisualMarkers } from "./visual/answerVisuals";

/** One line of a subject, ellipsized — a path or pattern never wraps the live row. */
function oneLine(text: string, max = 72): string {
  const flat = text.replace(/\s+/g, " ").trim();
  return flat.length > max ? flat.slice(0, max) + "…" : flat;
}

const SHELL_TOOLS = new Set(["run_shell", "terminal_send"]);

/**
 * A tool call as it is being written — the name as soon as the provider sends it, the
 * arguments as they arrive.
 *
 * Deliberately not a tool card: nothing has run, there is no result, and no approval has
 * been asked. The card arrives with `tool_proposed`, a moment later, when the engine
 * dispatches the parsed call. This row exists for that moment in between, which on a slow
 * model is several seconds of a screen that used to say nothing at all.
 */
function LiveToolCall({ name, argsRaw, written = false }: { name: string; argsRaw: string; written?: boolean }) {
  if (!name) return null;
  // A visual draws itself as its arguments arrive: each block appears once its
  // fields have landed, the way the finished call will show it.
  if (name === "show_visual") return <VisualBlocks blocks={readBlocks(parsePartialJson(argsRaw))} streaming />;
  // While a call is being WRITTEN, the row reads as the action it will be — the family's
  // running verb ("Editing", "Running", "Searching") and its subject (a path, a pattern) —
  // never the raw argument JSON. The JSON preview put `{"command": "python3 -c \"\nimport…`
  // and `replace_in_file {"path": …, "old": …` on screen for the whole stream (GUI review
  // F1). A shell command shows as `$ command`, unescaped and growing, the way Claude Code
  // and Codex show one. A tool with its OWN surface (plan/folder/question card) and any MCP
  // tool show the verb alone. An edit shows its `+N/−M`.
  const meta = toolMeta(name);
  const churn = churnFromArgs(name, argsRaw);
  const hasChurn = churn !== null && (churn.added > 0 || churn.removed > 0);
  const subject = isHiddenTool(name) ? null : liveSubject(name, argsRaw);
  const shell = subject !== null && SHELL_TOOLS.has(name);
  return (
    <div className="live-call" data-testid="live-call" data-written={written || undefined}>
      {/* The family icon: the same glyph this call's finished row will wear, so the live
          view and the settled transcript read as one continuous list. */}
      <Icon name={meta.icon} size={14} className="live-call-ico" running={!written} />
      {/* The verb, not a finished sentence: `humanize` writes "Ran ls" from complete
          arguments, and here they are half-typed. The verb SHIMMERS (the reference design's
          TextShimmer) — that motion is the "still arriving" signal.
          A call the model has finished writing while it writes the next one has not run either:
          the engine runs a message's calls only once the whole message has arrived. It reads as
          the row it becomes then, the noun at rest and "waiting to run". "Editing" over it, still
          shimmering, read as a write that never finished (owner's screenshot, 2026-10-20). */}
      <span className={"live-call-name" + (written ? "" : " shimmer-text")}>{written ? meta.label : (meta.running ?? meta.label)}</span>
      {subject !== null && !shell && <span className="live-call-args">{oneLine(subject)}</span>}
      {shell && <span className="live-call-args live-call-cmd">$ {oneLine(subject ?? "", 120)}</span>}
      {churn && hasChurn && (
        <span className="tool-row-churn" data-testid="live-churn">
          {churn.added > 0 && <span className="churn-add">+{churn.added}</span>}
          {churn.removed > 0 && <span className="churn-del">−{churn.removed}</span>}
        </span>
      )}
      {written && (
        <span className="tool-row-queued" data-testid="live-call-queued">
          waiting to run
        </span>
      )}
    </div>
  );
}

export function LiveStep({
  blocks,
  showText,
  hideLiveThought = false,
  part = "all",
}: {
  blocks: StreamBlock[];
  /** Whether the stream gate has promoted this step's prose to the answer. */
  showText: boolean;
  /** Leave the trace being written off this view: the running turn's status line carries
   *  its newest line instead (design B, 2026-09-28). A finished trace still shows here as
   *  its "Thought · Ns" row, timed from when this view first saw it. */
  hideLiveThought?: boolean;
  /** Which half of the step to draw (owner, 2026-09-30: the answer streams OFF the
   *  timeline). "steps": traces, calls being written, and prose that a later call in this
   *  step makes narration. "answer": the prose after the step's last call, which is the
   *  answer until a call follows it. "all": both, as one view. */
  part?: "all" | "steps" | "answer";
}) {
  const live = liveIndex(blocks);
  const lastCall = blocks.reduce((at, block, index) => (block.kind === "tool_call" ? index : at), -1);
  const inPart = (block: StreamBlock, index: number): boolean => {
    if (part === "all") return true;
    const answerText = block.kind === "text" && index > lastCall;
    return part === "answer" ? answerText : !answerText;
  };
  // Live traces are measured with the WALL CLOCK, not with event stamps: a streaming block
  // has no stamp until it settles, and the interval a reader is watching is exactly the one
  // this component was rendering for. `spans` records, per block index, when we first saw it
  // and when it stopped being the live one — the moment its Thought row stops saying "Think"
  // and starts saying "Thought · Ns". A stale span for an index that changed kind is dropped
  // on sight, because block indexes are recycled by the fold.
  //
  // Deliberately bookkept DURING RENDER rather than in an effect: the closing happens on the
  // render where the block stops being live, and an effect would close it one paint late —
  // the row would show "Thought" with nothing to trigger the update that adds the duration.
  const spans = useRef(new Map<number, { start: number; end?: number; kind: string }>());
  const now = Date.now() / 1000;
  const thoughtSecs = (index: number, block: StreamBlock): number | undefined => {
    const span = spans.current.get(index);
    if (!span || span.kind !== block.kind) {
      spans.current.set(index, { start: now, kind: block.kind });
      return undefined;
    }
    if (block.kind === "reasoning" && index !== live && span.end === undefined) span.end = now;
    if (span.end === undefined || span.end <= span.start) return undefined;
    return span.end - span.start;
  };
  const rows = blocks.map((block, index) => {
    if (!inPart(block, index)) return null;
    if (block.kind === "reasoning") {
      if (!block.text.trim()) return null;
      const secs = thoughtSecs(index, block);
      if (hideLiveThought && index === live) return null;
      // `live` marks the block still being written — the only one whose summary should
      // follow the tail. An earlier trace in the same step is finished and reads better as
      // its opening line, and carries the clock of how long it ran.
      return (
        <ThinkRow
          key={index}
          text={block.text}
          running={index === live}
          duration={index === live ? undefined : secs}
        />
      );
    }
    if (block.kind === "tool_call") {
      return <LiveToolCall key={index} name={block.name} argsRaw={block.argsRaw} written={index !== live} />;
    }
    if (!showText || !block.text) return null;
    return (
      <div className="live-text" key={index}>
        {/* `streaming`: the tail is repaired (an unclosed fence or `**bold` stays readable
            instead of flickering), which is the reference design's `parseIncompleteMarkdown`. The parse is
            NOT batched per animation frame — rAF stops firing in a hidden tab, and text that
            freezes when the user switches tabs is worse than the parse it saves; the
            per-block memo above is what keeps each token cheap. The settled bubble drops the
            flag, since repair completes what only LOOKS open. */}
        <Markdown text={stripVisualMarkers(block.text)} streaming />
        {index === live && <span className="stream-cursor">▍</span>}
      </div>
    );
  });
  if (rows.every((row) => row === null)) return null;

  // The answer half is the answer's own shape from its first word, so the finished message
  // takes the same place and box: nothing moves or re-enters when the stream settles.
  if (part === "answer") {
    return (
      <div className="group bubble-assistant" data-testid="live-answer">
        {rows}
      </div>
    );
  }

  // One bubble for the whole step, never one per text block. A step that says something,
  // calls a tool and speaks again is ONE assistant turn, and a second "assistant" label
  // mid-step would read as a second speaker. It is also how the finalized message renders
  // (Transcript's assistant case: the trace, then the prose), so the bubble does
  // not appear or change shape when the stream settles.
  //
  // No bubble until there is prose in it: a step that has only thought so far renders the
  // trace bare, matching the thinking-only assistant item for the same reason.
  const speaking =
    showText && blocks.some((b, index) => inPart(b, index) && b.kind === "text" && b.text !== "");
  if (!speaking) {
    return (
      <div className="transcript live-step" data-testid="live-step">
        {rows}
      </div>
    );
  }
  return (
    <div className="transcript live-step" data-testid="live-step">
      {/* No role label inside the bubble (owner 2026-09-19): the row list already says who
          is speaking, and a second "assistant" reads as a second speaker. */}
      <div className="group bubble-assistant">{rows}</div>
    </div>
  );
}
