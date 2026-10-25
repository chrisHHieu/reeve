// A run of consecutive same-family lifecycle calls, folded under one header.
//
// `run_shell` followed by its `shell_task_output`/`shell_task_kill` polls is ONE piece of
// work a reader follows, not one row per poll. This is the MCP-group posture applied to the
// detached-run shell family (the reference design groups nothing here; this is Reeve's own call). A delegated `task` child is NOT folded: each is its own
// card (see `groupKey`). Collapsed by default; opening it lists each call. A single call is
// NOT folded — it is already one clean line, and a header over a lone step would be a
// heading with nothing to head.

import { useState, memo } from "react";
import { sameItems } from "./rowMemo";
import type { ToolItem } from "../cards/cardModels";
import { groupMeta } from "../toolMeta";
import { Icon } from "./Icon";
import { ToolCallRow } from "./ToolCallRow";
import { Disclosure } from "../ui/Disclosure";

function FamilyGroupRowInner({
  familyKey,
  tools,
  onReveal,
  onOpenChild,
}: {
  /** "shell" or "subagent" — selects the family's icon and word. */
  familyKey: string;
  tools: ToolItem[];
  onReveal?: (path: string) => void;
  /** A delegated call inside the group: open its child session. */
  onOpenChild?: (run: { runId: string; title: string }) => void;
}) {
  const [open, setOpen] = useState(false);
  const running = tools.some((t) => t.status === "…");
  const meta = groupMeta(familyKey);
  const toggle = () => setOpen((v) => !v);
  return (
    <div
      className="tool-row family-group"
      data-family={familyKey}
      data-state={running ? "running" : "ok"}
      data-testid="family-group"
    >
      <div
        className="tool-row-line"
        role="button"
        tabIndex={0}
        aria-expanded={open}
        onClick={toggle}
        onKeyDown={(event) => {
          if (event.key !== "Enter" && event.key !== " ") return;
          event.preventDefault();
          toggle();
        }}
      >
        <span className="tool-row-leading">
          <Icon name={meta.icon} size={14} running={running} />
        </span>
        <span className={"tool-row-title" + (running ? " shimmer-text" : "")}>
          {running ? meta.running : (meta.done ?? meta.label)}
        </span>
        <span className="tool-row-summary">
          {tools.length} call{tools.length === 1 ? "" : "s"}
        </span>
        <span className={"tool-row-chevron" + (open ? " open" : "")} aria-hidden>
          <Icon name="chevronRight" size={12} />
        </span>
      </div>
      {/* The calls open and close as a motion, the way one row's body does. */}
      <Disclosure open={open}>
        <div className="explore-group-body">
          {tools.map((t) => (
            <ToolCallRow key={t.id} tool={t} onOpenFile={onReveal} onOpenChild={onOpenChild} />
          ))}
        </div>
      </Disclosure>
    </div>
  );
}

export const FamilyGroupRow = memo(
  FamilyGroupRowInner,
  (a, b) =>
    a.familyKey === b.familyKey &&
    sameItems(a.tools, b.tools) &&
    a.onReveal === b.onReveal &&
    a.onOpenChild === b.onOpenChild,
);
