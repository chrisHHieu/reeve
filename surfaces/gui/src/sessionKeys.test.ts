import { describe, expect, it } from "vitest";
import { shouldStopOnEscape } from "./sessionKeys";

const running = { running: true, gateOpen: false };
const esc = (over: Partial<KeyboardEvent> = {}) => ({
  key: "Escape",
  defaultPrevented: false,
  ...over,
});

describe("shouldStopOnEscape", () => {
  it("stops the turn when the agent is working and nothing else owns the key", () => {
    expect(shouldStopOnEscape(esc(), running, null)).toBe(true);
    expect(shouldStopOnEscape(esc(), running, { tag: "BODY", contentEditable: false })).toBe(true);
  });

  it("leaves the key alone while the user is typing", () => {
    // Escape closes the slash popup and clears a draft in the composer; killing the run from
    // there would be the app deciding which of the two the user meant.
    expect(shouldStopOnEscape(esc(), running, { tag: "TEXTAREA", contentEditable: false })).toBe(
      false,
    );
    expect(shouldStopOnEscape(esc(), running, { tag: "INPUT", contentEditable: false })).toBe(false);
    expect(shouldStopOnEscape(esc(), running, { tag: "DIV", contentEditable: true })).toBe(false);
  });

  it("does nothing when no turn is running", () => {
    expect(shouldStopOnEscape(esc(), { running: false, gateOpen: false }, null)).toBe(false);
  });

  it("does nothing under a gate: the engine is suspended, not working", () => {
    expect(shouldStopOnEscape(esc(), { running: true, gateOpen: true }, null)).toBe(false);
  });

  it("ignores other keys and presses something already handled", () => {
    expect(shouldStopOnEscape(esc({ key: "Enter" }), running, null)).toBe(false);
    expect(shouldStopOnEscape(esc({ defaultPrevented: true }), running, null)).toBe(false);
  });
});
