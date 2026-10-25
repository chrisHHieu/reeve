// `/terminal`'s panel: xterm.js over the session's own PTY. The emulator writes the
// terminal's byte stream, keystrokes go back raw, resize is reported, the shell's marks say
// what runs and how it ended, and a refused shell or a dropped socket is visible rather than
// a dead-looking prompt.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { TerminalPanel } from "./TerminalPanel";
import { openUserTerminal } from "../../api/terminal";

const xterm = vi.hoisted(() => ({
  writes: [] as string[],
  dataHandlers: [] as ((data: string) => void)[],
  resizeHandlers: [] as ((size: { cols: number; rows: number }) => void)[],
  osc: {} as Record<number, (data: string) => boolean>,
  lines: [] as string[],
  disposed: 0,
  reset() {
    xterm.writes = [];
    xterm.dataHandlers = [];
    xterm.resizeHandlers = [];
    xterm.osc = {};
    xterm.lines = [];
    xterm.disposed = 0;
  },
}));

vi.mock("@xterm/xterm", () => ({
  Terminal: class {
    cols = 100;
    rows = 30;
    parser = {
      registerOscHandler(code: number, handler: (data: string) => boolean) {
        xterm.osc[code] = handler;
        return { dispose() {} };
      },
    };
    buffer = {
      active: {
        get baseY() {
          return 0;
        },
        get cursorY() {
          return Math.max(0, xterm.lines.length - 1);
        },
        getLine(y: number) {
          return { translateToString: () => xterm.lines[y] ?? "" };
        },
      },
    };
    loadAddon() {}
    open() {}
    registerMarker() {
      return { line: Math.max(0, xterm.lines.length - 1), dispose() {} };
    }
    onData(handler: (data: string) => void) {
      xterm.dataHandlers.push(handler);
      return { dispose() {} };
    }
    onResize(handler: (size: { cols: number; rows: number }) => void) {
      xterm.resizeHandlers.push(handler);
      return { dispose() {} };
    }
    onSelectionChange() {
      return { dispose() {} };
    }
    write(data: string, done?: () => void) {
      xterm.writes.push(data);
      // A device-attributes query is answered as the emulator parses it, as xterm.js does.
      if (data.includes("\x1b[c")) for (const handler of xterm.dataHandlers) handler("\x1b[?1;2c");
      done?.();
    }
    clear() {
      xterm.writes.push("__clear__");
    }
    reset() {
      xterm.writes.push("__reset__");
    }
    focus() {}
    dispose() {
      xterm.disposed += 1;
    }
  },
}));

vi.mock("@xterm/addon-fit", () => ({ FitAddon: class { fit() {} } }));
vi.mock("@xterm/addon-search", () => ({
  SearchAddon: class {
    findNext() {
      return true;
    }
    findPrevious() {
      return true;
    }
    clearDecorations() {}
    onDidChangeResults() {
      return { dispose() {} };
    }
  },
}));

vi.mock("../../api/terminal", () => ({
  openUserTerminal: vi.fn().mockResolvedValue({ ok: true, running: true, shell: "cmd.exe" }),
  listUserTerminalShells: vi.fn().mockResolvedValue({ ok: true, shells: [] }),
  openUserTerminalSocket: (sessionId: string, terminalId: string) =>
    new (globalThis as any).WebSocket(`ws://test/ws/terminal/${sessionId}?terminal=${terminalId}`),
}));

class FakeWebSocket {
  static CONNECTING = 0;
  static OPEN = 1;
  static CLOSING = 2;
  static CLOSED = 3;
  static instances: FakeWebSocket[] = [];

  readyState = FakeWebSocket.OPEN;
  sent: string[] = [];
  onmessage: ((event: { data: string }) => void) | null = null;
  onclose: (() => void) | null = null;

  constructor(public url: string) {
    FakeWebSocket.instances.push(this);
  }

  send(data: string) {
    this.sent.push(data);
  }

  close() {
    this.readyState = FakeWebSocket.CLOSED;
    this.onclose?.();
  }

  emit(message: unknown) {
    act(() => {
      this.onmessage?.({ data: JSON.stringify(message) });
    });
  }

  drop() {
    act(() => {
      this.onclose?.();
    });
  }
}

beforeEach(() => {
  FakeWebSocket.instances = [];
  xterm.reset();
  vi.mocked(openUserTerminal).mockClear();
  vi.stubGlobal("WebSocket", FakeWebSocket);
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const socket = () => FakeWebSocket.instances[FakeWebSocket.instances.length - 1];
const state = () => screen.getByTestId("terminal-state").textContent;

/** The shell's own marks, as the emulator's parser hands them over. */
const mark = (code: 133 | 633, data: string) => act(() => void xterm.osc[code]?.(data));

async function mount(props: Partial<Parameters<typeof TerminalPanel>[0]> = {}) {
  const onState = vi.fn();
  render(<TerminalPanel sessionId="s1" terminalId="t1" onState={onState} {...props} />);
  // The create/attach POST resolves before the socket is opened.
  await act(async () => {});
  return { onState };
}

describe("TerminalPanel", () => {
  it("attaches its own terminal channel with the tab id and the emulator's size", async () => {
    await mount({ terminalId: "t2" });
    expect(FakeWebSocket.instances).toHaveLength(1);
    expect(socket().url).toContain("/ws/terminal/s1?terminal=t2");
    expect(openUserTerminal).toHaveBeenCalledWith("s1", { terminal: "t2", shell: undefined, cols: 100, rows: 30 });
  });

  it("replays the scrollback; a marked shell at its prompt reads Ready, never running", async () => {
    await mount();
    socket().emit({
      type: "terminal",
      data: { running: true, marks: true, cwd: "/work/app", shell: "pwsh", output: "PS> hello\r\n", produced: 10 },
    });
    expect(xterm.writes).toContain("__reset__");
    expect(xterm.writes).toContain("PS> hello\r\n");
    expect(state()).toBe("Ready");
    expect(screen.getByText("/work/app")).toBeTruthy();
    // Nothing runs, so there is nothing to stop.
    expect(screen.queryByTestId("terminal-interrupt")).toBeNull();
  });

  it("answers a replayed scrollback's queries to nobody: the shell never sees them as typing", async () => {
    await mount();
    socket().emit({ type: "terminal", data: { running: true, output: "\x1b[cPS> ", produced: 6 } });
    expect(socket().sent.some((frame) => frame.includes("?1;2c"))).toBe(false);
    // What the person types after the replay still goes through.
    act(() => xterm.dataHandlers[0]("dir\r"));
    expect(socket().sent).toContain(JSON.stringify({ type: "input", data: "dir\r" }));
  });

  it("passes the menu's shell through and shows it in the head", async () => {
    await mount({ shell: "cmd" });
    expect(openUserTerminal).toHaveBeenCalledWith("s1", { terminal: "t1", shell: "cmd", cols: 100, rows: 30 });
    expect(screen.getByTestId("terminal-shell").textContent).toBe("cmd");
    socket().emit({ type: "terminal", data: { running: true, output: "", produced: 0, shell: "C:\\Windows\\System32\\cmd.exe" } });
    expect(screen.getByTestId("terminal-shell").textContent).toBe("cmd");
  });

  it("keystrokes go back raw and resize reports the emulator's size", async () => {
    await mount();
    act(() => xterm.dataHandlers[0]("git status\r"));
    expect(socket().sent).toContain(JSON.stringify({ type: "input", data: "git status\r" }));
    act(() => xterm.resizeHandlers[0]({ cols: 132, rows: 43 }));
    expect(socket().sent).toContain(JSON.stringify({ type: "resize", data: { cols: 132, rows: 43 } }));
  });

  it("surfaces a refused shell instead of opening a socket", async () => {
    vi.mocked(openUserTerminal).mockResolvedValueOnce({
      ok: false,
      error: "this session works on a remote host; the local terminal panel cannot attach to it yet",
    });
    await mount();
    expect(FakeWebSocket.instances).toHaveLength(0);
    expect(xterm.writes.join("")).toContain("remote host");
    expect(state()).toContain("Exited");
  });

  it("says which command runs, offers Stop while it does, and names the tab after it", async () => {
    const { onState } = await mount();
    socket().emit({ type: "terminal", data: { running: true, marks: true, cwd: "C:\\work\\app", output: "", produced: 0 } });
    expect(onState).toHaveBeenLastCalledWith(expect.objectContaining({ title: "app", running: false }));

    mark(633, "E;npm run dev");
    mark(133, "C");
    expect(state()).toMatch(/^Running npm run dev · 0:0\d$/);
    expect(onState).toHaveBeenLastCalledWith(expect.objectContaining({ title: "npm run dev", running: true }));
    fireEvent.click(screen.getByTestId("terminal-interrupt"));
    expect(socket().sent).toContain(JSON.stringify({ type: "interrupt" }));

    mark(133, "D;0");
    expect(state()).toBe("Ready");
    expect(screen.queryByTestId("terminal-failed")).toBeNull();
    expect(onState).toHaveBeenLastCalledWith(expect.objectContaining({ title: "app", running: false }));
  });

  it("a failed command offers to ask Reeve, which fills the chat box and sends nothing", async () => {
    await mount();
    socket().emit({ type: "terminal", data: { running: true, marks: true, output: "", produced: 0 } });
    const asked: string[] = [];
    const listen = (event: Event) => asked.push((event as CustomEvent).detail.text);
    window.addEventListener("reeve-insert-text", listen);

    mark(633, "E;pytest -q");
    xterm.lines = ["PS> pytest -q", "FAILED tests/test_a.py::test_x - AssertionError", "1 failed"];
    mark(133, "C");
    mark(133, "D;1");
    const chip = screen.getByTestId("terminal-failed");
    expect(chip.textContent).toContain("Exit 1");
    expect(chip.textContent).toContain("pytest -q");
    fireEvent.click(screen.getByTestId("terminal-ask-failed"));
    window.removeEventListener("reeve-insert-text", listen);
    expect(asked).toHaveLength(1);
    expect(asked[0]).toContain("This command failed in my terminal (exit 1)");
    expect(asked[0]).toContain("$ pytest -q");
    expect(asked[0]).toContain("1 failed");
    expect(screen.getByTestId("terminal-ask-failed").textContent).toBe("Added to your message ✓");
  });

  it("a replayed scrollback sets the state but raises no old failure", async () => {
    await mount();
    socket().emit({ type: "terminal", data: { running: true, marks: true, output: "", produced: 0 } });
    // Marks parsed while the snapshot is being written are history.
    act(() => {
      socket().onmessage?.({
        data: JSON.stringify({ type: "terminal", data: { running: true, marks: true, output: "old", produced: 3 } }),
      });
    });
    expect(screen.queryByTestId("terminal-failed")).toBeNull();
  });

  it("a shell that cannot mark its commands keeps Ctrl-C and says nothing it cannot know", async () => {
    await mount({ shell: "cmd" });
    socket().emit({ type: "terminal", data: { running: true, marks: false, output: "", produced: 0 } });
    expect(state()).toBe("");
    expect(screen.getByTestId("terminal-interrupt").textContent).toBe("Ctrl-C");
  });

  it("clear resets the view", async () => {
    await mount();
    socket().emit({ type: "terminal", data: { running: true, output: "", produced: 0 } });
    socket().emit({ type: "output", data: { chunk: "hello\r\n" } });
    expect(xterm.writes).toContain("hello\r\n");
    fireEvent.click(screen.getByTestId("terminal-clear"));
    expect(xterm.writes).toContain("__clear__");
    expect(xterm.writes).toContain("\x1b[2J\x1b[H");
  });

  it("an exited shell says its code and starts again on request", async () => {
    await mount();
    socket().emit({ type: "terminal", data: { running: true, marks: true, output: "", produced: 0 } });
    socket().emit({ type: "exit", data: { exit_code: 0 } });
    expect(state()).toBe("Exited · code 0");
    expect(screen.getByTestId("terminal-exited").textContent).toContain("exited with code 0");
    fireEvent.click(screen.getByTestId("terminal-restart"));
    await act(async () => {});
    expect(FakeWebSocket.instances).toHaveLength(2);
  });

  it("a dropped socket reads disconnected, keeps the text, and reconnects on request", async () => {
    await mount();
    socket().emit({ type: "terminal", data: { running: true, output: "", produced: 0 } });
    socket().drop();
    expect(state()).toBe("Disconnected");
    expect(xterm.writes).not.toContain("__clear__");
    fireEvent.click(screen.getByTestId("terminal-reconnect"));
    await act(async () => {});
    expect(FakeWebSocket.instances).toHaveLength(2);
  });
});
