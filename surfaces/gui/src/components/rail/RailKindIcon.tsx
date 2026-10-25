// One mark per workspace-panel kind, drawn the same way wherever the kind is named: the tab
// strip, the + menu, the Start page, the overview. The strip used to mix dots and a folder
// glyph, so the same tab looked different in each place.
import { Icon } from "../Icon";
import type { RailTabKind } from "./tabs";

export function RailKindIcon({
  kind,
  size = 14,
  open,
  running,
  outcome,
}: {
  kind: RailTabKind;
  size?: number;
  /** The tab is the one on screen: Files draws its folder open. */
  open?: boolean;
  /** Something runs in it: a terminal's caret blinks. */
  running?: boolean;
  /** A subagent's tab once the child settled: the mark its row wears in the chat. */
  outcome?: "completed" | "failed" | "stopped";
}) {
  if (kind === "subagent") {
    if (!running && outcome) {
      return (
        <span className="rail-mark" data-state={outcome}>
          <Icon name={outcome === "completed" ? "check" : outcome === "failed" ? "x" : "stop"} size={size} />
        </span>
      );
    }
    return (
      <span className="rail-mark" data-state={running ? "running" : "idle"}>
        <Icon name="bot" size={size} running={running} />
      </span>
    );
  }
  if (kind === "files") return <Icon name={open ? "folderOpen" : "folder"} size={size} />;
  if (kind === "designs") return <Icon name="layoutGrid" size={size} />;
  if (kind === "design") return <Icon name="appWindow" size={size} />;
  if (kind === "design-app") return <Icon name="gitCompare" size={size} />;
  if (kind === "terminal") return <Icon name="terminal" size={size} running={running} />;
  if (kind === "browser") return <Icon name="globe" size={size} running={running} />;
  // A page with a plus over a minus: a diff.
  if (kind === "review") return <Icon name="fileDiff" size={size} />;
  // The guide: a compass, on the same grid and stroke as the rest (Lucide's).
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.75}
      strokeLinecap="round"
      strokeLinejoin="round"
      className="ico ico-compass"
      aria-hidden="true"
    >
      <g className="ico-g">
        <circle cx="12" cy="12" r="10" />
        <polygon points="16.24 7.76 14.12 14.12 7.76 16.24 9.88 9.88 16.24 7.76" />
      </g>
    </svg>
  );
}
