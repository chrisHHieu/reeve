// The rail's Start page: what the panel can show, as one list that reads from the top, each
// row carrying what is true of this session right now (how much changed, which folders, the
// shell, a dev server the conversation named), so a row is chosen on facts rather than on
// opening every tab to look. Start is not a tab: it is what the panel shows with none open
// (docs/mockups: the workspace panel redesign, 2026-10-11).
//
// Each row is a stretched button with its controls above it (the shell chip, the dev-server
// chip), so a control never nests inside a button. The terminal row's chip opens the launch
// menu (host-discovered shells, the remembered one checked) — `/v1/sessions/{id}/terminal/shells`.
import { useEffect, useRef, useState, type ReactNode } from "react";
import { listUserTerminalShells, type UserTerminalShell } from "../../api/terminal";
import { Icon } from "../Icon";
import { readShellPref, shellDisplayLabel } from "./shellPref";
import { railTabTitle, type RailTab } from "./tabs";
import { RailKindIcon } from "./RailKindIcon";

export type GuideDoor = "review" | "files" | "terminal" | "browser" | "designs";

export interface GuidePick {
  /** The shell id the menu picked (`cmd` / `bash` / `powershell`). */
  shell?: string;
  /** The address a Browser row's chip opens. */
  url?: string;
}

/** What the session's git working tree (or Reeve's own edits outside git) holds now. */
export interface GuideReview {
  files: number;
  add: number;
  del: number;
}

interface Props {
  sessionId: string;
  /** The session's shell name — the chip until a shell is picked. */
  shellLabel: string;
  onPick: (door: GuideDoor, pick?: GuidePick) => void;
  /** The primary folder's name, the page's title. */
  workspace?: string;
  /** Every folder the session can see, by name. */
  folders?: string[];
  review?: GuideReview | null;
  recentClosed?: RailTab[];
  onReopen?: (tab: RailTab) => void;
}

/** The panel toggle as this computer's keyboard writes it (App listens for ⌘/Ctrl + Alt + B). */
const HIDE_KEYS =
  typeof navigator !== "undefined" && /Mac|iPhone|iPad/.test(navigator.platform) ? "⌘ ⌥ B" : "Ctrl Alt B";

type MenuState =
  | { phase: "loading" }
  | { phase: "ready"; shells: UserTerminalShell[] }
  | { phase: "failed"; message: string };

function Chevron() {
  return (
    <span className="guide-row-chev" aria-hidden>
      <Icon name="chevronRight" size={14} />
    </span>
  );
}

function Row({
  door,
  title,
  question,
  label,
  onOpen,
  children,
}: {
  door: GuideDoor;
  title: string;
  /** The one question this tab answers: Review and Files were easy to mistake for each
   *  other while the row said only their names (2026-09-29 redesign). */
  question: string;
  label: string;
  onOpen: () => void;
  /** The row's facts and controls, on the right. */
  children?: ReactNode;
}) {
  return (
    <div className="guide-row">
      <button
        type="button"
        className="guide-row-hit"
        data-testid={`rail-door-${door}`}
        aria-label={label}
        aria-describedby={`guide-q-${door}`}
        onClick={onOpen}
      />
      <span className="guide-row-icon" aria-hidden>
        <RailKindIcon kind={door} size={17} />
      </span>
      <span className="guide-row-text">
        <span className="guide-row-title">{title}</span>
        <span className="guide-row-question" id={`guide-q-${door}`}>
          {question}
        </span>
      </span>
      {children}
      <Chevron />
    </div>
  );
}

export function GuideView({
  sessionId,
  shellLabel,
  onPick,
  workspace,
  folders = [],
  review,
  recentClosed = [],
  onReopen,
}: Props) {
  const [menuOpen, setMenuOpen] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const [state, setState] = useState<MenuState>({ phase: "loading" });
  const shellRef = useRef<HTMLSpanElement | null>(null);
  const remembered = readShellPref();

  // Shell discovery runs per open; a failed lookup offers Retry inside the menu.
  useEffect(() => {
    if (!menuOpen) return;
    let alive = true;
    setState({ phase: "loading" });
    listUserTerminalShells(sessionId).then(
      (result) => {
        if (!alive) return;
        if (!result?.ok || !Array.isArray(result.shells)) {
          setState({ phase: "failed", message: result?.error || "Could not read the shells." });
          return;
        }
        setState({ phase: "ready", shells: result.shells });
      },
      (error: unknown) => {
        if (!alive) return;
        setState({ phase: "failed", message: error instanceof Error ? error.message : String(error) });
      },
    );
    return () => {
      alive = false;
    };
  }, [menuOpen, attempt, sessionId]);

  useEffect(() => {
    if (!menuOpen) return;
    const close = (event: MouseEvent) => {
      if (!shellRef.current?.contains(event.target as Node)) setMenuOpen(false);
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

  const openTerminal = (pick?: GuidePick) => {
    setMenuOpen(false);
    onPick("terminal", pick);
  };

  const shellChip = shellDisplayLabel(remembered || shellLabel);
  const folderFact = folders.length ? folders.join(", ") : "";

  return (
    <div className="rail-guide" data-testid="rail-guide">
      <div className="guide-head">
        <span className="guide-kicker">Workspace</span>
        <h2 className="guide-title">{workspace || "This session"}</h2>
      </div>

      <div className="guide-list">
        <Row door="review" title="Review" question="What changed, and do I keep it?" label="Open Review" onOpen={() => onPick("review")}>
          {review && review.files > 0 ? (
            <span className="guide-fact guide-num" data-testid="rail-door-review-fact">
              {review.add > 0 && <span className="add">+{review.add}</span>}
              {review.add > 0 && review.del > 0 && " "}
              {review.del > 0 && <span className="del">−{review.del}</span>}
              <span className="guide-fact-quiet">
                {review.add > 0 || review.del > 0 ? " · " : ""}
                {review.files} file{review.files === 1 ? "" : "s"}
              </span>
            </span>
          ) : review ? (
            <span className="guide-fact guide-fact-quiet" data-testid="rail-door-review-fact">
              Nothing changed
            </span>
          ) : null}
        </Row>
        <Row door="files" title="Files" question="What is in my folders right now?" label="Open Files" onOpen={() => onPick("files")}>
          {folderFact && (
            <span className="guide-fact" title={folderFact}>
              {folderFact}
            </span>
          )}
        </Row>
        <Row
          door="terminal"
          title="Terminal"
          question="Run a command myself"
          label={`Open a terminal (${shellChip})`}
          onOpen={() => {
            const pick = readShellPref();
            openTerminal(pick ? { shell: pick } : undefined);
          }}
        >
          <span className="guide-shell" ref={shellRef}>
            <button
              type="button"
              className="guide-chip"
              data-testid="rail-door-terminal-menu"
              aria-label={`Shell: ${shellChip}. Choose another`}
              aria-haspopup="menu"
              aria-expanded={menuOpen}
              onClick={() => setMenuOpen((open) => !open)}
            >
              {shellChip}
              <Icon name="chevronDown" size={11} />
            </button>
            {menuOpen && (
              <div className="guide-menu" role="menu" aria-label="Shells" data-testid="rail-shell-menu">
                {state.phase === "loading" && (
                  <button type="button" className="guide-menu-item" data-testid="rail-shell-loading" disabled>
                    Loading shells…
                  </button>
                )}
                {state.phase === "failed" && (
                  <>
                    <button type="button" className="guide-menu-item" data-testid="rail-shell-error" disabled>
                      {state.message}
                    </button>
                    <button
                      type="button"
                      className="guide-menu-item"
                      data-testid="rail-shell-retry"
                      onClick={() => setAttempt((value) => value + 1)}
                    >
                      Retry
                    </button>
                  </>
                )}
                {state.phase === "ready" && state.shells.length === 0 && (
                  <button type="button" className="guide-menu-item" data-testid="rail-shell-empty" disabled>
                    No shells available
                  </button>
                )}
                {state.phase === "ready" &&
                  state.shells.map((shell) => (
                    <button
                      key={shell.id}
                      type="button"
                      role="menuitemradio"
                      aria-checked={shell.id === remembered}
                      className={"guide-menu-item" + (shell.id === remembered ? " picked" : "")}
                      data-testid={`rail-shell-${shell.id}`}
                      title={shell.path}
                      onClick={() => openTerminal({ shell: shell.id })}
                    >
                      <span className="guide-menu-name">{shell.name}</span>
                      {shell.id === remembered && (
                        <span className="guide-menu-check" aria-hidden>
                          ✓
                        </span>
                      )}
                    </button>
                  ))}
              </div>
            )}
          </span>
          <kbd className="guide-kbd">Ctrl `</kbd>
        </Row>
        <Row door="browser" title="Browser" question="Look at a page" label="Open the browser" onOpen={() => onPick("browser")} />
        <Row door="designs" title="Designs" question="What has Reeve drawn here?" label="Open Designs" onOpen={() => onPick("designs")} />
      </div>

      {recentClosed.length > 0 && onReopen && (
        <div className="guide-recent" data-testid="rail-guide-recent">
          <div className="guide-section">Recently closed</div>
          {recentClosed.slice(0, 4).map((tab, index) => (
            <button
              key={`${tab.id}-${index}`}
              type="button"
              className="guide-recent-row"
              data-testid={`rail-guide-reopen-${tab.id}`}
              onClick={() => onReopen(tab)}
            >
              <RailKindIcon kind={tab.kind} size={14} />
              <span className="guide-recent-name">{railTabTitle(tab)}</span>
              <span className="guide-recent-go">Reopen</span>
            </button>
          ))}
        </div>
      )}

      <div className="guide-foot">
        <kbd className="guide-kbd">{HIDE_KEYS}</kbd>
        <span>hides this panel</span>
      </div>
    </div>
  );
}
