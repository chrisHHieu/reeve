import { useEffect } from "react";
import type { SessionInfo } from "../../types";
import { Icon, type IconName } from "../Icon";

// A queue row's ⋮ menu: Rename, Fork, Pin, Archive, and Delete, which asks first (four
// icons on every row read as clutter). It is `position: fixed`, because a folded group clips what
// overflows it and a menu on one of its lower rows was cut off.

const WIDTH = 160; // w-40
// Five items and a divider. Only used to open upward near the window's foot.
const HEIGHT = 180;

/** Where the menu opens for the button it belongs to, which it keeps to know when it is left. */
export type RowMenuPlace = { top: number; left: number; anchor: HTMLElement };

export function placeRowMenu(anchor: HTMLElement): RowMenuPlace {
  const box = anchor.getBoundingClientRect();
  const below = box.bottom + 4;
  return { top: below + HEIGHT > window.innerHeight ? box.top - HEIGHT : below, left: Math.max(8, box.right - WIDTH), anchor };
}

type RowMenuProps = {
  session: SessionInfo;
  place: RowMenuPlace;
  onClose: () => void;
  onRename: () => void;
  onFork?: () => void;
  onTogglePin: () => void;
  onArchive: () => void;
  onDelete: () => void;
};

export function RowMenu({ session, place, onClose, onRename, onFork, onTogglePin, onArchive, onDelete }: RowMenuProps) {
  // Escape closes it, and so does scrolling anything that holds its row, which would leave the
  // fixed menu behind. Other scrolling (the transcript following a streaming turn, constantly)
  // leaves it open.
  useEffect(() => {
    const key = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    const scroll = (e: Event) => {
      if (e.target === document || (e.target instanceof Node && e.target.contains(place.anchor))) onClose();
    };
    window.addEventListener("keydown", key);
    window.addEventListener("scroll", scroll, true);
    return () => {
      window.removeEventListener("keydown", key);
      window.removeEventListener("scroll", scroll, true);
    };
  }, [place.anchor, onClose]);

  const item = (name: string, icon: IconName, label: string, act: () => void, danger = false) => (
    <button
      className={"w-full flex items-center gap-2 px-2.5 py-1.5 text-[13px] text-left hover:bg-paper" + (danger ? " text-danger" : "")}
      data-testid={`row-menu-${name}`}
      role="menuitem"
      onClick={() => {
        onClose();
        act();
      }}
    >
      <Icon name={icon} size={13} className={"shrink-0" + (danger ? "" : " text-muted")} />
      <span className="flex-1">{label}</span>
    </button>
  );

  return (
    <>
      {/* A press anywhere else closes it, and goes no further. */}
      <div
        className="fixed inset-0 z-40"
        onClick={(e) => {
          e.stopPropagation();
          onClose();
        }}
      />
      <div className="fixed z-50 w-40 rounded-xl border border-line bg-panel shadow-xl py-1" style={{ top: place.top, left: place.left }} role="menu" onClick={(e) => e.stopPropagation()}>
        {item("rename", "pencil", "Rename", onRename)}
        {onFork && item("fork", "gitFork", "Fork", onFork)}
        {item("pin", "pin", session.pinned ? "Unpin" : "Pin", onTogglePin)}
        {item("archive", "archive", session.archived ? "Unarchive" : "Archive", onArchive)}
        <div className="h-px bg-line my-1 mx-2" />
        {item("delete", "trash", "Delete…", onDelete, true)}
      </div>
    </>
  );
}
