// A message sent with a slash command: the bubble draws the command and what was typed
// after it, the template the model read waits behind "What Reeve sent", the question line lists the
// command, and a reopened conversation draws it the same from the message's `_command` row.

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { Item } from "../types";
import { itemsFromMessages } from "../itemsFromMessages";
import { Transcript } from "./Transcript";
import { Composer } from "./Composer";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const TEMPLATE = "Write or update `AGENTS.md` for this repository.\n\nWhat the person asked for (this comes first):\nkeep it short";
const init: Item = {
  kind: "user",
  text: TEMPLATE,
  command: { kind: "prompt", name: "init", args: "keep it short", description: "write a short AGENTS.md from commands that ran" },
};

describe("a message sent with a command", () => {
  it("draws the command and what was typed, and keeps the template behind a fold", () => {
    const { container } = render(<Transcript items={[init]} running={false} onApprove={vi.fn()} />);
    expect(screen.getByTestId("command-name").textContent).toBe("/init");
    expect(screen.getByText("Write a short AGENTS.md from commands that ran")).toBeTruthy();
    expect(screen.getByText("keep it short", { selector: ".cmd-args" })).toBeTruthy();
    expect(screen.queryByTestId("command-sent")).toBeNull();
    expect(container.textContent).not.toContain("Write or update");
    fireEvent.click(screen.getByTestId("command-sent-toggle"));
    expect(screen.getByTestId("command-sent").textContent).toContain("Write or update `AGENTS.md`");
    // The question line and Copy read the line the person wrote.
    expect(container.querySelector("[data-user-text]")?.getAttribute("data-user-text")).toBe("/init keep it short");
  });

  it("draws a skill by its name with nothing more to open", () => {
    const skill: Item = { kind: "user", text: "/greet the team", command: { kind: "skill", name: "greet", args: "the team" } };
    render(<Transcript items={[skill]} running={false} onApprove={vi.fn()} />);
    expect(screen.getByTestId("command-bubble").dataset.kind).toBe("skill");
    expect(screen.getByTestId("command-name").textContent).toBe("/greet");
    expect(screen.queryByTestId("command-sent-toggle")).toBeNull();
  });

  it("is drawn the same when the conversation is reopened", () => {
    const [user] = itemsFromMessages([
      { role: "user", content: TEMPLATE, _command: { kind: "prompt", name: "init", args: "keep it short" } },
    ]) as Array<Extract<Item, { kind: "user" }>>;
    expect(user.command?.name).toBe("init");
    expect(user.text).toBe(TEMPLATE);
  });
});

describe("the composer with a command", () => {
  const props = () => ({
    mode: "interactive",
    model: "gpt-5.6-sol",
    running: false,
    connected: true,
    sessionId: "s1",
    onSend: vi.fn(),
    onInterrupt: vi.fn(),
    onModeChange: vi.fn(),
    onModelChange: vi.fn(),
  });

  it("highlights /init where it stands and sends the command with the expanded text", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true, json: async () => ({}) }) as Response));
    const p = props();
    render(<Composer {...p} />);
    const box = screen.getByPlaceholderText(/Ask anything/) as HTMLTextAreaElement;
    fireEvent.change(box, { target: { value: "/init " } });
    const mark = await screen.findByTestId("command-mark");
    expect(mark.textContent).toContain("/init");
    expect(mark.textContent).toContain("write a short AGENTS.md");
    fireEvent.change(box, { target: { value: "/init keep it short" } });
    // Something typed after it: the line about it gives way.
    expect(screen.getByTestId("command-mark").textContent).toBe("/init");
    fireEvent.keyDown(box, { key: "Enter" });
    await waitFor(() => expect(p.onSend).toHaveBeenCalled());
    const [text, , , , command] = p.onSend.mock.calls[0];
    expect(text).toContain("Write or update `AGENTS.md`");
    expect(command).toEqual(expect.objectContaining({ kind: "prompt", name: "init", args: "keep it short" }));
  });
});
