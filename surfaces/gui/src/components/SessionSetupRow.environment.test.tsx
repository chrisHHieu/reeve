// The welcome environment chip (the menu shape, now the real pick): LOCAL checked when local, saved SSH hosts selectable, a workspace-path
// step after a host is picked, the bound host checked, LOCAL clearing it, and every
// refusal shown inline in the server's own words. Folder chips are untouched.
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ComponentProps } from "react";
import { SessionSetupRow } from "./SessionSetupRow";
import type {
  RemoteDirectoryEntry,
  RemoteEnvironmentProfile,
  RemoteEnvironmentSummary,
} from "../api";

const HOSTS: RemoteEnvironmentProfile[] = [
  {
    id: "h1",
    name: "Build server",
    host: "dev.example.com",
    user: "ubuntu",
    port: 2222,
    identity_file: null,
    remembered: false,
    has_password: false,
    created_at: "2026-09-18T00:00:00Z",
    updated_at: "2026-09-18T00:00:00Z",
  },
  {
    id: "h2",
    name: "Home box",
    host: "home.local",
    user: null,
    port: 22,
    identity_file: null,
    remembered: false,
    has_password: false,
    created_at: "2026-09-18T00:00:00Z",
    updated_at: "2026-09-18T00:00:00Z",
  },
];

const BOUND: RemoteEnvironmentSummary = {
  id: "h1",
  name: "Build server",
  user: "ubuntu",
  host: "dev.example.com",
  port: 2222,
};

function stubHosts(
  hosts: RemoteEnvironmentProfile[],
  listing: Record<string, RemoteDirectoryEntry[]> = {},
) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/directories")) {
        const path = new URL(url, "http://localhost").searchParams.get("path") || "/home/dev";
        return {
          ok: true,
          json: async () => ({ ok: true, path, entries: listing[path] ?? [] }),
        } as Response;
      }
      if (url.includes("/v1/remote-environments")) {
        return { ok: true, json: async () => ({ environments: hosts }) } as Response;
      }
      return { ok: true, json: async () => ({}) } as Response;
    }),
  );
}

const LISTING: Record<string, RemoteDirectoryEntry[]> = {
  "/home/dev": [
    { name: "project", path: "/home/dev/project" },
    { name: "shared", path: "/home/dev/shared" },
  ],
  "/home/dev/project": [
    { name: "docs", path: "/home/dev/project/docs" },
    { name: "src", path: "/home/dev/project/src" },
  ],
};

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

const baseProps: ComponentProps<typeof SessionSetupRow> = {
  folderName: "exports",
  folderPath: "/Users/me/exports",
  tempFolder: false,
  environment: null,
  onPickFolder: vi.fn(),
  onUseTemp: vi.fn(async () => ({ ok: true })),
  onOpenRemoteSettings: vi.fn(),
  onPickRemote: vi.fn(async () => ({ ok: true })),
  onClearEnvironment: vi.fn(async () => ({ ok: true })),
};

async function openEnvironmentMenu() {
  fireEvent.click(screen.getByTestId("environment-chip"));
  await screen.findByTestId("environment-panel");
}

describe("SessionSetupRow environment chip", () => {
  it("checks LOCAL and lists saved remote hosts as selectable rows", async () => {
    stubHosts(HOSTS);
    render(<SessionSetupRow {...baseProps} />);
    await openEnvironmentMenu();

    expect(screen.getByTestId("environment-local").getAttribute("aria-checked")).toBe("true");
    expect(screen.getByTestId("environment-chip").textContent).toContain("Local");

    await waitFor(() => expect(screen.getAllByTestId("environment-remote")).toHaveLength(2));
    const remotes = screen.getAllByTestId("environment-remote");
    expect(remotes[0].textContent).toContain("Build server");
    expect(remotes[0].textContent).toContain("ubuntu@dev.example.com:2222");
    for (const remote of remotes) {
      expect((remote as HTMLButtonElement).disabled).toBe(false);
      expect(remote.textContent).not.toContain("not available");
    }
  });

  it("shows the empty remote state when no host is saved", async () => {
    stubHosts([]);
    render(<SessionSetupRow {...baseProps} />);
    await openEnvironmentMenu();
    await screen.findByTestId("environment-remote-empty");
    expect(screen.getByTestId("environment-remote-empty").textContent).toContain(
      "No SSH hosts saved yet",
    );
  });

  it("asks for the workspace path after a host pick, validates it, then binds", async () => {
    stubHosts(HOSTS);
    const onPickRemote = vi.fn(async () => ({ ok: true }));
    render(<SessionSetupRow {...baseProps} onPickRemote={onPickRemote} />);
    await openEnvironmentMenu();

    fireEvent.click(screen.getAllByTestId("environment-remote")[0]);
    const input = await screen.findByTestId("environment-path");
    expect(screen.getByTestId("environment-path-step").textContent).toContain("Build server");

    // A relative path is refused locally (the server's rule, said early).
    fireEvent.change(input, { target: { value: "home/dev/app" } });
    fireEvent.click(screen.getByTestId("environment-path-bind"));
    await screen.findByTestId("environment-error");
    expect(screen.getByTestId("environment-error").textContent).toContain("absolute");
    expect(onPickRemote).not.toHaveBeenCalled();

    // An absolute path binds through the app callback and retires the panel.
    fireEvent.change(input, { target: { value: "/srv/app" } });
    fireEvent.click(screen.getByTestId("environment-path-bind"));
    await waitFor(() =>
      expect(onPickRemote).toHaveBeenCalledWith(
        expect.objectContaining({ id: "h1", host: "dev.example.com" }),
        "/srv/app",
      ),
    );
    await waitFor(() => expect(screen.queryByTestId("environment-panel")).toBeNull());
  });

  it("browses the host's folders: lists, filters, descends and climbs", async () => {
    stubHosts(HOSTS, LISTING);
    render(<SessionSetupRow {...baseProps} />);
    await openEnvironmentMenu();

    fireEvent.click(screen.getAllByTestId("environment-remote")[0]);
    await waitFor(() =>
      expect(screen.getAllByTestId("environment-dir")).toHaveLength(2),
    );
    // The default listing is the host's $HOME, resolved by the server.
    expect(screen.getByTestId("environment-path-readout").textContent).toContain("/home/dev");

    fireEvent.change(screen.getByTestId("environment-filter"), {
      target: { value: "proj" },
    });
    await waitFor(() =>
      expect(screen.getAllByTestId("environment-dir")).toHaveLength(1),
    );
    expect(screen.getByTestId("environment-dir").textContent).toContain("project");

    // A click descends into that folder; the readout follows the listing.
    fireEvent.click(screen.getByTestId("environment-dir"));
    await waitFor(() =>
      expect(screen.getByTestId("environment-path-readout").textContent).toContain(
        "/home/dev/project",
      ),
    );
    await waitFor(() =>
      expect(screen.getAllByTestId("environment-dir").map((el) => el.textContent)).toEqual([
        "docs",
        "src",
      ]),
    );

    // Up climbs to the parent folder (computed by the client, never sent as `..`).
    fireEvent.click(screen.getByTestId("environment-up"));
    await waitFor(() =>
      expect(screen.getByTestId("environment-path-readout").textContent).toContain(
        "/home/dev",
      ),
    );
  });

  it("binds the folder chosen in the browser through the existing bind path", async () => {
    stubHosts(HOSTS, LISTING);
    const onPickRemote = vi.fn(async () => ({ ok: true }));
    render(<SessionSetupRow {...baseProps} onPickRemote={onPickRemote} />);
    await openEnvironmentMenu();
    fireEvent.click(screen.getAllByTestId("environment-remote")[0]);

    await waitFor(() =>
      expect(screen.getAllByTestId("environment-dir")).toHaveLength(2),
    );
    fireEvent.click(
      screen
        .getAllByTestId("environment-dir")
        .find((el) => el.textContent?.includes("project"))!,
    );
    await waitFor(() =>
      expect(screen.getByTestId("environment-path-readout").textContent).toContain(
        "/home/dev/project",
      ),
    );

    fireEvent.click(screen.getByTestId("environment-path-bind"));
    await waitFor(() =>
      expect(onPickRemote).toHaveBeenCalledWith(
        expect.objectContaining({ id: "h1" }),
        "/home/dev/project",
      ),
    );
    await waitFor(() => expect(screen.queryByTestId("environment-panel")).toBeNull());
  });

  it("shows a listing refusal inline and keeps the manual input usable", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.includes("/directories")) {
          return {
            ok: true,
            json: async () => ({ ok: false, error: "Permission denied reading /root" }),
          } as Response;
        }
        if (url.includes("/v1/remote-environments")) {
          return { ok: true, json: async () => ({ environments: HOSTS }) } as Response;
        }
        return { ok: true, json: async () => ({}) } as Response;
      }),
    );
    render(<SessionSetupRow {...baseProps} />);
    await openEnvironmentMenu();
    fireEvent.click(screen.getAllByTestId("environment-remote")[0]);

    await screen.findByTestId("environment-error");
    expect(screen.getByTestId("environment-error").textContent).toContain(
      "Permission denied reading /root",
    );
    // The manual path is the fallback: an absolute typed path still binds.
    fireEvent.change(screen.getByTestId("environment-path"), {
      target: { value: "/srv/app" },
    });
    fireEvent.click(screen.getByTestId("environment-path-bind"));
    await waitFor(() => expect(screen.queryByTestId("environment-panel")).toBeNull());
  });

  it("shows a server refusal in the panel and keeps it open", async () => {
    stubHosts(HOSTS);
    const onPickRemote = vi.fn(async () => ({
      ok: false,
      error: "Authentication failed for dev@dev.example.com.",
    }));
    render(<SessionSetupRow {...baseProps} onPickRemote={onPickRemote} />);
    await openEnvironmentMenu();

    fireEvent.click(screen.getAllByTestId("environment-remote")[0]);
    fireEvent.change(await screen.findByTestId("environment-path"), {
      target: { value: "/srv/app" },
    });
    fireEvent.click(screen.getByTestId("environment-path-bind"));

    await screen.findByTestId("environment-error");
    expect(screen.getByTestId("environment-error").textContent).toContain(
      "Authentication failed for dev@dev.example.com.",
    );
    expect(screen.getByTestId("environment-panel")).toBeTruthy();
  });

  it("checks the bound host, labels the chip, and LOCAL clears the binding", async () => {
    stubHosts(HOSTS);
    const onClearEnvironment = vi.fn(async () => ({ ok: true }));
    render(
      <SessionSetupRow
        {...baseProps}
        environment={BOUND}
        remoteWorkspace="/srv/app"
        onClearEnvironment={onClearEnvironment}
      />,
    );
    expect(screen.getByTestId("environment-chip").textContent).toContain(
      "ubuntu@dev.example.com:2222",
    );

    await openEnvironmentMenu();
    expect(screen.getByTestId("environment-local").getAttribute("aria-checked")).toBe("false");
    const bound = screen
      .getAllByTestId("environment-remote")
      .find((el) => el.textContent?.includes("Build server"))!;
    expect(bound.getAttribute("aria-checked")).toBe("true");

    // Re-opening the path step for the already-bound host prefills its workspace.
    fireEvent.click(bound);
    expect((await screen.findByTestId("environment-path") as HTMLInputElement).value).toBe(
      "/srv/app",
    );
    fireEvent.click(screen.getByTestId("environment-path-back"));

    fireEvent.click(screen.getByTestId("environment-local"));
    await waitFor(() => expect(onClearEnvironment).toHaveBeenCalledOnce());
    await waitFor(() => expect(screen.queryByTestId("environment-panel")).toBeNull());
  });

  it("shows a LOCAL refusal inline instead of pretending it worked", async () => {
    stubHosts(HOSTS);
    const onClearEnvironment = vi.fn(async () => ({
      ok: false,
      error: "choose a local folder to switch to",
    }));
    render(
      <SessionSetupRow
        {...baseProps}
        environment={BOUND}
        remoteWorkspace="/srv/app"
        onClearEnvironment={onClearEnvironment}
      />,
    );
    await openEnvironmentMenu();
    fireEvent.click(screen.getByTestId("environment-local"));

    await screen.findByTestId("environment-error");
    expect(screen.getByTestId("environment-error").textContent).toContain(
      "choose a local folder to switch to",
    );
    expect(screen.getByTestId("environment-panel")).toBeTruthy();
  });

  it("surfaces the app's typed start error without opening the panel", () => {
    stubHosts(HOSTS);
    render(
      <SessionSetupRow
        {...baseProps}
        environment={BOUND}
        remoteWorkspace="/srv/app"
        remoteError="Authentication failed for dev@dev.example.com."
      />,
    );
    const banner = screen.getByTestId("environment-error-banner");
    expect(banner.textContent).toContain("Authentication failed");
  });

  it("hides the local folder chip while a remote environment is bound", async () => {
    stubHosts(HOSTS);
    render(<SessionSetupRow {...baseProps} />);
    expect(screen.getByTestId("folder-chip")).toBeTruthy();
    cleanup();
    render(
      <SessionSetupRow {...baseProps} environment={BOUND} remoteWorkspace="/srv/app" />,
    );
    expect(screen.queryByTestId("folder-chip")).toBeNull();
  });

  it("Add SSH host… opens Settings ▸ Remote and closes the menu", async () => {
    stubHosts(HOSTS);
    const onOpenRemoteSettings = vi.fn();
    render(<SessionSetupRow {...baseProps} onOpenRemoteSettings={onOpenRemoteSettings} />);
    await openEnvironmentMenu();

    fireEvent.click(screen.getByTestId("environment-add-host"));
    expect(onOpenRemoteSettings).toHaveBeenCalledOnce();
    await waitFor(() => expect(screen.queryByTestId("environment-panel")).toBeNull());
  });
});
