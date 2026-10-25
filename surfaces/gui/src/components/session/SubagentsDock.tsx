// The subagents: the session's delegated `task` runs, as a segment of the activity row above
// the composer (`ActivityRow.tsx`) and the roster that opens under it.
//
// The ones that need a look are on top: running, failed, stopped, or holding a worktree that
// waits on the person. The finished ones are behind "Finished N", closed, so thirty settled
// children are one line and not thirty (it was a 32-row list in the header's chip). With
// nothing on top the fold starts open: there is nothing else to show.
//
// A row is `SubagentRow`: status, agent + task, its live line, what it spent, and the door
// into the child's transcript.

import { memo, useMemo, useState } from "react";
import { Icon } from "../Icon";
import { Disclosure } from "../../ui/Disclosure";
import { rosterOrder, type DelegationNode } from "../../delegations";
import { SubagentRow } from "./SubagentRow";

export interface SubagentsPanelProps {
  /** The session's `task` runs, in spawn order (the transcript's own order). */
  nodes: readonly DelegationNode[];
  /** Open one child's transcript. Absent when the surface cannot navigate. */
  onOpenChild?: (child: { runId: string; title: string }) => void;
}

export const SubagentsPanel = memo(function SubagentsPanel({ nodes, onOpenChild }: SubagentsPanelProps) {
  const [expanded, setExpanded] = useState<string | null>(null);
  const [foldOpen, setFoldOpen] = useState<boolean | undefined>(undefined);
  const ordered = useMemo(() => rosterOrder(nodes), [nodes]);
  const active = ordered.filter((n) => n.outcome !== "completed" || n.kept);
  const finished = ordered.filter((n) => n.outcome === "completed" && !n.kept);
  const running = active.filter((n) => n.outcome === "running").length;
  const showFinished = foldOpen ?? active.length === 0;

  const row = (node: DelegationNode) => (
    <SubagentRow
      key={node.key}
      node={node}
      open={expanded === node.key}
      onToggle={() => setExpanded((v) => (v === node.key ? null : node.key))}
      onOpenChild={onOpenChild}
    />
  );

  return (
    <div className="act-roster">
      {active.length > 0 && (
        <>
          {finished.length > 0 && (
            <div className="act-h">{running === active.length ? `Running ${running}` : `Needs a look ${active.length}`}</div>
          )}
          <ul className="act-list">{active.map(row)}</ul>
        </>
      )}
      {finished.length > 0 && (
        <>
          <button
            type="button"
            className="act-fold"
            aria-expanded={showFinished}
            data-testid="subagents-finished-toggle"
            onClick={() => setFoldOpen(!showFinished)}
          >
            <Icon name="chevronRight" size={12} className={showFinished ? "open" : undefined} />
            Finished {finished.length}
          </button>
          <Disclosure open={showFinished}>
            <ul className="act-list">{finished.map(row)}</ul>
          </Disclosure>
        </>
      )}
    </div>
  );
});
