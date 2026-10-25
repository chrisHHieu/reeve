// Which shell a session's commands actually run in — and the furniture that follows from it.
//
// The transcript drew one shell for every machine: a `$` prompt, the plain command, and a
// generic "Shell" row. Now that a session can be remote (POSIX over SSH) or run on
// this Windows machine (PowerShell 5.1, see `harness/tools/shell.py`), the same card is
// wrong on at least one of them. The kind is the `shell` the model passed to `run_shell` when it
// chose one (bash or PowerShell on the same Windows machine); otherwise derivable from
// what the GUI already knows — the session's bound environment and the controller's own OS —
// plus a best-effort peek at the command for an interpreter it names explicitly (`cmd /c`,
// `pwsh`, `powershell`).
//
// The remote probe reports platform/arch (`RemoteEnvironmentTestResult`), but that is the
// settings page's one-shot test: nothing carries it on the session wire. A remote session is
// therefore read as POSIX (`bash`), which is what the remote world executes, and the local
// machine answers with its own OS (`platformOS()`).

export type ShellKind = "bash" | "powershell" | "cmd" | "sh";

/** The session facts the kind is derived from. `environment` is the bound SSH host (the
 *  shape of `SessionInfo.environment`); null/absent = the local machine. `platform` is the
 *  controller's OS as `platformOS()` reports it ("windows" | "macos" | "linux"). */
export interface ShellSessionInfo {
  environment?: { user?: string | null; host?: string; port?: number } | null;
  platform?: string | null;
  /** The session's default shell kind (a Setting), from its `ready` frame. */
  shell?: string | null;
}

/** The executable a command names as its own interpreter, or null for an ordinary command. */
function explicitInterpreter(command: string): ShellKind | null {
  const trimmed = command.trim();
  // The executable is the first word; a Windows path to it (`C:\…\cmd.exe`) still counts,
  // and a quoted executable (`"C:\Program Files\PowerShell\7\pwsh.exe"`) is unwrapped.
  const first = (trimmed.split(/\s/, 1)[0] ?? "").replace(/^["']|["']$/g, "");
  const name = first
    .slice(Math.max(first.lastIndexOf("\\"), first.lastIndexOf("/")) + 1)
    .toLowerCase();
  if (name === "cmd" || name === "cmd.exe") {
    // A bare `cmd` opens an interactive prompt; only `/c` (or `/k`) is a command being run.
    return /^[^\s]*cmd(?:\.exe)?\s+\/[ck](?:\s|$)/i.test(trimmed) ? "cmd" : null;
  }
  if (
    name === "pwsh" ||
    name === "pwsh.exe" ||
    name === "powershell" ||
    name === "powershell.exe"
  ) {
    return "powershell";
  }
  return null;
}

/**
 * The shell a session's commands run in.
 *
 * Precedence is deliberate: what the COMMAND says about itself beats what the session says
 * about its machine, because a person writes `cmd /c …` precisely to leave PowerShell — and
 * the local OS beats "unknown", since a non-Windows machine runs bash with or without a
 * platform answer.
 */
export function shellKind(
  session: ShellSessionInfo | null | undefined,
  command?: string,
  chosen?: unknown,
): ShellKind {
  // The `shell` argument the model passed to `run_shell` is the one authority on
  // which dialect the command is written in: the OS only says what the default would have been.
  // An older transcript has none, and a remote session has no choice, so both fall through.
  if (!session?.environment?.host) {
    if (chosen === "bash" || chosen === "powershell" || chosen === "cmd") return chosen;
    // PowerShell 7 and Windows PowerShell are one dialect to the reader: the same prompt, colours and name.
    if (chosen === "pwsh") return "powershell";
  }
  if (command) {
    const explicit = explicitInterpreter(command);
    if (explicit) return explicit;
  }
  if (session?.environment?.host) return "bash";
  // The session's own default (a Setting) before the OS's: on Windows it may be bash,
  // and a bash session's rows were drawn as PowerShell (owner, 2026-10-15).
  const fallback = session?.shell;
  if (fallback === "bash" || fallback === "cmd") return fallback;
  if (fallback === "powershell" || fallback === "pwsh") return "powershell";
  return session?.platform === "windows" ? "powershell" : "bash";
}

/** The word a title or tooltip reads: "bash", "PowerShell", "cmd", "sh". */
export function shellDisplayName(kind: ShellKind): string {
  return kind === "powershell" ? "PowerShell" : kind;
}

/** The shiki language id that colours a command of this kind. */
export function shellGrammar(kind: ShellKind): string {
  if (kind === "powershell") return "powershell";
  if (kind === "cmd") return "cmd";
  return "bash";
}

/** The prompt glyph a card shows before the command. */
export function shellPrompt(kind: ShellKind): string {
  if (kind === "powershell") return "PS >";
  if (kind === "cmd") return ">";
  return "$";
}

/** The tool row's title once the kind is known: the interpreter's own name ("PowerShell",
 *  "bash"), not the generic family word, which says nothing the name does not. */
export function shellTitle(kind: ShellKind): string {
  return shellDisplayName(kind);
}

/** `user@host` (port only when non-default) for a remote session; null when local. */
export function shellHost(session: ShellSessionInfo | null | undefined): string | null {
  const environment = session?.environment;
  if (!environment?.host) return null;
  const destination = environment.user
    ? `${environment.user}@${environment.host}`
    : environment.host;
  return environment.port && environment.port !== 22
    ? `${destination}:${environment.port}`
    : destination;
}
