// @vitest-environment node
// The shell's decisions that do not need Electron.
import { describe, expect, it } from "vitest";
import { closeBehavior, externalAllowed, lastLines, nextRestart, trayLine, webAddress } from "./policy";
import { parseEnv } from "./shellEnv";

describe("the sidecar's restart budget", () => {
  it("restarts three times in a row with growing delays, then stops", () => {
    expect(nextRestart(0)).toEqual({ attempt: 1, delayMs: 500 });
    expect(nextRestart(1)).toEqual({ attempt: 2, delayMs: 1000 });
    expect(nextRestart(2)).toEqual({ attempt: 3, delayMs: 2000 });
    expect(nextRestart(3)).toBeNull();
  });
});

describe("the tray's line", () => {
  it("says what runs and what waits, in words", () => {
    expect(trayLine(0, 0, "")).toBe("Nothing running");
    expect(trayLine(1, 0, "Fix the export")).toBe("1 task running — Fix the export");
    expect(trayLine(2, 1, "")).toBe("2 tasks running · 1 needs you");
    expect(trayLine(0, 2, "")).toBe("2 need you");
  });
});

describe("closing the window", () => {
  it("asks until the person has chosen tray or quit", () => {
    expect(closeBehavior(undefined)).toBe("ask");
    expect(closeBehavior("tray")).toBe("tray");
    expect(closeBehavior("quit")).toBe("quit");
    expect(closeBehavior("explode")).toBe("ask");
  });
});

describe("what may leave the app, and what the Browser tab may load", () => {
  it("hands only web, mail and phone links to the OS", () => {
    expect(externalAllowed("https://example.com")).toBe(true);
    expect(externalAllowed("mailto:a@example.com")).toBe(true);
    expect(externalAllowed("file:///C:/Windows/system32/calc.exe")).toBe(false);
    expect(externalAllowed("javascript:alert(1)")).toBe(false);
  });

  it("loads only web addresses in the Browser tab", () => {
    expect(webAddress("https://claude.ai/")).toBe(true);
    expect(webAddress("http://localhost:5173")).toBe(true);
    expect(webAddress("file:///etc/passwd")).toBe(false);
    expect(webAddress("reeve://app/index.html")).toBe(false);
    expect(webAddress("not a url")).toBe(false);
  });
});

describe("the login shell's environment", () => {
  it("reads only what is between the markers, and drops what describes the probe shell", () => {
    const text = [
      "Welcome back!",
      "__REEVE_ENV_START__",
      "PATH=/opt/homebrew/bin:/usr/bin",
      "AWS_PROFILE=dev",
      "MULTI=one",
      "  continued line",
      "SHLVL=2",
      "__REEVE_ENV_END__",
      "NOT_INSIDE=1",
    ].join("\n");
    expect(parseEnv(text)).toEqual({ PATH: "/opt/homebrew/bin:/usr/bin", AWS_PROFILE: "dev", MULTI: "one" });
  });
});

describe("the end of the server's log, for Copy details", () => {
  it("is its last lines, blank ones left out, whatever the line endings", () => {
    const log = "booting\r\n\r\nloading tools\nTraceback (most recent call last):\n  File x\nImportError: no module\n\n";
    expect(lastLines(log, 3)).toBe("Traceback (most recent call last):\n  File x\nImportError: no module");
    expect(lastLines(log, 50).split("\n")).toHaveLength(5);
    expect(lastLines("", 5)).toBe("");
  });
});
