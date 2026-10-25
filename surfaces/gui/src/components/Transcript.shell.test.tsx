// The shell card, one per MACHINE: a remote session is POSIX bash on a named host, this
// Windows machine is PowerShell, and a command that names its interpreter (`cmd /c`) wins
// over the session default. The row title, the prompt glyph, the host line and the syntax
// highlighting all follow from that one fact, so each test opens the same card and reads
// the pieces that changed.

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { Transcript } from "./Transcript";
import { SessionShellContext, type SessionShell } from "../sessionShell";
import { ensureLanguage } from "./primitives/highlight";
import type { RemoteEnvironmentSummary } from "../api/remote-environments";
import type { Item } from "../types";

afterEach(cleanup);

const REMOTE_ENV: RemoteEnvironmentSummary = {
  id: "h1",
  name: "dev box",
  user: "ubuntu",
  host: "192.168.2.125",
  port: 22,
};

function shellCall(command: string): Item {
  return {
    kind: "tool",
    id: "t1",
    name: "run_shell",
    args: { command },
    status: "ok",
    presentation: {
      summary: "Command succeeded",
      card: {
        card: "terminal",
        title: command,
        cwd: "/srv/app",
        output: "ok\n",
        exit_code: 0,
      },
    },
  } as Item;
}

/** Open the turn and its one tool row so the card is on screen. */
function openShellCard(shell: SessionShell, command = "npm test") {
  render(
    <SessionShellContext.Provider value={shell}>
      <Transcript
        items={[{ kind: "user", text: "go" }, shellCall(command)]}
        onApprove={vi.fn()}
      />
    </SessionShellContext.Provider>,
  );
  fireEvent.click(document.querySelector("[data-disclosure-row]")!);
}

describe("a remote shell card", () => {
  it("reads bash and names the host that ran the command", async () => {
    openShellCard({ environment: REMOTE_ENV, platform: "windows" });
    // The row title carries the interpreter; the card's prompt carries `user@host:cwd`.
    expect(screen.getByText("bash")).toBeTruthy();
    expect(screen.getByTestId("terminal-host").textContent).toBe(
      "ubuntu@192.168.2.125:/srv/app",
    );
    expect(screen.getByTestId("terminal-prompt").textContent).toBe("$");
    expect(screen.getByTestId("terminal-status").textContent).toBe("exit 0");
    expect(screen.getByTestId("terminal-output").textContent).toContain("ok");
    // The card says which shell it is drawing for, so CSS and tests can both see it.
    const card = screen.getByTestId("terminal-command").closest(".rv-card")!;
    expect(card.getAttribute("data-shell")).toBe("bash");
  });

  it("highlights the command with the POSIX grammar once it lands", async () => {
    openShellCard({ environment: REMOTE_ENV, platform: "windows" }, 'echo "hi" | grep hi');
    await ensureLanguage("bash");
    await waitFor(() => {
      const command = screen.getByTestId("terminal-command");
      const coloured = Array.from(command.querySelectorAll("span")).find((span) =>
        /var\(--syntax-/.test(span.style.color),
      );
      expect(coloured).toBeDefined();
    });
    // The text is still exactly the command, however many runs it was split into.
    expect(screen.getByTestId("terminal-command").textContent).toBe('echo "hi" | grep hi');
  });
});

describe("a local shell card", () => {
  it("reads PowerShell on this Windows machine, with a PS prompt", () => {
    openShellCard({ environment: null, platform: "windows" }, "Get-ChildItem");
    expect(screen.getByText("PowerShell")).toBeTruthy();
    expect(screen.getByTestId("terminal-prompt").textContent).toBe("PS >");
    expect(screen.queryByTestId("terminal-host")).toBeNull();
    const card = screen.getByTestId("terminal-command").closest(".rv-card")!;
    expect(card.getAttribute("data-shell")).toBe("powershell");
  });

  it("follows the session's default shell when Settings made it bash", () => {
    // The row said PowerShell with a `PS >` prompt over bash's own error (owner, 2026-10-15).
    openShellCard({ environment: null, platform: "windows", shell: "bash" }, ".venv/Scripts/python.exe x.py");
    expect(screen.getByText("bash")).toBeTruthy();
    expect(screen.getByTestId("terminal-prompt").textContent).toBe("$");
  });

  it("believes the result card about the shell a finished command ran in", () => {
    const call = shellCall("ls") as Extract<Item, { kind: "tool" }>;
    const card = { ...(call.presentation!.card as object), shell: "bash" };
    render(
      <SessionShellContext.Provider value={{ environment: null, platform: "windows" }}>
        <Transcript
          items={[{ kind: "user", text: "go" }, { ...call, presentation: { ...call.presentation!, card } } as Item]}
          onApprove={vi.fn()}
        />
      </SessionShellContext.Provider>,
    );
    fireEvent.click(document.querySelector("[data-disclosure-row]")!);
    expect(screen.getByText("bash")).toBeTruthy();
    expect(screen.getByTestId("terminal-prompt").textContent).toBe("$");
  });

  it("lets cmd /c name its own interpreter over the session's PowerShell", () => {
    openShellCard({ environment: null, platform: "windows" }, "cmd /c dir");
    expect(screen.getByText("cmd")).toBeTruthy();
    expect(screen.getByTestId("terminal-prompt").textContent).toBe(">");
  });

  it("keeps a local POSIX session on bash with the plain prompt", () => {
    openShellCard({ environment: null, platform: "macos" });
    expect(screen.getByText("bash")).toBeTruthy();
    expect(screen.getByTestId("terminal-prompt").textContent).toBe("$");
  });

  it("defaults to bash when no session shell has been provided at all", () => {
    // The pre-remote behaviour, and the contract isolated card tests already rely on.
    render(
      <Transcript
        items={[{ kind: "user", text: "go" }, shellCall("npm test")]}
        onApprove={vi.fn()}
      />,
    );
    expect(screen.getByText("bash")).toBeTruthy();
  });
});
