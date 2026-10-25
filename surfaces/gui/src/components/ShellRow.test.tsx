// The default shell row (amendment of 2026-10-14): it offers Automatic and the shells the
// machine reports, posts the pick, puts the old one back when the server refuses, and is absent
// where there is nothing to choose.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { ShellRow } from "./SettingsView";

let calls: { url: string; init?: RequestInit }[] = [];

function stubFetch(settings: Record<string, unknown>, accepted = true) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      calls.push({ url, init });
      if (url.includes("/v1/settings/shell")) {
        return { ok: true, json: async () => (accepted ? { ok: true } : { ok: false, error: "no" }) } as Response;
      }
      return { ok: true, json: async () => settings } as Response;
    }),
  );
}

const MACHINE = [
  { kind: "powershell", label: "PowerShell 5.1" },
  { kind: "bash", label: "Git Bash" },
];

afterEach(cleanup);

beforeEach(() => {
  calls = [];
});

const checked = (id: string) => screen.getByTestId(id).getAttribute("aria-checked");

describe("ShellRow", () => {
  it("offers Automatic and the machine's shells, and posts the one picked", async () => {
    stubFetch({ shell: "", shells: MACHINE });
    render(<ShellRow />);
    await screen.findByTestId("shell-auto");
    expect(checked("shell-auto")).toBe("true");
    expect(screen.getByTestId("shell-powershell").textContent).toBe("PowerShell 5.1");

    fireEvent.click(screen.getByTestId("shell-bash"));
    await waitFor(() => {
      const posted = calls.filter((c) => c.url.includes("/v1/settings/shell"));
      expect(posted).toHaveLength(1);
      expect(JSON.parse(String(posted[0].init?.body))).toEqual({ shell: "bash" });
    });
    expect(checked("shell-bash")).toBe("true");
  });

  it("goes back to the shell it had when the server refuses", async () => {
    stubFetch({ shell: "powershell", shells: MACHINE }, false);
    render(<ShellRow />);
    await screen.findByTestId("shell-bash");
    fireEvent.click(screen.getByTestId("shell-bash"));
    await waitFor(() => expect(checked("shell-powershell")).toBe("true"));
  });

  it("is absent on a machine with one shell, or an older backend", async () => {
    stubFetch({ shell: "", shells: [{ kind: "bash", label: "bash" }] });
    render(<ShellRow />);
    await waitFor(() => expect(calls.length).toBeGreaterThan(0));
    expect(screen.queryByTestId("shell-card")).toBeNull();
  });
});
