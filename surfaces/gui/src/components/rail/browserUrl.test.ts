import { describe, expect, it } from "vitest";
import { displayHost, isLocalUrl, normalizeAddress } from "./browserUrl";

describe("normalizeAddress", () => {
  it("returns null for empty or blank input", () => {
    expect(normalizeAddress("")).toBeNull();
    expect(normalizeAddress("   ")).toBeNull();
  });

  it("returns null for what is not an address: a space inside, or a bare word", () => {
    expect(normalizeAddress("not a url ::")).toBeNull();
    expect(normalizeAddress("http://exa mple.com")).toBeNull();
    expect(normalizeAddress("notaurl")).toBeNull();
    expect(normalizeAddress("localhost")).toBe("http://localhost");
  });

  it("keeps already-http(s) addresses untouched, path/query/hash included", () => {
    expect(normalizeAddress("http://example.com/a/b?c=1#d")).toBe("http://example.com/a/b?c=1#d");
    expect(normalizeAddress("https://example.com")).toBe("https://example.com");
    expect(normalizeAddress("HTTPS://Example.com/Path")).toBe("HTTPS://Example.com/Path");
  });

  it("rejects non-http(s) schemes instead of navigating them", () => {
    expect(normalizeAddress("file:///C:/secret.txt")).toBeNull();
    expect(normalizeAddress("javascript:alert(1)")).toBeNull();
    expect(normalizeAddress("data:text/html,<h1>hi</h1>")).toBeNull();
    expect(normalizeAddress("mailto:dev@example.com")).toBeNull();
    expect(normalizeAddress("about:blank")).toBeNull();
  });

  it("prepends http:// for loopback, private hosts and explicit ports", () => {
    expect(normalizeAddress("localhost:5173/app?x=1#h")).toBe("http://localhost:5173/app?x=1#h");
    expect(normalizeAddress("127.0.0.1")).toBe("http://127.0.0.1");
    expect(normalizeAddress("127.0.0.1:8000")).toBe("http://127.0.0.1:8000");
    expect(normalizeAddress("[::1]:8080")).toBe("http://[::1]:8080");
    expect(normalizeAddress("0.0.0.0:3000")).toBe("http://0.0.0.0:3000");
    expect(normalizeAddress("10.0.0.5")).toBe("http://10.0.0.5");
    expect(normalizeAddress("192.168.2.125/x")).toBe("http://192.168.2.125/x");
    expect(normalizeAddress("172.16.4.2:9000")).toBe("http://172.16.4.2:9000");
    expect(normalizeAddress("172.31.255.254")).toBe("http://172.31.255.254");
    expect(normalizeAddress("macbook.local")).toBe("http://macbook.local");
    expect(normalizeAddress("example.com:8080")).toBe("http://example.com:8080");
  });

  it("defaults plain public hosts to https and keeps their path/query/hash", () => {
    expect(normalizeAddress("example.com")).toBe("https://example.com");
    expect(normalizeAddress("  docs.example.com/guide?q=1#top  ")).toBe("https://docs.example.com/guide?q=1#top");
    expect(normalizeAddress("172.32.0.1")).toBe("https://172.32.0.1");
  });
});

describe("displayHost", () => {
  it("shows host[:port] for the tab chip", () => {
    expect(displayHost("http://localhost:3000/x?y=1")).toBe("localhost:3000");
    expect(displayHost("https://example.com")).toBe("example.com");
    expect(displayHost("http://127.0.0.1:1420/")).toBe("127.0.0.1:1420");
  });

  it("falls back to the raw string when unparsable", () => {
    expect(displayHost("not a url")).toBe("not a url");
    expect(displayHost("")).toBe("");
  });
});

describe("isLocalUrl", () => {
  it("is true for this computer and its network, false for the web", () => {
    expect(isLocalUrl("http://localhost:5173/")).toBe(true);
    expect(isLocalUrl("http://192.168.1.4:8000")).toBe(true);
    expect(isLocalUrl("https://example.com")).toBe(false);
    expect(isLocalUrl("garbage")).toBe(false);
  });
});
