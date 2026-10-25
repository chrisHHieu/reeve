// @vitest-environment jsdom
// The composer's shell: the strip under the card where a new session is set up (and which
// leaves with the first message), and the send/Stop button at the end of the row. (The pickers' own behaviour lives in
// ModelPicker/ProviderPicker tests.)

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { Composer } from "./Composer";
// The stylesheet as text: the narrow-width collapse is a container query, and jsdom does
// not evaluate CSS — the shipped file is the only place the rule can be asserted.
import css from "../styles.css?raw";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function stubFetch() {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => ({ ok: true, json: async () => ({ models: [], default: "" }) }) as Response),
  );
}

const props = (over: Record<string, unknown> = {}) => ({
  mode: "interactive",
  model: "gpt-5.6-sol",
  models: ["gpt-5.6-sol"],
  running: false,
  connected: true,
  onSend: vi.fn(),
  onInterrupt: vi.fn(),
  onModeChange: vi.fn(),
  onModelChange: vi.fn(),
  ...over,
});

describe("Composer / shell readout", () => {
  it("a fresh session states the folder and the branch in the strip under the card, where they stay", () => {
    stubFetch();
    render(
      <Composer
        {...props({
          workspace: "/Users/me/wealthlog",
          workspaceName: "wealthlog",
          gitBranch: "main",
          hasMessages: false,
        })}
      />,
    );
    // No row of chips above the card, and nothing of it in the row of controls: where the
    // session works is a strip tucked under the card, the card's next sibling.
    expect(screen.queryByTestId("composer-chips")).toBeNull();
    const readout = screen.getByTestId("composer-session");
    const card = document.querySelector(".composer")!;
    expect(card.contains(readout)).toBe(false);
    expect(card.nextElementSibling).toBe(readout);
    expect(readout.className).toContain("composer-tray");
    expect(readout.getAttribute("data-state")).toBe("setup");
    expect(readout.textContent).toContain("wealthlog");
    expect(readout.textContent).toContain("main");
    // The machine it runs on is at the strip's end.
    expect(readout.lastElementChild?.getAttribute("data-testid")).toBe("composer-local");
    expect(readout.lastElementChild?.textContent).toBe("Local");
  });

  it("holds the session's own pickers in that place until the first message", () => {
    stubFetch();
    render(
      <Composer
        {...props({
          workspace: "/Users/me/wealthlog",
          workspaceName: "wealthlog",
          gitBranch: "main",
          hasMessages: false,
          setupSlot: <span data-testid="setup-slot-stub">setup</span>,
        })}
      />,
    );
    const readout = screen.getByTestId("composer-session");
    expect(readout.contains(screen.getByTestId("setup-slot-stub"))).toBe(true);
    expect(document.querySelector(".composer")!.nextElementSibling).toBe(readout);
    // The pickers carry the machine themselves: it is not stated a second time.
    expect(screen.queryByTestId("composer-local")).toBeNull();
    // The pickers' folder control IS the folder: it is not stated a second time beside them.
    expect(screen.queryByTestId("composer-folder")).toBeNull();
    expect(readout.textContent).toContain("main");
  });

  // The strip holds the pickers that set a session up; once the first message is in, the top
  // bar's crumb says where the conversation works (owner, 2026-10-13). It said it a second time
  // here, in a row at the foot of the page.
  it("leaves with the first message: it slides away, then it is gone", async () => {
    stubFetch();
    const base = {
      sessionId: "s1",
      workspace: "/Users/me/wealthlog",
      workspaceName: "wealthlog",
      gitBranch: "main",
      setupSlot: <span data-testid="setup-slot-stub">setup</span>,
    };
    const onSend = vi.fn();
    const view = render(<Composer {...props({ ...base, onSend, hasMessages: false })} />);
    expect(screen.getByTestId("composer-session").hasAttribute("data-leaving")).toBe(false);
    fireEvent.change(screen.getByRole("textbox", { name: "Message" }), { target: { value: "hello" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    expect(onSend).toHaveBeenCalledTimes(1);
    view.rerender(<Composer {...props({ ...base, onSend, hasMessages: true })} />);
    expect(screen.getByTestId("composer-session").hasAttribute("data-leaving")).toBe(true);
    await waitFor(() => expect(screen.queryByTestId("composer-session")).toBeNull());
  });

  it("does not slide away when the conversation's history arrives after it was opened", () => {
    stubFetch();
    const base = { sessionId: "s1", workspace: "/Users/me/wealthlog", workspaceName: "wealthlog", gitBranch: "main" };
    const view = render(<Composer {...props({ ...base, hasMessages: false })} />);
    // Nothing was sent from here: the messages are the history, read from the server.
    view.rerender(<Composer {...props({ ...base, hasMessages: true })} />);
    expect(screen.queryByTestId("composer-session")).toBeNull();
  });

  it("is not there in a conversation that already has messages, and does not pass through", () => {
    stubFetch();
    const base = { workspace: "/Users/me/wealthlog", workspaceName: "wealthlog", gitBranch: "main" };
    const view = render(<Composer {...props({ ...base, sessionId: "draft", hasMessages: false })} />);
    // Opening another conversation, one with history: no leaving motion, no strip.
    view.rerender(<Composer {...props({ ...base, sessionId: "older", hasMessages: true })} />);
    expect(screen.queryByTestId("composer-chips")).toBeNull();
    expect(screen.queryByTestId("composer-session")).toBeNull();
  });

  it("renders no readout at all without a folder or a branch", () => {
    stubFetch();
    render(<Composer {...props()} />);
    expect(screen.queryByTestId("composer-chips")).toBeNull();
    expect(screen.queryByTestId("composer-session")).toBeNull();
  });

  // A remote session's readout is `user@host:path`, never the
  // local folder/branch (which describe the wrong machine).
  const REMOTE = {
    id: "h1",
    name: "Build server",
    user: "ubuntu",
    host: "dev.example.com",
    port: 2222,
  };

  it("a fresh remote session states the host in the strip, and no local machine", () => {
    stubFetch();
    render(
      <Composer
        {...props({
          environment: REMOTE,
          remoteWorkspace: "/srv/app",
          hasMessages: false,
        })}
      />,
    );
    const chip = screen.getByTestId("composer-environment");
    expect(chip.textContent).toBe("ubuntu@dev.example.com:2222:/srv/app");
    expect(chip.getAttribute("title")).toBe("ubuntu@dev.example.com:2222:/srv/app");
    expect(screen.queryByTestId("composer-local")).toBeNull();
  });

  it("a remote conversation with messages has no strip either: the top bar names the machine", () => {
    stubFetch();
    render(
      <Composer
        {...props({
          workspace: "/Users/me/wealthlog",
          workspaceName: "wealthlog",
          gitBranch: "main",
          environment: REMOTE,
          remoteWorkspace: "/srv/app",
          hasMessages: true,
        })}
      />,
    );
    expect(screen.queryByTestId("composer-session")).toBeNull();
  });

  it("never appends a local workspace to the remote readout (the owner screenshot)", () => {
    stubFetch();
    render(
      <Composer
        {...props({
          // The exact leak: App's local workspace state handed to the remote readout.
          workspace: "C:\\Users\\DELI",
          workspaceName: "DELI",
          environment: REMOTE,
          remoteWorkspace: "C:\\Users\\DELI",
          hasMessages: false,
        })}
      />,
    );
    const readout = screen.getByTestId("composer-session");
    expect(readout.textContent).toBe("ubuntu@dev.example.com:2222");
    expect(readout.textContent).not.toContain("C:");
    expect(readout.textContent).not.toContain("DELI");
  });

  it("shows a Stop button and no working label while a turn runs", () => {
    stubFetch();
    render(<Composer {...props({ running: true })} />);
    expect(screen.queryByTestId("composer-status")).toBeNull();
    const stop = screen.getByTestId("composer-stop");
    expect(stop.getAttribute("aria-label")).toBe("Stop");
    expect(stop.className).toContain("rounded-full");
    // Stop is the last thing in the row of controls, where Send was: nothing moves.
    const footer = document.querySelector(".composer-footer")!;
    expect(footer.contains(stop)).toBe(true);
    expect(footer.lastElementChild?.contains(stop)).toBe(true);
    expect(document.querySelector(".composer-input")?.contains(stop)).toBe(false);
    expect(screen.queryByLabelText("Send")).toBeNull();
  });

  it("the idle slot is the circular Send button at the end of the row of controls", () => {
    stubFetch();
    render(<Composer {...props()} />);
    const send = screen.getByLabelText("Send");
    expect(send.className).toContain("rounded-full");
    const footer = document.querySelector(".composer-footer")!;
    expect(footer.lastElementChild?.contains(send)).toBe(true);
    // The text has its zone to itself, edge to edge.
    expect(document.querySelector(".composer-input")?.querySelectorAll("button")).toHaveLength(0);
    expect(screen.queryByTestId("composer-status")).toBeNull();
    expect(screen.queryByTestId("composer-stop")).toBeNull();
  });

  it("orders the row: attach, permissions, then the model that says its thinking level, then Send", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({
        ok: true,
        json: async () => ({
          models: [
            {
              id: "gpt-5.6-sol",
              name: "GPT-5.6 Sol",
              provider: "openai",
              provider_title: "OpenAI",
              reasoning_levels: ["low", "high"],
              reasoning_default: "low",
            },
          ],
          default: "gpt-5.6-sol",
        }),
      }) as Response),
    );
    render(
      <Composer
        {...props({
          workspace: "/Users/me/wealthlog",
        })}
      />,
    );
    const footer = document.querySelector(".composer-footer");
    expect(footer).toBeTruthy();
    // No agent chip in the footer (owner call): the picker is the `/agent` doorway, and
    // the topbar states the active agent.
    expect(screen.queryByTestId("agent-picker-chip")).toBeNull();
    const order = [
      screen.getByRole("button", { name: "Attach" }),
      screen.getByRole("button", { name: "Mode" }),
      screen.getByTestId("model-picker-trigger"),
      screen.getByLabelText("Send"),
    ];
    for (let i = 1; i < order.length; i++) {
      expect(
        order[i - 1].compareDocumentPosition(order[i]) & Node.DOCUMENT_POSITION_FOLLOWING,
      ).toBeTruthy();
    }
    // The level rides the model's own trigger once the catalogue reports the model's levels…
    expect((await screen.findByTestId("model-picker-level")).textContent).toBe("· Low");
    // …and the provider and the level are not controls of the row any more: three of them
    // side by side were cut to "Vertex AI …" and "H…" in a rail-open column.
    expect(footer!.querySelector('[data-testid="provider-picker-trigger"]')).toBeNull();
    expect(footer!.querySelector(".pill-sep")).toBeNull();
    expect(footer!.querySelectorAll(":scope > .mp, :scope > .pp, :scope > .dd")).toHaveLength(1);
  });

  it("shows a legacy session mode under the new preset name, and a pick still wins", () => {
    stubFetch();
    const onModeChange = vi.fn();
    render(<Composer {...props({ mode: "plan", workspace: "/repo", onModeChange })} />);
    // The persisted mode is the prop the App hydrated from the session record: the chip
    // names it without any click, and a legacy wire value reads as its preset.
    const trigger = screen.getByRole("button", { name: "Mode" });
    expect(trigger.textContent).toContain("Read only");
    fireEvent.click(trigger);
    fireEvent.click(screen.getByText("Full access"));
    expect(onModeChange).toHaveBeenCalledWith("danger-full-access");
  });

  it("offers exactly the three presets plus the Plan toggle", () => {
    stubFetch();
    const onPlanChange = vi.fn();
    render(
      <Composer
        {...props({ mode: "workspace-write", workspace: "/repo", onPlanChange })}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Mode" }));
    // Scope to the menu: the trigger carries the current mode's word too.
    const menu = screen.getByTestId("mode-menu");
    expect(within(menu).getByText("Read only")).toBeTruthy();
    expect(within(menu).getByText("Workspace write")).toBeTruthy();
    expect(within(menu).getByText("Full access")).toBeTruthy();
    // The retired picker entries are gone; Plan is a toggle row, not an option.
    expect(within(menu).queryByText("Ask for approval")).toBeNull();
    expect(within(menu).queryByText("Bypass approvals")).toBeNull();
    expect(within(menu).getByTestId("plan-toggle-row")).toBeTruthy();
    fireEvent.click(within(menu).getByRole("switch"));
    expect(onPlanChange).toHaveBeenCalledWith(true);
  });

  it("narrow widths give up the least useful things first; the send circle cannot collapse", () => {
    // First the thinking level leaves the model's trigger and the machine keeps only its
    // glyph; then the branch leaves the strip. The folder stays.
    expect(css).toMatch(/@container\s*\(max-width:\s*560px\)\s*\{[^@]*\.composer \.mp-level\s*\{\s*display:\s*none/);
    expect(css).toMatch(/@container\s*\(max-width:\s*560px\)\s*\{[^@]*\.composer-tray-part\.env \.composer-tray-label[^{]*\{\s*display:\s*none/);
    expect(css).toMatch(/@container\s*\(max-width:\s*420px\)\s*\{[^@]*\.composer-tray \.branch-pop[^{]*\{\s*display:\s*none/);
    // A placeholder never wraps and pushes the row down.
    expect(css).toMatch(/\.composer textarea::placeholder\s*\{[^}]*white-space:\s*nowrap/);
    // Send is in the row and cannot shrink out of it.
    expect(css).toMatch(/\.composer-send-slot\s*\{[^}]*flex:\s*none/);
  });

  it("the placeholder teaches / and @ in one line", () => {
    stubFetch();
    render(<Composer {...props()} />);
    const box = screen.getByRole("textbox");
    expect(box.getAttribute("placeholder")).toContain("Ask anything");
    expect(box.getAttribute("placeholder")).toContain("/ for commands");
    // "@" finds the session's files (and its agents): the line promises what is there.
    expect(box.getAttribute("placeholder")).toBe("Ask anything, / for commands, @ for files");
  });
});
