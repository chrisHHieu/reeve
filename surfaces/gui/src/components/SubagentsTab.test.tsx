import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { SubagentsTab } from "./SubagentsTab";

// Settings ▸ Subagents — the mockup's list (Installed over Built-in), the form's
// validation and preset → tool-grid behaviour, the save payload the API receives, and the
// inline delete confirmation.

type Call = { url: string; method: string; body: any };

function stubFetch(routes: { match: string; method?: string; json: any }[]) {
  const calls: Call[] = [];
  const fn = vi.fn(async (url: string, init?: RequestInit) => {
    const method = (init?.method || "GET").toUpperCase();
    calls.push({
      url,
      method,
      body: init?.body ? JSON.parse(String(init.body)) : undefined,
    });
    for (const r of routes) {
      if (url.includes(r.match) && (!r.method || r.method === method)) {
        return { ok: true, json: async () => r.json } as Response;
      }
    }
    return { ok: true, json: async () => ({}) } as Response;
  });
  vi.stubGlobal("fetch", fn);
  return calls;
}

const GENERAL = {
  name: "general",
  description: "General-purpose agent for researching complex questions.",
  mode: "subagent",
  native: true,
  source: "builtin",
  file: null,
  overrides: false,
  prompt: "",
  tools: { preset: "all", allow: [], deny: [] },
  inject_agents: true,
  tool_count: null,
};

const EXPLORE = {
  ...GENERAL,
  name: "explore",
  description: "Fast agent specialized for exploring codebases.",
  prompt: "You are a file search specialist.",
  tools: { preset: "custom", allow: ["read", "grep", "list", "shell-read", "shell", "web"], deny: [] },
  tool_count: 10,
};

const REVIEWER = {
  ...GENERAL,
  name: "code-reviewer",
  description: "Reviews a diff and reports blocking issues as path:line.",
  native: false,
  source: "user",
  file: "/home/u/.config/Reeve/agents/code-reviewer.md",
  prompt: "You are a code reviewer. You do not fix.",
  color: "#2f9e5f",
  tools: { preset: "custom", allow: ["read", "grep", "list", "shell-read"], deny: [] },
  inject_agents: false,
  tool_count: 7,
};

const DOCS_WRITER = {
  ...GENERAL,
  name: "docs-writer",
  description: "Turns a change into a short, factual doc update.",
  native: false,
  source: "project",
  file: "/ws/reeve/.reeve/agents/docs-writer.md",
  prompt: "You write docs.",
  tools: { preset: "custom", allow: ["read", "write"], deny: [] },
  inject_agents: true,
  tool_count: 5,
};

const LIST = { agents: [GENERAL, EXPLORE, REVIEWER, DOCS_WRITER] };

const SETTINGS = {
  models: ["deepseek:chat", "anthropic:claude-sonnet"],
  model_labels: { "deepseek:chat": "DeepSeek V4.1 Flash" },
};

// The grouped model menu mirrors the composer picker's catalogue (`/v1/models/catalog`):
// Z.ai (with a Vision badge on one row) and Deepseek, each collapsed behind its submenu
// row until expanded — only a pinned model's provider opens expanded.
const CATALOG = {
  models: [
    { id: "zai:glm-5.3", name: "GLM-5.3", provider: "zai", provider_title: "Z.ai" },
    {
      id: "zai:glm-5.3-flash",
      name: "GLM-5.3-Flash",
      provider: "zai",
      provider_title: "Z.ai",
      vision: true,
    },
    {
      id: "deepseek:chat",
      name: "DeepSeek V4.1 Flash",
      provider: "deepseek",
      provider_title: "Deepseek",
    },
  ],
  default: "",
};

const WORKSPACES = {
  workspaces: [
    { path: "/ws/reeve", name: "reeve", exists: true },
    { path: "/ws/haha", name: "haha", exists: true },
  ],
};

const routes = (extra: { match: string; method?: string; json: any }[] = []) => [
  { match: "/v1/subagents", method: "GET", json: LIST },
  { match: "/v1/models/catalog", method: "GET", json: CATALOG },
  { match: "/v1/settings", method: "GET", json: SETTINGS },
  { match: "/v1/workspaces/recent", method: "GET", json: WORKSPACES },
  ...extra,
];

const renderTab = async () => {
  const calls = stubFetch(routes());
  const view = render(<SubagentsTab workspace="/ws" />);
  await screen.findByText("code-reviewer");
  return { ...view, calls };
};

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("SubagentsTab — list", () => {
  it("renders installed and built-in rows with their badges and actions", async () => {
    await renderTab();

    // Installed: scope + tool-count badges, duplicate/delete, a model menu.
    expect(screen.getByText("code-reviewer")).toBeTruthy();
    expect(screen.getByText("7 tools")).toBeTruthy();
    const reviewerRow = screen.getByText("code-reviewer").closest(".group") as HTMLElement;
    expect(within(reviewerRow).getByText("User")).toBeTruthy();
    expect(within(reviewerRow).queryByText("Project")).toBeNull();
    expect(screen.getByLabelText("Duplicate code-reviewer")).toBeTruthy();
    expect(screen.getByLabelText("Delete code-reviewer")).toBeTruthy();
    expect(screen.getByLabelText("code-reviewer model")).toBeTruthy();

    // Built-in: listed read-only with its resolved values and a model menu, no Override
    // affordance anywhere and no delete.
    expect(screen.getByText("explore")).toBeTruthy();
    expect(screen.getByText("10 tools")).toBeTruthy();
    expect(screen.getByLabelText("View general")).toBeTruthy();
    expect(screen.getByLabelText("general model")).toBeTruthy();
    expect(screen.getByLabelText("explore model")).toBeTruthy();
    expect(screen.queryAllByText("Override")).toHaveLength(0);
    expect(screen.queryByText("Override built-in")).toBeNull();
    expect(screen.queryByText(/Saving writes an override/)).toBeNull();
    expect(screen.queryByLabelText("Delete explore")).toBeNull();
  });

  it("opens the read-only detail from a built-in row and the edit form from an installed one", async () => {
    await renderTab();

    fireEvent.click(screen.getByLabelText("View general"));
    expect(screen.getByRole("heading", { name: /general/ })).toBeTruthy();
    expect(screen.getByText("Built-in")).toBeTruthy();
    expect(
      screen.getByText("General-purpose agent for researching complex questions."),
    ).toBeTruthy();
    expect(screen.getByText("Allowed tools")).toBeTruthy();
    expect(screen.getByText("System prompt")).toBeTruthy();
    // Read-only: no name/tools/prompt inputs, no delete, no save.
    expect(screen.queryByLabelText("Name")).toBeNull();
    expect(screen.queryByLabelText("System prompt")).toBeNull();
    expect(screen.queryByRole("button", { name: /Delete/ })).toBeNull();
    expect(screen.queryByRole("button", { name: "Save" })).toBeNull();

    // Back to the list, then an installed row still opens the editable form.
    fireEvent.click(screen.getByRole("button", { name: "Subagents" }));
    fireEvent.click(screen.getByLabelText("Edit code-reviewer"));
    expect(screen.getByRole("heading", { name: "Edit subagent" })).toBeTruthy();
    expect((screen.getByLabelText("Name") as HTMLInputElement).disabled).toBe(true);
    expect(screen.getByRole("button", { name: /Delete/ })).toBeTruthy();
  });

  it("shows the built-in's system prompt only when the collapsible section opens", async () => {
    await renderTab();
    fireEvent.click(screen.getByLabelText("View explore"));

    expect(screen.queryByText("You are a file search specialist.")).toBeNull();
    const toggle = screen.getByRole("button", { name: /System prompt/ });
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    fireEvent.click(toggle);
    expect(toggle.getAttribute("aria-expanded")).toBe("true");
    expect(screen.getByText("You are a file search specialist.")).toBeTruthy();
  });

  it("confirms a delete inline before calling the API", async () => {
    const calls = stubFetch(
      routes([{ match: "/v1/subagents/code-reviewer", method: "DELETE", json: { ok: true } }]),
    );
    render(<SubagentsTab workspace="/ws" />);
    await screen.findByText("code-reviewer");

    fireEvent.click(screen.getByLabelText("Delete code-reviewer"));
    expect(calls.some((c) => c.method === "DELETE")).toBe(false);
    fireEvent.click(screen.getByText("Confirm delete"));
    await waitFor(() => expect(calls.some((c) => c.method === "DELETE")).toBe(true));
    const del = calls.find((c) => c.method === "DELETE")!;
    expect(del.url).toContain("/v1/subagents/code-reviewer");
    expect(del.url).toContain("scope=user");
    // User scope is global: no workspace parameter.
    expect(del.url).not.toContain("workspace=");
  });

  it("deletes a project row against the chosen workspace", async () => {
    const calls = stubFetch(
      routes([{ match: "/v1/subagents/docs-writer", method: "DELETE", json: { ok: true } }]),
    );
    render(<SubagentsTab workspace="/ws" />);
    await screen.findByText("docs-writer");

    fireEvent.click(screen.getByRole("button", { name: "User" }));
    fireEvent.click(screen.getByRole("menuitemradio", { name: "reeve" }));
    await waitFor(() =>
      calls.some(
        (c) =>
          c.method === "GET" &&
          c.url.includes("/v1/subagents") &&
          c.url.includes("workspace=%2Fws%2Freeve"),
      ),
    );

    fireEvent.click(screen.getByLabelText("Delete docs-writer"));
    fireEvent.click(screen.getByText("Confirm delete"));
    await waitFor(() => expect(calls.some((c) => c.method === "DELETE")).toBe(true));
    const del = calls.find((c) => c.method === "DELETE")!;
    expect(del.url).toContain("/v1/subagents/docs-writer");
    expect(del.url).toContain("scope=project");
    expect(del.url).toContain("workspace=%2Fws%2Freeve");
  });
});

describe("SubagentsTab — scope picker", () => {
  it("opens the menu with User checked and the Workspaces group", async () => {
    await renderTab();

    fireEvent.click(screen.getByRole("button", { name: "User" }));

    expect(screen.getByRole("menu", { name: "Scope" })).toBeTruthy();
    const user = screen.getByRole("menuitemradio", { name: "User" });
    expect(user.getAttribute("aria-checked")).toBe("true");
    // Monitor glyph + the check on the selected row.
    expect(user.querySelectorAll("svg")).toHaveLength(2);
    expect(screen.getByText("Workspaces")).toBeTruthy();
    // The open session's folder joins the app's recents.
    expect(screen.getByRole("menuitemradio", { name: "reeve" })).toBeTruthy();
    expect(screen.getByRole("menuitemradio", { name: "haha" })).toBeTruthy();
    expect(screen.getByRole("menuitemradio", { name: "ws" })).toBeTruthy();
    expect(
      screen.getByRole("menuitemradio", { name: "reeve" }).getAttribute("aria-checked"),
    ).toBe("false");
  });

  it("Escape closes the menu and returns focus to the trigger", async () => {
    await renderTab();
    const trigger = screen.getByRole("button", { name: "User" });

    fireEvent.click(trigger);
    expect(screen.getByRole("menu", { name: "Scope" })).toBeTruthy();

    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.queryByRole("menu")).toBeNull();
    expect(document.activeElement).toBe(trigger);
  });

  it("an outside click closes the menu", async () => {
    const { container } = await renderTab();
    fireEvent.click(screen.getByRole("button", { name: "User" }));
    expect(screen.getByRole("menu", { name: "Scope" })).toBeTruthy();

    fireEvent.click(container.querySelector(".fixed.inset-0")!);
    expect(screen.queryByRole("menu")).toBeNull();
  });

  it("choosing a workspace refetches with the workspace param and checks the item", async () => {
    const { calls } = await renderTab();

    fireEvent.click(screen.getByRole("button", { name: "User" }));
    fireEvent.click(screen.getByRole("menuitemradio", { name: "haha" }));

    await waitFor(() =>
      calls.some(
        (c) =>
          c.method === "GET" &&
          c.url.includes("/v1/subagents") &&
          c.url.includes("workspace=%2Fws%2Fhaha"),
      ),
    );
    expect(screen.queryByRole("menu")).toBeNull();
    // The pill now shows the workspace.
    expect(screen.getByRole("button", { name: "haha" })).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "haha" }));
    const item = screen.getByRole("menuitemradio", { name: "haha" });
    expect(item.getAttribute("aria-checked")).toBe("true");
    // Folder glyph + the check.
    expect(item.querySelectorAll("svg")).toHaveLength(2);
    expect(
      screen.getByRole("menuitemradio", { name: "User" }).getAttribute("aria-checked"),
    ).toBe("false");
  });
});

describe("SubagentsTab — form", () => {
  const openNew = async () => {
    await renderTab();
    fireEvent.click(screen.getByRole("button", { name: "New subagent" }));
    expect(screen.getByRole("heading", { name: "New subagent" })).toBeTruthy();
  };

  it("blocks a bad name inline and enables Save only when valid", async () => {
    await openNew();

    const name = screen.getByLabelText("Name") as HTMLInputElement;
    fireEvent.change(name, { target: { value: "Bad Name" } });
    expect(screen.getByText(/Lowercase letters, digits, and dashes/)).toBeTruthy();
    expect((screen.getByRole("button", { name: "Save" }) as HTMLButtonElement).disabled).toBe(
      true,
    );

    fireEvent.change(name, { target: { value: "good-name" } });
    expect(screen.queryByText(/Lowercase letters, digits, and dashes/)).toBeNull();
    // Still missing description + prompt.
    expect((screen.getByRole("button", { name: "Save" }) as HTMLButtonElement).disabled).toBe(
      true,
    );
    fireEvent.change(screen.getByLabelText("Description"), {
      target: { value: "Does a thing" },
    });
    fireEvent.change(screen.getByLabelText("System prompt"), {
      target: { value: "You do the thing." },
    });
    expect((screen.getByRole("button", { name: "Save" }) as HTMLButtonElement).disabled).toBe(
      false,
    );
  });

  it("reveals the tool checkbox grid only for the custom preset, and toggles the boxes", async () => {
    await openNew();
    expect(screen.queryByRole("checkbox", { name: "Read" })).toBeNull();

    fireEvent.click(screen.getByLabelText("Allowed tools"));
    fireEvent.click(screen.getByRole("option", { name: "Custom allowed tools" }));
    expect(
      screen.getByText("Control which tools this subagent can use. Everything else is denied."),
    ).toBeTruthy();
    expect(screen.getAllByRole("checkbox")).toHaveLength(11);
    const box = screen.getByRole("checkbox", { name: "Read" }) as HTMLInputElement;
    expect(box.checked).toBe(false);
    fireEvent.click(box);
    expect(box.checked).toBe(true);
    fireEvent.click(box);
    expect(box.checked).toBe(false);

    // Only the mutating groups carry the danger dot.
    const cell = (name: string) =>
      screen.getByRole("checkbox", { name }).closest("label") as HTMLElement;
    expect(cell("Write").querySelector("[title]")).toBeTruthy();
    expect(cell("Edit").querySelector("[title]")).toBeTruthy();
    expect(cell("Shell").querySelector("[title]")).toBeTruthy();
    expect(cell("Read").querySelector("[title]")).toBeNull();
  });

  it("sends the write body the API expects, then returns to the list", async () => {
    const calls = stubFetch(
      routes([{ match: "/v1/subagents/my-agent", method: "PUT", json: { ok: true } }]),
    );
    render(<SubagentsTab workspace="/ws" />);
    await screen.findByText("code-reviewer");
    fireEvent.click(screen.getByRole("button", { name: "New subagent" }));

    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "my-agent" } });
    fireEvent.change(screen.getByLabelText("Description"), {
      target: { value: "Reviews a diff" },
    });
    fireEvent.change(screen.getByLabelText("System prompt"), {
      target: { value: "You review diffs." },
    });
    // The Model field is the app's grouped picker over `/v1/models/catalog`, not a native
    // select over the settings list: pick DeepSeek V4.1 Flash from its provider's group.
    fireEvent.click(screen.getByLabelText("Model"));
    const modelMenu = screen.getByRole("menu", { name: "subagent model menu" });
    expect(within(modelMenu).getByText("Z.ai")).toBeTruthy();
    fireEvent.click(within(modelMenu).getByRole("menuitem", { name: "Deepseek" }));
    fireEvent.click(
      within(modelMenu).getByRole("menuitemradio", { name: /DeepSeek V4\.1 Flash/ }),
    );
    fireEvent.click(screen.getByLabelText("Allowed tools"));
    fireEvent.click(screen.getByRole("option", { name: "Custom allowed tools" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "Read" }));
    // The AGENTS.md toggle is on by default; turn it off.
    fireEvent.click(screen.getByLabelText("Inject AGENTS.md"));

    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(calls.some((c) => c.method === "PUT")).toBe(true));

    const put = calls.find((c) => c.method === "PUT")!;
    expect(put.url).toContain("/v1/subagents/my-agent");
    // The default scope is User (global) — no workspace parameter.
    expect(put.url).not.toContain("workspace=");
    expect(put.body).toEqual({
      scope: "user",
      description: "Reviews a diff",
      color: "",
      model: "deepseek:chat",
      tools: { preset: "custom", allow: ["read"], deny: [] },
      prompt: "You review diffs.",
      inject_agents: false,
    });
    await waitFor(() =>
      expect(screen.queryByRole("heading", { name: "New subagent" })).toBeNull(),
    );
    expect(
      calls.filter((c) => c.method === "GET" && c.url.includes("/v1/subagents")).length,
    ).toBeGreaterThan(1);
  });

  it("groups every catalogue provider in the form's model menu, checks the value and clears with Inherit", async () => {
    const calls = stubFetch(
      routes([{ match: "/v1/subagents/my-agent", method: "PUT", json: { ok: true } }]),
    );
    render(<SubagentsTab workspace="/ws" />);
    await screen.findByText("code-reviewer");
    fireEvent.click(screen.getByRole("button", { name: "New subagent" }));

    fireEvent.click(screen.getByLabelText("Model"));
    const menu = screen.getByRole("menu", { name: "subagent model menu" });
    // Both catalogue providers start collapsed (the field is unpinned) and Inherit
    // default is the checked row.
    expect(within(menu).getByRole("menuitem", { name: "Z.ai" })).toBeTruthy();
    expect(within(menu).getByRole("menuitem", { name: "Deepseek" })).toBeTruthy();
    expect(within(menu).queryByRole("menuitemradio", { name: /GLM-5\.3$/ })).toBeNull();
    expect(
      within(menu)
        .getByRole("menuitemradio", { name: /Inherit default/ })
        .getAttribute("aria-checked"),
    ).toBe("true");

    // Expanding a provider reveals its models.
    fireEvent.click(within(menu).getByRole("menuitem", { name: "Z.ai" }));
    expect(within(menu).getByRole("menuitemradio", { name: /GLM-5\.3$/ })).toBeTruthy();
    expect(
      within(menu).getByRole("menuitemradio", { name: /GLM-5\.3-Flash/ }),
    ).toBeTruthy();

    // Picking a model shows it on the trigger and marks it current on reopen.
    fireEvent.click(within(menu).getByRole("menuitemradio", { name: /GLM-5\.3$/ }));
    expect(screen.getByLabelText("Model").textContent).toContain("GLM 5.3");
    fireEvent.click(screen.getByLabelText("Model"));
    const reopened = screen.getByRole("menu", { name: "subagent model menu" });
    expect(
      within(reopened)
        .getByRole("menuitemradio", { name: /GLM-5\.3$/ })
        .getAttribute("aria-checked"),
    ).toBe("true");
    // The value's provider opens expanded; every other stays collapsed.
    expect(within(reopened).getByRole("menuitemradio", { name: /GLM-5\.3-Flash/ })).toBeTruthy();
    expect(within(reopened).getByRole("menuitem", { name: "Deepseek" })).toBeTruthy();

    // Inherit default clears the field, and the save sends `model: null`.
    fireEvent.click(
      within(reopened).getByRole("menuitemradio", { name: /Inherit default/ }),
    );
    expect(screen.getByLabelText("Model").textContent).toContain("Inherit default");
    fireEvent.change(screen.getByLabelText("Name"), {
      target: { value: "my-agent" },
    });
    fireEvent.change(screen.getByLabelText("Description"), {
      target: { value: "Does a thing" },
    });
    fireEvent.change(screen.getByLabelText("System prompt"), {
      target: { value: "You do the thing." },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(calls.some((c) => c.method === "PUT")).toBe(true));
    expect(calls.find((c) => c.method === "PUT")!.body.model).toBeNull();
  });

  it("opens the model menu from the keyboard, walks it with arrows and restores focus on Escape", async () => {
    await openNew();
    const trigger = screen.getByLabelText("Model");
    trigger.focus();

    fireEvent.keyDown(trigger, { key: "ArrowDown" });
    await screen.findByRole("menu", { name: "subagent model menu" });
    // No pin: focus enters the first provider's submenu row, then ArrowDown walks on and
    // ArrowUp walks back.
    expect(document.activeElement?.textContent).toContain("Z.ai");
    fireEvent.keyDown(document.activeElement!, { key: "ArrowDown" });
    expect(document.activeElement?.textContent).toContain("Deepseek");
    fireEvent.keyDown(document.activeElement!, { key: "ArrowUp" });
    expect(document.activeElement?.textContent).toContain("Z.ai");

    // Activating the provider (Enter/click on the focused button) expands it and roving
    // focus lands on its first model row.
    fireEvent.click(document.activeElement!);
    expect(document.activeElement?.textContent).toContain("GLM-5.3");

    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.queryByRole("menu")).toBeNull();
    expect(document.activeElement).toBe(trigger);
  });

  it("saves a project-scoped subagent into the workspace chosen in the form", async () => {
    const calls = stubFetch(
      routes([{ match: "/v1/subagents/project-agent", method: "PUT", json: { ok: true } }]),
    );
    render(<SubagentsTab workspace="/ws" />);
    await screen.findByText("code-reviewer");
    fireEvent.click(screen.getByRole("button", { name: "New subagent" }));

    // The form header carries the same picker; choosing a workspace switches to project scope.
    fireEvent.click(screen.getByRole("button", { name: "User" }));
    fireEvent.click(screen.getByRole("menuitemradio", { name: "haha" }));
    expect((screen.getByRole("button", { name: "haha" }) as HTMLButtonElement).disabled).toBe(
      false,
    );

    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "project-agent" } });
    fireEvent.change(screen.getByLabelText("Description"), {
      target: { value: "Project scoped" },
    });
    fireEvent.change(screen.getByLabelText("System prompt"), {
      target: { value: "You do project things." },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(calls.some((c) => c.method === "PUT")).toBe(true));

    const put = calls.find((c) => c.method === "PUT")!;
    expect(put.url).toContain("workspace=%2Fws%2Fhaha");
    expect(put.body.scope).toBe("project");
  });

  it("fixes the scope picker after saving", async () => {
    await renderTab();
    fireEvent.click(screen.getByLabelText("Edit code-reviewer"));
    expect((screen.getByRole("button", { name: "User" }) as HTMLButtonElement).disabled).toBe(
      true,
    );
  });

  it("surfaces a backend error next to the form", async () => {
    stubFetch(
      routes([
        {
          match: "/v1/subagents/my-agent",
          method: "PUT",
          json: { ok: false, error: "a description is required" },
        },
      ]),
    );
    render(<SubagentsTab workspace="/ws" />);
    await screen.findByText("code-reviewer");
    fireEvent.click(screen.getByRole("button", { name: "New subagent" }));
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "my-agent" } });
    fireEvent.change(screen.getByLabelText("Description"), {
      target: { value: "Reviews a diff" },
    });
    fireEvent.change(screen.getByLabelText("System prompt"), {
      target: { value: "You review diffs." },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    expect(await screen.findByRole("alert")).toBeTruthy();
    expect(screen.getByRole("alert").textContent).toContain("a description is required");
    expect(screen.getByRole("heading", { name: "New subagent" })).toBeTruthy();
  });
});

describe("SubagentsTab — built-in model", () => {
  it("renders the grouped provider menu, a Vision badge, a provider submenu and Inherit pinned", async () => {
    await renderTab();
    fireEvent.click(screen.getByLabelText("general model"));

    const menu = screen.getByRole("menu", { name: "general model menu" });
    // No pin: every provider is a collapsed submenu row…
    expect(within(menu).getByRole("menuitem", { name: "Z.ai" })).toBeTruthy();
    expect(within(menu).getByRole("menuitem", { name: "Deepseek" })).toBeTruthy();
    expect(within(menu).queryByRole("menuitemradio", { name: /GLM-5\.3$/ })).toBeNull();
    // …expanding one shows its models under the provider title plus a count badge…
    fireEvent.click(within(menu).getByRole("menuitem", { name: "Z.ai" }));
    expect(within(menu).getByRole("menuitemradio", { name: /GLM-5\.3$/ })).toBeTruthy();
    const vision = within(menu).getByRole("menuitemradio", { name: /GLM-5\.3-Flash/ });
    expect(within(vision).getByText("Vision")).toBeTruthy();
    // …and Inherit default is pinned below the scroller, checked for an unpinned row.
    const inherit = within(menu).getByRole("menuitemradio", { name: /Inherit default/ });
    expect(inherit.getAttribute("aria-checked")).toBe("true");
    expect(menu.lastElementChild?.contains(inherit)).toBe(true);
    expect(menu.querySelector(".overflow-y-auto")?.contains(inherit)).toBe(false);
  });

  it("expands another provider's submenu", async () => {
    await renderTab();
    fireEvent.click(screen.getByLabelText("general model"));
    fireEvent.click(screen.getByRole("menuitem", { name: "Deepseek" }));
    expect(
      screen.getByRole("menuitemradio", { name: /DeepSeek V4\.1 Flash/ }),
    ).toBeTruthy();
  });

  it("calls the built-in model route and clears the pin with Inherit default", async () => {
    const calls = stubFetch(
      routes([
        {
          match: "/v1/subagents/general/model",
          method: "PUT",
          json: { ok: true, agent: { ...GENERAL, model: "zai:glm-5.3" } },
        },
      ]),
    );
    render(<SubagentsTab workspace="/ws" />);
    await screen.findByText("general");

    fireEvent.click(screen.getByLabelText("general model"));
    fireEvent.click(screen.getByRole("menuitem", { name: "Z.ai" }));
    fireEvent.click(screen.getByRole("menuitemradio", { name: /GLM-5\.3$/ }));
    await waitFor(() => expect(calls.some((c) => c.method === "PUT")).toBe(true));
    const put = calls.find((c) => c.method === "PUT")!;
    expect(put.url).toContain("/v1/subagents/general/model");
    expect(put.body).toEqual({ model: "zai:glm-5.3" });
    // The list re-reads after the route answers.
    await waitFor(() =>
      expect(
        calls.filter((c) => c.method === "GET" && c.url.includes("/v1/subagents")).length,
      ).toBeGreaterThan(1),
    );

    fireEvent.click(screen.getByLabelText("general model"));
    fireEvent.click(screen.getByRole("menuitemradio", { name: /Inherit default/ }));
    await waitFor(() => expect(calls.filter((c) => c.method === "PUT").length).toBe(2));
    expect(calls.filter((c) => c.method === "PUT")[1].body).toEqual({ model: null });
  });

  it("surfaces a model route error inline", async () => {
    stubFetch(
      routes([
        {
          match: "/v1/subagents/general/model",
          method: "PUT",
          json: { ok: false, error: "not a built-in" },
        },
      ]),
    );
    render(<SubagentsTab workspace="/ws" />);
    await screen.findByText("general");
    fireEvent.click(screen.getByLabelText("general model"));
    fireEvent.click(screen.getByRole("menuitem", { name: "Z.ai" }));
    fireEvent.click(screen.getByRole("menuitemradio", { name: /GLM-5\.3$/ }));
    expect(await screen.findByRole("alert")).toBeTruthy();
    expect(screen.getByRole("alert").textContent).toContain("not a built-in");
  });
});

// The popup is an overlay: capped and internally scrolling, so a catalogue with many
// providers/models can never grow the settings page, and only the pinned provider opens.
describe("SubagentsTab — model menu overlay", () => {
  const overlayRoutes = (agents: any[], models: any[]) => [
    { match: "/v1/subagents", method: "GET", json: { agents } },
    { match: "/v1/models/catalog", method: "GET", json: { models, default: "" } },
    { match: "/v1/settings", method: "GET", json: SETTINGS },
    { match: "/v1/workspaces/recent", method: "GET", json: WORKSPACES },
  ];

  it("caps a long model list inside an internal scroller, with Inherit outside it", async () => {
    const many = Array.from({ length: 60 }, (_, i) => ({
      id: `zai:model-${i}`,
      name: `GLM Test ${i}`,
      provider: "zai",
      provider_title: "Z.ai",
    }));
    stubFetch(overlayRoutes([{ ...GENERAL, model: "zai:model-0" }], many));
    render(<SubagentsTab workspace="/ws" />);
    await screen.findByText("general");

    fireEvent.click(screen.getByLabelText("general model"));
    const menu = screen.getByRole("menu", { name: "general model menu" });

    // Absolute in a relative wrapper (never in flow) with a hard height cap.
    expect(menu.className).toContain("absolute");
    expect(menu.className).toContain("max-h-[min(360px,60vh)]");
    expect(menu.className).toContain("flex-col");
    expect(menu.closest(".relative")).toBeTruthy();

    // The rows live in their own scroller; Inherit default sits below it, not inside.
    const scroller = menu.querySelector(".overflow-y-auto") as HTMLElement;
    expect(scroller).toBeTruthy();
    expect(scroller.className).toContain("overscroll-contain");
    expect(within(scroller).getByRole("menuitemradio", { name: "GLM Test 59" })).toBeTruthy();
    const inherit = within(menu).getByRole("menuitemradio", { name: /Inherit default/ });
    expect(scroller.contains(inherit)).toBe(false);
    expect(menu.lastElementChild?.contains(inherit)).toBe(true);
  });

  it("opens with only the pinned model's provider expanded", async () => {
    stubFetch(overlayRoutes([{ ...GENERAL, model: "deepseek:chat" }], CATALOG.models));
    render(<SubagentsTab workspace="/ws" />);
    await screen.findByText("general");

    // The pinned model's provider is expanded; the other is a collapsed submenu row.
    fireEvent.click(screen.getByLabelText("general model"));
    const menu = screen.getByRole("menu", { name: "general model menu" });
    expect(within(menu).getByRole("menuitemradio", { name: /DeepSeek V4\.1 Flash/ })).toBeTruthy();
    expect(within(menu).getByRole("menuitem", { name: "Z.ai" })).toBeTruthy();
    expect(within(menu).queryByRole("menuitemradio", { name: /GLM-5\.3$/ })).toBeNull();

    // The unpinned form opens with every provider collapsed.
    fireEvent.keyDown(window, { key: "Escape" });
    fireEvent.click(screen.getAllByRole("button", { name: "New subagent" })[0]);
    fireEvent.click(screen.getByLabelText("Model"));
    const formMenu = screen.getByRole("menu", { name: "subagent model menu" });
    expect(within(formMenu).getByRole("menuitem", { name: "Z.ai" })).toBeTruthy();
    expect(within(formMenu).getByRole("menuitem", { name: "Deepseek" })).toBeTruthy();
    expect(within(formMenu).queryByRole("menuitemradio", { name: /GLM-5\.3$/ })).toBeNull();
    expect(
      within(formMenu).queryByRole("menuitemradio", { name: /DeepSeek V4\.1 Flash/ }),
    ).toBeNull();
  });
});

// The external CLI agents (`provider` set): the row shows install/auth markers, the detail
// swaps the provider catalogue for a free-text CLI model (blank = the CLI's own default),
// and Sign in / Install POST to the provider routes.
describe("SubagentsTab — external CLI agents", () => {
  const CLAUDE = {
    ...GENERAL,
    name: "claude-code",
    description: "Claude Code CLI (external agent).",
    provider: "claude-code",
  };

  const status = (over: Partial<{
    installed: boolean;
    signed_in: boolean;
    detail: string;
  }> = {}) => ({
    provider: "claude-code",
    installed: true,
    path: "C:\\bin\\claude.exe",
    signed_in: true,
    detail: "credentials at ~/.claude/.credentials.json",
    ...over,
  });

  // The external status route must precede the generic `/v1/subagents` GET (the stub
  // matches on `url.includes`), and the model PUT must exist before the list re-reads.
  const extRoutes = (
    st: ReturnType<typeof status>,
    extra: { match: string; method?: string; json: any }[] = [],
  ) => [
    { match: "/v1/subagents/external", method: "GET", json: { providers: [st] } },
    ...extra,
    { match: "/v1/subagents", method: "GET", json: { agents: [...LIST.agents, CLAUDE] } },
    { match: "/v1/models/catalog", method: "GET", json: CATALOG },
    { match: "/v1/settings", method: "GET", json: SETTINGS },
    { match: "/v1/workspaces/recent", method: "GET", json: WORKSPACES },
  ];

  const openExternal = async (
    st: ReturnType<typeof status>,
    extra: { match: string; method?: string; json: any }[] = [],
  ) => {
    const calls = stubFetch(extRoutes(st, extra));
    render(<SubagentsTab workspace="/ws" />);
    // The CLI providers section repeats the provider name, so wait on the agent row.
    await screen.findByLabelText("View claude-code");
    return calls;
  };

  it("badges the row with the provider and its status instead of the provider menu", async () => {
    await openExternal(status({ signed_in: false, detail: "not signed in — use Sign in" }));

    expect(screen.getByText("claude-code CLI")).toBeTruthy();
    expect(screen.queryByLabelText("claude-code model")).toBeNull();
    // Both the row chip and the CLI providers badge carry the state.
    expect(screen.getAllByText("Sign-in needed").length).toBeGreaterThan(0);

    // The detail swaps the catalogue for the CLI model field.
    fireEvent.click(screen.getByLabelText("View claude-code"));
    expect(screen.getByLabelText("claude-code CLI model")).toBeTruthy();
    expect(screen.queryByLabelText("claude-code model")).toBeNull();
    expect(screen.getByText(/uses that CLI’s own model and sign-in/)).toBeTruthy();
    expect(screen.getByText("Installed — not signed in — use Sign in")).toBeTruthy();
  });

  it("shows Install only when the CLI is missing, and POSTs the install route", async () => {
    const calls = await openExternal(
      status({ installed: false, signed_in: false, detail: "not signed in — use Sign in" }),
      [{ match: "/v1/subagents/external/claude-code/install", method: "POST", json: { ok: true } }],
    );

    expect(screen.getAllByText("Not installed").length).toBeGreaterThan(0);
    fireEvent.click(screen.getByLabelText("View claude-code"));
    expect(screen.getByText(/Not on PATH/)).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: /Install CLI/ }));
    await waitFor(() =>
      expect(
        calls.some(
          (c) =>
            c.method === "POST" &&
            c.url.includes("/v1/subagents/external/claude-code/install"),
        ),
      ).toBe(true),
    );
  });

  it("Sign in POSTs the login route when installed but not signed in", async () => {
    const calls = await openExternal(
      status({ signed_in: false, detail: "not signed in — use Sign in" }),
      [{ match: "/v1/subagents/external/claude-code/login", method: "POST", json: { ok: true } }],
    );

    fireEvent.click(screen.getByLabelText("View claude-code"));
    fireEvent.click(screen.getByRole("button", { name: /Sign in/ }));
    await waitFor(() =>
      expect(
        calls.some(
          (c) =>
            c.method === "POST" &&
            c.url.includes("/v1/subagents/external/claude-code/login"),
        ),
      ).toBe(true),
    );
  });

  it("opens the CLI model menu and pins the selection through the model route", async () => {
    const calls = stubFetch([
      {
        match: "/v1/subagents/claude-code/model",
        method: "PUT",
        json: { ok: true, agent: { ...CLAUDE, model: "claude-sonnet-5" } },
      },
      {
        match: "/v1/subagents/external/claude-code/models",
        method: "GET",
        json: {
          ok: true,
          source: "Claude account",
          models: [{ id: "claude-sonnet-5", name: "Claude Sonnet 5" }],
        },
      },
      { match: "/v1/subagents/external", method: "GET", json: { providers: [status()] } },
      { match: "/v1/subagents", method: "GET", json: { agents: [...LIST.agents, CLAUDE] } },
      { match: "/v1/models/catalog", method: "GET", json: CATALOG },
      { match: "/v1/settings", method: "GET", json: SETTINGS },
      { match: "/v1/workspaces/recent", method: "GET", json: WORKSPACES },
    ]);
    render(<SubagentsTab workspace="/ws" />);
    await screen.findByLabelText("View claude-code");

    fireEvent.click(screen.getByLabelText("View claude-code"));
    // The trigger is a menu (like the built-in rows); its pinned bottom row is CLI default.
    const trigger = screen.getByLabelText("claude-code CLI model");
    expect(trigger.textContent).toContain("CLI default");
    await screen.findByText(/1 model on Claude account/);
    fireEvent.click(trigger);

    const menu = screen.getByRole("menu", { name: "claude-code CLI model menu" });
    expect(within(menu).getByRole("menuitemradio", { name: /CLI default/ })).toBeTruthy();
    // Only what the CLI reported: the menu never carries a hardcoded alias row.
    expect(within(menu).queryByRole("menuitemradio", { name: "Aliases" })).toBeNull();
    fireEvent.click(
      within(menu).getByRole("menuitemradio", { name: /claude-sonnet-5/ }),
    );
    await waitFor(() => expect(calls.some((c) => c.method === "PUT")).toBe(true));
    const put = calls.find((c) => c.method === "PUT")!;
    expect(put.url).toContain("/v1/subagents/claude-code/model");
    expect(put.body).toEqual({ model: "claude-sonnet-5" });
  });

  it("opens with the account's models grouped, and no datalist anywhere", async () => {
    const calls = stubFetch([
      {
        match: "/v1/subagents/external/claude-code/models",
        method: "GET",
        json: {
          ok: true,
          source: "Claude account",
          models: [
            { id: "claude-opus-5", name: "Claude Opus 5" },
            { id: "claude-sonnet-5", name: "Claude Sonnet 5" },
          ],
        },
      },
      { match: "/v1/subagents/external", method: "GET", json: { providers: [status()] } },
      { match: "/v1/subagents", method: "GET", json: { agents: [...LIST.agents, CLAUDE] } },
      { match: "/v1/models/catalog", method: "GET", json: CATALOG },
      { match: "/v1/settings", method: "GET", json: SETTINGS },
      { match: "/v1/workspaces/recent", method: "GET", json: WORKSPACES },
    ]);
    render(<SubagentsTab workspace="/ws" />);
    await screen.findByLabelText("View claude-code");
    fireEvent.click(screen.getByLabelText("View claude-code"));

    await screen.findByText(/2 models on Claude account/);
    fireEvent.click(screen.getByLabelText("claude-code CLI model"));
    const menu = screen.getByRole("menu", { name: "claude-code CLI model menu" });
    expect(within(menu).getByText("Claude account")).toBeTruthy();
    expect(within(menu).getByRole("menuitemradio", { name: /claude-opus-5/ })).toBeTruthy();
    expect(within(menu).getByRole("menuitemradio", { name: /claude-sonnet-5/ })).toBeTruthy();
    // Never the browser's own datalist dropdown.
    expect(document.querySelector("datalist")).toBeNull();
    expect(calls.some((c) => c.url.includes("/external/claude-code/models"))).toBe(true);
  });

  it("selecting the pinned default when nothing is pinned fires no save", async () => {
    const calls = await openExternal(status(), []);
    fireEvent.click(screen.getByLabelText("View claude-code"));

    fireEvent.click(screen.getByLabelText("claude-code CLI model"));
    const menu = screen.getByRole("menu", { name: "claude-code CLI model menu" });
    const pinned = within(menu).getByRole("menuitemradio", { name: /CLI default/ });
    expect(pinned.getAttribute("aria-checked")).toBe("true");
    fireEvent.click(pinned);
    expect(calls.some((c) => c.method === "PUT")).toBe(false);
  });

  it("lists every CLI provider so a missing one can still be installed", async () => {
    // `codex` has credentials but no binary: no agent row exists for it, so the CLI
    // providers section is the only place its Install action is reachable.
    const codex = {
      provider: "codex",
      installed: false,
      path: "",
      signed_in: true,
      detail: "credentials at ~/.codex/auth.json",
    };
    const calls = stubFetch([
      { match: "/v1/subagents/external/codex/install", method: "POST", json: { ok: true } },
      { match: "/v1/subagents/external", method: "GET", json: { providers: [codex, status()] } },
      { match: "/v1/subagents", method: "GET", json: { agents: [...LIST.agents, CLAUDE] } },
      { match: "/v1/models/catalog", method: "GET", json: CATALOG },
      { match: "/v1/settings", method: "GET", json: SETTINGS },
      { match: "/v1/workspaces/recent", method: "GET", json: WORKSPACES },
    ]);
    render(<SubagentsTab workspace="/ws" />);

    await screen.findByText("CLI providers");
    expect(screen.getByText("codex")).toBeTruthy();
    expect(screen.getByText("Not installed")).toBeTruthy();
    expect(screen.getByText("Ready")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: /Install CLI/ }));
    await waitFor(() =>
      expect(
        calls.some(
          (c) =>
            c.method === "POST" && c.url.includes("/v1/subagents/external/codex/install"),
        ),
      ).toBe(true),
    );
  });
});

describe("SubagentsTab — enable switch", () => {
  it("turns a built-in off through the store and shows it off", async () => {
    const disabled = { ...GENERAL, disabled: true };
    const calls = stubFetch([
      { match: "/v1/subagents", method: "GET", json: { agents: [disabled, EXPLORE] } },
      { match: "/v1/models/catalog", method: "GET", json: CATALOG },
      { match: "/v1/settings", method: "GET", json: SETTINGS },
      { match: "/v1/workspaces/recent", method: "GET", json: WORKSPACES },
      {
        match: "/v1/subagents/general/enabled",
        method: "PUT",
        json: { ok: true, agent: { ...GENERAL, disabled: false } },
      },
    ]);
    render(<SubagentsTab workspace="/ws" />);
    await screen.findByText("general");

    // A disabled row stays listed (so the switch can flip back) with its Off badge.
    const toggle = screen.getByTestId("subagent-enabled-general");
    expect(toggle.getAttribute("aria-checked")).toBe("false");
    expect(screen.getByText("Off")).toBeTruthy();

    fireEvent.click(toggle);
    await waitFor(() =>
      expect(
        calls.some(
          (c) =>
            c.method === "PUT" &&
            c.url.includes("/v1/subagents/general/enabled"),
        ),
      ).toBe(true),
    );
    const put = calls.find((c) => c.url.includes("/v1/subagents/general/enabled"));
    expect(put?.body).toEqual({ enabled: true });
  });

  it("offers the switch on every built-in row, including the CLI agents", async () => {
    const CLAUDE = { ...GENERAL, name: "claude-code", provider: "claude-code" };
    stubFetch([
      {
        match: "/v1/subagents/external",
        method: "GET",
        json: { providers: [] },
      },
      { match: "/v1/subagents", method: "GET", json: { agents: [GENERAL, CLAUDE] } },
      { match: "/v1/models/catalog", method: "GET", json: CATALOG },
      { match: "/v1/settings", method: "GET", json: SETTINGS },
      { match: "/v1/workspaces/recent", method: "GET", json: WORKSPACES },
    ]);
    render(<SubagentsTab workspace="/ws" />);
    await screen.findByText("claude-code");
    expect(screen.getByLabelText("Disable general")).toBeTruthy();
    expect(screen.getByLabelText("Disable claude-code")).toBeTruthy();
  });
});

describe("SubagentsTab — model selection switch", () => {
  it("renders on by default and posts the toggle", async () => {
    // An older backend omits the key; the switch must read ON rather than guess OFF.
    const calls = stubFetch(routes());
    render(<SubagentsTab workspace="/ws" />);
    await screen.findByText("code-reviewer");

    const toggle = await screen.findByTestId("subagent-model-selection-toggle");
    expect(toggle.getAttribute("aria-checked")).toBe("true");

    fireEvent.click(toggle);
    await waitFor(() =>
      expect(
        calls.some(
          (c) =>
            c.method === "POST" &&
            c.url.includes("/v1/settings/subagent-model-selection"),
        ),
      ).toBe(true),
    );
    const post = calls.find((c) =>
      c.url.includes("/v1/settings/subagent-model-selection"),
    );
    expect(post?.body).toEqual({ enabled: false });
  });

  it("reflects a stored off value", async () => {
    // Built inline, not via `routes(extra)`: the default `/v1/settings` route would
    // match first there, and the stored value is exactly what this case is about.
    stubFetch([
      { match: "/v1/subagents", method: "GET", json: LIST },
      { match: "/v1/models/catalog", method: "GET", json: CATALOG },
      {
        match: "/v1/settings",
        method: "GET",
        json: { ...SETTINGS, subagent_model_selection: false },
      },
      { match: "/v1/workspaces/recent", method: "GET", json: WORKSPACES },
    ]);
    render(<SubagentsTab workspace="/ws" />);
    await screen.findByText("code-reviewer");
    const toggle = await screen.findByTestId("subagent-model-selection-toggle");
    expect(toggle.getAttribute("aria-checked")).toBe("false");
  });
});
