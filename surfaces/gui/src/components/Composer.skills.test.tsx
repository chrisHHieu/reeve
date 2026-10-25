import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Composer } from "./Composer";

// The composer's "/" menu: a leading slash opens it on the session's own skills
// and the built-in commands, typing narrows it, and a picked skill goes with the message as a field
// of its own, never as words in it.

const skill = (name: string, over: object = {}) => ({ name, description: name, scope: "global", enabled: true, ...over });

const MENU = {
  skills: [
    skill("weekly-report", { description: "Monday status report" }),
    skill("greet", { description: "says hello", scope: "project" }),
    // Turned off for this session: not offered.
    skill("muted-one", { enabled: false }),
    // `user-invocable: false` is the agent's alone (P1-7): not offered.
    skill("auto-only", { user_invocable: false }),
    // `disable-model-invocation` only takes it from the agent: the person still runs it here.
    skill("run-me", { model_invocable: false }),
  ],
};

/** /v1 answering the session's skills with `menu`, and a compaction that freed something. */
function serve(menu: unknown = MENU) {
  const sent: { url: string; method: string }[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      sent.push({ url, method: (init?.method || "GET").toUpperCase() });
      const body = url.includes("/skills") ? menu : url.includes("/compact") ? { ok: true, saved_tokens: 1234 } : {};
      return { ok: true, json: async () => body } as Response;
    }),
  );
  return sent;
}

const props = (extra: Partial<Parameters<typeof Composer>[0]> = {}) => ({
  mode: "interactive",
  model: "gpt-5.6-sol",
  running: false,
  connected: true,
  sessionId: "s1",
  onSend: vi.fn(),
  onInterrupt: vi.fn(),
  onModeChange: vi.fn(),
  onModelChange: vi.fn(),
  ...extra,
});

const box = () => screen.getByPlaceholderText(/Ask anything/) as HTMLTextAreaElement;
const type = (value: string) => fireEvent.change(box(), { target: { value } });
const press = (key: string) => fireEvent.keyDown(box(), { key });

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("Composer / skills popup", () => {
  it("opens on a leading '/' with the skills the session may run, and where each lives", async () => {
    serve();
    render(<Composer {...props()} />);
    type("/");
    await screen.findByTestId("skill-popup");
    expect(await screen.findByTestId("slash-skill-weekly-report")).toBeTruthy();
    expect(screen.getByTestId("slash-skill-greet").textContent).toContain("this project");
    expect(screen.getByTestId("slash-skill-run-me")).toBeTruthy();
    for (const absent of ["muted-one", "auto-only"]) expect(screen.queryByTestId(`slash-skill-${absent}`)).toBeNull();
  });

  it("narrows as you type", async () => {
    serve();
    render(<Composer {...props()} />);
    type("/");
    await screen.findByTestId("slash-skill-weekly-report");
    type("/wee");
    expect(screen.getByTestId("slash-skill-weekly-report")).toBeTruthy();
    expect(screen.queryByTestId("slash-skill-greet")).toBeNull();
  });

  it("stays shut for a slash inside the text, and in a composer with no session", async () => {
    serve();
    render(<Composer {...props()} />);
    type("rate 5/10 please");
    expect(screen.queryByTestId("skill-popup")).toBeNull();
    cleanup();
    render(<Composer {...props({ sessionId: undefined })} />);
    type("/");
    expect(screen.queryByTestId("skill-popup")).toBeNull();
  });

  it("closes on Escape", async () => {
    serve();
    render(<Composer {...props()} />);
    type("/gr");
    await screen.findByTestId("skill-popup");
    press("Escape");
    expect(screen.queryByTestId("skill-popup")).toBeNull();
  });

  it("puts /name in the text on a pick, and sends the skill as its own field with the rest", async () => {
    serve();
    const p = props();
    render(<Composer {...p} />);
    type("/gr");
    fireEvent.click(await screen.findByTestId("slash-skill-greet"));
    expect(box().value).toBe("/greet ");
    type("/greet say hi to the team");
    press("Enter");
    await waitFor(() => expect(p.onSend).toHaveBeenCalled());
    // The transcript is told the command it went with.
    expect(p.onSend).toHaveBeenCalledWith("say hi to the team", [], "greet", undefined, expect.objectContaining({ kind: "skill", name: "greet", args: "say hi to the team" }));
  });

  it("picks with Enter inside the menu, and sends a skill with no words on the next", async () => {
    serve();
    const p = props();
    render(<Composer {...p} />);
    type("/wee");
    await screen.findByTestId("slash-skill-weekly-report");
    press("Enter");
    expect(p.onSend).not.toHaveBeenCalled();
    expect(box().value).toBe("/weekly-report ");
    press("Enter");
    await waitFor(() => expect(p.onSend).toHaveBeenCalledWith("", [], "weekly-report", undefined, expect.objectContaining({ kind: "skill", name: "weekly-report" })));
  });

  it("drops the skill when its /name is edited out of the text", async () => {
    serve();
    const p = props();
    render(<Composer {...p} />);
    type("/gr");
    fireEvent.click(await screen.findByTestId("slash-skill-greet"));
    type("hello plain");
    press("Enter");
    await waitFor(() => expect(p.onSend).toHaveBeenCalledWith("hello plain", [], undefined));
  });
});

// `/compact` (alias `/summarize`): a built-in command RUNS on pick — nothing is
// inserted and nothing is sent as a message. It leads the menu, ahead of the skills.
describe("Composer / built-in compact command", () => {
  it("runs on pick instead of inserting text, and reports its phase upward", async () => {
    const calls = serve();
    const onCompactStatus = vi.fn();
    const p = props({ onCompactStatus });
    render(<Composer {...p} />);
    fireEvent.change(box(), { target: { value: "/comp" } });
    fireEvent.click(await screen.findByTestId("slash-command-compact"));
    await waitFor(() =>
      expect(
        calls.some((c) => c.url.includes("/compact") && c.method === "POST"),
      ).toBe(true),
    );
    expect((box() as HTMLTextAreaElement).value).toBe(""); // nothing inserted
    expect(p.onSend).not.toHaveBeenCalled(); // and nothing sent
    // Busy → idle with no problem: the chat shows the transient, the marker then lands
    // over the session socket — nothing renders on the prompt bar.
    await waitFor(() => expect(onCompactStatus).toHaveBeenCalledTimes(2));
    expect(onCompactStatus.mock.calls).toEqual([[true], [false]]);
    expect(screen.queryByTestId("command-notice")).toBeNull();
  });

  it("the /summarize alias and a bare Enter reach the same command", async () => {
    const calls = serve();
    render(<Composer {...props()} />);
    fireEvent.change(box(), { target: { value: "/summ" } });
    await screen.findByTestId("slash-command-compact");
    fireEvent.keyDown(box(), { key: "Enter" });
    await waitFor(() =>
      expect(calls.some((c) => c.url.includes("/compact") && c.method === "POST")).toBe(
        true,
      ),
    );
  });

  it("refuses mid-turn with a reason instead of firing into a rejection", async () => {
    const calls = serve();
    const onCompactStatus = vi.fn();
    render(<Composer {...props({ running: true, onCompactStatus })} />);
    fireEvent.change(box(), { target: { value: "/compact" } });
    fireEvent.click(await screen.findByTestId("slash-command-compact"));
    await waitFor(() =>
      expect(onCompactStatus).toHaveBeenCalledWith(
        false,
        "Wait for the current task to finish.",
        "info",
      ),
    );
    expect(calls.some((c) => c.url.includes("/compact"))).toBe(false);
  });
});

// The popup's built-in set, in menu order: commands run on pick, prompt templates insert,
// skills follow. This is the list the slash menu is matched against.
describe("Composer / the built-in command set", () => {
  it("leads the menu with the skills, every built-in command after them", async () => {
    // GUI UX review, Skills: nine commands filled the first screen and a person's own
    // skills sat below it — and a bare "/" + Enter ran /compact.
    serve();
    render(<Composer {...props()} />);
    fireEvent.change(box(), { target: { value: "/" } });
    await screen.findByTestId("skill-popup");
    // The two sections carry their muted headers.
    expect(screen.getByText("Commands")).toBeTruthy();
    expect(screen.getByText("Skills")).toBeTruthy();
    for (const name of [
      "compact",
      "goal",
      "undo",
      "redo",
      "open",
      "new",
      "export",
      "model",
      "fork",
      "init",
      "review",
      "mcp",
    ]) {
      expect(screen.getByTestId(`slash-command-${name}`)).toBeTruthy();
    }
    const options = screen.getAllByRole("option");
    const lastCommand = options.findIndex(
      (o) => o.getAttribute("data-testid") === "slash-command-mcp",
    );
    const firstSkill = options.findIndex((o) =>
      o.getAttribute("data-testid")?.startsWith("slash-skill-"),
    );
    const firstCommand = options.findIndex((o) =>
      o.getAttribute("data-testid")?.startsWith("slash-command-"),
    );
    const lastSkill = options.map((o) => o.getAttribute("data-testid") ?? "").filter((id) => id.startsWith("slash-skill-")).length - 1;
    expect(lastCommand).toBeGreaterThanOrEqual(0);
    expect(firstSkill).toBe(0);
    expect(firstCommand).toBeGreaterThan(lastSkill);
    // No pill repeats the section heading on a command row.
    expect(screen.getByTestId("slash-command-compact").textContent).not.toContain("command");
  });

  it("ranks the skills by how often the agent used them, and says where each comes from", async () => {
    serve({
      skills: [
        { name: "alpha", description: "a", scope: "global", enabled: true, uses: 0 },
        { name: "weekly-report", description: "w", scope: "global", enabled: true, uses: 12 },
        { name: "beta", description: "b", scope: "global", enabled: true, uses: 3 },
      ],
    });
    render(<Composer {...props()} />);
    fireEvent.change(box(), { target: { value: "/" } });
    await screen.findByTestId("slash-skill-weekly-report");
    const skills = screen
      .getAllByRole("option")
      .map((o) => o.getAttribute("data-testid") ?? "")
      .filter((id) => id.startsWith("slash-skill-"));
    expect(skills).toEqual(["slash-skill-weekly-report", "slash-skill-beta", "slash-skill-alpha"]);
    // The order is the count; the row says where the skill lives (it was a "used 12×" pill).
    expect(screen.getByTestId("slash-skill-weekly-report").textContent).not.toContain("used");
    expect(screen.getByTestId("slash-skill-alpha").textContent).toContain("yours");
  });

  it("shows a skill's line for people where it has one, and what Enter will do on the row it is on", async () => {
    serve({
      skills: [
        {
          name: "release-notes",
          description: "Use this skill whenever the person asks for release notes",
          summary: "Draft release notes from the git log",
          scope: "project",
          enabled: true,
          uses: 4,
        },
      ],
    });
    render(<Composer {...props()} />);
    fireEvent.change(box(), { target: { value: "/rel" } });
    const row = await screen.findByTestId("slash-skill-release-notes");
    expect(row.textContent).toContain("Draft release notes from the git log");
    expect(row.textContent).not.toContain("Use this skill whenever");
    expect(row.textContent).toContain("this project");
    expect(row.getAttribute("aria-selected")).toBe("true");
    expect(row.textContent).toContain("Enter adds it to this message");
    expect(row.querySelector(".menu-match")?.textContent).toBe("rel");
  });
});

// The popup's cap + sections (owner screenshot 2026-09-18): with many compat skills
// installed the uncapped, in-flow list grew past the window and could not be scrolled.
// Fixed by anchoring the panel above the composer, capping its height and giving it its
// own scroller; the Skills section previews the first 8 names until a query narrows.
describe("Composer / skills popup — capped, sectioned, scrollable", () => {
  const MANY = {
    skills: Array.from({ length: 12 }, (_, i) => ({
      name: `compat-${String(i + 1).padStart(2, "0")}`,
      description: `compat skill ${i + 1}`,
      scope: "compat",
      enabled: true,
    })),
  };

  it("anchors the panel above the card, caps it, and scrolls its own body", async () => {
    serve(MANY);
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
    // Commands render fully inside the scroller.
    expect(scroller.contains(screen.getByTestId("slash-command-compact"))).toBe(true);
    expect(scroller.contains(screen.getByTestId("slash-command-mcp"))).toBe(true);
  });

  it("previews the first 8 skills while the query is empty; typing shows all matches", async () => {
    serve(MANY);
    render(<Composer {...props()} />);
    fireEvent.change(box(), { target: { value: "/" } });
    await screen.findByTestId("slash-skill-compat-01");
    expect(screen.getByTestId("slash-skill-compat-08")).toBeTruthy();
    expect(screen.queryByTestId("slash-skill-compat-09")).toBeNull();
    const hint = screen.getByTestId("skill-popup-more");
    expect(hint.textContent).toContain("4 more skills");
    expect(hint.textContent).toContain("Keep typing");
    fireEvent.change(box(), { target: { value: "/compat" } });
    // A non-empty query shows every match; the hint is gone (the scroller handles height).
    expect(screen.getByTestId("slash-skill-compat-09")).toBeTruthy();
    expect(screen.getByTestId("slash-skill-compat-12")).toBeTruthy();
    expect(screen.queryByTestId("skill-popup-more")).toBeNull();
  });

  it("sorts the Skills section alphabetically", async () => {
    serve({
      skills: [
        { name: "zulu", description: "z", scope: "global", enabled: true },
        { name: "alpha", description: "a", scope: "global", enabled: true },
        { name: "mike", description: "m", scope: "global", enabled: true },
      ],
    });
    render(<Composer {...props()} />);
    fireEvent.change(box(), { target: { value: "/" } });
    await screen.findByTestId("slash-skill-alpha");
    const names = screen
      .getAllByRole("option")
      .filter((o) => o.getAttribute("data-testid")?.startsWith("slash-skill-"))
      .map((o) => o.getAttribute("data-testid"));
    expect(names).toEqual([
      "slash-skill-alpha",
      "slash-skill-mike",
      "slash-skill-zulu",
    ]);
  });

  it("arrows walk across sections, auto-scroll the selection, and Enter picks it", async () => {
    const scrollSpy = vi.fn();
    Element.prototype.scrollIntoView = scrollSpy;
    serve(MANY);
    render(<Composer {...props()} />);
    fireEvent.change(box(), { target: { value: "/" } });
    await screen.findByTestId("slash-skill-compat-01");
    // Opens on the first skill: Enter on a bare "/" inserts a skill, it never runs a command.
    expect(screen.getByTestId("slash-skill-compat-01").getAttribute("aria-selected")).toBe(
      "true",
    );
    // ArrowUp wraps from the Skills head to the Commands tail — across sections.
    fireEvent.keyDown(box(), { key: "ArrowUp" });
    const selected = screen.getAllByRole("option").find((o) => o.getAttribute("aria-selected") === "true");
    expect(selected?.getAttribute("data-testid")).toMatch(/^slash-command-/);
    await waitFor(() => expect(scrollSpy).toHaveBeenCalledWith({ block: "nearest" }));
    // Back down across the wrap, and Enter picks the skill (inserts, does not send).
    fireEvent.keyDown(box(), { key: "ArrowDown" });
    fireEvent.keyDown(box(), { key: "ArrowDown" });
    expect(screen.getByTestId("slash-skill-compat-02").getAttribute("aria-selected")).toBe("true");
    fireEvent.keyDown(box(), { key: "Enter" });
    expect((box() as HTMLTextAreaElement).value).toBe("/compat-02 ");
  });
});

describe("Composer — the doorway prefill", () => {
  it("keeps a prefill that arrives in the same render as a switch of session", async () => {
    // Clearing the draft for the new session has to happen before the prefill, or the prefill
    // is cleared with it, as it once was.
    serve();
    const { rerender } = render(<Composer {...props({ resetKey: "s1" })} />);
    rerender(<Composer {...props({ resetKey: "s2", prefill: { text: "Build a new skill for me: release procedure", nonce: 1 } })} />);
    await waitFor(() => expect(box().value).toBe("Build a new skill for me: release procedure"));
  });
});
