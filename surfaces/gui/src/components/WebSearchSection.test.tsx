// Settings ▸ Web search (`WebSearchSection.tsx`): the mode, the key row and the test, against a
// stubbed server that keeps what it was told the way the real one does.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { WebSearchSection } from "./WebSearchSection";
import { keyDetail, searchState, testVerdict } from "./settings/webSearchView";

afterEach(cleanup);

type Server = {
  mode: "auto" | "tavily" | "duckduckgo";
  key: string;
  keySetAt: string | null;
  tavilyKey: string;
  test: Record<string, unknown>;
};
let server: Server;
let posted: Array<Record<string, unknown>>;

beforeEach(() => {
  server = {
    mode: "auto",
    key: "",
    keySetAt: null,
    tavilyKey: "",
    test: { ok: true, query: "q", provider: "exa", results: 3, ms: 1200 },
  };
  posted = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      const body = init?.body ? JSON.parse(String(init.body)) : {};
      if (url.endsWith("/v1/web-search/test")) {
        posted.push({ test: body.query });
        return { json: async () => ({ ...server.test, query: body.query }) } as Response;
      }
      if (url.endsWith("/v1/web-search") && init?.method === "POST") {
        posted.push(body);
        server.mode = body.provider === "exa" ? "auto" : body.provider;
        if (body.tavily_api_key) server.tavilyKey = body.tavily_api_key;
        if (body.remove_tavily_key) {
          server.tavilyKey = "";
          if (server.mode === "tavily") server.mode = "auto";
        }
        if (body.api_key) {
          server.key = body.api_key;
          server.keySetAt = "2026-10-05";
        } else if (body.remove_key) {
          server.key = "";
          server.keySetAt = null;
        }
        return { json: async () => ({ ok: true }) } as Response;
      }
      if (url.endsWith("/v1/web-search")) {
        return {
          json: async () => ({
            mode: server.mode,
            destination: server.mode === "auto" ? "exa → duckduckgo" : "duckduckgo",
            has_key: Boolean(server.key),
            key_source: server.key ? "settings" : null,
            key_set_at: server.keySetAt,
            tavily: { has_key: Boolean(server.tavilyKey), key_source: server.tavilyKey ? "settings" : null },
            engine:
              server.mode === "tavily" ? (server.tavilyKey ? "tavily" : "exa") : server.mode === "auto" ? "exa" : "duckduckgo",
          }),
        } as Response;
      }
      return { json: async () => ({}) } as Response;
    }),
  );
});

describe("WebSearchSection", () => {
  it("says a fresh install searches Exa's free tier, with DuckDuckGo behind it", async () => {
    render(<WebSearchSection />);
    await waitFor(() => expect(screen.getByTestId("web-search-key-status").textContent).toContain("150 searches a day"));
    expect(screen.getByTestId("web-search-state-word").textContent).toBe("Exa, free shared access");
    expect(screen.getByTestId("web-search-mode-auto").getAttribute("aria-checked")).toBe("true");
    expect(screen.queryByTestId("web-search-remove")).toBeNull();
    expect(screen.getByTestId("web-search-add")).toBeTruthy();
  });

  it("opens the key field on Add, saves a pasted key, and says whose account searches use", async () => {
    render(<WebSearchSection />);
    expect(screen.queryByTestId("web-search-key")).toBeNull(); // no field until a key is being entered
    fireEvent.click(await screen.findByTestId("web-search-add"));
    const field = (await screen.findByTestId("web-search-key")) as HTMLInputElement;
    expect(field.type).toBe("password");
    fireEvent.change(field, { target: { value: "  pasted-key  " } });
    fireEvent.click(screen.getByTestId("web-search-save"));
    await waitFor(() => expect(screen.getByTestId("web-search-key-status").textContent).toContain("Saved 2026-10-05"));
    expect(posted[0]).toEqual({ provider: "auto", api_key: "pasted-key" });
    expect(screen.getByTestId("web-search-state").textContent).toContain("With your key");
    expect(screen.getByTestId("web-search-remove")).toBeTruthy();
  });

  it("switches to DuckDuckGo only without sending or dropping the key", async () => {
    server.key = "kept";
    server.keySetAt = "2026-10-01";
    render(<WebSearchSection />);
    fireEvent.click(await screen.findByTestId("web-search-mode-duckduckgo"));
    await waitFor(() =>
      expect(screen.getByTestId("web-search-mode-duckduckgo").getAttribute("aria-checked")).toBe("true"),
    );
    expect(posted[0]).toEqual({ provider: "duckduckgo" });
    expect(screen.getByTestId("web-search-key-kept").textContent).toContain("Your Exa key is kept");
    expect(screen.getByTestId("web-search-state-word").textContent).toBe("DuckDuckGo");
  });

  it("removes the key only once the question is answered", async () => {
    server.key = "kept";
    render(<WebSearchSection />);
    fireEvent.click(await screen.findByTestId("web-search-remove"));
    expect(posted).toEqual([]); // the first click only asks
    fireEvent.click(await screen.findByTestId("confirm-dialog-ok"));
    await waitFor(() => expect(screen.getByTestId("web-search-key-status").textContent).toContain("free shared access"));
    expect(posted[0]).toEqual({ provider: "auto", remove_key: true });
  });

  it("runs a test search and says who answered", async () => {
    render(<WebSearchSection />);
    fireEvent.click(await screen.findByTestId("web-search-own-query"));
    fireEvent.change(await screen.findByTestId("web-search-test-query"), { target: { value: "widgets" } });
    fireEvent.click(screen.getByTestId("web-search-test"));
    const verdict = await screen.findByTestId("web-search-verdict");
    expect(verdict.getAttribute("data-tone")).toBe("ok");
    expect(verdict.textContent).toContain("Exa answered: 3 results in 1.2 s.");
    expect(posted).toEqual([{ test: "widgets" }]);
  });

  it("shows a rejected key as a warning, with the way to replace it", async () => {
    server.key = "bad";
    server.test = {
      ok: true,
      query: "q",
      provider: "duckduckgo",
      results: 2,
      fallback_from: "exa",
      fallback_reason: "exa api returned HTTP 401",
    };
    render(<WebSearchSection />);
    fireEvent.click(await screen.findByTestId("web-search-test"));
    const verdict = await screen.findByTestId("web-search-verdict");
    expect(verdict.getAttribute("data-tone")).toBe("warn");
    expect(verdict.textContent).toContain("Exa rejected the key, so DuckDuckGo answered");
    expect(screen.getByTestId("web-search-state-word").textContent).toBe("Exa rejected the key");
    fireEvent.click(screen.getByTestId("web-search-fix-key"));
    expect(await screen.findByTestId("web-search-key")).toBeTruthy();
  });

  it("asks for Tavily's key right here when Tavily is chosen without one, and saving it chooses Tavily", async () => {
    render(<WebSearchSection />);
    const tavily = await screen.findByTestId("web-search-mode-tavily");
    expect(tavily.textContent).toContain("Needs a Tavily API key");
    fireEvent.click(tavily);
    expect(posted).toEqual([]); // nothing to choose yet: the key comes first
    fireEvent.change(await screen.findByTestId("web-search-tavily-key"), { target: { value: " tvly-pasted " } });
    fireEvent.click(screen.getByTestId("web-search-tavily-save"));
    await waitFor(() => expect(screen.getByTestId("web-search-state-word").textContent).toBe("Tavily"));
    expect(posted[0]).toEqual({ provider: "tavily", tavily_api_key: "tvly-pasted" });
    expect(screen.getByTestId("web-search-tavily-key-status").textContent).toContain("Searches use your Tavily account");
  });

  it("switches to a Tavily that has a key with one click, and removing the key goes back to Exa", async () => {
    server.tavilyKey = "tvly-saved";
    render(<WebSearchSection />);
    fireEvent.click(await screen.findByTestId("web-search-mode-tavily"));
    await waitFor(() => expect(screen.getByTestId("web-search-state-word").textContent).toBe("Tavily"));
    expect(posted[0]).toEqual({ provider: "tavily" });
    fireEvent.click(screen.getByTestId("web-search-tavily-remove"));
    fireEvent.click(await screen.findByTestId("confirm-dialog-ok"));
    await waitFor(() => expect(screen.getByTestId("web-search-mode-auto").getAttribute("aria-checked")).toBe("true"));
    expect(posted[1]).toEqual({ provider: "tavily", remove_tavily_key: true });
  });

  it("drops an old test's verdict when the setting changes", async () => {
    render(<WebSearchSection />);
    fireEvent.click(await screen.findByTestId("web-search-test"));
    await screen.findByTestId("web-search-verdict");
    fireEvent.click(screen.getByTestId("web-search-mode-duckduckgo"));
    await waitFor(() => expect(screen.queryByTestId("web-search-verdict")).toBeNull());
  });
});

describe("web search settings, in words", () => {
  const base = { mode: "auto" as const, destination: "exa → duckduckgo", has_key: false, key_source: null };

  it("names where the key comes from", () => {
    expect(keyDetail({ ...base, key_source: "environment" })).toContain("EXA_API_KEY");
    expect(keyDetail({ ...base, key_source: "connector" })).toContain("Exa connector");
    expect(keyDetail({ ...base, has_key: true, key_source: "settings", key_set_at: "2026-10-05" })).toContain(
      "Saved 2026-10-05",
    );
  });

  it("says where searches go in one word", () => {
    expect(searchState({ ...base, mode: "duckduckgo" }).word).toBe("DuckDuckGo");
    expect(searchState(base).word).toBe("Exa, free shared access");
    expect(searchState({ ...base, has_key: true, key_source: "environment" }).line).toContain("EXA_API_KEY");
    // Tavily with its key; Tavily chosen with no key (searches run on Exa).
    expect(searchState({ ...base, mode: "tavily", engine: "tavily" }).word).toBe("Tavily");
    const gone = searchState({ ...base, mode: "tavily", engine: "exa" });
    expect(gone.tone).toBe("warn");
    expect(gone.word).toBe("Tavily has no key");
  });

  it("reads a failed test as a failure and a quiet fallback as a warning", () => {
    expect(testVerdict({ ok: false, query: "q", error: "The search failed: boom" })).toEqual({
      tone: "danger",
      text: "The search failed: boom",
    });
    const fell = testVerdict({ ok: true, query: "q", provider: "duckduckgo", results: 1, fallback_from: "exa", fallback_reason: "rate limited" });
    expect(fell.tone).toBe("warn");
    expect(fell.text).not.toContain("rejected the key"); // a rate limit is not a bad key
    expect(fell.text).toContain("Exa did not answer (rate limited)");
  });
});
