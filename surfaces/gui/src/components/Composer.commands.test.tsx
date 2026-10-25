// The composer's built-in session commands: `/new`, `/export`,
// `/model`, `/agent`, `/fork`, `/mcp`, `/undo`, `/redo`, `/open` and `/plan` run the app's
// EXISTING handlers on pick, and `/init` / `/review` are prompt templates — selecting one
// INSERTs `/name ` into the draft and
// submitting expands the template. A row that cannot run is visibly disabled and says why.
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { Composer } from "./Composer";

const CATALOG = {
  models: [
    {
      id: "gpt-5.6-sol",
      name: "GPT-5.6 Sol",
      provider: "openai",
      provider_title: "OpenAI",
    },
    {
      id: "deepseek:deepseek-v4-flash",
      name: "DeepSeek V4 Flash",
      provider: "deepseek",
      provider_title: "DeepSeek",
    },
  ],
  default: "gpt-5.6-sol",
};

function stubFetch() {
  const calls: { url: string; method: string; body?: unknown }[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      const method = (init?.method || "GET").toUpperCase();
      const body = init?.body ? JSON.parse(String(init.body)) : undefined;
      calls.push({ url, method, body });
      if (url.includes("/skills")) return { ok: true, json: async () => ({ skills: [] }) } as Response;
      if (url.includes("/models/catalog")) return { ok: true, json: async () => CATALOG } as Response;
      return { ok: true, json: async () => ({}) } as Response;
    }),
  );
  return calls;
}

type CommandProps = Parameters<typeof Composer>[0] & { onSend: ReturnType<typeof vi.fn> };

const props = (extra: Partial<Parameters<typeof Composer>[0]> = {}): CommandProps =>
  ({
    mode: "interactive",
    model: "gpt-5.6-sol",
    // Both turned on in Settings: the picker lists what Settings turned on.
    models: ["gpt-5.6-sol", "deepseek:deepseek-v4-flash"],
    running: false,
    connected: true,
    sessionId: "s1",
    onSend: vi.fn(),
    onInterrupt: vi.fn(),
    onModeChange: vi.fn(),
    onModelChange: vi.fn(),
    ...extra,
  }) as CommandProps;

const box = () => screen.getByPlaceholderText(/Ask anything/);
const draft = () => (box() as HTMLTextAreaElement).value;

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("Composer / built-in session commands", () => {
  it("/new runs the new-session handler and inserts nothing", async () => {
    stubFetch();
    const onNewSession = vi.fn();
    const p = props({ onNewSession });
    render(<Composer {...p} />);
    fireEvent.change(box(), { target: { value: "/new" } });
    fireEvent.click(await screen.findByTestId("slash-command-new"));
    expect(onNewSession).toHaveBeenCalledTimes(1);
    expect(draft()).toBe("");
    expect(p.onSend).not.toHaveBeenCalled();
  });

  it("/export runs the export handler; with no handler the row is disabled and says why", async () => {
    stubFetch();
    const onExportSession = vi.fn();
    render(<Composer {...props({ onExportSession })} />);
    fireEvent.change(box(), { target: { value: "/export" } });
    fireEvent.click(await screen.findByTestId("slash-command-export"));
    expect(onExportSession).toHaveBeenCalledTimes(1);

    cleanup();
    stubFetch();
    render(<Composer {...props()} />);
    fireEvent.change(box(), { target: { value: "/export" } });
    const row = await screen.findByTestId("slash-command-export");
    expect((row as HTMLButtonElement).disabled).toBe(true);
    expect(row.getAttribute("aria-disabled")).toBe("true");
    expect(row.textContent).toContain("Unavailable in this surface");
  });

  it("/model opens the composer's model picker in place (no modal)", async () => {
    stubFetch();
    const onModelChange = vi.fn();
    render(<Composer {...props({ onModelChange })} />);
    fireEvent.change(box(), { target: { value: "/model" } });
    fireEvent.click(await screen.findByTestId("slash-command-model"));
    await screen.findByTestId("model-picker-panel");
    // The menu mounts narrowed to the session model's provider; the grouped catalogue is
    // the user's own "All" pick on its left.
    fireEvent.click(screen.getByTestId("provider-row-all"));
    fireEvent.click(await screen.findByTestId("model-row-deepseek:deepseek-v4-flash"));
    await waitFor(() =>
      expect(onModelChange).toHaveBeenCalledWith("deepseek:deepseek-v4-flash"),
    );
    // The panel stays for the thinking level; Escape leaves it.
    fireEvent.keyDown(screen.getByTestId("model-picker-panel"), { key: "Escape" });
    expect(screen.queryByTestId("model-picker-panel")).toBeNull();
  });

  it("the provider pick narrows the /model menu to that provider", async () => {
    stubFetch();
    render(<Composer {...props()} />);
    // The providers are down the model panel's left: open it, pick DeepSeek…
    fireEvent.click(screen.getByTestId("model-picker-trigger"));
    fireEvent.click(await screen.findByTestId("provider-row-deepseek"));
    // …and the panel, still open, offers only DeepSeek's rows.
    expect(await screen.findByTestId("model-row-deepseek:deepseek-v4-flash")).toBeTruthy();
    expect(screen.queryByTestId("model-row-gpt-5.6-sol")).toBeNull();
  });

  // There is one agent: nothing to switch a session to, so there is no `/agent` row. A
  // subagent is named with "@".
  it("has no /agent command", async () => {
    stubFetch();
    render(<Composer {...props()} />);
    fireEvent.change(box(), { target: { value: "/agent" } });
    await screen.findByTestId("skill-popup");
    expect(screen.queryByTestId("slash-command-agent")).toBeNull();
    expect(screen.queryByTestId("agent-picker-menu")).toBeNull();
  });

  it("/fork forks through the app handler; an empty session disables the row with the reference design's reason", async () => {
    stubFetch();
    const onForkSession = vi.fn();
    render(<Composer {...props({ hasMessages: true, onForkSession })} />);
    fireEvent.change(box(), { target: { value: "/fork" } });
    fireEvent.click(await screen.findByTestId("slash-command-fork"));
    expect(onForkSession).toHaveBeenCalledTimes(1);

    cleanup();
    stubFetch();
    const blocked = vi.fn();
    render(<Composer {...props({ hasMessages: false, onForkSession: blocked })} />);
    fireEvent.change(box(), { target: { value: "/fork" } });
    const row = await screen.findByTestId("slash-command-fork");
    expect((row as HTMLButtonElement).disabled).toBe(true);
    expect(row.textContent).toContain("No messages to fork from");
    // Enter on the open popup must not run a disabled row either.
    fireEvent.keyDown(box(), { key: "Enter" });
    expect(blocked).not.toHaveBeenCalled();
    expect(draft()).toBe("/fork");
  });

  it("/mcp navigates to the Connectors surface", async () => {
    stubFetch();
    const onOpenConnectors = vi.fn();
    render(<Composer {...props({ onOpenConnectors })} />);
    fireEvent.change(box(), { target: { value: "/mcp" } });
    fireEvent.click(await screen.findByTestId("slash-command-mcp"));
    expect(onOpenConnectors).toHaveBeenCalledTimes(1);
  });

  it("/undo runs the revert handler; with no visible user message the row says why", async () => {
    stubFetch();
    const onUndoSession = vi.fn();
    render(<Composer {...props({ canUndo: true, onUndoSession })} />);
    fireEvent.change(box(), { target: { value: "/undo" } });
    fireEvent.click(await screen.findByTestId("slash-command-undo"));
    expect(onUndoSession).toHaveBeenCalledTimes(1);
    expect(draft()).toBe(""); // a command runs in place, it does not insert

    cleanup();
    stubFetch();
    const blocked = vi.fn();
    render(<Composer {...props({ canUndo: false, onUndoSession: blocked })} />);
    fireEvent.change(box(), { target: { value: "/undo" } });
    const row = await screen.findByTestId("slash-command-undo");
    expect((row as HTMLButtonElement).disabled).toBe(true);
    expect(row.textContent).toContain("No messages to undo");
    fireEvent.keyDown(box(), { key: "Enter" });
    expect(blocked).not.toHaveBeenCalled();
  });

  it("/redo runs the redo handler; nothing staged disables it", async () => {
    stubFetch();
    const onRedoSession = vi.fn();
    render(<Composer {...props({ canRedo: true, onRedoSession })} />);
    fireEvent.change(box(), { target: { value: "/redo" } });
    fireEvent.click(await screen.findByTestId("slash-command-redo"));
    expect(onRedoSession).toHaveBeenCalledTimes(1);

    cleanup();
    stubFetch();
    render(<Composer {...props({ canRedo: false })} />);
    fireEvent.change(box(), { target: { value: "/redo" } });
    const row = await screen.findByTestId("slash-command-redo");
    expect((row as HTMLButtonElement).disabled).toBe(true);
    expect(row.textContent).toContain("Nothing to redo");
  });

  it("/open opens the file picker; with no handler the row is disabled and says why", async () => {
    stubFetch();
    const onOpenFile = vi.fn();
    render(<Composer {...props({ onOpenFile })} />);
    fireEvent.change(box(), { target: { value: "/open" } });
    fireEvent.click(await screen.findByTestId("slash-command-open"));
    expect(onOpenFile).toHaveBeenCalledTimes(1);
    expect(draft()).toBe(""); // a command runs in place, it does not insert

    cleanup();
    stubFetch();
    render(<Composer {...props()} />);
    fireEvent.change(box(), { target: { value: "/open" } });
    const row = await screen.findByTestId("slash-command-open");
    expect((row as HTMLButtonElement).disabled).toBe(true);
    expect(row.textContent).toContain("Unavailable in this surface");
  });

  it("/terminal toggles the user-shell panel; with no handler the row is disabled", async () => {
    stubFetch();
    const onToggleTerminal = vi.fn();
    render(<Composer {...props({ onToggleTerminal })} />);
    fireEvent.change(box(), { target: { value: "/terminal" } });
    fireEvent.click(await screen.findByTestId("slash-command-terminal"));
    expect(onToggleTerminal).toHaveBeenCalledTimes(1);
    expect(draft()).toBe("");

    cleanup();
    stubFetch();
    render(<Composer {...props()} />);
    fireEvent.change(box(), { target: { value: "/terminal" } });
    const row = await screen.findByTestId("slash-command-terminal");
    expect((row as HTMLButtonElement).disabled).toBe(true);
    expect(row.textContent).toContain("Unavailable in this surface");
  });

  it("/workspace toggles the worktrees section; without a folder it says why", async () => {
    stubFetch();
    const onToggleWorktrees = vi.fn();
    render(<Composer {...props({ workspace: "/repo", onToggleWorktrees })} />);
    fireEvent.change(box(), { target: { value: "/workspace" } });
    fireEvent.click(await screen.findByTestId("slash-command-workspace"));
    expect(onToggleWorktrees).toHaveBeenCalledTimes(1);
    expect(draft()).toBe("");

    cleanup();
    stubFetch();
    render(<Composer {...props({ workspace: "", onToggleWorktrees: vi.fn() })} />);
    fireEvent.change(box(), { target: { value: "/workspace" } });
    const row = await screen.findByTestId("slash-command-workspace");
    expect((row as HTMLButtonElement).disabled).toBe(true);
    expect(row.textContent).toContain("No project folder");
  });

  it("/share runs the configured upload; with no endpoint the row is disabled and says why", async () => {
    stubFetch();
    const onShareSession = vi.fn();
    render(<Composer {...props({ canShare: true, onShareSession })} />);
    fireEvent.change(box(), { target: { value: "/share" } });
    fireEvent.click(await screen.findByTestId("slash-command-share"));
    expect(onShareSession).toHaveBeenCalledTimes(1);
    expect(draft()).toBe("");

    cleanup();
    stubFetch();
    render(
      <Composer
        {...props({
          onShareSession: vi.fn(),
          canShare: false,
          shareReason: "Sharing needs a configured endpoint",
        })}
      />,
    );
    fireEvent.change(box(), { target: { value: "/share" } });
    const row = await screen.findByTestId("slash-command-share");
    expect((row as HTMLButtonElement).disabled).toBe(true);
    expect(row.textContent).toContain("Sharing needs a configured endpoint");
  });

  it("/unshare only runs when the endpoint supports a revoke", async () => {
    stubFetch();
    const onUnshareSession = vi.fn();
    render(<Composer {...props({ canUnshare: true, onUnshareSession })} />);
    fireEvent.change(box(), { target: { value: "/unshare" } });
    fireEvent.click(await screen.findByTestId("slash-command-unshare"));
    expect(onUnshareSession).toHaveBeenCalledTimes(1);

    cleanup();
    stubFetch();
    render(
      <Composer
        {...props({
          onUnshareSession: vi.fn(),
          canUnshare: false,
          unshareReason: "The configured endpoint has no revoke",
        })}
      />,
    );
    fireEvent.change(box(), { target: { value: "/unshare" } });
    const row = await screen.findByTestId("slash-command-unshare");
    expect((row as HTMLButtonElement).disabled).toBe(true);
    expect(row.textContent).toContain("The configured endpoint has no revoke");
  });
});

describe("Composer / prompt commands", () => {
  it("/init inserts /init into the draft and resolves the template on send", async () => {
    stubFetch();
    const p = props();
    render(<Composer {...p} />);
    fireEvent.change(box(), { target: { value: "/ini" } });
    const row = await screen.findByTestId("slash-command-init");
    expect(row.textContent).toContain("write a short AGENTS.md from commands that ran");
    fireEvent.click(row);
    // The custom commands INSERT; nothing is sent on pick.
    expect(draft()).toBe("/init ");
    expect(p.onSend).not.toHaveBeenCalled();

    fireEvent.change(box(), { target: { value: "/init focus on the GUI layer" } });
    fireEvent.keyDown(box(), { key: "Enter" });
    await waitFor(() => expect(p.onSend).toHaveBeenCalledTimes(1));
    const [text] = p.onSend.mock.calls[0];
    expect(text).toContain("Write or update `AGENTS.md`");
    expect(text).toContain("focus on the GUI layer");
    expect(text).not.toContain("$ARGUMENTS");
    expect(draft()).toBe("");
  });

  it("/review inserts and resolves the argument tail", async () => {
    stubFetch();
    const p = props();
    render(<Composer {...p} />);
    fireEvent.change(box(), { target: { value: "/rev" } });
    fireEvent.click(await screen.findByTestId("slash-command-review"));
    expect(draft()).toBe("/review ");

    fireEvent.change(box(), { target: { value: "/review main" } });
    fireEvent.keyDown(box(), { key: "Enter" });
    await waitFor(() => expect(p.onSend).toHaveBeenCalledTimes(1));
    const [text] = p.onSend.mock.calls[0];
    expect(text).toContain("You are a code reviewer.");
    expect(text).toContain("Input: main");
  });

  it("a command that merely starts with /init text still sends as a plain message", async () => {
    stubFetch();
    const p = props();
    render(<Composer {...p} />);
    fireEvent.change(box(), { target: { value: "/initiative status" } });
    fireEvent.keyDown(box(), { key: "Enter" });
    await waitFor(() => expect(p.onSend).toHaveBeenCalledTimes(1));
    expect(p.onSend.mock.calls[0][0]).toBe("/initiative status");
  });

  it("/plan turns the Plan overlay on from the row, and inserts nothing", async () => {
    stubFetch();
    const onPlanChange = vi.fn();
    const p = props({ onPlanChange });
    render(<Composer {...p} />);
    fireEvent.change(box(), { target: { value: "/plan" } });
    fireEvent.click(await screen.findByTestId("slash-command-plan"));
    expect(onPlanChange).toHaveBeenCalledWith(true);
    expect(p.onSend).not.toHaveBeenCalled();
    expect(draft()).toBe("");
  });

  it("a session already in Plan disables the /plan row with its reason", async () => {
    stubFetch();
    render(<Composer {...props({ plan: true, onPlanChange: vi.fn() })} />);
    fireEvent.change(box(), { target: { value: "/plan" } });
    const row = await screen.findByTestId("slash-command-plan");
    expect((row as HTMLButtonElement).disabled).toBe(true);
    expect(row.textContent).toContain("Plan is already on");
  });

  it("typed /plan enters, /plan off leaves, and /plan <objective> enters and sends", async () => {
    stubFetch();
    const onPlanChange = vi.fn();
    const p = props({ onPlanChange });
    render(<Composer {...p} />);

    // With whitespace after the name the popup is closed and submit owns the draft.
    fireEvent.change(box(), { target: { value: "/plan off" } });
    fireEvent.keyDown(box(), { key: "Enter" });
    await waitFor(() => expect(onPlanChange).toHaveBeenLastCalledWith(false));
    expect(p.onSend).not.toHaveBeenCalled();

    fireEvent.change(box(), { target: { value: "/plan split the importer" } });
    fireEvent.keyDown(box(), { key: "Enter" });
    await waitFor(() => expect(onPlanChange).toHaveBeenLastCalledWith(true));
    expect(p.onSend).toHaveBeenCalledWith("split the importer", [], undefined);
    expect(draft()).toBe("");
  });

  it("a command that merely starts with /planning still sends as a plain message", async () => {
    stubFetch();
    const onPlanChange = vi.fn();
    const p = props({ onPlanChange });
    render(<Composer {...p} />);
    fireEvent.change(box(), { target: { value: "/planning the release" } });
    fireEvent.keyDown(box(), { key: "Enter" });
    await waitFor(() => expect(p.onSend).toHaveBeenCalledTimes(1));
    expect(onPlanChange).not.toHaveBeenCalled();
  });
});

// The "/" popup is a floating panel now: anchored above the composer card, height-capped,
// and scrolled internally — the shape the model/usage menu already uses. It used to be an
// in-flow, uncapped list (owner screenshot 2026-09-18: with many compat skills installed it
// grew past the window and could not be scrolled).
describe("Composer / the '/' popup shell", () => {
  it("anchors above the card, caps its height, and scrolls its body", async () => {
    stubFetch();
    render(<Composer {...props()} />);
    fireEvent.change(box(), { target: { value: "/" } });
    const popup = await screen.findByTestId("skill-popup");
    expect(popup.className).toContain("absolute");
    expect(popup.className).toContain("bottom-full");
    // Its own ceiling; the stylesheet takes the smaller of it and the room the window has.
    expect(popup.className).toContain("[--pop-cap:340px]");
    expect(popup.className).toContain("flex-col");
    // The card is the positioning context, so the panel floats over the transcript
    // instead of pushing the page around.
    expect(popup.closest(".composer.relative")).toBeTruthy();
    const scroller = screen.getByTestId("skill-popup-scroll");
    expect(scroller.className).toContain("overflow-y-auto");
    expect(scroller.className).toContain("overscroll-contain");
    expect(scroller.contains(screen.getByTestId("slash-command-compact"))).toBe(true);
    // The Commands section header is a quiet sentence-case label, as every section label in
    // the composer's menus is (no tracked capitals: owner's rule, ui-skills).
    const header = screen.getByText("Commands");
    expect(header.className).not.toContain("uppercase");
    expect(header.closest(".menu-head")).toBeTruthy();
    // No skills in this fixture → no Skills header and no empty shell.
    expect(screen.queryByText("Skills")).toBeNull();
    expect(screen.queryByTestId("skill-popup-more")).toBeNull();
  });

  it("arrow keys move the highlight and scroll the selection into view", async () => {
    const scrollSpy = vi.fn();
    Element.prototype.scrollIntoView = scrollSpy;
    stubFetch();
    render(<Composer {...props()} />);
    fireEvent.change(box(), { target: { value: "/" } });
    await screen.findByTestId("skill-popup");
    expect(screen.getByTestId("slash-command-compact").getAttribute("aria-selected")).toBe(
      "true",
    );
    fireEvent.keyDown(box(), { key: "ArrowDown" });
    expect(screen.getByTestId("slash-command-goal").getAttribute("aria-selected")).toBe(
      "true",
    );
    expect(screen.getByTestId("slash-command-compact").getAttribute("aria-selected")).toBe(
      "false",
    );
    await waitFor(() => expect(scrollSpy).toHaveBeenCalledWith({ block: "nearest" }));
  });
});
