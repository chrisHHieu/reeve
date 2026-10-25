// The user's terminal tab — a REAL terminal: xterm.js renders the session's own PTY
// (`/ws/terminal/{session}?terminal=…`, harness/tools/pty_session.py), so prompts, colours,
// arrow keys, Ctrl-C and full-screen programs behave like a terminal. Deliberately not the
// agent's tool shell: that stays pipe-backed (harness/tools/terminal.py) because its `read()`
// drains a cursor, and two readers over one stream would steal each other's output.
//
// The shell marks its prompt and each command (OSC 133 / 633;E, harness/tools/shell_marks.py),
// and the emulator reads those marks as they stream: so the tab can say what is running and
// for how long, how a command ended, and hand a failed command's output to Reeve — rather than
// "running" while the shell sat at an idle prompt (workspace panel redesign, 2026-10-11). A
// shell that cannot mark its commands (cmd.exe) keeps Ctrl-C at hand and says nothing it
// cannot know.
import { useEffect, useRef, useState } from "react";
import { Terminal as XTerm, type IMarker, type ITheme } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import { SearchAddon } from "@xterm/addon-search";
import "@xterm/xterm/css/xterm.css";
import { openUserTerminal, openUserTerminalSocket } from "../../api/terminal";
import { FONT_EVENT, terminalOptions } from "../../fonts";
import { PALETTE_EVENT } from "../../theme";
import { Icon } from "../Icon";
import { shellDisplayLabel } from "../rail/shellPref";

/** What the tab strip says about this terminal: its name and whether a command runs. */
export interface TerminalTabState {
  title: string;
  running: boolean;
}

interface Props {
  sessionId: string;
  /** The tab's server-side terminal id ("t1", "t2", …) — several terminals per session. */
  terminalId: string;
  /** The shell the guide menu picked (`reeve:rail-shell:v1`); absent = server default. */
  shell?: string;
  /** Whether this tab is the one on show (a background terminal stays attached, unseen). */
  active?: boolean;
  onState?: (state: TerminalTabState) => void;
}

interface TerminalStatus {
  running: boolean;
  cwd?: string;
  shell?: string;
  disconnected?: boolean;
  exitCode?: number | null;
  /** The shell marks its commands (OSC 133); without it, what runs cannot be known. */
  marks?: boolean;
}

interface Command {
  line: string;
  /** When it started, if it was seen start (a replayed scrollback does not say). */
  startedAt: number | null;
  start?: IMarker;
}

interface Failed {
  line: string;
  code: number;
  output: string;
}

/** How much of a failed command's output goes to Reeve: its last lines are where the error is. */
const ASK_LINES = 80;
const ASK_CHARS = 6000;

function token(name: string, fallback: string): string {
  if (typeof window === "undefined") return fallback;
  const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return value || fallback;
}

/** The emulator's palette: the app's own code-surface tokens, so the terminal reads as one
 *  more panel of Reeve rather than a foreign black box. Re-read per mount, so a theme or
 *  palette switch repaints the next terminal (the same rule as the code cards). */
export function terminalTheme(): ITheme {
  return {
    background: token("--md-code-bg", "#1b222a"),
    foreground: token("--syntax-variable", "#e9edf1"),
    cursor: token("--accent", "#6cc7dc"),
    cursorAccent: token("--md-code-bg", "#1b222a"),
    selectionBackground: token("--selection-bg", "#25424e"),
    // The 16 colours are the palette's own (`--ansi-*`, palettes.css): an editor theme carries
    // its terminal table, and the hand-tuned palettes map their syntax roles as before.
    black: token("--ansi-black", "#211e19"),
    red: token("--ansi-red", "#b3342f"),
    green: token("--ansi-green", "#2c7441"),
    yellow: token("--ansi-yellow", "#8a5b00"),
    blue: token("--ansi-blue", "#0d6a8a"),
    magenta: token("--ansi-magenta", "#8a3e9e"),
    cyan: token("--ansi-cyan", "#6d3fc4"),
    white: token("--ansi-white", "#6a665d"),
    brightBlack: token("--ansi-bright-black", "#6a665d"),
    brightRed: token("--ansi-bright-red", "#a62b24"),
    brightGreen: token("--ansi-bright-green", "#1c7340"),
    brightYellow: token("--ansi-bright-yellow", "#955608"),
    brightBlue: token("--ansi-bright-blue", "#0d6a8a"),
    brightMagenta: token("--ansi-bright-magenta", "#a63d6e"),
    brightCyan: token("--ansi-bright-cyan", "#6d3fc4"),
    brightWhite: token("--ansi-bright-white", "#211e19"),
  };
}

function elapsed(ms: number): string {
  const s = Math.max(0, Math.floor(ms / 1000));
  const m = Math.floor(s / 60);
  const h = Math.floor(m / 60);
  if (h) return `${h}:${String(m % 60).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
  return `${m}:${String(s % 60).padStart(2, "0")}`;
}

/** Put words in the chat box for the person to read and send (Composer listens). */
function askReeve(text: string) {
  window.dispatchEvent(new CustomEvent("reeve-insert-text", { detail: { text } }));
}

export function TerminalPanel({ sessionId, terminalId, shell, active = true, onState }: Props) {
  const [status, setStatus] = useState<TerminalStatus | null>(null);
  const [command, setCommand] = useState<Command | null>(null);
  const [failed, setFailed] = useState<Failed | null>(null);
  const [asked, setAsked] = useState(false);
  const [selection, setSelection] = useState(false);
  const [findOpen, setFindOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [found, setFound] = useState<{ index: number; count: number } | null>(null);
  const [restart, setRestart] = useState(0);
  const [, setTick] = useState(0);
  const hostRef = useRef<HTMLDivElement | null>(null);
  const termRef = useRef<XTerm | null>(null);
  const fitRef = useRef<FitAddon | null>(null);
  const searchRef = useRef<SearchAddon | null>(null);
  const socketRef = useRef<WebSocket | null>(null);
  const commandRef = useRef<Command | null>(null);
  const findRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    const host = hostRef.current;
    if (!sessionId || !host) return;
    let disposed = false;
    setStatus(null);
    setCommand(null);
    setFailed(null);
    commandRef.current = null;

    const term = new XTerm({
      // Face, size, line height and cursor are Settings → Appearance's (src/fonts.ts).
      ...terminalOptions(),
      cursorBlink: true,
      scrollback: 10000,
      allowProposedApi: true,
      theme: terminalTheme(),
    });
    const fit = new FitAddon();
    const search = new SearchAddon();
    term.loadAddon(fit);
    term.loadAddon(search);
    term.open(host);
    termRef.current = term;
    fitRef.current = fit;
    searchRef.current = search;
    const fitNow = () => {
      try {
        fit.fit();
      } catch {
        /* a hidden host has no geometry yet; the observer retries */
      }
    };
    fitNow();

    // A choice made in Settings, or a palette switch, repaints the open terminal: the emulator
    // reads its options once, so they are handed to it again.
    const restyle = () => {
      Object.assign(term.options, terminalOptions(), { theme: terminalTheme() });
      fitNow();
    };
    window.addEventListener(FONT_EVENT, restyle);
    window.addEventListener(PALETTE_EVENT, restyle);

    // A scrollback replayed on attach carries the marks of commands long finished: they set
    // the state as it is, but never raise a failure or time a command from now.
    let replaying = false;
    let pendingLine = "";
    const marks = [
      term.parser?.registerOscHandler?.(633, (data) => {
        if (data.startsWith("E;")) pendingLine = data.slice(2).trim();
        return true;
      }),
      term.parser?.registerOscHandler?.(133, (data) => {
        const [kind, arg] = data.split(";");
        if (kind === "C") {
          const run: Command = {
            line: pendingLine,
            startedAt: replaying ? null : Date.now(),
            start: term.registerMarker?.(0),
          };
          commandRef.current = run;
          setCommand(run);
          if (!replaying) {
            setFailed(null);
            setAsked(false);
          }
        } else if (kind === "D") {
          const run = commandRef.current;
          commandRef.current = null;
          setCommand(null);
          const code = Number(arg);
          if (!replaying && run && Number.isFinite(code) && code !== 0) {
            setFailed({ line: run.line, code, output: outputOf(run.start) });
            setAsked(false);
          }
          run?.start?.dispose();
        }
        return true;
      }),
    ];

    /** The text a command printed: from its start mark to where the cursor is now. */
    const outputOf = (start?: IMarker): string => {
      const buffer = term.buffer?.active;
      if (!buffer || !start || start.line < 0) return "";
      const end = buffer.baseY + buffer.cursorY;
      const lines: string[] = [];
      for (let y = Math.max(start.line, end - ASK_LINES); y <= end; y += 1) {
        lines.push(buffer.getLine(y)?.translateToString(true) ?? "");
      }
      const text = lines.join("\n").trim();
      return text.length > ASK_CHARS ? text.slice(-ASK_CHARS) : text;
    };

    // Keystrokes go straight to the pty — xterm emits exactly what was pressed. Not while a
    // scrollback is replayed: the emulator answers the queries in it (ConPTY's device-attributes
    // query at startup), and the answer reached the shell as typed text, "[?1;2c" at the
    // prompt (seen on a second terminal, 2026-10-11). Those queries were the past's to answer.
    const dataSub = term.onData((data) => {
      if (replaying) return;
      const socket = socketRef.current;
      if (socket && socket.readyState === WebSocket.OPEN) {
        socket.send(JSON.stringify({ type: "input", data }));
      }
    });
    const resizeSub = term.onResize(({ cols, rows }) => {
      const socket = socketRef.current;
      if (socket && socket.readyState === WebSocket.OPEN) {
        socket.send(JSON.stringify({ type: "resize", data: { cols, rows } }));
      }
    });
    const selectionSub = term.onSelectionChange?.(() => setSelection(!!term.hasSelection?.()));
    const resultsSub = search.onDidChangeResults?.((result) =>
      setFound(result ? { index: result.resultIndex, count: result.resultCount } : null),
    );
    let resizeTimer: number | undefined;
    const report = () => {
      window.clearTimeout(resizeTimer);
      resizeTimer = window.setTimeout(fitNow, 120);
    };
    let observer: ResizeObserver | null = null;
    if (typeof ResizeObserver !== "undefined") {
      observer = new ResizeObserver(report);
      observer.observe(host);
    }
    window.addEventListener("resize", report);

    const attach = async () => {
      // Create/attach the menu's interpreter BEFORE the socket, with the emulator's real
      // size, so the first prompt is already formatted for this pane.
      try {
        const info = await openUserTerminal(sessionId, {
          terminal: terminalId,
          shell,
          cols: term.cols,
          rows: term.rows,
        });
        if (disposed) return;
        if (info && info.ok === false) {
          term.write(`\r\n[terminal error] ${info.error || "could not start a shell"}\r\n`);
          setStatus({ running: false });
          return;
        }
      } catch {
        /* the socket's own attach reports what it sees */
      }
      if (disposed) return;
      const socket = openUserTerminalSocket(sessionId, terminalId);
      socketRef.current = socket;
      socket.onmessage = (event) => {
        let message: any;
        try {
          message = JSON.parse(String(event.data));
        } catch {
          return;
        }
        const data = message?.data ?? {};
        if (message.type === "terminal") {
          term.reset();
          replaying = true;
          term.write(String(data.output || ""), () => {
            replaying = false;
          });
          setStatus({
            running: data.running !== false,
            cwd: data.cwd,
            shell: data.shell,
            marks: !!data.marks,
            exitCode: data.exit_code ?? null,
          });
        } else if (message.type === "output") {
          const chunk = String(data.chunk || "");
          term.write(chunk);
        } else if (message.type === "exit") {
          commandRef.current = null;
          setCommand(null);
          setStatus((current) => ({ ...(current ?? { running: false }), running: false, exitCode: data.exit_code ?? null }));
        } else if (message.type === "error") {
          term.write(`\r\n[terminal error] ${data.error || "unknown"}\r\n`);
          setStatus((current) => ({ ...(current ?? { running: false }), running: false }));
        }
      };
      socket.onclose = () => {
        // The text on screen stays; the pty may well still run, and Reconnect reattaches.
        setStatus((current) => ({ ...(current ?? { running: false }), disconnected: true }));
      };
    };
    void attach();

    return () => {
      disposed = true;
      window.removeEventListener(FONT_EVENT, restyle);
      window.removeEventListener(PALETTE_EVENT, restyle);
      window.clearTimeout(resizeTimer);
      observer?.disconnect();
      window.removeEventListener("resize", report);
      dataSub.dispose();
      resizeSub.dispose();
      selectionSub?.dispose();
      resultsSub?.dispose();
      for (const mark of marks) mark?.dispose();
      const socket = socketRef.current;
      if (socket) {
        socket.onmessage = null;
        socket.onclose = null;
        socket.close();
      }
      socketRef.current = null;
      term.dispose();
      termRef.current = null;
      fitRef.current = null;
      searchRef.current = null;
    };
  }, [sessionId, terminalId, shell, restart]);

  // A tab coming back on show takes its size again (a hidden host has none).
  useEffect(() => {
    if (!active) return;
    const timer = window.setTimeout(() => {
      try {
        fitRef.current?.fit();
      } catch {
        /* no geometry yet */
      }
      termRef.current?.focus?.();
    }, 0);
    return () => window.clearTimeout(timer);
  }, [active]);

  // The running command's clock, while it is on show.
  useEffect(() => {
    if (!command?.startedAt || !active) return;
    const timer = window.setInterval(() => setTick((n) => n + 1), 1000);
    return () => window.clearInterval(timer);
  }, [command, active]);

  const folder = (status?.cwd || "").split(/[\\/]/).filter(Boolean).pop() || "";
  const shellName = shellDisplayLabel(status?.shell || shell);


  const send = (message: unknown) => {
    const socket = socketRef.current;
    if (socket && socket.readyState === WebSocket.OPEN) socket.send(JSON.stringify(message));
  };

  const clearView = () => {
    const term = termRef.current;
    if (!term) return;
    term.clear();
    term.write("\x1b[2J\x1b[H");
  };

  const findStep = (forward: boolean) => {
    const search = searchRef.current;
    if (!search || !query) return;
    const options = {
      decorations: {
        matchBackground: token("--selection-bg", "#cfe3ea"),
        activeMatchBackground: token("--warn-soft", "#f7f0dc"),
        matchOverviewRuler: token("--faint", "#8f887b"),
        activeMatchColorOverviewRuler: token("--ink", "#201d18"),
      },
    };
    if (forward) search.findNext(query, options);
    else search.findPrevious(query, options);
  };

  const exited = status !== null && !status.running && !status.disconnected;
  const disconnected = !!status?.disconnected;
  const live = status !== null && status.running && !disconnected;
  const stateText = disconnected
    ? "Disconnected"
    : exited
      ? `Exited${status?.exitCode != null ? ` · code ${status.exitCode}` : ""}`
      : command
        ? `Running ${command.line || "a command"}${command.startedAt ? ` · ${elapsed(Date.now() - command.startedAt)}` : ""}`
        : status?.marks
          ? "Ready"
          : status
            ? ""
            : "Starting…";
  // Stop is for a command that runs; a shell that cannot say whether one runs keeps it at hand.
  const showStop = live && (!!command || !status?.marks);
  useEffect(() => {
    onState?.({ title: command?.line || folder || shellName, running: !!command });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [command?.line, folder, shellName]);

  return (
    <div className="terminal-panel" data-testid="terminal-panel">
      <div className="terminal-head">
        <span className="terminal-cwd" title={status?.cwd || ""}>
          {status?.cwd || "…"}
        </span>
        <span className="terminal-shell" data-testid="terminal-shell" title={status?.shell || shell || ""}>
          {shellName}
        </span>
        <span className="terminal-fill" />
        <span
          className={
            "terminal-state" +
            (disconnected ? " is-disconnected" : exited ? " is-exited" : command ? " is-running" : "")
          }
          data-testid="terminal-state"
          role="status"
        >
          {stateText}
        </span>
        <button
          type="button"
          className="terminal-action icon"
          data-testid="terminal-find-toggle"
          aria-label="Find in the output"
          aria-expanded={findOpen}
          title="Find (Ctrl+F)"
          onClick={() => {
            setFindOpen((open) => !open);
            window.setTimeout(() => findRef.current?.focus(), 0);
          }}
        >
          <Icon name="search" size={13} />
        </button>
        <button type="button" className="terminal-action" data-testid="terminal-clear" title="Clear the view" onClick={clearView}>
          Clear
        </button>
        {showStop && (
          <button
            type="button"
            className="terminal-action line"
            data-testid="terminal-interrupt"
            title="Send Ctrl+C to what runs"
            onClick={() => send({ type: "interrupt" })}
          >
            {command ? "Stop" : "Ctrl-C"}
          </button>
        )}
      </div>
      {findOpen && (
        <div className="terminal-find" data-testid="terminal-find">
          <input
            ref={findRef}
            aria-label="Find in the output"
            placeholder="Find in the output"
            value={query}
            onChange={(event) => {
              setQuery(event.target.value);
              setFound(null);
            }}
            onKeyDown={(event) => {
              if (event.key === "Enter") findStep(!event.shiftKey);
              if (event.key === "Escape") {
                searchRef.current?.clearDecorations?.();
                setFindOpen(false);
              }
            }}
          />
          <span className="terminal-find-count" aria-live="polite">
            {found && query ? (found.count ? `${found.index + 1} of ${found.count}` : "No match") : ""}
          </span>
          <button type="button" className="terminal-action icon" aria-label="Previous match" onClick={() => findStep(false)}>
            <Icon name="chevronUp" size={13} />
          </button>
          <button type="button" className="terminal-action icon" aria-label="Next match" onClick={() => findStep(true)}>
            <Icon name="chevronDown" size={13} />
          </button>
          <button
            type="button"
            className="terminal-action icon"
            aria-label="Close find"
            onClick={() => {
              searchRef.current?.clearDecorations?.();
              setFindOpen(false);
            }}
          >
            <Icon name="x" size={12} />
          </button>
        </div>
      )}
      <div className="terminal-body">
        <div className="terminal-host" data-testid="terminal-host" ref={hostRef} />
        {selection && (
          <div className="terminal-selection" data-testid="terminal-selection">
            <button
              type="button"
              className="terminal-action"
              onClick={() => void navigator.clipboard?.writeText(termRef.current?.getSelection?.() || "")}
            >
              Copy
            </button>
            <button
              type="button"
              className="terminal-action line"
              data-testid="terminal-ask-selection"
              onClick={() => {
                const text = termRef.current?.getSelection?.() || "";
                if (text.trim()) askReeve(`From my terminal:\n\n\`\`\`\n${text.trim()}\n\`\`\`\n`);
              }}
            >
              Ask Reeve about this
            </button>
          </div>
        )}
      </div>
      {(failed || exited || disconnected) && (
        <div className="terminal-notes">
          {failed && !exited && !disconnected && (
            <div className="terminal-note" data-testid="terminal-failed">
              <span className="terminal-note-code">Exit {failed.code}</span>
              <span className="terminal-note-text">{failed.line}</span>
              <button
                type="button"
                className="terminal-action line"
                data-testid="terminal-ask-failed"
                onClick={() => {
                  askReeve(
                    `This command failed in my terminal (exit ${failed.code}):\n\n\`\`\`\n$ ${failed.line}\n${failed.output}\n\`\`\`\n`,
                  );
                  setAsked(true);
                }}
              >
                {asked ? "Added to your message ✓" : "Ask Reeve about this"}
              </button>
            </div>
          )}
          {exited && (
            <div className="terminal-note" role="status" data-testid="terminal-exited">
              <span className="terminal-note-text">
                {shellName} exited{status?.exitCode != null ? ` with code ${status.exitCode}` : ""}.
              </span>
              <button type="button" className="terminal-action line" data-testid="terminal-restart" onClick={() => setRestart((n) => n + 1)}>
                Start again
              </button>
            </div>
          )}
          {disconnected && (
            <div className="terminal-note" role="alert" data-testid="terminal-disconnected">
              <span className="terminal-note-text">Lost the connection to Reeve. The shell may still be running.</span>
              <button type="button" className="terminal-action line" data-testid="terminal-reconnect" onClick={() => setRestart((n) => n + 1)}>
                Reconnect
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
