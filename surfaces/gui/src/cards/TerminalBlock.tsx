// A shell command and what it printed: a prompt line, then ANSI-coloured output.
//
// The parts that carry the look:
//
//   The prompt line shows the WORKING DIRECTORY, shortened to `~` for home, then the
//   command. A command without its directory is only half the fact — `npm test` in the
//   wrong package is a different command with the same text. On a REMOTE session the host
//   is part of that fact too, so the prompt reads `ubuntu@192.168.2.125:/srv/app` — the
//   readout the composer already uses — rather than pretending the path is local.
//
//   The prompt glyph and the command's colours follow the SHELL that ran it: `$` and a
//   POSIX grammar for bash, `PS >` and the PowerShell grammar locally on Windows, `>` and
//   the cmd grammar for an explicit `cmd /c`. One `$` for every machine drew Get-ChildItem
//   as if it were a POSIX command.
//
//   The exit status is a PILL beside the prompt, never a line inside the output. It reads
//   as the command's verdict rather than as something the command printed.
//
//   Output never soft-wraps, and never collapses behind a "… more lines" control: a tall
//   output scrolls inside a bounded body (`.rv-scroll`), keeping both the command's
//   context and its verdict reachable without a second disclosure.
//
//   While the command RUNS, that body stays on its last line (`useFollowTail`): text that
//   arrives below the fold of a bounded region is text nobody sees arrive. It lets go when the
//   reader scrolls up, as a terminal does.

import { Fragment, useMemo, useRef } from "react";
import { CopyButton } from "../components/primitives/CopyButton";
import { StateDot } from "../components/primitives/StateDot";
import { ansiLinesToText, parseAnsiLines } from "../components/primitives/ansi";
import { highlightLines, useGrammars } from "../components/primitives/highlight";
import { shellGrammar, shellPrompt, type ShellKind } from "../shellKind";
import { ScrollFade } from "../components/primitives/ScrollFade";
import { useFollowTail } from "../components/primitives/useFollowTail";

/** Kept for callers that still import it; the card no longer caps its rows. */
export const TERMINAL_MAX_LINES = 16;

/** `~/project` for the home directory, else the last two segments, else the path. */
export function shortCwd(cwd: string | undefined, home?: string): string | undefined {
  if (!cwd) return undefined;
  const normalised = cwd.replace(/\\/g, "/").replace(/\/+$/, "");
  if (home) {
    const normalisedHome = home.replace(/\\/g, "/").replace(/\/+$/, "");
    if (normalisedHome && normalised.toLowerCase().startsWith(normalisedHome.toLowerCase())) {
      const rest = normalised.slice(normalisedHome.length);
      return rest ? `~${rest}` : "~";
    }
  }
  const segments = normalised.split("/").filter(Boolean);
  if (segments.length <= 2) return normalised;
  return `…/${segments.slice(-2).join("/")}`;
}

function statusLabel(exitCode: number | undefined, signal: string | undefined): string | undefined {
  // A signal wins over an exit code: a process killed by one is reported as killed, not as
  // whatever number the shell happened to synthesise for it.
  if (signal) return `signal ${signal}`;
  if (exitCode === undefined) return undefined;
  return exitCode === 0 ? "exit 0" : `exit ${exitCode}`;
}

export function TerminalBlock({
  command,
  cwd,
  home,
  output,
  exitCode,
  signal,
  running,
  labels,
  shell = "bash",
  host,
  truncated,
  omittedLines,
  follow,
}: {
  command: string;
  cwd?: string;
  home?: string;
  output?: string;
  exitCode?: number;
  signal?: string;
  running?: boolean;
  truncated?: boolean;
  /** Lines the retention budget dropped. The card must SAY the output is short, or a reader
   *  takes the last line they can see for the command's verdict. */
  omittedLines?: number;
  maxLines?: number;
  /** Overrides for the two strings that are copy rather than structure. */
  labels?: { running?: string; noOutput?: string };
  /** The shell that ran the command: prompt glyph, grammar and the card's `data-shell`. */
  shell?: ShellKind;
  /** `user@host` for a remote session; the other half of the working directory's fact. */
  host?: string;
  /** Keep the body on its last line as output arrives. Defaults to `running`: a finished
   *  command is read from its top, a running one is watched at its end. A caller whose reader
   *  wants the END of a finished log (a background job's verdict) passes true. */
  follow?: boolean;
}) {
  const lines = useMemo(() => parseAnsiLines(output ?? ""), [output]);
  // `empty` is computed from the PARSED lines, not from `text.trim()`: an output that is
  // only escape sequences parses to nothing and must read as empty, and one that is only
  // spaces parses to a row that must read as present.
  const empty = lines.length === 0;
  const card = useRef<HTMLDivElement>(null);
  useFollowTail(card, ".rv-term-output", follow ?? !!running, lines, !empty);
  const status = statusLabel(exitCode, signal);
  const directory = shortCwd(cwd, home);
  // Tokenize the whole command in one call, like every other highlighting card here, so a
  // multi-line command keeps its grammar context. `useGrammars` is what re-renders the card
  // when the lazy grammar lands; until then `undefined` renders the plain command.
  const grammars = useGrammars(shellGrammar(shell));
  const commandSpans = useMemo(
    () => highlightLines(command, shellGrammar(shell)),
    [command, shell, grammars],
  );

  return (
    <div
      ref={card}
      className="rv-card"
      data-terminal=""
      data-shell={shell}
      data-running={running || undefined}
    >
      {/* Two lines: where/when on the meta line, WHAT RAN on its own line
          below it. One shared line lost the command entirely in a narrow card — the command
          was the flexible part, so a long cwd and the pill squeezed it to zero and the card
          read as a prompt with nothing after it. */}
      <div className="rv-prompt">
        {running ? (
          <StateDot state="ongoing" size={10} />
        ) : (
          <span className="rv-prompt-mark" data-testid="terminal-prompt" aria-hidden>
            {shellPrompt(shell)}
          </span>
        )}
        {host ? (
          <span
            className="rv-prompt-host"
            data-testid="terminal-host"
            title={cwd ? `${host}:${cwd}` : host}
          >
            {directory ? `${host}:${directory}` : host}
          </span>
        ) : (
          directory && <span className="rv-prompt-cwd">{directory}</span>
        )}
        <span className="rv-banner-spacer" />
        {running ? (
          <span className="rv-pill">{labels?.running ?? "Running"}</span>
        ) : (
          status && (
            <span
              className="rv-pill"
              data-tone={status === "exit 0" ? "ok" : "danger"}
              data-testid="terminal-status"
            >
              {status}
            </span>
          )
        )}
        {!running && !empty && <CopyButton text={ansiLinesToText(lines)} />}
      </div>
      <div className="rv-prompt-cmd-line">
        <span className="rv-prompt-cmd" data-testid="terminal-command">
          {commandSpans
            ? commandSpans.map((line, index) => (
                <Fragment key={index}>
                  {index > 0 && "\n"}
                  {line.map((span, spanIndex) => (
                    <span key={spanIndex} style={span.style}>
                      {span.text}
                    </span>
                  ))}
                </Fragment>
              ))
            : command}
        </span>
      </div>
      {empty ? (
        running ? null : (
          <div className="rv-empty">{labels?.noOutput ?? "No output"}</div>
        )
      ) : (
        <ScrollFade className="rv-body rv-term-output rv-scroll" data-testid="terminal-output">
          {lines.map((line, index) => (
            <div key={index} className="rv-term-line">
              {line.length === 0 ? (
                "\u00a0"
              ) : (
                line.map((span, spanIndex) => (
                  <span key={spanIndex} style={span.style}>
                    {span.text}
                  </span>
                ))
              )}
            </div>
          ))}
        </ScrollFade>
      )}
      {truncated && !running && (
        // The one thing the card must not let a reader assume: that the last visible line is
        // the end of the output. The count comes from the retainer; without it the note still
        // says the output is short.
        <div className="rv-foot" data-testid="terminal-truncated">
          └ output truncated
          {omittedLines && omittedLines > 0
            ? ` · ${omittedLines.toLocaleString()} lines omitted`
            : ""}{" "}
          · full output in the spill file
        </div>
      )}
    </div>
  );
}
