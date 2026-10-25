// Settings ▸ Plugins: what is installed and its switch (locked when a project
// or an administrator decides), what marketplaces offer, and the consent screen an install
// goes through — settings sent typed, a secret in a password field.
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { PluginsTab, componentsLine, lockedReason } from "./PluginsTab";

type Call = { url: string; method: string; body?: any };

const CONSENT = {
  id: "deployer@acme",
  name: "deployer",
  display_name: "",
  version: "1.0.0",
  description: "Deploys things",
  author: "Acme",
  source: "https://example.com/deployer.git",
  sha: "abcdef1234567890",
  skills: ["deployer:checklist"],
  commands: ["deployer:deploy"],
  agents: [],
  executable: [{ kind: "hooks", detail: '{"hooks": {"PreToolUse": []}}' }],
  skipped: ["hooks: runs code — Reeve does not run a plugin's code yet (it will ask for your consent when it does)"],
  user_config: [
    { key: "branch", title: "Branch", description: "Deploy from", type: "string", sensitive: false, required: true },
    { key: "token", title: "Token", description: "API token", type: "string", sensitive: true, required: false },
    { key: "retries", title: "Retries", description: "How many", type: "number", sensitive: false, required: false },
  ],
  dependencies: [],
};

function stub(view: Record<string, unknown>, extra: (call: Call) => unknown = () => ({})) {
  const calls: Call[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      const call = { url, method: (init?.method || "GET").toUpperCase(), body: init?.body ? JSON.parse(String(init.body)) : undefined };
      calls.push(call);
      let body: unknown = extra(call);
      if (url.endsWith("/v1/plugins") || url.includes("/v1/plugins?")) body = view;
      else if (url.endsWith("/v1/plugin-marketplaces")) body = call.method === "GET" ? { marketplaces: view.marketplaces ?? [] } : body;
      else if (url.includes("/v1/plugin-marketplaces/acme/plugins")) body = { plugins: view.offered ?? [] };
      return { ok: true, status: 200, json: async () => body } as Response;
    }),
  );
  return calls;
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("PluginsTab tabs", () => {
  it("opens on Marketplaces while nothing is installed, on Installed once something is, and switches", async () => {
    stub({
      plugins: [],
      errors: [],
      installed: [],
      marketplaces: [{ name: "acme", plugins: 0, error: "", installLocation: "/m" }],
    });
    render(<PluginsTab />);
    expect(await screen.findByTestId("plugins-marketplaces")).toBeTruthy();
    expect(screen.queryByTestId("plugins-installed")).toBeNull();
    expect(screen.getByTestId("plugins-tab-browse").getAttribute("aria-selected")).toBe("true");
    fireEvent.click(screen.getByTestId("plugins-tab-installed"));
    expect(screen.getByTestId("plugins-installed").textContent).toContain("Install one from Marketplaces");
    expect(screen.queryByTestId("plugins-marketplaces")).toBeNull();
    cleanup();

    stub({
      plugins: [],
      errors: [],
      installed: [{ id: "mine@acme", enabled: true, scope: "user", version: "1.0.0" }],
      marketplaces: [{ name: "acme", plugins: 1, error: "", installLocation: "/m" }],
    });
    render(<PluginsTab />);
    expect(await screen.findByTestId("plugin-mine@acme")).toBeTruthy();
    expect(screen.getByTestId("plugins-tab-installed").textContent).toContain("1");
    expect(screen.queryByTestId("plugins-marketplaces")).toBeNull();
  });
});

describe("PluginsTab", () => {
  it("says what a plugin brought, and why a switch is locked", () => {
    expect(componentsLine({ skills: 1, commands: 2, agents: 0 })).toBe("1 skill · 2 commands");
    expect(componentsLine({})).toBe("Nothing to load");
    const row = { id: "a@b", version: "1", scopes: ["user"], enabled: true, enabled_by: "managed", install_path: "" };
    expect(lockedReason(row)).toBe("Set by your administrator");
    expect(lockedReason({ ...row, enabled_by: "user" })).toBeNull();
  });

  it("lists a session plugin with what it skipped, and a failed one under Errors", async () => {
    stub({
      plugins: [
        {
          id: "release-helper@inline",
          name: "release-helper",
          version: "0.3.0",
          root: "/plugins/release-helper",
          scope: "session",
          display_name: "",
          description: "Release tools",
          components: { skills: 1, agents: 2 },
          skipped: ["hooks: runs code"],
        },
      ],
      errors: [{ id: "broken@inline", error: "plugin name 'Bad Name' must be kebab-case" }],
      installed: [],
    });
    render(<PluginsTab />);
    const row = await screen.findByTestId("plugin-release-helper");
    expect(row.textContent).toContain("v0.3.0 · 1 skill · 2 agents");
    expect(row.textContent).toContain("1 not loaded");
    expect(screen.getByTestId("plugins-errors").textContent).toContain("must be kebab-case");
  });

  it("an installed plugin's switch turns it off, and a project's choice locks it", async () => {
    const calls = stub({
      plugins: [],
      errors: [],
      installed: [
        { id: "mine@acme", version: "1", scopes: ["user"], enabled: true, enabled_by: "user", install_path: "" },
        { id: "theirs@acme", version: "1", scopes: ["user"], enabled: true, enabled_by: "project", install_path: "" },
      ],
    });
    render(<PluginsTab workspace="/ws" />);
    fireEvent.click(await screen.findByTestId("plugin-switch-mine@acme"));
    await waitFor(() => expect(calls.some((c) => c.method === "PATCH")).toBe(true));
    const patch = calls.find((c) => c.method === "PATCH")!;
    expect(patch.url).toContain("/v1/plugins/mine%40acme");
    expect(patch.body).toEqual({ enabled: false, scope: "user", workspace: "/ws" });
    const locked = screen.getByTestId("plugin-switch-theirs@acme") as HTMLButtonElement;
    expect(locked.disabled).toBe(true);
    expect(screen.getByTestId("plugin-theirs@acme").textContent).toContain("Set by this project's plugin settings");
  });

  it("Install shows what runs code and sends the settings typed on confirm", async () => {
    const calls = stub(
      {
        plugins: [],
        errors: [],
        installed: [],
        marketplaces: [{ name: "acme", plugins: 1, error: "", installLocation: "/m" }],
        offered: [{ id: "deployer@acme", name: "deployer", description: "Deploys things", version: "1.0.0", installed: false }],
      },
      (call) =>
        call.url.endsWith("/v1/plugins/install") ? { token: "tok12345", consent: CONSENT } : call.url.includes("/confirm") ? { id: "deployer@acme" } : {},
    );
    render(<PluginsTab />);
    fireEvent.click(await screen.findByTestId("plugin-install-deployer@acme"));
    const dialog = await screen.findByTestId("plugin-consent");
    expect(screen.getByTestId("plugin-consent-code").textContent).toContain("PreToolUse");
    expect((screen.getByLabelText("Token") as HTMLInputElement).type).toBe("password");
    fireEvent.change(screen.getByLabelText("Branch"), { target: { value: "release" } });
    fireEvent.change(screen.getByLabelText("Retries"), { target: { value: "3" } });
    expect(dialog.textContent).toContain("never gains a permission");
    fireEvent.click(screen.getByTestId("plugin-consent-confirm"));
    await waitFor(() => expect(calls.some((c) => c.url.includes("/install/tok12345/confirm"))).toBe(true));
    const confirm = calls.find((c) => c.url.includes("/confirm"))!;
    expect(confirm.body).toEqual({ user_config: { branch: "release", retries: 3 } });
  });

  it("a plugin whose code waits says so, and Review asks for a yes to that code", async () => {
    const calls = stub(
      {
        plugins: [
          {
            id: "guard@acme",
            name: "guard",
            version: "1",
            root: "/c",
            scope: "project",
            display_name: "",
            description: "",
            components: { skills: 1 },
            skipped: [],
            code: "needs_consent",
          },
        ],
        errors: [],
        installed: [{ id: "guard@acme", version: "1", scopes: ["user"], enabled: true, enabled_by: "project", install_path: "/c" }],
      },
      (call) => (call.url.includes("/consent") ? { token: "tok98765", consent: { ...CONSENT, id: "guard@acme", name: "guard" } } : {}),
    );
    render(<PluginsTab workspace="/ws" />);
    expect((await screen.findByTestId("plugin-code-waiting-guard@acme")).textContent).toContain("Code not running");
    fireEvent.click(screen.getByTestId("plugin-review-guard@acme"));
    await screen.findByTestId("plugin-consent");
    const review = calls.find((c) => c.url.includes("/consent"))!;
    expect(review.url).toContain("/v1/plugins/guard%40acme/consent");
    expect(review.body).toEqual({ workspace: "/ws" });  // a project's plugin: this workspace's yes
    expect(screen.getByRole("dialog").querySelector("h2")?.textContent).toBe("Allow guard to run its code?");
    expect(screen.getByTestId("plugin-consent-confirm").textContent).toBe("Allow");
  });

  it("a withdrawn plugin says it is not loaded and can be kept; a waiting update asks for review", async () => {
    const calls = stub({
      plugins: [],
      errors: [],
      installed: [
        {
          id: "gone@acme",
          version: "1",
          scopes: ["user"],
          enabled: true,
          enabled_by: "user",
          install_path: "",
          flagged: { marketplace: "acme", flaggedAt: "2026-09-27T00:00:00Z" },
        },
        {
          id: "next@acme",
          version: "1",
          scopes: ["user"],
          enabled: true,
          enabled_by: "user",
          install_path: "",
          update_waiting: { version: "2.0.0", reason: "the code it runs changed", checkedAt: "2026-09-27T00:00:00Z" },
        },
      ],
    });
    render(<PluginsTab />);
    expect((await screen.findByTestId("plugin-withdrawn-gone@acme")).textContent).toContain("acme no longer lists it");
    expect(screen.getByTestId("plugin-update-waiting-next@acme").textContent).toContain(
      "Update to v2.0.0 waiting for you: the code it runs changed.",
    );
    expect(screen.getByTestId("plugin-update-next@acme").textContent).toBe("Review update");
    expect(screen.queryByTestId("plugin-keep-next@acme")).toBeNull();
    fireEvent.click(screen.getByTestId("plugin-keep-gone@acme"));
    await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.url.endsWith("/v1/plugins/gone%40acme/keep"))).toBe(true));
  });

  it("a marketplace's auto-update is off until switched on", async () => {
    const calls = stub({
      plugins: [],
      errors: [],
      installed: [],
      marketplaces: [{ name: "acme", plugins: 0, error: "", installLocation: "/m" }],
    });
    render(<PluginsTab />);
    const row = await screen.findByTestId("market-acme");
    expect(row.textContent).toContain("Auto-update off");
    fireEvent.click(screen.getByTestId("market-auto-acme"));
    await waitFor(() => expect(calls.some((c) => c.method === "PATCH" && c.url.endsWith("/v1/plugin-marketplaces/acme"))).toBe(true));
    expect(calls.find((c) => c.method === "PATCH")!.body).toEqual({ autoUpdate: true });
  });

  it("This project offers to add a suggested marketplace and to install a declared plugin at project scope", async () => {
    const calls = stub(
      {
        plugins: [],
        errors: [],
        installed: [],
        marketplaces: [{ name: "acme", plugins: 1, error: "", installLocation: "/m" }],
      },
      (call) => {
        if (call.url.includes("/v1/plugins/project"))
          return {
            trusted: true,
            plugins: [
              { id: "formatter@acme", scope: "project", state: "missing" },
              { id: "lint@team", scope: "project", state: "missing" },
            ],
            marketplaces: [{ name: "team", source: "/ws/tools-market", file: ".reeve/plugins.json", known: false }],
            pending: 3,
          };
        if (call.url.endsWith("/v1/plugins/install")) return { token: "tok12345", consent: CONSENT };
        if (call.url.endsWith("/v1/plugin-marketplaces") && call.method === "POST") return { name: "team", plugins: 1 };
        return {};
      },
    );
    render(<PluginsTab workspace="/ws" />);
    await screen.findByTestId("plugins-project");
    // lint@team's marketplace is not known here: no Install until it is added.
    expect(screen.getByTestId("project-plugin-lint@team").textContent).toContain("add the team marketplace first");
    expect(screen.queryByTestId("project-install-lint@team")).toBeNull();

    fireEvent.click(screen.getByTestId("project-market-add-team"));
    await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.url.endsWith("/v1/plugin-marketplaces"))).toBe(true));
    expect(calls.find((c) => c.method === "POST" && c.url.endsWith("/v1/plugin-marketplaces"))!.body).toEqual({ source: "/ws/tools-market" });

    fireEvent.click(screen.getByTestId("project-install-formatter@acme"));
    await waitFor(() => expect(calls.some((c) => c.url.endsWith("/v1/plugins/install"))).toBe(true));
    expect(calls.find((c) => c.url.endsWith("/v1/plugins/install"))!.body).toEqual({ id: "formatter@acme", scope: "project", workspace: "/ws" });
  });
});
