import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { InstructionsSection } from "./InstructionsSection";

let calls: { url: string; init?: RequestInit }[] = [];
let fixture: Record<string, unknown> = {};

afterEach(cleanup);

beforeEach(() => {
  calls = [];
  fixture = { enabled: true, user_rules: "", soul_path: "/Users/u/.reeve/SOUL.md" };
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      calls.push({ url, init });
      if (url.includes("/v1/memory/settings")) {
        const merged =
          init?.method === "PUT" && init.body
            ? { ...fixture, ...JSON.parse(String(init.body)) }
            : fixture;
        return { json: async () => merged } as Response;
      }
      return { json: async () => ({}) } as Response;
    }),
  );
});

describe("InstructionsSection", () => {
  it("loads the SOUL.md text and shows the resolved path", async () => {
    fixture = { ...fixture, user_rules: "Be brief." };
    render(<InstructionsSection />);
    const editor = (await screen.findByTestId("instructions-input")) as HTMLTextAreaElement;
    await waitFor(() => expect(editor.value).toBe("Be brief."));
    expect(screen.getByTestId("instructions-path").textContent).toContain("SOUL.md");
  });

  it("saves the edited text through the memory settings API once typing pauses, and on leaving", async () => {
    fixture = { ...fixture, user_rules: "old" };
    render(<InstructionsSection />);
    const editor = (await screen.findByTestId("instructions-input")) as HTMLTextAreaElement;
    await waitFor(() => expect(editor.value).toBe("old"));
    const puts = () => calls.filter((c) => c.url.includes("/v1/memory/settings") && c.init?.method === "PUT");

    fireEvent.change(editor, { target: { value: "new rules" } });
    // Not on every keystroke: nothing is written until the pause.
    expect(puts()).toHaveLength(0);
    await waitFor(() => expect(puts()).toHaveLength(1), { timeout: 2000 });
    expect(JSON.parse(String(puts()[0].init?.body))).toEqual({ user_rules: "new rules" });
    await waitFor(() => expect(screen.getByTestId("instructions-status").textContent).toContain("Saved"));

    // Leaving the field writes at once; leaving it unchanged writes nothing.
    fireEvent.change(editor, { target: { value: "newer rules" } });
    fireEvent.blur(editor);
    await waitFor(() => expect(puts()).toHaveLength(2));
    expect(JSON.parse(String(puts()[1].init?.body))).toEqual({ user_rules: "newer rules" });
    fireEvent.blur(editor);
    await new Promise((resolve) => setTimeout(resolve, 900));
    expect(puts()).toHaveLength(2);
  });
});
