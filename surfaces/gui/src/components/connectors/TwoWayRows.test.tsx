import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { Connector } from "../../api";

const calls: unknown[][] = [];
vi.mock("../../api", () => ({
  allowUser: (...a: unknown[]) => (calls.push(["allow", ...a]), Promise.resolve({ ok: true })),
  disallowUser: (...a: unknown[]) => (calls.push(["disallow", ...a]), Promise.resolve({ ok: true })),
  resolveUnauthorized: (...a: unknown[]) => (calls.push(["resolve", ...a]), Promise.resolve({ ok: true })),
  unsubscribeChannel: (...a: unknown[]) => (calls.push(["unsubscribe", ...a]), Promise.resolve({ ok: true })),
  getSubscriptions: () =>
    Promise.resolve([
      { session_id: "s1", session_title: "Standup notes", channel: "telegram:42", channel_name: "team" },
      { session_id: "s2", session_title: "Elsewhere", channel: "slack:C1", channel_name: "general" },
    ]),
}));

const { TwoWayGroup, initials } = await import("./TwoWayRows");

// An invented Telegram bot: one person allowed, one stranger who wrote, one message kept.
const bot = {
  name: "telegram",
  title: "Telegram",
  two_way: true,
  channels: true,
  allowed_users: ["7"],
  allowed_user_names: { "7": "Ada Lovelace" },
  recent: [
    { user_id: "7", user_name: "Ada Lovelace", chat_type: "private", authorized: true },
    { user_id: "9", user_name: "Bo", chat_type: "group", authorized: false },
  ],
  unauthorized: [{ id: "pk1", user_id: "9", user_name: "Bo", chat_id: "42", chat_name: "team", text: "hello?", ts: 0 }],
} as unknown as Connector;

afterEach(() => {
  cleanup();
  calls.length = 0;
});

describe("TwoWayGroup", () => {
  it("answers each row on the connector it belongs to", async () => {
    const onChanged = vi.fn();
    render(<TwoWayGroup c={bot} onChanged={onChanged} />);
    expect(screen.getByText("Ada Lovelace")).toBeTruthy();
    // Only the sender not allowed yet is offered.
    expect(screen.queryByTestId("recent-allow-7")).toBeNull();
    fireEvent.click(screen.getByTestId("recent-allow-9"));
    await waitFor(() => expect(calls).toContainEqual(["allow", "telegram", "9"]));
    fireEvent.click(screen.getByTestId("parked-allow-deliver-pk1"));
    await waitFor(() => expect(calls).toContainEqual(["resolve", "telegram", "pk1", "allow_deliver"]));
    fireEvent.click(screen.getByTitle("remove"));
    await waitFor(() => expect(calls).toContainEqual(["disallow", "telegram", "7"]));
    expect(onChanged).toHaveBeenCalledTimes(3);
  });

  it("lists only the conversations following this platform's channels", async () => {
    render(<TwoWayGroup c={bot} onChanged={() => {}} />);
    const listening = await screen.findByTestId("listening-telegram");
    expect(listening.textContent).toContain("Standup notes");
    expect(listening.textContent).toContain("#team");
    expect(listening.textContent).not.toContain("Elsewhere");
  });

  it("asks for no subscriptions where a connector has no channels", () => {
    render(<TwoWayGroup c={{ ...bot, channels: false } as Connector} onChanged={() => {}} />);
    expect(screen.queryByTestId("listening-telegram")).toBeNull();
  });
});

describe("initials", () => {
  it.each([
    ["Ada Lovelace", "AL"],
    ["Ada Byron Lovelace", "AL"],
    ["bo", "BO"],
    ["  ", "?"],
  ])("%j → %s", (name, want) => expect(initials(name)).toBe(want));
});
