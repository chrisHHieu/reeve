import { useState } from "react";
import type { Item, NoticeItem } from "../../types";

// Notices the transcript draws as more than a status line.

/** The item whose notice offers Retry: the last error, looking past the info lines after it (a
 *  switch of model must not use the retry up, since switching and then retrying is the way out)
 *  and past the failed turn's receipt, which lands after its error. -1 when the last thing is
 *  anything else. */
export function retryAnchor(items: Item[]): number {
  for (let i = items.length - 1; i >= 0; i--) {
    const item = items[i];
    if (item.kind === "changes") continue;
    if (item.kind !== "notice" || !(item.retriable || item.tone === "info")) return -1;
    if (item.retriable) return i;
  }
  return -1;
}

/** An MCP server that did not start, as one quiet line (owner, 2026-08-21): it names the server,
 *  keeps the raw error (stderr, a traceback) behind Details, and offers the Connectors page, where
 *  it is fixed or removed. Never a wall of error text in the conversation. */
export function McpNotice({ item, onOpenConnectors }: { item: NoticeItem; onOpenConnectors?: () => void }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="mcp-notice" data-testid="mcp-notice">
      <div className="mcp-notice-line">
        <span aria-hidden>⚠</span>
        <span className="min-w-0 truncate">{item.text}</span>
        <button className="mcp-notice-act" data-testid="mcp-notice-details" onClick={() => setOpen(!open)}>
          Details {open ? "⌃" : "⌄"}
        </button>
        {onOpenConnectors && (
          <button className="mcp-notice-act" data-testid="mcp-notice-connectors" onClick={onOpenConnectors}>
            Open Connectors
          </button>
        )}
      </div>
      {open && (
        <pre className="mcp-notice-detail" data-testid="mcp-notice-detail">
          {item.detail}
        </pre>
      )}
    </div>
  );
}

/** A notice that is prose: a slash command's output with its lines kept (`command`, `/goal`'s
 *  block), or a heading over paragraphs (`title`). Null for a notice that is neither. */
export function ProseNotice({ item }: { item: NoticeItem }) {
  const tone = item.tone === "warn" ? " warn" : "";
  if (item.command)
    return (
      <div className={"notice notice-block command-notice" + tone} data-testid="command-notice">
        {item.title && <div className="notice-title">{item.title}</div>}
        <p className="command-notice-body">{item.text}</p>
      </div>
    );
  if (!item.title) return null;
  return (
    <div className={"notice notice-block" + tone} data-testid="mode-notice">
      <div className="notice-title">{item.title}</div>
      {item.text.split("\n\n").map((paragraph, i) => (
        <p key={i}>{paragraph}</p>
      ))}
    </div>
  );
}
