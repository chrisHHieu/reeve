import { describe, expect, it } from "vitest";
import {
  shellDisplayName,
  shellGrammar,
  shellHost,
  shellKind,
  shellPrompt,
  shellTitle,
  type ShellSessionInfo,
} from "./shellKind";

const REMOTE: ShellSessionInfo = {
  environment: { user: "ubuntu", host: "192.168.2.125", port: 22 },
  platform: "windows",
};
const REMOTE_NONSTANDARD_PORT: ShellSessionInfo = {
  environment: { user: "deploy", host: "box.example", port: 2222 },
  platform: "linux",
};

describe("shellKind", () => {
  it("reads a remote session as POSIX bash, whatever the controller runs", () => {
    expect(shellKind(REMOTE)).toBe("bash");
    expect(shellKind(REMOTE, "ls -la")).toBe("bash");
  });

  it("reads a local Windows session as PowerShell", () => {
    expect(shellKind({ environment: null, platform: "windows" })).toBe("powershell");
    expect(shellKind({ platform: "windows" }, "Get-ChildItem")).toBe("powershell");
  });

  it("reads a local non-Windows session — or an unknown platform — as bash", () => {
    expect(shellKind({ environment: null, platform: "macos" })).toBe("bash");
    expect(shellKind({ environment: null, platform: "linux" })).toBe("bash");
    expect(shellKind({ environment: null })).toBe("bash");
    expect(shellKind(null)).toBe("bash");
    expect(shellKind(undefined)).toBe("bash");
  });

  it("lets a command's explicit interpreter win over the session's kind", () => {
    const windows: ShellSessionInfo = { environment: null, platform: "windows" };
    expect(shellKind(windows, "cmd /c dir")).toBe("cmd");
    expect(shellKind(windows, "cmd.exe /k set")).toBe("cmd");
    expect(shellKind(windows, "C:\\Windows\\System32\\cmd.exe /c dir")).toBe("cmd");
    expect(shellKind(windows, "pwsh -Command Get-ChildItem")).toBe("powershell");
    expect(shellKind(windows, "powershell.exe -File clean.ps1")).toBe("powershell");
    // The remote default is overridable the same way: an explicit interpreter is a fact.
    expect(shellKind(REMOTE, "cmd /c dir")).toBe("cmd");
    expect(shellKind(REMOTE, "pwsh -c 'Get-ChildItem'")).toBe("powershell");
  });

  it("takes the shell the model chose for the call over what the OS would have run", () => {
    const windows: ShellSessionInfo = { environment: null, platform: "windows" };
    // Git Bash on Windows: the card draws `$` and bash colours, and the row says "bash".
    expect(shellKind(windows, "git log --oneline | head", "bash")).toBe("bash");
    expect(shellKind({ environment: null, platform: "linux" }, "Get-Process", "powershell")).toBe(
      "powershell",
    );
    // PowerShell 7 reads as PowerShell; cmd keeps its own prompt and grammar.
    expect(shellKind(windows, "Get-Process", "pwsh")).toBe("powershell");
    expect(shellKind(windows, "dir", "cmd")).toBe("cmd");
    // An older transcript has no `shell`; anything else the model wrote is not a shell.
    expect(shellKind(windows, "dir", undefined)).toBe("powershell");
    expect(shellKind(windows, "dir", "zsh")).toBe("powershell");
    expect(shellKind(windows, "dir", 3)).toBe("powershell");
    // A remote session has no choice to make: its commands are POSIX whatever was recorded.
    expect(shellKind(REMOTE, "ls", "powershell")).toBe("bash");
  });

  it("takes the session's default shell over the OS when the call names none", () => {
    // Settings ▸ shell = bash on Windows: a call with no `shell` ran in bash, and the row said
    // PowerShell with a `PS >` prompt over bash's own error (owner, 2026-10-15).
    const bashDefault: ShellSessionInfo = { environment: null, platform: "windows", shell: "bash" };
    expect(shellKind(bashDefault, ".venv/Scripts/python.exe x.py")).toBe("bash");
    expect(shellKind({ platform: "linux", shell: "pwsh" }, "Get-Process")).toBe("powershell");
    // The call's own choice and an explicit interpreter still come first.
    expect(shellKind(bashDefault, "dir", "powershell")).toBe("powershell");
    expect(shellKind(bashDefault, "cmd /c dir")).toBe("cmd");
    // A remote world is POSIX whatever the local default.
    expect(shellKind({ ...REMOTE, shell: "powershell" }, "ls")).toBe("bash");
  });

  it("does not mistake an ordinary command that merely starts like the shell", () => {
    // `cmd` alone or as an argument is not `cmd /c`; `cmdx` is a different executable.
    expect(shellKind({ platform: "windows" }, "cmd")).toBe("powershell");
    expect(shellKind({ platform: "windows" }, "cmd /x dir")).toBe("powershell");
    expect(shellKind({ platform: "windows" }, "cmdx /c dir")).toBe("powershell");
    expect(shellKind({ platform: "windows" }, "get-command pwsh")).toBe("powershell");
    // A POSIX command that names the word in a later position is not an override.
    expect(shellKind({ environment: null, platform: "linux" }, "ls powershell")).toBe("bash");
  });
});

describe("shell display furniture", () => {
  it("names the kinds for the row title", () => {
    expect(shellDisplayName("bash")).toBe("bash");
    expect(shellDisplayName("powershell")).toBe("PowerShell");
    expect(shellDisplayName("cmd")).toBe("cmd");
    expect(shellDisplayName("sh")).toBe("sh");
    expect(shellTitle("bash")).toBe("bash");
    expect(shellTitle("powershell")).toBe("PowerShell");
  });

  it("gives each kind its own grammar and prompt glyph", () => {
    expect(shellGrammar("bash")).toBe("bash");
    expect(shellGrammar("sh")).toBe("bash");
    expect(shellGrammar("powershell")).toBe("powershell");
    // shiki ships cmd as an alias of its `bat` grammar.
    expect(shellGrammar("cmd")).toBe("cmd");
    expect(shellPrompt("bash")).toBe("$");
    expect(shellPrompt("sh")).toBe("$");
    expect(shellPrompt("powershell")).toBe("PS >");
    expect(shellPrompt("cmd")).toBe(">");
  });

  it("formats the remote destination, port only when non-default", () => {
    expect(shellHost(REMOTE)).toBe("ubuntu@192.168.2.125");
    expect(shellHost(REMOTE_NONSTANDARD_PORT)).toBe("deploy@box.example:2222");
    expect(shellHost({ environment: { host: "bare.example" } })).toBe("bare.example");
    expect(shellHost({ environment: null, platform: "windows" })).toBeNull();
    expect(shellHost(null)).toBeNull();
  });
});
