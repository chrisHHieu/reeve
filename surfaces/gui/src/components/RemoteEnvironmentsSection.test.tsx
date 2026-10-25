// Settings ▸ Remote (phase 1): SSH host CRUD through /v1/remote-environments and
// the real Test Connection, whose status is an inline card — never an alert. The server-side
// probe is mocked at the network layer; the security posture itself is pinned in Python.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { RemoteEnvironmentsSection } from "./RemoteEnvironmentsSection";
import type { RemoteEnvironmentProfile, RemoteEnvironmentTestResult } from "../api";

const PROFILE: RemoteEnvironmentProfile = {
  id: "abc",
  name: "Build server",
  host: "dev.example.com",
  user: "ubuntu",
  port: 2222,
  identity_file: "~/.ssh/id_ed25519",
  remembered: false,
  has_password: false,
  created_at: "2026-09-18T00:00:00Z",
  updated_at: "2026-09-18T00:00:00Z",
};

let profiles: RemoteEnvironmentProfile[] = [];
let testResult: RemoteEnvironmentTestResult;
let calls: { method: string; url: string; body?: Record<string, unknown> }[] = [];

const json = (body: unknown) =>
  ({ ok: true, json: async () => body }) as unknown as Response;

function stubRoutes() {
  profiles = [];
  calls = [];
  testResult = { ok: true, platform: "linux", arch: "x64", home: "/home/ubuntu", error: null };
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method || "GET";
      calls.push({
        method,
        url,
        body: init?.body ? (JSON.parse(String(init.body)) as Record<string, unknown>) : undefined,
      });
      if (url.endsWith("/v1/remote-environments") && method === "GET") {
        return json({ environments: profiles });
      }
      if (url.endsWith("/v1/remote-environments") && method === "POST") {
        const body = JSON.parse(String(init?.body)) as Record<string, unknown>;
        const created: RemoteEnvironmentProfile = {
          ...PROFILE,
          id: "new-1",
          name: String(body.name),
          host: String(body.host),
          user: (body.user as string) ?? null,
          port: (body.port as number) ?? 22,
          identity_file: (body.identity_file as string) ?? null,
        };
        profiles = [...profiles, created];
        return json({ ok: true, environment: created });
      }
      if (url.includes("/test")) return json(testResult);
      if (method === "DELETE") {
        profiles = profiles.filter((p) => !url.endsWith(p.id));
        return json({ ok: true });
      }
      if (method === "PATCH") {
        return json({ ok: true, environment: { ...PROFILE, name: "Renamed" } });
      }
      return json({});
    }),
  );
}

beforeEach(stubRoutes);

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("Settings → Remote environments", () => {
  it("shows the empty state until the first host is added", async () => {
    render(<RemoteEnvironmentsSection />);
    await screen.findByTestId("remote-empty");
    expect(screen.getByTestId("remote-empty").textContent).toContain("Work on another computer");
    // The empty list offers Add host itself; the header does not offer it twice.
    expect(screen.queryByTestId("remote-new")).toBeNull();
    expect(screen.queryAllByTestId("remote-row")).toHaveLength(0);
  });

  it("lists saved hosts with their destination and an untested connection", async () => {
    profiles = [PROFILE];
    render(<RemoteEnvironmentsSection />);
    await screen.findByTestId("remote-row");
    expect(screen.getByText("Build server")).toBeTruthy();
    expect(screen.getByText("ubuntu@dev.example.com:2222")).toBeTruthy();
    expect(screen.getByTestId("remote-status").textContent).toContain("Connection: Not tested");
  });

  it("validates the new-host form before any request", async () => {
    render(<RemoteEnvironmentsSection />);
    await screen.findByTestId("remote-empty");
    fireEvent.click(screen.getByTestId("remote-empty-add"));

    fireEvent.click(screen.getByTestId("remote-save"));
    expect(screen.getByTestId("remote-form-error").textContent).toContain("Name is required.");

    fireEvent.change(screen.getByTestId("remote-name"), { target: { value: "Box" } });
    fireEvent.click(screen.getByTestId("remote-save"));
    expect(screen.getByTestId("remote-form-error").textContent).toContain("SSH host is required.");

    fireEvent.change(screen.getByTestId("remote-host"), { target: { value: "box" } });
    fireEvent.change(screen.getByTestId("remote-port"), { target: { value: "99999" } });
    fireEvent.click(screen.getByTestId("remote-save"));
    expect(screen.getByTestId("remote-form-error").textContent).toContain(
      "SSH port must be an integer between 1 and 65535.",
    );
    expect(calls.filter((c) => c.method === "POST" && c.url.endsWith("/v1/remote-environments"))).toHaveLength(0);
  });

  it("saves a new host through POST and shows it in the list", async () => {
    render(<RemoteEnvironmentsSection />);
    await screen.findByTestId("remote-empty");
    fireEvent.click(screen.getByTestId("remote-empty-add"));
    fireEvent.change(screen.getByTestId("remote-name"), { target: { value: "Build server" } });
    fireEvent.change(screen.getByTestId("remote-host"), { target: { value: "dev.example.com" } });
    fireEvent.change(screen.getByTestId("remote-user"), { target: { value: "ubuntu" } });
    fireEvent.change(screen.getByTestId("remote-port"), { target: { value: "2222" } });
    fireEvent.change(screen.getByTestId("remote-identity"), {
      target: { value: "~/.ssh/id_ed25519" },
    });
    fireEvent.click(screen.getByTestId("remote-save"));

    await screen.findByTestId("remote-row");
    expect(screen.queryByTestId("remote-form")).toBeNull();
    const post = calls.find((c) => c.method === "POST" && c.url.endsWith("/v1/remote-environments"));
    expect(post?.body).toEqual({
      name: "Build server",
      host: "dev.example.com",
      user: "ubuntu",
      port: 2222,
      identity_file: "~/.ssh/id_ed25519",
      password: "",
      remember: false,
    });
  });

  it("Test Connection reports Connected with platform/arch inline", async () => {
    profiles = [PROFILE];
    render(<RemoteEnvironmentsSection />);
    await screen.findByTestId("remote-row");

    fireEvent.click(screen.getByTestId("remote-test"));
    await waitFor(() =>
      expect(screen.getByTestId("remote-status").textContent).toContain("Connection: Connected"),
    );
    expect(screen.getByTestId("remote-status").textContent).toContain("linux x64");
    expect(calls.some((c) => c.method === "POST" && c.url.endsWith(`/${PROFILE.id}/test`))).toBe(true);
  });

  it("Test Connection reports a failure inline, with no alert", async () => {
    profiles = [PROFILE];
    testResult = {
      ok: false,
      platform: null,
      arch: null,
      home: null,
      error: "The host key for dev.example.com is not trusted or has changed.",
    };
    const alert = vi.spyOn(window, "alert").mockImplementation(() => {});
    render(<RemoteEnvironmentsSection />);
    await screen.findByTestId("remote-row");

    fireEvent.click(screen.getByTestId("remote-test"));
    await screen.findByTestId("remote-status-error");
    expect(screen.getByTestId("remote-status").textContent).toContain("Connection: Disconnected");
    expect(screen.getByTestId("remote-status-error").textContent).toContain("not trusted");
    expect(alert).not.toHaveBeenCalled();
  });

  it("deletes a host after confirmation", async () => {
    profiles = [PROFILE];
    vi.spyOn(window, "confirm").mockReturnValue(true);
    render(<RemoteEnvironmentsSection />);
    await screen.findByTestId("remote-row");

    fireEvent.click(screen.getByTestId("remote-delete"));
    await waitFor(() => expect(screen.queryAllByTestId("remote-row")).toHaveLength(0));
    expect(calls.some((c) => c.method === "DELETE" && c.url.endsWith(`/${PROFILE.id}`))).toBe(true);
  });

  it("masks the password field and toggles its visibility", async () => {
    render(<RemoteEnvironmentsSection />);
    await screen.findByTestId("remote-empty");
    fireEvent.click(screen.getByTestId("remote-empty-add"));

    const field = screen.getByTestId("remote-password") as HTMLInputElement;
    expect(field.type).toBe("password");
    fireEvent.change(field, { target: { value: "hunter2" } });
    expect((screen.getByTestId("remote-password") as HTMLInputElement).value).toBe("hunter2");

    fireEvent.click(screen.getByTestId("remote-password-toggle"));
    expect((screen.getByTestId("remote-password") as HTMLInputElement).type).toBe("text");
    fireEvent.click(screen.getByTestId("remote-password-toggle"));
    expect((screen.getByTestId("remote-password") as HTMLInputElement).type).toBe("password");
  });

  it("sends the transient password unchecked and the remember flag checked", async () => {
    render(<RemoteEnvironmentsSection />);
    await screen.findByTestId("remote-empty");
    fireEvent.click(screen.getByTestId("remote-empty-add"));
    fireEvent.change(screen.getByTestId("remote-name"), { target: { value: "Box" } });
    fireEvent.change(screen.getByTestId("remote-host"), { target: { value: "box" } });
    fireEvent.change(screen.getByTestId("remote-password"), { target: { value: "s3cret" } });
    expect(screen.getByTestId("remote-password-help").textContent).toContain("not saved");

    fireEvent.click(screen.getByTestId("remote-save"));
    await screen.findByTestId("remote-row");
    const first = calls.find((c) => c.method === "POST" && c.url.endsWith("/v1/remote-environments"));
    expect(first?.body).toEqual({
      name: "Box",
      host: "box",
      user: "",
      port: 22,
      identity_file: "",
      password: "s3cret",
      remember: false,
    });

    fireEvent.click(screen.getByTestId("remote-new"));
    fireEvent.change(screen.getByTestId("remote-name"), { target: { value: "Box 2" } });
    fireEvent.change(screen.getByTestId("remote-host"), { target: { value: "box2" } });
    fireEvent.click(screen.getByTestId("remote-remember"));
    expect(screen.getByTestId("remote-password-help").textContent).toContain(
      "Windows Credential Manager",
    );
    fireEvent.change(screen.getByTestId("remote-password"), { target: { value: "s3cret" } });
    fireEvent.click(screen.getByTestId("remote-save"));

    await waitFor(() =>
      expect(
        calls.filter((c) => c.method === "POST" && c.url.endsWith("/v1/remote-environments")),
      ).toHaveLength(2),
    );
    const second = calls.filter(
      (c) => c.method === "POST" && c.url.endsWith("/v1/remote-environments"),
    )[1];
    expect(second.body).toMatchObject({ password: "s3cret", remember: true });
  });

  it("tests the entered password from the edit form, inline, with no alert", async () => {
    profiles = [PROFILE];
    testResult = {
      ok: false,
      platform: null,
      arch: null,
      home: null,
      error: "Authentication failed (password) for dev.example.com.",
    };
    const alert = vi.spyOn(window, "alert").mockImplementation(() => {});
    render(<RemoteEnvironmentsSection />);
    await screen.findByTestId("remote-row");

    fireEvent.click(screen.getByTestId("remote-edit"));
    fireEvent.change(screen.getByTestId("remote-password"), { target: { value: "wrong-pw" } });
    fireEvent.click(screen.getByTestId("remote-form-test"));

    await screen.findByTestId("remote-form-test-error");
    expect(screen.getByTestId("remote-form-test-status").textContent).toContain("Connection: Disconnected");
    expect(screen.getByTestId("remote-form-test-error").textContent).toContain(
      "Authentication failed (password)",
    );
    const testCall = calls.find((c) => c.url.endsWith(`/${PROFILE.id}/test`));
    expect(testCall?.body).toEqual({ password: "wrong-pw" });
    expect(alert).not.toHaveBeenCalled();
    // The form stays open; the failure is the payload.
    expect(screen.getByTestId("remote-form")).toBeTruthy();
  });

  it("prechecks Remember for a remembered host and says where it lives", async () => {
    profiles = [{ ...PROFILE, remembered: true, has_password: true }];
    render(<RemoteEnvironmentsSection />);
    await screen.findByTestId("remote-row");
    expect(screen.getByTestId("remote-remembered").textContent).toContain("password remembered");

    fireEvent.click(screen.getByTestId("remote-edit"));
    expect((screen.getByTestId("remote-remember") as HTMLInputElement).checked).toBe(true);
    expect(screen.getByTestId("remote-password-help").textContent).toContain(
      "Windows Credential Manager",
    );
    expect((screen.getByTestId("remote-password") as HTMLInputElement).placeholder).toContain(
      "Remembered",
    );
  });
});
