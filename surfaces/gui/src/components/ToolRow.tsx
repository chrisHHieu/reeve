// One tool call, as a single collapsed line with an optional card underneath.
//
// The anatomy of the collapsed line is the whole design, and it is a fixed five-part rhythm:
//
//     [leading 16px] gap [Title 13px] gap [· 2px dot] gap [summary, one line, ellipsized]
//
//   The leading slot shows the tool's icon, and YIELDS to a status dot on error or when
//   stopped. A running row keeps its icon: the in-flight signal is the shimmer across the
//   TITLE, and swapping the icon for a dot would make a working
//   row look like a finished one.
//
//   The separator is a 2px dot, not a colon or a dash. It is decoration with no text, so it
//   cannot be read aloud, searched for, or confused with content.
//
//   The summary is the only flexible part: one line, ellipsized. On an error row it is
//   REPLACED by the failure's first line, because "exit 1" is the fact and the description
//   the model wrote is no longer the truth.
//
//   The suffix sits outside the ellipsized span. That is what keeps a todo row's `+2` from
//   being clipped off by a long item name — the one piece of the todo row that is load
//   bearing rather than cosmetic.
//
// The expanded body is a SIBLING of the header, so clicking inside a diff to select a line
// does not fold the row. The card wins over the plain text output when there is one: a card
// is a better rendering of the same result, and showing both would double every payload.

import { useContext, useState, type ReactNode } from "react";
import type { ApprovalDecision } from "../types";
import type { RowSpec } from "../cards/registry";
import { DiffBlock } from "../cards/DiffBlock";
import { TerminalBlock } from "../cards/TerminalBlock";
import { ReadBlock } from "../cards/ReadBlock";
import { SearchBlock } from "../cards/SearchBlock";
import { WebBlock } from "../cards/WebBlock";
import { CodeBlock } from "../cards/CodeBlock";
import { SkillBlock } from "../cards/SkillBlock";
import { FoldBlock } from "../cards/FoldBlock";
import { PlanBlock } from "../cards/PlanBlock";
import { FieldsBlock } from "../cards/FieldsBlock";
import { VisualBlocks } from "./visual/VisualBlocks";
import { DesignCheckLine } from "./DesignCheckLine";
import { OPEN_DESIGN_APP_EVENT } from "./rail/tabs";
import { DesignCards } from "./designCards";

import { revealVisual } from "./visual/VisualPlacement";
import { fieldsOf } from "../cards/fields";
import { DisclosureRow } from "./primitives/DisclosureRow";
import { ImageOverlay } from "./markdown/MarkdownImage";
import { StateDot } from "./primitives/StateDot";
import { CopyButton } from "./primitives/CopyButton";
import { Elapsed } from "../ui";
import { Icon } from "./Icon";
import { ConnectorIcon } from "../connectors/ConnectorIcon";
import { FileIcon } from "./FileIcon";
import { JsonTree } from "./JsonTree";
import { argsLine, serverIdentity, serverName, toolWords } from "../toolMeta";
import { useConnectorTools } from "../connectorTools";
import { shellTitle, type ShellKind } from "../shellKind";


/** The word a screen reader gets for a state the dot only shows as colour. */
function stateStatus(state: RowSpec["state"]): string | null {
  switch (state) {
    case "running":
      return "Running";
    case "error":
      return "Failed";
    case "stopped":
      return "Stopped";
    default:
      return null;
  }
}

/** "4.2s", "1m 12s"; nothing under a second, where a number would only be noise. */
export function formatTook(ms: number | undefined): string | null {
  if (typeof ms !== "number" || !(ms >= 1000)) return null;
  const seconds = ms / 1000;
  if (seconds < 10) return `${seconds.toFixed(1)}s`;
  if (seconds < 60) return `${Math.round(seconds)}s`;
  const whole = Math.round(seconds);
  return `${Math.floor(whole / 60)}m ${String(whole % 60).padStart(2, "0")}s`;
}

/** The leading slot: a dot where the state matters more than the tool, else the tool. */
function leading(state: RowSpec["state"], icon: ReactNode): ReactNode {
  if (state === "error") return <StateDot state="error" size={10} />;
  if (state === "stopped") return <StateDot state="warning" size={10} />;
  return icon;
}

/**
 * Whether the row has anything behind it. Exported because a RUNNING row is auto-opened by
 * its own component (`ToolCallRow`), and the answer has to be the same one that decides
 * whether the row is a disclosure at all — two expressions of that rule would drift into a
 * row that claims to open and then shows nothing.
 */
export function hasBody(spec: RowSpec): boolean {
  // A plan waiting on the person is drawn whole under its row: it is the thing being
  // judged, so it must not need a click. Once answered it folds behind the row.
  if (spec.plan) return spec.state !== "running";
  // A visual is drawn under its row, like a plan: the row has nothing of its own to open.
  if (spec.visual) return false;
  if (spec.fields) return spec.fields.length > 0;
  const card =
    spec.terminal ??
    spec.diff ??
    spec.read ??
    spec.search ??
    spec.web ??
    spec.skill ??
    spec.fold ??
    spec.code;
  return spec.body !== null || spec.output !== null || card !== undefined;
}

/** The approval outcome as a chip: the reader's own decision, recorded on the call it
 *  governed. It lives here (not in Transcript) because both the tool row and the
 *  declined-ask row wear it. */
export function approvalChip(resolved: ApprovalDecision | undefined) {
  if (resolved === "deny")
    return <span className="text-[11px] px-1.5 rounded-full bg-dangerSoft text-danger shrink-0">✕ declined</span>;
  return (
    <span
      className="text-[11px] px-1.5 rounded-full bg-okSoft text-ok shrink-0"
      title={resolved ? `approved · ${resolved.replace(/_/g, " ")}` : "approved"}
    >
      ✓ approved
    </span>
  );
}

/** A picture a tool returned (a read image, a screenshot): drawn in the row's body, and opened large
 *  in the viewer the conversation's other pictures use (step, zoom, download) on a click. */
function ToolImage({ src, alt }: { src: string; alt: string }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button type="button" className="tool-image-open" onClick={() => setOpen(true)} aria-label={`Open ${alt}`} title="Click to enlarge">
        <img className="tool-image" src={src} alt={alt} data-testid="tool-image" draggable={false} />
      </button>
      {open && <ImageOverlay images={[{ src, alt }]} start={0} onClose={() => setOpen(false)} />}
    </>
  );
}

export function ToolRow({
  spec,
  open,
  onToggle,
  testId = "turn-step",
  startedAt,
  queued = false,
  onOpenFile,
  onOpenChild,
  trailing,
  shell,
  host,
  visualInAnswer,
  visualFolded,
  tookMs,
}: {
  spec: RowSpec;
  open: boolean;
  onToggle: () => void;
  testId?: string;
  /** When the call was dispatched. A running row counts up from here. */
  startedAt?: number;
  /** Written whole but not dispatched yet: its batch runs in order and an earlier call is
   *  still going. The row rests and says so instead of shimmering as if it were working. */
  queued?: boolean;
  onOpenFile?: (path: string) => void;
  /** A delegated call: open the child's session in its own view. */
  onOpenChild?: () => void;
  /** A trailing chip on the collapsed line (the call's approval outcome). */
  trailing?: ReactNode;
  /** A shell call's interpreter (`bash` / `PowerShell` / `cmd`); the title says which. */
  shell?: ShellKind;
  /** A remote shell call's `user@host`; the card shows where the command ran. */
  host?: string;
  /** The call whose visual the turn's answer draws: the row is then one line that
   *  links to it, instead of a second copy of the visual. */
  visualInAnswer?: string;
  /** A visual the turn's answer left out (a draft, a second try): folded behind a toggle. */
  visualFolded?: boolean;
  /** The call's own wall time, from the server's clock. Said once it is a second or more. */
  tookMs?: number;
}) {
  const [visualOpen, setVisualOpen] = useState(false);
  const designCards = useContext(DesignCards);
  // A proposed plan is not work in flight: the call is open because it waits on the person.
  const planWaiting = spec.plan !== undefined && spec.state === "running";
  // Next in line: drawn at rest, like a plan waiting on its OK, but muted, not warm.
  const nextInLine = queued && spec.state === "running" && !planWaiting;
  const atRest = planWaiting || nextInLine;
  const queuedLabel = (
    <span className="tool-row-queued" data-testid="tool-queued">
      waiting to run
    </span>
  );
  // A plan the person sent back is their answer, not a call that broke: no red mark.
  const planBack = spec.plan !== undefined && spec.state === "error";
  const status = spec.plan !== undefined ? null : stateStatus(spec.state);
  // A shell row is titled by the interpreter it ran in ("PowerShell", "bash"), because the
  // kind is the difference between reading the command correctly and misreading it. Rows
  // whose tool has no shell keep the family word.
  const shellOrOwn = shell ? shellTitle(shell) : spec.title;
  // A row that calls out to a server wears that server's connector logo (MCP server or a
  // connector like Gmail) instead of the generic tool glyph — the brand mark a reader
  // recognizes. The connector index supplies logo + brand color for the non-MCP half.
  const connectorTools = useConnectorTools();
  const identity = spec.name ? serverIdentity(spec.name, connectorTools) : null;
  // A server's row is titled by the server, in every state: the logo and the name say who
  // was called, the shimmer says it is running. Then the tool, then what it was sent. A
  // server's tools are not a fixed set, so the row is built from what every call has.
  const title = identity ? serverName(identity) : shellOrOwn;
  const served = identity && spec.name ? { what: toolWords(spec.name, identity), args: argsLine(spec.body) } : null;
  // The lead glyph is the TOOL's (the Edit row: pencil, then the file's own logo, then the
  // name). The file-type icon moved next to the name below, where it labels the file rather
  // than the action — a reader scanning the left column sees what was done, and a reader
  // reading the line sees what was touched.
  const leadIcon = identity ? (
    <ConnectorIcon
      connector={{ logo: identity.logo, brand_color: identity.brandColor }}
      size={14}
    />
  ) : (
    // A running row's glyph moves (the caret blinks, the lens circles); a finished one is still.
    <Icon name={spec.icon} size={14} running={spec.state === "running" && !atRest} />
  );
  // A card is the result, rendered better. Both would double the payload.
  const card = spec.terminal ? (
    <TerminalBlock {...spec.terminal} shell={shell} host={host} />
  ) : spec.diff ? (
    <DiffBlock diffs={spec.diff.diffs} path={spec.diff.path} onOpenFile={onOpenFile} />
  ) : spec.read ? (
    <ReadBlock
      lines={spec.read.lines}
      totalLines={spec.read.totalLines}
      path={spec.read.path}
      lang={spec.read.lang}
    />
  ) : spec.search ? (
    <SearchBlock card={spec.search.card} recovery={spec.search.recovery} pattern={spec.search.pattern} />
  ) : spec.web ? (
    <WebBlock card={spec.web} />
  ) : spec.skill ? (
    <SkillBlock card={spec.skill} />
  ) : spec.fold ? (
    <FoldBlock note={spec.fold.note} card={spec.fold.card} />
  ) : spec.code ? (
    <>
      <CodeBlock code={spec.code.text} lang={spec.code.lang} label={spec.title} />
      {spec.code.output !== undefined && <CodeBlock code={spec.code.output} label="Output" />}
    </>
  ) : spec.fields && !spec.plan ? (
    <FieldsBlock rows={spec.fields} testId="interaction-fields" />
  ) : null;
  // Reeve's own tools read as labelled lines; only an MCP server's payload, whose shape
  // nobody here knows, keeps the JSON tree (owner rule, 2026-09-28).
  // A server's payload keeps the JSON tree, a connector's as much as an MCP server's: their
  // shapes are not ours to know (owner, 2026-09-28, widened to connectors 2026-10-13).
  const mcp = identity !== null || (spec.name ?? "").startsWith("mcp__");
  const io = (value: string, error: boolean) => {
    const rows = mcp ? null : fieldsOf(value);
    return rows && rows.length ? (
      <FieldsBlock rows={rows} testId="tool-io-fields" />
    ) : (
      <span className="tool-io-json" data-error={error || undefined}>
        <JsonTree value={value} error={error} clamp />
      </span>
    );
  };
  // A failed row keeps saying what was run, and says why it failed on a line of its own
  // under it. The reason used to replace the summary, so the row lost its command (owner,
  // 2026-10-13).
  const failure = spec.state === "error" && !planBack ? (spec.errorSummary ?? null) : null;
  const summary = spec.summary;
  const suffix = spec.summarySuffix ?? null;
  const fileLink = spec.filePath !== undefined && onOpenFile !== undefined;
  const took = spec.state === "running" ? null : formatTook(tookMs);
  const expandable = hasBody(spec);

  const openFile = (event: React.MouseEvent) => {
    // The row is the disclosure target, so a click on the path must not also fold the row —
    // the reader would lose the card they were about to read.
    event.stopPropagation();
    if (spec.filePath !== undefined) onOpenFile?.(spec.filePath);
  };

  return (
    <div
      className="tool-row"
      data-variant={spec.variant}
      // The kind of tool, for a palette that draws it (Cupertino's coloured tiles).
      data-icon={identity ? "connector" : spec.icon}
      data-state={planWaiting ? "waiting" : nextInLine ? "queued" : planBack ? "sentback" : spec.state}
      data-testid={testId}
    >
      {status !== null && <span className="sr-only">{status}</span>}
      <DisclosureRow
        rowClassName="tool-row-line"
        leadingClassName="tool-row-leading"
        // A running row's title shimmers: the motion is the
        // in-flight signal, and it rides the TEXT rather than a band sweeping the row.
        titleClassName={
          "tool-row-title" + (spec.state === "running" && !atRest ? " shimmer-text" : "")
        }
        icon={leading(planBack ? "ok" : spec.state, leadIcon)}
        title={title}
        open={open}
        expandable={expandable}
        expandOnRowClick
        keepContentWhenOpen
        onToggle={onToggle}
        collapsedContent={
          // A running row says what is running: its subject stays on the line, and the
          // clock joins it on the right once the call has been going for a second. The
          // row used to drop to its title and a clock while it ran (owner, 2026-10-13).
          served !== null ? (
            <>
              {identity?.isMcp && (
                <span className="mcp-tag" data-testid="mcp-tag">
                  {identity.plugin ? `MCP · plugin ${identity.plugin}` : "MCP"}
                </span>
              )}
              <span className="rv-dot" aria-hidden />
              <span className="tool-row-what" data-testid="tool-what">
                {served.what}
              </span>
              {served.args !== "" && <span className="tool-row-summary tool-row-args">{served.args}</span>}
              <span className="tool-row-meta">
                {nextInLine
                  ? queuedLabel
                  : spec.state === "running"
                  ? startedAt !== undefined && <Elapsed from={startedAt} className="tool-row-elapsed" after={1} />
                  : took !== null && (
                      <span className="tool-row-suffix" data-testid="tool-took">
                        {took}
                      </span>
                    )}
              </span>
              {trailing}
            </>
          ) : planWaiting ? (
            <>
              {summary !== "" && (
                <>
                  <span className="rv-dot" aria-hidden />
                  <span className="tool-row-summary">{summary}</span>
                </>
              )}
              <span className="tool-row-waiting" data-testid="plan-waiting">
                needs your OK
              </span>
            </>
          ) : spec.state === "running" ? (
            <>
              {summary !== "" && (
                <>
                  <span className="rv-dot" aria-hidden />
                  <span className="tool-row-summary">{summary}</span>
                </>
              )}
              {nextInLine
                ? queuedLabel
                : startedAt !== undefined && <Elapsed from={startedAt} className="tool-row-elapsed" after={1} />}
            </>
          ) : (
            <>
              {summary !== "" && (
                <>
                  <span className="rv-dot" aria-hidden />
                  {fileLink ? (
                    <button
                      type="button"
                      className="file-link tool-row-summary"
                      onClick={openFile}
                      title={spec.filePath}
                    >
                      {/* The file's OWN logo, detected from its name: the
                          extension is the fastest way to see what kind of thing this is. */}
                      {spec.filePath !== undefined && (
                        <span className="tool-row-fileico" aria-hidden>
                          <FileIcon path={spec.filePath} size={13} />
                        </span>
                      )}
                      {summary}
                    </button>
                  ) : (
                    <span className="tool-row-summary">{summary}</span>
                  )}
                  {/* What the call found and how long it took, in one column on the right:
                      a scan down the turn reads the facts without reading the rows. */}
                  <span className="tool-row-meta">
                  {suffix !== null && <span className="tool-row-suffix">{suffix}</span>}
                  {spec.churn && (spec.churn.added > 0 || spec.churn.removed > 0) && (
                    <span className="tool-row-churn" data-testid="tool-churn">
                      {/* A zero side is omitted, the rule: `+18` says "new lines" and
                          `+18 −0` says "new lines, and also I checked". */}
                      {spec.churn.added > 0 && <span className="churn-add">+{spec.churn.added}</span>}
                      {spec.churn.removed > 0 && (
                        <span className="churn-del">−{spec.churn.removed}</span>
                      )}
                    </span>
                  )}
                  {took !== null && <span className="tool-row-suffix" data-testid="tool-took">{took}</span>}
                  </span>
                  {trailing}
                  {visualInAnswer !== undefined && (
                    <button
                      type="button"
                      className="tool-run-open"
                      data-testid="visual-in-answer"
                      title="Show it in the answer"
                      onClick={(event) => {
                        event.stopPropagation();
                        revealVisual(visualInAnswer);
                      }}
                    >
                      ↓ In the answer
                    </button>
                  )}
                  {visualFolded && (
                    <button
                      type="button"
                      className="tool-run-open"
                      data-testid="visual-folded"
                      aria-expanded={visualOpen}
                      title="Not part of the answer"
                      onClick={(event) => {
                        event.stopPropagation();
                        setVisualOpen((v) => !v);
                      }}
                    >
                      {visualOpen ? "Hide" : "Show"}
                    </button>
                  )}
                  {/* A delegated call says how much work it did, and offers the one
                      affordance that is NOT the disclosure: open the child's session. */}
                  {onOpenChild && (
                    <>
                      {spec.stepCount !== undefined && (
                        <span className="tool-row-suffix" data-testid="run-steps">
                          · {spec.stepCount} steps
                        </span>
                      )}
                      <button
                        type="button"
                        className="tool-run-open"
                        data-testid="run-open"
                        title="Open this subagent's session"
                        onClick={(event) => {
                          // The row is a disclosure target; opening the run must not also
                          // fold the row the reader is looking at.
                          event.stopPropagation();
                          onOpenChild();
                        }}
                      >
                        ↗ Open
                      </button>
                    </>
                  )}
                </>
              )}
            </>
          )
        }
      >
        <div className="tool-row-body">
          {spec.image ? (
            <ToolImage src={spec.image} alt={spec.summary || "image"} />
          ) : (
            card ?? (
              // An answered plan's body is the plan itself and what was answered: an
              // args/result dump next to it would show the same text twice, once escaped.
              spec.plan ? (
                <>
                  <PlanBlock text={spec.plan.text} path={spec.plan.path} rejected={spec.state === "error"} />
                  {spec.fields && spec.fields.length > 0 && (
                    <FieldsBlock rows={spec.fields} testId="plan-verdict" />
                  )}
                </>
              ) : (
              <div className="tool-io">
                {spec.code === undefined && spec.body !== null && (
                  <div className="tool-io-section">
                    <span className="tool-io-label">IN</span>
                    {io(spec.body, false)}
                  </div>
                )}
                {spec.output !== null && (
                  <div className="tool-io-section">
                    <span className="tool-io-label">OUT</span>
                    {io(spec.output, spec.state === "error")}
                  </div>
                )}
                {/* What was called and how much came back. The text a person wants to paste
                    into a bug report is the failure's own output, so the copy is here. */}
                {spec.output !== null && (
                  <div className="tool-io-foot" data-testid="tool-io-foot">
                    <span>
                      {[spec.name, `${spec.output.length.toLocaleString("en-US")} characters`].filter(Boolean).join(" · ")}
                    </span>
                    <CopyButton
                      text={spec.output}
                      label={failure !== null ? "Copy error" : "Copy"}
                      copiedLabel={failure !== null ? "Error copied" : "Copied"}
                      className="tool-error-copy"
                    />
                  </div>
                )}
              </div>
              )
            )
          )}
        </div>
      </DisclosureRow>
      {/* Why it failed, on its own line under what was run. Folded only: opened, the
          body below is the failure's own output. */}
      {failure !== null && !open && (
        <div className="tool-row-why" data-testid="tool-row-why" title={failure}>
          {failure}
        </div>
      )}
      {/* What Reeve measured on a design page this call wrote. */}
      {spec.designCheck && spec.designCheck.length > 0 && (
        // The page's card under its latest change this turn; the earlier ones keep their line.
        <DesignCheckLine checks={spec.designCheck} compact={designCards !== null && !designCards.has(spec.designCall ?? "")} />
      )}
      {/* A compare of the app with its design opens the two side by side. */}
      {spec.designCompare && (
        <div className="design-compare-line">
          <button
            type="button"
            className="design-compare-open"
            data-testid="design-compare-review"
            onClick={() => window.dispatchEvent(new CustomEvent(OPEN_DESIGN_APP_EVENT, { detail: { path: spec.designCompare!.page, url: spec.designCompare!.url } }))}
          >
            <Icon name="gitCompare" size={14} />
            Review beside the design
          </button>
        </div>
      )}
      {/* A plan waiting on the person is shown INLINE under its row, whole: it is the thing
          the reader is being asked to judge, so it must not need a click. It still lives
          inside the turn group. Once answered it is the row's body instead. */}
      {spec.plan && planWaiting && <PlanBlock text={spec.plan.text} path={spec.plan.path} />}
      {/* A visual is drawn under its row until the answer takes it; one the answer
          left out opens on request. */}
      {spec.visual && visualInAnswer === undefined && (!visualFolded || visualOpen) && (
        <VisualBlocks blocks={spec.visual} streaming={spec.state === "running"} />
      )}
    </div>
  );
}
