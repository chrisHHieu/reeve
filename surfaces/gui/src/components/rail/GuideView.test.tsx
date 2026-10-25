// @vitest-environment jsdom
// The Start page: a row per kind with the session's facts, and the terminal row's shell menu.
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { GuideView } from "./GuideView";
import { listUserTerminalShells } from "../../api/terminal";
import { writeShellPref } from "./shellPref";

vi.mock("../../api/terminal", () => ({
  listUserTerminalShells: vi.fn(),
}));

const SHELLS = [
  { id: "cmd", name: "cmd.exe", path: "C:\\Windows\\System32\\cmd.exe" },
  { id: "bash", name: "bash", path: "C:\\Program Files\\Git\\bin\\bash.exe" },
  { id: "powershell", name: "powershell.exe", path: "C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe" },
];

afterEach(() => {
  cleanup();
  localStorage.clear();
  vi.mocked(listUserTerminalShells).mockReset();
});

function guide(over: Partial<Parameters<typeof GuideView>[0]> = {}) {
  const onPick = vi.fn();
  render(<GuideView sessionId="s1" shellLabel="PowerShell" onPick={onPick} {...over} />);
  return { onPick };
}

describe("GuideView", () => {
  it("lists the four kinds from the top, each row naming the question its tab answers", () => {
    writeShellPref("cmd");
    guide({ workspace: "reeve" });
    expect(screen.getByTestId("rail-guide")).toBeTruthy();
    expect(screen.getByRole("heading", { name: "reeve" })).toBeTruthy();
    const rows = ["review", "files", "terminal", "browser"].map(
      (door) => screen.getByTestId(`rail-door-${door}`).closest(".guide-row")!.textContent,
    );
    // Review and Files were mistaken for each other while a tile said only the name.
    expect(rows[0]).toContain("What changed, and do I keep it?");
    expect(rows[1]).toContain("What is in my folders right now?");
    expect(rows[2]).toContain("Run a command myself");
    expect(rows[3]).toContain("Look at a page");
    // The terminal row wears the remembered shell on its own control.
    expect(screen.getByTestId("rail-door-terminal-menu").textContent).toContain("cmd");
    expect(screen.getByTestId("rail-door-terminal").getAttribute("aria-label")).toBe("Open a terminal (cmd)");
  });

  it("each row carries what is true of the session now", () => {
    guide({
      folders: ["workspace", "scratch"],
      review: { files: 3, add: 42, del: 7 },
    });
    expect(screen.getByTestId("rail-door-review-fact").textContent).toBe("+42 −7 · 3 files");
    expect(screen.getByTestId("rail-door-files").closest(".guide-row")!.textContent).toContain("workspace, scratch");
    cleanup();
    guide({ review: { files: 0, add: 0, del: 0 } });
    expect(screen.getByTestId("rail-door-review-fact").textContent).toBe("Nothing changed");
  });

  it("the main terminal button opens the remembered shell", () => {
    writeShellPref("powershell");
    const props = guide();
    fireEvent.click(screen.getByTestId("rail-door-review"));
    fireEvent.click(screen.getByTestId("rail-door-browser"));
    fireEvent.click(screen.getByTestId("rail-door-terminal"));
    expect(props.onPick.mock.calls).toEqual([
      ["review"],
      ["browser"],
      ["terminal", { shell: "powershell" }],
    ]);
  });

  it("a closed tab can come back from Start", () => {
    const onReopen = vi.fn();
    guide({ recentClosed: [{ id: "browser-1", kind: "browser", url: "http://localhost:8000/x" }], onReopen });
    expect(screen.getByTestId("rail-guide-recent").textContent).toContain("localhost:8000");
    fireEvent.click(screen.getByTestId("rail-guide-reopen-browser-1"));
    expect(onReopen).toHaveBeenCalledWith(expect.objectContaining({ id: "browser-1" }));
  });

  it("the chevron opens the launch menu with the remembered pick checked", async () => {
    writeShellPref("cmd");
    vi.mocked(listUserTerminalShells).mockResolvedValue({ ok: true, shells: SHELLS });
    guide();
    fireEvent.click(screen.getByTestId("rail-door-terminal-menu"));
    expect(screen.getByTestId("rail-shell-loading")).toBeTruthy();

    const cmd = await screen.findByTestId("rail-shell-cmd");
    expect(screen.getByTestId("rail-shell-bash").textContent).toContain("bash");
    expect(screen.getByTestId("rail-shell-powershell").textContent).toContain("powershell.exe");
    expect(cmd.className).toContain("picked");
    expect(cmd.textContent).toContain("✓");
    expect(listUserTerminalShells).toHaveBeenCalledWith("s1");
  });

  it("choosing a shell hands its id up and closes the menu", async () => {
    vi.mocked(listUserTerminalShells).mockResolvedValue({ ok: true, shells: SHELLS });
    const props = guide();
    fireEvent.click(screen.getByTestId("rail-door-terminal-menu"));
    fireEvent.click(await screen.findByTestId("rail-shell-cmd"));
    expect(props.onPick).toHaveBeenCalledWith("terminal", { shell: "cmd" });
    expect(screen.queryByTestId("rail-shell-menu")).toBeNull();
  });

  it("a failed discovery offers Retry, which refetches", async () => {
    vi.mocked(listUserTerminalShells)
      .mockRejectedValueOnce(new Error("ssh unreachable"))
      .mockResolvedValueOnce({ ok: true, shells: SHELLS });
    guide();
    fireEvent.click(screen.getByTestId("rail-door-terminal-menu"));
    expect((await screen.findByTestId("rail-shell-error")).textContent).toContain("ssh unreachable");

    fireEvent.click(screen.getByTestId("rail-shell-retry"));
    expect(await screen.findByTestId("rail-shell-cmd")).toBeTruthy();
    await waitFor(() => expect(listUserTerminalShells).toHaveBeenCalledTimes(2));
  });

  it("an empty catalogue says so instead of showing an empty menu", async () => {
    vi.mocked(listUserTerminalShells).mockResolvedValue({ ok: true, shells: [] });
    guide();
    fireEvent.click(screen.getByTestId("rail-door-terminal-menu"));
    expect(await screen.findByTestId("rail-shell-empty")).toBeTruthy();
  });
});
