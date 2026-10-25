// The rail's tab strip. A tab is its kind's mark and its full name; the open one sits on a soft
// pill that slides from tab to tab, and a hairline parts the others, as OpenDesign's tabs are
// drawn (the owner's pick, 2026-10-19; until then a 2px rule under the open tab). Start is not a
// tab: with none open the strip holds only + and the window controls (workspace panel
// redesign, 2026-10-11). The open tab may draw its own actions in the row (`onActionsSlot`).
//
// Motion stays light (ui-skills: feedback under 200ms): the pill moves by transform and eases
// its width, a new tab fades up, a closed one fades before it is removed and its neighbours
// slide into the gap (FLIP). Reduced motion turns all of it off.
import { useEffect, useLayoutEffect, useRef, useState, type KeyboardEvent as ReactKeyboardEvent } from "react";
import { Icon } from "../Icon";
import { RailKindIcon } from "./RailKindIcon";
import { railTabTitle, type OpenableTabKind, type RailTab, type RailTabKind } from "./tabs";

export type NewTabKind = OpenableTabKind;

interface Props {
  tabs: RailTab[];
  activeId: string;
  onSelect: (id: string) => void;
  onClose: (id: string) => void;
  onNew: (kind: NewTabKind) => void;
  /** A chip was dragged onto another: put `fromId` where `toId` sits. */
  onReorder: (fromId: string, toId: string) => void;
  /** Tabs closed in this session, most recent first — the overview's "Recently closed". */
  recentClosed: RailTab[];
  onReopen: (tab: RailTab) => void;
  onHide: () => void;
  fullscreen: boolean;
  onToggleFullscreen: () => void;
  /** A number beside a tab's name (Review: how many files changed). */
  counts?: Partial<Record<RailTabKind, number>>;
  /** How many folders the session sees — the + menu's Files line. */
  folderCount?: number;
  /** A tab's own name for itself (a terminal: its running command, else its folder). */
  titles?: Record<string, string>;
  /** Tabs where something runs: a dot beside the name. */
  busy?: Record<string, boolean>;
  /** How a subagent's tab ended, by tab id: its mark settles into a check, a cross or a stop. */
  outcomes?: Record<string, "completed" | "failed" | "stopped">;
  /** The place in the row where the open tab draws its own actions (a design's Export…). */
  onActionsSlot?: (element: HTMLDivElement | null) => void;
}

const CLOSE_MS = 120;
const SLIDE_MS = 180;
const EASE_OUT = "cubic-bezier(.22, 1, .36, 1)";

function reducedMotion(): boolean {
  return typeof window !== "undefined" && !!window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
}

export function RailTabs({
  tabs,
  activeId,
  onSelect,
  onClose,
  onNew,
  onReorder,
  recentClosed,
  onReopen,
  onHide,
  fullscreen,
  onToggleFullscreen,
  counts,
  folderCount,
  titles,
  busy,
  outcomes,
  onActionsSlot,
}: Props) {
  const titleOf = (tab: RailTab) => titles?.[tab.id] || railTabTitle(tab);
  const chips = tabs.filter((tab) => tab.kind !== "guide");
  const [menuOpen, setMenuOpen] = useState(false);
  // The menu opens away from the panel's nearest edge (many chips push the + right).
  const [menuSide, setMenuSide] = useState<"left" | "right">("left");
  // A tab's own click menu.
  const [tabMenu, setTabMenu] = useState<{ id: string; left: number } | null>(null);
  // Every open tab in one list, plus the ones recently closed: the strip scrolls, so a tab
  // can be out of sight. The control shows only when one is, or when a closed tab can return.
  const [overview, setOverview] = useState(false);
  const [overviewQuery, setOverviewQuery] = useState("");
  const [hidden, setHidden] = useState(0);
  const [closing, setClosing] = useState<string | null>(null);
  const [dragId, setDragId] = useState<string | null>(null);
  const [rule, setRule] = useState<{ x: number; w: number } | null>(null);
  const wrapRef = useRef<HTMLDivElement | null>(null);
  const overviewRef = useRef<HTMLDivElement | null>(null);
  const stripRef = useRef<HTMLDivElement | null>(null);
  const chipsRef = useRef<HTMLDivElement | null>(null);
  const ruleReady = useRef(false);
  // The chips' left edges as last laid out, for the slide into a closed tab's gap.
  const lefts = useRef(new Map<string, number>());
  // The live-reorder handler runs from dragover, which React may deliver after the state
  // has moved on; the ref keeps the dragged id honest for the whole gesture.
  const dragRef = useRef<string | null>(null);

  const chipEl = (id: string) => chipsRef.current?.querySelector<HTMLElement>(`[data-tab-id="${id}"]`) ?? null;
  // The open tab as of now, for a measure that runs outside a render (a resize).
  const activeRef = useRef(activeId);
  activeRef.current = activeId;
  /** The pill under the open tab, where and as wide as that chip is laid out now. */
  const placeRule = () => {
    const on = chipEl(activeRef.current);
    const next = on ? { x: on.offsetLeft, w: on.offsetWidth } : null;
    setRule((now) => (now && next && now.x === next.x && now.w === next.w ? now : next));
  };

  const toggleMenu = () => {
    if (!menuOpen) {
      const rail = wrapRef.current?.closest(".right-rail")?.getBoundingClientRect();
      const anchor = wrapRef.current?.getBoundingClientRect().right ?? 0;
      setMenuSide(rail && anchor > rail.left + rail.width - 250 ? "right" : "left");
    }
    setMenuOpen((open) => !open);
  };

  useEffect(() => {
    if (!menuOpen) return;
    const close = (event: MouseEvent) => {
      if (!wrapRef.current?.contains(event.target as Node)) setMenuOpen(false);
    };
    const escape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setMenuOpen(false);
    };
    window.addEventListener("mousedown", close);
    window.addEventListener("keydown", escape);
    return () => {
      window.removeEventListener("mousedown", close);
      window.removeEventListener("keydown", escape);
    };
  }, [menuOpen]);

  useEffect(() => {
    if (!tabMenu) return;
    const close = () => setTabMenu(null);
    window.addEventListener("mousedown", close);
    window.addEventListener("keydown", close);
    return () => {
      window.removeEventListener("mousedown", close);
      window.removeEventListener("keydown", close);
    };
  }, [tabMenu]);

  useEffect(() => {
    if (!overview) return;
    const close = (event: MouseEvent) => {
      if (!overviewRef.current?.contains(event.target as Node)) setOverview(false);
    };
    const escape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOverview(false);
    };
    window.addEventListener("mousedown", close);
    window.addEventListener("keydown", escape);
    return () => {
      window.removeEventListener("mousedown", close);
      window.removeEventListener("keydown", escape);
    };
  }, [overview]);

  const ids = chips.map((tab) => tab.id).join("|");

  // Measure after every layout that can move a chip: the rule under the open tab, how many
  // tabs the strip hides, and the slide of chips whose place changed.
  useLayoutEffect(() => {
    const box = chipsRef.current;
    if (!box) return;
    const animate = !reducedMotion();
    const next = new Map<string, number>();
    box.querySelectorAll<HTMLElement>("[data-tab-id]").forEach((el) => {
      const id = el.dataset.tabId || "";
      const left = el.offsetLeft;
      next.set(id, left);
      const before = lefts.current.get(id);
      if (animate && before !== undefined && before !== left && typeof el.animate === "function") {
        el.animate([{ transform: `translateX(${before - left}px)` }, { transform: "translateX(0)" }], {
          duration: SLIDE_MS,
          easing: EASE_OUT,
        });
      }
    });
    lefts.current = next;
    placeRule();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ids, activeId, counts?.review]);

  // A chip changes size without the tabs changing: a browser tab opens on its host and then its
  // page names itself, a terminal names its running command, a font arrives. The pill followed
  // only the effect above, so it kept the first width and the close button stood outside it
  // until another click (owner's screenshot, 2026-10-20; RailTabs.test reproduces it).
  useEffect(() => {
    const box = chipsRef.current;
    if (!box || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(() => placeRule());
    box.querySelectorAll<HTMLElement>("[data-tab-id]").forEach((el) => observer.observe(el));
    return () => observer.disconnect();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ids]);

  // The rule appears in place the first time, then slides.
  useEffect(() => {
    if (rule) ruleReady.current = true;
  }, [rule]);

  // How many tabs are out of sight, on scroll and on resize.
  useEffect(() => {
    const box = chipsRef.current;
    if (!box) return;
    const count = () => {
      const frame = box.getBoundingClientRect();
      let n = 0;
      box.querySelectorAll<HTMLElement>("[data-tab-id]").forEach((el) => {
        const r = el.getBoundingClientRect();
        if (r.width > 0 && (r.left < frame.left - 1 || r.right > frame.right + 1)) n += 1;
      });
      setHidden(n);
    };
    count();
    box.addEventListener("scroll", count, { passive: true });
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(count);
    observer?.observe(box);
    return () => {
      box.removeEventListener("scroll", count);
      observer?.disconnect();
    };
  }, [ids]);

  // The open tab is always in view.
  useEffect(() => {
    chipEl(activeId)?.scrollIntoView?.({ behavior: "smooth", block: "nearest", inline: "nearest" });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeId, ids]);

  const pick = (kind: NewTabKind) => {
    setMenuOpen(false);
    onNew(kind);
  };

  /** Close with a short fade; the neighbours slide into the gap once it is gone. */
  const closeSoftly = (id: string) => {
    if (reducedMotion()) {
      onClose(id);
      return;
    }
    setClosing(id);
    window.setTimeout(() => {
      setClosing((current) => (current === id ? null : current));
      onClose(id);
    }, CLOSE_MS);
  };

  /** The tab strip's own actions, on a clicked tab. */
  const runTabMenu = (action: "close" | "others" | "all") => {
    const target = tabMenu?.id;
    setTabMenu(null);
    if (!target) return;
    if (action === "close") closeSoftly(target);
    if (action === "others") chips.filter((t) => t.id !== target).forEach((t) => onClose(t.id));
    if (action === "all") chips.forEach((t) => onClose(t.id));
  };

  /** Arrow keys move between tabs (the tablist pattern); Delete closes the focused one. */
  const onChipKey = (event: ReactKeyboardEvent, index: number, id: string) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      onSelect(id);
      return;
    }
    if (event.key === "Delete") {
      event.preventDefault();
      closeSoftly(id);
      return;
    }
    const step = event.key === "ArrowRight" ? 1 : event.key === "ArrowLeft" ? -1 : 0;
    const target =
      event.key === "Home" ? chips[0] : event.key === "End" ? chips[chips.length - 1] : step ? chips[index + step] : undefined;
    if (!target) return;
    event.preventDefault();
    onSelect(target.id);
    chipEl(target.id)?.focus();
  };

  const showOverview = hidden > 0 || recentClosed.length > 0;
  const reviewCount = counts?.review ?? 0;

  return (
    <div className="rail-strip" data-testid="rail-tabs" ref={stripRef}>
      <div className="rail-chips" role="tablist" aria-label="Workspace tabs" data-testid="rail-chips" ref={chipsRef}>
        {chips.map((tab, index) => {
          const active = tab.id === activeId;
          const count = counts?.[tab.kind];
          return (
            <span
              key={tab.id}
              className={
                "rail-chip" + (active ? " on" : "") + (dragId === tab.id ? " dragging" : "") + (closing === tab.id ? " closing" : "")
              }
              data-tab-id={tab.id}
              data-kind={tab.kind}
              data-testid={`rail-tab-${tab.id}`}
              role="tab"
              aria-selected={active}
              tabIndex={active ? 0 : -1}
              title={titleOf(tab)}
              draggable
              onDragStart={(event) => {
                dragRef.current = tab.id;
                setDragId(tab.id);
                event.dataTransfer.effectAllowed = "move";
                event.dataTransfer.setData("text/plain", tab.id);
              }}
              onDragOver={(event) => {
                const dragged = dragRef.current;
                if (!dragged || dragged === tab.id) return;
                // Reorder live as the pointer crosses a chip: the strip is the preview.
                event.preventDefault();
                event.dataTransfer.dropEffect = "move";
                onReorder(dragged, tab.id);
              }}
              onDrop={(event) => {
                event.preventDefault();
                dragRef.current = null;
                setDragId(null);
              }}
              onDragEnd={() => {
                dragRef.current = null;
                setDragId(null);
              }}
              onClick={() => onSelect(tab.id)}
              onAuxClick={(event) => {
                // Middle-click closes.
                if (event.button !== 1) return;
                event.preventDefault();
                closeSoftly(tab.id);
              }}
              onContextMenu={(event) => {
                event.preventDefault();
                // Positioned inside the STRIP, not at the pointer: the chips scroller clips.
                const strip = stripRef.current?.getBoundingClientRect();
                const chip = (event.currentTarget as HTMLElement).getBoundingClientRect();
                setTabMenu({ id: tab.id, left: chip.left - (strip?.left ?? 0) });
              }}
              onKeyDown={(event) => onChipKey(event, index, tab.id)}
            >
              <span className="rail-chip-title">
                {/* Something running in the tab is told by its mark (a terminal's caret
                    blinks), not by a dot beside the name. */}
                <RailKindIcon
                  kind={tab.kind}
                  size={14}
                  open={active}
                  running={!!busy?.[tab.id]}
                  outcome={outcomes?.[tab.id]}
                />
                <span className="rail-chip-name">{titleOf(tab)}</span>
                {busy?.[tab.id] && <span className="sr-only">running</span>}
                {!!count && (
                  <span className="rail-chip-count" data-testid={`rail-tab-count-${tab.id}`}>
                    {count > 99 ? "99+" : count}
                  </span>
                )}
              </span>
              <button
                type="button"
                className="rail-chip-close"
                data-testid={`rail-tab-close-${tab.id}`}
                aria-label={`Close ${titleOf(tab)}`}
                title="Close"
                tabIndex={active ? 0 : -1}
                onClick={(event) => {
                  event.stopPropagation();
                  closeSoftly(tab.id);
                }}
              >
                <Icon name="x" size={11} />
              </button>
            </span>
          );
        })}
        {rule && (
          <span
            className={"rail-rule" + (ruleReady.current ? " slides" : "")}
            aria-hidden
            style={{ transform: `translateX(${rule.x}px)`, width: `${rule.w}px` }}
          />
        )}
      </div>
      {/* The + sits directly after the tabs; the fill pushes only the window controls. */}
      <div className="rail-new-wrap" ref={wrapRef}>
        <button
          type="button"
          className="rail-chrome-btn"
          data-testid="rail-new"
          aria-label="Open a tab"
          aria-haspopup="menu"
          aria-expanded={menuOpen}
          title="Open a tab"
          onClick={toggleMenu}
        >
          <Icon name="plus" size={14} className={menuOpen ? "rail-new-open" : undefined} />
        </button>
        {menuOpen && (
          <div
            className={"rail-new-menu" + (menuSide === "right" ? " side-right" : "")}
            role="menu"
            aria-label="Open a tab"
            data-testid="rail-new-menu"
          >
            <div className="rail-new-menu-head">Open</div>
            <button type="button" role="menuitem" className="rail-new-item" data-testid="rail-new-review" onClick={() => pick("review")}>
              <RailKindIcon kind="review" size={14} />
              Review
              {reviewCount > 0 && (
                <span className="rail-new-hint">
                  {reviewCount} file{reviewCount === 1 ? "" : "s"}
                </span>
              )}
            </button>
            <button type="button" role="menuitem" className="rail-new-item" data-testid="rail-new-files" onClick={() => pick("files")}>
              <RailKindIcon kind="files" size={14} />
              Files
              {!!folderCount && (
                <span className="rail-new-hint">
                  {folderCount} folder{folderCount === 1 ? "" : "s"}
                </span>
              )}
            </button>
            <button
              type="button"
              role="menuitem"
              className="rail-new-item"
              data-testid="rail-new-terminal"
              onClick={() => pick("terminal")}
            >
              <RailKindIcon kind="terminal" size={14} />
              Terminal
              <kbd className="rail-new-hint mono">Ctrl `</kbd>
            </button>
            <button type="button" role="menuitem" className="rail-new-item" data-testid="rail-new-browser" onClick={() => pick("browser")}>
              <RailKindIcon kind="browser" size={14} />
              Browser
            </button>
            <button type="button" role="menuitem" className="rail-new-item" data-testid="rail-new-designs" onClick={() => pick("designs")}>
              <RailKindIcon kind="designs" size={14} />
              Designs
            </button>
          </div>
        )}
      </div>
      {/* Where the tab row is the window's title row, its free stretch moves the window. */}
      <span className="rail-strip-fill" data-drag-region />
      <div className="rail-actions" ref={onActionsSlot} />
      {tabMenu && (
        <div className="rail-tab-menu" role="menu" data-testid="rail-tab-menu" style={{ left: Math.max(0, tabMenu.left) }}>
          <button type="button" role="menuitem" onClick={() => runTabMenu("close")}>
            Close
          </button>
          <button type="button" role="menuitem" onClick={() => runTabMenu("others")}>
            Close other tabs
          </button>
          <button type="button" role="menuitem" onClick={() => runTabMenu("all")}>
            Close all tabs
          </button>
        </div>
      )}
      <div className="rail-chrome">
        {showOverview && (
          <div className="rail-overview-wrap" ref={overviewRef}>
            <button
              type="button"
              className="rail-chrome-btn rail-overview-btn"
              data-testid="rail-overview"
              aria-label={hidden > 0 ? `All tabs, ${hidden} out of sight` : "All tabs"}
              aria-haspopup="dialog"
              aria-expanded={overview}
              title="All tabs"
              onClick={() => {
                setOverviewQuery("");
                setOverview((open) => !open);
              }}
            >
              <Icon name="list" size={14} />
              {hidden > 0 && <span className="rail-overview-more">+{hidden}</span>}
            </button>
            {overview && (
              <div className="rail-overview" role="dialog" aria-label="All tabs" data-testid="rail-overview-pop">
                <input
                  className="rail-overview-search"
                  data-testid="rail-overview-search"
                  placeholder="Find a tab"
                  aria-label="Find a tab"
                  value={overviewQuery}
                  autoFocus
                  onChange={(event) => setOverviewQuery(event.target.value)}
                />
                <div className="rail-overview-list">
                  {chips
                    .filter((tab) => railTabTitle(tab).toLowerCase().includes(overviewQuery.trim().toLowerCase()))
                    .map((tab) => (
                      <button
                        key={tab.id}
                        type="button"
                        className={"rail-overview-row" + (tab.id === activeId ? " on" : "")}
                        data-testid={`rail-overview-tab-${tab.id}`}
                        onClick={() => {
                          onSelect(tab.id);
                          setOverview(false);
                        }}
                      >
                        <RailKindIcon kind={tab.kind} size={13} running={!!busy?.[tab.id]} outcome={outcomes?.[tab.id]} />
                        <span className="rail-overview-name">{railTabTitle(tab)}</span>
                        <span
                          className="rail-overview-close"
                          role="button"
                          aria-label={`Close ${railTabTitle(tab)}`}
                          title="Close"
                          onClick={(event) => {
                            event.stopPropagation();
                            onClose(tab.id);
                          }}
                        >
                          <Icon name="x" size={11} />
                        </span>
                      </button>
                    ))}
                </div>
                {recentClosed.length > 0 && (
                  <div className="rail-overview-recent" data-testid="rail-overview-recent">
                    <div className="rail-overview-head">Recently closed</div>
                    {recentClosed.map((tab, index) => (
                      <button
                        key={`${tab.id}-${index}`}
                        type="button"
                        className="rail-overview-row closed"
                        data-testid={`rail-overview-reopen-${tab.id}`}
                        title={`Reopen ${railTabTitle(tab)}`}
                        onClick={() => {
                          onReopen(tab);
                          setOverview(false);
                        }}
                      >
                        <RailKindIcon kind={tab.kind} size={13} />
                        <span className="rail-overview-name">{railTabTitle(tab)}</span>
                      </button>
                    ))}
                  </div>
                )}
              </div>
            )}
          </div>
        )}
        <button
          type="button"
          className={"rail-chrome-btn" + (fullscreen ? " on" : "")}
          data-testid="rail-fullscreen"
          aria-label={fullscreen ? "Exit full screen" : "Full screen"}
          aria-pressed={fullscreen}
          title={fullscreen ? "Exit full screen" : "Full screen"}
          onClick={onToggleFullscreen}
        >
          <Icon name={fullscreen ? "minimize" : "expand"} size={14} />
        </button>
        <button type="button" className="rail-chrome-btn" data-testid="rail-hide" aria-label="Hide panel" title="Hide panel" onClick={onHide}>
          <Icon name="panelClose" size={14} />
        </button>
      </div>
    </div>
  );
}
