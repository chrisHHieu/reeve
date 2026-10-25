import { useEffect, useRef, useState } from "react";
import type { SessionInfo } from "../types";
import { Icon } from "./Icon";

// Search opens over the whole app as a palette instead of filtering the sidebar where it stands
// (which collapsed the grouped list). It looks through every conversation, pinned ones first, then
// the rest, narrows as you type, and answers ↑/↓, Enter and ⌘1–⌘9.

const newestFirst = (a: SessionInfo, b: SessionInfo) => (b.updated_at || "").localeCompare(a.updated_at || "");
const HEADING = "px-2 py-1 text-[11px] uppercase tracking-[0.05em] text-faint font-semibold";
const KEY = "text-[11px] text-faint bg-paper border border-line rounded px-1.5 py-0.5 font-sans";

export function SearchModal({
  sessions,
  onSelect,
  onClose,
}: {
  sessions: SessionInfo[];
  onSelect: (id: string, workspace: string) => void;
  onClose: () => void;
}) {
  const [query, setQuery] = useState("");
  const [highlighted, setHighlighted] = useState(0);
  const input = useRef<HTMLInputElement>(null);
  useEffect(() => input.current?.focus(), []);

  const wanted = query.trim().toLowerCase();
  // Hidden threads (`__…`) and archived conversations are not offered.
  const found = sessions.filter(
    (s) => !s.session_id.startsWith("__") && !s.archived && (!wanted || (s.title || s.session_id).toLowerCase().includes(wanted)),
  );
  const pinned = found.filter((s) => s.pinned).sort(newestFirst);
  const recent = found.filter((s) => !s.pinned).sort(newestFirst);
  const inOrder = [...pinned, ...recent]; // what the arrows and ⌘N move through
  useEffect(() => setHighlighted(0), [wanted]);

  const open = (session = inOrder[highlighted]) => {
    if (!session) return;
    onSelect(session.session_id, session.workspace);
    onClose();
  };

  const onKey = (event: React.KeyboardEvent) => {
    const { key } = event;
    if ((event.metaKey || event.ctrlKey) && key >= "1" && key <= "9") {
      const target = inOrder[Number(key) - 1];
      if (target) {
        event.preventDefault();
        open(target);
      }
      return;
    }
    const moves: Record<string, () => void> = {
      Escape: onClose,
      ArrowDown: () => setHighlighted((i) => Math.min(i + 1, inOrder.length - 1)),
      ArrowUp: () => setHighlighted((i) => Math.max(i - 1, 0)),
      Enter: () => open(),
    };
    if (moves[key]) {
      event.preventDefault();
      moves[key]();
    }
  };

  const row = (session: SessionInfo, index: number) => (
    <button
      key={session.session_id}
      className={"w-full flex items-center gap-3 px-3 py-2 rounded-lg text-left " + (index === highlighted ? "bg-accentSoft" : "hover:bg-paper")}
      onMouseEnter={() => setHighlighted(index)}
      onClick={() => open(session)}
    >
      <span className="min-w-0 flex-1 truncate text-[13px] text-ink">{session.title || session.session_id}</span>
      {index < 9 && <kbd className={KEY + " shrink-0"}>⌘{index + 1}</kbd>}
    </button>
  );

  return (
    <div className="fixed inset-0 z-50" onKeyDown={onKey}>
      <div className="absolute inset-0 bg-black/30 backdrop-blur-[1px]" onClick={onClose} />
      <div className="absolute left-1/2 top-[14vh] -translate-x-1/2 w-[640px] max-w-[92vw] rounded-xl2 border border-line bg-panel shadow-2xl overflow-hidden">
        <div className="px-4 pt-3.5 pb-2.5 border-b border-line flex items-center gap-2.5">
          <Icon name="search" size={16} className="text-faint shrink-0" />
          <input
            ref={input}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search chats"
            className="flex-1 bg-transparent outline-none text-[14px] text-ink placeholder:text-faint"
          />
          <kbd className={KEY}>Esc</kbd>
        </div>
        <div className="max-h-[52vh] overflow-y-auto hairline-scroll py-2">
          {inOrder.length === 0 ? (
            <div className="px-4 py-8 text-center text-[13px] text-faint">No chats found.</div>
          ) : (
            <>
              {pinned.length > 0 && (
                <div className="px-2">
                  <div className={HEADING}>Pinned chats</div>
                  {pinned.map((s, i) => row(s, i))}
                </div>
              )}
              {recent.length > 0 && (
                <div className="px-2 mt-1">
                  <div className={HEADING}>Recent chats</div>
                  {recent.map((s, i) => row(s, pinned.length + i))}
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
