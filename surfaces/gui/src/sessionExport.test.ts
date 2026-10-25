// `/export` (api/core.ts): the JSON shape and filename (`utils/session-export.ts`).
// The filename is pure and pinned here; `exportSession` composes the sessions list (our
// server has no single-session GET) with the session's messages and refuses to write an
// empty export for an unknown id.
import { afterEach, describe, expect, it, vi } from "vitest";
import { exportSession, sessionExportFilename } from "./api";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("sessionExportFilename", () => {
  it("matches the reference design's shape: lowercased title, non-alphanumerics collapsed, .json", () => {
    expect(sessionExportFilename({ session_id: "s1", title: "Fix the Flaky  Test!" })).toBe(
      "fix-the-flaky-test.json",
    );
  });

  it("falls back to the id when the title is empty or punctuation-only", () => {
    expect(sessionExportFilename({ session_id: "sess_01", title: "" })).toBe("sess_01.json");
    expect(sessionExportFilename({ session_id: "sess_01", title: "???" })).toBe("sess_01.json");
    expect(sessionExportFilename({ session_id: "sess_01" })).toBe("sess_01.json");
  });
});

describe("exportSession", () => {
  it("returns { info, messages } from the sessions list and the transcript route", async () => {
    const info = { session_id: "s1", title: "Release notes", messages: 2 };
    const messages = [{ role: "user", content: "hi" }, { role: "assistant", content: "hello" }];
    const urls: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        urls.push(url);
        if (url.endsWith("/v1/sessions")) return { json: async () => ({ sessions: [info] }) } as Response;
        return { json: async () => ({ messages }) } as Response;
      }),
    );

    const data = await exportSession("s1");
    expect(data.info).toEqual(info);
    expect(data.messages).toEqual(messages);
    expect(urls.some((u) => u.endsWith("/v1/sessions/s1/messages"))).toBe(true);
  });

  it("throws for an unknown session that also has no messages", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        if (url.endsWith("/v1/sessions")) return { json: async () => ({ sessions: [] }) } as Response;
        return { json: async () => ({ messages: [] }) } as Response;
      }),
    );

    await expect(exportSession("missing")).rejects.toThrow("Session not found: missing");
  });

  it("still exports a session the list no longer carries, from its messages alone", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        if (url.endsWith("/v1/sessions")) return { json: async () => ({ sessions: [] }) } as Response;
        return { json: async () => ({ messages: [{ role: "user", content: "kept" }] }) } as Response;
      }),
    );

    const data = await exportSession("orphan");
    expect(data.info).toBeNull();
    expect(data.messages).toHaveLength(1);
  });
});
