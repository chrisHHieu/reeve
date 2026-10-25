import { describe, expect, it } from "vitest";
import { modeNotice, modeNoticeStep, withModeNotice, type ModeNoticeState } from "./modeNotice";

const A = "session-a";
const at = (mode: string, session = A) => ({ session, mode });

describe("modeNotice", () => {
  it("names the new mode with the picker's label", () => {
    expect(modeNotice("workspace-write", A, at("read-only"))).toMatchObject({ kind: "notice", tone: "info", text: "Workspace write is on." });
  });

  it.each([
    ["plan", "discuss", "Read only is on."],
    ["auto-approve", "interactive", "Workspace write is on."],
    ["auto", "custom", "Full access is on."],
    ["bypass-approvals", "auto", "Full access is on."],
  ])("says an old wire value %j by its preset's name", (mode, before, text) => {
    expect(modeNotice(mode, A, at(before))?.text).toBe(text);
  });

  it("is silent when nothing changed, when there was no mode before, and across sessions", () => {
    expect(modeNotice("workspace-write", A, at("workspace-write"))).toBeNull();
    expect(modeNotice("workspace-write", A, null)).toBeNull();
    expect(modeNotice("read-only", "session-b", at("danger-full-access"))).toBeNull();
  });

  it("goes inside a running turn, and stands alone between turns", () => {
    expect(modeNotice("danger-full-access", A, at("workspace-write"), true)).toMatchObject({ text: "Full access is on.", inline: true });
    expect(modeNotice("danger-full-access", A, at("workspace-write"))).not.toHaveProperty("inline");
  });
});

describe("modeNoticeStep", () => {
  const start: ModeNoticeState = { mark: null };
  const steps = (...seen: [mode: string, session: string, confirmedFor: string, inline?: boolean][]) => {
    let state = start;
    const said: (string | null)[] = [];
    for (const [mode, session, confirmedFor, inline] of seen) {
      const next = modeNoticeStep(state, mode, session, confirmedFor, inline);
      said.push(next.item ? next.item.text + (next.item.inline ? " (inline)" : "") : null);
      state = next.state;
    }
    return { said, state };
  };

  it("keeps nothing while the mode is not yet known to be this session's", () => {
    // Recording the stale pair would make `ready`'s real mode read as a change.
    expect(steps(["danger-full-access", "s2", "s1"])).toEqual({ said: [null], state: start });
  });

  it("says nothing as a session opens in its own mode, however often it is seen", () => {
    expect(steps(["workspace-write", "s1", "s1"], ["workspace-write", "s1", "s1"]).said).toEqual([null, null]);
  });

  it("says a change inside the session once, as a line and not a block", () => {
    const { said } = steps(["workspace-write", "s1", "s1"], ["read-only", "s1", "s1"]);
    expect(said).toEqual([null, "Read only is on."]);
    expect(modeNoticeStep({ mark: at("workspace-write", "s1") }, "read-only", "s1", "s1").item).not.toHaveProperty("title");
  });

  it("passes a running turn on to the line", () => {
    expect(steps(["workspace-write", "s1", "s1"], ["danger-full-access", "s1", "s1", true]).said[1]).toBe("Full access is on. (inline)");
  });

  it("does not take a move to another session for a change of mode", () => {
    const { said } = steps(["workspace-write", "s1", "s1"], ["danger-full-access", "s2", "s1"], ["danger-full-access", "s2", "s2"]);
    expect(said).toEqual([null, null, null]);
  });
});

describe("withModeNotice", () => {
  const line = { kind: "notice", tone: "info", text: "Read only is on." } as const;

  it("adds the line to a conversation with something in it", () => {
    const said = [{ kind: "user", text: "hi" }] as object[];
    expect(withModeNotice(said, line)).toEqual([...said, line]);
  });

  it("leaves an empty conversation empty, so the start page stays the start page", () => {
    expect(withModeNotice([], line)).toEqual([]);
    expect(withModeNotice([], null)).toEqual([]);
  });
});
