import { afterEach, describe, expect, it } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { JsonTree } from "./JsonTree";
import { LiveSession } from "../live/liveSession";
import { handleSessionEvent, type SessionEventHost } from "../live/sessionEvents";
import type { WsEvent } from "../types";

afterEach(cleanup);

const lines = (count: number) => Array.from({ length: count }, (_, n) => `line ${n + 1}`).join("\n");

describe("a call's result as text", () => {
  it("shows a short result whole", () => {
    render(<JsonTree value={lines(5)} clamp />);
    expect(screen.getByTestId("jt-text").textContent).toBe(lines(5));
    expect(screen.queryByTestId("jt-more")).toBeNull();
  });

  it("shows the first lines of a long one and opens the rest in place", () => {
    render(<JsonTree value={lines(212)} clamp />);
    expect(screen.getByTestId("jt-text").textContent).toBe(lines(8));
    const more = screen.getByTestId("jt-more");
    expect(more.textContent).toBe("Show all 212 lines");
    expect(more.getAttribute("aria-expanded")).toBe("false");
    fireEvent.click(more);
    expect(screen.getByTestId("jt-text").textContent).toBe(lines(212));
    expect(screen.getByTestId("jt-more").textContent).toBe("Show fewer lines");
  });

  it("marks an error by its colour class, not by a box", () => {
    render(<JsonTree value="Error: unknown task: bg-1" error clamp />);
    expect(screen.getByTestId("jt-text").className).toContain("is-error");
  });

  it("still draws JSON as a tree", () => {
    render(<JsonTree value={'{"saved": "start-page.png", "width": 1440}'} clamp />);
    expect(screen.getByTestId("jt-tree").textContent).toContain("saved");
    expect(screen.queryByTestId("jt-text")).toBeNull();
  });
});

describe("the result a live row is given", () => {
  const host: SessionEventHost = {
    factsChanged: () => {},
    railStale: () => {},
    reloadTranscript: () => {},
    engineReplaced: () => {},
    turnEnded: () => {},
    showsEnvironmentError: () => false,
  };
  const finish = (data: Record<string, unknown>) => {
    const live = new LiveSession("s");
    const feed = (type: string, payload: Record<string, unknown>) =>
      handleSessionEvent(live, { type, data: payload } as WsEvent, host);
    feed("tool_proposed", { name: "mcp__browser__read_page", call_id: "c1", arguments: {} });
    feed("tool_finished", { name: "mcp__browser__read_page", call_id: "c1", status: "ok", ...data });
    return live.current.items[0] as { preview?: string };
  };

  it("is the whole text when the 300-character preview was cut", () => {
    // Owner-hit 2026-10-13: the row drew `{"title": "Export…` cut mid-value, with its newlines
    // escaped, and only a reload showed the result.
    const whole = lines(40);
    expect(finish({ result_preview: '{"text": "line 1\\nline 2...', result_text: whole }).preview).toBe(whole);
  });

  it("is the preview when nothing was cut", () => {
    expect(finish({ result_preview: '{"ok": true}' }).preview).toBe('{"ok": true}');
  });
});
